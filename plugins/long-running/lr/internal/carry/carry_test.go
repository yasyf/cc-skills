package carry

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const box = "/home/sprite/drive/carried"

func carrier(t *testing.T) (Carrier, *shelltest.Fake) {
	t.Helper()
	fake := &shelltest.Fake{}
	return Carrier{Runner: fake, Home: t.TempDir(), Repo: "/drive/checkout", Into: t.TempDir(), Box: box}, fake
}

func put(t *testing.T, name, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(name), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(name, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
}

func landed(t *testing.T, c Carrier, onBox string) string {
	t.Helper()
	rel, ok := strings.CutPrefix(onBox, c.Box)
	if !ok {
		t.Fatalf("%s is not under the box coordinator %s", onBox, c.Box)
	}
	data, err := os.ReadFile(filepath.Join(c.Into, rel))
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func carried(t *testing.T, c Carrier, brief string) string {
	t.Helper()
	out, err := c.Carry(context.Background(), brief)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func refused(t *testing.T, c Carrier, brief string) string {
	t.Helper()
	out, err := c.Carry(context.Background(), brief)
	if err == nil {
		t.Fatalf("carried a brief that should be refused, as %q", out)
	}
	return err.Error()
}

func TestAMacFileAndACcnDocTheBriefNamesAreReadableAtTheirRewrittenPaths(t *testing.T) {
	c, fake := carrier(t)
	plan := filepath.Join(c.Home, ".claude/scratch/drive/one-reader-2/plan.md")
	put(t, plan, "the plan\n")
	fake.Reply("register rows\n", "ccn", "-R", c.Repo, "doc", "show", "c3230a2")
	brief := "Read " + plan + " first, then ~/.claude/scratch/drive/one-reader-2/plan.md again.\nOther rows: `ccn doc show c3230a2`.\n"

	out := carried(t, c, brief)

	want := "Read " + box + plan + " first, then " + box + plan + " again.\nOther rows: `" + box + "/ccn/doc-c3230a2.md`.\n"
	if out != want {
		t.Fatalf("rewritten brief:\n%s\nwant:\n%s", out, want)
	}
	if got := landed(t, c, box+plan); got != "the plan\n" {
		t.Fatalf("plan copy holds %q", got)
	}
	if got := landed(t, c, box+"/ccn/doc-c3230a2.md"); got != "register rows\n" {
		t.Fatalf("doc copy holds %q", got)
	}
	if calls := fake.Calls(); len(calls) != 1 {
		t.Fatalf("ran ccn %d times for one doc named once, want 1", len(calls))
	}
}

func TestTheCcnPlanABriefNamesTravelsWithIt(t *testing.T) {
	c, fake := carrier(t)
	fake.Reply("step 1: relaunch the watchdog\n", "ccn", "-R", c.Repo, "plan", "show", "p-7")

	out := carried(t, c, "Plan p-7 (`ccn plan show p-7`).\n")

	if out != "Plan p-7 (`"+box+"/ccn/plan-p-7.md`).\n" {
		t.Fatalf("rewritten brief %q", out)
	}
	if got := landed(t, c, box+"/ccn/plan-p-7.md"); got != "step 1: relaunch the watchdog\n" {
		t.Fatalf("plan copy holds %q", got)
	}
}

func TestAMissingPathRefusesTheLaunchNamingEveryOne(t *testing.T) {
	c, _ := carrier(t)
	gone := filepath.Join(c.Home, ".claude/scratch/gone.md")
	report := filepath.Join(c.Home, ".claude/scratch/lane/report.md")

	fault := refused(t, c, "Read "+gone+" and write "+report+".\n")

	for _, name := range []string{gone, report} {
		if !strings.Contains(fault, name+" does not exist on this Mac") {
			t.Fatalf("refusal %q does not name %s", fault, name)
		}
	}
}

func TestACredentialPathNeverLeavesTheMac(t *testing.T) {
	c, _ := carrier(t)
	key := filepath.Join(c.Home, ".ssh/id_ed25519")
	put(t, key, "secret\n")
	link := filepath.Join(c.Home, "notes/key")
	if err := os.MkdirAll(filepath.Dir(link), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(key, link); err != nil {
		t.Fatal(err)
	}

	fault := refused(t, c, "Use ~/.ssh/id_ed25519 or "+link+"\n")

	if !strings.Contains(fault, key+" is a credential path") || !strings.Contains(fault, link+" links to") {
		t.Fatalf("refusal %q names neither credential path", fault)
	}
	if entries, _ := os.ReadDir(c.Into); len(entries) != 0 {
		t.Fatalf("copied %d entries of a refused credential", len(entries))
	}
}

func TestADirectoryTravelsWholeAndOneOverTheCapIsRefused(t *testing.T) {
	c, _ := carrier(t)
	logs := filepath.Join(c.Home, "logs")
	put(t, filepath.Join(logs, "a.log"), "a\n")
	put(t, filepath.Join(logs, "nested/b.log"), "b\n")

	out := carried(t, c, "Logs are in "+logs+"/ now.\n")

	if out != "Logs are in "+box+logs+"/ now.\n" {
		t.Fatalf("rewritten brief %q", out)
	}
	if got := landed(t, c, box+logs+"/nested/b.log"); got != "b\n" {
		t.Fatalf("nested copy holds %q", got)
	}

	big := filepath.Join(c.Home, "big")
	put(t, filepath.Join(big, "small.txt"), "x")
	if err := os.Truncate(filepath.Join(big, "small.txt"), maxBytes+1); err != nil {
		t.Fatal(err)
	}
	if fault := refused(t, c, "See "+big+"\n"); !strings.Contains(fault, big+" holds 65 MiB, over the 64 MiB a launch carries") {
		t.Fatalf("refusal %q", fault)
	}
}

func TestTextNamingNothingOnThisMacPassesThroughUnchanged(t *testing.T) {
	c, fake := carrier(t)
	brief := strings.Join([]string{
		"Any path in this brief under " + c.Home + "/ is the drive's Mac, so " + c.Home + "/... is unavailable.",
		"Work from https://github.com/yasyf/cc-skills/pull/1990 and origin/main, and/or /v1/cluster.",
		"The box the coordinator is /home/sprite/drive, and a lane worktree is ~/.claude/worktrees/<lane>.",
		"A brief sits at <state dir>/meta/brief.md, " + c.Home + "/.claude/scratch/durable-agents-rebuil… was cut.",
		"Render one with `ccn doc show <id>`.",
		"",
	}, "\n")

	if out := carried(t, c, brief); out != brief {
		t.Fatalf("rewrote a brief naming nothing on this Mac:\n%s", out)
	}
	if calls := fake.Calls(); len(calls) != 0 {
		t.Fatalf("ran %v for a brief naming no ccn entity", calls)
	}
}

func TestACcnEntityThatDoesNotRenderRefusesNamingIt(t *testing.T) {
	c, fake := carrier(t)
	fake.Fail(1, "not-found: entity not found: no task matches \"t-9\"", "ccn")

	fault := refused(t, c, "Follow `ccn task show t-9`.\n")

	if !strings.Contains(fault, "`ccn task show t-9` failed") || !strings.Contains(fault, "no task matches") {
		t.Fatalf("refusal %q", fault)
	}
}
