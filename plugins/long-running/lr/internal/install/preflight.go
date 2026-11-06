package install

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	ciWorkflow     = "long-running CI"
	ciWorkflowFile = "long-running-ci.yml"
	pluginName     = "long-running"
	pinEnv         = "LONG_RUNNING_PIN"
	probeTimeout   = 2 * time.Minute
	readTimeout    = time.Minute
	shownFaults    = 5
	lastLineLimit  = 300
)

var (
	installMarkers = []string{"__pycache__", ".in_use", ".orphaned_at"}
	githubRepo     = regexp.MustCompile(`^https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?$`)
)

type Checks struct {
	Runner   shell.Runner
	CCX      ccx.Client
	Installs string
	Claude   string
	Now      func() time.Time
}

func (c Checks) dir(version string) string { return filepath.Join(c.Installs, version) }

func (c Checks) tool(version, name string) string {
	return filepath.Join(c.dir(version), "bin", name)
}

func (c Checks) cached() bool {
	return filepath.Base(filepath.Dir(filepath.Dir(c.Installs))) == "cache"
}

type release struct {
	URL  string `json:"url"`
	Path string `json:"path"`
}

func (c Checks) release() (release, error) {
	marketplace := filepath.Dir(c.Installs)
	path := filepath.Join(filepath.Dir(filepath.Dir(marketplace)), "marketplaces", filepath.Base(marketplace), ".claude-plugin", "marketplace.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return release{}, err
	}
	var parsed struct {
		Plugins []struct {
			Name   string          `json:"name"`
			Source json.RawMessage `json:"source"`
		} `json:"plugins"`
	}
	if err := json.Unmarshal(data, &parsed); err != nil {
		return release{}, fmt.Errorf("%s: %w", path, err)
	}
	for _, entry := range parsed.Plugins {
		if entry.Name != pluginName {
			continue
		}
		var source release
		if err := json.Unmarshal(entry.Source, &source); err != nil {
			return release{}, fmt.Errorf("%s: %w", path, err)
		}
		return source, nil
	}
	return release{}, fmt.Errorf("%s has no %s entry", path, pluginName)
}

func tag(version string) string { return pluginName + "--v" + version }

func (c Checks) ReleaseFaults(ctx context.Context, version string) []string {
	if !c.cached() {
		return nil
	}
	if _, err := os.Stat(filepath.Join(c.dir(version), ".claude-plugin", "plugin.json")); err != nil {
		return []string{fmt.Sprintf("%s holds no install of %s", c.dir(version), version)}
	}
	source, err := c.release()
	if err != nil {
		return []string{err.Error()}
	}
	faults, err := treeFaults(ctx, c.Runner, c.dir(version), source.URL, tag(version), source.Path, filepath.Join(c.Claude, pluginName, "releases.git"))
	if err != nil {
		return []string{err.Error()}
	}
	if len(faults) > shownFaults {
		return append(faults[:shownFaults:shownFaults], fmt.Sprintf("and %d more", len(faults)-shownFaults))
	}
	return faults
}

type released struct {
	mode string
	sha  string
}

func treeFaults(ctx context.Context, runner shell.Runner, install, url, tagName, source, releases string) ([]string, error) {
	if err := fetchTag(ctx, runner, releases, url, tagName); err != nil {
		return []string{fmt.Sprintf("the release tag %s did not fetch from %s: %s", tagName, url, lastLine(err))}, nil
	}
	git := func(stdin string, args ...string) (shell.Result, error) {
		return runner.Run(ctx, shell.Command{Argv: slices.Concat([]string{"git", "-C", releases}, args), Stdin: stdin, Timeout: readTimeout})
	}
	listed, err := git("", "ls-tree", "-r", "-z", tagName, "--", source+"/")
	if err != nil {
		return nil, err
	}
	rows := map[string]released{}
	for row := range strings.SplitSeq(listed.Stdout, "\x00") {
		meta, path, found := strings.Cut(row, "\t")
		if !found {
			continue
		}
		fields := strings.Fields(meta)
		rows[strings.TrimPrefix(path, source+"/")] = released{fields[0], fields[2]}
	}
	held, err := heldFiles(install)
	if err != nil {
		return nil, err
	}
	ignored, err := git("", "--work-tree="+install, "ls-files", "-z", "--others", "--ignored", "--exclude-standard")
	if err != nil {
		return nil, err
	}
	built := map[string]bool{}
	for relative := range strings.SplitSeq(ignored.Stdout, "\x00") {
		built[relative] = true
	}
	held = slices.DeleteFunc(held, func(relative string) bool {
		_, tracked := rows[relative]
		return built[relative] && !tracked
	})
	var missing, blobs, paths []string
	for _, relative := range sortedKeys(rows) {
		row := rows[relative]
		if !present(filepath.Join(install, relative), row.mode) {
			missing = append(missing, relative)
			continue
		}
		if row.mode != "120000" {
			blobs = append(blobs, relative)
			paths = append(paths, filepath.Join(install, relative))
		}
	}
	hashed, err := git(strings.Join(paths, "\n"), "hash-object", "--stdin-paths")
	if err != nil {
		return nil, err
	}
	hashes := strings.Fields(hashed.Stdout)
	if len(hashes) != len(blobs) {
		return nil, fmt.Errorf("git hash-object printed %d hashes for %d files", len(hashes), len(blobs))
	}
	faults := make([]string, 0, len(missing)+len(held)+len(blobs)+1)
	if len(held) != len(rows) {
		faults = append(faults, fmt.Sprintf("the install holds %d files where the release holds %d", len(held), len(rows)))
	}
	for _, relative := range missing {
		faults = append(faults, relative+" is missing")
	}
	for _, relative := range held {
		if _, ok := rows[relative]; !ok {
			faults = append(faults, relative+" is not in the release")
		}
	}
	for i, relative := range blobs {
		if hashes[i] != rows[relative].sha {
			faults = append(faults, relative+" differs from the release")
		}
	}
	return faults, nil
}

func sortedKeys(rows map[string]released) []string {
	keys := make([]string, 0, len(rows))
	for key := range rows {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	return keys
}

func present(path, mode string) bool {
	if mode == "120000" {
		info, err := os.Lstat(path)
		return err == nil && info.Mode()&os.ModeSymlink != 0
	}
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

func heldFiles(install string) ([]string, error) {
	var held []string
	err := filepath.WalkDir(install, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if slices.Contains(installMarkers, entry.Name()) {
			if entry.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if entry.Type().IsRegular() || entry.Type()&os.ModeSymlink != 0 {
			relative, err := filepath.Rel(install, path)
			if err != nil {
				return err
			}
			held = append(held, relative)
		}
		return nil
	})
	return held, err
}

func (c Checks) CIFault(ctx context.Context, version string) string {
	if !c.cached() {
		return ""
	}
	source, err := c.release()
	if err != nil {
		return err.Error()
	}
	found := githubRepo.FindStringSubmatch(source.URL)
	if found == nil {
		return ""
	}
	repo, tagName := found[1], tag(version)
	var commit struct {
		Parents []struct {
			SHA string `json:"sha"`
		} `json:"parents"`
	}
	if err := c.read(ctx, fmt.Sprintf("repos/%s/commits/%s", repo, tagName), &commit); err != nil {
		return err.Error()
	}
	if len(commit.Parents) == 0 {
		return fmt.Sprintf("%s has no parent commit to read a %s run from", tagName, ciWorkflow)
	}
	merged := commit.Parents[0].SHA
	var runs struct {
		Runs []struct {
			Name       string `json:"name"`
			Attempt    int    `json:"run_attempt"`
			Status     string `json:"status"`
			Conclusion string `json:"conclusion"`
			URL        string `json:"html_url"`
		} `json:"workflow_runs"`
	}
	if err := c.read(ctx, fmt.Sprintf("repos/%s/actions/runs?head_sha=%s&per_page=100", repo, merged), &runs); err != nil {
		return err.Error()
	}
	best, attempt := -1, -1
	for i, run := range runs.Runs {
		if run.Name == ciWorkflow && run.Attempt > attempt {
			best, attempt = i, run.Attempt
		}
	}
	short := merged[:min(10, len(merged))]
	if best < 0 {
		return fmt.Sprintf("no %s run on main has started for %s, the commit %s was bumped from", ciWorkflow, short, tagName)
	}
	run := runs.Runs[best]
	if run.Status != "completed" {
		return fmt.Sprintf("the %s run on %s, the commit %s was bumped from, is %s: %s", ciWorkflow, short, tagName, run.Status, run.URL)
	}
	if run.Conclusion == "cancelled" {
		return c.supersededFault(ctx, repo, merged, fmt.Sprintf("the %s run on %s, the commit %s was bumped from, was cancelled (%s)", ciWorkflow, short, tagName, run.URL))
	}
	if run.Conclusion != "success" {
		return fmt.Sprintf("the %s run on %s, the commit %s was bumped from, ended %s: %s", ciWorkflow, short, tagName, run.Conclusion, run.URL)
	}
	return ""
}

type ciRun struct {
	SHA        string    `json:"head_sha"`
	Status     string    `json:"status"`
	Conclusion string    `json:"conclusion"`
	URL        string    `json:"html_url"`
	Started    time.Time `json:"run_started_at"`
	Updated    time.Time `json:"updated_at"`
}

func (c Checks) supersededFault(ctx context.Context, repo, base, cancelled string) string {
	var listed struct {
		Runs []ciRun `json:"workflow_runs"`
	}
	if err := c.read(ctx, fmt.Sprintf("repos/%s/actions/workflows/%s/runs?branch=main&per_page=100", repo, ciWorkflowFile), &listed); err != nil {
		return err.Error()
	}
	var next *ciRun
	for i, run := range listed.Runs {
		if run.Conclusion == "cancelled" {
			continue
		}
		var compared struct {
			Status string `json:"status"`
		}
		if err := c.read(ctx, fmt.Sprintf("repos/%s/compare/%s...%s", repo, base, run.SHA), &compared); err != nil {
			return err.Error()
		}
		if compared.Status != "ahead" && compared.Status != "identical" {
			break
		}
		if run.Status != "completed" {
			next = &listed.Runs[i]
			continue
		}
		if run.Conclusion != "success" {
			return fmt.Sprintf("%s, and the newest finished main run after it, on %s, ended %s: %s", cancelled, run.SHA[:min(10, len(run.SHA))], run.Conclusion, run.URL)
		}
		return ""
	}
	if next == nil {
		return cancelled + ", and no main run after it has finished"
	}
	return fmt.Sprintf("%s, and no main run after it has finished; the run on %s is %s and should finish in about %d minutes: %s", cancelled, next.SHA[:min(10, len(next.SHA))], next.Status, c.minutesUntilDone(listed.Runs, *next), next.URL)
}

func (c Checks) minutesUntilDone(runs []ciRun, next ciRun) int {
	now := c.Now()
	var took time.Duration
	if i := slices.IndexFunc(runs, func(run ciRun) bool { return run.Status == "completed" && run.Conclusion != "cancelled" }); i >= 0 {
		took = runs[i].Updated.Sub(runs[i].Started)
	}
	freed := now
	if i := slices.IndexFunc(runs, func(run ciRun) bool { return run.Status == "in_progress" }); i >= 0 && runs[i].Started.Add(took).After(now) {
		freed = runs[i].Started.Add(took)
	}
	done := freed
	if next.Status != "in_progress" {
		done = freed.Add(took)
	}
	return max(1, int(math.Ceil(done.Sub(now).Minutes())))
}

func (c Checks) read(ctx context.Context, path string, into any) error {
	out, err := c.CCX.GH(ctx, ccx.MaxAgeFresh, "api", path)
	if err != nil {
		return fmt.Errorf("gh api %s: %w", path, err)
	}
	if err := json.Unmarshal([]byte(out), into); err != nil {
		return fmt.Errorf("gh api %s: %w", path, err)
	}
	return nil
}

func (c Checks) TargetFault(ctx context.Context, version, config string) string {
	_, err := c.Runner.Run(ctx, shell.Command{
		Argv:    []string{c.tool(version, "lr"), "install", "check", "--config", config},
		Env:     []string{pinEnv + "=" + version},
		Timeout: probeTimeout,
	})
	if err != nil {
		return fmt.Sprintf("the %s binary refuses this drive: %s", version, lastLine(err))
	}
	return ""
}

func lastLine(err error) string {
	said := err.Error()
	var exit *shell.ExitError
	if errors.As(err, &exit) && strings.TrimSpace(exit.Stderr) != "" {
		said = exit.Stderr
	}
	lines := strings.Split(strings.TrimSpace(said), "\n")
	last := lines[len(lines)-1]
	return last[:min(lastLineLimit, len(last))]
}
