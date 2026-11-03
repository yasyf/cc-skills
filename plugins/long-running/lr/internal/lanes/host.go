package lanes

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
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

func proofs(s *store.Store, d Deps, lanes []store.Lane, found map[string]Peer, now time.Time) map[string]Verdict {
	probes := must(s.Probes())
	proven := map[string]Verdict{}
	var asked []string
	for _, lane := range lanes {
		if lane.Placement != placedOnSprite || lane.State != store.LaneDesired || Classify(lane, peerOf(found, lane.Name), now, 0, 0).Kind != Unproven || !needsProof(lane, silence(lane, now), d.RetireAfter) {
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
