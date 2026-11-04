package install

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx/ccxtest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

func git(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", dir, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"}, args...)...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v: %s", args, err, out)
	}
	return string(out)
}

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
}

func releasedTree(t *testing.T) (origin, install string) {
	t.Helper()
	origin, install = t.TempDir(), t.TempDir()
	git(t, origin, "init", "-q")
	for _, root := range []string{filepath.Join(origin, "plugins", "p"), install} {
		write(t, filepath.Join(root, "a.txt"), "a\n")
		write(t, filepath.Join(root, "sub", "b.txt"), "b\n")
		if err := os.Symlink("a.txt", filepath.Join(root, "link")); err != nil {
			t.Fatal(err)
		}
	}
	git(t, origin, "add", ".")
	git(t, origin, "commit", "-q", "-m", "release")
	git(t, origin, "tag", "p--v1")
	return origin, install
}

func faultsOf(t *testing.T, origin, install string) []string {
	t.Helper()
	faults, err := treeFaults(context.Background(), shell.Exec{}, install, origin, "p--v1", "plugins/p", filepath.Join(t.TempDir(), "releases.git"))
	if err != nil {
		t.Fatal(err)
	}
	return faults
}

func TestAnInstallThatMatchesItsTagHasNoFaultsAndMarkersAreIgnored(t *testing.T) {
	origin, install := releasedTree(t)
	write(t, filepath.Join(install, ".in_use"), "")
	write(t, filepath.Join(install, "sub", "__pycache__", "b.pyc"), "x")

	if faults := faultsOf(t, origin, install); len(faults) != 0 {
		t.Fatalf("faults %v", faults)
	}
}

func TestEveryWayAnInstallDiffersFromItsTagIsNamed(t *testing.T) {
	origin, install := releasedTree(t)
	write(t, filepath.Join(install, "a.txt"), "edited in place\n")
	write(t, filepath.Join(install, "extra.txt"), "added past the tag\n")
	if err := os.Remove(filepath.Join(install, "sub", "b.txt")); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(filepath.Join(install, "link")); err != nil {
		t.Fatal(err)
	}

	got := strings.Join(faultsOf(t, origin, install), "\n")

	for _, want := range []string{"the install holds 2 files where the release holds 3", "link is missing", "sub/b.txt is missing", "extra.txt is not in the release", "a.txt differs from the release"} {
		if !strings.Contains(got, want) {
			t.Fatalf("no %q in:\n%s", want, got)
		}
	}
}

func TestATagThatWillNotFetchIsAFaultNotACrash(t *testing.T) {
	origin, install := releasedTree(t)
	faults, err := treeFaults(context.Background(), shell.Exec{}, install, origin, "p--v9", "plugins/p", filepath.Join(t.TempDir(), "releases.git"))
	if err != nil || len(faults) != 1 || !strings.HasPrefix(faults[0], "the release tag p--v9 did not fetch from ") {
		t.Fatalf("got %v %v", faults, err)
	}
}

func cachedChecks(t *testing.T, gh map[string]string) Checks {
	t.Helper()
	plugins := t.TempDir()
	write(t, filepath.Join(plugins, "marketplaces", "skills", ".claude-plugin", "marketplace.json"),
		`{"plugins":[{"name":"codex","source":"./plugins/codex"},{"name":"long-running","source":{"url":"https://github.com/o/r.git","path":"plugins/long-running"}}]}`)
	return Checks{CCX: &ccxtest.Fake{GHOut: gh}, Installs: filepath.Join(plugins, "cache", "skills", "long-running"), Claude: t.TempDir()}
}

const parentOfTag = `{"parents":[{"sha":"abcdef0123456789"}]}`

const runsPath = "api repos/o/r/actions/runs?head_sha=abcdef0123456789&per_page=100"

func TestCIFaultReadsTheNewestAttemptOnTheCommitTheTagWasBumpedFrom(t *testing.T) {
	cases := map[string]struct {
		runs string
		want string
	}{
		"passed":      {`{"workflow_runs":[{"name":"long-running CI","run_attempt":1,"status":"completed","conclusion":"failure","html_url":"u1"},{"name":"long-running CI","run_attempt":2,"status":"completed","conclusion":"success","html_url":"u2"}]}`, ""},
		"none":        {`{"workflow_runs":[{"name":"Autobump","run_attempt":1,"status":"completed","conclusion":"success"}]}`, "no long-running CI run on main has started for abcdef0123"},
		"in progress": {`{"workflow_runs":[{"name":"long-running CI","run_attempt":1,"status":"in_progress","html_url":"u"}]}`, "is in_progress: u"},
		"red":         {`{"workflow_runs":[{"name":"long-running CI","run_attempt":1,"status":"completed","conclusion":"failure","html_url":"u"}]}`, "ended failure: u"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			checks := cachedChecks(t, map[string]string{"api repos/o/r/commits/long-running--v1.2.3": parentOfTag, runsPath: tc.runs})
			got := checks.CIFault(context.Background(), "1.2.3")
			if tc.want == "" && got != "" || tc.want != "" && !strings.Contains(got, tc.want) {
				t.Fatalf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestAnUncachedInstallHasNoReleaseToCompare(t *testing.T) {
	checks := Checks{Installs: t.TempDir()}
	if faults := checks.ReleaseFaults(context.Background(), "1.2.3"); faults != nil {
		t.Fatalf("faults %v", faults)
	}
	if fault := checks.CIFault(context.Background(), "1.2.3"); fault != "" {
		t.Fatalf("fault %q", fault)
	}
}

func TestAMissingInstallIsNamed(t *testing.T) {
	checks := cachedChecks(t, nil)
	faults := checks.ReleaseFaults(context.Background(), "1.2.3")
	if len(faults) != 1 || !strings.HasSuffix(faults[0], "holds no install of 1.2.3") {
		t.Fatalf("faults %v", faults)
	}
}

func TestATargetBinaryThatRefusesCarriesItsOwnWords(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "lr: /state/lr.json names go-sweep, which never registered on drive d\n", "/i/1.2.3/bin/lr")
	checks := Checks{Runner: fake, Installs: "/i"}

	fault := checks.TargetFault(context.Background(), "1.2.3", "/state/lr.json")

	if !strings.Contains(fault, "names go-sweep, which never registered on drive d") {
		t.Fatalf("fault %q", fault)
	}
	call := fake.Calls()[0]
	if !slices.Equal(call.Argv, []string{"/i/1.2.3/bin/lr", "install", "check", "--config", "/state/lr.json"}) || !slices.Equal(call.Env, []string{"LONG_RUNNING_PIN=1.2.3"}) {
		t.Fatalf("call %+v", call)
	}
}

func TestInstallPreflightDoesNotReadOrReplayDriveHistory(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("", "/i/1.2.3/bin/lr", "install", "check")
	faults := preflight(context.Background(), Checks{Runner: fake, Installs: "/i"}, "/state/lr.json", "1.2.3", options{ciRedOK: "owner requested immediate installation"})
	if len(faults) != 0 {
		t.Fatal(faults)
	}
	calls := fake.Calls()
	if len(calls) != 1 || !slices.Equal(calls[0].Argv, []string{"/i/1.2.3/bin/lr", "install", "check", "--config", "/state/lr.json"}) {
		t.Fatalf("preflight commands: %+v", calls)
	}
}

func TestAnUnregisteredGoLaneRefusesTheRollAsItKilledTheLandingDesk(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"lane":"go-sweep-1"}`+"\n", "cci", "who", "--drive", "d", "--lane", "go-sweep-1")
	fake.Reply("", "cci", "who", "--drive", "d", "--lane", "go-sweep")
	fake.Reply("", "cci", "tail", "--drive", "d", "--lane", "go-sweep")
	cfg := config.Config{Drive: "d", GoLanes: "go-sweep-1, go-sweep"}

	got := lanesFaults(context.Background(), fake, cfg, "/s/lr.json")

	if !slices.Equal(got, []string{"/s/lr.json names go-sweep, which never registered on drive d; start each lane or remove it from the config"}) {
		t.Fatalf("got %v", got)
	}
}

func TestALaneThatOnlyPostedCountsAsRegistered(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("", "cci", "who")
	fake.Reply(`{"seq":1}`+"\n", "cci", "tail")
	if got := lanesFaults(context.Background(), fake, config.Config{Drive: "d", GoLanes: "remote-lane"}, "/s/lr.json"); len(got) != 0 {
		t.Fatalf("got %v", got)
	}
}

func cciDrive(t *testing.T, workerURL string) (plugin, state string) {
	t.Helper()
	plugin, state = t.TempDir(), t.TempDir()
	write(t, filepath.Join(plugin, ".claude-plugin", "plugin.json"), `{"dependencies":[{"name":"captain-hook","version":">=12.0.0"},{"name":"cc-inbox","version":">=0.49.34"}]}`)
	if workerURL != "" {
		write(t, filepath.Join(state, "runner.json"), `{"orca":{"sprite":{"env":{"WORKER_CCI_URL":"`+workerURL+`"}}}}`)
	}
	return plugin, state
}

func TestACCIBehindThePackFloorRefusesHereAndForSpriteWorkers(t *testing.T) {
	url := "https://github.com/yasyf/cc-inbox/releases/download/v0.49.33/cci_0.49.33_linux_amd64.tar.gz"
	plugin, state := cciDrive(t, url)
	fake := &shelltest.Fake{}
	fake.Reply("0.49.33\n", "cci", "--version")

	got := cciFaults(context.Background(), fake, plugin, state)

	want := []string{
		"this machine's cci is 0.49.33, and the pack needs cc-inbox 0.49.34 or later; run cci upgrade",
		"runner.json's WORKER_CCI_URL " + url + " is no cc-inbox 0.49.34 or later release, so every Sprite worker would install a cci this pack outruns",
	}
	if !slices.Equal(got, want) {
		t.Fatalf("got %v", got)
	}
}

func TestACCIAtOrPastThePackFloorPassesAndADriveWithoutSpritesChecksOnlyThisMachine(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("0.49.34\n", "cci", "--version")
	plugin, state := cciDrive(t, "https://github.com/yasyf/cc-inbox/releases/download/v0.49.100/cci_0.49.100_linux_amd64.tar.gz")
	if got := cciFaults(context.Background(), fake, plugin, state); len(got) != 0 {
		t.Fatalf("got %v", got)
	}
	plugin, state = cciDrive(t, "")
	if got := cciFaults(context.Background(), fake, plugin, state); len(got) != 0 {
		t.Fatalf("got %v", got)
	}
}

func toolPack(t *testing.T) string {
	t.Helper()
	plugin := t.TempDir()
	write(t, filepath.Join(plugin, toolFloorsFile), `[{"package":"cc-notes","tool":"ccn","version":"0.75.18"},{"package":"cc-slack","tool":"cc-slack","version":"0.4.80"}]`)
	return plugin
}

func TestAPackRefusesAToolBehindItsFloorAndChecksOnlyTheToolsItNames(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("v0.75.17 (33165ba92bf7d19a85723679f52edbfddc826068)\n", "ccn", "--version")
	fake.Reply("cc-slack version 0.4.80\n", "cc-slack", "--version")

	got := toolFaults(context.Background(), fake, toolPack(t))

	want := []string{`this machine's ccn is "v0.75.17 (33165ba92bf7d19a85723679f52edbfddc826068)", and the pack needs cc-notes 0.75.18 or later; install that first`}
	if !slices.Equal(got, want) {
		t.Fatalf("got %v", got)
	}
}

func TestAToolAtOrPastItsFloorPassesInEitherVersionFormat(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("v0.75.18 (5b680988491296a994b747254f6b5454c6bf6d61)\n", "ccn", "--version")
	fake.Reply("cc-slack version 0.4.100\n", "cc-slack", "--version")
	if got := toolFaults(context.Background(), fake, toolPack(t)); len(got) != 0 {
		t.Fatalf("got %v", got)
	}
}

func TestAPackWithNoToolFloorsChecksNoTools(t *testing.T) {
	if got := toolFaults(context.Background(), &shelltest.Fake{}, t.TempDir()); len(got) != 0 {
		t.Fatalf("got %v", got)
	}
}

func TestAToolThatDoesNotAnswerRefusesTheInstall(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(127, "ccn: not found", "ccn", "--version")
	fake.Reply("cc-slack version 0.4.80\n", "cc-slack", "--version")
	got := toolFaults(context.Background(), fake, toolPack(t))
	if len(got) != 1 || !strings.Contains(got[0], "ccn --version failed, and the pack needs cc-notes 0.75.18 or later") {
		t.Fatalf("got %v", got)
	}
}

func TestLocalEditsInTheCheckoutRefuseOnlyWhenTheLandingMoverRuns(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(" M go/a.go\nM  go/b.go\n M go/untouched.go\n", "git", "-C", "/co", "status")
	fake.Reply("go/a.go\ngo/b.go\ngo/c.go\n", "git", "-C", "/co", "diff")
	got := checkoutFault(context.Background(), fake, config.Config{Checkout: "/co", Trunk: "main", Movers: []string{"lanes", config.MoverLanding}})
	if got != "/co has local edits to go/a.go, go/b.go, so no pass can fast-forward it to origin/main" {
		t.Fatalf("got %q", got)
	}
	if got := checkoutFault(context.Background(), fake, config.Config{Checkout: "/co", Trunk: "main", Movers: []string{"lanes"}}); got != "" {
		t.Fatalf("a drive that lands nothing refused on %q", got)
	}
}

func TestLocalEditsTheTrunkDoesNotMoveDoNotRefuse(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(" M go/a.go\n", "git", "-C", "/co", "status")
	fake.Reply("go/b.go\n", "git", "-C", "/co", "diff")
	if got := checkoutFault(context.Background(), fake, config.Config{Checkout: "/co", Trunk: "main", Movers: []string{config.MoverLanding}}); got != "" {
		t.Fatalf("got %q", got)
	}
}

func TestAShimNamingAVerbTheBinaryLacksIsNamed(t *testing.T) {
	root := &cobra.Command{Use: "lr", Args: cobra.NoArgs, RunE: func(*cobra.Command, []string) error { return nil }}
	landing := &cobra.Command{Use: "landing"}
	landing.AddCommand(&cobra.Command{Use: "restack", RunE: func(*cobra.Command, []string) error { return nil }})
	root.AddCommand(landing)
	script := `case "$TOOL" in
  lr)
    exec "$ROOT/scripts/binrun.sh" "$ROOT/bin/lr.binrun" "$@"
    ;;
  remote-restack.py) exec "$ROOT/bin/lr" landing restack "$@" ;;
  finder.py) exec "$ROOT/bin/lr" grade finder "$@" ;;
  mq.py) exec "$ROOT/bin/lr" landing queue "$@" ;;
esac`

	got := ShimFaults(root, script)

	if !slices.Equal(got, []string{"the finder.py shim runs lr grade finder, which this binary lacks", "the mq.py shim runs lr landing queue, which this binary lacks"}) {
		t.Fatalf("got %v", got)
	}
}

func TestPromoteMovesTheLinkOnlyUp(t *testing.T) {
	claude := t.TempDir()
	installs := t.TempDir()
	for _, version := range []string{"0.7.9", "0.7.44", "0.7.57"} {
		if err := os.Mkdir(filepath.Join(installs, version), 0o750); err != nil {
			t.Fatal(err)
		}
	}
	for _, step := range []struct{ promote, want string }{{"0.7.44", "0.7.44"}, {"0.7.9", "0.7.44"}, {"0.7.57", "0.7.57"}} {
		if err := Promote(claude, filepath.Join(installs, step.promote)); err != nil {
			t.Fatal(err)
		}
		if got := Installed(claude); got != step.want {
			t.Fatalf("after %s the link names %s, want %s", step.promote, got, step.want)
		}
	}
	if err := Promote(claude, filepath.Join(installs, "long-running")); err == nil {
		t.Fatal("promoted a directory that is no version")
	}
}
