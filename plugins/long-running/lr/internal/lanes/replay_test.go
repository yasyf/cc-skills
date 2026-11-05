package lanes

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const replayTick = 30 * time.Second

var (
	launchedLine = regexp.MustCompile(`^LAUNCHED \S+ ([\w.-]+): dispatch (ctx_\w+)`)
	endedLine    = regexp.MustCompile(`^OUTCOME \S+ [\w.-]+: worker_done \w+ dispatch=(ctx_\w+)`)
)

func jsonInto(text string, into any) error {
	return json.Unmarshal([]byte(text), into)
}

type replayRow struct {
	Row
	AnnouncedAt time.Time `json:"announced_at"`
}

type replayFixture struct {
	Class  string           `json:"class"`
	Expect map[string][]any `json:"expect"`
	Input  struct {
		Records []Record    `json:"records"`
		Who     []replayRow `json:"who"`
		Since   string      `json:"since"`
	} `json:"input"`
}

func parseAt(t *testing.T, text string) time.Time {
	t.Helper()
	at, err := time.Parse(time.RFC3339Nano, text)
	if err != nil {
		t.Fatal(err)
	}
	return at.UTC()
}

func stubBriefs(t *testing.T, records []Record, dir string) []Record {
	t.Helper()
	out := make([]Record, 0, len(records))
	for _, r := range records {
		brief := r.Attr("brief")
		spawn := r.Kind == kindSpawn && brief != ""
		if spawn || (r.Kind == "state" && strings.HasPrefix(r.Text, "SPAWNED ")) {
			words := strings.Fields(r.Text)
			name := ""
			switch {
			case spawn && len(r.Refs.Lanes) > 0:
				name = r.Refs.Lanes[0]
			case !spawn:
				name = words[1]
			}
			written := brief
			if written == "" && len(words) > 2 {
				written = words[2]
			}
			stub := filepath.Join(dir, "spawned", filepath.Base(filepath.Dir(written)), filepath.Base(written))
			if err := os.MkdirAll(filepath.Dir(stub), 0o750); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(stub, []byte(fmt.Sprintf("# %s\nRegister slice: lane=%s step=all\n", name, name)), 0o600); err != nil {
				t.Fatal(err)
			}
			if !spawn {
				r.Text = fmt.Sprintf("SPAWNED %s %s", name, stub)
			}
			attrs := map[string]string{}
			for key, value := range r.Fields.Attrs {
				attrs[key] = value
			}
			attrs["brief"] = stub
			r.Fields.Attrs = attrs
		}
		out = append(out, r)
	}
	return out
}

type dispatch struct {
	lane     string
	launched time.Time
	ended    time.Time
}

func dispatchesOf(feed []Record) []dispatch {
	ended := map[string]time.Time{}
	for _, r := range feed {
		if found := endedLine.FindStringSubmatch(r.Text); found != nil {
			ended[found[1]] = r.At
		}
	}
	var out []dispatch
	for _, r := range feed {
		if found := launchedLine.FindStringSubmatch(r.Text); found != nil {
			out = append(out, dispatch{found[1], r.At, ended[found[2]]})
		}
	}
	return out
}

func whoAt(rows []replayRow, orca []dispatch, stoodDown map[string]time.Time, now time.Time) []Row {
	var out []Row
	for _, one := range orca {
		if one.launched.After(now) {
			continue
		}
		live := one.ended.IsZero() || one.ended.After(now)
		seen := one.launched
		if live {
			seen = now
		}
		out = append(out, Row{Lane: one.lane, Live: live, SeenAt: seen})
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

func decided(t *testing.T, fixture replayFixture) map[string][]any {
	t.Helper()
	dir := t.TempDir()
	feed := stubBriefs(t, fixture.Input.Records, dir)
	slices.SortFunc(feed, func(a, b Record) int { return int(a.Seq - b.Seq) })
	stoodDown := map[string]time.Time{}
	for _, r := range feed {
		if r.Kind == "stand-down" {
			for _, to := range r.To {
				name, _, _ := strings.Cut(to, "@")
				if _, seen := stoodDown[name]; !seen {
					stoodDown[name] = r.At
				}
			}
		}
	}
	orca := dispatchesOf(feed)
	now := feed[0].At.UTC()
	if fixture.Input.Since != "" && parseAt(t, fixture.Input.Since).After(now) {
		now = parseAt(t, fixture.Input.Since)
	}
	shown := 0
	var posts []Post
	woken, relaunched, held := map[string]bool{}, map[string]bool{}, map[string]bool{}
	deps := Deps{
		Drive: "replay", Checkout: dir, Briefs: filepath.Join(dir, "lanes"),
		Desks:        func() (map[string]string, error) { return nil, nil },
		RetireAfter:  config.RetireSilent,
		OpenBranches: func(*store.Store) (map[string][]int, error) { return nil, nil },
		OpenWork:     func() ([]Record, error) { return nil, nil },
		Owed:         func() ([]deletions.Owed, error) { return nil, nil },
		Present:      func(rows []deletions.Owed) ([]deletions.Owed, error) { return rows, nil },
		PublishNotes: func(string) error { return nil },
		Gaps:         func() ([]string, error) { return nil, nil },
		Orders:       func(*store.Store) ([]owner.Row, error) { return nil, nil },
		Promises:     func() ([]Promise, error) { return nil, nil },
		Records: func(since int64) ([]Record, error) {
			start := shown
			for shown < len(feed) && !feed[shown].At.After(now) {
				shown++
			}
			var visible []Record
			for _, r := range feed[start:shown] {
				if r.Seq > since {
					visible = append(visible, r)
				}
			}
			return visible, nil
		},
		Who:        func([]string) ([]Row, error) { return whoAt(fixture.Input.Who, orca, stoodDown, now), nil },
		Tokens:     func(Row) int { return 0 },
		Transcript: func(Row) string { return "" },
		Post:       func(p Post) error { posts = append(posts, p); return nil },
		Launch:     func(string, string, string, string, string) error { return nil },
		Withdraw:   func(string) error { return nil },
		Wake: func(name string, _ Row, _ map[string]any) (bool, error) {
			woken[name] = true
			return false, nil
		},
		PRStates: func([]int) (map[int]string, error) { return map[int]string{}, nil },
		Held:     func() (map[int]bool, error) { return nil, nil },
	}
	s := memory(t)
	if err := s.Advance(CursorName, feed[0].Seq-1); err != nil {
		t.Fatal(err)
	}
	end := feed[len(feed)-1].At.UTC()
	for {
		out, err := Tick(s, deps, now, 1)
		if err != nil {
			t.Fatal(err)
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
		now = now.Add(replayTick)
		if now.After(end) {
			now = end
		}
	}
	result := map[string][]any{"relaunched": sorted(relaunched), "held": sorted(held), "woken": sorted(woken), "unread": {}, "untyped": {}}
	for _, p := range posts {
		if p.Kind != "defect" {
			continue
		}
		if strings.Contains(p.Text, unreadMark) {
			result["unread"] = append(result["unread"], p.LaneRef)
		} else if p.Re != 0 {
			result["untyped"] = append(result["untyped"], float64(p.Re))
		}
	}
	return result
}

func sorted(set map[string]bool) []any {
	names := make([]string, 0, len(set))
	for name := range set {
		names = append(names, name)
	}
	slices.Sort(names)
	out := make([]any, 0, len(names))
	for _, name := range names {
		out = append(out, name)
	}
	return out
}

func TestReplayFixtures(t *testing.T) {
	paths, err := filepath.Glob("testdata/replay/supervisor-*.json")
	if err != nil || len(paths) == 0 {
		t.Fatalf("fixtures %v err %v", paths, err)
	}
	for _, path := range paths {
		t.Run(filepath.Base(path), func(t *testing.T) {
			data, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			var fixture replayFixture
			if err := json.Unmarshal(data, &fixture); err != nil {
				t.Fatal(err)
			}
			got := decided(t, fixture)
			for key, want := range fixture.Expect {
				if want == nil {
					want = []any{}
				}
				equal(t, append([]any{}, got[key]...), append([]any{}, want...))
			}
		})
	}
}
