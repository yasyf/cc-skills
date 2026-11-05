package lanes

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	notesCursor        = "notes-published"
	notesSyncTimeout   = 2 * time.Minute
	notesSyncBudget    = 60
	exitNotesDeferred  = 75
	publishNotesScript = `IFS= read -r NOTES_TOKEN
export NOTES_TOKEN CCN_LOCK_WAIT=5s GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0= GIT_CONFIG_KEY_1=credential.helper
export GIT_CONFIG_VALUE_1='!f() { echo username=x-access-token; echo "password=$NOTES_TOKEN"; }; f'
cd "$1" || exit 1
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
origin=$scratch/origin
git ls-remote origin 'refs/cc-notes/*' > "$origin" || { echo "could not read origin's cc-notes refs" >&2; exit 1; }
rows=$(git for-each-ref --format='%(refname) %(objectname)' refs/cc-notes/ | awk 'FILENAME == ARGV[1] { theirs[$2] = $1; next } theirs[$1] != $2 { print $1 "," $2 "," theirs[$1] }' "$origin" -)
moved=$(printf '%s\n' "$rows" | awk -F, '$3 != "" { print $1 }')
[ -z "$moved" ] || git fetch -q --no-write-fetch-head origin $moved 2> /dev/null
started=$(date +%s)
status=0
for row in $rows; do
  ref=${row%%,*} theirs=${row##*,} tip=${row#*,}
  tip=${tip%,*}
  [ -n "$theirs" ] && git merge-base --is-ancestor "$tip" "$theirs" 2> /dev/null && continue
  [ $(( $(date +%s) - started )) -lt "$2" ] || exit 75
  if ! ccn sync --prefix "$ref" --json 2> "$scratch/err"; then
    cat "$scratch/err" >&2
    grep -qE 'retry shortly|authority busy' "$scratch/err" && exit 75
    status=1
    echo "$ref did not reach origin" >&2
  fi
done
exit $status`
)

var (
	ErrSpriteGone    = errors.New("its Sprite no longer exists")
	ErrNotesDeferred = errors.New("ccn on its Sprite is importing, busy or out of time, and publishes the rest on the next pass")
)

type spriteSyncError struct {
	reason string
	cause  error
}

func (e *spriteSyncError) Error() string { return e.reason }

func (e *spriteSyncError) Unwrap() error { return e.cause }

func spriteSyncFailure(sprite string, err error) error {
	var exit *shell.ExitError
	switch {
	case errors.As(err, &exit) && exit.Code == exitNotesDeferred:
		return fmt.Errorf("ccn sync on Sprite %s: %w", sprite, ErrNotesDeferred)
	case errors.As(err, &exit):
		first, _, _ := strings.Cut(strings.TrimSpace(exit.Stderr), "\n")
		return &spriteSyncError{reason: fmt.Sprintf("ccn sync on Sprite %s exited %d: %s", sprite, exit.Code, first), cause: err}
	case errors.Is(err, context.DeadlineExceeded):
		return &spriteSyncError{reason: fmt.Sprintf("ccn sync on Sprite %s ran past %s", sprite, notesSyncTimeout), cause: err}
	}
	return fmt.Errorf("ccn sync on Sprite %s: %w", sprite, err)
}

func publishSpriteNotes(ctx context.Context, runner shell.Runner, sprite, checkout, token string) error {
	result, err := runner.Run(ctx, shell.Command{Argv: []string{"sprite", "exec", "-s", sprite, "--", "sh", "-c", publishNotesScript, "sh", checkout, strconv.Itoa(notesSyncBudget)}, Stdin: token + "\n", Timeout: notesSyncTimeout})
	if err != nil {
		return spriteSyncFailure(sprite, err)
	}
	var kept []string
	reports := json.NewDecoder(strings.NewReader(result.Stdout))
	for {
		var report struct {
			Defaulted []struct {
				Kind   string `json:"kind"`
				ID     string `json:"id"`
				Reason string `json:"reason"`
			} `json:"defaulted"`
		}
		err := reports.Decode(&report)
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return fmt.Errorf("ccn sync on Sprite %s printed %q: %w", sprite, result.Stdout, err)
		}
		for _, one := range report.Defaulted {
			kept = append(kept, fmt.Sprintf("%s %s (%s)", one.Kind, one.ID, one.Reason))
		}
	}
	if len(kept) == 0 {
		return nil
	}
	return fmt.Errorf("a local default keeps these on Sprite %s, off origin: %s. Publish each with `ccn local unmark <id>`, or keep it deliberately with `ccn local mark <id>`", sprite, strings.Join(kept, "; "))
}

func (e Env) publishNotes(ctx context.Context, token func() (string, error), lane string) error {
	receipt := receiptOf(e.receipts(), lane)
	sprite := receipt.Result.Server.Name
	if sprite == "" {
		return nil
	}
	checkout, ok := receipt.worktree()
	if !ok {
		return fmt.Errorf("the launch receipt of %s on Sprite %s names no worktree", lane, sprite)
	}
	secret, err := token()
	if err != nil {
		return fmt.Errorf("read the GitHub token for Sprite %s: %w", sprite, err)
	}
	synced := publishSpriteNotes(ctx, e.Runner, sprite, checkout, secret)
	var exit *shell.ExitError
	if !errors.As(synced, &exit) {
		return synced
	}
	listed, err := e.laneSprites(ctx, []string{lane})
	if err == nil && len(listed) == 0 {
		return fmt.Errorf("%w: %s", ErrSpriteGone, sprite)
	}
	return synced
}

func (r launchReceipt) worktree() (string, bool) {
	for _, effect := range r.Result.Effects {
		if effect.Kind == "worktree" {
			_, path, found := strings.Cut(effect.ID, "::")
			return path, found
		}
	}
	return "", false
}

type NotesPublish struct {
	Store    *store.Store
	Deps     Deps
	deferred []string
}

func NewNotesPublish(s *store.Store, d Deps) (_ *NotesPublish, err error) {
	defer recovered(&err)
	if must(s.Cursor(notesCursor)) == 0 {
		do(s.Advance(notesCursor, must(s.Cursor(CursorName))))
	}
	return &NotesPublish{Store: s, Deps: d}, nil
}

func (n *NotesPublish) Check(_ context.Context, now time.Time) (err error) {
	defer recovered(&err)
	folded := must(n.Store.Cursor(CursorName))
	from := must(n.Store.Cursor(notesCursor))
	if from == 0 {
		return n.Store.Advance(notesCursor, folded)
	}
	writers := n.deferred
	if folded > from {
		for _, r := range must(n.Deps.Records(from)) {
			if r.Seq <= folded {
				writers = append(writers, Canonical(n.Deps.Drive, r.Lane))
			}
		}
	}
	n.deferred = publishWritten(n.Store, n.Deps, writers, now.UTC().Truncate(time.Second))
	return n.Store.Advance(notesCursor, max(folded, from))
}

type laneLocks struct {
	guard sync.Mutex
	held  map[string]*sync.Mutex
}

func (l *laneLocks) hold(lane string) func() {
	l.guard.Lock()
	one, found := l.held[lane]
	if !found {
		one = &sync.Mutex{}
		l.held[lane] = one
	}
	l.guard.Unlock()
	one.Lock()
	return one.Unlock
}

func notesOwed(s *store.Store, d Deps, name string) error {
	err := d.PublishNotes(name)
	if !errors.Is(err, ErrSpriteGone) {
		return err
	}
	do(d.Post(Post{Kind: "blocker", To: []string{noticeTo(s, name), name}, LaneRef: name, Text: fmt.Sprintf("%s ended and %v, so any note it wrote there and never published is lost.", name, err)}))
	return nil
}

func (out *folded) orderedOff(s *store.Store, name string, fresh bool, at time.Time) bool {
	if fresh {
		return true
	}
	one, owes := deletionsOwed(s, out.deps, name)
	if owes {
		hold(s, name, one.owes(), at)
		out.owing = append(out.owing, one)
		return false
	}
	if err := notesOwed(s, out.deps, name); err != nil {
		do(out.deps.Post(Post{Kind: "blocker", To: []string{noticeTo(s, name), name}, LaneRef: name, Text: fmt.Sprintf("%s was stood down by order with cc-notes that did not reach origin: %v. Its Sprite still holds them until it is reclaimed.", name, err)}))
	}
	return true
}

func publishWritten(s *store.Store, d Deps, names []string, now time.Time) (deferred []string) {
	var writers []string
	read, seen := map[string]bool{}, map[string]bool{}
	for _, name := range names {
		if read[name] {
			continue
		}
		read[name] = true
		lane, found := laneOf(s, name)
		if found && isLive(lane.State) && !seen[lane.Name] {
			seen[lane.Name] = true
			writers = append(writers, lane.Name)
		}
	}
	failed := make([]error, len(writers))
	var wg sync.WaitGroup
	for i, name := range writers {
		wg.Go(func() { failed[i] = d.PublishNotes(name) })
	}
	wg.Wait()
	for i, name := range writers {
		subject := "notes:" + name
		if errors.Is(failed[i], ErrNotesDeferred) {
			deferred = append(deferred, name)
			continue
		}
		if failed[i] == nil {
			do(s.ClearEscalation(subject))
			continue
		}
		if must(s.Escalate(subject, failed[i].Error(), name, now)) {
			do(d.Post(Post{Kind: "blocker", To: []string{name, noticeTo(s, name)}, LaneRef: name, Text: fmt.Sprintf("%s has cc-notes that did not reach origin, so no other lane can read them: %v. The supervisor retries at its next record and before it retires.", name, failed[i])}))
		}
	}
	return deferred
}
