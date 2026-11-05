package awake

import (
	"bytes"
	"context"
	"io/fs"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	WorkEvery     = 5 * time.Minute
	WorkRefs      = "refs/lr/work/"
	CheckoutsFile = ".local/state/lr/work-checkouts"
	laneVariable  = "CLAUDE_LONG_RUNNING_LANE"
	checkoutDepth = 3
)

const carryWork = `set -eu
cd "$1"
case $(git config remote.origin.url) in *github.com*) ;; *) exit 0 ;; esac
index=$(mktemp)
trap 'rm -f "$index"' EXIT
cp "$(git rev-parse --git-path index)" "$index"
export GIT_INDEX_FILE="$index"
git add -A
tree=$(git write-tree)
head=$(git rev-parse HEAD)
unpushed=$(git rev-list HEAD --branches --not --remotes)
[ -n "$unpushed" ] || [ "$tree" != "$(git rev-parse "HEAD^{tree}")" ] || exit 0
parents=$head
said="HEAD $(git symbolic-ref -q --short HEAD || echo detached) $head"
for branch in $(git for-each-ref --format='%(objectname):%(refname:short)' refs/heads); do
  tip=${branch%%:*}
  case "$unpushed" in *"$tip"*) ;; *) continue ;; esac
  said="$said
branch ${branch#*:} $tip"
  case " $parents " in *" $tip "*) ;; *) parents="$parents $tip" ;; esac
done
[ "$(git log -1 --format='%T %P' "$2" 2>/dev/null)" != "$tree $parents" ] || exit 0
commit=$(printf 'lane work in %s\n\n%s\n' "$1" "$said" | git commit-tree "$tree" $(printf -- '-p %s ' $parents))
git push --quiet --no-verify origin "+$commit:$2"
git update-ref "$2" "$commit"
echo "$commit"`

type Work struct {
	Runner shell.Runner
	Proc   string
	Home   string
	Every  time.Duration
	Log    *slog.Logger
}

func (w Work) Run(ctx context.Context) {
	for {
		w.carry(ctx)
		select {
		case <-ctx.Done():
			return
		case <-time.After(w.Every):
		}
	}
}

func (w Work) carry(ctx context.Context) {
	lane, env := w.agent()
	if lane == "" {
		return
	}
	checkouts, err := Checkouts(w.Home)
	if err != nil {
		w.Log.Warn("the Sprite's checkouts did not read, so no work was carried", "err", err)
		return
	}
	if err := w.record(checkouts); err != nil {
		w.Log.Warn("the Sprite's checkouts were not recorded for gc", "err", err)
	}
	for _, checkout := range checkouts {
		result, err := w.Runner.Run(ctx, shell.Command{Argv: []string{"sh", "-c", carryWork, "sh", checkout, WorkRefs + lane}, Env: env, Timeout: w.Every})
		if err != nil {
			w.Log.Warn("a checkout's work did not reach its ref", "checkout", checkout, "ref", WorkRefs+lane, "err", err)
			continue
		}
		if pushed := strings.TrimSpace(result.Stdout); pushed != "" {
			w.Log.Info("a checkout's work reached its ref", "checkout", checkout, "ref", WorkRefs+lane, "commit", pushed)
		}
	}
}

// The lane's GitHub credential lives only in its agent's environment: cc-remote deletes the key file once the agent starts.
func (w Work) agent() (string, []string) {
	pids, err := filepath.Glob(filepath.Join(w.Proc, "[0-9]*"))
	if err != nil {
		return "", nil
	}
	for _, pid := range pids {
		comm, err := os.ReadFile(filepath.Join(pid, "comm"))
		if err != nil || !slices.Contains([]string{"claude", "codex"}, strings.TrimSpace(string(comm))) {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(pid, "environ"))
		if err != nil {
			continue
		}
		lane, env := "", []string{}
		for _, variable := range bytes.Split(raw, []byte{0}) {
			name, value, _ := strings.Cut(string(variable), "=")
			switch {
			case name == laneVariable:
				lane = value
			case name == "GH_TOKEN" || name == "GITHUB_TOKEN" || strings.HasPrefix(name, "GIT_CONFIG_"):
				env = append(env, string(variable))
			}
		}
		if lane != "" {
			return lane, env
		}
	}
	return "", nil
}

func (w Work) record(checkouts []string) error {
	path := filepath.Join(w.Home, CheckoutsFile)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	if err := os.WriteFile(path+".new", []byte(strings.Join(checkouts, "\n")+"\n"), 0o600); err != nil {
		return err
	}
	return os.Rename(path+".new", path)
}

func Checkouts(home string) ([]string, error) {
	var found []string
	err := filepath.WalkDir(home, func(path string, entry fs.DirEntry, err error) error {
		if err != nil || path == home {
			return err
		}
		if !entry.IsDir() {
			return nil
		}
		if strings.HasPrefix(entry.Name(), ".") {
			return filepath.SkipDir
		}
		if _, err := os.Lstat(filepath.Join(path, ".git")); err == nil {
			found = append(found, path)
		}
		if strings.Count(strings.TrimPrefix(path, home), string(filepath.Separator)) >= checkoutDepth {
			return filepath.SkipDir
		}
		return nil
	})
	return found, err
}
