package incident

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	callTimeout    = 900 * time.Second
	claudeFastMode = `--settings '{"fastMode":true}'`
	claudeArgsEnv  = "ORCA_LAUNCH_CLAUDE_ARGS"
	buildPageSize  = 100
	landingLookup  = 10 * time.Minute
)

var (
	launchLine       = regexp.MustCompile(`^(\S+) (ready|unsupervised) task=(\S+) dispatch=(\S+) terminal=(\S*) worktree=(\S+)$`)
	unfinishedStates = []string{"creating", "scheduled", "running", "failing", "canceling", "blocked"}
)

func stdout(ctx context.Context, runner shell.Runner, c shell.Command) (string, error) {
	c.Timeout = callTimeout
	result, err := runner.Run(ctx, c)
	return result.Stdout, err
}

func exitAndOutput(ctx context.Context, runner shell.Runner, c shell.Command) (int, string, error) {
	c.Timeout = callTimeout
	result, err := runner.Run(ctx, c)
	var exit *shell.ExitError
	if err != nil && !errors.As(err, &exit) {
		return 0, "", err
	}
	return result.Code, result.Stdout + result.Stderr, nil
}

type OrcaCLI struct {
	Runner   shell.Runner
	Lane     string
	Run      string
	Repo     string
	Launcher string
}

func (o OrcaCLI) Launch(ctx context.Context, lane, model, effort, brief string, fast bool) (LaunchReceipt, error) {
	if o.Run == "" || o.Repo == "" {
		return LaunchReceipt{}, &EffectFailed{"no Orca run: open the incident with --orca-run and --orca-repo"}
	}
	env := append(lanes.Identity(o.Lane), "ORCA_LAUNCH_RUN="+o.Run, "ORCA_LAUNCH_REPO="+o.Repo)
	if fast {
		args := claudeFastMode
		if held := os.Getenv(claudeArgsEnv); held != "" {
			args = held + " " + args
		}
		env = append(env, claudeArgsEnv+"="+args)
	}
	out, err := stdout(ctx, o.Runner, shell.Command{Argv: []string{o.Launcher, lane, model, effort, brief}, Env: env})
	if err != nil {
		return LaunchReceipt{}, err
	}
	lines := strings.Split(strings.TrimSpace(out), "\n")
	line := lines[len(lines)-1]
	match := launchLine.FindStringSubmatch(line)
	if match == nil {
		return LaunchReceipt{}, &EffectFailed{line}
	}
	return LaunchReceipt{Lane: match[1], State: match[2], Task: match[3], Dispatch: match[4], Terminal: match[5], Worktree: match[6]}, nil
}

func (o OrcaCLI) state() (string, error) {
	if state := os.Getenv("ORCA_LAUNCH_STATE"); state != "" {
		return state, nil
	}
	base := os.Getenv("XDG_STATE_HOME")
	if base == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		base = filepath.Join(home, ".local", "state")
	}
	return filepath.Join(base, "long-running", "orca-launch", o.Run), nil
}

func (o OrcaCLI) Receipt(lane string) (*LaunchReceipt, error) {
	state, err := o.state()
	if err != nil {
		return nil, err
	}
	for _, suffix := range []string{".json", ".json.new"} {
		raw, err := os.ReadFile(filepath.Join(state, lane+suffix))
		if errors.Is(err, os.ErrNotExist) || (err == nil && len(raw) == 0) {
			continue
		}
		if err != nil {
			return nil, err
		}
		var file struct {
			Result struct {
				TaskID     string `json:"taskId"`
				DispatchID string `json:"dispatchId"`
				State      string `json:"state"`
			} `json:"result"`
		}
		if err := json.Unmarshal(raw, &file); err != nil {
			return nil, fmt.Errorf("launch receipt %s: %w", lane+suffix, err)
		}
		if file.Result.TaskID != "" && file.Result.DispatchID != "" {
			return &LaunchReceipt{Lane: lane, State: file.Result.State, Task: file.Result.TaskID, Dispatch: file.Result.DispatchID}, nil
		}
	}
	return nil, nil
}

func (o OrcaCLI) Engaged(ctx context.Context, lane string, now time.Time) (bool, error) {
	state, err := o.state()
	if err != nil {
		return false, err
	}
	began, beganErr := os.Stat(filepath.Join(state, lane+".binding.json"))
	if beganErr == nil {
		landed, landedErr := os.Stat(filepath.Join(state, lane+".json"))
		settled := landedErr == nil && !landed.ModTime().Before(began.ModTime())
		if !settled && now.Sub(began.ModTime()) < callTimeout {
			return true, nil
		}
	}
	receipt, err := o.Receipt(lane)
	if err != nil || receipt == nil {
		return false, err
	}
	out, err := stdout(ctx, o.Runner, shell.Command{Argv: []string{"orca", "orchestration", "worker-show", "--dispatch", receipt.Dispatch, "--full", "--json"}})
	if isLost(err) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	var shown struct {
		Result struct {
			Dispatch struct {
				Status string `json:"status"`
			} `json:"dispatch"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(out), &shown); err != nil {
		return false, err
	}
	status := shown.Result.Dispatch.Status
	return status != "completed" && status != "failed", nil
}

type GitHubCLI struct {
	Runner shell.Runner
	Repo   string
	Trunk  string
}

func (g GitHubCLI) read(ctx context.Context, env []string, args ...string) (string, error) {
	argv := append([]string{"ccx", "vcs", "gh", "--max-age", ccx.MaxAgeCached, "--"}, args...)
	return stdout(ctx, g.Runner, shell.Command{Argv: argv, Env: env})
}

func (g GitHubCLI) PRs(ctx context.Context, numbers []int) (map[int]PRView, error) {
	if len(numbers) == 0 {
		return map[int]PRView{}, nil
	}
	owner, name, found := strings.Cut(g.Repo, "/")
	if !found {
		return nil, fmt.Errorf("repo %q is not owner/name", g.Repo)
	}
	aliases := make([]string, len(numbers))
	for i, number := range numbers {
		aliases[i] = fmt.Sprintf("p%d: pullRequest(number: %d) { number state headRefOid url }", number, number)
	}
	query := fmt.Sprintf(`query { repository(owner: %q, name: %q) { %s } }`, owner, name, strings.Join(aliases, " "))
	out, err := g.read(ctx, []string{"GH_REPO=" + g.Repo}, "api", "graphql", "-f", "query="+query)
	if err != nil {
		return nil, err
	}
	var graph struct {
		Data struct {
			Repository map[string]*struct {
				Number     int    `json:"number"`
				State      string `json:"state"`
				HeadRefOid string `json:"headRefOid"`
				URL        string `json:"url"`
			} `json:"repository"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(out), &graph); err != nil {
		return nil, err
	}
	views := make(map[int]PRView, len(graph.Data.Repository))
	for _, pr := range graph.Data.Repository {
		if pr != nil {
			views[pr.Number] = PRView{State: pr.State, Head: pr.HeadRefOid, URL: pr.URL}
		}
	}
	return views, nil
}

func (g GitHubCLI) Landed(ctx context.Context, pr int) (string, error) {
	out, err := g.read(ctx, nil, "pr", "view", strconv.Itoa(pr), "--repo", g.Repo, "--json", "state,mergedAt,mergeCommit,closedAt")
	if err != nil {
		return "", err
	}
	var view struct {
		State       string `json:"state"`
		ClosedAt    string `json:"closedAt"`
		MergeCommit *struct {
			OID string `json:"oid"`
		} `json:"mergeCommit"`
	}
	if err := json.Unmarshal([]byte(out), &view); err != nil {
		return "", err
	}
	if view.State == "MERGED" {
		return view.MergeCommit.OID, nil
	}
	if view.State != "CLOSED" {
		return "", nil
	}
	closedAt, err := store.Parse(view.ClosedAt)
	if err != nil {
		return "", err
	}
	path := fmt.Sprintf("repos/%s/commits?sha=%s&since=%s&per_page=100", g.Repo, g.Trunk, store.Stamp(closedAt.Add(-landingLookup)))
	if out, err = g.read(ctx, nil, "api", path); err != nil {
		return "", err
	}
	var commits []struct {
		SHA    string `json:"sha"`
		Commit struct {
			Message string `json:"message"`
		} `json:"commit"`
	}
	if err := json.Unmarshal([]byte(out), &commits); err != nil {
		return "", err
	}
	suffix := fmt.Sprintf("(#%d)", pr)
	for _, commit := range commits {
		subject, _, _ := strings.Cut(commit.Commit.Message, "\n")
		if strings.HasSuffix(subject, suffix) {
			return commit.SHA, nil
		}
	}
	return "", nil
}

type BuildkiteCLI struct {
	Runner   shell.Runner
	Pipeline string
}

func (b BuildkiteCLI) api(ctx context.Context, path string, into any) error {
	out, err := stdout(ctx, b.Runner, shell.Command{Argv: []string{"bk", "api", path}})
	if err != nil {
		return err
	}
	return json.Unmarshal([]byte(out), into)
}

func (b BuildkiteCLI) Configuration(ctx context.Context) (string, error) {
	var pipeline struct {
		Configuration string `json:"configuration"`
	}
	err := b.api(ctx, "/pipelines/"+b.Pipeline, &pipeline)
	return pipeline.Configuration, err
}

func (b BuildkiteCLI) builds(ctx context.Context, query string) ([]Build, error) {
	var found []Build
	for page := 1; ; page++ {
		var batch []Build
		path := fmt.Sprintf("/pipelines/%s/builds?%s&per_page=%d&page=%d", b.Pipeline, query, buildPageSize, page)
		if err := b.api(ctx, path, &batch); err != nil {
			return nil, err
		}
		found = append(found, batch...)
		if len(batch) < buildPageSize {
			return found, nil
		}
	}
}

func (b BuildkiteCLI) Failed(ctx context.Context, since, until string) ([]Build, error) {
	return b.builds(ctx, "state[]=failed&state[]=canceled&created_from="+since+"&created_to="+until)
}

func (b BuildkiteCLI) Unfinished(ctx context.Context, since, until string) ([]Build, error) {
	states := make([]string, len(unfinishedStates))
	for i, state := range unfinishedStates {
		states[i] = "state[]=" + state
	}
	return b.builds(ctx, strings.Join(states, "&")+"&created_from="+since+"&created_to="+until)
}

func (b BuildkiteCLI) Since(ctx context.Context, since string) ([]Build, error) {
	return b.builds(ctx, "created_from="+since)
}

func (b BuildkiteCLI) Rebuild(ctx context.Context, number int) (Build, error) {
	out, err := stdout(ctx, b.Runner, shell.Command{Argv: []string{"bk", "api", "-X", "PUT", fmt.Sprintf("/pipelines/%s/builds/%d/rebuild", b.Pipeline, number)}})
	if err != nil {
		return Build{}, err
	}
	var build Build
	return build, json.Unmarshal([]byte(out), &build)
}

type ActivationCLI struct {
	Runner   shell.Runner
	Checkout string
	Pipeline string
	Worktree string
}

func (a ActivationCLI) Prepare(ctx context.Context, sha string) (string, error) {
	if _, err := os.Stat(a.Worktree); errors.Is(err, os.ErrNotExist) {
		for _, argv := range [][]string{
			{"git", "-C", a.Checkout, "fetch", "origin", sha},
			{"git", "-C", a.Checkout, "worktree", "add", "--detach", a.Worktree, sha},
			{"bun", "install", "--cwd", filepath.Join(a.Worktree, "infra", "ci"), "--frozen-lockfile"},
		} {
			if _, err := stdout(ctx, a.Runner, shell.Command{Argv: argv}); err != nil {
				return "", err
			}
		}
	} else if err != nil {
		return "", err
	}
	head, err := stdout(ctx, a.Runner, shell.Command{Argv: []string{"git", "-C", a.Worktree, "rev-parse", "HEAD"}})
	if err != nil {
		return "", err
	}
	if head = strings.TrimSpace(head); head != sha {
		return "", &EffectFailed{fmt.Sprintf("activation worktree %s is at %s, not the landed %s", a.Worktree, head, sha)}
	}
	return a.Worktree, nil
}

func (a ActivationCLI) sync(ctx context.Context, tree string, flags ...string) (int, string, error) {
	argv := append([]string{"bun", "infra/ci/src/cli.ts", "sync", a.Pipeline}, flags...)
	return exitAndOutput(ctx, a.Runner, shell.Command{Argv: argv, Dir: tree})
}

func tail(text string) string {
	text = strings.TrimSpace(text)
	return text[max(0, len(text)-300):]
}

func (a ActivationCLI) Drifted(ctx context.Context, tree string) (bool, error) {
	code, out, err := a.sync(ctx, tree)
	if err != nil {
		return false, err
	}
	if code == 0 && strings.Contains(out, "match Buildkite") {
		return false, nil
	}
	if code != 0 && strings.Contains(out, "drifted") {
		return true, nil
	}
	return false, &EffectFailed{fmt.Sprintf("ci sync %s dry run exited %d: %s", a.Pipeline, code, tail(out))}
}

func (a ActivationCLI) Apply(ctx context.Context, tree string) ([]string, error) {
	code, out, err := a.sync(ctx, tree, "--apply")
	if err != nil {
		return nil, err
	}
	if code != 0 {
		return nil, &EffectFailed{fmt.Sprintf("ci sync %s --apply exited %d: %s", a.Pipeline, code, tail(out))}
	}
	applied := []string{}
	for line := range strings.SplitSeq(out, "\n") {
		if strings.Contains(line, "applied") {
			applied = append(applied, strings.TrimSpace(line))
		}
	}
	return applied, nil
}

type RecordsCLI struct {
	Runner shell.Runner
	Repo   string
}

func (r RecordsCLI) ccn(ctx context.Context, args ...string) (string, error) {
	argv := append(append([]string{"ccn", "-R", r.Repo}, args...), "--json")
	return stdout(ctx, r.Runner, shell.Command{Argv: argv})
}

type noted struct {
	ID string `json:"id"`
}

func (n noted) short() string {
	return n.ID[:min(7, len(n.ID))]
}

func (r RecordsCLI) listed(ctx context.Context, args ...string) (string, error) {
	out, err := r.ccn(ctx, args...)
	if err != nil {
		return "", err
	}
	var found []noted
	if err := json.Unmarshal([]byte(out), &found); err != nil {
		return "", err
	}
	if len(found) == 0 {
		return "", nil
	}
	return found[0].short(), nil
}

func (r RecordsCLI) Open(ctx context.Context, id, title, premise string) (RecordIDs, error) {
	label := topic(id)
	investigation, err := r.listed(ctx, "investigation", "list", "--all", "--label", label)
	if err != nil {
		return RecordIDs{}, err
	}
	if investigation == "" {
		if investigation, err = r.addedID(ctx, "investigation", "open", title, "--body", premise, "--label", "incident", "--label", label); err != nil {
			return RecordIDs{}, err
		}
	}
	log, err := r.listed(ctx, "log", "list", "--label", label)
	if err != nil {
		return RecordIDs{}, err
	}
	if log == "" {
		if log, err = r.addedID(ctx, "log", "add", "incident "+id, "--label", "incident", "--label", label, "--entry", "investigation "+investigation+": "+title); err != nil {
			return RecordIDs{}, err
		}
	}
	return RecordIDs{Investigation: investigation, Log: log}, nil
}

func (r RecordsCLI) addedID(ctx context.Context, args ...string) (string, error) {
	out, err := r.ccn(ctx, args...)
	if err != nil {
		return "", err
	}
	var record noted
	if err := json.Unmarshal([]byte(out), &record); err != nil {
		return "", err
	}
	return record.short(), nil
}

func (r RecordsCLI) Entry(ctx context.Context, log, text, attach string) error {
	args := []string{"log", "append", log, "--entry", text}
	if attach != "" {
		args = append(args, "--attach", attach, "--replace")
	}
	_, err := r.ccn(ctx, args...)
	return err
}

func (r RecordsCLI) Verdict(ctx context.Context, investigation, verb, text, commit string) error {
	args := []string{"investigation", verb, investigation, text}
	if commit != "" {
		args = append(args, "--commit", commit)
	}
	_, err := r.ccn(ctx, args...)
	return err
}
