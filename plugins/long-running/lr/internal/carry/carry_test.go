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

func TestAHelperNamedThroughAVariableTravelsAndItsReferenceReadsTheCopy(t *testing.T) {
	c, _ := carrier(t)
	drive := filepath.Join(c.Home, ".claude/scratch/drive")
	c.Vars = map[string]string{DriveDir: drive}
	put(t, drive+"/go-desk-2/carry.py", "print('carried')")
	put(t, drive+"/fg/bin.zsh", "echo fg")
	put(t, c.Home+"/notes.md", "notes")

	rewritten, err := c.Carry(context.Background(), `python3 "$DRIVE_DIR/go-desk-2/carry.py" --post; ${DRIVE_DIR}/fg/bin.zsh; cat $HOME/notes.md`)
	if err != nil {
		t.Fatal(err)
	}

	want := `python3 "` + box + drive + `/go-desk-2/carry.py" --post; ` + box + drive + `/fg/bin.zsh; cat ` + box + c.Home + `/notes.md`
	if rewritten != want {
		t.Fatalf("rewritten %q, want %q", rewritten, want)
	}
	if got := landed(t, c, box+drive+"/go-desk-2/carry.py"); got != "print('carried')" {
		t.Fatalf("carried %q", got)
	}
}

func TestAVariableTheBriefAssignsItselfExpands(t *testing.T) {
	c, _ := carrier(t)
	root := filepath.Join(c.Home, "lane")
	put(t, root+"/items.md", "items")

	rewritten, err := c.Carry(context.Background(), "Read `$B/items.md` once.\nexport B="+root+"/\n")
	if err != nil {
		t.Fatal(err)
	}

	if !strings.Contains(rewritten, box+root+"//items.md") && !strings.Contains(rewritten, box+root+"/items.md") {
		t.Fatalf("rewritten %q does not read the copy", rewritten)
	}
}

func TestAVariablePathThatDoesNotExistYetIsRewrittenAndNeverRefused(t *testing.T) {
	c, _ := carrier(t)
	drive := filepath.Join(c.Home, "drive")
	c.Vars = map[string]string{DriveDir: drive}

	rewritten, err := c.Carry(context.Background(), "Write $DRIVE_DIR/lane/NOTES.md as you go.")
	if err != nil {
		t.Fatal(err)
	}

	if rewritten != "Write "+box+drive+"/lane/NOTES.md as you go." {
		t.Fatalf("rewritten %q", rewritten)
	}
}

func TestAVariableNoOneDefinedOrATemplateUnderOnePassesThrough(t *testing.T) {
	c, _ := carrier(t)
	c.Vars = map[string]string{DriveDir: filepath.Join(c.Home, "drive")}
	text := "cd $DESK_WT/plugins; run $DRIVE_DIR/<lane>/carry.py; ls $DRIVE_DIR"

	rewritten, err := c.Carry(context.Background(), text)
	if err != nil {
		t.Fatal(err)
	}

	if rewritten != text {
		t.Fatalf("rewritten %q, want it unchanged", rewritten)
	}
}

func TestACredentialNamedThroughAVariableNeverLeavesTheMac(t *testing.T) {
	c, _ := carrier(t)
	put(t, c.Home+"/.ssh/id", "secret")

	fault := refused(t, c, "cat $HOME/.ssh/id")

	if !strings.Contains(fault, "credential path") {
		t.Fatalf("refusal %q does not name the credential", fault)
	}
}

func TestACarriedBriefBringsItsDirectoryAndWhatItNamesAndWhatItCannotCarryStaysBehind(t *testing.T) {
	c, _ := carrier(t)
	c.Vars = map[string]string{DriveDir: c.Home + "/drive"}
	desk := c.Home + "/drive/desk-24"
	put(t, desk+"/bin/fg.zsh", "grade\n")
	put(t, desk+"/env.sh", "export DRIVE_DIR\n")
	put(t, desk+"/state/handoff.md", "next head\n")
	put(t, c.Home+"/drive/lr.json", "{}\n")
	put(t, c.Home+"/.ssh/id_ed25519", "secret\n")
	put(t, desk+"/brief.md", "Start with `. "+desk+"/env.sh "+c.Home+"/drive/lr.json`, then `zsh \"$DRIVE_DIR/desk-24/bin/fg.zsh\"`. Key "+c.Home+"/.ssh/id_ed25519. Old notes "+c.Home+"/drive/gone/notes.txt. Read its state/handoff.md.\n")

	out := carried(t, c, "Read "+desk+"/brief.md and do the same.\n")

	onBox := box + desk + "/brief.md"
	if !strings.Contains(out, onBox) {
		t.Fatalf("brief reads %q", out)
	}
	nested := landed(t, c, onBox)
	for _, want := range []string{box + desk + "/env.sh", box + c.Home + "/drive/lr.json", box + c.Home + "/drive/desk-24/bin/fg.zsh", c.Home + "/.ssh/id_ed25519", c.Home + "/drive/gone/notes.txt", "its state/handoff.md"} {
		if !strings.Contains(nested, want) {
			t.Errorf("nested brief lacks %q: %s", want, nested)
		}
	}
	for _, want := range []string{"/bin/fg.zsh", "/env.sh", "/state/handoff.md"} {
		if got := landed(t, c, box+desk+want); got == "" {
			t.Errorf("%s did not travel", want)
		}
	}
	if _, err := os.Stat(c.Into + c.Home + "/.ssh/id_ed25519"); err == nil {
		t.Error("a credential travelled")
	}
}

func TestBriefsNamingEachOtherCarryOnceAndEnd(t *testing.T) {
	c, _ := carrier(t)
	one, two := c.Home+"/a/one.brief.md", c.Home+"/b/two.brief.md"
	put(t, one, "then "+two+"\n")
	put(t, two, "back to "+one+"\n")

	out := carried(t, c, "Read "+one+"\n")

	if !strings.Contains(landed(t, c, box+two), box+one) || !strings.Contains(out, box+one) {
		t.Fatalf("out %q", out)
	}
}

func TestAMissingPathInTheBriefItselfStillRefusesWhileANestedOneDoesNot(t *testing.T) {
	c, _ := carrier(t)
	put(t, c.Home+"/a/one.brief.md", "old "+c.Home+"/gone.txt\n")

	carried(t, c, "Read "+c.Home+"/a/one.brief.md\n")
	if got := refused(t, c, "Read "+c.Home+"/gone.txt\n"); !strings.Contains(got, "does not exist") {
		t.Fatal(got)
	}
}
