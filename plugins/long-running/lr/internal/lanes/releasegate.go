package lanes

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"maps"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	gateGrace   = 5 * time.Minute
	gateEvery   = time.Minute
	gateBack    = 12 * time.Hour
	gateWaiting = "waiting"
)

var buildEnded = []string{"passed", "failed", "canceled", "skipped", "not_run"}

type releaseOrder struct {
	answer string
	at     time.Time
	lane   string
}

type ReleaseGates struct {
	Env        Env
	ReleaseCLI []string
	Token      bkread.Token
	Log        *slog.Logger
	read       time.Time
	after      int64
	orders     map[string]releaseOrder
	cuts       map[string]orderedCut
	raised     map[string]bool
}

type orderedCut struct {
	seq  int64
	name string
	lane string
	at   time.Time
}

type gateStatus struct {
	Record struct {
		Hold struct {
			Job string `json:"job"`
		} `json:"hold"`
		Failures []struct {
			Unlisted bool   `json:"unlisted"`
			Stack    string `json:"stack"`
			Job      string `json:"job"`
			Cause    string `json:"cause"`
			Said     string `json:"said"`
		} `json:"failures"`
	} `json:"record"`
	Build struct {
		State string `json:"state"`
		Gate  struct {
			State string    `json:"state"`
			At    time.Time `json:"at"`
		} `json:"gate"`
	} `json:"build"`
}

func (g *ReleaseGates) Check(ctx context.Context, now time.Time) error {
	if !g.read.IsZero() && now.Sub(g.read) < gateEvery {
		return nil
	}
	g.read = now
	if err := g.readOrders(ctx, now); err != nil {
		g.Log.Warn("release gates did not read the drive", "err", err)
		return nil
	}
	for _, release := range slices.Sorted(maps.Keys(g.orders)) {
		if err := g.tend(ctx, release, now); err != nil {
			return err
		}
	}
	for _, answer := range slices.Sorted(maps.Keys(g.cuts)) {
		if err := g.tendCut(ctx, answer, now); err != nil {
			return err
		}
	}
	return nil
}

func (g *ReleaseGates) tendCut(ctx context.Context, answer string, now time.Time) error {
	cut := g.cuts[answer]
	waited := now.Sub(cut.at)
	if waited < gateGrace {
		return nil
	}
	text := fmt.Sprintf("CUT-SILENT %s: no release record carries owner order %s %d minutes after it; an owner-ordered release with no build is a blocker, never a wait. Start it now: `%s start --manual <component> --order %s`, or resolve %s if the order no longer stands.", cut.lane, answer, int(waited.Minutes()), strings.Join(g.ReleaseCLI, " "), answer, cut.name)
	if err := g.raise(ctx, "cut-silent:"+answer, text); err != nil {
		return err
	}
	delete(g.cuts, answer)
	return nil
}

func (g *ReleaseGates) closeCuts(row client.Record) {
	for answer, cut := range g.cuts {
		refused := row.Kind == "refuse" && row.Lane == cut.lane
		resolved := row.Lane == Root && (row.Resolves == cut.seq || slices.Contains(row.AlsoResolves, cut.seq))
		if refused || resolved {
			delete(g.cuts, answer)
		}
	}
}

func (g *ReleaseGates) readOrders(ctx context.Context, now time.Time) error {
	query := client.Query{Drive: g.Env.Config.Drive, After: g.after}
	if g.after == 0 {
		query.Since = now.Add(-gateBack)
	}
	rows, err := g.Env.Bus.Tail(ctx, query)
	if err != nil {
		return err
	}
	if g.orders == nil {
		g.orders = map[string]releaseOrder{}
		g.cuts = map[string]orderedCut{}
	}
	for _, row := range rows {
		g.after = max(g.after, row.Seq)
		attrs := row.Fields.Attrs
		release, answer := attrs["release"], attrs["order"]
		g.closeCuts(row)
		if answer == "" {
			continue
		}
		at := orderedAt(attrs["ordered_at"], row.At.UTC())
		if release == "" {
			if row.Lane == Root && attrs["lane"] != "" {
				g.cuts[answer] = orderedCut{row.Seq, row.Name(), attrs["lane"], at}
			}
			continue
		}
		delete(g.cuts, answer)
		if known, seen := g.orders[release]; seen && !at.Before(known.at) {
			continue
		}
		g.orders[release] = releaseOrder{answer: answer, at: at, lane: or(attrs["lane"], row.Lane)}
	}
	return nil
}

func orderedAt(said string, posted time.Time) time.Time {
	if at, err := time.Parse(time.RFC3339, said); err == nil {
		return at.UTC()
	}
	return posted
}

func (g *ReleaseGates) tend(ctx context.Context, release string, now time.Time) error {
	status, err := g.status(ctx, release)
	if err != nil {
		g.Log.Warn("release gates did not read the release", "release", release, "err", err)
		return nil
	}
	order := g.orders[release]
	for _, failure := range status.Record.Failures {
		if !failure.Unlisted {
			continue
		}
		text := fmt.Sprintf("RELEASE-STOP %s: %s stopped outside owner order %s: %s; job %s", release, failure.Stack, order.answer, or(failure.Said, failure.Cause), failure.Job)
		if err := g.raise(ctx, "release-order-stop/"+release+"/"+failure.Stack, text); err != nil {
			return err
		}
	}
	if slices.Contains(buildEnded, status.Build.State) {
		delete(g.orders, release)
		return nil
	}
	gate := status.Build.Gate
	if gate.State != gateWaiting || now.Before(newer(gate.At.UTC(), order.at).Add(gateGrace)) {
		return nil
	}
	job := status.Record.Hold.Job
	text := fmt.Sprintf("RELEASE-GATE %s: at its approve gate (job %s) for %d min, more than five minutes past owner order %s; %s approves it now: `%s approve %s`", release, job, int(now.Sub(gate.At).Minutes()), order.answer, order.lane, strings.Join(g.ReleaseCLI, " "), release)
	return g.raise(ctx, "release-gate:"+release+":"+job, text)
}

func (g *ReleaseGates) raise(ctx context.Context, key, text string) error {
	if g.raised[key] {
		return nil
	}
	if _, err := g.Env.Bus.Post(ctx, client.Draft{Drive: g.Env.Config.Drive, Lane: Self, Kind: blockerKind, To: []string{Root}, ID: key, Text: text}); err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	if g.raised == nil {
		g.raised = map[string]bool{}
	}
	g.raised[key] = true
	return nil
}

func (g *ReleaseGates) status(ctx context.Context, release string) (gateStatus, error) {
	argv := append(slices.Clone(g.ReleaseCLI), "status", release, "--json")
	result, err := g.Token.Run(ctx, g.Env.Runner, shell.Command{Argv: argv, Dir: g.Env.Config.Checkout, Timeout: readTimeout})
	if err != nil {
		return gateStatus{}, err
	}
	var status gateStatus
	err = json.Unmarshal([]byte(result.Stdout), &status)
	return status, err
}
