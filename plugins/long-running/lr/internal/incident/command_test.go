package incident

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func verb(t *testing.T, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	a := app.New(io.Discard)
	a.Now = func() time.Time { return time.Date(2026, 10, 1, 17, 25, 46, 0, time.UTC) }
	cmd := Command(a)
	cmd.SetOut(&out)
	cmd.SetErr(io.Discard)
	cmd.SetArgs(args)
	cmd.SilenceUsage = true
	cmd.SilenceErrors = true
	err := cmd.Execute()
	return out.String(), err
}

func TestRunNeedsASlugWithABrief(t *testing.T) {
	for _, args := range [][]string{{"run"}, {"run", "slug"}, {"run", "--fix-brief", "b"}} {
		if _, err := verb(t, args...); err == nil {
			t.Errorf("%v was accepted", args)
		}
	}
}

type fakeBus struct {
	records  []client.Record
	verdicts map[string]string
	drafts   []client.Draft
	nextSeq  int64
	at       time.Time
}

func newRecord(t *testing.T, seq int64, lane, kind, topic, text string, re int64, at time.Time, to ...string) client.Record {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"seq": seq, "drive": "prog", "lane": lane, "kind": kind, "at": at, "text": text, "topic": topic, "to": to, "re": re})
	if err != nil {
		t.Fatal(err)
	}
	var record client.Record
	if err := json.Unmarshal(raw, &record); err != nil {
		t.Fatal(err)
	}
	return record
}

func (f *fakeBus) add(seq int64, raw client.Record) {
	f.records = append(f.records, raw)
	f.nextSeq = max(f.nextSeq, seq)
}

func (f *fakeBus) Post(_ context.Context, d client.Draft) (client.Record, error) {
	f.drafts = append(f.drafts, d)
	f.nextSeq++
	raw, err := json.Marshal(map[string]any{"seq": f.nextSeq, "drive": d.Drive, "lane": d.Lane, "kind": d.Kind, "at": f.at, "text": d.Text, "topic": d.Topic, "to": d.To, "re": d.Re})
	if err != nil {
		return client.Record{}, err
	}
	var stored client.Record
	if err := json.Unmarshal(raw, &stored); err != nil {
		return client.Record{}, err
	}
	f.records = append(f.records, stored)
	if verdict, ok := f.verdicts[fmt.Sprintf("R%d", f.nextSeq)]; ok {
		_, after, _ := strings.Cut(d.Text, "launch ")
		lane, _, _ := strings.Cut(after, " ")
		f.nextSeq++
		reply, err := json.Marshal(map[string]any{"seq": f.nextSeq, "drive": d.Drive, "lane": deskRunner, "kind": "report", "at": f.at, "text": strings.ReplaceAll(verdict, "{lane}", lane)})
		if err != nil {
			return client.Record{}, err
		}
		var answer client.Record
		if err := json.Unmarshal(reply, &answer); err != nil {
			return client.Record{}, err
		}
		f.records = append(f.records, answer)
	}
	return stored, nil
}

func (f *fakeBus) Tail(_ context.Context, q client.Query) ([]client.Record, error) {
	var found []client.Record
	for _, record := range f.records {
		if record.Seq <= q.After || (!q.Since.IsZero() && record.At.Before(q.Since)) {
			continue
		}
		if len(q.Kinds) > 0 && !slices.Contains(q.Kinds, string(record.Kind)) {
			continue
		}
		found = append(found, record)
	}
	return found, nil
}

func deskFixture(t *testing.T) (DeskLaunch, *fakeBus, string, *bytes.Buffer) {
	t.Helper()
	dir := t.TempDir()
	state := filepath.Join(dir, "prog")
	claude := filepath.Join(dir, "claude")
	if err := os.MkdirAll(filepath.Join(claude, "long-running", "drives"), 0o750); err != nil {
		t.Fatal(err)
	}
	entry, err := json.Marshal(map[string]any{"drive": "abc", "sessions": []string{"s1"}, "state_dir": state})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(claude, "long-running", "drives", "abc.json"), entry, 0o600); err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	bus := &fakeBus{verdicts: map[string]string{}, at: now}
	bus.add(99, newRecord(t, 99, deskRunner, "state", "orca", "orca alive 11:20 PM pass 3", 0, now, "drive-watchdog@prog"))
	bus.add(100, newRecord(t, 100, "root", "go", "old", "R2025 (11:50 PM) orca-desk: launch old-fix NOW incident xhigh brief=/b.md", 0, now, deskLane))
	bus.add(101, newRecord(t, 101, "root", "go", "old", "orca-desk: unhold sandsql-5177", 0, now, deskLane))
	var out bytes.Buffer
	return DeskLaunch{
		Bus: bus, Drive: "prog", Author: "root", Claude: claude, Timeout: time.Minute, Poll: time.Millisecond, Now: time.Now, Out: &out,
	}, bus, dir, &out
}

func briefFile(t *testing.T, dir, name, text string) string {
	t.Helper()
	path := filepath.Join(dir, name)
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestAnIncidentTrioPostsAddressedLaunchLinesKeyedByTheirRecordsSeq(t *testing.T) {
	for _, name := range []string{"inc-app-origin", "inc-app-origin-fix"} {
		t.Run(name, func(t *testing.T) {
			desk, bus, dir, out := deskFixture(t)
			desk.Name = name
			desk.FixBrief = briefFile(t, dir, "fix-brief.md", "fix")
			desk.EvidenceBrief = briefFile(t, dir, "evidence-brief.md", "evidence")
			desk.CommsBrief = briefFile(t, dir, "comms-brief.md", "comms")
			bus.verdicts["R102"] = "LAUNCHED R102 {lane}: dispatch ctx_fix terminal term_fix"
			bus.verdicts["R104"] = "LAUNCHED R104 {lane}: dispatch ctx_ev terminal term_ev"
			bus.verdicts["R106"] = "LAUNCHED R106 {lane}: dispatch ctx_co terminal term_co"
			if err := desk.Run(context.Background()); err != nil {
				t.Fatal(err)
			}
			if len(bus.drafts) != 3 {
				t.Fatalf("drafts %+v", bus.drafts)
			}
			for i, want := range []string{
				"orca-desk: launch inc-app-origin-fix NOW incident xhigh role=fix brief=" + desk.FixBrief,
				"orca-desk: launch inc-app-origin-evidence NOW incident xhigh role=evidence brief=" + desk.EvidenceBrief,
				"orca-desk: launch inc-app-origin-comms NOW incident-opus xhigh role=comms brief=" + desk.CommsBrief,
			} {
				draft := bus.drafts[i]
				if !reflect.DeepEqual(draft.To, []string{deskLane}) || draft.Kind != "go" || draft.Lane != "root" || draft.Topic != "inc-app-origin" {
					t.Fatalf("draft %+v", draft)
				}
				if draft.Text != want {
					t.Fatalf("line %q", draft.Text)
				}
			}
			for _, want := range []string{
				"inc-app-origin-fix R102 LAUNCHED R102 inc-app-origin-fix: dispatch ctx_fix terminal term_fix",
				"inc-app-origin-evidence R104 LAUNCHED R104 inc-app-origin-evidence: dispatch ctx_ev terminal term_ev",
				"inc-app-origin-comms R106 LAUNCHED R106 inc-app-origin-comms: dispatch ctx_co terminal term_co",
			} {
				if !strings.Contains(out.String(), want) {
					t.Fatalf("output lacks %q:\n%s", want, out)
				}
			}
		})
	}
}

func TestARefusedCommsLaneDoesNotFailAnIncidentWhoseFixLaneStands(t *testing.T) {
	cases := []struct {
		name       string
		fixVerdict bool
		timeout    time.Duration
		commsKey   string
	}{
		{"the fix lane launched", true, time.Minute, "R104"},
		{"the fix lane's verdict is still pending", false, time.Millisecond, "R103"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			desk, bus, dir, out := deskFixture(t)
			desk.Name, desk.FixBrief, desk.CommsBrief, desk.Timeout = "inc-x", briefFile(t, dir, "fix.md", "fix"), briefFile(t, dir, "comms.md", "comms"), c.timeout
			if c.fixVerdict {
				bus.verdicts["R102"] = "LAUNCHED R102 {lane}: dispatch ctx_fix terminal term_fix"
			}
			bus.verdicts[c.commsKey] = "LAUNCH-FAILED " + c.commsKey + " {lane}: refused"
			if err := desk.Run(context.Background()); err != nil {
				t.Fatal(err)
			}
			if want := "inc-x-comms " + c.commsKey + " LAUNCH-FAILED " + c.commsKey + " inc-x-comms: refused"; !strings.Contains(out.String(), want) {
				t.Fatalf("output lacks %q:\n%s", want, out)
			}
		})
	}
}

func TestARetriedIncidentRunGivesEachLaunchItsOwnAttemptKey(t *testing.T) {
	desk, bus, dir, _ := deskFixture(t)
	desk.Name, desk.FixBrief = "inc-x", briefFile(t, dir, "fix.md", "fix")
	bus.verdicts["R102"] = "LAUNCH-FAILED desk-lane-{lane}/R102 {lane}: no terminal has appeared"
	if err := desk.Run(context.Background()); err == nil {
		t.Fatal("the refused launch succeeded")
	}
	bus.verdicts["R104"] = "LAUNCHED R104 {lane}: dispatch ctx_l terminal term_l"

	if err := desk.Run(context.Background()); err != nil {
		t.Fatal(err)
	}

	first, second := bus.drafts[0], bus.drafts[1]
	if first.Text != second.Text || first.ID == "" || first.ID == second.ID {
		t.Fatalf("attempts share a key the bus dedups on: %q and %q", first.ID, second.ID)
	}
}

func TestALaunchTheRunnerRefusesFailsWithItsLine(t *testing.T) {
	desk, bus, dir, out := deskFixture(t)
	desk.Name, desk.FixBrief = "inc-x", briefFile(t, dir, "fix.md", "fix")
	bus.verdicts["R102"] = "LAUNCH-FAILED desk-lane-{lane}/R102 {lane}: orca-launch.sh exited 1"
	err := desk.Run(context.Background())
	if err == nil || !strings.Contains(err.Error(), "did not launch") {
		t.Fatalf("got %v", err)
	}
	if want := "inc-x-fix R102 LAUNCH-FAILED desk-lane-inc-x-fix/R102 inc-x-fix: orca-launch.sh exited 1"; !strings.Contains(out.String(), want) {
		t.Fatalf("output:\n%s", out)
	}
}

func TestALaunchWithNoVerdictFromALiveRunnerIsPostedAndSucceeds(t *testing.T) {
	desk, _, dir, out := deskFixture(t)
	desk.Name, desk.FixBrief, desk.Timeout = "slow", briefFile(t, dir, "fix.md", "fix"), 0
	if err := desk.Run(context.Background()); err != nil {
		t.Fatalf("got %v", err)
	}
	if !strings.Contains(out.String(), "slow-fix R102 posted with no verdict after 0s; the orca runner is alive, so its verdict arrives as a desk-runner record") {
		t.Fatalf("output:\n%s", out)
	}
}

func TestALaunchWithNoVerdictFromAStaleRunnerIsUnsettled(t *testing.T) {
	desk, bus, dir, out := deskFixture(t)
	desk.Name, desk.FixBrief, desk.Timeout = "slow", briefFile(t, dir, "fix.md", "fix"), 0
	desk.Now = func() time.Time {
		if len(bus.drafts) == 0 {
			return bus.at
		}
		return bus.at.Add(6 * time.Minute)
	}
	if err := desk.Run(context.Background()); !errors.Is(err, ErrUnsettled) {
		t.Fatalf("got %v", err)
	}
	if !strings.Contains(out.String(), "slow-fix R102 unsettled after 0s: no orca desk runner has posted an orca alive record on cci drive prog in the last five minutes") {
		t.Fatalf("output:\n%s", out)
	}
}

func TestAMissingBriefPostsNothing(t *testing.T) {
	desk, bus, dir, _ := deskFixture(t)
	desk.Name, desk.FixBrief = "inc-x", filepath.Join(dir, "missing.md")
	if err := desk.Run(context.Background()); err == nil {
		t.Fatal("a missing brief launched")
	}
	if len(bus.drafts) != 0 {
		t.Fatalf("drafts %+v", bus.drafts)
	}
}

const alertTemplate = "# <lane> (incident fix lane)\nRoot: fill this in\n\nAlert: <monitor or Slack link>\n\nRoot: and this\n\n\n0. SCOPE FIRST: post `SCOPE <lane>: active <yes|no>` as `<lane>`.\n"

func TestAnAlertFillsTheIncidentTemplateIntoTheLanesOwnBrief(t *testing.T) {
	desk, bus, dir, _ := deskFixture(t)
	alert := "monitor 339599887 Run queue backlog not draining, 4:46 PM PT. Owner words, verbatim: fix asap"
	desk.Name, desk.FixBrief, desk.Alert = "run-queue", briefFile(t, dir, "incident-template-fix.md", alertTemplate), alert
	bus.verdicts["R102"] = "LAUNCHED R102 {lane}: dispatch ctx_fix terminal term_fix"
	if err := desk.Run(context.Background()); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "prog", "run-queue-fix", "brief.md")
	want := "# run-queue-fix (incident fix lane)\n\nAlert: " + alert + "\n\n0. SCOPE FIRST: post `SCOPE run-queue-fix: active <yes|no>` as `run-queue-fix`.\n"
	if got := readFile(t, path); got != want {
		t.Fatalf("brief:\n%q\nwant\n%q", got, want)
	}
	if !strings.HasSuffix(bus.drafts[0].Text, "orca-desk: launch run-queue-fix NOW incident xhigh role=fix brief="+path) {
		t.Fatalf("line %q", bus.drafts[0].Text)
	}
}

func TestAnAlertHoldingItsOwnAngleBracketsStillFillsAndPosts(t *testing.T) {
	desk, bus, dir, _ := deskFixture(t)
	alert := "monitor 339599887 fires on <RUN> for team <X>, 8:40 PM PT"
	desk.Name, desk.FixBrief, desk.Alert = "run-queue", briefFile(t, dir, "t.md", alertTemplate), alert
	bus.verdicts["R102"] = "LAUNCHED R102 {lane}: dispatch ctx_fix terminal term_fix"
	if err := desk.Run(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got := readFile(t, filepath.Join(dir, "prog", "run-queue-fix", "brief.md")); !strings.Contains(got, "Alert: "+alert+"\n") {
		t.Fatalf("brief:\n%s", got)
	}
}

func TestATemplateWithNoAlertLinePostsNothing(t *testing.T) {
	desk, bus, dir, _ := deskFixture(t)
	desk.Name, desk.FixBrief, desk.Alert = "run-queue", briefFile(t, dir, "t.md", "# <lane>\n"), "monitor 1"
	if err := desk.Run(context.Background()); err == nil || len(bus.drafts) != 0 {
		t.Fatalf("got %v, drafts %+v", err, bus.drafts)
	}
}

func TestABriefStillHoldingARootPlaceholderPostsNothing(t *testing.T) {
	for i, text := range []string{
		"# <lane>\n\nAlert: monitor 1, 4:46 PM\n",
		"# run-queue-fix\n\nAlert: <monitor or Slack link>\n",
		"# run-queue-fix\n\nAlert: monitor 1. Owner words, verbatim: <...>.\n",
		"# run-queue-fix\n\nAlert: monitor 1\nWork in <state>/run-queue-fix.\n",
	} {
		t.Run(strconv.Itoa(i), func(t *testing.T) {
			desk, bus, dir, _ := deskFixture(t)
			desk.Name, desk.FixBrief = "run-queue", briefFile(t, dir, "run-queue-fix/brief.md", text)
			if err := desk.Run(context.Background()); err == nil || !strings.Contains(err.Error(), "still holds a placeholder") {
				t.Fatalf("got %v", err)
			}
			if len(bus.drafts) != 0 {
				t.Fatalf("drafts %+v", bus.drafts)
			}
		})
	}
}

func TestALanesOwnPlaceholdersPassOnceRootFilledItsOwn(t *testing.T) {
	desk, bus, dir, _ := deskFixture(t)
	desk.Name, desk.FixBrief = "run-queue", briefFile(t, dir, "run-queue-fix/brief.md", "# run-queue-fix\n\nAlert: monitor 1, 4:46 PM\n\nPost `CANDIDATE run-queue-fix: <files> <sha>`.\n")
	bus.verdicts["R102"] = "LAUNCHED R102 {lane}: dispatch ctx_fix terminal term_fix"
	if err := desk.Run(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestADriveTheRegistryLacksPostsNothing(t *testing.T) {
	desk, bus, dir, _ := deskFixture(t)
	desk.Drive, desk.Name, desk.FixBrief = "other", "inc-x", briefFile(t, dir, "fix.md", "fix")
	err := desk.Run(context.Background())
	if err == nil || !strings.Contains(err.Error(), "no drive in the registry is cci drive other") {
		t.Fatalf("got %v", err)
	}
	if len(bus.drafts) != 0 {
		t.Fatalf("drafts %+v", bus.drafts)
	}
}

func TestASessionFindsItsDriveInTheRegistry(t *testing.T) {
	desk, bus, dir, _ := deskFixture(t)
	t.Setenv(sessionEnv, "s1")
	desk.Drive, desk.Name, desk.FixBrief = "", "inc-x", briefFile(t, dir, "fix.md", "fix")
	bus.verdicts["R102"] = "LAUNCHED R102 {lane}: dispatch ctx_fix terminal term_fix"
	if err := desk.Run(context.Background()); err != nil {
		t.Fatal(err)
	}
	if bus.drafts[0].Drive != "prog" {
		t.Fatalf("draft %+v", bus.drafts[0])
	}
}

func TestARunnerWithNoRecentAliveRecordRefusesNamingTheAgentFallback(t *testing.T) {
	for name, age := range map[string]time.Duration{"never": 0, "stale": 6 * time.Minute} {
		t.Run(name, func(t *testing.T) {
			desk, bus, dir, _ := deskFixture(t)
			desk.Name, desk.FixBrief = "inc-x", briefFile(t, dir, "fix.md", "fix")
			desk.EvidenceBrief = briefFile(t, dir, "evidence.md", "evidence")
			desk.CommsBrief = briefFile(t, dir, "comms.md", "comms")
			bus.records = slices.DeleteFunc(bus.records, func(record client.Record) bool { return record.Lane == deskRunner })
			if age > 0 {
				bus.add(98, newRecord(t, 98, deskRunner, "state", "orca", "orca alive", 0, time.Now().Add(-age)))
			}
			err := desk.Run(context.Background())
			if err == nil || !strings.HasPrefix(err.Error(), "refused: no orca desk runner has posted an orca alive record on cci drive prog in the last five minutes (") {
				t.Fatalf("got %v", err)
			}
			if strings.Contains(err.Error(), "(never)") != (age == 0) {
				t.Fatalf("got %v", err)
			}
			if !strings.HasSuffix(err.Error(), "nothing reads orca-desk lines; spawn an Agent named `inc-x-fix` and one named `inc-x-evidence` and one named `inc-x-comms` instead") {
				t.Fatalf("got %v", err)
			}
			if len(bus.drafts) != 0 {
				t.Fatalf("drafts %+v", bus.drafts)
			}
		})
	}
}

func TestAStuckPassRefusesAnIncidentLaunchAndALongOneDoesNot(t *testing.T) {
	for name, running := range map[string]time.Duration{"long": 8 * time.Minute, "stuck": StuckPass} {
		t.Run(name, func(t *testing.T) {
			desk, bus, dir, _ := deskFixture(t)
			desk.Name, desk.FixBrief = "inc-x", briefFile(t, dir, "fix.md", "fix")
			bus.records = slices.DeleteFunc(bus.records, func(record client.Record) bool { return record.Lane == deskRunner })
			alive := newRecord(t, 98, deskRunner, "state", "orca", "orca alive pass 3", 0, time.Now())
			alive.Fields.Attrs = map[string]string{PassSecondsAttr: fmt.Sprint(int(running.Seconds())), PassStepAttr: "deliver"}
			bus.add(98, alive)
			fault, err := desk.runnerFault(context.Background(), "prog")
			if err != nil {
				t.Fatal(err)
			}
			if running < StuckPass {
				if fault != "" {
					t.Fatal(fault)
				}
				return
			}
			err = desk.Run(context.Background())
			if err == nil || !strings.HasPrefix(err.Error(), "refused: the orca desk runner on cci drive prog is alive but stuck: pass 3 has run 20 minutes, at step deliver, so nothing reads orca-desk lines; spawn an Agent named `inc-x-fix`") {
				t.Fatalf("got %v", err)
			}
			if len(bus.drafts) != 0 {
				t.Fatalf("posted %+v", bus.drafts)
			}
		})
	}
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

type cciMe string

func (line cciMe) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	if !slices.Equal(c.Argv, []string{"cci", "me"}) {
		return shell.Result{}, fmt.Errorf("unexpected %v", c.Argv)
	}
	return shell.Result{Stdout: string(line) + "\n"}, nil
}

func TestADeskLaunchFromASessionTheRegistryDoesNotListUsesTheDriveCCIBindsItTo(t *testing.T) {
	t.Setenv(sessionEnv, "a-later-session")
	for _, c := range []struct {
		name, me, want, refusal string
	}{
		{"bound", "monorepo-8a lane drive=prog lane=root busy now", "abc", ""},
		{"unbound", "monorepo-8a lane lane=root busy now", "", "no drive in the registry is bound to session a-later-session and cci me names none"},
		{"bound elsewhere", "monorepo-8a lane drive=other lane=root busy now", "", "no drive in the registry is cci drive other"},
	} {
		t.Run(c.name, func(t *testing.T) {
			d, _, _, _ := deskFixture(t)
			d.Drive, d.Runner = "", cciMe(c.me)
			entry, err := d.entry(context.Background())
			if c.refusal != "" {
				if err == nil || !strings.Contains(err.Error(), c.refusal) {
					t.Fatalf("entry %+v, %v, want refusal %q", entry, err, c.refusal)
				}
				return
			}
			if err != nil || entry.Drive != c.want {
				t.Fatalf("entry %+v, %v, want %s", entry, err, c.want)
			}
		})
	}
}
