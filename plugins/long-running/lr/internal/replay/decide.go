package replay

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	tick      = 30 * time.Second
	kindSpawn = "spawn"
)

var (
	launchedLine = regexp.MustCompile(`^LAUNCHED \S+ ([\w.-]+): dispatch (ctx_\w+)`)
	endedLine    = regexp.MustCompile(`^OUTCOME \S+ ([\w.-]+): worker_done \w+ dispatch=(ctx_\w+)`)
)

type WhoRow struct {
	lanes.Row
	AnnouncedAt time.Time `json:"announced_at"`
}

type PR struct {
	PR      int                `json:"pr"`
	Patches map[string]*string `json:"patches"`
}

type Input struct {
	Records []lanes.Record `json:"records"`
	Who     []WhoRow       `json:"who"`
	Since   string         `json:"since,omitempty"`
	PRs     []PR           `json:"prs"`
}

type Decided struct {
	Relaunched []string `json:"relaunched"`
	Held       []string `json:"held"`
	Woken      []string `json:"woken"`
	Unread     []string `json:"unread"`
	Untyped    []int64  `json:"untyped"`
	Refused    []int    `json:"refused"`
}

func Decide(given Input) (Decided, error) {
	decided, err := decideLanes(given)
	if err != nil {
		return Decided{}, err
	}
	decided.Refused = refusals(given.PRs)
	return decided, nil
}

func refusals(prs []PR) []int {
	refused := make([]int, 0, len(prs))
	for _, entry := range prs {
		if gates.TestOnlyOffender(entry.Patches) != "" {
			refused = append(refused, entry.PR)
		}
	}
	slices.Sort(refused)
	return refused
}

func stubbed(records []lanes.Record, dir string) ([]lanes.Record, error) {
	out := make([]lanes.Record, 0, len(records))
	for _, record := range records {
		brief := record.Attr("brief")
		spawn := record.Kind == kindSpawn && brief != ""
		if spawn || (record.Kind == "state" && strings.HasPrefix(record.Text, "SPAWNED ")) {
			words := strings.Fields(record.Text)
			name := ""
			switch {
			case spawn && len(record.Refs.Lanes) > 0:
				name = record.Refs.Lanes[0]
			case !spawn:
				name = words[1]
			}
			written := brief
			if written == "" && len(words) > 2 {
				written = words[2]
			}
			stub := filepath.Join(dir, "spawned", filepath.Base(filepath.Dir(written)), filepath.Base(written))
			if err := os.MkdirAll(filepath.Dir(stub), 0o750); err != nil {
				return nil, err
			}
			if err := os.WriteFile(stub, fmt.Appendf(nil, "# %s\nRegister slice: lane=%s step=all\n", name, name), 0o600); err != nil {
				return nil, err
			}
			if !spawn {
				record.Text = fmt.Sprintf("SPAWNED %s %s", name, stub)
			}
			attrs := map[string]string{}
			for key, value := range record.Fields.Attrs {
				attrs[key] = value
			}
			attrs["brief"] = stub
			record.Fields.Attrs = attrs
		}
		out = append(out, record)
	}
	return out, nil
}

type dispatch struct {
	lane     string
	launched time.Time
	ended    time.Time
}

func dispatchesOf(feed []lanes.Record) []dispatch {
	ended := map[string]time.Time{}
	for _, record := range feed {
		if found := endedLine.FindStringSubmatch(record.Text); found != nil {
			ended[found[2]] = record.At
		}
	}
	out := make([]dispatch, 0, len(ended))
	for _, record := range feed {
		if found := launchedLine.FindStringSubmatch(record.Text); found != nil {
			out = append(out, dispatch{found[1], record.At, ended[found[2]]})
		}
	}
	return out
}

func whoAt(rows []WhoRow, orca []dispatch, stoodDown map[string]time.Time, now time.Time) []lanes.Row {
	out := make([]lanes.Row, 0, len(orca)+len(rows))
	for _, one := range orca {
		if one.launched.After(now) {
			continue
		}
		live := one.ended.IsZero() || one.ended.After(now)
		seen := one.launched
		if live {
			seen = now
		}
		out = append(out, lanes.Row{Lane: one.lane, Live: live, SeenAt: seen})
	}
	for _, row := range rows {
		announced := row.AnnouncedAt
		if announced.IsZero() {
			announced = row.SeenAt
		}
		if announced.After(now) {
			continue
		}
		copied := row.Row
		at, marked := stoodDown[row.Lane]
		copied.StoodDown = row.StoodDown && marked && !at.After(now)
		if row.SeenAt.After(now) {
			copied.Live, copied.SeenAt = true, now
		}
		out = append(out, copied)
	}
	return out
}

func decideLanes(given Input) (Decided, error) {
	decided := Decided{Relaunched: []string{}, Held: []string{}, Woken: []string{}, Unread: []string{}, Untyped: []int64{}, Refused: []int{}}
	if len(given.Records) == 0 {
		return decided, nil
	}
	dir, err := os.MkdirTemp("", "lr-replay")
	if err != nil {
		return decided, err
	}
	defer func() { _ = os.RemoveAll(dir) }()
	feed, err := stubbed(given.Records, dir)
	if err != nil {
		return decided, err
	}
	slices.SortFunc(feed, func(a, b lanes.Record) int {
		switch {
		case a.Seq < b.Seq:
			return -1
		case a.Seq > b.Seq:
			return 1
		}
		return 0
	})
	stoodDown := map[string]time.Time{}
	for _, record := range feed {
		if record.Kind != "stand-down" {
			continue
		}
		for _, to := range record.To {
			name, _, _ := strings.Cut(to, "@")
			if _, seen := stoodDown[name]; !seen {
				stoodDown[name] = record.At
			}
		}
	}
	orca := dispatchesOf(feed)
	now := feed[0].At.UTC()
	if given.Since != "" {
		since, err := time.Parse(time.RFC3339Nano, given.Since)
		if err != nil {
			return decided, err
		}
		if since.After(now) {
			now = since.UTC()
		}
	}
	shown := 0
	var posts []lanes.Post
	woken, relaunched, held := map[string]bool{}, map[string]bool{}, map[string]bool{}
	deps := lanes.Deps{
		Drive: "replay", Checkout: dir, Briefs: filepath.Join(dir, "lanes"),
		Desks:        func() (map[string]string, error) { return nil, nil },
		RetireAfter:  config.RetireSilent,
		OpenBranches: func(*store.Store) (map[string][]int, error) { return nil, nil },
		OpenWork:     func() ([]lanes.Record, error) { return nil, nil },
		Owed:         func() ([]deletions.Owed, error) { return nil, nil },
		Present:      func(rows []deletions.Owed) ([]deletions.Owed, error) { return rows, nil },
		PublishNotes: func(string) error { return nil },
		Gaps:         func() ([]string, error) { return nil, nil },
		Orders:       func(*store.Store) ([]owner.Row, error) { return nil, nil },
		Promises:     func() ([]lanes.Promise, error) { return nil, nil },
		Records: func(since int64) ([]lanes.Record, error) {
			start := shown
			for shown < len(feed) && !feed[shown].At.After(now) {
				shown++
			}
			visible := make([]lanes.Record, 0, shown-start)
			for _, record := range feed[start:shown] {
				if record.Seq > since {
					visible = append(visible, record)
				}
			}
			return visible, nil
		},
		Who:        func([]string) ([]lanes.Row, error) { return whoAt(given.Who, orca, stoodDown, now), nil },
		Tokens:     func(lanes.Row) int { return 0 },
		Transcript: func(lanes.Row) string { return "" },
		Post:       func(post lanes.Post) error { posts = append(posts, post); return nil },
		Launch:     func(string, string, string, string) error { return nil },
		Withdraw:   func(string) error { return nil },
		Wake: func(name string, _ lanes.Row, _ map[string]any) (bool, error) {
			woken[name] = true
			return false, nil
		},
		PRStates: func([]int) (map[int]string, error) { return map[int]string{}, nil },
		Held:     func() (map[int]bool, error) { return nil, nil },
	}
	s, err := store.Memory()
	if err != nil {
		return decided, err
	}
	defer func() { _ = s.Close() }()
	if err := s.Advance(lanes.CursorName, feed[0].Seq-1); err != nil {
		return decided, err
	}
	end := feed[len(feed)-1].At.UTC()
	for {
		out, err := lanes.Tick(s, deps, now, 1)
		if err != nil {
			return decided, err
		}
		for _, one := range out.Relaunched {
			relaunched[one.Old] = true
		}
		for _, name := range out.Held {
			held[name] = true
		}
		if !now.Before(end) {
			break
		}
		now = now.Add(tick)
		if now.After(end) {
			now = end
		}
	}
	decided.Relaunched, decided.Held, decided.Woken = sortedNames(relaunched), sortedNames(held), sortedNames(woken)
	for _, post := range posts {
		if post.Kind != "defect" {
			continue
		}
		if post.LaneRef != "" {
			decided.Unread = append(decided.Unread, post.LaneRef)
		} else if post.Re != 0 {
			decided.Untyped = append(decided.Untyped, post.Re)
		}
	}
	slices.Sort(decided.Unread)
	slices.Sort(decided.Untyped)
	return decided, nil
}

func sortedNames(set map[string]bool) []string {
	names := make([]string, 0, len(set))
	for name := range set {
		names = append(names, name)
	}
	slices.Sort(names)
	return names
}
