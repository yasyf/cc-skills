package landing

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestTheGateCountsGoClassesBesideTheExemptGradesTheRunnerAdmitsAlone(t *testing.T) {
	path := filepath.Join(t.TempDir(), runnerFile)
	runner := `{"drive":"d","orca":{"briefs":{"repo":"o/r"},"desk_inbox":"i","run":"r"},"landing":{"go_classes":["class owner-pick"]}}`
	if err := os.WriteFile(path, []byte(runner), 0o600); err != nil {
		t.Fatal(err)
	}
	section, err := landingSection(path)
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"pure port, no interaction", "test-only or CI-only, no non-test code", "class owner-pick"}; !slices.Equal(section.ExemptGrades, want) {
		t.Fatalf("got %q, want %q", section.ExemptGrades, want)
	}
}

func TestTheSweepGatesOnlyAGreenApprovedGradedHeadWithNoGoHoldOrTap(t *testing.T) {
	ready := machine.Facts{Number: 1, Head: "abc1234", CI: machine.CISuccess, Approved: true, Finder: machine.FinderClear, Fidelity: machine.FidelityPass}
	if !goReady(ready) {
		t.Fatalf("%+v should be gated", ready)
	}
	for name, change := range map[string]func(*machine.Facts){
		"red":        func(f *machine.Facts) { f.CI = "" },
		"unapproved": func(f *machine.Facts) { f.Approved = false },
		"ungraded":   func(f *machine.Facts) { f.Fidelity = "" },
		"has a go":   func(f *machine.Facts) { f.Exempt = true },
		"held":       func(f *machine.Facts) { f.Hold = "landing" },
		"draft":      func(f *machine.Facts) { f.Draft = true },
		"closed":     func(f *machine.Facts) { f.Closed = true },
		"merged":     func(f *machine.Facts) { f.Merged = true },
		"tapped":     func(f *machine.Facts) { f.Tap = machine.TapTapped },
	} {
		t.Run(name, func(t *testing.T) {
			f := ready
			change(&f)
			if goReady(f) {
				t.Fatalf("%+v should not be gated", f)
			}
		})
	}
}

func TestARefusedHeadIsNotGatedAgainWithinTenMinutesSoThePassMakesNoReadsForIt(t *testing.T) {
	s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	now := time.Date(2026, 11, 4, 18, 0, 0, 0, time.UTC)
	ready := machine.Facts{Number: 7, Head: "abc1234", CI: machine.CISuccess, Approved: true, Finder: machine.FinderClear, Fidelity: machine.FidelityPass}
	if err := s.Probe(goProbe(ready.Number), now); err != nil {
		t.Fatal(err)
	}

	if err := SweepGos(context.Background(), nil, config.Config{}, s, []machine.Facts{ready}, now.Add(goRegate-time.Second), io.Discard); err != nil {
		t.Fatalf("the sweep read the gate for a head refused within %s: %v", goRegate, err)
	}
}

func TestOnlyARefusedStandingClaimTurnsItsRowBackToHim(t *testing.T) {
	s, err := store.Open(filepath.Join(t.TempDir(), "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	now := time.Date(2026, 11, 4, 18, 0, 0, 0, time.UTC)
	claim := func(number int) gates.Claim { return gates.Claim{Repo: "o/r", PR: number, Head: "abc1234"} }
	refused := []string{"port: its source #9 is not a merged pull request"}
	for _, each := range []struct {
		number int
		clean  bool
		said   []string
		want   string
	}{
		{1, true, refused, refused[0]},
		{2, false, refused, ""},
		{3, true, nil, ""},
	} {
		if err := noteStandingRefused(s, claim(each.number), each.clean, each.said, now); err != nil {
			t.Fatal(err)
		}
		facts, err := s.Facts("o/r", each.number, "abc1234")
		if err != nil {
			t.Fatal(err)
		}
		if got := facts[gates.StandingRefused]; got != each.want {
			t.Fatalf("#%d: fact %q, want %q", each.number, got, each.want)
		}
	}
}

func TestAStandingRefusalClearsWhenTheGoNextAdmitsTheHead(t *testing.T) {
	s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	now := time.Date(2026, 11, 4, 18, 0, 0, 0, time.UTC)
	claim := gates.Claim{Repo: "o/r", PR: 1, Head: "abc1234"}
	if err := noteStandingRefused(s, claim, true, []string{"port: its source #9 is not a merged pull request"}, now); err != nil {
		t.Fatal(err)
	}
	if err := noteStandingRefused(s, claim, true, nil, now.Add(time.Minute)); err != nil {
		t.Fatal(err)
	}
	facts, err := s.Facts("o/r", 1, "abc1234")
	if err != nil {
		t.Fatal(err)
	}
	if class, refused := gates.LandsOnWord(map[string]string{gates.StandingRefused: facts[gates.StandingRefused], gates.PortOfAttr: "9", gates.PortInteractsAttr: gates.PortCleanWord}); class != gates.OldTreePortClass || refused != "" {
		t.Fatalf("class %q, refused %q, want the port landing on his word", class, refused)
	}
}

func TestAPortSourceLandedWhenGitHubSaysMergedOrACommitNamingItIsOnTrunk(t *testing.T) {
	const source = 40810
	for name, c := range map[string]struct {
		state string
		log   string
		want  bool
	}{
		"merged on GitHub":                            {"MERGED", "", true},
		"closed by the queue, its commit on trunk":    {"CLOSED", "sand: publish a thread's task outline (#40810)\n", true},
		"closed with no commit on trunk":              {"CLOSED", "", false},
		"closed, and only a later commit mentions it": {"CLOSED", "sand: port of the outline (#40810) again (#50303)\n", false},
		"closed, and only a revert names it":          {"CLOSED", "Revert \"sand: outline (#40810)\" (#41000)\n", false},
	} {
		t.Run(name, func(t *testing.T) {
			runner := &shelltest.Fake{}
			runner.Reply(`[{"number":40810,"state":"`+c.state+`"}]`, "ccx", "vcs", "pr", "status")
			runner.Reply("", "git", "-C", "/checkout", "fetch")
			runner.Reply(c.log, "git", "-C", "/checkout", "log")
			a := app.New(io.Discard)
			a.Runner = runner
			got, err := sourceLanded(context.Background(), a, config.Config{Repo: "o/r", Checkout: "/checkout", Trunk: "dev"}, source)
			if err != nil {
				t.Fatal(err)
			}
			if got != c.want {
				t.Fatalf("sourceLanded = %v, want %v", got, c.want)
			}
		})
	}
}

func TestChangedFilesKeepsAFileGitHubGaveNoPatchFor(t *testing.T) {
	files, patches, err := changedFiles(`["a_test.go","+x"]` + "\n" + `["big_test.go",null]` + "\n")
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(files, []string{"a_test.go", "big_test.go"}) || *patches["a_test.go"] != "+x" || patches["big_test.go"] != nil {
		t.Fatalf("files %q, patches %v", files, patches)
	}
}
