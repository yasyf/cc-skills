package update

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	kindHold     = "hold"
	kindTapOrder = "lr.tap"
	headWaits    = "You approved it; it waits for a record naming its head."
	kindIncident = "incident"
	kindRelease  = "release"
	statusOpen   = "open"
	sessionChars = 8
	boardWatch   = "board-watch"
	boardTopic   = "board-rows"
)

var (
	waitsOnRoot = []string{"ask", "blocker", "blocked"}
	rootNames   = []string{root, "main"}
	namedPR     = regexp.MustCompile(`#(\d+)\b`)
	openTasks   = []string{"in_progress", "pending"}
)

type Sources struct {
	Lanes    func(ctx context.Context) ([]Lane, error)
	Records  func(ctx context.Context, since time.Time, kinds ...string) ([]Record, error)
	Plans    func(ctx context.Context) ([]*plans.Plan, error)
	Book     func(ctx context.Context) (asks.Book, error)
	PRRows   func() ([]store.ReviewRow, error)
	Board    func(ctx context.Context, snapshots []board.Snapshot) (board.Live, error)
	Tasks    func() ([]ClaudeTask, error)
	Before   func() (Snapshot, error)
	Retro    func(ctx context.Context) ([]Retro, error)
	Owed     func(ctx context.Context) ([]deletions.Owed, error)
	Programs func() ([]Program, error)
	Streams  func() ([]Workstream, error)
	Rulings  func(ctx context.Context) ([]PlanRuling, error)
	Overdue  func(now time.Time) ([]store.Item, error)
	Orders   func(ctx context.Context) ([]owner.Row, error)
	Pins     []drive.Pin
	TapQueue func() (string, error)
	Where    func(ctx context.Context, shas map[int]string) map[int]string
}

func midnight(now time.Time) time.Time {
	local := now.In(pacificZone)
	return time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, pacificZone)
}

func addressedToRoot(record Record) bool {
	return slices.ContainsFunc(record.To, func(to string) bool {
		address, _, _ := strings.Cut(to, "@")
		return slices.Contains(rootNames, address)
	})
}

func waitingOnRoot(records []Record) []Record {
	var waits []Record
	for _, record := range records {
		if slices.Contains(waitsOnRoot, record.Kind) && record.Status == statusOpen && addressedToRoot(record) {
			waits = append(waits, record)
		}
	}
	slices.SortStableFunc(waits, func(one, other Record) int { return int(one.Seq - other.Seq) })
	return waits
}

func heldPRs(records []Record) map[string]int64 {
	held := map[string]int64{}
	for _, record := range records {
		if record.Kind != kindHold || record.Status != statusOpen || record.Lane != bus.OwnerBy && record.Attrs[bus.BreaksAttr] == "" {
			continue
		}
		for _, pr := range record.PRs {
			held[pr[strings.LastIndex(pr, "#")+1:]] = record.Seq
		}
	}
	return held
}

func withPendingTaps(rows []store.ReviewRow, records []Record) []store.ReviewRow {
	pending := map[string]bool{}
	for _, record := range records {
		for _, pr := range record.PRs {
			number := pr[strings.LastIndex(pr, "#")+1:]
			switch record.Kind {
			case bus.TapPendingKind:
				pending[number] = true
			case kindTapOrder, bus.TapRefusedKind:
				delete(pending, number)
			}
		}
	}
	for at, row := range rows {
		if pending[strconv.Itoa(row.Number)] && !row.Tapped {
			rows[at].Tapped, rows[at].WaitsOn = true, headWaits
		}
	}
	return rows
}

func Collect(ctx context.Context, src Sources, bus string, now time.Time) (Inputs, error) {
	lanes, err := src.Lanes(ctx)
	if err != nil {
		return Inputs{}, err
	}
	before, err := src.Before()
	if err != nil {
		return Inputs{}, err
	}
	if before.At.IsZero() {
		before.At = now.Add(-defaultSince)
	}
	dayStart := now.Add(-quietAfter)
	window := dayStart
	if before.At.Before(window) {
		window = before.At
	}
	all, err := src.Records(ctx, window)
	if err != nil {
		return Inputs{}, err
	}
	day := recordsFrom(all, dayStart)
	retro, err := src.Retro(ctx)
	if err != nil {
		return Inputs{}, err
	}
	holds, err := src.Records(ctx, time.Time{}, kindHold)
	if err != nil {
		return Inputs{}, err
	}
	list, err := src.Plans(ctx)
	if err != nil {
		return Inputs{}, err
	}
	book, err := src.Book(ctx)
	if err != nil {
		return Inputs{}, err
	}
	prRows, err := src.PRRows()
	if err != nil {
		return Inputs{}, err
	}
	owed, err := src.Owed(ctx)
	if err != nil {
		return Inputs{}, err
	}
	programs, err := src.Programs()
	if err != nil {
		return Inputs{}, err
	}
	rulings, err := src.Rulings(ctx)
	if err != nil {
		return Inputs{}, err
	}
	overdue, err := src.Overdue(now)
	if err != nil {
		return Inputs{}, err
	}
	orders, err := src.Orders(ctx)
	if err != nil {
		return Inputs{}, err
	}
	in := Inputs{Drive: bus, Now: now, Lanes: lanes, Plans: list, Book: book, PRRows: withPendingTaps(prRows, all), Held: heldPRs(holds), Waits: waitingOnRoot(day), All: all, Before: before, Retro: retro, Owed: owed, Pins: src.Pins, Programs: programs, Rulings: rulings, Overdue: slices.DeleteFunc(overdue, func(item store.Item) bool { return item.Mover != machine.MoverRoot }), Orders: orders}
	in.Cards, in.Unboarded = boardOf(ctx, src, day)
	if in.TapQueue, err = src.TapQueue(); err != nil {
		return Inputs{}, err
	}
	in.Streams, in.Unread = src.Streams()
	in.Leading = leadingAsks(all)
	if shas := landedSHAs(src.Pins, in.Leading, book); len(shas) > 0 {
		in.Released = src.Where(ctx, shas)
	}
	named := namesOf(working(lanes, now))
	start := midnight(now)
	for _, record := range day {
		if slices.Contains(named, record.Lane) {
			in.Records = append(in.Records, record)
		}
		if record.At.Before(start) {
			continue
		}
		switch {
		case incidentRecord(record):
			in.Incidents = append(in.Incidents, record)
		case record.Kind == kindRelease:
			in.Releases = append(in.Releases, record)
		}
	}
	return in, nil
}

func snapshotsOf(records []Record) []board.Snapshot {
	var snapshots []board.Snapshot
	for _, record := range records {
		if record.Lane == boardWatch && record.Topic == boardTopic && record.Board != "" {
			snapshots = append(snapshots, board.Snapshot{Seq: record.Seq, Num: record.Num, At: record.At, Board: record.Board})
		}
	}
	return snapshots
}

func boardOf(ctx context.Context, src Sources, records []Record) ([]board.Wait, error) {
	live, err := src.Board(ctx, snapshotsOf(records))
	if err != nil {
		return nil, err
	}
	return live.Waits(), nil
}

type ClaudeTask struct {
	ID      string `json:"id"`
	Subject string `json:"subject"`
	Status  string `json:"status"`
	Owner   string `json:"owner"`
}

func ReadTasks(configDir, session string) ([]ClaudeTask, error) {
	paths, err := filepath.Glob(filepath.Join(configDir, "tasks", "session-"+session[:min(sessionChars, len(session))], "*.json"))
	if err != nil {
		return nil, err
	}
	tasks := make([]ClaudeTask, 0, len(paths))
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		var task ClaudeTask
		if err := json.Unmarshal(data, &task); err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		tasks = append(tasks, task)
	}
	slices.SortStableFunc(tasks, func(one, other ClaudeTask) int { return taskOrder(one) - taskOrder(other) })
	return tasks, nil
}

func taskOrder(task ClaudeTask) int {
	number, err := strconv.Atoi(task.ID)
	if err != nil {
		return 0
	}
	return number
}

type StatusAsk struct {
	Key   string `json:"key"`
	Lane  string `json:"lane"`
	State string `json:"state"`
	Text  string `json:"text"`
	Line  string `json:"line"`
}

type LastPost struct {
	Kind string    `json:"kind"`
	At   time.Time `json:"at"`
}

type InFlight struct {
	Task    string    `json:"task"`
	Lane    string    `json:"lane"`
	Subject string    `json:"subject"`
	Last    *LastPost `json:"last"`
	Line    string    `json:"line"`
}

type Delivered struct {
	Task    string    `json:"task"`
	Lane    string    `json:"lane"`
	Subject string    `json:"subject"`
	Seq     int64     `json:"seq"`
	Kind    string    `json:"kind"`
	At      time.Time `json:"at"`
	Text    string    `json:"text"`
	PRs     []string  `json:"prs"`
	Line    string    `json:"line"`
}

type BoardLine struct {
	PR   string `json:"pr"`
	Head string `json:"head"`
	Line string `json:"line"`
}

type IncidentLine struct {
	Seq  int64     `json:"seq"`
	Lane string    `json:"lane"`
	At   time.Time `json:"at"`
	Text string    `json:"text"`
	Line string    `json:"line"`
}

type Status struct {
	Since     time.Time      `json:"since"`
	Asks      []StatusAsk    `json:"asks"`
	InFlight  []InFlight     `json:"in_flight"`
	Delivered []Delivered    `json:"delivered"`
	Board     []BoardLine    `json:"board"`
	Incidents []IncidentLine `json:"incidents"`
}

func (s Status) Lines() []string {
	lines := make([]string, 0, len(s.Asks)+len(s.InFlight)+len(s.Delivered)+len(s.Board)+len(s.Incidents))
	for _, ask := range s.Asks {
		lines = append(lines, ask.Line)
	}
	for _, item := range s.InFlight {
		lines = append(lines, item.Line)
	}
	for _, item := range s.Delivered {
		lines = append(lines, item.Line)
	}
	for _, item := range s.Board {
		lines = append(lines, item.Line)
	}
	for _, item := range s.Incidents {
		lines = append(lines, item.Line)
	}
	return lines
}

func lastWords(record Record, found bool) string {
	if !found {
		return "no record in 24 h"
	}
	return fmt.Sprintf("last %s %s", record.Kind, pacific(record.At))
}

func newestByLane(records []Record) map[string]Record {
	newest := map[string]Record{}
	for _, record := range bySeq(records) {
		newest[record.Lane] = record
	}
	return newest
}

func statusAsks(book asks.Book, now time.Time, latest map[string]Record) []StatusAsk {
	open := book.Open(now)
	out := make([]StatusAsk, len(open))
	for i, ask := range open {
		last, found := latest[ask.Lane]
		out[i] = StatusAsk{ask.Key, ask.Lane, string(ask.State), ask.Text, fmt.Sprintf("ask %s %s | %s", ask.State, clip(ask.Line()), lastWords(last, found))}
	}
	return out
}

func statusInFlight(tasks []ClaudeTask, latest map[string]Record) []InFlight {
	out := []InFlight{}
	for _, task := range tasks {
		if task.Status == "in_progress" && task.Owner != "" {
			last, found := latest[task.Owner]
			var post *LastPost
			if found {
				post = &LastPost{last.Kind, last.At}
			}
			out = append(out, InFlight{task.ID, task.Owner, task.Subject, post, fmt.Sprintf("in flight #%s %s: %s | %s", task.ID, task.Owner, clip(task.Subject), lastWords(last, found))})
		}
	}
	return out
}

func statusDelivered(tasks []ClaudeTask, records []Record, since time.Time, kinds []string) []Delivered {
	ordered := slices.Clone(tasks)
	slices.SortStableFunc(ordered, func(one, other ClaudeTask) int { return inProgressFirst(one) - inProgressFirst(other) })
	owned := map[string]ClaudeTask{}
	for _, task := range ordered {
		if slices.Contains(openTasks, task.Status) && task.Owner != "" {
			if _, taken := owned[task.Owner]; !taken {
				owned[task.Owner] = task
			}
		}
	}
	var eligible []Record
	for _, record := range records {
		if _, mine := owned[record.Lane]; mine && slices.Contains(kinds, record.Kind) && record.At.After(since) {
			eligible = append(eligible, record)
		}
	}
	newest := newestByLane(eligible)
	lanes := make([]string, 0, len(newest))
	for lane := range newest {
		lanes = append(lanes, lane)
	}
	slices.SortFunc(lanes, func(one, other string) int { return int(newest[one].Seq - newest[other].Seq) })
	out := make([]Delivered, len(lanes))
	for i, lane := range lanes {
		record, task := newest[lane], owned[lane]
		text := clip(record.Text)
		out[i] = Delivered{task.ID, lane, task.Subject, record.Seq, record.Kind, record.At, text, prsOf(record), fmt.Sprintf("delivered #%s %s: %s | %s %s %s: %s", task.ID, lane, clip(task.Subject), record.Kind, record.Name(), pacific(record.At), text)}
	}
	return out
}

func inProgressFirst(task ClaudeTask) int {
	if task.Status == "in_progress" {
		return 0
	}
	return 1
}

func prsOf(record Record) []string {
	named := append([]string{}, record.PRs...)
	for _, found := range namedPR.FindAllStringSubmatch(record.Text, -1) {
		named = append(named, found[1])
	}
	slices.Sort(named)
	return slices.Compact(named)
}

func statusBoard(ctx context.Context, src Sources, records []Record) []BoardLine {
	live, err := src.Board(ctx, snapshotsOf(records))
	if err != nil {
		return []BoardLine{{Line: fmt.Sprintf("board unread: %v", err)}}
	}
	out := []BoardLine{}
	for _, wait := range live.Ready {
		out = append(out, BoardLine{wait.PR, wait.Head, wait.Line()})
	}
	return out
}

func statusIncidents(records []Record) []IncidentLine {
	out := []IncidentLine{}
	for _, record := range records {
		if record.Kind == kindIncident && record.Status == statusOpen {
			out = append(out, IncidentLine{record.Seq, record.Lane, record.At, record.Text, fmt.Sprintf("incident %s %s %s: %s", record.Name(), record.Lane, pacific(record.At), clip(record.Text))})
		}
	}
	return out
}

func ReadStatus(ctx context.Context, src Sources, now, since time.Time, kinds, only []string) (Status, error) {
	tasks, err := src.Tasks()
	if err != nil {
		return Status{}, err
	}
	window := now.Add(-quietAfter)
	if since.Before(window) {
		window = since
	}
	records, err := src.Records(ctx, window)
	if err != nil {
		return Status{}, err
	}
	owners := map[string]bool{}
	for _, task := range tasks {
		if slices.Contains(openTasks, task.Status) && task.Owner != "" {
			owners[task.Owner] = true
		}
	}
	mine := make([]Record, 0, len(records))
	for _, record := range records {
		if owners[record.Lane] {
			mine = append(mine, record)
		}
	}
	latest := newestByLane(mine)
	status := Status{Since: since, Asks: []StatusAsk{}, InFlight: []InFlight{}, Delivered: []Delivered{}, Board: []BoardLine{}, Incidents: []IncidentLine{}}
	wanted := func(section string) bool { return len(only) == 0 || slices.Contains(only, section) }
	if wanted("asks") {
		book, err := src.Book(ctx)
		if err != nil {
			return Status{}, err
		}
		status.Asks = statusAsks(book, now, latest)
	}
	if wanted("in_flight") {
		status.InFlight = statusInFlight(tasks, latest)
	}
	if wanted("delivered") {
		if len(kinds) == 0 {
			kinds = deliveredKinds
		}
		status.Delivered = statusDelivered(tasks, mine, since, kinds)
	}
	if wanted("board") {
		status.Board = statusBoard(ctx, src, records)
	}
	if wanted("incidents") {
		status.Incidents = statusIncidents(records)
	}
	return status, nil
}
