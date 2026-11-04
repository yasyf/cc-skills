package lanes

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	macWaitAfter = 10 * time.Minute
	macOS        = "macos"
	macJobsQuery = `query { organization(slug: %q) { jobs(first: 50, state: [SCHEDULED], type: [COMMAND], agentQueryRules: ["queue=%s"]) { edges { node { ... on JobTypeCommand { label runnableAt build { url } } } } } } }`
)

type MacQueue struct {
	Org       string `json:"org"`
	Queue     string `json:"queue"`
	LimitVCPU int    `json:"limit_vcpu"`
}

type macJob struct {
	Label      string    `json:"label"`
	RunnableAt time.Time `json:"runnableAt"`
	Build      struct {
		URL string `json:"url"`
	} `json:"build"`
}

type MacWaits struct {
	Env   Env
	Queue MacQueue
	CLI   []string
	Token bkread.Token
	Log   *slog.Logger
	read  time.Time
	noted map[string]bool
}

func (m *MacWaits) Check(ctx context.Context, now time.Time) error {
	if !m.read.IsZero() && now.Sub(m.read) < buildsEvery {
		return nil
	}
	m.read = now
	jobs, err := m.waiting(ctx, now)
	if err != nil {
		m.Log.Warn("mac waits did not read Buildkite", "err", err)
		return nil
	}
	if len(jobs) == 0 {
		m.noted = nil
		return nil
	}
	boxes, err := readNscBoxes(ctx, m.Env)
	if err != nil {
		m.Log.Warn("mac waits did not list the boxes", "err", err)
		return nil
	}
	held := macHolders(boxes)
	for rank, box := range held[:min(len(jobs), len(held))] {
		if m.noted[box.ID] {
			continue
		}
		if _, err := m.Env.Bus.Post(ctx, m.note(box, rank, jobs, held, now)); err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
		if m.noted == nil {
			m.noted = map[string]bool{}
		}
		m.noted[box.ID] = true
	}
	return nil
}

func (m *MacWaits) waiting(ctx context.Context, now time.Time) ([]macJob, error) {
	query := fmt.Sprintf(macJobsQuery, m.Queue.Org, m.Queue.Queue)
	result, err := m.Token.Run(ctx, m.Env.Runner, shell.Command{Argv: append(slices.Clone(m.CLI), "graphql", query), Dir: m.Env.Config.Checkout, Timeout: readTimeout})
	if err != nil {
		return nil, err
	}
	var read struct {
		Organization struct {
			Jobs struct {
				Edges []struct {
					Node macJob `json:"node"`
				} `json:"edges"`
			} `json:"jobs"`
		} `json:"organization"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &read); err != nil {
		return nil, err
	}
	var jobs []macJob
	for _, edge := range read.Organization.Jobs.Edges {
		if job := edge.Node; !job.RunnableAt.IsZero() && now.Sub(job.RunnableAt) >= macWaitAfter {
			jobs = append(jobs, job)
		}
	}
	slices.SortFunc(jobs, func(a, b macJob) int { return a.RunnableAt.Compare(b.RunnableAt) })
	return jobs, nil
}

func (m *MacWaits) note(box nscBox, rank int, jobs []macJob, held []nscBox, now time.Time) client.Draft {
	oldest := jobs[0]
	up := now.Sub(box.Created)
	return client.Draft{
		Drive: m.Env.Config.Drive, Lane: Self, Kind: nscRenewed, To: []string{cmp.Or(box.Labels[nscLabel], Root)}, Force: true,
		ID: fmt.Sprintf("mac-wait:%s:%d", box.ID, oldest.RunnableAt.Unix()),
		Text: fmt.Sprintf("MAC-WAIT: %d CI Mac jobs have waited over %d minutes for an agent; the longest, %q in %s, has waited %d minutes, and a rerun makes Namespace try a fresh create: cancel that job and retry it, never inside a trunk-merge build without asking. Lanes hold %d macOS boxes, %d of the %d vCPU the workspace allows, and CI's mac queue runs on the rest. Your box %s, up %dh%02dm, is number %d of %d by age. If its work is done, destroy it with `nsc destroy %s`; a builder that must stay up belongs on `tools/ci mac-box`. The supervisor touched nothing.",
			len(jobs), int(macWaitAfter.Minutes()), oldest.Label, oldest.Build.URL, int(now.Sub(oldest.RunnableAt).Minutes()),
			len(held), vcpuOf(held), m.Queue.LimitVCPU, box.ID, int(up.Hours()), int(up.Minutes())%60, rank+1, len(held), box.ID),
	}
}

func readNscBoxes(ctx context.Context, env Env) ([]nscBox, error) {
	stdout, err := env.Run(ctx, "nsc", "list", "-o", "json")
	if err != nil {
		return nil, err
	}
	var boxes []nscBox
	if err := json.Unmarshal([]byte(stdout), &boxes); err != nil {
		return nil, err
	}
	return boxes, nil
}

func macHolders(boxes []nscBox) []nscBox {
	held := slices.DeleteFunc(slices.Clone(boxes), func(b nscBox) bool { return b.Shape.OS != macOS })
	slices.SortFunc(held, func(a, b nscBox) int { return a.Created.Compare(b.Created) })
	return held
}

func vcpuOf(boxes []nscBox) int {
	total := 0
	for _, box := range boxes {
		total += box.Shape.VCPU
	}
	return total
}

func asksForMac(flags []string) bool {
	return slices.ContainsFunc(flags, func(flag string) bool { return strings.Contains(flag, macOS) })
}

func macHeldWarning(held []nscBox, limitVCPU int) string {
	return fmt.Sprintf("Warning, nothing was blocked: lanes already hold %d macOS boxes, %d of the %d vCPU the workspace allows, and CI's mac queue runs on the rest, so each box you add makes a release's Mac build wait longer. Destroy this box when its run ends; a builder that must stay up belongs on `tools/ci mac-box`.", len(held), vcpuOf(held), limitVCPU)
}

func macCreateWarning(ctx context.Context, runner shell.Runner, driveName string, flags []string) string {
	if !asksForMac(flags) {
		return ""
	}
	entry, err := drive.Named(driveName)
	if err != nil {
		return ""
	}
	landing, err := ReadRunnerLanding(entry.StateDir)
	if err != nil || landing.MacQueue.LimitVCPU == 0 {
		return ""
	}
	boxes, err := readNscBoxes(ctx, Env{Runner: runner})
	if err != nil {
		return ""
	}
	return macHeldWarning(macHolders(boxes), landing.MacQueue.LimitVCPU)
}
