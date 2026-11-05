package lanes

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	notesSyncTimeout   = 2 * time.Minute
	publishNotesScript = `IFS= read -r NOTES_TOKEN
export NOTES_TOKEN GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0= GIT_CONFIG_KEY_1=credential.helper
export GIT_CONFIG_VALUE_1='!f() { echo username=x-access-token; echo "password=$NOTES_TOKEN"; }; f'
cd "$1" || exit 1
status=0
for ref in $(git for-each-ref --format='%(objectname) %(refname)' refs/cc-notes/ refs/cc-notes-sync/origin/ | sed 's#refs/cc-notes-sync/origin/#refs/cc-notes/#' | sort | uniq -u | cut -d' ' -f2 | sort -u); do
  tip=$(git rev-parse -q --verify "$ref") || continue
  tracked=$(git rev-parse -q --verify "refs/cc-notes-sync/origin/${ref#refs/cc-notes/}") && git merge-base --is-ancestor "$tip" "$tracked" && continue
  ccn sync --prefix "$ref" --json || { status=1; echo "$ref did not reach origin" >&2; }
done
exit $status`
)

var ErrSpriteGone = errors.New("its Sprite no longer exists")

func publishSpriteNotes(ctx context.Context, runner shell.Runner, sprite, checkout, token string) error {
	result, err := runner.Run(ctx, shell.Command{Argv: []string{"sprite", "exec", "-s", sprite, "--", "sh", "-c", publishNotesScript, "sh", checkout}, Stdin: token + "\n", Timeout: notesSyncTimeout})
	if err != nil {
		return fmt.Errorf("ccn sync on Sprite %s: %w", sprite, err)
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

func publishWritten(s *store.Store, d Deps, records []Record, now time.Time) {
	var writers []string
	seen := map[string]bool{}
	for _, r := range records {
		lane, found := laneOf(s, r.Lane)
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
		if failed[i] == nil {
			do(s.ClearEscalation(subject))
			continue
		}
		if must(s.Escalate(subject, failed[i].Error(), name, now)) {
			do(d.Post(Post{Kind: "blocker", To: []string{name, noticeTo(s, name)}, LaneRef: name, Text: fmt.Sprintf("%s has cc-notes that did not reach origin, so no other lane can read them: %v. The supervisor retries at its next record and before it retires.", name, failed[i])}))
		}
	}
}
