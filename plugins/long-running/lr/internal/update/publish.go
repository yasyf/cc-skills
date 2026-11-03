package update

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	progressDesk = "progress-desk"
	stepsFile    = "steps.json"
	snapshotFile = "owner-update.json"
	labelWords   = 2
	stateOpen    = "open"
	stateDone    = "done"
	plannedWait  = "A planned task is waiting on other work."
	noWorker     = "Nobody is on it; the coordinator puts someone on it next."
	nothingYet   = "Nothing so far."
	noPlanYet    = "none yet"
	leftTheTable = "no longer open (landed, answered or closed)"
)

var facetNames = []string{"who", "blocker", "state"}

type StepDetail struct {
	Blocker   string `json:"blocker"`
	Next      string `json:"next"`
	WentWrong string `json:"went_wrong"`
}

type Step struct {
	ID         string     `json:"id"`
	Label      string     `json:"label"`
	Title      string     `json:"title"`
	Section    string     `json:"section"`
	State      string     `json:"state"`
	Tone       string     `json:"tone"`
	Delivers   string     `json:"delivers"`
	Who        []string   `json:"who"`
	Blocker    *string    `json:"blocker"`
	Next       string     `json:"next"`
	Unblocking string     `json:"unblocking"`
	WentWrong  string     `json:"went_wrong"`
	Detail     StepDetail `json:"detail"`
	PRs        []int      `json:"prs"`
	Lanes      []string   `json:"lanes"`
}

type ReleaseNow struct {
	ID         string   `json:"id"`
	Name       string   `json:"name"`
	State      string   `json:"state"`
	Parts      []string `json:"parts"`
	Lanes      []string `json:"lanes"`
	Blocker    *string  `json:"blocker"`
	Unblocking string   `json:"unblocking"`
	WentWrong  string   `json:"went_wrong"`
}

type Now struct {
	Blocker  *string      `json:"blocker"`
	Release  *ReleaseNow  `json:"release"`
	Releases []ReleaseNow `json:"releases"`
}

type Published struct {
	Updated time.Time       `json:"updated"`
	Steps   []Step          `json:"steps"`
	FYI     []FYI           `json:"fyi"`
	Now     Now             `json:"now"`
	PRState json.RawMessage `json:"pr_state,omitempty"`
	History json.RawMessage `json:"history,omitempty"`
}

type Snapshot struct {
	At   time.Time           `json:"at"`
	Rows map[string][]string `json:"rows"`
}

func blockerOf(row Row) *string {
	if row.Blocks == nothingBlocks {
		return nil
	}
	return &row.Blocks
}

func (v view) plainBlocker(on []string, row Row) *string {
	var since time.Time
	for _, lane := range on {
		if wait, found := v.waits[lane]; found && (since.IsZero() || wait.At.Before(since)) {
			since = wait.At
		}
	}
	words := plannedWait
	switch {
	case !since.IsZero():
		words = fmt.Sprintf("Waiting on a coordinator decision since %s.", pacific(since))
	case row.Blocks == nothingBlocks:
		return nil
	}
	return &words
}

func (v view) plainNext(on []string) string {
	if len(on) == 0 {
		return noWorker
	}
	due := v.byLane[on[0]].Heard
	for _, lane := range on {
		if heard := v.byLane[lane].Heard; heard.Before(due) {
			due = heard
		}
	}
	workers := "workers are"
	if len(on) == 1 {
		workers = "worker is"
	}
	return fmt.Sprintf("%d %s on it; the next word is due by %s.", len(on), workers, pacific(due.Add(store.StaleAfter)))
}

func (v view) plainWrong(lanes []string) string {
	var failed []string
	for _, row := range v.retro {
		if slices.Contains(lanes, row.Owner) && row.State != retroLive {
			failed = append(failed, fmt.Sprintf("%s. Prevention: %s.", strings.TrimRight(row.Failure, "."), orWord(row.Mechanism, noPlanYet)))
		}
	}
	return orWord(strings.Join(failed, " "), nothingYet)
}

func (v view) plain(lanes []string, row Row) (blocker *string, next, wentWrong string) {
	if row.Done {
		return nil, "Done.", "Nothing open."
	}
	on := slices.DeleteFunc(unique(lanes), func(lane string) bool { return !v.live(lane) })
	return v.plainBlocker(on, row), v.plainNext(on), v.plainWrong(lanes)
}

func (v view) stepEntry(title, sectionTitle string, row Row, lanes []Lane) Step {
	tone := "moving"
	switch {
	case row.Done:
		tone = "done"
	case row.Who == nobody:
		tone = "later"
	}
	who := []string{}
	if row.Who != nobody {
		who = strings.Split(row.Who, ", ")
	}
	blocker, next, wentWrong := v.plain(namesOf(lanes), row)
	prs := make([]int, 0, len(lanes))
	for _, lane := range lanes {
		prs = append(prs, lane.PRs...)
	}
	slices.Sort(prs)
	words := strings.Fields(title)
	return Step{
		Label:      strings.Join(words[:min(labelWords, len(words))], " "),
		Title:      title,
		Section:    sectionTitle,
		State:      row.State,
		Tone:       tone,
		Delivers:   row.Delivers,
		Who:        who,
		Blocker:    blocker,
		Next:       next,
		Unblocking: next,
		WentWrong:  wentWrong,
		Detail:     StepDetail{row.Blocks, row.Unblock, row.Wrong},
		PRs:        slices.Compact(prs),
		Lanes:      namesOf(lanes),
	}
}

func numbered(steps []Step) []Step {
	for i := range steps {
		steps[i].ID = fmt.Sprintf("s%d", i)
	}
	return steps
}

func nowOf(releases []Row) Now {
	rows := make([]ReleaseNow, len(releases))
	for i, row := range releases {
		rows[i] = ReleaseNow{"release:" + row.Name, row.Name, row.State, []string{}, []string{row.Name}, blockerOf(row), row.Unblock, row.Wrong}
	}
	now := Now{Releases: rows}
	if len(rows) > 0 {
		now.Release = &rows[len(rows)-1]
	}
	return now
}

func facets(row Row) []string {
	state := stateOpen
	if row.Done {
		state = stateDone
	}
	return []string{row.Who, row.Blocks, state}
}

func snapshotKey(found section, row Row) string {
	return found.title + "|" + row.Name
}

func takeSnapshot(sections []section, at time.Time) Snapshot {
	rows := map[string][]string{}
	for _, found := range sections {
		for _, row := range found.rows {
			rows[snapshotKey(found, row)] = facets(row)
		}
	}
	return Snapshot{at, rows}
}

func sinceLines(sections []section, before map[string][]string) []string {
	current := takeSnapshot(sections, time.Time{}).Rows
	var out []string
	for _, key := range slices.Sorted(maps.Keys(before)) {
		if _, stays := current[key]; !stays && before[key][2] != stateDone {
			_, name, _ := strings.Cut(key, "|")
			out = append(out, fmt.Sprintf("- %s: %s", name, leftTheTable))
		}
	}
	for _, found := range sections {
		for _, row := range found.rows {
			old, known := before[snapshotKey(found, row)]
			if !known {
				out = append(out, "- new: "+row.Name)
				continue
			}
			var moved []string
			for i, value := range facets(row) {
				if old[i] != value {
					moved = append(moved, fmt.Sprintf("%s %s -> %s", facetNames[i], clip(old[i]), clip(value)))
				}
			}
			if len(moved) > 0 {
				out = append(out, fmt.Sprintf("- %s: %s", row.Name, strings.Join(moved, "; ")))
			}
		}
	}
	return out
}

func ReadSnapshot(stateDir string) (Snapshot, error) {
	data, err := os.ReadFile(filepath.Join(stateDir, progressDesk, snapshotFile))
	if errors.Is(err, fs.ErrNotExist) {
		return Snapshot{}, nil
	}
	if err != nil {
		return Snapshot{}, err
	}
	var snapshot Snapshot
	if err := json.Unmarshal(data, &snapshot); err != nil {
		return Snapshot{}, fmt.Errorf("%s: %w", snapshotFile, err)
	}
	return snapshot, nil
}

func (r Report) Publish(stateDir string, sent bool) error {
	desk := filepath.Join(stateDir, progressDesk)
	if err := os.MkdirAll(desk, 0o700); err != nil {
		return err
	}
	steps := filepath.Join(desk, stepsFile)
	published, err := carryForward(r.Published, steps)
	if err != nil {
		return err
	}
	if err := writeJSON(steps, published); err != nil {
		return err
	}
	if !sent {
		return nil
	}
	return writeJSON(filepath.Join(desk, snapshotFile), r.Snapshot)
}

func carryForward(published Published, path string) (Published, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return published, nil
	}
	if err != nil {
		return Published{}, err
	}
	var kept struct {
		PRState json.RawMessage `json:"pr_state"`
		History json.RawMessage `json:"history"`
	}
	if err := json.Unmarshal(data, &kept); err != nil {
		return Published{}, fmt.Errorf("%s: %w", stepsFile, err)
	}
	published.PRState, published.History = kept.PRState, kept.History
	return published, nil
}

func writeJSON(path string, value any) error {
	encoded, err := json.Marshal(value)
	if err != nil {
		return err
	}
	scratch := path + ".tmp"
	if err := os.WriteFile(scratch, encoded, 0o600); err != nil {
		return err
	}
	return os.Rename(scratch, path)
}
