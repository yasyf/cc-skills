package handoff

import (
	"bytes"
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans/planstest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	testRepo     = "/repo"
	testLedger   = "1a2b3c4d1a2b3c4d1a2b3c4d1a2b3c4d1a2b3c4d"
	registerDoc  = "cccccccccccccccccccccccccccccccccccccccc"
	registerBody = "# brook register\n\n1. Release as it merges, never on the owner's word (4ffc9a5).\n2. Pulumi state is the only truth (ec2881e).\n"
	registerTag  = "standing-rules:brook"
	preCompact   = "# ssql drive: pre-compact handoff 2026-10-10T2215Z (supersedes a3f05a3)\n\nRead first after the plan. Drive ssql-hacks, coordinator session b577ef71.\n\n## Standing owner rules\n\n- Never cancel a release build (answer 4cd6a3d).\n- Dormant reads wake and migrate on every target (answer b70e998).\n\n## A. Every owner instruction, with receipts\n\n### This window (2026-10-10)\n1. \"are you waiting for anything from me?\" ANSWERED: no.\n\n## C. Other in-flight work\n\n| item | state | owner / next |\n|---|---|---|\n| Registry stack R2 | green | landing-desk |\n\n## G. Open owner-facing items\n- #23 reply to Andrew: the copy is drafted and unsent."
)

var (
	docA = strings.Repeat("a", 40)
	docB = strings.Repeat("b", 40)
	base = time.Date(2026, 10, 10, 22, 0, 0, 0, time.UTC)
)

type fakeNotes struct {
	mu            sync.Mutex
	stdout        bytes.Buffer
	docs          map[string]string
	order         []string
	active        []string
	titles        map[string]string
	supersedes    map[string][]string
	updated       map[string]string
	stuck         map[string]bool
	history       map[string][]map[string]any
	registers     []string
	registerLabel string
	added         map[string]string
	calls         [][]string
	answers       []string
	askRows       []ledger.Row
	standing      []busRecord
	laneRecords   []busRecord
	notes         *planstest.Fake
	rowsJSON      string
	rowsErr       string
	laneJSON      string
	ownerReport   string
	blockers      [][2]string
}

func creationEntry(body string) map[string]any {
	return map[string]any{"kind": "create", "changes": []map[string]any{{"field": "body", "to": body}}}
}

func generatedRecord(narrative string) string {
	return "# brook: progress 2026-10-01T0000Z (generated)\n\n" + generatedLine + "2026-10-01T00:00:00Z. Every section above `## Root narrative` is rebuilt at each handoff.\n\n## Root narrative\n\n_From doc aaaaaaa._\n\n" + narrative + "\n"
}

func askRow(key string, fields map[string]string) ledger.Row {
	row := map[string]string{"text": "ask " + key, "lane": "lane-a", "accept": "check", "asked_at": "2026-10-01T00:00:00Z"}
	for name, value := range fields {
		row[name] = value
	}
	return ledger.Row{Key: "ask/" + key, Fields: row}
}

func newFake(narrative string, withRegister bool, rows ...ledger.Row) *fakeNotes {
	if narrative == "" {
		narrative = "## The coordinator's next actions\n1. watch Acme"
	}
	if len(rows) == 0 {
		rows = []ledger.Row{askRow("000001", nil), askRow("000002", map[string]string{"dropped_at": "2026-10-01T01:00:00Z"}), askRow("000003", map[string]string{"answered_at": "2026-10-01T01:00:00Z"})}
	}
	f := &fakeNotes{
		docs:          map[string]string{docA: generatedRecord(narrative)},
		order:         []string{docA},
		active:        []string{docA},
		titles:        map[string]string{docA: "brook: progress 2026-10-01T0000Z (generated)"},
		supersedes:    map[string][]string{},
		updated:       map[string]string{},
		stuck:         map[string]bool{},
		history:       map[string][]map[string]any{},
		registerLabel: registerTag,
		added:         map[string]string{},
		answers:       []string{"4ffc9a5000000000000000000000000000000000", "ec2881e000000000000000000000000000000000", "abcdef1000000000000000000000000000000000"},
		askRows:       rows,
		standing:      []busRecord{{Seq: 2, Drive: "brook", Topic: standingTopic, Kind: goKind, Text: "every landed PR is deployed in the same pass it lands"}},
		notes:         &planstest.Fake{},
		rowsJSON:      `{"ledger":null,"program":"brook","rows":{},"classified":[]}`,
	}
	if withRegister {
		f.addRegister(registerDoc, registerBody)
	}
	return f
}

func (f *fakeNotes) addRegister(id, body string) {
	f.docs[id] = body
	f.order = append(f.order, id)
	f.registers = append(f.registers, id)
}

func (f *fakeNotes) rule(kind, text string, re int64, ccn string) {
	f.standing = append(f.standing, busRecord{Seq: int64(len(f.standing) + 2), Drive: "brook", Topic: standingTopic, Kind: kind, Text: text, Re: re, CCN: ccn})
}

func (f *fakeNotes) write(doc, body, title string, supersedes ...string) {
	f.docs[doc], f.titles[doc] = body, title
	f.order = append(f.order, doc)
	f.active = append(f.active, doc)
	for _, older := range supersedes {
		f.active = slices.DeleteFunc(f.active, func(id string) bool { return id == older })
		f.supersedes[doc] = append(f.supersedes[doc], older)
	}
}

func (f *fakeNotes) touch(doc string) {
	f.updated[doc] = fmt.Sprintf("2026-10-02T00:%02d:00Z", len(f.updated))
}

func (f *fakeNotes) tags(doc string) []string {
	if slices.Contains(f.registers, doc) {
		return []string{f.registerLabel}
	}
	return []string{"progress:brook"}
}

func (f *fakeNotes) record(doc string) map[string]any {
	created := fmt.Sprintf("2026-10-01T00:%02d:00Z", slices.Index(f.order, doc))
	title, ok := f.titles[doc]
	if !ok {
		title = "brook: progress"
	}
	record := map[string]any{"id": doc, "title": title, "body": f.docs[doc], "tags": f.tags(doc), "created_at": created, "updated_at": cmp.Or(f.updated[doc], created)}
	newer := []string{}
	for candidate, olds := range f.supersedes {
		if slices.Contains(olds, doc) {
			newer = append(newer, candidate)
		}
	}
	slices.Sort(newer)
	if len(newer) > 0 {
		record["superseded_by"] = newer
	}
	return record
}

func labelsIn(argv []string) []string {
	found := []string{}
	for at, arg := range argv {
		if arg == "--label" {
			found = append(found, argv[at+1])
		}
	}
	return found
}

func jsonResult(t any) (shell.Result, error) {
	data, err := json.Marshal(t)
	return shell.Result{Stdout: string(data)}, err
}

func (f *fakeNotes) listDocs(argv []string) (shell.Result, error) {
	labels := labelsIn(argv)
	listed := f.active
	if slices.Contains(labels, f.registerLabel) {
		listed = f.registers
	}
	if !slices.Contains(argv, "--full") {
		rows := make([]map[string]any, 0, len(listed))
		for _, doc := range listed {
			rows = append(rows, map[string]any{"id": doc, "title": f.record(doc)["title"], "updated_at": f.record(doc)["updated_at"]})
		}
		return jsonResult(rows)
	}
	listed = slices.Clone(listed)
	if slices.Contains(argv, "--include-superseded") {
		for _, doc := range f.order {
			if _, older := f.record(doc)["superseded_by"]; older && !slices.Contains(listed, doc) {
				listed = append(listed, doc)
			}
		}
	}
	rows := []map[string]any{}
	for _, doc := range listed {
		if holdsAll(f.tags(doc), labels) {
			rows = append(rows, f.record(doc))
		}
	}
	return jsonResult(rows)
}

func holdsAll(tags, labels []string) bool {
	for _, label := range labels {
		if !slices.Contains(tags, label) {
			return false
		}
	}
	return true
}

func (f *fakeNotes) editDoc(c shell.Command) (shell.Result, error) {
	doc := c.Argv[5]
	entries, ok := f.history[doc]
	if !ok {
		entries = []map[string]any{creationEntry(f.docs[doc])}
	}
	f.history[doc] = append([]map[string]any{{"kind": "edit", "changes": []map[string]any{{"field": "body", "from": f.docs[doc], "to": c.Stdin}}}}, entries...)
	f.docs[doc] = c.Stdin
	if at := slices.Index(c.Argv, "--title"); at >= 0 {
		f.titles[doc] = c.Argv[at+1]
	}
	f.touch(doc)
	return shell.Result{}, nil
}

func (f *fakeNotes) addDoc(c shell.Command) (shell.Result, error) {
	doc := strings.Repeat(fmt.Sprintf("%x", len(f.docs)), 40)
	f.docs[doc] = c.Stdin
	f.order = append(f.order, doc)
	f.history[doc] = []map[string]any{creationEntry(c.Stdin)}
	f.active = append(f.active, doc)
	f.added[doc] = c.Argv[5]
	f.titles[doc] = c.Argv[5]
	f.touch(doc)
	return jsonResult(map[string]string{"id": doc})
}

func (f *fakeNotes) called(argv []string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, argv)
}

func (f *fakeNotes) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	f.called(c.Argv)
	argv := c.Argv
	switch {
	case argv[0] == "rulings.py":
		if f.rowsErr != "" {
			return shell.Result{Code: 2, Stderr: f.rowsErr}, &shell.ExitError{Argv: argv, Code: 2, Stderr: f.rowsErr}
		}
		return shell.Result{Stdout: f.rowsJSON}, nil
	case argv[0] == "drive_supervisor.py":
		return shell.Result{Stdout: f.laneJSON}, nil
	case argv[0] == "lr":
		return shell.Result{Stdout: f.ownerReport}, nil
	case argv[1] == "ledger":
		return jsonResult(map[string]any{"id": argv[3], "rows": f.askRows})
	}
	switch verb := argv[3] + " " + argv[4]; {
	case verb == "answer list":
		rows := make([]map[string]string, 0, len(f.answers))
		for _, id := range f.answers {
			rows = append(rows, map[string]string{"id": id})
		}
		return jsonResult(rows)
	case argv[3] == "task" || argv[3] == "plan":
		out, err := f.notes.Ccn(ctx, argv[3:]...)
		return shell.Result{Stdout: out}, err
	case verb == "doc list":
		return f.listDocs(argv)
	case verb == "doc show":
		return jsonResult(f.record(argv[5]))
	case verb == "doc history":
		if entries, ok := f.history[argv[5]]; ok {
			return jsonResult(entries)
		}
		return jsonResult([]map[string]any{creationEntry(f.docs[argv[5]])})
	case verb == "doc add":
		return f.addDoc(c)
	case verb == "doc edit":
		return f.editDoc(c)
	case verb == "doc supersede":
		if !f.stuck[argv[5]] {
			f.active = slices.DeleteFunc(slices.Clone(f.active), func(id string) bool { return id == argv[5] })
		}
		f.supersedes[argv[7]] = append(f.supersedes[argv[7]], argv[5])
		return shell.Result{}, nil
	}
	return shell.Result{}, fmt.Errorf("unexpected call: %v", argv)
}

func (f *fakeNotes) records(_ context.Context, q recordQuery) ([]busRecord, error) {
	f.called([]string{"cci", "tail", q.drive})
	source := f.laneRecords
	if slices.Contains(q.kinds, goKind) {
		source = f.standing
	}
	found := []busRecord{}
	for _, record := range source {
		if !record.At.Before(q.since) {
			found = append(found, record)
		}
	}
	return found, nil
}

func (f *fakeNotes) post(_ context.Context, drive, _, text string) {
	f.blockers = append(f.blockers, [2]string{drive, text})
}

func tickingClock() func() time.Time {
	ticks := 0
	return func() time.Time {
		moment := base.Add(time.Duration(ticks) * time.Minute)
		ticks++
		return moment
	}
}

func depsFor(f *fakeNotes) *deps {
	return &deps{runner: f, ledger: ledger.CLI{Runner: f}, records: f.records, blocker: f.post, now: tickingClock(), stdin: strings.NewReader(""), stdout: &f.stdout}
}

type driveHome struct {
	root string
	plan string
	sess string
}

func writeFile(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func encoded(t *testing.T, value any) string {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func newHome(t *testing.T) driveHome {
	t.Helper()
	root := t.TempDir()
	t.Setenv("HOME", root)
	t.Setenv("CLAUDE_CONFIG_DIR", "")
	inbox := filepath.Join(root, ".claude", "scratch", "brook", "inbox")
	writeFile(t, filepath.Join(inbox, "orca-desk.md"), "- R1 (the coordinator) → desk: launch l01\n- R3 (the coordinator) → desk: launch l02\n")
	writeFile(t, filepath.Join(inbox, "orca-desk.md.cursor"), "R2\n")
	writeFile(t, filepath.Join(inbox, "landing-desk.md"), "- L1 (the coordinator) → desk: hold #1\n")
	plan := filepath.Join(root, ".claude", "plans", "brook.md")
	writeFile(t, plan, "# brook\n\n- Acme release on the owner's word\n- Acme releases as it merges (4ffc9a5), never on the owner's word\n")
	home := driveHome{root: root, plan: plan, sess: filepath.Join(root, "session.json")}
	home.writeDrive(t, map[string]any{})
	home.writeSession(t, []sessionTask{{ID: "7", Status: "pending", Subject: "OWNER: release as merged"}}, []backgroundTask{
		{Type: "teammate", Status: running, Description: "orca-desk-6"},
		{Type: "monitor", Status: running, Description: "batches.jsonl grep"},
	})
	return home
}

func (h driveHome) writeDrive(t *testing.T, changes map[string]any) {
	t.Helper()
	entry := map[string]any{"drive": "d1", "ledger": testLedger, "repo": "o/r", "checkout": testRepo, "sessions": []string{"s-root"}, "orca_run": "run_1", "state_dir": filepath.Join(h.root, "state", "brook")}
	for name, value := range changes {
		entry[name] = value
	}
	writeFile(t, filepath.Join(h.root, ".claude", "long-running", "drives", "d1.json"), encoded(t, entry))
}

func (h driveHome) writeSession(t *testing.T, tasks []sessionTask, background []backgroundTask) {
	t.Helper()
	writeFile(t, h.sess, encoded(t, session{SessionID: "s-root", Tasks: tasks, Background: background}))
}

func (h driveHome) options(extra ...func(*generateOptions)) generateOptions {
	opts := generateOptions{program: "brook", plan: h.plan, session: h.sess, repo: testRepo}
	for _, change := range extra {
		change(&opts)
	}
	return opts
}

func folderMode(opts *generateOptions) { opts.folder = true }
func strictMode(opts *generateOptions) { opts.strict = true }
func noSession(opts *generateOptions)  { opts.session = "" }

func runGenerate(t *testing.T, d *deps, opts generateOptions) generateOutput {
	t.Helper()
	code, err := d.generate(context.Background(), opts)
	if err != nil {
		t.Fatal(err)
	}
	out, _ := d.stdout.(*bytes.Buffer)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, out)
	}
	var found generateOutput
	if err := json.Unmarshal(out.Bytes(), &found); err != nil {
		t.Fatalf("%v: %s", err, out)
	}
	out.Reset()
	return found
}

func refused(t *testing.T, d *deps, opts generateOptions, want int) string {
	t.Helper()
	code, err := d.generate(context.Background(), opts)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, code, want)
	out, _ := d.stdout.(*bytes.Buffer)
	printed := out.String()
	out.Reset()
	return printed
}

func hasDocCall(f *fakeNotes, verb string) bool {
	return slices.ContainsFunc(f.calls, func(call []string) bool { return len(call) > 4 && call[3] == "doc" && call[4] == verb })
}

func str(value *string) string {
	if value == nil {
		return "<nil>"
	}
	return *value
}

func TestGenerateWritesEverySourceIntoTheActiveGeneratedDoc(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)

	out := runGenerate(t, depsFor(f), home.options())

	body := f.docs[str(out.ID)]
	equal(t, str(out.ID), docA)
	equal(t, f.active, []string{docA})
	equal(t, len(f.added), 0)
	equal(t, readFile(t, out.File), body)
	equal(t, filepath.Dir(out.File), filepath.Join(home.root, ".claude", "plans", "brook-progress"))
	if title := f.titles[docA]; !strings.HasPrefix(title, "brook: progress ") || !strings.HasSuffix(title, " (generated)") {
		t.Fatalf("title %q", title)
	}
	equal(t, out.ReadFirst, []string{})
	equal(t, str(out.Narrative), "aaaaaaa")
	contains(t, body,
		"Register `ccn doc show ccccccc`: 2 owner-approved rules, delivered verbatim after every compaction.\n",
		"- #2 [cci #2]",
		"- ask/000001 lane-a: ask 000001",
		"## Open tasks\n- none in progress\n- 1 pending in `TaskList`\n",
		"- teammate: orca-desk-6",
		"- monitor: batches.jsonl grep",
		"- orca-desk.md: head R3, cursor R2: - R3 (the coordinator) → desk: launch l02",
		"- landing-desk.md: head L1, cursor -: - L1 (the coordinator) → desk: hold #1",
		"- plan owner-gate line cites no live answer: brook.md:3: - Acme release on the owner's word",
		"- Drive `d1`: ledger `1a2b3c4`, Orca run `run_1`, checkout `/repo`, coordinator sessions s-root\n\n## Open owner asks\n",
		fmt.Sprintf(catchUp, "brook"),
	)
	lacks(t, body, "Unrelated", "000002", "000003")
	lacks(t, after(body, findingsHeading), "never on the owner's word")
	equal(t, strings.TrimSpace(after(body, "## Root narrative\n")), "_From doc aaaaaaa, carried forward._\n\n## The coordinator's next actions\n1. watch Acme")
}

func TestGenerateReadsTheRegisterOfTheDrivesProgramNotThePlanStem(t *testing.T) {
	home := newHome(t)
	home.writeDrive(t, map[string]any{"state_dir": filepath.Join(home.root, "state", "durable-program")})
	f := newFake("", true)
	f.registerLabel = "standing-rules:durable-program"

	out := runGenerate(t, depsFor(f), home.options())

	contains(t, f.docs[str(out.ID)], "Register `ccn doc show ccccccc`: 2 owner-approved rules")
	for _, call := range f.calls {
		for _, label := range labelsIn(call) {
			if strings.HasPrefix(label, "standing-rules:") {
				equal(t, label, "standing-rules:durable-program")
			}
		}
	}
}

func TestTheRootsDocGainsTheGeneratedSectionsInPlaceAndSupersedesTheRest(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	f.write(docB, "## The coordinator's next actions\n1. land l11", "brook: progress")

	out := runGenerate(t, depsFor(f), home.options())

	equal(t, str(out.ID), docB)
	equal(t, f.active, []string{docB})
	equal(t, hasDocCall(f, "add"), false)
	if !slices.ContainsFunc(f.calls, func(call []string) bool {
		return slices.Equal(call, []string{"ccn", "-R", testRepo, "doc", "supersede", docA, "--by", docB})
	}) {
		t.Fatalf("no supersede call in %v", f.calls)
	}
	body := f.docs[docB]
	equal(t, strings.Count(body, "## Standing owner rules"), 1)
	if !strings.HasSuffix(body, "_From doc bbbbbbb._\n\n## The coordinator's next actions\n1. land l11\n") {
		t.Fatalf("body:\n%s", body)
	}
	contains(t, out.Digest, "Then read the progress doc `ccn doc show bbbbbbb`")
}

func TestAHandWrittenDocIsAugmentedWhicheverSessionWroteItAndCarriedInPlaceAfter(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	f.write(docB, "## The coordinator's next actions\n1. land l11", "brook: progress")
	d := depsFor(f)

	first := runGenerate(t, d, home.options())
	second := runGenerate(t, d, home.options())

	equal(t, str(first.ID), docB)
	equal(t, str(second.ID), docB)
	equal(t, f.active, []string{docB})
	equal(t, len(f.added), 0)
	equal(t, f.titles[docB], "brook: progress")
	body := f.docs[docB]
	equal(t, strings.Count(body, "## Standing owner rules"), 1)
	equal(t, strings.Count(body, "_From "), 1)
	if !strings.HasSuffix(body, "_From doc bbbbbbb, carried forward._\n\n## The coordinator's next actions\n1. land l11\n") {
		t.Fatalf("body:\n%s", body)
	}
	equal(t, str(first.Narrative), "bbbbbbb")
	equal(t, str(second.Narrative), "bbbbbbb")
}

func TestAHandWrittenPreCompactHandoffStaysVerbatimUnderTheGeneratedSectionsAtEveryCompaction(t *testing.T) {
	home := newHome(t)
	f := newFake("", false)
	f.write(docB, preCompact, "brook: progress")
	d := depsFor(f)

	runGenerate(t, d, home.options())
	fresh := f.docs[docB]
	runGenerate(t, d, home.options())
	runGenerate(t, d, home.options())

	equal(t, after(fresh, narrativeHeading+"\n"), "\n_From doc bbbbbbb._\n\n"+preCompact+"\n")
	carriedBody := f.docs[docB]
	equal(t, after(carriedBody, narrativeHeading+"\n"), "\n_From doc bbbbbbb, carried forward._\n\n"+preCompact+"\n")
	lacks(t, carriedBody, "## Folded narrative", "## Carried binding sections")
	lines, _ := section(carriedBody)
	equal(t, lines[0], "- no `standing-rules` register doc; the coordinator's own `## Standing owner rules` follows verbatim under `## Root narrative`")
}

func TestGenerateFailsLoudlyWhenAnotherProgressDocStaysActive(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	f.write(docB, "## The coordinator's next actions\n1. land l11", "brook: progress")
	f.stuck[docA] = true

	printed := refused(t, depsFor(f), home.options(noSession), severalActive)

	if !strings.HasPrefix(printed, "2 active progress:brook docs after generation, expected only bbbbbbb: aaaaaaa, bbbbbbb; ") {
		t.Fatalf("printed %q", printed)
	}
}

func TestTheActiveGeneratedDocIsEditedInPlaceNotChained(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	d := depsFor(f)

	first := runGenerate(t, d, home.options())
	second := runGenerate(t, d, home.options())
	third := runGenerate(t, d, home.options())

	equal(t, str(first.ID), str(second.ID))
	equal(t, str(second.ID), str(third.ID))
	equal(t, f.active, []string{str(first.ID)})
	equal(t, hasDocCall(f, "add"), false)
	edits := 0
	for _, call := range f.calls {
		if len(call) > 5 && call[3] == "doc" && call[4] == "edit" && call[5] == str(first.ID) {
			edits++
		}
	}
	equal(t, edits, 3)
	equal(t, str(first.Register), registerDoc)
	equal(t, f.registers, []string{registerDoc})
	if !strings.HasSuffix(f.titles[str(first.ID)], " (generated)") {
		t.Fatalf("title %q", f.titles[str(first.ID)])
	}
	body := f.docs[str(first.ID)]
	equal(t, strings.Count(body, "## Standing owner rules"), 1)
	contains(t, body, "_From doc aaaaaaa, carried forward._")
}

func TestWithNoActiveProgressDocGenerationAddsOne(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	f.active = nil

	out := runGenerate(t, depsFor(f), home.options())

	equal(t, f.active, []string{str(out.ID)})
	if !strings.HasSuffix(f.added[str(out.ID)], " (generated)") {
		t.Fatalf("added %v", f.added)
	}
	equal(t, hasDocCall(f, "edit"), false)
	if !strings.HasSuffix(f.docs[str(out.ID)], "## Root narrative\n\n_The coordinator has written no narrative yet._\n") {
		t.Fatalf("body:\n%s", f.docs[str(out.ID)])
	}
}

func TestACarriedNarrativeIsOnlyThePreviousNarrativeSection(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	d := depsFor(f)

	first := runGenerate(t, d, home.options())
	second := runGenerate(t, d, home.options())

	body := f.docs[str(second.ID)]
	equal(t, strings.Count(body, "## Standing owner rules"), 1)
	equal(t, strings.Count(body, "1. watch Acme"), 1)
	equal(t, strings.Count(body, "_From "), 1)
	contains(t, body, "_From doc aaaaaaa, carried forward._")
	equal(t, str(first.ID), str(second.ID))
}

func TestFolderModeCallsNoCcn(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)

	out := runGenerate(t, depsFor(f), home.options(folderMode))

	equal(t, out.ID == nil, true)
	equal(t, len(f.calls), 0)
	equal(t, out.Register == nil, true)
	if want := "Compacted long-running drive `brook`. Before acting, read the generated handoff `" + out.File + "`, then "; !strings.HasPrefix(out.Digest, want) {
		t.Fatalf("digest %q", out.Digest)
	}
	contains(t, readFile(t, out.File), "- no `standing-rules` register doc")
}

func TestFolderModeNarratesTheHandWrittenFilesNewerThanTheLastGeneration(t *testing.T) {
	home := newHome(t)
	folder := progressFolder(home.plan)
	older, generated, newer := filepath.Join(folder, "older.md"), filepath.Join(folder, "2026-10-01T0000Z-generated.md"), filepath.Join(folder, "newer.md")
	writeFile(t, older, "the old narrative")
	writeFile(t, generated, generatedRecord("the generated narrative"))
	writeFile(t, newer, "the new narrative")
	for at, path := range []string{older, generated, newer} {
		moment := base.Add(time.Duration(at) * time.Minute)
		if err := os.Chtimes(path, moment, moment); err != nil {
			t.Fatal(err)
		}
	}
	f := newFake("", true)

	out := runGenerate(t, depsFor(f), home.options(folderMode))

	equal(t, str(out.Narrative), "newer.md")
	equal(t, out.Fresh, true)
	equal(t, out.ReadFirst, []string{})
	if !strings.HasSuffix(readFile(t, out.File), "_From file newer.md._\n\nthe new narrative\n") {
		t.Fatalf("file:\n%s", readFile(t, out.File))
	}
}

func TestDigestNamesTheRegisterFirst(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)

	out := runGenerate(t, depsFor(f), home.options())

	equal(t, splitLines(out.Digest), []string{
		"Compacted long-running drive `brook`. Before acting, read the standing rules register `ccn doc show ccccccc`: it arrives verbatim with your next tool result, binds every lane brief, and outranks the summary. Then read the progress doc `ccn doc show " + str(out.ID)[:7] + "`, then `" + home.plan + "`. Reload Skill `long-running` if its rules are gone.",
		"Register: 2 owner-approved rules, 1 live standing rules.",
		"Open: 1 owner asks, 1 tasks, 1 lanes, 1 monitors, 1 lint findings.",
	})
}

func TestDigestStaysInsideTheInjectedContextBudget(t *testing.T) {
	const budget = 2000
	var rules strings.Builder
	for number := 1; number <= 30; number++ {
		fmt.Fprintf(&rules, "%d. rule\n", number)
	}
	big := &progressDoc{
		program: "release-v3",
		plan:    "/Users/someone/.claude/plans/" + strings.Repeat("x", 120) + ".md",
		at:      base,
		reg:     &register{id: "7654321000000000000000000000000000000000", body: rules.String()},
		asks:    make([]string, 40),
		tasks:   make([]sessionTask, 200),
	}
	for number := range 80 {
		big.standing.rules = append(big.standing.rules, standingRule{id: fmt.Sprintf("#%d", number), text: strings.Repeat("rule text ", 60)})
	}

	text := big.digest("the progress doc `ccn doc show 1234567`")

	if len(text) > budget {
		t.Fatalf("digest is %d bytes", len(text))
	}
	if !strings.HasPrefix(text, "Compacted long-running drive `release-v3`. Before acting, read the standing rules register `ccn doc show 7654321`") {
		t.Fatalf("digest %q", text)
	}
	lines := splitLines(text)
	equal(t, lines[len(lines)-2], "Register: 30 owner-approved rules, 80 live standing rules.")
	if !strings.HasPrefix(lines[len(lines)-1], "Open: 40 owner asks, 200 tasks") {
		t.Fatalf("line %q", lines[len(lines)-1])
	}
}

func TestLintFlagsThePlansUncitedOwnerGateLines(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	d := depsFor(f)
	out := runGenerate(t, d, home.options())

	code, err := d.lint(context.Background(), lintOptions{doc: str(out.ID), program: "brook", plan: home.plan, repo: testRepo})
	if err != nil {
		t.Fatal(err)
	}

	equal(t, code, violations)
	printed := f.stdout.String()
	contains(t, printed, "plan owner-gate line cites no live answer: brook.md:3: - Acme release on the owner's word")
	lacks(t, printed, "missing durable rule")
}

func TestLintReadsTheRegisterFromADocOrAFile(t *testing.T) {
	home := newHome(t)
	f := newFake("", false)
	f.registerLabel = "standing-rules:v3"
	f.addRegister(testReg.id, testReg.body)
	f.docs[docA] = "## Standing owner rules\n- none\n"
	d := depsFor(f)

	code, err := d.lint(context.Background(), lintOptions{doc: docA, program: "v3", repo: testRepo})
	if err != nil {
		t.Fatal(err)
	}
	equal(t, code, violations)
	equal(t, f.stdout.String(), unquoted+"\n")
	f.stdout.Reset()

	file := filepath.Join(home.root, "handoff.md")
	writeFile(t, file, carriedDoc)
	code, err = d.lint(context.Background(), lintOptions{file: file, program: "v3", repo: testRepo})
	if err != nil {
		t.Fatal(err)
	}
	equal(t, code, 0)
}

func TestStrictRefusesANarrativeWithAnUncitedOwnerGate(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	f.write(docB, "## The coordinator's next actions\n1. ship sanddb on Acme on the owner's word", "brook: progress")

	printed := refused(t, depsFor(f), home.options(strictMode, noSession), violations)

	contains(t, printed, "owner-gate line cites no live answer id: 1. ship sanddb on Acme on the owner's word")
	equal(t, hasDocCall(f, "add"), false)
	if _, err := os.Stat(progressFolder(home.plan)); !os.IsNotExist(err) {
		t.Fatalf("the progress folder exists: %v", err)
	}
}

func TestARuleTheSourcesDroppedIsCarriedOnceAsSuperseded(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	d := depsFor(f)
	first := runGenerate(t, d, home.options())
	f.rule(correctionKind, "deploy every landing within five minutes", 2, "4ffc9a5")

	second := runGenerate(t, d, home.options())
	body := f.docs[str(second.ID)]
	third := runGenerate(t, d, home.options())

	contains(t, body, "- #2 superseded by #3", "- #3 [ccn 4ffc9a5]", "## Lint findings\n- plan owner-gate line")
	lines, _ := section(f.docs[str(third.ID)])
	lacks(t, strings.Join(lines, "\n"), "superseded by")
	equal(t, str(first.ID), str(second.ID))
	equal(t, str(second.ID), str(third.ID))
}

func TestStrictNamesAnUncitedStandingRuleUntilACitedRecordReplacesIt(t *testing.T) {
	for name, fix := range map[string]struct {
		kind string
		text string
		re   int64
	}{
		"a correction": {correctionKind, "no release waits on the owner's word (answer 4ffc9a5)", 3},
		"a new go":     {goKind, "every release ships on the owner's word only once (answer 4ffc9a5)", 0},
	} {
		t.Run(name, func(t *testing.T) {
			home := newHome(t)
			f := newFake("", true)
			f.write(docB, "## The coordinator's next actions\n1. land l11", "brook: progress")
			f.rule(goKind, "every release ships on the owner's word only once", 0, "")
			d := depsFor(f)

			finding := strings.TrimSpace(refused(t, d, home.options(strictMode, noSession), violations))

			if !strings.HasPrefix(finding, "cci #3: standing rule #3 requires owner approval but cites no live answer id: ") {
				t.Fatalf("finding %q", finding)
			}
			contains(t, finding, "--kind correction --re-seq 3 --topic standing")
			lacks(t, finding, "ccn doc edit")

			if fix.kind == goKind {
				f.standing = f.standing[:len(f.standing)-1]
			}
			f.rule(fix.kind, fix.text, fix.re, "")

			runGenerate(t, d, home.options(strictMode, noSession))
		})
	}
}

func TestStrictNamesTheNarrativeLineAndItsEdit(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	f.write(docB, "## The coordinator's next actions\n1. land l11\n2. ship sanddb on Acme on the owner's word", "brook: progress")

	printed := refused(t, depsFor(f), home.options(strictMode, noSession), violations)

	equal(t, strings.TrimSpace(printed), "narrative (doc bbbbbbb) line 3: owner-gate line cites no live answer id: 2. ship sanddb on Acme on the owner's word; end that line with `(answer <id>)` via `ccn doc edit bbbbbbbb --body -`")
}

func TestStrictLintsOnlyTheNewestHandWrittenRecordNeverTheOnesItSupersedes(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	docD := strings.Repeat("d", 40)
	f.write(docB, "## The coordinator's next actions\n1. ship sanddb on Acme on the owner's word", "brook: progress", docA)
	f.write(docD, "## The coordinator's next actions\n1. land l11", "brook: progress", docB)

	out := runGenerate(t, depsFor(f), home.options(strictMode))

	equal(t, str(out.Narrative), "ddddddd")
	equal(t, out.ReadFirst, []string{"ccn doc show bbbbbbb"})
}

func TestTheHandoffNamesTheRegisterDocAndNeverWritesIt(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	older := strings.Repeat("e", 40)
	f.registers = append([]string{older}, f.registers...)
	f.docs[older] = "# an older draft\n"
	f.order = append(f.order, older)
	f.updated[older] = "2026-09-30T00:00:00Z"
	d := depsFor(f)

	first := runGenerate(t, d, home.options())
	beforeBody := f.docs[str(first.ID)]
	second := runGenerate(t, d, home.options())

	equal(t, str(first.Register), registerDoc)
	equal(t, str(second.Register), registerDoc)
	for _, call := range f.calls {
		if len(call) > 4 && call[3] == "doc" && slices.Contains([]string{"add", "edit", "supersede"}, call[4]) && slices.Contains(call, registerDoc) {
			t.Fatalf("the register was written: %v", call)
		}
	}
	equal(t, f.docs[registerDoc], registerBody)
	lines, _ := section(f.docs[str(second.ID)])
	rendered := strings.Join(lines, "\n")
	reg := &register{id: registerDoc, body: registerBody}
	contains(t, rendered, pointer(reg))
	lacks(t, f.docs[str(second.ID)], splitLines(registerBody)[2])
	lacks(t, rendered, "an older draft")
	problems := lintBody(lintView(f.docs[str(second.ID)]), beforeBody, reg, []string{"4ffc9a5000000000000000000000000000000000"}, nil)
	if len(problems) != 0 {
		t.Fatalf("problems %v", problems)
	}
}

func TestAHandWrittenNarrativeIsCarriedVerbatimNeverFolded(t *testing.T) {
	home := newHome(t)
	var dump strings.Builder
	dump.WriteString("## 10:17 PM dump 2\n\n### Owner rulings since 9 PM (verbatim, binding)\n- \"ship it\"\n\n### Program state\nCensus 231/293. Lanes a, b.\n\n## 11:57 PM dump 3\n\n### Program state\nCensus 240/293.\n")
	for number := range 2000 {
		fmt.Fprintf(&dump, "- lane %d: still running\n", number)
	}
	narrative := dump.String()
	f := newFake("", true)
	f.write(docB, narrative, "brook: progress")
	d := depsFor(f)

	first := runGenerate(t, d, home.options())
	second := runGenerate(t, d, home.options())

	equal(t, str(first.ID), docB)
	equal(t, str(second.ID), docB)
	if len(narrative) <= progressCap {
		t.Fatalf("the narrative is only %d bytes", len(narrative))
	}
	equal(t, after(f.docs[docB], "\n## Root narrative\n"), "\n_From doc bbbbbbb, carried forward._\n\n"+strings.TrimSpace(narrative)+"\n")
}

func TestGenerateRefusesGeneratedSectionsOverTheCapAndWritesNothing(t *testing.T) {
	home := newHome(t)
	tasks := make([]sessionTask, 0, 500)
	for number := range 500 {
		tasks = append(tasks, sessionTask{ID: fmt.Sprint(number), Status: inProgress, Subject: strings.Repeat("x", 200)})
	}
	home.writeSession(t, tasks, nil)
	f := newFake("", true)

	printed := refused(t, depsFor(f), home.options(), tooLarge)

	contains(t, printed, "its largest section is `Open tasks` at ")
	equal(t, hasDocCall(f, "add"), false)
	equal(t, hasDocCall(f, "edit"), false)
	if _, err := os.Stat(progressFolder(home.plan)); !os.IsNotExist(err) {
		t.Fatalf("the progress folder exists: %v", err)
	}
}

func TestAnOversizedAskIsLeftOutWithItsCommandAndTheHandoffStillWrites(t *testing.T) {
	home := newHome(t)
	f := newFake("", true, askRow("000001", map[string]string{"text": strings.Repeat("x", progressCap)}))

	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	contains(t, body, "## Open owner asks\n- 1 more: run `ledger.py show --drive brook --asks`\n")
}

func TestTheHandWrittenHandoffAndItsAddendumSurviveARegeneratingCompaction(t *testing.T) {
	fullHandoff := "# Drive brook: pre-compact handoff, 2026-10-10 ~22:10Z (3:10pm PT)\n\nThis supersedes progress doc aaaaaaa.\n\n## 2. Live lanes\n\n- oncall-ic-dual-3 (124)\n"
	addendum := "# Addendum to progress doc bbbbbbb (everything after 22:10Z; updated ~22:20Z)\n\n## Next actions for the coordinator\n\n1. Keep the freeze until the release-v3 drive or the owner lifts it.\n"
	doc9 := strings.Repeat("9", 40)
	for name, supersedes := range map[string][]string{"read alongside": nil, "superseding": {docB}} {
		t.Run(name, func(t *testing.T) {
			home := newHome(t)
			f := newFake("", true)
			f.write(docB, fullHandoff, "brook: progress 2026-10-10T2210Z (pre-compact handoff, full)", docA)
			f.write(doc9, addendum, "brook: progress 2026-10-10T2225Z addendum (read with bbbbbbb)", supersedes...)
			d := depsFor(f)

			stop := runGenerate(t, d, home.options())
			compaction := runGenerate(t, d, home.options())

			equal(t, str(stop.ID), doc9)
			equal(t, str(compaction.ID), doc9)
			equal(t, f.active, []string{doc9})
			equal(t, len(f.added), 0)
			equal(t, f.docs[docB], fullHandoff)
			equal(t, f.titles[doc9], "brook: progress 2026-10-10T2225Z addendum (read with bbbbbbb)")
			body := f.docs[doc9]
			contains(t, body, readFirstPrefix+"`ccn doc show bbbbbbb`: brook: progress 2026-10-10T2210Z (pre-compact handoff, full)\n")
			equal(t, after(body, "\n## Root narrative\n"), "\n_From doc 9999999, carried forward._\n\n"+strings.TrimSpace(addendum)+"\n")
			for _, out := range []generateOutput{stop, compaction} {
				equal(t, out.ReadFirst, []string{"ccn doc show bbbbbbb"})
				equal(t, str(out.Narrative), "9999999")
				contains(t, out.Digest, "Then read the hand-written handoff `ccn doc show bbbbbbb`, then the progress doc `ccn doc show 9999999`, then ")
			}
		})
	}
}

func TestGenerateReadsTheWholeProgressLineageInOneListingWhateverItsLength(t *testing.T) {
	counts := map[int]int{}
	for _, length := range []int{1, 6} {
		home := newHome(t)
		f := newFake("", true)
		chain := []string{}
		older := docA
		for number := 1; number <= length; number++ {
			doc := strings.Repeat(fmt.Sprint(number), 40)
			f.write(doc, "## The coordinator's next actions\n1. land "+doc[:7], "brook: progress", older)
			chain = append(chain, doc)
			older = doc
		}

		out := runGenerate(t, depsFor(f), home.options())

		equal(t, str(out.ID), chain[len(chain)-1])
		wantFirst := make([]string, 0, length)
		for _, doc := range chain[:len(chain)-1] {
			wantFirst = append(wantFirst, "ccn doc show "+doc[:7])
		}
		equal(t, out.ReadFirst, wantFirst)
		full := [][]string{}
		for _, call := range f.calls {
			if len(call) > 4 && call[3] == "doc" && call[4] == "list" && slices.Contains(call, "--full") {
				full = append(full, call)
			}
		}
		equal(t, full, [][]string{
			{"ccn", "-R", testRepo, "doc", "list", "--label", registerTag, "--full", "--json"},
			{"ccn", "-R", testRepo, "doc", "list", "--label", "progress:brook", "--include-superseded", "--full", "--json"},
		})
		equal(t, hasDocCall(f, "show"), false)
		for _, call := range f.calls {
			if len(call) > 3 && call[3] == "doc" {
				counts[length]++
			}
		}
	}
	equal(t, counts[1], counts[6])
}

type storedDoc struct {
	id      string
	label   string
	by      []string
	deleted bool
	made    bool
	updated string
}

const progressLabel = "progress:brook"

func storedOf(key string, changes ...func(*storedDoc)) storedDoc {
	doc := storedDoc{id: strings.Repeat(key, 40), label: progressLabel, updated: "2026-10-01T00:00:00Z"}
	for _, change := range changes {
		change(&doc)
	}
	return doc
}

func by(keys ...string) func(*storedDoc) {
	return func(doc *storedDoc) {
		for _, key := range keys {
			doc.by = append(doc.by, strings.Repeat(key, 40))
		}
	}
}

func labelled(label string) func(*storedDoc) { return func(doc *storedDoc) { doc.label = label } }
func made(doc *storedDoc)                    { doc.made = true }
func deleted(doc *storedDoc)                 { doc.deleted = true }

func (s storedDoc) body() string {
	narrative := "## The coordinator's next actions\n1. " + s.id[:1]
	if s.made {
		return generatedRecord(narrative)
	}
	return narrative
}

func defaultOrder(store []storedDoc) []storedDoc {
	ordered := slices.Clone(store)
	slices.SortStableFunc(ordered, func(a, b storedDoc) int {
		return cmp.Or(cmp.Compare(b.updated, a.updated), cmp.Compare(a.id, b.id))
	})
	return ordered
}

func showWalk(store []storedDoc) []string {
	live := map[string]storedDoc{}
	for _, doc := range store {
		if !doc.deleted {
			live[doc.id] = doc
		}
	}
	queue := []string{}
	for _, doc := range defaultOrder(store) {
		if !doc.deleted && doc.label == progressLabel && len(doc.by) == 0 {
			queue = append(queue, doc.id)
		}
	}
	seen := map[string]bool{}
	walked := []string{}
	for len(queue) > 0 {
		id := queue[len(queue)-1]
		queue = queue[:len(queue)-1]
		doc, known := live[id]
		if seen[id] || !known {
			seen[id] = true
			continue
		}
		seen[id] = true
		if doc.label != progressLabel {
			continue
		}
		walked = append(walked, id)
		if !isGenerated(doc.body()) {
			older := []string{}
			for _, other := range live {
				if slices.Contains(other.by, id) {
					older = append(older, other.id)
				}
			}
			slices.Sort(older)
			queue = append(queue, older...)
		}
	}
	return walked
}

func fullListing(store []storedDoc) ([]noteDoc, []noteDoc) {
	all := []noteDoc{}
	for _, doc := range defaultOrder(store) {
		if !doc.deleted && doc.label == progressLabel {
			all = append(all, noteDoc{ID: doc.id, Body: doc.body(), UpdatedAt: doc.updated, CreatedAt: doc.updated, SupersededBy: doc.by})
		}
	}
	active := slices.DeleteFunc(slices.Clone(all), func(doc noteDoc) bool { return len(doc.SupersededBy) > 0 })
	return all, active
}

func walkIDs(store []storedDoc) []string {
	all, active := fullListing(store)
	walked := lineage(all, active)
	ids := make([]string, 0, len(walked))
	for _, doc := range walked {
		ids = append(ids, doc.ID)
	}
	return ids
}

func randomStore(seed int) []storedDoc {
	state := seed*7919 + 17
	next := func(limit int) int {
		state = (state*1103515245 + 12345) & 0x7fffffff
		return (state >> 8) % limit
	}
	count := 1 + next(9)
	stamps := []string{"2026-10-01T00:00:00Z", "2026-10-01T01:00:00Z", "2026-10-01T02:00:00Z"}
	store := make([]storedDoc, 0, count)
	for number := range count {
		key := fmt.Sprintf("%x", number)
		doc := storedOf(key)
		pool := []string{"f"}
		for other := range count {
			if other != number {
				pool = append(pool, fmt.Sprintf("%x", other))
			}
		}
		for range next(3) {
			at := next(len(pool))
			doc.by = append(doc.by, strings.Repeat(pool[at], 40))
			pool = slices.Delete(pool, at, at+1)
			if len(pool) == 0 {
				break
			}
		}
		if next(3) == 0 {
			doc.label = "progress:other"
		}
		doc.deleted = next(7) == 0
		doc.made = next(10) < 3
		doc.updated = stamps[next(len(stamps))]
		store = append(store, doc)
	}
	return store
}

func lineageCases() map[string][]storedDoc {
	return map[string][]storedDoc{
		"predecessor":      {storedOf("a", by("b")), storedOf("b")},
		"cross-label-stop": {storedOf("a", by("b")), storedOf("b", by("c"), labelled("progress:other")), storedOf("c")},
		"generated-stop":   {storedOf("a", by("b")), storedOf("b", by("c"), made), storedOf("c")},
		"inactive-through": {storedOf("a", by("b")), storedOf("b", labelled("progress:other")), storedOf("d", by("f")), storedOf("e")},
		"tombstoned":       {storedOf("a", by("b"), deleted), storedOf("b")},
		"equal-timestamps": {storedOf("a"), storedOf("b")},
		"reconverging":     {storedOf("a", by("b", "c")), storedOf("b", by("c")), storedOf("c")},
		"cycle":            {storedOf("a", by("b")), storedOf("b", by("a")), storedOf("c")},
	}
}

func TestTheBatchLineageWalksExactlyWhatADocShowPerRecordWalked(t *testing.T) {
	for name, store := range lineageCases() {
		t.Run(name, func(t *testing.T) {
			equal(t, walkIDs(store), showWalk(store))
		})
	}
	for seed := range 300 {
		store := randomStore(seed)
		t.Run(fmt.Sprintf("random-%d", seed), func(t *testing.T) {
			equal(t, walkIDs(store), showWalk(store))
		})
	}
}

func TestTheBatchLineageKeepsTheCasesTheContractNames(t *testing.T) {
	cases := lineageCases()
	for name, want := range map[string][]string{
		"predecessor":      {"b", "a"},
		"cross-label-stop": {"c"},
		"generated-stop":   {"c", "b"},
		"inactive-through": {"e"},
		"tombstoned":       {"b"},
		"equal-timestamps": {"b", "a"},
		"reconverging":     {"c", "b", "a"},
		"cycle":            {"c"},
	} {
		t.Run(name, func(t *testing.T) {
			ids := walkIDs(cases[name])
			got := make([]string, 0, len(ids))
			for _, id := range ids {
				got = append(got, id[:1])
			}
			equal(t, got, want)
		})
	}
}

func TestOpenTasksListOnlyInProgressAndLanesOnlyRunningTen(t *testing.T) {
	home := newHome(t)
	tasks := make([]sessionTask, 0, 13)
	for number := range 13 {
		status := "pending"
		if number == 12 {
			status = inProgress
		}
		tasks = append(tasks, sessionTask{ID: fmt.Sprint(number), Status: status, Subject: fmt.Sprintf("task %d", number)})
	}
	background := make([]backgroundTask, 0, 15)
	for number := range 14 {
		background = append(background, backgroundTask{Type: "teammate", Status: running, Description: fmt.Sprintf("lane-%d", number)})
	}
	background = append(background, backgroundTask{Type: "teammate", Status: "completed", Description: "lane-done"})
	home.writeSession(t, tasks, background)
	f := newFake("", true)

	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	equal(t, splitLines(before(after(body, "## Open tasks\n"), "\n\n")), []string{"- #12 task 12", "- 12 pending in `TaskList`"})
	lanes := splitLines(before(after(body, "## Lanes and monitors\n"), "\n\n"))
	equal(t, lanes[0], "- teammate: lane-13")
	equal(t, lanes[len(lanes)-1], "- 4 more running lanes")
	equal(t, len(lanes), 11)
	lacks(t, body, "lane-done")
}

func TestAStandingRuleIsNamedByIDAndItsTextStaysInCci(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)

	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	lines, _ := section(body)
	if !strings.HasSuffix(strings.Join(lines, "\n"), "- #2 [cci #2]") {
		t.Fatalf("section %v", lines)
	}
	lacks(t, body, "every landed PR is deployed")
}

func TestANarrativeTheRootEditsIntoTheGeneratedDocIsFreshOnceAndStaysVerbatim(t *testing.T) {
	for name, above := range map[string]bool{"above": true, "below": false} {
		t.Run(name, func(t *testing.T) {
			home := newHome(t)
			f := newFake("## 10:00 PM dump 1\n\n### Program state\nCensus 200/293. Lanes a, b.\n", true)
			d := depsFor(f)
			first := runGenerate(t, d, home.options())
			doc := str(first.ID)
			head, carriedPart, _ := strings.Cut(f.docs[doc], narrativeHeading+"\n\n")
			carriedPart = provenance.ReplaceAllString(carriedPart, "")
			var dump, edited string
			if above {
				dump = "Pre-compact handoff written by main at 3:10 PM. Read all of it.\n\n### A. Current state\nReleases are paused.\n"
				edited = head + narrativeHeading + "\n\n" + dump + "\n" + carriedPart
			} else {
				dump = "## 3:10 PM dump 2\n\n### Current state\nReleases are paused.\n"
				edited = head + narrativeHeading + "\n\n" + carriedPart + "\n" + dump
			}
			if _, err := f.Run(context.Background(), shell.Command{Argv: []string{"ccn", "-R", testRepo, "doc", "edit", doc, "--body", "-"}, Stdin: edited}); err != nil {
				t.Fatal(err)
			}

			second := runGenerate(t, d, home.options())
			editedNarrative := narrativeOf(f.docs[doc])
			third := runGenerate(t, d, home.options())

			equal(t, []bool{first.Fresh, second.Fresh, third.Fresh}, []bool{false, true, false})
			equal(t, str(second.ID), doc)
			equal(t, str(third.ID), doc)
			narrative := after(f.docs[doc], narrativeHeading+"\n")
			if !strings.HasPrefix(narrative, "\n_From doc "+doc[:7]+", carried forward._\n\n") {
				t.Fatalf("narrative:\n%s", narrative)
			}
			equal(t, narrative, "\n_From doc "+doc[:7]+", carried forward._\n\n"+editedNarrative+"\n")
			equal(t, strings.Count(narrative, strings.Trim(dump, "\n")), 1)
			contains(t, narrative, "\n## 10:00 PM dump 1")
			lacks(t, narrative, "## Folded narrative")
		})
	}
}

func TestOpenTasksEndWithTheDrivePlansDoneAndOpenSteps(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	f.notes = &planstest.Fake{
		Tasks: []planstest.Fields{
			planstest.Task("a", "Ship the reader", planstest.Fields{"plan": planstest.PlanID, "labels": []string{"step:1"}, "status": "done"}),
			planstest.Task("b", "Drop the column", planstest.Fields{"plan": planstest.PlanID, "labels": []string{"step:2"}}),
		},
		Plans: []planstest.Fields{planstest.Plan(planstest.PlanID, "Migration")},
	}

	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	equal(t, splitLines(before(after(body, "## Open tasks\n"), "\n\n")), []string{"- none in progress", "- 1 pending in `TaskList`", "- Plan `fc8714e` Migration: 1 of 2 tasks done, 0 blocked; steps open: 2 0/1"})
}

func laneRecord(seq int64, lane, kind, text string, minutes int) busRecord {
	return busRecord{Seq: seq, Drive: "brook", Lane: lane, Kind: kind, Text: text, At: base.Add(-time.Duration(minutes) * time.Minute)}
}

func TestTheHandoffListsEachIncidentLaneWithItsLastRecordAndAge(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)
	f.laneRecords = []busRecord{
		laneRecord(9, "inc-old-fix", "mechanism", "older than the window", 60*7),
		laneRecord(10, "inc-liam-load-fix", "mechanism", "the fork reads the parent", 30),
		laneRecord(11, "inc-liam-load-fix", "opened", "opened #41461", 20),
		laneRecord(12, "inc-liam-load-opus", "mechanism", "SAND-59 is a fork", 14),
		laneRecord(13, "enum-local-client", "mechanism", "the set is proven", 12),
		laneRecord(14, "inc-chats-load-fix", "report", "CANDIDATE the sidecar drops the lease", 9),
		laneRecord(15, "inc-chats-load-fix", "state", "still reading the sidecar log", 3),
	}

	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	contains(t, body, "## Incident lanes\n"+
		"- `inc-chats-load-fix`: last state 3 min ago: still reading the sidecar log | CANDIDATE 9 min ago, no OPENED or BLOCKER since\n"+
		"- `inc-liam-load-opus`: last mechanism 14 min ago: SAND-59 is a fork | MECHANISM 14 min ago, no OPENED or BLOCKER since\n"+
		"- `inc-liam-load-fix`: last opened 20 min ago: opened #41461\n")
	lacks(t, body, "enum-local-client", "inc-old-fix")
}

func TestTheHandoffCarriesTheLaneRegistryAndTheOwnerUpdateRenderedFromSources(t *testing.T) {
	home := newHome(t)
	writeFile(t, filepath.Join(home.root, "state", "brook", "lr.json"), "{}")
	f := newFake("", true)
	f.laneJSON = `{"lane":"tooling-codify-1","state":"desired","heard":null,"step":"tooling","standing":"","brief":"/b/codify.md","prs":[1904]}
{"lane":"board-refresher-7","state":"retired","heard":null,"step":"all","standing":"refresh the board","brief":"/b/board.md","prs":[]}
{"lane":"old-lane-1","state":"retired","heard":null,"step":"","standing":"","brief":"/b/old.md","prs":[]}
`
	f.ownerReport = "# Owner update: d1, 6:00 PM\n\n## Standing duties\n- refresh the board: NO LIVE OWNER\n"

	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	contains(t, body,
		"## Lane registry\n- tooling-codify desired, heard an unrecorded time, step tooling, PRs #1904\n- board-refresher retired, heard an unrecorded time, step -, standing: refresh the board\n",
		"## Owner update\n\n### Standing duties\n- refresh the board: NO LIVE OWNER\n",
	)
	lacks(t, body, "old-lane")
}

func TestACoordinatorOutsideTheSessionListReadsItsDriveByName(t *testing.T) {
	home := newHome(t)
	home.writeDrive(t, map[string]any{"sessions": []string{"s-communicator"}, "cci_drive": "bus-1", "state_dir": filepath.Join(home.root, "state", "brook")})
	writeFile(t, filepath.Join(home.root, "state", "brook", "lr.json"), "{}")
	f := newFake("", true)
	f.laneJSON = `{"lane":"tooling-codify-1","state":"desired","heard":null,"step":"tooling","standing":"","prs":[]}
`
	f.ownerReport = "# Owner update: d1, 6:00 PM\n\n## Standing duties\n- none\n"

	lacks(t, f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)], "tooling-codify")

	t.Setenv(driveEnv, "d1")
	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	contains(t, body, "## Lane registry\n- tooling-codify desired", "### Standing duties\n- none")
	called := false
	for _, call := range f.calls {
		called = called || slices.Equal(call, []string{"cci", "tail", "bus-1"})
	}
	if !called {
		t.Fatal(f.calls)
	}
}

func TestAHandoffOnAWideDriveStaysUnderTheCapAndNamesWhatItLeftOut(t *testing.T) {
	home := newHome(t)
	writeFile(t, filepath.Join(home.root, "state", "brook", "lr.json"), "{}")
	f := newFake("", true)
	var lanes, update strings.Builder
	update.WriteString("# Owner update: d1, 6:00 PM\n\n")
	for i := range 2_000 {
		fmt.Fprintf(&lanes, `{"lane":"lane-%d","state":"desired","heard":null,"step":"all","standing":"","prs":[%d]}`+"\n", i, i)
		fmt.Fprintf(&update, "- row %d: what it changes, who owns it and what blocks it\n", i)
	}
	f.laneJSON = lanes.String()
	f.ownerReport = update.String()

	out := runGenerate(t, depsFor(f), home.options())
	body := f.docs[str(out.ID)]

	head, _, _ := strings.Cut(body, narrativeMarker)
	if len(head) > progressCap {
		t.Fatalf("%d bytes", len(head))
	}
	contains(t, body, "more: run `lr lanes --config ", "more: run `lr update --drive brook --render`")
}

func TestWithinBudgetKeepsWholeLinesAndCountsTheRest(t *testing.T) {
	equal(t, strings.Join(withinBudget([]string{"aaaa", "bbbb", "cccc", "dddd"}, 10, "whole"), "|"), "aaaa|bbbb|- 2 more: run `whole`")
	equal(t, strings.Join(withinBudget([]string{"aaaa", "bbbb"}, 10, "whole"), "|"), "aaaa|bbbb")
}

func TestAnUnreadRegistryIsNamedAndTheHandoffStillWrites(t *testing.T) {
	home := newHome(t)
	writeFile(t, filepath.Join(home.root, "state", "brook", "lr.json"), "{}")
	f := newFake("", true)
	f.laneJSON = "not json\n"

	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	contains(t, body, "## Lane registry\n- registry unread: ", "## Owner update\n- no lane registry\n")
}

func TestEachOpenTaskWhoseLaneDeliveredSinceTheLastProgressRecordIsListedWithThatRecord(t *testing.T) {
	home := newHome(t)
	home.writeSession(t, []sessionTask{
		{ID: "11", Status: inProgress, Subject: "Perf worker scenarios", Owner: "devbox-explore-7"},
		{ID: "12", Status: inProgress, Subject: "Quiet work", Owner: "quiet-lane-1"},
		{ID: "13", Status: "pending", Subject: "Board rows", Owner: "board-refresher-6"},
	}, nil)
	f := newFake("", true)
	f.updated[docA] = base.Add(-30 * time.Minute).Format(secondsStamp)
	f.laneRecords = []busRecord{
		laneRecord(20, "devbox-explore-7", "report", "scenario 3 measured", 10),
		laneRecord(21, "devbox-explore-7", "state", "measuring", 5),
		laneRecord(22, "quiet-lane-1", "report", "before the last progress record", 45),
		laneRecord(23, "board-refresher-6", "head", "rows at the new head", 8),
		laneRecord(24, "stranger-1", "report", "owns no open task", 8),
	}

	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	listed := splitLines(before(after(body, deliveredHeading+"\n"), "\n\n"))
	heads := make([]string, 0, len(listed))
	for _, line := range listed {
		heads = append(heads, before(line, " | "))
	}
	equal(t, heads, []string{"- #11 devbox-explore-7: Perf worker scenarios", "- #13 board-refresher-6: Board rows"})
	if !strings.HasPrefix(listed[0], "- #11 devbox-explore-7: Perf worker scenarios | report #20 ") || !strings.HasSuffix(listed[0], ": scenario 3 measured") {
		t.Fatalf("line %q", listed[0])
	}
}

func TestADriveWithNoDeliverySaysNone(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)

	contains(t, f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)], deliveredHeading+"\n- none\n")
}

func TestADriveWithNoIncidentLaneSaysSo(t *testing.T) {
	home := newHome(t)
	f := newFake("", true)

	contains(t, f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)], "## Incident lanes\n- none posted in the last 6 h\n")
}

func (h driveHome) inbox(name string) string {
	return filepath.Join(h.root, ".claude", "scratch", "brook", "inbox", name)
}

func age(t *testing.T, path string, at time.Time) {
	t.Helper()
	if err := os.Chtimes(path, at, at); err != nil {
		t.Fatal(err)
	}
}

func TestGenerateClearsACursorFromBeforeTheDriveThatNoLineSitsPastAndKeepsOneWithALinePastIt(t *testing.T) {
	home := newHome(t)
	home.writeDrive(t, map[string]any{"started_at": "2026-10-20T00:00:00Z"})
	stale, behind := home.inbox("landing-desk.md.cursor"), home.inbox("orca-desk.md.cursor")
	writeFile(t, stale, "L1\n")
	written := time.Date(2026, 10, 1, 0, 0, 0, 0, time.UTC)
	age(t, stale, written)
	age(t, behind, written)
	f := newFake("", true)

	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	if _, err := os.Stat(stale); !os.IsNotExist(err) {
		t.Fatalf("the stale cursor remains: %v", err)
	}
	equal(t, readFile(t, behind), "R2\n")
	contains(t, body,
		"- orca-desk.md: head R3, cursor R2: - R3 (the coordinator) → desk: launch l02",
		"- landing-desk.md: head L1, cursor -: - L1 (the coordinator) → desk: hold #1",
		"- cleared stale cursor landing-desk.md.cursor at L1, written before the drive started at 2026-10-20T00:00:00Z",
	)
}

func TestGenerateKeepsAnOldCursorAtTheNewestRulingWhenALineFollowsIt(t *testing.T) {
	home := newHome(t)
	home.writeDrive(t, map[string]any{"started_at": "2026-10-20T00:00:00Z"})
	writeFile(t, home.inbox("landing-desk.md"), "- L1 (the coordinator) → desk: hold #1\nMONITOR alert fired: queue stalled\n")
	cursor := home.inbox("landing-desk.md.cursor")
	writeFile(t, cursor, "L1\n")
	age(t, cursor, time.Date(2026, 10, 1, 0, 0, 0, 0, time.UTC))
	f := newFake("", true)

	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	equal(t, readFile(t, cursor), "L1\n")
	lacks(t, body, "cleared stale cursor")
}

func TestGenerateKeepsAReadThroughCursorWrittenAfterTheDriveStarted(t *testing.T) {
	home := newHome(t)
	home.writeDrive(t, map[string]any{"started_at": "2026-10-20T00:00:00Z"})
	cursor := home.inbox("landing-desk.md.cursor")
	writeFile(t, cursor, "L1\n")
	age(t, cursor, time.Date(2026, 10, 25, 0, 0, 0, 0, time.UTC))
	f := newFake("", true)

	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	equal(t, readFile(t, cursor), "L1\n")
	lacks(t, body, "cleared stale cursor")
}

func TestAPartialInboxLineAndArchivedLinesCount(t *testing.T) {
	home := newHome(t)
	writeFile(t, home.inbox("landing-desk.md.archive/0001.md"), "- L0 (the coordinator) → desk: older\n")
	writeFile(t, home.inbox("landing-desk.md"), "- L1 (the coordinator) → desk: hold #1\n- L2 half a line")
	writeFile(t, home.inbox("landing-desk.md.cursor"), "2\n")
	doc := &progressDoc{}

	if err := readInboxes(doc, filepath.Dir(home.inbox("landing-desk.md"))); err != nil {
		t.Fatal(err)
	}

	landing := doc.inboxes[slices.IndexFunc(doc.inboxes, func(item inbox) bool { return item.name == "landing-desk.md" })]
	equal(t, landing.count, 2)
	equal(t, landing.head, "L1")
	equal(t, landing.readThrough(), true)
}

func scopeRowsJSON(t *testing.T, count int) string {
	t.Helper()
	var rows strings.Builder
	for number := range count {
		items := make([]implication, 0, 5)
		for at := 1; at <= 5; at++ {
			items = append(items, implication{N: at, Text: fmt.Sprintf("surface %d.%d shows no syncing state %s", number, at, strings.Repeat("x", 80)), Direction: "removes", State: "proposed"})
		}
		fields := map[string]string{
			"kind": "scope", "status": inForce, "words": fmt.Sprintf("the app shows no syncing state, and the downloading bar is the one transfer state, row %d", number),
			"answer": encoded(t, []string{fmt.Sprintf("%07x", number)}), "said": "1", "implications": encoded(t, items),
			"names": encoded(t, []string{fmt.Sprintf("syncing state %d", number), "Syncing"}), "updated_at": "2026-10-01T00:00:00Z", "evidence": "seed",
		}
		if number > 0 {
			rows.WriteString(",")
		}
		fmt.Fprintf(&rows, "%q:%s", fmt.Sprintf("rule/%07x", number), encoded(t, fields))
	}
	return `{"ledger":"0d4cfee","program":"brook","rows":{` + rows.String() + `},"classified":[]}`
}

func TestGeneratePrintsEveryScopeRowInTheOwnersWordsPastTheSizeCap(t *testing.T) {
	home := newHome(t)
	f := newFake("", false)
	f.rowsJSON = scopeRowsJSON(t, 400)

	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	if head := before(body, narrativeMarker); len(head) <= progressCap {
		t.Fatalf("the generated head is only %d bytes", len(head))
	}
	lacks(t, body, "no `standing-rules` register doc")
	for number := range 400 {
		contains(t, body, fmt.Sprintf("- `rule/%07x` (in-force, said 1): “the app shows no syncing state, and the downloading bar is the one transfer state, row %d”\n", number, number))
	}
	equal(t, len(f.blockers), 0)
	rows := depsFor(f).registerRows(context.Background(), testRepo, []string{"--program", "brook"}, "brook", base)
	if problems := lintBody(lintView(body), "", nil, nil, rows); len(problems) != 0 {
		t.Fatalf("problems %v", problems)
	}
}

func TestAFailedRegisterReadStillGeneratesAndPostsOneBlocker(t *testing.T) {
	home := newHome(t)
	f := newFake("", false)
	f.rowsErr = "rulings.py: error: no register ledger for brook\n"

	body := f.docs[str(runGenerate(t, depsFor(f), home.options()).ID)]

	contains(t, body, "## Standing owner rules\n\n- no `standing-rules` register doc\n\n- #2 [cci #2]\n\n- the register rows did not load (rulings.py: error: no register ledger for brook)")
	equal(t, len(f.blockers), 1)
	equal(t, f.blockers[0][0], "brook")
	if !strings.HasPrefix(f.blockers[0][1], "The register rows for brook did not load for the handoff: rulings.py: error: no register ledger for brook.") {
		t.Fatalf("blocker %q", f.blockers[0][1])
	}
	if !slices.ContainsFunc(f.calls, func(call []string) bool {
		return slices.Equal(call, []string{"rulings.py", "rows", "--drive", "d1", "--repo", testRepo, "--json"})
	}) {
		t.Fatalf("no rulings call in %v", f.calls)
	}
}

func TestLintFailsTheAff1d7d4HandoffAgainstTheRegister(t *testing.T) {
	home := newHome(t)
	words := "the app shows no syncing state, and the downloading bar is the one transfer state"
	f := newFake("", true)
	f.rowsJSON = `{"ledger":"0d4cfee","program":"brook","rows":{"rule/f958522":` + encoded(t, map[string]string{"kind": "scope", "status": inForce, "words": words, "answer": `["f958522"]`, "updated_at": "2026-10-01T00:00:00Z"}) + `},"classified":[]}`
	file := filepath.Join(home.root, "aff1d7d4.md")
	writeFile(t, file, aff1d7d4(t))

	code, err := depsFor(f).lint(context.Background(), lintOptions{file: file, program: "brook", repo: testRepo})
	if err != nil {
		t.Fatal(err)
	}

	equal(t, code, violations)
	printed := splitLines(f.stdout.String())
	for _, want := range []string{
		noDoc,
		"`f958522` cites without its words register row `rule/f958522`: - f958522: the app shows no syncing state." + verbatim(words),
	} {
		if !slices.Contains(printed, want) {
			t.Fatalf("%q is missing from %v", want, printed)
		}
	}
}

func TestRegisterRowsReadThroughRulingsAndAFailedReadPostsOneBlocker(t *testing.T) {
	posted := [][2]string{}
	withReply := func(configure func(*shelltest.Fake)) (*rowSet, []shell.Command) {
		fake := &shelltest.Fake{}
		configure(fake)
		d := &deps{runner: fake, blocker: func(_ context.Context, drive, _, text string) { posted = append(posted, [2]string{drive, text}) }}
		return d.registerRows(context.Background(), "/repo", []string{"--drive", "b7"}, "brook", base), fake.Calls()
	}

	rows, calls := withReply(func(f *shelltest.Fake) {
		f.Reply(`{"ledger":"0d4cfee","program":"brook","rows":{"rule/f958522":{"words":"the words"}}}`, "rulings.py", "rows")
	})
	equal(t, rows.failure, "")
	equal(t, rows.rows[0].key, "rule/f958522")
	equal(t, rows.rows[0].words, "the words")
	equal(t, calls[0].Argv, []string{"rulings.py", "rows", "--drive", "b7", "--repo", "/repo", "--json"})

	rows, _ = withReply(func(f *shelltest.Fake) { f.Reply(`{"ledger":null,"rows":{}}`, "rulings.py") })
	equal(t, rows == nil, true)
	rows, _ = withReply(func(f *shelltest.Fake) { f.Reply("null", "rulings.py") })
	equal(t, rows == nil, true)
	equal(t, len(posted), 0)

	rows, _ = withReply(func(f *shelltest.Fake) { f.Fail(2, "rulings.py: error: invalid choice: 'rows'\n", "rulings.py") })
	equal(t, rows.failure, "rulings.py: error: invalid choice: 'rows'")
	equal(t, posted, [][2]string{{
		"brook",
		"The register rows for brook did not load for the handoff: rulings.py: error: invalid choice: 'rows'. Read them with `rulings.py rows --drive b7 --json` and carry the scope rows' words by hand.",
	}})

	rows, _ = withReply(func(f *shelltest.Fake) { f.Reply("", "rulings.py") })
	equal(t, rows.failure, "printed no JSON")
	rows, _ = withReply(func(f *shelltest.Fake) { f.Fail(2, "", "rulings.py") })
	equal(t, rows.failure, "exit 2")
}

func TestTheStandingVerbListsEachLiveRuleWithItsSource(t *testing.T) {
	f := newFake("", false)
	f.rule(goKind, "dev is always releasable", 0, "87833ea")

	if err := depsFor(f).standing(context.Background(), "brook"); err != nil {
		t.Fatal(err)
	}

	equal(t, f.stdout.String(), "live standing: #2, #3\n#2 every landed PR is deployed in the same pass it lands [cci #2]\n#3 dev is always releasable [ccn 87833ea]\n")
}
