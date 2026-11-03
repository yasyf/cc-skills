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
	"syscall"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

const onsetStamp = "2026-10-01T16:45:36Z"

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

func mustVerb(t *testing.T, args ...string) string {
	t.Helper()
	out, err := verb(t, args...)
	if err != nil {
		t.Fatalf("lr incident %v: %v", args, err)
	}
	return out
}

func home(t *testing.T) Store {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("HOME", dir)
	t.Setenv("CLAUDE_CONFIG_DIR", "")
	return Store{Root: filepath.Join(dir, ".claude", "long-running", "incidents")}
}

func openAlert(t *testing.T, id string, extra ...string) {
	t.Helper()
	args := append([]string{
		"open", "--kind", "alert", "--incident", id, "--target", "api", "--thread", "t", "--onset", onsetStamp, "--reported", reportedStamp,
		"--bus", "b", "--comms-lane", "c", "--root-lane", "r", "--checkout", "/m",
	}, extra...)
	mustVerb(t, args...)
}

func TestOpenRecordsItsFlagsAndNamesTheNextVerb(t *testing.T) {
	s := home(t)
	out := mustVerb(t, "open", "--kind", "alert", "--incident", "x2", "--target", "api", "--thread", "t", "--onset", onsetStamp, "--reported", reportedStamp,
		"--bus", "b", "--comms-lane", "c", "--root-lane", "r", "--checkout", "/m", "--alert", "https://forge-rf.sentry.io/issues/7766636402/", "--runbook", "4950740",
		"--adopt", "fix=api-1n85-fix", "--grant", "thread=g", "--ruling", ruling, "--entry-point", entryPoint)
	path, err := s.Path("x2")
	if err != nil {
		t.Fatal(err)
	}
	if want := "incident x2 at " + path + "; run: lr incident run --incident x2\n"; out != want {
		t.Fatalf("printed %q, want %q", out, want)
	}
	facts, err := s.Facts("x2", "alert", "runbook", "adopted", "grants", "rulings", "entry_point", "repo", "trunk")
	if err != nil {
		t.Fatal(err)
	}
	if facts.Alert != "https://forge-rf.sentry.io/issues/7766636402/" || facts.Runbook != "4950740" || facts.Adopted[RoleFix] != "api-1n85-fix" || facts.Grants["thread"] != "g" {
		t.Fatalf("facts %+v", facts)
	}
	if !reflect.DeepEqual(facts.Rulings, []string{ruling}) || facts.EntryPoint != entryPoint || facts.Repo != "Forge-AI/monorepo" || facts.Trunk != "dev" {
		t.Fatalf("facts %+v", facts)
	}
}

func TestOpenRefusesABadPair(t *testing.T) {
	home(t)
	for _, extra := range [][]string{{"--adopt", "backup=lane"}, {"--adopt", "fix="}, {"--grant", "deploy=ref"}} {
		args := append([]string{
			"open", "--kind", "alert", "--incident", "x", "--target", "api", "--thread", "t", "--onset", onsetStamp, "--reported", reportedStamp,
			"--bus", "b", "--comms-lane", "c", "--root-lane", "r", "--checkout", "/m",
		}, extra...)
		if _, err := verb(t, args...); err == nil {
			t.Errorf("%v was accepted", extra)
		}
	}
	if _, err := verb(t, "open", "--kind", "alert"); err == nil || !strings.Contains(err.Error(), "required") {
		t.Fatalf("got %v, want missing required flags", err)
	}
}

func TestNoteRecordsEachFieldAndBumpsTheInputRevision(t *testing.T) {
	s := home(t)
	openAlert(t, "x")
	out := mustVerb(t, "note", "--incident", "x", "--pr", "28998", "--mechanism", "m", "--live", "l", "--not-ours", "n", "--design-check", "d", "--design-ok")
	if out != "noted on x at input revision 2\n" {
		t.Fatalf("printed %q", out)
	}
	facts, err := s.Facts("x", "repair_pr", "mechanism", "live", "not_ours", "design_check", "design_ok")
	if err != nil {
		t.Fatal(err)
	}
	if facts.RepairPR != 28998 || facts.Mechanism != "m" || facts.Live != "l" || facts.NotOurs != "n" || facts.DesignCheck != "d" || !facts.DesignOK {
		t.Fatalf("facts %+v", facts)
	}
	mustVerb(t, "note", "--incident", "x", "--mechanism", "m2")
	if facts, err = s.Facts("x", "repair_pr", "mechanism"); err != nil {
		t.Fatal(err)
	}
	if facts.RepairPR != 28998 || facts.Mechanism != "m2" {
		t.Fatalf("a later note changed what it did not name: %+v", facts)
	}
}

func TestGrantAddsANamedAuthorityAndRefusesAnother(t *testing.T) {
	s := home(t)
	openAlert(t, "x", "--grant", "thread=g-thread")
	if out := mustVerb(t, "grant", "--incident", "x", "--name", "sync", "--ref", "g-sync"); out != "x grant sync=g-sync\n" {
		t.Fatalf("printed %q", out)
	}
	facts, err := s.Facts("x", "grants")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(facts.Grants, map[string]string{"thread": "g-thread", "sync": "g-sync"}) {
		t.Fatalf("grants %v", facts.Grants)
	}
	if _, err := verb(t, "grant", "--incident", "x", "--name", "deploy", "--ref", "r"); err == nil {
		t.Fatal("an unknown grant was accepted")
	}
}

func TestStatusPrintsMilestonesAndOpenDecisionsInPacificTime(t *testing.T) {
	s := home(t)
	openAlert(t, "x")
	at := time.Date(2026, 10, 1, 17, 49, 0, 0, time.UTC)
	err := s.Owned("x", 1, func(tx *Tx) error {
		if _, err := tx.Milestone("opened", "alert outage", at); err != nil {
			return err
		}
		if _, err := tx.Decide("grant:sync", "needs the sync grant", at); err != nil {
			return err
		}
		_, err := tx.Decide("resolved", "already settled", at)
		if err != nil {
			return err
		}
		return tx.Resolve("resolved", at)
	})
	if err != nil {
		t.Fatal(err)
	}
	want := "x open owner=incident-x generation=1\n  10:49am opened: alert outage\n  open since 10:49am: needs the sync grant\n"
	if out := mustVerb(t, "status", "--incident", "x"); out != want {
		t.Fatalf("printed\n%s\nwant\n%s", out, want)
	}
	var dumped map[string]any
	if err := json.Unmarshal([]byte(mustVerb(t, "status", "--incident", "x", "--json")), &dumped); err != nil {
		t.Fatal(err)
	}
	if dumped["incident_id"] != "x" || dumped["status"] != "open" || dumped["facts"].(map[string]any)["target"] != "api" {
		t.Fatalf("dumped %v", dumped)
	}
}

func TestBriefWritesTheBriefsRunWouldLaunchAndPrintsTheirPaths(t *testing.T) {
	s := home(t)
	openAlert(t, "x", "--adopt", "evidence=plat-evidence")
	paths := strings.Split(strings.TrimSpace(mustVerb(t, "brief", "--incident", "x")), "\n")
	want := []string{filepath.Join(s.Root, "x", "incident-x-fix.full.md"), filepath.Join(s.Root, "x", "incident-x-evidence.full.md")}
	if !reflect.DeepEqual(paths, want) {
		t.Fatalf("paths %v", paths)
	}
	fix := readFile(t, paths[0])
	if !strings.Contains(fix, "incident-x-fix") || !strings.Contains(fix, "plat-evidence") || placeholder.FindString(fix+readFile(t, paths[1])) != "" {
		t.Fatalf("fix brief:\n%s", fix)
	}
	if out := mustVerb(t, "brief", "--incident", "x", "--role", "evidence"); strings.TrimSpace(out) != paths[1] {
		t.Fatalf("printed %q", out)
	}
	if _, err := verb(t, "brief", "--incident", "x", "--role", "backup"); err == nil {
		t.Fatal("a backup brief was written")
	}
}

func TestARunnerForAFormerOwnerRefusesToStart(t *testing.T) {
	s := home(t)
	openAlert(t, "x")
	transfer(t, s, "x", "runner-b")
	_, err := verb(t, "run", "--incident", "x", "--once")
	if err == nil || !strings.Contains(err.Error(), "is owned by runner-b at generation 2") {
		t.Fatalf("got %v", err)
	}
}

func TestRunRefusesAnIncidentThatAlreadyHasARunner(t *testing.T) {
	s := home(t)
	openAlert(t, "x")
	lock, err := os.OpenFile(filepath.Join(s.Root, ".x.runner"), os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		t.Fatal(err)
	}
	_, err = verb(t, "run", "--incident", "x", "--once")
	if err == nil || !strings.Contains(err.Error(), "already has a runner") {
		t.Fatalf("got %v", err)
	}
}

func TestRunNeedsAnIncidentOrASlugWithABrief(t *testing.T) {
	home(t)
	for _, args := range [][]string{{"run"}, {"run", "slug"}, {"run", "slug", "--incident", "x", "--fix-brief", "b"}} {
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

func TestCommsPostsToTheLaneAndReadsItsPostedTS(t *testing.T) {
	bus := &fakeBus{nextSeq: 6}
	comms := CommsBus{Bus: bus, Drive: "bus1", Sender: "incident-x", CommsLane: "incident-comms", RootLane: "release-v3", Topic: "incident:x"}
	ctx := context.Background()
	seq, err := comms.Event(ctx, "ack", map[string]any{"reaction": "eyes"}, "g-thread")
	if err != nil || seq != 7 {
		t.Fatalf("seq %d, err %v", seq, err)
	}
	draft := bus.drafts[0]
	if draft.Kind != "ask" || !reflect.DeepEqual(draft.To, []string{"incident-comms"}) || draft.Topic != "incident:x" || draft.Lane != "incident-x" || draft.Drive != "bus1" {
		t.Fatalf("draft %+v", draft)
	}
	var text map[string]any
	if err := json.Unmarshal([]byte(draft.Text), &text); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(text, map[string]any{"event": "ack", "grant": "g-thread", "reaction": "eyes"}) {
		t.Fatalf("text %v", text)
	}
	bus.add(9, newRecord(t, 9, "incident-comms", "answer", "incident:x", "posted ts=1790877617.443359", 7, time.Now()))
	if ts, err := comms.Posted(ctx, 7); err != nil || ts != "1790877617.443359" {
		t.Fatalf("ts %q, err %v", ts, err)
	}
	if ts, err := comms.Posted(ctx, 8); err != nil || ts != "" {
		t.Fatalf("ts %q, err %v", ts, err)
	}
}

func TestCommsFindMatchesOnlyItsOwnEventsAndATruncatedPostStillMatches(t *testing.T) {
	bus := &fakeBus{}
	comms := CommsBus{Bus: bus, Drive: "bus1", Sender: "incident-x", CommsLane: "incident-comms", RootLane: "r", Topic: "incident:x"}
	ctx := context.Background()
	seq, err := comms.Event(ctx, "ack", map[string]any{"surface": "thread"}, "g")
	if err != nil {
		t.Fatal(err)
	}
	truncated := `{"event":"recovered","grant":"g","green":3,"red":[{"pr":"https://github.com/o/n/pu`
	bus.add(50, newRecord(t, 50, "incident-x", "ask", "incident:x", truncated, 0, time.Now(), "incident-comms"))
	bus.add(51, newRecord(t, 51, "someone-else", "ask", "incident:x", `{"event":"pr","grant":"g"}`, 0, time.Now(), "incident-comms"))
	if found, err := comms.Find(ctx, "ack"); err != nil || found != seq {
		t.Fatalf("found %d, want %d, err %v", found, seq, err)
	}
	if found, err := comms.Find(ctx, "recovered"); err != nil || found != 50 {
		t.Fatalf("found %d, err %v", found, err)
	}
	if found, err := comms.Find(ctx, "pr"); err != nil || found != 0 {
		t.Fatalf("found %d, err %v", found, err)
	}
}

func TestCommsAsksAndReportsToTheRootLane(t *testing.T) {
	bus := &fakeBus{}
	comms := CommsBus{Bus: bus, Drive: "b", Sender: "s", CommsLane: "c", RootLane: "root", Topic: "t"}
	ctx := context.Background()
	if err := comms.AskRoot(ctx, "q"); err != nil {
		t.Fatal(err)
	}
	if err := comms.Report(ctx, "r"); err != nil {
		t.Fatal(err)
	}
	if bus.drafts[0].Kind != "ask" || bus.drafts[1].Kind != "decision" || bus.drafts[0].To[0] != "root" || bus.drafts[1].To[0] != "root" {
		t.Fatalf("drafts %+v", bus.drafts)
	}
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

func TestAnIncidentPairPostsAddressedLaunchLinesKeyedByTheirRecordsSeq(t *testing.T) {
	for _, name := range []string{"inc-app-origin", "inc-app-origin-fix"} {
		t.Run(name, func(t *testing.T) {
			desk, bus, dir, out := deskFixture(t)
			desk.Name = name
			desk.FixBrief = briefFile(t, dir, "fix-brief.md", "fix")
			desk.EvidenceBrief = briefFile(t, dir, "evidence-brief.md", "evidence")
			bus.verdicts["R102"] = "LAUNCHED R102 {lane}: dispatch ctx_fix terminal term_fix"
			bus.verdicts["R104"] = "LAUNCHED R104 {lane}: dispatch ctx_ev terminal term_ev"
			if err := desk.Run(context.Background()); err != nil {
				t.Fatal(err)
			}
			if len(bus.drafts) != 2 {
				t.Fatalf("drafts %+v", bus.drafts)
			}
			for i, want := range []string{
				"orca-desk: launch inc-app-origin-fix NOW incident xhigh role=fix brief=" + desk.FixBrief,
				"orca-desk: launch inc-app-origin-evidence NOW incident xhigh role=evidence brief=" + desk.EvidenceBrief,
			} {
				draft := bus.drafts[i]
				if !reflect.DeepEqual(draft.To, []string{deskLane}) || draft.Kind != "go" || draft.Lane != "root" || draft.Topic != "inc-app-origin" || draft.Stamp {
					t.Fatalf("draft %+v", draft)
				}
				if draft.Text != want {
					t.Fatalf("line %q", draft.Text)
				}
			}
			for _, want := range []string{
				"inc-app-origin-fix R102 LAUNCHED R102 inc-app-origin-fix: dispatch ctx_fix terminal term_fix",
				"inc-app-origin-evidence R104 LAUNCHED R104 inc-app-origin-evidence: dispatch ctx_ev terminal term_ev",
			} {
				if !strings.Contains(out.String(), want) {
					t.Fatalf("output lacks %q:\n%s", want, out)
				}
			}
		})
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

func TestAnUnsettledLaunchFailsAtTheTimeout(t *testing.T) {
	desk, _, dir, out := deskFixture(t)
	desk.Name, desk.FixBrief, desk.Timeout = "slow", briefFile(t, dir, "fix.md", "fix"), 0
	if err := desk.Run(context.Background()); !errors.Is(err, ErrUnsettled) {
		t.Fatalf("got %v", err)
	}
	if !strings.Contains(out.String(), "slow-fix R102 unsettled after 0s") {
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
			if !strings.HasSuffix(err.Error(), "nothing reads orca-desk lines; spawn an Agent named `inc-x-fix` and one named `inc-x-evidence` instead") {
				t.Fatalf("got %v", err)
			}
			if len(bus.drafts) != 0 {
				t.Fatalf("drafts %+v", bus.drafts)
			}
		})
	}
}
