package handoff

import (
	"bytes"
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"
	_ "time/tzdata"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	retiredState      = "retired"
	unregisteredState = "unregistered"
	pendingStatus     = "pending"
	allStep           = "all"
	mechanismKind     = "mechanism"
	mechanismMark     = "MECHANISM"
	deliveryChars     = 120
	unrecordedTime    = "an unrecorded time"
)

var (
	ruling        = regexp.MustCompile("^\\s*(?:[-*]\\s+)?\\**`?(" + ruleID + ")\\b")
	incidentLane  = regexp.MustCompile(`^(?:inc|incident|outage)-`)
	mechanismText = regexp.MustCompile(`^(?:\d{1,2}:\d{2} [AP]M )?(MECHANISM|CANDIDATE)\b`)
	settlingText  = regexp.MustCompile(`^(?:\d{1,2}:\d{2} [AP]M )?(?:OPENED|BLOCKER|READY|NOT-OURS|DONE|STOPPED)\b`)
	textKinds     = []string{"report", "incident", "state"}
	settlingKinds = []string{"opened", "head", "ready", "blocker", "blocked", "fix-live", "not-ours", "stopped", "stand-down", "done"}
	reportedKinds = []string{"report", "done", "fix-live", "handoff", "head"}
	laneTypes     = []string{"subagent", "teammate", "workflow", "cloud session"}
	pacific       = mustLocation("America/Los_Angeles")
)

func mustLocation(name string) *time.Location {
	location, err := time.LoadLocation(name)
	if err != nil {
		panic(err)
	}
	return location
}

type recordQuery struct {
	drive string
	kinds []string
	since time.Time
}

type recordReader func(ctx context.Context, q recordQuery) ([]busRecord, error)

type blockerPoster func(ctx context.Context, drive, id, text string)

type deps struct {
	runner  shell.Runner
	ledger  ledger.Store
	records recordReader
	blocker blockerPoster
	now     func() time.Time
	stdin   io.Reader
	stdout  io.Writer
}

func (d *deps) run(ctx context.Context, argv []string, stdin string) (string, error) {
	result, err := d.runner.Run(ctx, shell.Command{Argv: argv, Stdin: stdin})
	return result.Stdout, err
}

func (d *deps) ccn(ctx context.Context, repo string, target any, args ...string) error {
	out, err := d.run(ctx, append(append([]string{"ccn", "-R", repo}, args...), "--json"), "")
	if err != nil {
		return err
	}
	if strings.TrimSpace(out) == "" {
		return nil
	}
	return json.Unmarshal([]byte(out), target)
}

func (d *deps) planLines(ctx context.Context, repo, program string) ([]string, error) {
	progress, err := plans.Read(ctx, func(ctx context.Context, args ...string) (string, error) {
		return d.run(ctx, append([]string{"ccn", "-R", repo}, args...), "")
	}, program)
	if err != nil {
		return nil, err
	}
	return plans.SummaryLines(progress), nil
}

func (d *deps) liveAnswers(ctx context.Context, repo string) ([]string, error) {
	var answers []struct {
		ID string `json:"id"`
	}
	if err := d.ccn(ctx, repo, &answers, "answer", "list", "--limit", "0"); err != nil {
		return nil, err
	}
	ids := make([]string, 0, len(answers))
	for _, answer := range answers {
		ids = append(ids, answer.ID)
	}
	return ids, nil
}

func (d *deps) readRegister(ctx context.Context, repo, program string) (*register, error) {
	var docs []noteDoc
	if err := d.ccn(ctx, repo, &docs, "doc", "list", "--label", "standing-rules:"+program, "--full"); err != nil {
		return nil, err
	}
	if len(docs) == 0 {
		return nil, nil
	}
	newest := newestDoc(docs)
	return &register{id: newest.ID, body: newest.Body}, nil
}

func failureOf(err error) string {
	var exit *shell.ExitError
	var syntax *json.SyntaxError
	switch {
	case errors.As(err, &exit):
		lines := splitLines(strings.TrimSpace(exit.Stderr))
		if len(lines) == 0 {
			return fmt.Sprintf("exit %d", exit.Code)
		}
		return lines[len(lines)-1]
	case errors.Is(err, context.DeadlineExceeded):
		return "timed out"
	case errors.As(err, &syntax):
		return "printed no JSON"
	}
	return err.Error()
}

func (d *deps) registerRows(ctx context.Context, repo string, which []string, drive string, at time.Time) *rowSet {
	rows, found, err := d.readRows(ctx, repo, which, at)
	if err != nil {
		reason := failureOf(err)
		d.blocker(ctx, drive, fmt.Sprintf("register-rows:%s:%s", drive, at.UTC().Format("20060102T15")),
			fmt.Sprintf("The register rows for %s did not load for the handoff: %s. Read them with `rulings.py rows %s --json` and carry the scope rows' words by hand.", drive, reason, strings.Join(which, " ")))
		return &rowSet{failure: reason, readAt: at}
	}
	if !found {
		return nil
	}
	return rows
}

func (d *deps) readRows(ctx context.Context, repo string, which []string, at time.Time) (*rowSet, bool, error) {
	out, err := d.run(ctx, slices.Concat([]string{"rulings.py", "rows"}, which, []string{"--repo", repo, "--json"}), "")
	if err != nil {
		return nil, false, err
	}
	var payload struct {
		Ledger string          `json:"ledger"`
		Rows   json.RawMessage `json:"rows"`
	}
	if err := json.Unmarshal([]byte(out), &payload); err != nil {
		return nil, false, err
	}
	if payload.Ledger == "" {
		return nil, false, nil
	}
	rows, err := parseRows(payload.Rows)
	if err != nil {
		return nil, false, err
	}
	return &rowSet{rows: rows, readAt: at}, true, nil
}

func (d *deps) openAsks(ctx context.Context, ledgerID string, at time.Time) ([]string, error) {
	rows, err := d.ledger.Rows(ctx, ledgerID)
	if err != nil {
		return nil, err
	}
	byKey := make(map[string]map[string]string, len(rows))
	for _, row := range rows {
		byKey[row.Key] = row.Fields
	}
	book, err := asks.Read(byKey)
	if err != nil {
		return nil, err
	}
	lines := []string{}
	for _, ask := range book.Open(at) {
		if ask.State != asks.Live {
			lines = append(lines, fmt.Sprintf("%s — %s", ask.Line(), ask.State))
		}
	}
	return lines, nil
}

func inboxText(path string) ([]string, error) {
	parts, err := filepath.Glob(filepath.Join(path+".archive", "*.md"))
	if err != nil {
		return nil, err
	}
	if _, err := os.Stat(path); err == nil {
		parts = append(parts, path)
	}
	var data bytes.Buffer
	for _, part := range parts {
		chunk, err := os.ReadFile(part)
		if err != nil {
			return nil, err
		}
		data.Write(chunk)
	}
	end := bytes.LastIndexByte(data.Bytes(), '\n')
	if end < 0 {
		return nil, nil
	}
	return strings.Split(strings.ToValidUTF8(string(data.Bytes()[:end]), "�"), "\n"), nil
}

func readInboxes(doc *progressDoc, directory string) error {
	doc.inboxDir = directory
	paths, err := filepath.Glob(filepath.Join(directory, "*.md"))
	if err != nil {
		return err
	}
	for _, path := range paths {
		lines, err := inboxText(path)
		if err != nil {
			return err
		}
		item := inbox{name: filepath.Base(path), count: len(lines)}
		for _, line := range lines {
			if match := ruling.FindStringSubmatch(line); match != nil {
				item.head, item.last = match[1], strings.TrimSpace(line)
			}
		}
		if len(lines) > 0 {
			if match := ruling.FindStringSubmatch(lines[len(lines)-1]); match != nil {
				item.tail = match[1]
			}
		}
		if cursor, err := os.ReadFile(path + ".cursor"); err == nil {
			item.cursor = strings.TrimSpace(string(cursor))
		}
		doc.inboxes = append(doc.inboxes, item)
	}
	return nil
}

func clearStaleCursors(doc *progressDoc, started time.Time) error {
	for at := range doc.inboxes {
		item := &doc.inboxes[at]
		if !item.readThrough() {
			continue
		}
		cursor := filepath.Join(doc.inboxDir, item.name+".cursor")
		info, err := os.Stat(cursor)
		if err != nil {
			return err
		}
		if !info.ModTime().Before(started) {
			continue
		}
		if err := os.Remove(cursor); err != nil {
			return err
		}
		doc.cleared = append(doc.cleared, fmt.Sprintf("%s at %s, written before the drive started at %s", filepath.Base(cursor), item.cursor, started.UTC().Format(secondsStamp)))
		item.cursor = ""
	}
	return nil
}

func minutesAgo(at, now time.Time) int {
	return int(math.Floor(now.Sub(at).Minutes()))
}

func mechanismOf(record busRecord) string {
	if record.Kind == mechanismKind {
		return mechanismMark
	}
	if slices.Contains(textKinds, record.Kind) {
		if match := mechanismText.FindStringSubmatch(record.Text); match != nil {
			return match[1]
		}
	}
	return ""
}

func settlesMechanism(record busRecord) bool {
	return slices.Contains(settlingKinds, record.Kind) || (slices.Contains(textKinds, record.Kind) && settlingText.MatchString(record.Text))
}

type waitingMark struct {
	mark string
	at   time.Time
}

func incidentLines(records []busRecord, now time.Time) []string {
	last := map[string]busRecord{}
	waiting := map[string]waitingMark{}
	order := []string{}
	for _, record := range records {
		if !incidentLane.MatchString(record.Lane) {
			continue
		}
		if _, seen := last[record.Lane]; !seen {
			order = append(order, record.Lane)
		}
		last[record.Lane] = record
		if mark := mechanismOf(record); mark != "" {
			if _, held := waiting[record.Lane]; !held {
				waiting[record.Lane] = waitingMark{mark, record.At}
			}
		} else if settlesMechanism(record) {
			delete(waiting, record.Lane)
		}
	}
	rank := func(lane string) int {
		if _, held := waiting[lane]; held {
			return 0
		}
		return 1
	}
	slices.SortStableFunc(order, func(a, b string) int {
		return cmp.Or(cmp.Compare(rank(a), rank(b)), cmp.Compare(minutesAgo(last[a].At, now), minutesAgo(last[b].At, now)))
	})
	lines := make([]string, 0, len(order))
	for _, lane := range order {
		record := last[lane]
		line := fmt.Sprintf("- `%s`: last %s %d min ago: %s", lane, record.Kind, minutesAgo(record.At, now), clip(record.Text, taskChars))
		if mark, held := waiting[lane]; held {
			line += fmt.Sprintf(" | %s %d min ago, no OPENED or BLOCKER since", mark.mark, minutesAgo(mark.at, now))
		}
		lines = append(lines, line)
	}
	return lines
}

func onDrive(records []busRecord, drive string) []busRecord {
	return slices.DeleteFunc(records, func(record busRecord) bool { return record.Drive != drive })
}

func (d *deps) readIncidentLanes(ctx context.Context, doc *progressDoc) error {
	records, err := d.records(ctx, recordQuery{drive: doc.busName(), since: doc.at.Add(-incidentWindow)})
	if err != nil {
		return err
	}
	doc.incidentLanes = incidentLines(onDrive(records, doc.busName()), doc.at)
	return nil
}

type laneRow struct {
	Lane     string `json:"lane"`
	State    string `json:"state"`
	Heard    string `json:"heard"`
	Step     string `json:"step"`
	Standing string `json:"standing"`
	PRs      []int  `json:"prs"`
}

func (r laneRow) line() string {
	step := r.Step
	if step == allStep {
		step = ""
	}
	line := fmt.Sprintf("- %s %s, heard %s, step %s", r.Lane, r.State, pacificText(r.Heard), dash(step))
	if len(r.PRs) > 0 {
		numbers := make([]string, 0, len(r.PRs))
		for _, pr := range r.PRs {
			numbers = append(numbers, fmt.Sprintf("#%d", pr))
		}
		line += ", PRs " + strings.Join(numbers, " ")
	}
	if r.Standing != "" {
		line += ", standing: " + r.Standing
	}
	return line
}

func pacificClock(at time.Time) string {
	return at.In(pacific).Format("3:04 PM")
}

func pacificText(stamp string) string {
	at, err := time.Parse(time.RFC3339Nano, stamp)
	if err != nil {
		return unrecordedTime
	}
	return pacificClock(at)
}

func (d *deps) laneRows(ctx context.Context, entry *driveEntry) ([]laneRow, error) {
	out, err := d.run(ctx, []string{"drive_supervisor.py", "lanes", "--config", filepath.Join(entry.StateDir, "lr.json"), "--json"}, "")
	if err != nil {
		return nil, err
	}
	rows := []laneRow{}
	for _, line := range splitLines(out) {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var row laneRow
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			return nil, err
		}
		rows = append(rows, row)
	}
	return rows, nil
}

func (d *deps) readOwnerUpdate(ctx context.Context, doc *progressDoc, entry *driveEntry) {
	rows, err := d.laneRows(ctx, entry)
	if err != nil {
		doc.laneRegistry = []string{"- registry unread: " + err.Error()}
		return
	}
	rendered, err := d.run(ctx, []string{"lr", "update", "--drive", entry.cciName(), "--render"}, "")
	if err != nil && rendered == "" {
		doc.laneRegistry = []string{"- registry unread: " + err.Error()}
		return
	}
	names := make([]string, len(rows))
	for n, row := range rows {
		names[n] = row.Lane
	}
	doc.aliases = lanes.AliasesOf(names)
	doc.ownerUpdate = strings.Join(withinBudget(splitLines(rendered), updateBudget, "lr update --drive "+entry.cciName()+" --render"), "\n")
	for _, row := range rows {
		if (row.State != retiredState && row.State != unregisteredState) || row.Standing != "" {
			doc.laneRegistry = append(doc.laneRegistry, row.line())
		}
	}
	doc.registered = len(doc.laneRegistry)
	doc.laneRegistry = withinBudget(doc.laneRegistry, registryBudget, "lr lanes --config "+filepath.Join(entry.StateDir, "lr.json"))
}

func withinBudget(lines []string, budget int, whole string) []string {
	used := 0
	for index, line := range lines {
		if used += len(line) + 1; used > budget {
			return append(slices.Clone(lines[:index]), fmt.Sprintf("- %d more: run `%s`", len(lines)-index, whole))
		}
	}
	return lines
}

func openTask(task sessionTask) bool {
	return task.Status == inProgress || task.Status == pendingStatus
}

func deliveryClip(text string) string {
	text = flat(text)
	runes := []rune(text)
	if len(runes) <= deliveryChars {
		return text
	}
	return strings.TrimRight(string(runes[:deliveryChars-1]), " ") + "…"
}

func deliveredLines(tasks []sessionTask, records []busRecord, since time.Time) []string {
	ordered := slices.Clone(tasks)
	slices.SortStableFunc(ordered, func(a, b sessionTask) int {
		rank := func(task sessionTask) int {
			if task.Status == inProgress {
				return 0
			}
			return 1
		}
		return cmp.Compare(rank(a), rank(b))
	})
	owned := map[string]sessionTask{}
	for _, task := range ordered {
		if _, held := owned[task.Owner]; openTask(task) && task.Owner != "" && !held {
			owned[task.Owner] = task
		}
	}
	bySeq := slices.Clone(records)
	slices.SortStableFunc(bySeq, func(a, b busRecord) int { return cmp.Compare(a.Seq, b.Seq) })
	newest := map[string]busRecord{}
	order := []string{}
	for _, record := range bySeq {
		if _, held := owned[record.Lane]; !held || !slices.Contains(reportedKinds, record.Kind) || !record.At.After(since) {
			continue
		}
		if _, seen := newest[record.Lane]; !seen {
			order = append(order, record.Lane)
		}
		newest[record.Lane] = record
	}
	lines := make([]string, 0, len(order))
	for _, lane := range order {
		task, record := owned[lane], newest[lane]
		lines = append(lines, fmt.Sprintf("#%s %s: %s | %s #%d %s: %s", task.ID, lane, deliveryClip(task.Subject), record.Kind, record.Seq, pacificClock(record.At), deliveryClip(record.Text)))
	}
	return lines
}

func (d *deps) readDeliveries(ctx context.Context, doc *progressDoc, since string) error {
	if !slices.ContainsFunc(doc.tasks, func(task sessionTask) bool { return task.Owner != "" && openTask(task) }) {
		return nil
	}
	at, err := time.Parse(time.RFC3339Nano, since)
	if err != nil {
		return err
	}
	records, err := d.records(ctx, recordQuery{drive: doc.busName(), kinds: reportedKinds, since: at})
	if err != nil {
		return err
	}
	doc.delivered = deliveredLines(doc.tasks, onDrive(records, doc.busName()), at)
	return nil
}

func configDir() (string, error) {
	if dir := os.Getenv("CLAUDE_CONFIG_DIR"); dir != "" {
		return dir, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".claude"), nil
}

func findDrive(sessionID string) (*driveEntry, error) {
	dir, err := configDir()
	if err != nil {
		return nil, err
	}
	named := os.Getenv(driveEnv)
	paths, err := filepath.Glob(filepath.Join(dir, "long-running", "drives", "*.json"))
	if err != nil {
		return nil, err
	}
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		var entry driveEntry
		if err := json.Unmarshal(data, &entry); err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		if entry.Drive == named || (named == "" && slices.Contains(entry.Sessions, sessionID)) {
			return &entry, nil
		}
	}
	return nil, nil
}
