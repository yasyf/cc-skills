package update

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"
)

const (
	progressDesk = "progress-desk"
	stepsFile    = "steps.json"
	snapshotFile = "owner-update.json"
	labelWords   = 2
)

var facetNames = []string{"who", "blocker", "state"}

type Step struct {
	ID         string   `json:"id"`
	Label      string   `json:"label"`
	Title      string   `json:"title"`
	Section    string   `json:"section"`
	State      string   `json:"state"`
	Tone       string   `json:"tone"`
	Delivers   string   `json:"delivers"`
	Who        []string `json:"who"`
	Blocker    *string  `json:"blocker"`
	Next       string   `json:"next"`
	Unblocking string   `json:"unblocking"`
	WentWrong  string   `json:"went_wrong"`
	PRs        []int    `json:"prs"`
	Lanes      []string `json:"lanes"`
}

type ReleaseNow struct {
	Name  string `json:"name"`
	State string `json:"state"`
}

type Now struct {
	Blocker  *string      `json:"blocker"`
	Release  *ReleaseNow  `json:"release"`
	Releases []ReleaseNow `json:"releases"`
}

type Published struct {
	Updated time.Time `json:"updated"`
	Steps   []Step    `json:"steps"`
	FYI     []FYI     `json:"fyi"`
	Now     Now       `json:"now"`
}

type Snapshot struct {
	At   time.Time           `json:"at"`
	Rows map[string][]string `json:"rows"`
}

func stepEntry(title, sectionTitle string, row Row, lanes []Lane) Step {
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
	var blocker *string
	if row.Blocks != nothingBlocks {
		blocker = &row.Blocks
	}
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
		Next:       row.Unblock,
		Unblocking: row.Unblock,
		WentWrong:  row.Wrong,
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
		rows[i] = ReleaseNow{row.Name, row.State}
	}
	now := Now{Releases: rows}
	if len(rows) > 0 {
		now.Release = &rows[len(rows)-1]
	}
	return now
}

func facets(row Row) []string {
	state := "open"
	if row.Done {
		state = "done"
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
	var out []string
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
	if err := writeJSON(filepath.Join(desk, stepsFile), r.Published); err != nil {
		return err
	}
	if !sent {
		return nil
	}
	return writeJSON(filepath.Join(desk, snapshotFile), r.Snapshot)
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
