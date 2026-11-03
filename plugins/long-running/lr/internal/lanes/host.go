package lanes

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/url"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type Host string

const (
	HostRunning     Host = "running"
	HostEmpty       Host = "none"
	HostUnreachable Host = "unreachable"

	placedOnSprite = "sprite"
	spriteRunning  = "running"
	lostAfter      = 5 * time.Minute
	reprobeAfter   = StaleAfter
	goneAfter      = 2 * StaleAfter
	workerScript   = `for comm in /proc/[0-9]*/comm; do read -r name < "$comm" 2>/dev/null || continue; case $name in claude|codex) echo running; exit;; esac; done; echo none`
)

func runningProbe(lane string) string {
	return "host-running:" + lane
}

func unreachableProbe(lane string) string {
	return "host-unreachable:" + lane
}

func peerOf(found map[string]Peer, name string) *Peer {
	if known, ok := found[name]; ok {
		return &known
	}
	return nil
}

func silence(lane store.Lane, now time.Time) time.Duration {
	if lane.Heard.IsZero() {
		return now.Sub(lane.Since)
	}
	return now.Sub(lane.Heard)
}

func needsProof(lane store.Lane, silent, retireAfter time.Duration) bool {
	return !holdsNothing(lane) || silent >= retireAfter
}

func proofs(s *store.Store, d Deps, lanes []store.Lane, found map[string]Peer, lost map[string]Sprite, now time.Time) map[string]Verdict {
	probes := must(s.Probes())
	proven := map[string]Verdict{}
	var asked []string
	for _, lane := range lanes {
		if _, gone := lost[lane.Name]; gone || lane.Placement != placedOnSprite || lane.State != store.LaneDesired || Classify(lane, peerOf(found, lane.Name), now, 0, 0).Kind != Unproven || !needsProof(lane, silence(lane, now), d.RetireAfter) {
			continue
		}
		if seen, ok := probes[runningProbe(lane.Name)]; ok && now.Sub(seen.Asked) < reprobeAfter {
			proven[lane.Name] = Verdict{Kind: Ok}
			continue
		}
		asked = append(asked, lane.Name)
	}
	if len(asked) == 0 {
		return proven
	}
	for name, host := range d.Hosts(asked) {
		gone := unreachableProbe(name)
		switch host {
		case HostRunning:
			do(s.Probe(runningProbe(name), now))
			do(s.Unprobe(gone))
			proven[name] = Verdict{Kind: Ok}
		case HostEmpty:
			do(s.Unprobe(gone))
			proven[name] = Verdict{Dead, "has a Sprite that answers with no claude or codex running"}
		case HostUnreachable:
			since, seen := probes[gone]
			if !seen {
				do(s.Probe(gone, now))
				since.Asked = now
			}
			proven[name] = Verdict{Kind: Ok}
			if quiet := now.Sub(since.Asked); quiet >= goneAfter {
				proven[name] = Verdict{Dead, fmt.Sprintf("has a Sprite that has not answered for %d min", int(quiet.Minutes()))}
			}
		}
	}
	return proven
}

type Sprite struct {
	Name        string
	Status      string
	LastRunning time.Time
}

func lostSprites(d Deps, lanes []store.Lane, found map[string]Peer, now time.Time) map[string]Sprite {
	var hosted []string
	for _, lane := range lanes {
		if peer := peerOf(found, lane.Name); lane.Placement == placedOnSprite && lane.State == store.LaneDesired && lane.Waiting == 0 && !settledLane(lane) && peer != nil && peer.Unsettled != "" {
			hosted = append(hosted, lane.Name)
		}
	}
	if len(hosted) == 0 {
		return nil
	}
	lost := map[string]Sprite{}
	for name, sprite := range d.Stopped(hosted) {
		if now.Sub(sprite.LastRunning) > lostAfter {
			lost[name] = sprite
		}
	}
	return lost
}

func lose(s *store.Store, d Deps, lane store.Lane, sprite Sprite, found map[string]Peer, taken map[string]bool, now time.Time, out *Outcome) {
	stopped := fmt.Sprintf("its Sprite %s is %s, last running %s, and its dispatch %s never settled", sprite.Name, sprite.Status, store.Stamp(sprite.LastRunning), found[lane.Name].Unsettled)
	partial := fmt.Sprintf("Partial work: %s was lost mid-task, since %s; what it never pushed is still on that Sprite, so read it with `sprite exec -s %s` before redoing any of it.", lane.Name, stopped, sprite.Name)
	relaunch(s, d, lane, found, "was lost: "+stopped, []string{partial}, taken, now, out)
	then := "held, not relaunched, since the supervisor relaunches a lane once"
	if after, _ := laneOf(s, lane.Name); after.State == store.LaneRetired {
		then = "relaunched as " + after.RetiredBy
	}
	to := slices.Compact(slices.DeleteFunc([]string{lane.Launcher, must(MetaDesk(s))}, func(name string) bool { return name == "" }))
	do(d.Post(Post{Kind: "report", To: to, LaneRef: lane.Name, PRs: lane.PRs, Text: fmt.Sprintf("LANE-LOST %s: %s; %s.", lane.Name, stopped, then)}))
}

func (e Env) stopped(ctx context.Context, lanes []string) map[string]Sprite {
	listed, err := e.spriteList(ctx)
	if err != nil {
		slog.Warn("no lost Sprite found this tick, since the Sprites API list failed", "err", err)
		return nil
	}
	receipts := e.receipts()
	stopped := map[string]Sprite{}
	for _, lane := range lanes {
		if sprite, ok := listed[spriteOf(receipts, lane)]; ok && sprite.Status != spriteRunning {
			stopped[lane] = sprite
		}
	}
	return stopped
}

func (e Env) spriteList(ctx context.Context) (map[string]Sprite, error) {
	listed := map[string]Sprite{}
	path := "/sprites"
	for {
		result, err := e.Runner.Run(ctx, shell.Command{Argv: []string{"sprite", "api", path}, Timeout: readTimeout})
		if err != nil {
			return nil, err
		}
		var page struct {
			Sprites []struct {
				Name          string    `json:"name"`
				Status        string    `json:"status"`
				LastRunningAt time.Time `json:"last_running_at"`
			} `json:"sprites"`
			HasMore bool   `json:"has_more"`
			Next    string `json:"next_continuation_token"`
		}
		if err := json.Unmarshal([]byte(result.Stdout), &page); err != nil {
			return nil, fmt.Errorf("sprite api %s: %w", path, err)
		}
		for _, sprite := range page.Sprites {
			listed[sprite.Name] = Sprite{sprite.Name, sprite.Status, sprite.LastRunningAt.UTC()}
		}
		if !page.HasMore {
			return listed, nil
		}
		path = "/sprites?continuation_token=" + url.QueryEscape(page.Next)
	}
}

func residueTo(s *store.Store, d Deps, lane string) string {
	if lane == Root {
		return d.coordinatorReportsTo()
	}
	if to := owningDesk(s, d, lane); to != Root {
		return to
	}
	return must(MetaDesk(s))
}

func (e Env) hosts(ctx context.Context, lanes []string) map[string]Host {
	listed, err := e.sprites(ctx)
	if err != nil {
		slog.Warn("no sprite probed this tick, since sprite list failed", "err", err)
		return nil
	}
	receipts := e.receipts()
	read := make([]Host, len(lanes))
	var wg sync.WaitGroup
	for i, lane := range lanes {
		sprite := spriteOf(receipts, lane)
		if !listed[sprite] {
			read[i] = HostUnreachable
			continue
		}
		wg.Go(func() {
			host, err := e.host(ctx, sprite)
			if err != nil {
				slog.Warn("sprite not probed this tick", "lane", lane, "sprite", sprite, "err", err)
			}
			read[i] = host
		})
	}
	wg.Wait()
	hosts := make(map[string]Host, len(lanes))
	for i, lane := range lanes {
		if read[i] != "" {
			hosts[lane] = read[i]
		}
	}
	return hosts
}

func (e Env) host(ctx context.Context, sprite string) (Host, error) {
	result, err := e.Runner.Run(ctx, shell.Command{Argv: []string{"sprite", "exec", "-s", sprite, "--no-stdin", "--", "sh", "-c", workerScript}, Timeout: readTimeout})
	var exit *shell.ExitError
	if errors.As(err, &exit) || errors.Is(err, context.DeadlineExceeded) {
		return HostUnreachable, nil
	}
	if err != nil {
		return "", err
	}
	switch host := Host(strings.TrimSpace(result.Stdout)); host {
	case HostRunning, HostEmpty:
		return host, nil
	}
	return "", fmt.Errorf("the worker probe printed %q, neither %s nor %s", result.Stdout, HostRunning, HostEmpty)
}
