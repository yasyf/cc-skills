package prompts

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch/screen"
)

type State string

const (
	Approval    = State(screen.Approval)
	Question    = State(screen.Question)
	Stale       = State("stale")
	Unknown     = State("unknown")
	Unreachable = State("unreachable")
	Clear       = State("clear")
	down        = State("down")
)

func (s State) prompt() bool {
	return s == Approval || s == Question
}

func (s State) unseen() bool {
	return s == Unknown || s == Unreachable
}

const (
	stampLayout = "2006-01-02T15:04:05Z"
	downAfter   = 2 * time.Minute
)

func stamp(at time.Time) string {
	return at.UTC().Format(stampLayout)
}

func parseStamp(text string) time.Time {
	at, err := time.Parse(stampLayout, text)
	if err != nil {
		panic(fmt.Errorf("prompt watch stamp: %w", err))
	}
	return at
}

type Alert struct {
	Seq     int64
	Episode string
}

func (a Alert) MarshalJSON() ([]byte, error) {
	if a.Seq != 0 {
		return json.Marshal(a.Seq)
	}
	return json.Marshal(a.Episode)
}

func (a *Alert) UnmarshalJSON(data []byte) error {
	if err := json.Unmarshal(data, &a.Seq); err == nil {
		return nil
	}
	return json.Unmarshal(data, &a.Episode)
}

type Wake struct {
	At      string   `json:"at"`
	Request string   `json:"request,omitempty"`
	Stages  []string `json:"stages,omitempty"`
	Reached string   `json:"reached,omitempty"`
	Error   string   `json:"error,omitempty"`
}

type Row struct {
	Role            string           `json:"role"`
	Name            string           `json:"name"`
	Terminal        string           `json:"terminal"`
	Environment     string           `json:"environment"`
	State           State            `json:"state"`
	StateAt         string           `json:"state_at"`
	Detail          string           `json:"detail"`
	Since           *int64           `json:"since"`
	Alerts          map[string]Alert `json:"alerts"`
	Unseen          int              `json:"unseen"`
	UnseenState     State            `json:"unseen_state,omitempty"`
	UnreadCapture   string           `json:"unread_capture,omitempty"`
	Reported        State            `json:"reported,omitempty"`
	Readable        int              `json:"readable,omitempty"`
	Episode         string           `json:"episode,omitempty"`
	ToldAt          string           `json:"told_at,omitempty"`
	Reminded        int64            `json:"reminded,omitempty"`
	Wake            *Wake            `json:"wake,omitempty"`
	Capture         string           `json:"capture,omitempty"`
	SupervisorInput string           `json:"supervisor_input,omitempty"`
	ShownAt         string           `json:"shown_at,omitempty"`
}

type saved struct {
	Drive    string          `json:"drive"`
	At       string          `json:"at"`
	PID      int             `json:"pid"`
	Subjects map[string]*Row `json:"subjects"`
}

func where(row *Row) string {
	terminal := row.Terminal
	if terminal == "" {
		terminal = "unresolved"
	}
	if row.Environment == "" {
		return "terminal=" + terminal
	}
	return "terminal=" + terminal + " environment=" + row.Environment
}

func loadState(path string) (saved, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return saved{Subjects: map[string]*Row{}}, nil
	}
	if err != nil {
		return saved{}, err
	}
	var state saved
	if err := json.Unmarshal(data, &state); err != nil {
		return saved{}, fmt.Errorf("%s: %w", path, err)
	}
	return state, nil
}

func encode(value any) ([]byte, error) {
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	err := encoder.Encode(value)
	return out.Bytes(), err
}

func writeAtomic(path string, value any) error {
	data, err := encode(value)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), ownerOnlyDir); err != nil {
		return err
	}
	staged := filepath.Join(filepath.Dir(path), "."+filepath.Base(path)+"."+strconv.Itoa(os.Getpid()))
	if err := os.WriteFile(staged, data, ownerOnly); err != nil {
		return err
	}
	return os.Rename(staged, path)
}

type ViewRow struct {
	Name   string `json:"name"`
	Role   string `json:"role"`
	State  State  `json:"state"`
	Detail string `json:"detail"`
	Line   string `json:"line"`
}

type Shown struct {
	Drive  string    `json:"drive"`
	Header string    `json:"header"`
	Rows   []ViewRow `json:"rows"`
}

func (s Shown) Text() string {
	lines := make([]string, 0, 1+len(s.Rows))
	lines = append(lines, s.Header)
	for _, row := range s.Rows {
		lines = append(lines, row.Line)
	}
	return strings.Join(lines, "\n")
}

func downRow(e Entry, detail string) ViewRow {
	detail = fmt.Sprintf("%s; check it with `lr watch prompts --drive %s`", detail, e.Drive)
	return ViewRow{e.Drive, roleWatch, down, detail, fmt.Sprintf("PROMPT-WATCH-DOWN %s: %s", e.Drive, detail)}
}

func View(e Entry, now time.Time) (Shown, error) {
	data, err := os.ReadFile(e.statePath())
	if errors.Is(err, fs.ErrNotExist) {
		return Shown{e.Drive, fmt.Sprintf("prompt-watch %s has never polled", e.Drive), []ViewRow{downRow(e, "it has never polled")}}, nil
	}
	if err != nil {
		return Shown{}, err
	}
	var state saved
	if err := json.Unmarshal(data, &state); err != nil {
		return Shown{}, fmt.Errorf("%s: %w", e.statePath(), err)
	}
	age := now.Sub(parseStamp(state.At))
	found := make([]*Row, 0, len(state.Subjects))
	for _, row := range state.Subjects {
		found = append(found, row)
	}
	slices.SortFunc(found, func(a, b *Row) int {
		return strings.Compare(string(a.State)+"\x00"+a.Name, string(b.State)+"\x00"+b.Name)
	})
	counts := make([]string, 0, 6)
	for _, name := range []State{Approval, Question, Stale, Unknown, Unreachable, Clear} {
		count := 0
		for _, row := range found {
			if row.State == name {
				count++
			}
		}
		counts = append(counts, fmt.Sprintf("%d %s", count, name))
	}
	rows := []ViewRow{}
	if age > downAfter {
		rows = append(rows, downRow(e, fmt.Sprintf("last poll %dm ago, so the states below are old", int(age.Minutes()))))
	}
	for _, row := range found {
		if row.State == Clear {
			continue
		}
		minutes := int(now.Sub(parseStamp(row.StateAt)).Minutes())
		label := fmt.Sprintf("PROMPT-%s %s %s", strings.ToUpper(string(row.State)), row.Name, row.Role)
		if row.State.prompt() {
			label = fmt.Sprintf("WAITING-ON-PROMPT %s %s %s", row.Name, row.Role, row.State)
		}
		rows = append(rows, ViewRow{row.Name, row.Role, row.State, row.Detail, fmt.Sprintf("%s %dm %s: %s", label, minutes, where(row), row.Detail)})
	}
	return Shown{e.Drive, fmt.Sprintf("prompt-watch %s polled %ds ago: %s", e.Drive, int(age.Seconds()), strings.Join(counts, ", ")), rows}, nil
}
