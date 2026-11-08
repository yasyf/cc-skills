package carry

import (
	"context"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const box = "/home/sprite/drive/carried"

func carrier(t *testing.T) (Carrier, *shelltest.Fake) {
	t.Helper()
	fake := &shelltest.Fake{}
	fake.Reply(`{"id":"144ffadb3d797d00"}`, "cci", "node", "--json")
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
	out, _ := c.Carry(context.Background(), brief)
	return out
}

func leftBehind(t *testing.T, c Carrier, brief string) string {
	t.Helper()
	_, left := c.Carry(context.Background(), brief)
	return strings.Join(left, "; ")
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

func TestAMissingPathWarnsNamingEveryOneAndLeavesItsReferenceAlone(t *testing.T) {
	c, _ := carrier(t)
	gone := filepath.Join(c.Home, ".claude/scratch/gone.md")
	report := filepath.Join(c.Home, ".claude/scratch/lane/report.md")

	brief := "Read " + gone + " and write " + report + ".\n"
	left := leftBehind(t, c, brief)

	for _, name := range []string{gone, report} {
		if !strings.Contains(left, name+" does not exist on this Mac") {
			t.Fatalf("warning %q does not name %s", left, name)
		}
	}
	if out := carried(t, c, brief); out != brief {
		t.Fatalf("rewritten brief %q", out)
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

	left := leftBehind(t, c, "Use ~/.ssh/id_ed25519 or "+link+"\n")

	if !strings.Contains(left, key+" lies outside") || !strings.Contains(left, link+" lies outside") {
		t.Fatalf("warning %q does not name both credential paths", left)
	}
	if entries, _ := os.ReadDir(c.Into); len(entries) != 0 {
		t.Fatalf("copied %d entries of a credential", len(entries))
	}
}

func TestADirectoryTravelsWholeAndOneOverTheCapStaysBehind(t *testing.T) {
	c, _ := carrier(t)
	logs := filepath.Join(c.Home, ".claude/scratch/logs")
	put(t, filepath.Join(logs, "a.log"), "a\n")
	put(t, filepath.Join(logs, "nested/b.log"), "b\n")

	out := carried(t, c, "Logs are in "+logs+"/ now.\n")

	if out != "Logs are in "+box+logs+"/ now.\n" {
		t.Fatalf("rewritten brief %q", out)
	}
	if got := landed(t, c, box+logs+"/nested/b.log"); got != "b\n" {
		t.Fatalf("nested copy holds %q", got)
	}

	big := filepath.Join(c.Home, ".claude/scratch/big")
	put(t, filepath.Join(big, "small.txt"), "x")
	if err := os.Truncate(filepath.Join(big, "small.txt"), maxBytes+1); err != nil {
		t.Fatal(err)
	}
	if left := leftBehind(t, c, "See "+big+"\n"); !strings.Contains(left, big+" holds 65 MiB, over the 64 MiB a launch carries") {
		t.Fatalf("warning %q", left)
	}
}

func TestAnotherLanesFifoOrDanglingLinkInTheDrivesScratchNeverStopsTheStagedLane(t *testing.T) {
	c, _ := carrier(t)
	c.Lane = "slack-bugs-4"
	drive := filepath.Join(c.Home, ".local/state/lr-scratch/durable-agents-rebuild")
	put(t, filepath.Join(drive, "slack-bugs-4/notes.md"), "mine\n")
	put(t, filepath.Join(drive, "agentic-stack-build-1/eval-pr-subset/kept.json"), "{}\n")
	fifo := filepath.Join(drive, "agentic-stack-build-1/eval-pr-subset/claim-centrally-means-shared-server.json")
	if err := syscall.Mkfifo(fifo, 0o600); err != nil {
		t.Fatal(err)
	}
	dangling := filepath.Join(drive, "agentic-stack-build-2/eval-pr-subset/gone.json")
	if err := os.MkdirAll(filepath.Dir(dangling), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(c.Home, "missing"), dangling); err != nil {
		t.Fatal(err)
	}

	out, left := c.Carry(context.Background(), "Scratch is "+drive+" here.\n")

	if out != "Scratch is "+box+drive+" here.\n" {
		t.Fatalf("rewritten brief %q", out)
	}
	if got := landed(t, c, box+drive+"/slack-bugs-4/notes.md"); got != "mine\n" {
		t.Fatalf("the lane's own note holds %q", got)
	}
	if len(left) != 1 || !strings.Contains(left[0], fifo) || !strings.Contains(left[0], dangling) {
		t.Fatalf("warnings %q, want one naming %s and %s", left, fifo, dangling)
	}

	own := filepath.Join(drive, "slack-bugs-4/pipe")
	if err := syscall.Mkfifo(own, 0o600); err != nil {
		t.Fatal(err)
	}
	if left := leftBehind(t, c, "Scratch is "+drive+" here.\n"); !strings.Contains(left, drive+" holds "+own+", which is neither a file nor a directory") {
		t.Fatalf("warning %q", left)
	}
}

func TestWhatTheLaneItselfNeedsIsLackedWhileOtherFaultsOnlyWarn(t *testing.T) {
	c, fake := carrier(t)
	c.Lane = "progress-desk-6"
	c.Vars = map[string]string{DriveDir: filepath.Join(c.Home, ".claude/scratch/durable-agents-rebuild")}
	big := filepath.Join(c.Vars[DriveDir], "progress-desk/board.sqlite")
	put(t, big, "x")
	if err := os.Truncate(big, maxBytes+1); err != nil {
		t.Fatal(err)
	}
	fake.Fail(3, "not-found", "ccn", "-R", c.Repo, "doc", "show", "6aeb9b9")
	missing := filepath.Join(c.Home, ".claude/scratch/elsewhere.md")
	config := filepath.Join(c.Home, ".claude.json")
	put(t, config, "{}\n")

	_, left, lacked := c.Stage(context.Background(), "State "+filepath.Dir(big)+", doc `ccn doc show 6aeb9b9`, notes "+missing+", never "+config+".\n")

	if len(lacked) != 2 || !strings.HasPrefix(lacked[0], filepath.Dir(big)+" holds 65 MiB") || !strings.Contains(lacked[1], "ccn doc show 6aeb9b9") {
		t.Fatalf("lacked %q", lacked)
	}
	if len(left) != 2 || !strings.HasPrefix(left[0], missing+" does not exist") || !strings.HasPrefix(left[1], config+" lies outside") {
		t.Fatalf("left %q", left)
	}
}

func TestAPathOutsideTheCarriedDirectoriesStaysBehindNamedEvenThroughALink(t *testing.T) {
	c, _ := carrier(t)
	config := filepath.Join(c.Home, ".claude.json")
	slack := filepath.Join(c.Home, ".config/cc-slack/drive.toml")
	put(t, config, "{}\n")
	put(t, slack, "token\n")
	link := filepath.Join(c.Home, ".claude/scratch/slack.toml")
	if err := os.MkdirAll(filepath.Dir(link), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(slack, link); err != nil {
		t.Fatal(err)
	}
	brief := "Read " + config + ", ~/.config/cc-slack/drive.toml and " + link + ".\n"

	out, left := c.Carry(context.Background(), brief)

	for _, name := range []string{config, slack, link} {
		if !slices.ContainsFunc(left, func(fault string) bool { return strings.HasPrefix(fault, name+" lies outside") }) {
			t.Errorf("warnings %q do not name %s", left, name)
		}
	}
	if out != brief {
		t.Errorf("rewritten brief %q, want it unchanged", out)
	}
	if entries, _ := os.ReadDir(c.Into); len(entries) != 0 {
		t.Errorf("copied %d entries from outside the carried directories", len(entries))
	}
}

func TestTheDrivesPlanDirectoryTravelsWhole(t *testing.T) {
	c, _ := carrier(t)
	plan := filepath.Join(c.Home, "handoffs/durable-agents-rebuild/plan")
	put(t, plan+"/HASH.txt", "9f2c\n")
	put(t, plan+"/steps/01.md", "step one\n")

	out := carried(t, c, "Follow ~/handoffs/durable-agents-rebuild/plan/ step by step.\n")

	if out != "Follow "+box+plan+"/ step by step.\n" {
		t.Fatalf("rewritten brief %q", out)
	}
	if got := landed(t, c, box+plan+"/steps/01.md"); got != "step one\n" {
		t.Fatalf("plan step holds %q", got)
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

func TestTheDrivesCheckoutIsTheSpritesOwnWhileAStrayMacFileStillStaysBehind(t *testing.T) {
	c, fake := carrier(t)
	c.Checkout = filepath.Join(c.Home, ".claude/worktrees/monorepo/drive-runner")
	stray := filepath.Join(c.Home, "Desktop/notes.md")
	put(t, stray, "notes\n")
	brief := "Run cci from " + c.Checkout + ", and read `ccn -R " + c.Checkout + "/ doc show 7b1a5bc` and " + stray + ".\n"

	out, left := c.Carry(context.Background(), brief)

	if out != brief {
		t.Fatalf("rewritten brief %q, want it unchanged", out)
	}
	if len(left) != 1 || !strings.HasPrefix(left[0], stray+" lies outside") {
		t.Fatalf("warnings %q, want only the stray file", left)
	}
	if calls := fake.Calls(); len(calls) != 0 {
		t.Fatalf("ran %v for the drive's checkout", calls)
	}
}

func TestAToolUnderTheInstallLinkRunsTheSpritesOwnInstall(t *testing.T) {
	c, fake := carrier(t)
	installed := filepath.Join(c.Home, ".claude/plugins/cache/skills/long-running/0.7.1937")
	put(t, filepath.Join(installed, "bin/drive.py"), "#!/bin/bash\n")
	if err := os.MkdirAll(filepath.Join(c.Home, ".claude/long-running"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(installed, filepath.Join(c.Home, ".claude/long-running/current")); err != nil {
		t.Fatal(err)
	}
	brief := "Run `" + c.Home + "/.claude/long-running/current/bin/drive.py lane-status meta-desk-3 --held`.\n"

	out, left := c.Carry(context.Background(), brief)

	if want := "Run `/home/sprite/.claude/long-running/current/bin/drive.py lane-status meta-desk-3 --held`.\n"; out != want {
		t.Fatalf("rewritten brief %q, want %q", out, want)
	}
	if len(left) != 0 {
		t.Fatalf("warnings %q, want none", left)
	}
	if entries, _ := os.ReadDir(c.Into); len(entries) != 0 {
		t.Fatalf("carried %d entries for an installed tool, want none", len(entries))
	}
	if calls := fake.Calls(); len(calls) != 0 {
		t.Fatalf("ran %v for an installed tool", calls)
	}
}

func TestADriveCheckoutIsTheOneItsLrJSONNames(t *testing.T) {
	dir := t.TempDir()
	put(t, filepath.Join(dir, "lr.json"), `{"repo":"Forge-AI/monorepo","drive":"brook","checkout":"/Users/o/brook-runner","author":"drive-bot","label":"brook","movers":["lanes"]}`)

	if got := DriveCheckout(dir); got != "/Users/o/brook-runner" {
		t.Fatalf("checkout %q", got)
	}
	if got := DriveCheckout(t.TempDir()); got != "" {
		t.Fatalf("a drive dir with no lr.json names checkout %q", got)
	}
}

func TestACcnEntityThatDoesNotRenderWarnsNamingIt(t *testing.T) {
	c, fake := carrier(t)
	fake.Fail(1, "not-found: entity not found: no task matches \"t-9\"", "ccn")

	left := leftBehind(t, c, "Follow `ccn task show t-9`.\n")

	if !strings.Contains(left, "`ccn task show t-9` failed") || !strings.Contains(left, "no task matches") {
		t.Fatalf("warning %q", left)
	}
}

func TestAHelperNamedThroughAVariableTravelsAndItsReferenceReadsTheCopy(t *testing.T) {
	c, _ := carrier(t)
	drive := filepath.Join(c.Home, ".claude/scratch/drive")
	c.Vars = map[string]string{DriveDir: drive}
	put(t, drive+"/go-desk-2/carry.py", "print('carried')")
	put(t, drive+"/fg/bin.zsh", "echo fg")
	put(t, c.Home+"/handoffs/notes.md", "notes")

	rewritten, _ := c.Carry(context.Background(), `python3 "$DRIVE_DIR/go-desk-2/carry.py" --post; ${DRIVE_DIR}/fg/bin.zsh; cat $HOME/handoffs/notes.md`)

	want := `python3 "` + box + drive + `/go-desk-2/carry.py" --post; ` + box + drive + `/fg/bin.zsh; cat ` + box + c.Home + `/handoffs/notes.md`
	if rewritten != want {
		t.Fatalf("rewritten %q, want %q", rewritten, want)
	}
	if got := landed(t, c, box+drive+"/go-desk-2/carry.py"); got != "print('carried')" {
		t.Fatalf("carried %q", got)
	}
}

func TestAVariableTheBriefAssignsItselfExpands(t *testing.T) {
	c, _ := carrier(t)
	root := filepath.Join(c.Home, ".claude/scratch/lane")
	put(t, root+"/items.md", "items")

	rewritten, _ := c.Carry(context.Background(), "Read `$B/items.md` once.\nexport B="+root+"/\n")

	if !strings.Contains(rewritten, box+root+"//items.md") && !strings.Contains(rewritten, box+root+"/items.md") {
		t.Fatalf("rewritten %q does not read the copy", rewritten)
	}
}

func TestAVariablePathThatDoesNotExistYetIsRewrittenAndNeverRefused(t *testing.T) {
	c, _ := carrier(t)
	drive := filepath.Join(c.Home, "drive")
	c.Vars = map[string]string{DriveDir: drive}

	rewritten, _ := c.Carry(context.Background(), "Write $DRIVE_DIR/lane/NOTES.md as you go.")

	if rewritten != "Write "+box+drive+"/lane/NOTES.md as you go." {
		t.Fatalf("rewritten %q", rewritten)
	}
}

func TestAVariableNoOneDefinedOrATemplateUnderOnePassesThrough(t *testing.T) {
	c, _ := carrier(t)
	c.Vars = map[string]string{DriveDir: filepath.Join(c.Home, "drive")}
	text := "cd $DESK_WT/plugins; run $DRIVE_DIR/<lane>/carry.py; ls $DRIVE_DIR"

	rewritten, _ := c.Carry(context.Background(), text)

	if rewritten != text {
		t.Fatalf("rewritten %q, want it unchanged", rewritten)
	}
}

func TestACredentialNamedThroughAVariableNeverLeavesTheMac(t *testing.T) {
	c, _ := carrier(t)
	put(t, c.Home+"/.ssh/id", "secret")

	if left := leftBehind(t, c, "cat $HOME/.ssh/id"); !strings.Contains(left, c.Home+"/.ssh/id lies outside") {
		t.Fatalf("warning %q does not name the credential", left)
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

func TestHisIrisSignInNeverTravelsWhetherABriefOrABriefItCarriesNamesIt(t *testing.T) {
	c, _ := carrier(t)
	signIn := c.Home + "/.iris/credentials.json"
	put(t, signIn, "secret\n")
	c.Vars = map[string]string{DriveDir: c.Home + "/drive"}
	desk := c.Home + "/drive/fidelity-shard-6"
	put(t, desk+"/brief.md", "- His session cookie and ~/.iris/credentials.json are the same grant as his signed-in browser.\n")

	carried(t, c, "Read "+desk+"/brief.md in full.\n")

	if _, err := os.Stat(c.Into + signIn); err == nil {
		t.Error("his Iris sign-in travelled with a brief the launch carried")
	}
	if left := leftBehind(t, c, "Never use ~/.iris/credentials.json.\n"); !strings.Contains(left, signIn+" lies outside") {
		t.Fatalf("warning %q does not name his Iris sign-in", left)
	}
	if _, err := os.Stat(c.Into + signIn); err == nil {
		t.Error("his Iris sign-in travelled with the brief that named it")
	}
}

func TestBriefsNamingEachOtherCarryOnceAndEnd(t *testing.T) {
	c, _ := carrier(t)
	one, two := c.Home+"/handoffs/a/one.brief.md", c.Home+"/handoffs/b/two.brief.md"
	put(t, one, "then "+two+"\n")
	put(t, two, "back to "+one+"\n")

	out := carried(t, c, "Read "+one+"\n")

	if !strings.Contains(landed(t, c, box+two), box+one) || !strings.Contains(out, box+one) {
		t.Fatalf("out %q", out)
	}
}

func TestAMissingPathInTheBriefItselfWarnsWhileANestedOneStaysInTheLog(t *testing.T) {
	c, _ := carrier(t)
	put(t, c.Home+"/handoffs/a/one.brief.md", "old "+c.Home+"/gone.txt\n")

	if left := leftBehind(t, c, "Read "+c.Home+"/handoffs/a/one.brief.md\n"); left != "" {
		t.Fatal(left)
	}
	if left := leftBehind(t, c, "Read "+c.Home+"/gone.txt\n"); !strings.Contains(left, "does not exist") {
		t.Fatal(left)
	}
}

func TestARegisterRowTheToolQuotedIsNeitherCarriedNorChecked(t *testing.T) {
	c, fake := carrier(t)
	kept := filepath.Join(c.Home, "handoffs/kept.md")
	put(t, kept, "kept\n")
	put(t, c.Home+"/.ssh/id", "secret")
	rows := "Register slice: lane=a step=all\n  > HAR is at " + c.Home + "/Downloads/gone.har\n  > see " + kept + ", " + c.Home + "/.ssh/id and `ccn task show t-9`\nslice-hash: 0c81c2cb1fc0\n"
	body := "Scale: small\nRead " + kept + " once.\n"

	out, left := c.Carry(context.Background(), "# a\n\n"+rows+body)
	if len(left) != 0 {
		t.Fatal(left)
	}
	if want := "# a\n\n" + rows + "Scale: small\nRead " + box + kept + " once.\n"; out != want {
		t.Fatalf("got %q, want %q", out, want)
	}
	if calls := fake.Calls(); len(calls) != 0 {
		t.Fatalf("a quoted row ran %v", calls)
	}
}

func TestAPathOutsideTheQuotedRowsIsStillChecked(t *testing.T) {
	c, _ := carrier(t)
	rows := "Register slice: lane=a step=all\nslice-hash: 0c81c2cb1fc0\n"
	put(t, c.Home+"/.ssh/id", "secret")

	if got := leftBehind(t, c, rows+"cat "+c.Home+"/.ssh/id\n"); !strings.Contains(got, "lies outside") {
		t.Fatal(got)
	}
	if left := leftBehind(t, c, rows+"Read "+c.Home+"/gone.txt\n"); !strings.Contains(left, "does not exist") {
		t.Fatal(left)
	}
}

const drive = "durable-agents-rebuild"

func TestARecordTheBriefCitesIsCarriedFromTheMacInEveryFormBriefsUse(t *testing.T) {
	c, fake := carrier(t)
	c.Drive = drive
	fake.Reply("@5:32 AM\n451308 note card-host\nthe first record\n", "cci", "show", "--drive", drive, "451308@144ffadb")
	fake.Reply("451335 note card-host\nthe second record\n", "cci", "show", "--drive", drive, "451335@144ffadb")
	fake.Reply("487 note notes-store-v5\nan origin-tagged record\n", "cci", "show", "--drive", drive, "487@32fde6d5")
	fake.Reply(`{"origin_seq":489273,"lane":"root","to":["owner-relay"]}`+"\n", "cci", "show", "--drive", drive, "--json", "489273@144ffadb")
	fake.Reply("489273 report root\nthe record beside a lane\n", "cci", "show", "--drive", drive, "489273@144ffadb")
	fake.Reply(`{"origin_seq":106361,"lane":"bk-desk","to":["root"]}`+"\n", "cci", "show", "--drive", drive, "--json", "106361@144ffadb")
	brief := "Read `cci show 451308`, record 451335 and #487@32fde6d5, then root's 489273 (PR #50456, build 106361).\n"

	out := carried(t, c, brief)

	record := func(id string) string { return box + "/cci/record-" + id + ".md" }
	want := "Read `" + record("451308") + "`, record " + record("451335") + " and " + record("487@32fde6d5") + ", then root's " + record("489273") + " (PR #50456, build 106361).\n"
	if out != want {
		t.Fatalf("rewritten brief:\n%s\nwant:\n%s", out, want)
	}
	if got := landed(t, c, record("451308")); got != "@5:32 AM\n451308 note card-host\nthe first record\n" {
		t.Fatalf("record copy holds %q", got)
	}
	if got := landed(t, c, record("489273")); got != "489273 report root\nthe record beside a lane\n" {
		t.Fatalf("lane-cited record copy holds %q", got)
	}
}

func TestABareRecordNumberIsTheStagingNodesRecordEvenOnceAnotherNodesRecordSharesIt(t *testing.T) {
	c, fake := carrier(t)
	c.Drive = drive
	fake.Reply("420406 ask step7-primary-rss\nthe card ask\n", "cci", "show", "--drive", drive, "420406@144ffadb")
	fake.Reply("487 note notes-store-v5\nan origin-tagged record\n", "cci", "show", "--drive", drive, "487@32fde6d5")

	out, left := c.Carry(context.Background(), "Read `cci show 420406 --drive "+drive+"` and #487@32fde6d5.\n")

	if want := "Read `" + box + "/cci/record-420406.md` and " + box + "/cci/record-487@32fde6d5.md.\n"; out != want || len(left) != 0 {
		t.Fatalf("rewritten brief %q with warnings %q, want %q", out, left, want)
	}
	if got := landed(t, c, box+"/cci/record-420406.md"); got != "420406 ask step7-primary-rss\nthe card ask\n" {
		t.Fatalf("record copy holds %q", got)
	}
}

func TestSeveralRecordsOneShowNamesTravelTogetherAndItsFlagsDrop(t *testing.T) {
	c, fake := carrier(t)
	fake.Reply("first\n", "cci", "show", "451308@144ffadb")
	fake.Reply("second\n", "cci", "show", "451335@144ffadb")

	out := carried(t, c, "Run cci show --body --budget 100 451308 '#451335' to read them.\n")

	if want := "Run " + box + "/cci/record-451308.md " + box + "/cci/record-451335.md to read them.\n"; out != want {
		t.Fatalf("rewritten brief %q, want %q", out, want)
	}
}

func TestARecordTheMacCannotShowWarnsNamingItAndLeavesItsReferenceAlone(t *testing.T) {
	c, fake := carrier(t)
	fake.Fail(1, "cci: show: 451308: no record has this number", "cci")
	brief := "Read `cci show 451308` and record 451308 first.\n"

	out, left := c.Carry(context.Background(), brief)

	if out != brief {
		t.Fatalf("rewritten brief %q, want it unchanged", out)
	}
	if len(left) != 1 || !strings.Contains(left[0], "`cci show 451308@144ffadb` failed") || !strings.Contains(left[0], "no record has this number") {
		t.Fatalf("warnings %q, want one naming the record", left)
	}
}

func TestANumberNoRecordOfTheNamedLaneHoldsIsNeverRewritten(t *testing.T) {
	c, fake := carrier(t)
	fake.Fail(1, "no record has this number", "cci", "show", "--json")
	brief := "Wait for build 106361 and the 13551 bytes of this brief.\n"

	out, left := c.Carry(context.Background(), brief)

	if out != brief || len(left) != 0 {
		t.Fatalf("rewrote %q with warnings %q", out, left)
	}
}

func TestRecordsInTheQuotedRegisterSliceAreNotCarried(t *testing.T) {
	c, fake := carrier(t)
	brief := "Register slice: lane=x step=all\n- dbdf2c0 see #7630@10283a3d and record 451308\nslice-hash: 50dbc362b0e7\n"

	if out := carried(t, c, brief); out != brief {
		t.Fatalf("rewrote the quoted rows: %q", out)
	}
	if calls := fake.Calls(); len(calls) != 0 {
		t.Fatalf("ran %v for records quoted from the register", calls)
	}
}
