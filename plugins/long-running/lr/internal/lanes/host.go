package lanes

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/awake"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type Host string

type Sprite struct {
	Name        string    `json:"name"`
	Status      string    `json:"status"`
	LastRunning time.Time `json:"last_running_at"`
}

const (
	HostRunning     Host = awake.WorkerRunning
	HostEmpty       Host = "none"
	HostUnreachable Host = "unreachable"

	placedOnSprite = "sprite"
	spriteRunning  = "running"
	reprobeAfter   = StaleAfter
	goneAfter      = 2 * StaleAfter
	lostAfter      = 5 * time.Minute
	hostGoneAfter  = 10 * time.Minute
	relayAnswer    = StaleAfter
)

func relayProbe(lane string) string {
	return "relay-asked:" + lane
}

func runningProbe(lane string) string {
	return "host-running:" + lane
}

func unreachableProbe(lane string) string {
	return "host-unreachable:" + lane
}

func hostGoneProbe(lane string) string {
	return "host-gone:" + lane
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

func proofs(s *store.Store, d Deps, lanes []store.Lane, found map[string]Peer, now time.Time) map[string]Verdict {
	proven := hostProofs(s, d, lanes, found, now)
	probes := must(s.Probes())
	for _, lane := range lanes {
		key := relayProbe(lane.Name)
		asked, relayed := probes[key]
		if !relayed {
			continue
		}
		verdict := Classify(lane, peerOf(found, lane.Name), now, 0, 0)
		if _, settled := proven[lane.Name]; settled || verdict.Kind != Unproven {
			do(s.Unprobe(key))
			continue
		}
		proven[lane.Name] = Verdict{Kind: Ok}
		if quiet := now.Sub(asked.Asked); quiet >= relayAnswer {
			proven[lane.Name] = Verdict{Unanswered, fmt.Sprintf("%s, and posted no record in the %d min after a relay asked its state", strings.TrimSuffix(verdict.Why, nothingProves), int(quiet.Minutes()))}
		}
	}
	return proven
}

func hostProofs(s *store.Store, d Deps, lanes []store.Lane, found map[string]Peer, now time.Time) map[string]Verdict {
	proven := map[string]Verdict{}
	probes := must(s.Probes())
	var unproven []store.Lane
	var names []string
	for _, lane := range lanes {
		peer, gone := peerOf(found, lane.Name), hostGoneProbe(lane.Name)
		_, tracked := probes[gone]
		switch down := lane.Placement == placedOnSprite && peer != nil && peer.Row.HostGone; {
		case down && !tracked:
			do(s.Probe(gone, now))
		case !down && tracked:
			do(s.Unprobe(gone))
			delete(probes, gone)
		}
		if (lane.Placement == placedOnSprite || (peer != nil && peer.Row.Unverified != "")) && lane.State == store.LaneDesired && Classify(lane, peer, now, 0, 0).Kind == Unproven {
			unproven = append(unproven, lane)
			names = append(names, lane.Name)
		}
	}
	if len(unproven) == 0 {
		return proven
	}
	sprites, err := d.Sprites(names)
	if err != nil {
		slog.Warn("no sprite read this tick, since the Sprites list failed", "err", err)
		for _, lane := range unproven {
			if lane.Placement == placedOnSprite {
				proven[lane.Name] = Verdict{Kind: Ok}
			}
		}
		return proven
	}
	var asked []string
	down := map[string]string{}
	for _, lane := range unproven {
		sprite, listed := sprites[lane.Name]
		if !listed && lane.Placement != placedOnSprite {
			continue
		}
		if listed && sprite.Status != spriteRunning && !settledLane(lane) {
			proven[lane.Name] = Verdict{Kind: Ok}
			if now.Sub(sprite.LastRunning) >= lostAfter {
				why := fmt.Sprintf("has a Sprite %s that is %s", sprite.Name, sprite.Status)
				if !sprite.LastRunning.IsZero() {
					why += " and last ran at " + pacific(sprite.LastRunning)
				}
				proven[lane.Name] = Verdict{Lost, why}
			}
			continue
		}
		if why := neverHeard(lane, now); why != "" && !settledLane(lane) {
			proven[lane.Name] = Verdict{Lost, why}
			continue
		}
		if why := orcaLostHost(lane, probes, now); why != "" && !settledLane(lane) {
			down[lane.Name] = why
			asked = append(asked, lane.Name)
			continue
		}
		if !needsProof(lane, silence(lane, now), d.RetireAfter) {
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
	hosts := d.Hosts(asked, sprites)
	for name, host := range hosts {
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
	for name, why := range down {
		if hosts[name] != HostRunning {
			proven[name] = Verdict{Lost, why}
		}
	}
	return proven
}

func orcaLostHost(lane store.Lane, probes map[string]store.Probe, now time.Time) string {
	if gone, tracked := probes[hostGoneProbe(lane.Name)]; tracked && now.Sub(gone.Asked) >= hostGoneAfter {
		return fmt.Sprintf("has a Sprite host Orca has reported unavailable for %d min", int(now.Sub(gone.Asked).Minutes()))
	}
	return ""
}

func neverHeard(lane store.Lane, now time.Time) string {
	if lane.Heard.IsZero() && now.Sub(lane.Since) >= StaleAfter {
		return fmt.Sprintf("has posted no record in the %d min since its launch, and Orca does not report it live", int(now.Sub(lane.Since).Minutes()))
	}
	return ""
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

func (e Env) laneSprites(ctx context.Context, lanes []string) (map[string]Sprite, error) {
	listed := map[string]Sprite{}
	for path := "/sprites"; ; {
		result, err := e.Runner.Run(ctx, shell.Command{Argv: []string{"sprite", "api", path}, Timeout: readTimeout})
		if err != nil {
			return nil, err
		}
		var page struct {
			Sprites []Sprite `json:"sprites"`
			HasMore bool     `json:"has_more"`
			Next    string   `json:"next_continuation_token"`
		}
		if err := json.Unmarshal([]byte(result.Stdout), &page); err != nil {
			return nil, fmt.Errorf("sprite api %s: %w", path, err)
		}
		for _, sprite := range page.Sprites {
			listed[sprite.Name] = sprite
		}
		if !page.HasMore {
			break
		}
		path = "/sprites?continuation_token=" + url.QueryEscape(page.Next)
	}
	receipts := e.receipts()
	sprites := map[string]Sprite{}
	for _, lane := range lanes {
		if sprite, ok := listed[spriteOf(receipts, lane)]; ok {
			sprites[lane] = sprite
		}
	}
	return sprites, nil
}

func (e Env) hosts(ctx context.Context, lanes []string, sprites map[string]Sprite) map[string]Host {
	read := make([]Host, len(lanes))
	var wg sync.WaitGroup
	for i, lane := range lanes {
		sprite, listed := sprites[lane]
		if !listed {
			read[i] = HostUnreachable
			continue
		}
		wg.Go(func() {
			host, err := e.host(ctx, sprite.Name)
			if err != nil {
				slog.Warn("sprite not probed this tick", "lane", lane, "sprite", sprite.Name, "err", err)
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
	result, err := e.Runner.Run(ctx, shell.Command{Argv: []string{"sprite", "exec", "-s", sprite, "--no-stdin", "--", "sh", "-c", awake.WorkerScript}, Timeout: readTimeout})
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
