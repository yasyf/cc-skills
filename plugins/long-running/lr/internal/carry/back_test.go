package carry

import (
	"context"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const reported = "overdue-picks-h-1: 22 picks judged, root-posts.zsh written\n\nJudged all 22 overdue picks and wrote one command per pick to root-posts.zsh (on this Sprite at /home/sprite/drive/carried/Users/me/.claude/scratch/durable-agents-rebuild/overdue-picks-h/); I posted the report to root and the done record."

func TestASpriteRecordNamingItsCarriedDirectoryCopiesItToTheSameMacPathKeepingWhatTheMacHolds(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("packed", "sprite", "exec")
	fake.Reply("", "tar")

	if left := Back(context.Background(), fake, "pool-qtqxmz2ocg6nnb6o", "/Users/me", []string{reported, "again=" + box + "/Users/me/.claude/scratch/durable-agents-rebuild/overdue-picks-h"}); len(left) > 0 {
		t.Fatalf("left behind %q", left)
	}

	calls := fake.Calls()
	if len(calls) != 2 {
		t.Fatalf("ran %d commands, want the Sprite's pack and the Mac's unpack once: %v", len(calls), calls)
	}
	if want := []string{"sprite", "exec", "-s", "pool-qtqxmz2ocg6nnb6o", "--no-stdin", "--", "tar", "-C", box + "/Users/me", "-czf", "-", ".claude/scratch/durable-agents-rebuild/overdue-picks-h"}; !slices.Equal(calls[0].Argv, want) {
		t.Fatalf("packed with %q, want %q", calls[0].Argv, want)
	}
	if want := []string{"tar", "-C", "/Users/me", "-xzkf", "-"}; !slices.Equal(calls[1].Argv, want) || calls[1].Stdin != "packed" {
		t.Fatalf("unpacked with %q from %q, want %q from the Sprite's pack", calls[1].Argv, calls[1].Stdin, want)
	}
}

func TestACarriedFileThatCannotCopyIsLeftBehindNamingWhy(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(2, "tar: Users/me/.vcache/scratch/x.md: Cannot stat: No such file or directory", "sprite", "exec")

	left := Back(context.Background(), fake, "pool-1", "/Users/me", []string{"table at " + box + "/Users/me/.vcache/scratch/x.md.", "rc at " + box + "/Users/me/.zshrc"})

	if len(left) != 2 || !strings.Contains(left[0], box+"/Users/me/.vcache/scratch/x.md (its copy from Sprite pool-1 failed") || !strings.Contains(left[0], "Cannot stat") {
		t.Fatalf("left %q, want the failed copy named with the Sprite's error", left)
	}
	if left[1] != box+"/Users/me/.zshrc (no directory carry shares with the Mac holds it)" {
		t.Fatalf("left %q, want the path outside carry's directories refused", left[1])
	}
	if calls := fake.Calls(); len(calls) != 1 {
		t.Fatalf("ran %v, want no unpack after a failed pack and no copy outside carry's directories", calls)
	}
}
