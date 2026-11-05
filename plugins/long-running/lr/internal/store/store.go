package store

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	_ "modernc.org/sqlite"
)

const (
	StaleAfter = 15 * time.Minute
	stampForm  = "2006-01-02T15:04:05Z"
)

var ErrStaleGeneration = errors.New("stale generation")

type LaneState string

const (
	LaneLaunching LaneState = "launching"
	LaneDesired   LaneState = "desired"
	LaneHeld      LaneState = "held"
	LaneRetired   LaneState = "retired"
)

type PR struct {
	Repo   string
	Number int
	Head   string
	Base   string
	Lane   string
	State  machine.State
	ReadAt time.Time
}

type Item struct {
	ID       int64
	Kind     string
	Repo     string
	Number   int
	Head     string
	Mover    machine.Mover
	Worker   string
	Created  time.Time
	Deadline time.Time
	Closed   time.Time
}

type NewItem struct {
	Kind     string
	Repo     string
	Number   int
	Head     string
	Mover    machine.Mover
	Worker   string
	Deadline time.Duration
}

type Escalation struct {
	Subject string
	Cause   string
	ToMover string
	Raised  time.Time
}

type Worker struct {
	Name        string
	Generation  int64
	Heartbeat   time.Time
	Item        int64
	ContextLeft *int64
}

type Lease struct {
	Drive      string
	Generation int64
	Version    string
	PID        int
	Renewed    time.Time
}

type Lane struct {
	Name        string
	Brief       string
	Model       string
	Effort      string
	Role        string
	State       LaneState
	Since       time.Time
	Heard       time.Time
	Node        string
	Late        time.Duration
	Handoff     string
	PRs         []int
	Predecessor string
	Reason      string
	Asked       time.Time
	Flushed     time.Time
	RetiredBy   string
	Waiting     int64
	Answered    time.Time
	Page        map[string]any
	Awaits      string
	Last        string
	Placement   string
	StoppedOn   int64
	Launcher    string
}

type Probe struct {
	Asked time.Time
	Dead  time.Time
}

type Store struct {
	db *sql.DB
}

func Open(path string) (*Store, error) {
	return open(path + "?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)")
}

func Memory() (*Store, error) {
	return open(":memory:")
}

func open(source string) (*Store, error) {
	db, err := sql.Open("sqlite", source)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	s := &Store{db}
	if err := s.migrate(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) Close() error {
	return s.db.Close()
}

func (s *Store) migrate() error {
	var oldRows int
	if err := s.db.QueryRow("SELECT count(*) FROM pragma_table_info('review_rows') WHERE name = 'drawn_at'").Scan(&oldRows); err != nil {
		return err
	}
	if oldRows > 0 {
		if _, err := s.db.Exec("DROP TABLE review_rows"); err != nil {
			return err
		}
		if _, err := s.db.Exec("DELETE FROM cursors WHERE source = ?", ReviewRowsCursor); err != nil {
			return err
		}
	}
	if _, err := s.db.Exec(schema); err != nil {
		return err
	}
	for _, added := range addedColumns {
		var present int
		if err := s.db.QueryRow("SELECT count(*) FROM pragma_table_info(?) WHERE name = ?", added.table, added.column).Scan(&present); err != nil {
			return err
		}
		if present == 0 {
			if _, err := s.db.Exec(fmt.Sprintf("ALTER TABLE %s ADD COLUMN %s %s", added.table, added.column, added.kind)); err != nil {
				return err
			}
		}
	}
	return nil
}

func Stamp(at time.Time) string {
	return at.UTC().Format(stampForm)
}

func Parse(text string) (time.Time, error) {
	return time.Parse(time.RFC3339, text)
}

func (s *Store) PutPR(pr PR) error {
	_, err := s.db.Exec(
		"INSERT INTO prs (repo, number, head, base, lane, state, read_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (repo, number) DO UPDATE SET head = excluded.head, base = excluded.base, lane = excluded.lane, state = excluded.state, read_at = excluded.read_at",
		pr.Repo, pr.Number, pr.Head, pr.Base, pr.Lane, string(pr.State), Stamp(pr.ReadAt),
	)
	return err
}

func (s *Store) PRs(repo string) ([]PR, error) {
	return s.prs("WHERE repo = ? ORDER BY number", repo)
}

func (s *Store) NumberedPRs(numbers []int) ([]PR, error) {
	given, err := json.Marshal(numbers)
	if err != nil {
		return nil, err
	}
	return s.prs("WHERE number IN (SELECT value FROM json_each(?)) ORDER BY number, repo", string(given))
}

func (s *Store) prs(query string, args ...any) ([]PR, error) {
	rows, err := s.db.Query("SELECT repo, number, head, base, lane, state, read_at FROM prs "+query, args...)
	return collect(rows, err, func(rows *sql.Rows) (PR, error) {
		var pr PR
		err := rows.Scan(&pr.Repo, &pr.Number, &pr.Head, &pr.Base, &pr.Lane, &pr.State, moment{&pr.ReadAt})
		return pr, err
	})
}

const itemColumns = "id, kind, repo, number, head, mover, worker, created, deadline, closed"

func scanItem(row interface{ Scan(...any) error }) (Item, error) {
	var item Item
	err := row.Scan(&item.ID, &item.Kind, &item.Repo, &item.Number, &item.Head, &item.Mover, nullable[string]{&item.Worker}, moment{&item.Created}, moment{&item.Deadline}, moment{&item.Closed})
	return item, err
}

func (s *Store) items(query string, args ...any) ([]Item, error) {
	rows, err := s.db.Query("SELECT "+itemColumns+" FROM items "+query, args...)
	return collect(rows, err, func(rows *sql.Rows) (Item, error) { return scanItem(rows) })
}

func (s *Store) OpenItem(item NewItem, at time.Time) (Item, error) {
	if _, err := s.db.Exec(
		"INSERT INTO items (kind, repo, number, head, mover, worker, created, deadline) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (repo, number, head, kind) WHERE closed IS NULL DO NOTHING",
		item.Kind, item.Repo, item.Number, item.Head, string(item.Mover), text(item.Worker), Stamp(at), Stamp(at.Add(item.Deadline)),
	); err != nil {
		return Item{}, err
	}
	return scanItem(s.db.QueryRow("SELECT "+itemColumns+" FROM items WHERE repo = ? AND number = ? AND head = ? AND kind = ? AND closed IS NULL", item.Repo, item.Number, item.Head, item.Kind))
}

func (s *Store) CloseItem(id int64, at time.Time) error {
	_, err := s.db.Exec("UPDATE items SET closed = ? WHERE id = ? AND closed IS NULL", Stamp(at), id)
	return err
}

func (s *Store) CloseItems(repo string, number int, at time.Time, keep string) (int64, error) {
	result, err := s.db.Exec("UPDATE items SET closed = ? WHERE repo = ? AND number = ? AND closed IS NULL AND kind IS NOT ?", Stamp(at), repo, number, text(keep))
	if err != nil {
		return 0, err
	}
	return result.RowsAffected()
}

func (s *Store) OpenItems(repo string, number int) ([]Item, error) {
	return s.items("WHERE closed IS NULL AND (?1 IS NULL OR repo = ?1) AND (?2 IS NULL OR number = ?2) ORDER BY id", text(repo), whole(int64(number)))
}

type OpenRow struct {
	Item
	Lane  string
	Facts map[string]string
}

func (s *Store) OpenRows(repo, kind string, mover machine.Mover, numbers []int) ([]OpenRow, error) {
	given, err := json.Marshal(numbers)
	if err != nil {
		return nil, err
	}
	type joined struct {
		row         OpenRow
		kind, value string
	}
	rows, err := s.db.Query(`SELECT i.id, i.kind, i.repo, i.number, i.head, i.mover, i.worker, i.created, i.deadline, i.closed, coalesce(p.lane, ''), f.kind, f.value
FROM items i
LEFT JOIN prs p ON p.repo = i.repo AND p.number = i.number
LEFT JOIN facts f ON f.repo = i.repo AND f.number = i.number AND length(f.head) >= 7 AND (substr(i.head, 1, length(f.head)) = f.head OR length(i.head) >= 7 AND substr(f.head, 1, length(i.head)) = i.head)
WHERE i.closed IS NULL AND i.repo = ? AND i.kind = ? AND i.mover = ? AND i.number IN (SELECT value FROM json_each(?))
ORDER BY i.id, f.posted, length(f.head)`, repo, kind, string(mover), string(given))
	found, err := collect(rows, err, func(rows *sql.Rows) (joined, error) {
		var j joined
		item := &j.row.Item
		err := rows.Scan(&item.ID, &item.Kind, &item.Repo, &item.Number, &item.Head, &item.Mover, nullable[string]{&item.Worker}, moment{&item.Created}, moment{&item.Deadline}, moment{&item.Closed}, &j.row.Lane, nullable[string]{&j.kind}, nullable[string]{&j.value})
		return j, err
	})
	if err != nil {
		return nil, err
	}
	var out []OpenRow
	for _, j := range found {
		if len(out) == 0 || out[len(out)-1].ID != j.row.ID {
			j.row.Facts = map[string]string{}
			out = append(out, j.row)
		}
		if j.kind != "" {
			out[len(out)-1].Facts[j.kind] = j.value
		}
	}
	return out, nil
}

func (s *Store) Overdue(now time.Time) ([]Item, error) {
	return s.items("WHERE closed IS NULL AND deadline <= ? ORDER BY deadline, id", Stamp(now))
}

func (s *Store) PutFact(repo string, number int, head, kind, value string, at time.Time) (bool, error) {
	result, err := s.db.Exec(
		"INSERT INTO facts (repo, number, head, kind, value, posted) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (repo, number, head, kind) DO UPDATE SET value = excluded.value, posted = excluded.posted WHERE facts.value IS NOT excluded.value OR facts.kind = 'verdict'",
		repo, number, head, kind, value, Stamp(at),
	)
	return changed(result, err)
}

func (s *Store) ClearExempt(repo string, number int, through time.Time) error {
	_, err := s.db.Exec("DELETE FROM facts WHERE repo = ? AND number = ? AND kind = 'exempt' AND posted <= ?", repo, number, Stamp(through))
	return err
}

func (s *Store) StandingTap(repo string, number int) (string, error) {
	var head, value string
	err := s.db.QueryRow("SELECT head, value FROM facts WHERE repo = ? AND number = ? AND kind = 'tap' ORDER BY posted DESC, rowid DESC LIMIT 1", repo, number).Scan(&head, &value)
	if errors.Is(err, sql.ErrNoRows) || err == nil && value != "tapped" {
		return "", nil
	}
	return head, err
}

func (s *Store) StandingGo(repo string, number int) (string, error) {
	var head string
	err := s.db.QueryRow("SELECT head FROM facts WHERE repo = ? AND number = ? AND kind = 'exempt' ORDER BY posted DESC, rowid DESC LIMIT 1", repo, number).Scan(&head)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return head, err
}

func (s *Store) TrunkFacts(repo, kind string) (map[string]string, error) {
	rows, err := s.db.Query("SELECT head, value FROM facts WHERE repo = ? AND number = 0 AND kind = ?", repo, kind)
	found, err := collect(rows, err, func(rows *sql.Rows) ([2]string, error) {
		var pair [2]string
		err := rows.Scan(&pair[0], &pair[1])
		return pair, err
	})
	if err != nil {
		return nil, err
	}
	facts := make(map[string]string, len(found))
	for _, pair := range found {
		facts[pair[0]] = pair[1]
	}
	return facts, nil
}

func (s *Store) DeleteFact(repo string, number int, head, kind string) error {
	_, err := s.db.Exec("DELETE FROM facts WHERE repo = ? AND number = ? AND head = ? AND kind = ?", repo, number, head, kind)
	return err
}

func (s *Store) Facts(repo string, number int, head string) (map[string]string, error) {
	rows, err := s.db.Query("SELECT kind, value FROM facts WHERE repo = ?1 AND number = ?2 AND length(head) >= 7 AND (substr(?3, 1, length(head)) = head OR length(?3) >= 7 AND substr(head, 1, length(?3)) = ?3) ORDER BY posted, kind = 'verdict', length(head)", repo, number, head)
	found, err := collect(rows, err, func(rows *sql.Rows) ([2]string, error) {
		var pair [2]string
		err := rows.Scan(&pair[0], &pair[1])
		return pair, err
	})
	if err != nil {
		return nil, err
	}
	facts := make(map[string]string, len(found))
	for _, pair := range found {
		foldFact(facts, pair[0], pair[1])
	}
	return facts, nil
}

func foldFact(facts map[string]string, kind, value string) {
	facts[kind] = value
	if kind == "finder" && value != string(machine.FinderClear) || kind == "fidelity" && value != string(machine.FidelityPass) {
		delete(facts, "verdict")
	}
}

func (s *Store) PRFacts(repo string, number int) (map[string]string, error) {
	rows, err := s.db.Query("SELECT kind, value FROM facts WHERE repo = ? AND number = ? ORDER BY posted, rowid", repo, number)
	found, err := collect(rows, err, func(rows *sql.Rows) ([2]string, error) {
		var pair [2]string
		err := rows.Scan(&pair[0], &pair[1])
		return pair, err
	})
	if err != nil {
		return nil, err
	}
	facts := make(map[string]string, len(found))
	for _, pair := range found {
		facts[pair[0]] = pair[1]
	}
	return facts, nil
}

type HeadFact struct {
	Head, Kind, Value string
}

func (s *Store) HeadFacts(repo string, number int, kinds []string) ([]HeadFact, error) {
	given, err := json.Marshal(kinds)
	if err != nil {
		return nil, err
	}
	rows, err := s.db.Query("SELECT head, kind, value FROM facts WHERE repo = ? AND number = ? AND kind IN (SELECT value FROM json_each(?)) ORDER BY posted, rowid", repo, number, string(given))
	return collect(rows, err, func(rows *sql.Rows) (HeadFact, error) {
		var fact HeadFact
		err := rows.Scan(&fact.Head, &fact.Kind, &fact.Value)
		return fact, err
	})
}

type FactKey struct {
	Number int
	Head   string
}

func (s *Store) FactsOfKind(repo, kind string) (map[FactKey]time.Time, error) {
	rows, err := s.db.Query("SELECT number, head, posted FROM facts WHERE repo = ? AND kind = ?", repo, kind)
	type row struct {
		key    FactKey
		posted string
	}
	found, err := collect(rows, err, func(rows *sql.Rows) (row, error) {
		var r row
		err := rows.Scan(&r.key.Number, &r.key.Head, &r.posted)
		return r, err
	})
	if err != nil {
		return nil, err
	}
	stamps := make(map[FactKey]time.Time, len(found))
	for _, r := range found {
		if stamps[r.key], err = Parse(r.posted); err != nil {
			return nil, err
		}
	}
	return stamps, nil
}

func (s *Store) Escalate(subject, cause, toMover string, at time.Time) (bool, error) {
	result, err := s.db.Exec("INSERT OR IGNORE INTO escalations (subject, cause, to_mover, raised) VALUES (?, ?, ?, ?)", subject, cause, toMover, Stamp(at))
	return changed(result, err)
}

func (s *Store) ClearEscalation(subject string) error {
	_, err := s.db.Exec("DELETE FROM escalations WHERE subject = ?", subject)
	return err
}

func (s *Store) UnstoppedSpriteLanes() ([]Lane, error) {
	return s.registered("WHERE state = ? AND placement = 'sprite' AND name NOT IN (SELECT lane FROM sprite_stops)", string(LaneRetired))
}

func (s *Store) StopSprite(lane string, at time.Time) (bool, error) {
	result, err := s.db.Exec("INSERT OR IGNORE INTO sprite_stops (lane, at) VALUES (?, ?)", lane, Stamp(at))
	return changed(result, err)
}

func (s *Store) FailSpriteStop(lane string, tries int) (exhausted bool, err error) {
	var failures int
	if err := s.db.QueryRow("INSERT INTO sprite_stop_failures (lane, failures) VALUES (?, 1) ON CONFLICT (lane) DO UPDATE SET failures = failures + 1 RETURNING failures", lane).Scan(&failures); err != nil {
		return false, err
	}
	if failures >= tries {
		return true, nil
	}
	_, err = s.db.Exec("DELETE FROM sprite_stops WHERE lane = ?", lane)
	return false, err
}

func (s *Store) Escalations(subject string) ([]Escalation, error) {
	rows, err := s.db.Query("SELECT subject, cause, to_mover, raised FROM escalations WHERE ?1 IS NULL OR subject = ?1 ORDER BY raised", text(subject))
	return collect(rows, err, func(rows *sql.Rows) (Escalation, error) {
		var escalation Escalation
		err := rows.Scan(&escalation.Subject, &escalation.Cause, &escalation.ToMover, moment{&escalation.Raised})
		return escalation, err
	})
}

func (s *Store) Heartbeat(worker Worker) error {
	if _, err := s.db.Exec(
		"INSERT INTO workers (name, generation, heartbeat, item, context_left) VALUES (?, ?, ?, ?, ?) ON CONFLICT (name) DO UPDATE SET generation = excluded.generation, heartbeat = excluded.heartbeat, item = excluded.item, context_left = excluded.context_left",
		worker.Name, worker.Generation, Stamp(worker.Heartbeat), whole(worker.Item), worker.ContextLeft,
	); err != nil {
		return err
	}
	if worker.Item == 0 {
		return nil
	}
	_, err := s.db.Exec("UPDATE items SET worker = ? WHERE id = ? AND closed IS NULL", worker.Name, worker.Item)
	return err
}

func (s *Store) Workers() ([]Worker, error) {
	rows, err := s.db.Query("SELECT name, generation, heartbeat, item, context_left FROM workers ORDER BY name")
	return collect(rows, err, func(rows *sql.Rows) (Worker, error) {
		var worker Worker
		err := rows.Scan(&worker.Name, &worker.Generation, moment{&worker.Heartbeat}, nullable[int64]{&worker.Item}, &worker.ContextLeft)
		return worker, err
	})
}

func (s *Store) StaleWorkers(now time.Time) ([]string, error) {
	return s.strings("SELECT name FROM workers WHERE heartbeat <= ? AND EXISTS (SELECT 1 FROM items WHERE items.worker = workers.name AND items.closed IS NULL) ORDER BY name", Stamp(now.Add(-StaleAfter)))
}

func (s *Store) ReleaseWorker(name string) ([]Item, error) {
	held, err := s.items("WHERE worker = ? AND closed IS NULL ORDER BY id", name)
	if err != nil {
		return nil, err
	}
	if _, err := s.db.Exec("UPDATE items SET worker = NULL WHERE worker = ? AND closed IS NULL", name); err != nil {
		return nil, err
	}
	_, err = s.db.Exec("DELETE FROM workers WHERE name = ?", name)
	return held, err
}

func (s *Store) AcquireLease(drive, version string, pid int, at time.Time) (Lease, error) {
	if _, err := s.db.Exec(
		"INSERT INTO lease (drive, generation, version, pid, renewed) VALUES (?, 1, ?, ?, ?) ON CONFLICT (drive) DO UPDATE SET generation = lease.generation + 1, version = excluded.version, pid = excluded.pid, renewed = excluded.renewed",
		drive, version, pid, Stamp(at),
	); err != nil {
		return Lease{}, err
	}
	return s.Lease(drive)
}

func (s *Store) RenewLease(drive string, generation int64, at time.Time) error {
	result, err := s.db.Exec("UPDATE lease SET renewed = ? WHERE drive = ? AND generation = ?", Stamp(at), drive, generation)
	renewed, err := changed(result, err)
	if err != nil || renewed {
		return err
	}
	current, err := s.Lease(drive)
	if err != nil {
		return err
	}
	return fmt.Errorf("%w: %s: generation %d is behind %d", ErrStaleGeneration, drive, generation, current.Generation)
}

func (s *Store) ServeVersion(drive string, pid int, version string) error {
	_, err := s.db.Exec("UPDATE lease SET version = ? WHERE drive = ? AND pid = ?", version, drive, pid)
	return err
}

func (s *Store) Lease(drive string) (Lease, error) {
	var lease Lease
	err := s.db.QueryRow("SELECT drive, generation, version, pid, renewed FROM lease WHERE drive = ?", drive).Scan(&lease.Drive, &lease.Generation, &lease.Version, &lease.PID, moment{&lease.Renewed})
	return lease, err
}

func (s *Store) PutRule(id, words, said, checkKind, checkRef string) error {
	_, err := s.db.Exec(
		"INSERT INTO rules (id, words, said, check_kind, check_ref) VALUES (?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET words = excluded.words, said = excluded.said, check_kind = excluded.check_kind, check_ref = excluded.check_ref",
		id, words, said, text(checkKind), text(checkRef),
	)
	return err
}

func (s *Store) UncheckedRules() ([]string, error) {
	return s.strings("SELECT id FROM rules WHERE check_kind IS NULL ORDER BY said, id")
}

func (s *Store) Cursor(source string) (int64, error) {
	var position int64
	err := s.db.QueryRow("SELECT position FROM cursors WHERE source = ?", source).Scan(&position)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, nil
	}
	return position, err
}

func (s *Store) Advance(source string, position int64) error {
	return advance(s.db, source, position)
}

func advance(db execer, source string, position int64) error {
	_, err := db.Exec("INSERT INTO cursors (source, position) VALUES (?, ?) ON CONFLICT (source) DO UPDATE SET position = max(cursors.position, excluded.position)", source, position)
	return err
}

const laneColumns = "name, brief, model, effort, role, state, since, handoff, predecessor, reason, asked, flushed, retired_by, waiting, answered, page, awaits, placement, stopped_on, launcher"

func (s *Store) WantLane(lane Lane, at time.Time) (bool, error) {
	added, err := changed(insertLane(s.db, lane, at))
	if err != nil {
		return false, err
	}
	return added, s.Own(lane.Name, lane.PRs)
}

func (s *Store) PutLanes(lanes []Lane, at time.Time) (err error) {
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			_ = tx.Rollback()
		}
	}()
	if _, err = tx.Exec("DELETE FROM lanes"); err != nil {
		return err
	}
	if _, err = tx.Exec("DELETE FROM owners"); err != nil {
		return err
	}
	for _, lane := range lanes {
		if _, err = insertLane(tx, lane, at); err != nil {
			return err
		}
		if err = own(tx, lane.Name, lane.PRs); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func insertLane(db execer, lane Lane, at time.Time) (sql.Result, error) {
	page, err := encodePage(lane.Page)
	if err != nil {
		return nil, err
	}
	return db.Exec(
		"INSERT INTO lanes ("+laneColumns+") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (name) DO NOTHING",
		lane.Name, lane.Brief, lane.Model, lane.Effort, lane.Role, string(lane.State), Stamp(at),
		text(lane.Handoff), text(lane.Predecessor), text(lane.Reason), when(lane.Asked), when(lane.Flushed), text(lane.RetiredBy),
		whole(lane.Waiting), when(lane.Answered), page, text(lane.Awaits), text(lane.Placement), whole(lane.StoppedOn), text(lane.Launcher),
	)
}

func (s *Store) Lane(name string) (Lane, bool, error) {
	found, err := s.registered("WHERE name = ?", name)
	if err != nil || len(found) == 0 {
		return Lane{}, false, err
	}
	return found[0], true, nil
}

func (s *Store) Lanes(states ...LaneState) ([]Lane, error) {
	args := make([]any, len(states))
	for i, state := range states {
		args[i] = string(state)
	}
	return s.registered("WHERE state IN ("+placeholders(len(states))+")", args...)
}

func (s *Store) AllLanes() ([]Lane, error) {
	return s.registered("")
}

func (s *Store) Names() ([]string, error) {
	return s.strings("SELECT name FROM lanes ORDER BY name")
}

func (s *Store) registered(where string, args ...any) ([]Lane, error) {
	rows, err := s.db.Query("SELECT "+laneColumns+" FROM lanes "+where+" ORDER BY name", args...)
	lanes, err := collect(rows, err, scanLane)
	if err != nil || len(lanes) == 0 {
		return lanes, err
	}
	scope := "WHERE lane IN (SELECT name FROM lanes " + where + ")"
	rows, err = s.db.Query("SELECT lane, at, kind, node, late_seconds FROM heard "+scope, args...)
	type heardRow struct {
		lane, kind, node string
		at               time.Time
		late             int64
	}
	heard, err := collect(rows, err, func(rows *sql.Rows) (heardRow, error) {
		var row heardRow
		err := rows.Scan(&row.lane, moment{&row.at}, nullable[string]{&row.kind}, nullable[string]{&row.node}, nullable[int64]{&row.late})
		return row, err
	})
	if err != nil {
		return nil, err
	}
	byLane := make(map[string]heardRow, len(heard))
	for _, row := range heard {
		byLane[row.lane] = row
	}
	owned, err := s.owned(scope, args...)
	if err != nil {
		return nil, err
	}
	for i := range lanes {
		row := byLane[lanes[i].Name]
		lanes[i].Heard, lanes[i].Last, lanes[i].PRs = row.at, row.kind, owned[lanes[i].Name]
		lanes[i].Node, lanes[i].Late = row.node, time.Duration(row.late)*time.Second
	}
	return lanes, nil
}

func scanLane(rows *sql.Rows) (Lane, error) {
	var lane Lane
	var page string
	err := rows.Scan(
		&lane.Name, &lane.Brief, &lane.Model, &lane.Effort, &lane.Role, &lane.State, moment{&lane.Since},
		nullable[string]{&lane.Handoff}, nullable[string]{&lane.Predecessor}, nullable[string]{&lane.Reason},
		moment{&lane.Asked}, moment{&lane.Flushed}, nullable[string]{&lane.RetiredBy}, nullable[int64]{&lane.Waiting}, moment{&lane.Answered},
		nullable[string]{&page}, nullable[string]{&lane.Awaits}, nullable[string]{&lane.Placement}, nullable[int64]{&lane.StoppedOn}, nullable[string]{&lane.Launcher},
	)
	if err != nil || page == "" {
		return lane, err
	}
	err = json.Unmarshal([]byte(page), &lane.Page)
	return lane, err
}

func (s *Store) Heard() (map[string]time.Time, error) {
	rows, err := s.db.Query("SELECT lane, at FROM heard")
	type row struct {
		lane string
		at   time.Time
	}
	found, err := collect(rows, err, func(rows *sql.Rows) (row, error) {
		var r row
		err := rows.Scan(&r.lane, moment{&r.at})
		return r, err
	})
	if err != nil {
		return nil, err
	}
	heard := make(map[string]time.Time, len(found))
	for _, r := range found {
		heard[r.lane] = r.at
	}
	return heard, nil
}

func (s *Store) Owners() (map[string][]int, error) {
	return s.owned("")
}

func (s *Store) owned(scope string, args ...any) (map[string][]int, error) {
	rows, err := s.db.Query("SELECT pr, lane FROM owners "+scope+" ORDER BY pr", args...)
	type row struct {
		pr   int
		lane string
	}
	found, err := collect(rows, err, func(rows *sql.Rows) (row, error) {
		var r row
		err := rows.Scan(&r.pr, &r.lane)
		return r, err
	})
	if err != nil {
		return nil, err
	}
	owned := map[string][]int{}
	for _, r := range found {
		owned[r.lane] = append(owned[r.lane], r.pr)
	}
	return owned, nil
}

func (s *Store) Answered(answers []int64, states ...LaneState) ([]string, error) {
	args := make([]any, 0, len(answers)+len(states))
	for _, answer := range answers {
		args = append(args, answer)
	}
	for _, state := range states {
		args = append(args, string(state))
	}
	return s.strings("SELECT name FROM lanes WHERE awaits IS NULL AND waiting IN ("+placeholders(len(answers))+") AND state IN ("+placeholders(len(states))+")", args...)
}

func (s *Store) OwnerOf(prs []int) (map[int]string, error) {
	args := make([]any, len(prs))
	owners := make(map[int]string, len(prs))
	for i, pr := range prs {
		args[i] = pr
		owners[pr] = ""
	}
	rows, err := s.db.Query("SELECT pr, lane FROM owners WHERE pr IN ("+placeholders(len(prs))+")", args...)
	type row struct {
		pr   int
		lane string
	}
	found, err := collect(rows, err, func(rows *sql.Rows) (row, error) {
		var r row
		err := rows.Scan(&r.pr, &r.lane)
		return r, err
	})
	if err != nil {
		return nil, err
	}
	for _, r := range found {
		owners[r.pr] = r.lane
	}
	return owners, nil
}

func (s *Store) Pages() (map[string]map[string]any, error) {
	rows, err := s.db.Query("SELECT name, page FROM lanes WHERE page IS NOT NULL")
	type row struct{ name, page string }
	found, err := collect(rows, err, func(rows *sql.Rows) (row, error) {
		var r row
		err := rows.Scan(&r.name, &r.page)
		return r, err
	})
	if err != nil {
		return nil, err
	}
	pages := make(map[string]map[string]any, len(found))
	for _, r := range found {
		var page map[string]any
		if err := json.Unmarshal([]byte(r.page), &page); err != nil {
			return nil, err
		}
		pages[r.name] = page
	}
	return pages, nil
}

func (s *Store) Probes() (map[string]Probe, error) {
	rows, err := s.db.Query("SELECT lane, asked, dead FROM probes")
	type row struct {
		lane  string
		probe Probe
	}
	found, err := collect(rows, err, func(rows *sql.Rows) (row, error) {
		var r row
		err := rows.Scan(&r.lane, moment{&r.probe.Asked}, moment{&r.probe.Dead})
		return r, err
	})
	if err != nil {
		return nil, err
	}
	probes := make(map[string]Probe, len(found))
	for _, r := range found {
		probes[r.lane] = r.probe
	}
	return probes, nil
}

func (s *Store) Probe(name string, at time.Time) error {
	_, err := s.db.Exec("INSERT INTO probes (lane, asked) VALUES (?, ?) ON CONFLICT (lane) DO UPDATE SET asked = excluded.asked, dead = NULL", name, Stamp(at))
	return err
}

func (s *Store) Unprobe(name string) error {
	_, err := s.db.Exec("DELETE FROM probes WHERE lane = ?", name)
	return err
}

func (s *Store) Own(name string, prs []int) error {
	return own(s.db, name, prs)
}

func own(db execer, name string, prs []int) error {
	for _, pr := range prs {
		if _, err := db.Exec("INSERT INTO owners (pr, lane) VALUES (?, ?) ON CONFLICT (pr) DO UPDATE SET lane = excluded.lane", pr, name); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) Disown(prs []int) error {
	for _, pr := range prs {
		if _, err := s.db.Exec("DELETE FROM owners WHERE pr = ?", pr); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) SetLaneState(name string, state LaneState, at time.Time, retiredBy, reason string) error {
	_, err := s.db.Exec("UPDATE lanes SET state = ?, since = ?, retired_by = coalesce(?, retired_by), reason = coalesce(?, reason) WHERE name = ?", string(state), Stamp(at), text(retiredBy), text(reason), name)
	return err
}

func (s *Store) ReviveLane(name string, at time.Time) error {
	_, err := s.db.Exec("UPDATE lanes SET state = ?, since = ?, retired_by = NULL WHERE name = ?", string(LaneDesired), Stamp(at), name)
	return err
}

func (s *Store) LaneHeard(name string, at time.Time, kind string, seq int64) error {
	_, err := s.db.Exec(
		"INSERT INTO heard (lane, at, kind, seq) VALUES (?, ?, ?, ?) ON CONFLICT (lane) DO UPDATE SET kind = CASE WHEN excluded.at >= heard.at THEN excluded.kind ELSE heard.kind END, node = CASE WHEN excluded.at > heard.at THEN NULL ELSE heard.node END, late_seconds = CASE WHEN excluded.at > heard.at THEN NULL ELSE heard.late_seconds END, at = max(heard.at, excluded.at), seq = max(coalesce(heard.seq, excluded.seq), coalesce(excluded.seq, heard.seq))",
		name, Stamp(at), kind, whole(seq),
	)
	return err
}

func (s *Store) LaneDelivered(name string, at time.Time, node string, late time.Duration) error {
	_, err := s.db.Exec("UPDATE heard SET node = ?, late_seconds = ? WHERE lane = ? AND at = ?", node, int64(late.Seconds()), name, Stamp(at))
	return err
}

func (s *Store) NewestHeard() (time.Time, error) {
	var newest time.Time
	err := s.db.QueryRow("SELECT max(at) FROM heard").Scan(moment{&newest})
	return newest, err
}

func (s *Store) NewestSeq(name string) (int64, error) {
	var seq int64
	err := s.db.QueryRow("SELECT seq FROM heard WHERE lane = ?", name).Scan(nullable[int64]{&seq})
	if errors.Is(err, sql.ErrNoRows) {
		return 0, nil
	}
	return seq, err
}

func (s *Store) SetLaneHandoff(name, doc string, flushed time.Time) error {
	_, err := s.db.Exec("UPDATE lanes SET handoff = ?, flushed = coalesce(?, flushed) WHERE name = ?", doc, when(flushed), name)
	return err
}

func (s *Store) SetLaneAsked(name string, at time.Time) error {
	_, err := s.db.Exec("UPDATE lanes SET asked = ? WHERE name = ?", when(at), name)
	return err
}

func (s *Store) SetLaneWaiting(name string, ask int64, answered time.Time, awaits string) error {
	_, err := s.db.Exec("UPDATE lanes SET waiting = ?, answered = ?, awaits = ? WHERE name = ?", whole(ask), when(answered), text(awaits), name)
	return err
}

type Unread struct {
	Lane   string
	Seq    int64
	At     time.Time
	Author string
	Kind   string
	Woke   bool
	Raised bool
}

func (s *Store) AddUnread(order Unread) error {
	_, err := s.db.Exec("INSERT INTO unread (lane, seq, at, author, kind) VALUES (?, ?, ?, ?, ?) ON CONFLICT (lane, seq) DO NOTHING", order.Lane, order.Seq, Stamp(order.At), order.Author, order.Kind)
	return err
}

func (s *Store) Unread() ([]Unread, error) {
	rows, err := s.db.Query("SELECT lane, seq, at, author, kind, woke, raised FROM unread ORDER BY lane, seq")
	return collect(rows, err, func(rows *sql.Rows) (Unread, error) {
		var order Unread
		err := rows.Scan(&order.Lane, &order.Seq, moment{&order.At}, &order.Author, &order.Kind, &order.Woke, &order.Raised)
		return order, err
	})
}

func (s *Store) ReadUnread(lane string, before time.Time) error {
	_, err := s.db.Exec("DELETE FROM unread WHERE lane = ? AND at < ?", lane, Stamp(before))
	return err
}

func (s *Store) ResolveUnread(seqs []int64) error {
	args := make([]any, len(seqs))
	for i, seq := range seqs {
		args[i] = seq
	}
	_, err := s.db.Exec("DELETE FROM unread WHERE seq IN ("+placeholders(len(seqs))+")", args...)
	return err
}

func (s *Store) KeepUnread(states ...LaneState) error {
	args := make([]any, len(states))
	for i, state := range states {
		args[i] = string(state)
	}
	_, err := s.db.Exec("DELETE FROM unread WHERE lane NOT IN (SELECT name FROM lanes WHERE state IN ("+placeholders(len(states))+"))", args...)
	return err
}

func (s *Store) MarkUnreadWoken(lane string) error {
	_, err := s.db.Exec("UPDATE unread SET woke = 1 WHERE lane = ?", lane)
	return err
}

func (s *Store) MarkUnreadRaised(lane string) error {
	_, err := s.db.Exec("UPDATE unread SET raised = 1 WHERE lane = ?", lane)
	return err
}

func (s *Store) Order(seq int64) error {
	_, err := s.db.Exec("INSERT INTO orders (seq) VALUES (?) ON CONFLICT (seq) DO NOTHING", seq)
	return err
}

func (s *Store) Orders(seqs []int64) ([]int64, error) {
	args := make([]any, len(seqs))
	for i, seq := range seqs {
		args[i] = seq
	}
	rows, err := s.db.Query("SELECT seq FROM orders WHERE seq IN ("+placeholders(len(seqs))+")", args...)
	return collect(rows, err, func(rows *sql.Rows) (int64, error) {
		var seq int64
		err := rows.Scan(&seq)
		return seq, err
	})
}

func (s *Store) SetLaneRole(name, role string) error {
	_, err := s.db.Exec("UPDATE lanes SET role = ? WHERE name = ?", role, name)
	return err
}

func (s *Store) SetLaneLauncher(name, launcher string) error {
	_, err := s.db.Exec("UPDATE lanes SET launcher = ? WHERE name = ?", launcher, name)
	return err
}

func (s *Store) SetLaneStoppedOn(name string, order int64) error {
	_, err := s.db.Exec("UPDATE lanes SET stopped_on = ? WHERE name = ?", whole(order), name)
	return err
}

func (s *Store) SetLanePage(name string, page map[string]any) error {
	encoded, err := encodePage(page)
	if err != nil {
		return err
	}
	_, err = s.db.Exec("UPDATE lanes SET page = ? WHERE name = ?", encoded, name)
	return err
}

func (s *Store) SetLanePRs(name string, prs []int) error {
	if _, err := s.db.Exec("DELETE FROM owners WHERE lane = ?", name); err != nil {
		return err
	}
	return s.Own(name, prs)
}

func (s *Store) strings(query string, args ...any) ([]string, error) {
	rows, err := s.db.Query(query, args...)
	return collect(rows, err, func(rows *sql.Rows) (string, error) {
		var value string
		err := rows.Scan(&value)
		return value, err
	})
}

func collect[T any](rows *sql.Rows, err error, scan func(*sql.Rows) (T, error)) ([]T, error) {
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var found []T
	for rows.Next() {
		value, err := scan(rows)
		if err != nil {
			return nil, err
		}
		found = append(found, value)
	}
	return found, rows.Err()
}

func changed(result sql.Result, err error) (bool, error) {
	if err != nil {
		return false, err
	}
	affected, err := result.RowsAffected()
	return affected == 1, err
}

func placeholders(n int) string {
	return strings.TrimSuffix(strings.Repeat("?, ", n), ", ")
}

func encodePage(page map[string]any) (any, error) {
	if len(page) == 0 {
		return nil, nil
	}
	encoded, err := json.Marshal(page)
	if err != nil {
		return nil, err
	}
	return string(encoded), nil
}

func text(value string) any {
	if value == "" {
		return nil
	}
	return value
}

func whole(value int64) any {
	if value == 0 {
		return nil
	}
	return value
}

func when(at time.Time) any {
	if at.IsZero() {
		return nil
	}
	return Stamp(at)
}

type nullable[T any] struct{ dest *T }

func (n nullable[T]) Scan(src any) error {
	var held sql.Null[T]
	if err := held.Scan(src); err != nil {
		return err
	}
	*n.dest = held.V
	return nil
}

type moment struct{ dest *time.Time }

func (m moment) Scan(src any) error {
	switch value := src.(type) {
	case nil:
		*m.dest = time.Time{}
		return nil
	case string:
		parsed, err := Parse(value)
		*m.dest = parsed
		return err
	default:
		return fmt.Errorf("a stamp column held %T", src)
	}
}
