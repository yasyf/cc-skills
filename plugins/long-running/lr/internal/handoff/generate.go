package handoff

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"
)

const (
	violations    = 3
	severalActive = 4
	tooLarge      = 5
)

type noteDoc struct {
	ID           string   `json:"id"`
	Title        string   `json:"title"`
	Body         string   `json:"body"`
	CreatedAt    string   `json:"created_at"`
	UpdatedAt    string   `json:"updated_at"`
	SupersededBy []string `json:"superseded_by"`
}

func newestDoc(docs []noteDoc) noteDoc {
	newest := docs[0]
	for _, doc := range docs[1:] {
		if doc.UpdatedAt > newest.UpdatedAt {
			newest = doc
		}
	}
	return newest
}

type historyEntry struct {
	Changes []struct {
		Field string          `json:"field"`
		From  json.RawMessage `json:"from"`
		To    json.RawMessage `json:"to"`
	} `json:"changes"`
}

func generationOf(body string) string {
	if match := generationStamp.FindStringSubmatch(body); match != nil {
		return match[1]
	}
	return ""
}

func decodedBody(raw json.RawMessage) string {
	var body string
	if len(raw) > 0 && json.Unmarshal(raw, &body) != nil {
		return ""
	}
	return body
}

func (d *deps) editedInPlace(ctx context.Context, repo string, doc noteDoc) (bool, error) {
	var history []historyEntry
	if err := d.ccn(ctx, repo, &history, "doc", "history", doc.ID, "--full"); err != nil {
		return false, err
	}
	for _, entry := range history {
		for _, change := range entry.Changes {
			if change.Field != "body" {
				continue
			}
			to := decodedBody(change.To)
			if made := generationOf(to); made != "" && made != generationOf(decodedBody(change.From)) {
				return narrativeOf(to) != narrativeOf(doc.Body), nil
			}
		}
	}
	return false, nil
}

func lineage(all, active []noteDoc) []noteDoc {
	byID := make(map[string]noteDoc, len(all))
	supersedes := map[string][]string{}
	for _, doc := range all {
		byID[doc.ID] = doc
		for _, newer := range doc.SupersededBy {
			supersedes[newer] = append(supersedes[newer], doc.ID)
		}
	}
	queue := make([]string, 0, len(active))
	for _, doc := range active {
		queue = append(queue, doc.ID)
	}
	seen := map[string]bool{}
	walked := []noteDoc{}
	for len(queue) > 0 {
		id := queue[len(queue)-1]
		queue = queue[:len(queue)-1]
		if seen[id] {
			continue
		}
		seen[id] = true
		doc := byID[id]
		walked = append(walked, doc)
		if !isGenerated(doc.Body) {
			older := slices.Clone(supersedes[id])
			slices.Sort(older)
			queue = append(queue, older...)
		}
	}
	return walked
}

func docRecord(doc noteDoc) progressRecord {
	short := cut(doc.ID, shortID)
	return progressRecord{"doc " + short, "ccn doc show " + short, doc.Title, doc.Body, fmt.Sprintf("via `ccn doc edit %s --body -`", cut(doc.ID, 8))}
}

func fileRecord(path string) (progressRecord, error) {
	body, err := os.ReadFile(path)
	if err != nil {
		return progressRecord{}, err
	}
	name := filepath.Base(path)
	return progressRecord{"file " + name, path, name, string(body), fmt.Sprintf("in `%s`", path)}, nil
}

func stem(path string) string {
	base := filepath.Base(path)
	return strings.TrimSuffix(base, filepath.Ext(base))
}

func progressFolder(plan string) string {
	return filepath.Join(filepath.Dir(plan), stem(plan)+"-progress")
}

func folderRecords(plan string) ([]progressRecord, string, error) {
	paths, err := filepath.Glob(filepath.Join(progressFolder(plan), "*.md"))
	if err != nil {
		return nil, "", err
	}
	times := make(map[string]time.Time, len(paths))
	for _, path := range paths {
		info, err := os.Stat(path)
		if err != nil {
			return nil, "", err
		}
		times[path] = info.ModTime()
	}
	slices.SortStableFunc(paths, func(a, b string) int { return times[a].Compare(times[b]) })
	last := ""
	for _, path := range paths {
		if strings.HasSuffix(stem(path), generatedStem) {
			last = path
		}
	}
	records := []progressRecord{}
	for _, path := range paths {
		if strings.HasSuffix(stem(path), generatedStem) || (last != "" && !times[path].After(times[last])) {
			continue
		}
		record, err := fileRecord(path)
		if err != nil {
			return nil, "", err
		}
		records = append(records, record)
	}
	return records, last, nil
}

func uncitedPlanLines(plan string, live []string) ([]string, error) {
	data, err := os.ReadFile(plan)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var lines []string
	for at, line := range splitLines(string(data)) {
		if gated(line, live) {
			lines = append(lines, fmt.Sprintf("plan owner-gate line cites no live answer: %s:%d: %s", filepath.Base(plan), at+1, clip(line, rulingChars)))
		}
	}
	return lines, nil
}

func (d *progressDoc) check(previous string, live []string) error {
	d.retire(previous)
	d.findings = slices.Concat(ruleFindings(d.render(), previous, d.reg), d.uncitedRules(live), d.uncitedNarrative(live))
	found, err := uncitedPlanLines(d.plan, live)
	d.planFindings = found
	return err
}

type generateOptions struct {
	program  string
	plan     string
	inboxDir string
	ledger   string
	session  string
	repo     string
	strict   bool
	folder   bool
}

func expandUser(path string) (string, error) {
	if path != "~" && !strings.HasPrefix(path, "~/") {
		return path, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, strings.TrimPrefix(path, "~")), nil
}

func (d *deps) readSession(path string) (session, error) {
	var found session
	if path == "" {
		return found, nil
	}
	var data []byte
	var err error
	if path == "-" {
		data, err = io.ReadAll(d.stdin)
	} else {
		data, err = os.ReadFile(path)
	}
	if err != nil {
		return found, err
	}
	return found, json.Unmarshal(data, &found)
}

func (d *deps) newDoc(opts generateOptions, plan string, sess session) (*progressDoc, error) {
	doc := &progressDoc{program: opts.program, plan: plan, at: d.now().UTC(), retitle: true, tasks: sess.Tasks}
	for _, task := range sess.Background {
		if slices.Contains(laneTypes, task.Type) {
			doc.lanes = append(doc.lanes, task)
		} else {
			doc.monitors = append(doc.monitors, task)
		}
	}
	if sess.SessionID == "" {
		return doc, nil
	}
	entry, err := findDrive(sess.SessionID)
	doc.registry = entry
	return doc, err
}

func defaultInboxDir(program string) (string, error) {
	if root := os.Getenv("LONG_RUNNING_SCRATCH_ROOT"); root != "" {
		return filepath.Join(root, program, "inbox"), nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".claude", "scratch", program, "inbox"), nil
}

func prepareInboxes(doc *progressDoc, directory string) error {
	if info, err := os.Stat(directory); err != nil || !info.IsDir() {
		return nil
	}
	if err := readInboxes(doc, directory); err != nil {
		return err
	}
	if doc.registry == nil || doc.registry.StartedAt == "" {
		return nil
	}
	started, err := time.Parse(time.RFC3339Nano, doc.registry.StartedAt)
	if err != nil {
		return err
	}
	return clearStaleCursors(doc, started)
}

func (d *deps) build(ctx context.Context, opts generateOptions) (*progressDoc, string, error) {
	plan, err := expandUser(opts.plan)
	if err != nil {
		return nil, "", err
	}
	sess, err := d.readSession(opts.session)
	if err != nil {
		return nil, "", err
	}
	doc, err := d.newDoc(opts, plan, sess)
	if err != nil {
		return nil, "", err
	}
	directory := opts.inboxDir
	if directory == "" {
		directory, err = defaultInboxDir(opts.program)
	} else {
		directory, err = expandUser(directory)
	}
	if err != nil {
		return nil, "", err
	}
	if err := prepareInboxes(doc, directory); err != nil {
		return nil, "", err
	}
	if opts.folder {
		previous, err := doc.buildFromFolder(plan)
		return doc, previous, err
	}
	previous, err := d.buildFromNotes(ctx, doc, opts)
	return doc, previous, err
}

func (d *progressDoc) buildFromFolder(plan string) (string, error) {
	records, last, err := folderRecords(plan)
	if err != nil {
		return "", err
	}
	previous, source := "", ""
	if last != "" {
		body, err := os.ReadFile(last)
		if err != nil {
			return "", err
		}
		previous, source = string(body), "file "+filepath.Base(last)
	}
	d.narrate(records, previous, source)
	d.fresh = len(records) > 0
	return previous, d.check(previous, nil)
}

func (d *deps) readGeneratedSources(ctx context.Context, doc *progressDoc, opts generateOptions) error {
	found, err := d.records(ctx, recordQuery{drive: doc.busName(), kinds: []string{goKind, correctionKind}})
	if err != nil {
		return err
	}
	doc.standing = readStanding(found, doc.busName())
	if err := d.readIncidentLanes(ctx, doc); err != nil {
		return err
	}
	program, which, drive := opts.program, []string{"--program", opts.program}, opts.program
	ledgerID := opts.ledger
	if entry := doc.registry; entry != nil {
		if _, err := os.Stat(filepath.Join(entry.StateDir, "lr.json")); err == nil {
			d.readOwnerUpdate(ctx, doc, entry)
		}
		program, which, drive = entry.programName(), []string{"--drive", entry.Drive}, entry.cciName()
		ledgerID = cmp.Or(ledgerID, entry.Ledger)
	}
	if doc.reg, err = d.readRegister(ctx, opts.repo, program); err != nil {
		return err
	}
	doc.rows = d.registerRows(ctx, opts.repo, which, drive, doc.at)
	if ledgerID != "" {
		if doc.asks, err = d.openAsks(ctx, ledgerID, doc.at); err != nil {
			return err
		}
	}
	doc.planProgress, err = d.planLines(ctx, opts.repo, opts.program)
	return err
}

func (d *deps) buildFromNotes(ctx context.Context, doc *progressDoc, opts generateOptions) (string, error) {
	if err := d.readGeneratedSources(ctx, doc, opts); err != nil {
		return "", err
	}
	var all []noteDoc
	if err := d.ccn(ctx, opts.repo, &all, "doc", "list", "--label", "progress:"+opts.program, "--include-superseded", "--full"); err != nil {
		return "", err
	}
	if len(all) > 0 {
		if err := d.readDeliveries(ctx, doc, newestDoc(all).UpdatedAt); err != nil {
			return "", err
		}
	}
	active := slices.DeleteFunc(slices.Clone(all), func(note noteDoc) bool { return len(note.SupersededBy) > 0 })
	var written, generated []noteDoc
	for _, note := range lineage(all, active) {
		if isGenerated(note.Body) {
			generated = append(generated, note)
		} else {
			written = append(written, note)
		}
	}
	slices.SortStableFunc(written, func(a, b noteDoc) int { return cmp.Compare(a.CreatedAt, b.CreatedAt) })
	isActive := func(note noteDoc) bool {
		return slices.ContainsFunc(active, func(other noteDoc) bool { return other.ID == note.ID })
	}
	if current := slices.DeleteFunc(slices.Clone(generated), func(note noteDoc) bool { return !isActive(note) }); len(current) > 0 {
		newest := newestDoc(current)
		doc.generated = newest.ID
		doc.retitle = strings.HasSuffix(newest.Title, generatedMark)
	}
	if len(written) > 0 {
		doc.record = written[len(written)-1].ID
	}
	keep := cmp.Or(doc.record, doc.generated)
	for _, note := range active {
		if note.ID != keep {
			doc.stale = append(doc.stale, note.ID)
		}
	}
	return d.narrateNotes(ctx, doc, opts.repo, written, generated)
}

func (d *deps) narrateNotes(ctx context.Context, doc *progressDoc, repo string, written, generated []noteDoc) (string, error) {
	records := make([]progressRecord, 0, len(written))
	for _, note := range written {
		records = append(records, docRecord(note))
	}
	previous, source := "", ""
	var latest noteDoc
	if len(generated) > 0 {
		latest = newestDoc(generated)
		previous, source = latest.Body, "doc "+cut(latest.ID, shortID)
	}
	doc.narrate(records, previous, source)
	if len(written) == 0 && len(generated) > 0 {
		edited, err := d.editedInPlace(ctx, repo, latest)
		if err != nil {
			return "", err
		}
		if edited {
			doc.records, doc.narrativeFrom = []progressRecord{docRecord(latest)}, source
		}
	}
	doc.fresh = len(doc.records) > 0
	live, err := d.liveAnswers(ctx, repo)
	if err != nil {
		return "", err
	}
	return previous, doc.check(previous, live)
}

type generateOutput struct {
	ID        *string  `json:"id"`
	File      string   `json:"file"`
	Register  *string  `json:"register"`
	Fresh     bool     `json:"fresh"`
	Digest    string   `json:"digest"`
	ReadFirst []string `json:"read_first"`
	Narrative *string  `json:"narrative"`
}

func (d *deps) printOutput(doc *progressDoc, written *string, path string, reg *string, reading string) error {
	refs := make([]string, 0, len(doc.readFirst))
	for _, first := range doc.readFirst {
		refs = append(refs, first.ref)
	}
	encoder := json.NewEncoder(d.stdout)
	encoder.SetEscapeHTML(false)
	return encoder.Encode(generateOutput{written, path, reg, doc.fresh, doc.digest(doc.reading(reading)), refs, doc.narrativeSource()})
}

func writeProgressFile(path, markdown string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(markdown), 0o600)
}

func (d *deps) writeProgress(ctx context.Context, doc *progressDoc, repo, markdown string) (string, error) {
	edit := func(id string, extra ...string) (string, error) {
		argv := slices.Concat([]string{"ccn", "-R", repo, "doc", "edit", id}, extra, []string{"--body", "-"})
		_, err := d.run(ctx, argv, markdown)
		return id, err
	}
	switch {
	case doc.record != "":
		return edit(doc.record)
	case doc.generated != "" && doc.retitle:
		return edit(doc.generated, "--title", doc.title())
	case doc.generated != "":
		return edit(doc.generated)
	}
	when := fmt.Sprintf("Resuming or compacting the %s drive: read before anything else, after the plan", doc.program)
	out, err := d.run(ctx, []string{"ccn", "-R", repo, "doc", "add", doc.title(), "--label", "progress:" + doc.program, "--when", when, "--body", "-", "--json"}, markdown)
	if err != nil {
		return "", err
	}
	var added struct {
		ID string `json:"id"`
	}
	return added.ID, json.Unmarshal([]byte(out), &added)
}

func (d *deps) generate(ctx context.Context, opts generateOptions) (int, error) {
	doc, _, err := d.build(ctx, opts)
	if err != nil {
		return 0, err
	}
	if opts.strict && len(doc.findings) > 0 {
		_, err := fmt.Fprintln(d.stdout, strings.Join(doc.findings, "\n"))
		return violations, err
	}
	markdown := doc.render()
	head, _, _ := strings.Cut(markdown, narrativeMarker)
	if refusal := oversized(strings.ReplaceAll(head, doc.standingSection(), "")); refusal != "" {
		_, err := fmt.Fprintln(d.stdout, refusal)
		return tooLarge, err
	}
	path := filepath.Join(progressFolder(doc.plan), doc.stamp()+generatedStem+".md")
	if err := writeProgressFile(path, markdown); err != nil {
		return 0, err
	}
	if opts.folder {
		return 0, d.printOutput(doc, nil, path, nil, fmt.Sprintf("the generated handoff `%s`", path))
	}
	return d.publish(ctx, doc, opts, markdown, path)
}

func (d *deps) publish(ctx context.Context, doc *progressDoc, opts generateOptions, markdown, path string) (int, error) {
	written, err := d.writeProgress(ctx, doc, opts.repo, markdown)
	if err != nil {
		return 0, err
	}
	for _, stale := range doc.stale {
		if _, err := d.run(ctx, []string{"ccn", "-R", opts.repo, "doc", "supersede", stale, "--by", written}, ""); err != nil {
			return 0, err
		}
	}
	var active []noteDoc
	if err := d.ccn(ctx, opts.repo, &active, "doc", "list", "--label", "progress:"+opts.program); err != nil {
		return 0, err
	}
	if len(active) != 1 || active[0].ID != written {
		shorts := make([]string, 0, len(active))
		for _, note := range active {
			shorts = append(shorts, cut(note.ID, shortID))
		}
		short := cut(written, shortID)
		_, err := fmt.Fprintf(d.stdout, "%d active progress:%s docs after generation, expected only %s: %s; supersede every other with `ccn doc supersede <id> --by %s`\n",
			len(active), opts.program, short, strings.Join(shorts, ", "), short)
		return severalActive, err
	}
	var reg *string
	if doc.reg != nil {
		reg = &doc.reg.id
	}
	return 0, d.printOutput(doc, &written, path, reg, fmt.Sprintf("the progress doc `ccn doc show %s`", cut(written, shortID)))
}

type lintOptions struct {
	doc          string
	file         string
	previousDoc  string
	previousFile string
	program      string
	plan         string
	repo         string
}

func (d *deps) readBody(ctx context.Context, repo, doc, file string) (string, error) {
	if file != "" {
		data, err := os.ReadFile(file)
		return string(data), err
	}
	if doc == "" {
		return "", nil
	}
	var shown noteDoc
	err := d.ccn(ctx, repo, &shown, "doc", "show", doc)
	return shown.Body, err
}

func (d *deps) lint(ctx context.Context, opts lintOptions) (int, error) {
	body, err := d.readBody(ctx, opts.repo, opts.doc, opts.file)
	if err != nil {
		return 0, err
	}
	previous, err := d.readBody(ctx, opts.repo, opts.previousDoc, opts.previousFile)
	if err != nil {
		return 0, err
	}
	live, err := d.liveAnswers(ctx, opts.repo)
	if err != nil {
		return 0, err
	}
	rows := d.registerRows(ctx, opts.repo, []string{"--program", opts.program}, opts.program, d.now().UTC())
	reg, err := d.readRegister(ctx, opts.repo, opts.program)
	if err != nil {
		return 0, err
	}
	problems := lintBody(lintView(body), previous, reg, live, rows)
	if opts.plan != "" {
		plan, err := expandUser(opts.plan)
		if err != nil {
			return 0, err
		}
		found, err := uncitedPlanLines(plan, live)
		if err != nil {
			return 0, err
		}
		problems = append(problems, found...)
	}
	if len(problems) == 0 {
		return 0, nil
	}
	_, err = fmt.Fprintln(d.stdout, strings.Join(problems, "\n"))
	return violations, err
}

func (d *deps) standing(ctx context.Context, drive string) error {
	found, err := d.records(ctx, recordQuery{drive: drive, kinds: []string{goKind, correctionKind}})
	if err != nil {
		return err
	}
	live := readStanding(found, drive)
	ids := make([]string, 0, len(live.rules))
	for _, rule := range live.rules {
		ids = append(ids, rule.id)
	}
	if _, err := fmt.Fprintf(d.stdout, "live standing: %s\n", cmp.Or(strings.Join(ids, ", "), "none")); err != nil {
		return err
	}
	for _, rule := range live.rules {
		if _, err := fmt.Fprintf(d.stdout, "%s [%s]\n", rule.text, rule.source); err != nil {
			return err
		}
	}
	return nil
}
