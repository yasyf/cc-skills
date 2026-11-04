package orca

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	ccRemoteTaskSchema = 1
	providerSprites    = "sprites"
	ccRemoteEnv        = "WORKER_CC_REMOTE"
	ccRemoteConfigEnv  = "CC_REMOTE_CONFIG"
	spriteReadTimeout  = 3 * time.Minute
)

const spriteCheckout = `git -C "$1" remote get-url origin && git -C "$1" rev-list --count --branches --glob=refs/stash --not HEAD --remotes=origin && git -C "$1" status --porcelain=v2 --branch`

const spriteHolders = `for p in /proc/[0-9]*; do
  pid=${p#/proc/}
  [ "$pid" = "$$" ] && continue
  case "$(cat "$p/comm" 2>/dev/null)" in
    claude|codex) echo "$pid"; continue ;;
  esac
  case "$(readlink "$p/cwd" 2>/dev/null)" in
    "$1"|"$1"/*) echo "$pid" ;;
  esac
done`

var (
	squashSubject = regexp.MustCompile(` \(#(\d+)\)$`)
	shellSafe     = regexp.MustCompile(`^[A-Za-z0-9_@%+=:,./-]+$`)
	githubOrigin  = regexp.MustCompile(`github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$`)
	githubUnknown = regexp.MustCompile(`HTTP 4(04|22)`)
)

type Checkout struct {
	Path        string
	Dirty       bool
	Branch      string
	Head        string
	OnTrunk     bool
	Repository  string
	Environment string
	OffOrigin   int
}

type CheckoutRead struct {
	Found   *Checkout
	Refusal string
}

type Landings interface {
	Landed(ctx context.Context, head string) (bool, error)
}

type Remote struct {
	Heads  map[string]bool
	Landed Landings
}

type Repositories interface {
	Checkout(ctx context.Context, path string) (CheckoutRead, error)
	Repository(ctx context.Context) (string, error)
	OnTrunk(ctx context.Context, head, repository string) (bool, error)
	Remote(ctx context.Context, repository string) (Remote, error)
	Remove(ctx context.Context, worktree string) (string, error)
}

type advertised struct {
	trunk string
	heads map[string]bool
}

type Git struct {
	Runner shell.Runner

	mu    sync.Mutex
	cache map[string]advertised
}

func (g *Git) run(ctx context.Context, dir string, args ...string) (shell.Result, error) {
	result, err := g.Runner.Run(ctx, shell.Command{Argv: append([]string{"git"}, args...), Dir: dir})
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		return result, nil
	}
	return result, err
}

func (g *Git) checked(ctx context.Context, dir string, args ...string) (string, error) {
	result, err := g.Runner.Run(ctx, shell.Command{Argv: append([]string{"git"}, args...), Dir: dir})
	return result.Stdout, err
}

func isDir(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

func (g *Git) Checkout(ctx context.Context, path string) (CheckoutRead, error) {
	if !isDir(path) {
		return CheckoutRead{}, nil
	}
	found, err := g.run(ctx, path, "rev-parse", "--path-format=absolute", "--git-common-dir", "HEAD")
	if err != nil {
		return CheckoutRead{}, err
	}
	if found.Code != 0 {
		return CheckoutRead{Refusal: "git cannot read it: " + describeExit(found.Stderr, "exit %d", found.Code)}, nil
	}
	lines := strings.Split(strings.TrimSpace(found.Stdout), "\n")
	repository, head := lines[0], lines[1]
	status, err := g.checked(ctx, path, "status", "--porcelain")
	if err != nil {
		return CheckoutRead{}, err
	}
	branch, err := g.run(ctx, path, "symbolic-ref", "--short", "-q", "HEAD")
	if err != nil {
		return CheckoutRead{}, err
	}
	onTrunk, err := g.OnTrunk(ctx, head, repository)
	if err != nil {
		return CheckoutRead{}, err
	}
	return CheckoutRead{Found: &Checkout{
		Path:       path,
		Dirty:      strings.TrimSpace(status) != "",
		Branch:     strings.TrimSpace(branch.Stdout),
		Head:       head,
		OnTrunk:    onTrunk,
		Repository: repository,
	}}, nil
}

func (g *Git) Repository(ctx context.Context) (string, error) {
	out, err := g.checked(ctx, "", "rev-parse", "--path-format=absolute", "--git-common-dir")
	return strings.TrimSpace(out), err
}

func (g *Git) Remove(ctx context.Context, worktree string) (string, error) {
	repository, err := g.checked(ctx, worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
	if err != nil {
		return "", err
	}
	done, err := g.Runner.Run(ctx, shell.Command{Argv: []string{"ccx", "vcs", "worktree", "rm", "--path", worktree}, Dir: strings.TrimSpace(repository)})
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		return describeExit(done.Stderr, "ccx vcs worktree rm exited %d", done.Code), nil
	}
	return "", err
}

func (g *Git) advertised(ctx context.Context, repository string) (advertised, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if known, ok := g.cache[repository]; ok {
		return known, nil
	}
	out, err := g.checked(ctx, repository, "ls-remote", "--symref", "origin", "HEAD", "refs/heads/*")
	if err != nil {
		return advertised{}, err
	}
	read := advertised{heads: map[string]bool{}}
	for line := range strings.Lines(out) {
		ref, name, found := strings.Cut(strings.TrimRight(line, "\n"), "\t")
		switch {
		case !found:
		case name == "HEAD" && strings.HasPrefix(ref, "ref: "):
			read.trunk = strings.TrimPrefix(ref, "ref: refs/heads/")
		case strings.HasPrefix(name, "refs/heads/"):
			read.heads[ref] = true
		}
	}
	if read.trunk == "" {
		return advertised{}, fmt.Errorf("origin of %s advertises no HEAD", repository)
	}
	if g.cache == nil {
		g.cache = map[string]advertised{}
	}
	g.cache[repository] = read
	return read, nil
}

func (g *Git) OnTrunk(ctx context.Context, head, repository string) (bool, error) {
	read, err := g.advertised(ctx, repository)
	if err != nil {
		return false, err
	}
	done, err := g.run(ctx, repository, "merge-base", "--is-ancestor", head, "origin/"+read.trunk)
	return done.Code == 0, err
}

func (g *Git) Remote(ctx context.Context, repository string) (Remote, error) {
	read, err := g.advertised(ctx, repository)
	if err != nil {
		return Remote{}, err
	}
	if _, err := g.run(ctx, repository, "fetch", "--quiet", "origin", read.trunk); err != nil {
		return Remote{}, err
	}
	subjects, err := g.run(ctx, repository, "log", "origin/"+read.trunk, "--since=30.days", "--format=%s")
	if err != nil {
		return Remote{}, err
	}
	squashed := map[int]bool{}
	for line := range strings.Lines(subjects.Stdout) {
		if match := squashSubject.FindStringSubmatch(strings.TrimRight(line, "\n")); match != nil {
			number, err := strconv.Atoi(match[1])
			if err != nil {
				return Remote{}, err
			}
			squashed[number] = true
		}
	}
	origin, err := g.checked(ctx, repository, "remote", "get-url", "origin")
	if err != nil {
		return Remote{}, err
	}
	return Remote{Heads: read.heads, Landed: PullRequests{Runner: g.Runner, Origin: strings.TrimSpace(origin), Squashed: squashed}}, nil
}

type closedPull struct {
	Number int    `json:"number"`
	Head   string `json:"headRefOid"`
}

type PullRequests struct {
	Runner   shell.Runner
	Origin   string
	Squashed map[int]bool
}

func (p PullRequests) Landed(ctx context.Context, head string) (bool, error) {
	result, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"gh", "pr", "list", "--repo", p.Origin, "--state", "closed", "--search", head, "--json", "number,headRefOid"}, Idempotent: true})
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		return false, githubRefusal{describeExit(result.Stderr, "gh exited %d", result.Code)}
	}
	if err != nil {
		return false, err
	}
	var pulls []closedPull
	if err := json.Unmarshal([]byte(result.Stdout), &pulls); err != nil {
		return false, err
	}
	return slices.ContainsFunc(pulls, func(pull closedPull) bool {
		return p.Squashed[pull.Number] && pull.Head == head
	}), nil
}

type githubRefusal struct{ reason string }

func (r githubRefusal) Error() string { return "GitHub could not answer: " + r.reason }

type Sprite struct {
	Workspace   string
	Host        string
	Root        string
	Environment string
}

type SpriteKey struct {
	Worktree string
	Terminal string
}

type SpriteFleet interface {
	Sprites() (map[SpriteKey]Sprite, error)
	Checkout(ctx context.Context, sprite Sprite) (CheckoutRead, error)
	Remote(checkout Checkout) Remote
	Holder(ctx context.Context, sprite Sprite) (string, error)
	Destroy(ctx context.Context, sprite Sprite) (string, error)
}

type CCRemote struct {
	Runner shell.Runner
	State  string
}

type ccRemoteTask struct {
	Schema      int    `json:"schemaVersion"`
	Provider    string `json:"provider"`
	Workspace   string `json:"workspace"`
	ProjectRoot string `json:"projectRoot"`
	Environment string `json:"environmentId"`
	Worktree    string `json:"worktreeId"`
	Terminal    string `json:"terminal"`
	Forward     *struct {
		Host string `json:"host"`
	} `json:"forward"`
}

func (c CCRemote) Sprites() (map[SpriteKey]Sprite, error) {
	paths, err := filepath.Glob(filepath.Join(c.State, "orca", "*.json"))
	if err != nil {
		return nil, err
	}
	hosted := map[SpriteKey]Sprite{}
	for _, path := range paths {
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		var task ccRemoteTask
		if err := json.Unmarshal(raw, &task); err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		if task.Schema != ccRemoteTaskSchema || task.Provider != providerSprites || task.Forward == nil || task.Worktree == "" || task.Terminal == "" {
			continue
		}
		if info, err := os.Stat(filepath.Join(c.State, "workspaces", task.Workspace+".json")); err != nil || !info.Mode().IsRegular() {
			continue
		}
		hosted[SpriteKey{task.Worktree, task.Terminal}] = Sprite{task.Workspace, task.Forward.Host, task.ProjectRoot, task.Environment}
	}
	return hosted, nil
}

func shellQuote(word string) string {
	if shellSafe.MatchString(word) {
		return word
	}
	return "'" + strings.ReplaceAll(word, "'", `'"'"'`) + "'"
}

func spriteExec(sprite Sprite, argv ...string) []string {
	return append([]string{"sprite", "exec", "-s", sprite.Host, "--no-stdin", "--no-port-forward", "--"}, argv...)
}

func (c CCRemote) Holder(ctx context.Context, sprite Sprite) (string, error) {
	result, err := c.Runner.Run(ctx, shell.Command{Argv: spriteExec(sprite, "sh", "-c", spriteHolders, "sh", sprite.Root), Timeout: spriteReadTimeout})
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		return fmt.Sprintf("sprite exec on %s could not read its processes: %s", sprite.Host, describeExit(result.Stderr, "exit %d", result.Code)), nil
	}
	if err != nil {
		return "", err
	}
	if held := strings.Fields(result.Stdout); len(held) > 0 {
		return "processes " + strings.Join(held, ",") + " still run on it", nil
	}
	return "", nil
}

func (c CCRemote) Checkout(ctx context.Context, sprite Sprite) (CheckoutRead, error) {
	result, err := c.Runner.Run(ctx, shell.Command{Argv: spriteExec(sprite, "sh", "-c", spriteCheckout, "sh", sprite.Root), Timeout: spriteReadTimeout})
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		return CheckoutRead{Refusal: fmt.Sprintf("sprite exec on %s could not read %s: %s", sprite.Host, sprite.Root, describeExit(result.Stderr, "exit %d", result.Code))}, nil
	}
	if err != nil {
		return CheckoutRead{}, err
	}
	origin, rest, _ := strings.Cut(result.Stdout, "\n")
	offOrigin, status, _ := strings.Cut(rest, "\n")
	hub := githubOrigin.FindStringSubmatch(origin)
	if hub == nil {
		return CheckoutRead{Refusal: sprite.Root + " has no GitHub origin to check its HEAD against"}, nil
	}
	checkout := Checkout{Path: sprite.Root, Repository: hub[1]}
	if checkout.OffOrigin, err = strconv.Atoi(strings.TrimSpace(offOrigin)); err != nil {
		return CheckoutRead{}, err
	}
	for line := range strings.Lines(status) {
		line = strings.TrimRight(line, "\n")
		if !strings.HasPrefix(line, "# ") {
			checkout.Dirty = true
			continue
		}
		name, value, _ := strings.Cut(line[2:], " ")
		switch name {
		case "branch.oid":
			checkout.Head = value
		case "branch.head":
			if value != "(detached)" {
				checkout.Branch = value
			}
		}
	}
	checkout.OnTrunk, err = c.onTrunk(ctx, checkout.Head, checkout.Repository)
	var refused githubRefusal
	if errors.As(err, &refused) {
		return CheckoutRead{Refusal: refused.Error()}, nil
	}
	if err != nil {
		return CheckoutRead{}, err
	}
	return CheckoutRead{Found: &checkout}, nil
}

func (c CCRemote) github(ctx context.Context, path string, into any) (bool, error) {
	result, err := c.Runner.Run(ctx, shell.Command{Argv: []string{"gh", "api", path}})
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		if githubUnknown.MatchString(result.Stderr) {
			return false, nil
		}
		return false, githubRefusal{describeExit(result.Stderr, "gh exited %d", result.Code)}
	}
	if err != nil {
		return false, err
	}
	return true, json.Unmarshal([]byte(result.Stdout), into)
}

func (c CCRemote) trunk(ctx context.Context, slug string) (string, error) {
	var repo struct {
		Trunk string `json:"default_branch"`
	}
	_, err := c.github(ctx, "repos/"+slug, &repo)
	return repo.Trunk, err
}

func (c CCRemote) onTrunk(ctx context.Context, head, slug string) (bool, error) {
	trunk, err := c.trunk(ctx, slug)
	if err != nil {
		return false, err
	}
	var compared struct {
		Status string `json:"status"`
	}
	found, err := c.github(ctx, fmt.Sprintf("repos/%s/compare/%s...%s?per_page=1", slug, trunk, head), &compared)
	return found && (compared.Status == "behind" || compared.Status == "identical"), err
}

func (c CCRemote) Remote(checkout Checkout) Remote {
	return Remote{Landed: githubLandings{c, checkout.Repository}}
}

type githubLandings struct {
	fleet CCRemote
	slug  string
}

func (l githubLandings) Landed(ctx context.Context, head string) (bool, error) {
	var branches []struct{}
	if _, err := l.fleet.github(ctx, fmt.Sprintf("repos/%s/commits/%s/branches-where-head", l.slug, head), &branches); err != nil || len(branches) > 0 {
		return len(branches) > 0, err
	}
	trunk, err := l.fleet.trunk(ctx, l.slug)
	if err != nil {
		return false, err
	}
	var pulls []struct {
		Merged string `json:"merged_at"`
		Head   struct {
			SHA string `json:"sha"`
		} `json:"head"`
		Base struct {
			Ref string `json:"ref"`
		} `json:"base"`
	}
	if _, err := l.fleet.github(ctx, fmt.Sprintf("repos/%s/commits/%s/pulls", l.slug, head), &pulls); err != nil {
		return false, err
	}
	for _, pull := range pulls {
		if pull.Merged != "" && pull.Head.SHA == head && pull.Base.Ref == trunk {
			return true, nil
		}
	}
	return false, nil
}

func (c CCRemote) tool(ctx context.Context) (string, []string, error) {
	top, err := c.Runner.Run(ctx, shell.Command{Argv: []string{"git", "rev-parse", "--show-toplevel"}})
	var outside *shell.ExitError
	if err != nil && !errors.As(err, &outside) {
		return "", nil, err
	}
	tools := filepath.Join(strings.TrimSpace(top.Stdout), "tools", "cc-remote")
	found := func(env, path string) string {
		if set := os.Getenv(env); set != "" {
			return set
		}
		if info, err := os.Stat(filepath.Join(tools, path)); err == nil && info.Mode().IsRegular() {
			return filepath.Join(tools, path)
		}
		return ""
	}
	command := cmp.Or(found(ccRemoteEnv, filepath.Join("bin", "cc-remote")), "cc-remote")
	config := found(ccRemoteConfigEnv, "config.yaml")
	if config == "" {
		return command, nil, nil
	}
	return command, []string{ccRemoteConfigEnv + "=" + config}, nil
}

func (c CCRemote) Destroy(ctx context.Context, sprite Sprite) (string, error) {
	command, env, err := c.tool(ctx)
	if err != nil {
		return "", err
	}
	result, err := c.Runner.Run(ctx, shell.Command{Argv: []string{command, "destroy", sprite.Workspace}, Env: env})
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		return describeExit(result.Stderr, "cc-remote destroy exited %d", result.Code), nil
	}
	return "", err
}
