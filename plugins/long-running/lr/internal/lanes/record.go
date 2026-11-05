package lanes

import (
	"encoding/json"
	"fmt"
	"slices"
	"strconv"
	"strings"
	"time"
	_ "time/tzdata"

	"github.com/yasyf/cc-inbox/client"
)

var pacificZone = func() *time.Location {
	zone, err := time.LoadLocation("America/Los_Angeles")
	if err != nil {
		panic(err)
	}
	return zone
}()

func pacific(at time.Time) string {
	return at.In(pacificZone).Format("3:04 PM")
}

type PRRef int

func (p *PRRef) UnmarshalJSON(data []byte) error {
	var number int
	if err := json.Unmarshal(data, &number); err == nil {
		*p = PRRef(number)
		return nil
	}
	var named string
	if err := json.Unmarshal(data, &named); err != nil {
		return err
	}
	number, err := strconv.Atoi(named[strings.LastIndex(named, "#")+1:])
	if err != nil {
		return fmt.Errorf("pr ref %q: %w", named, err)
	}
	*p = PRRef(number)
	return nil
}

type Refs struct {
	PRs     []PRRef  `json:"prs,omitempty"`
	Lanes   []string `json:"lanes,omitempty"`
	Targets []string `json:"targets,omitempty"`
	CCN     string   `json:"ccn,omitempty"`
}

type Fields struct {
	Attrs map[string]string `json:"attrs,omitempty"`
}

type Record struct {
	Seq          int64     `json:"seq"`
	Origin       string    `json:"origin,omitempty"`
	Lane         string    `json:"lane"`
	Kind         string    `json:"kind"`
	At           time.Time `json:"at"`
	Text         string    `json:"text"`
	Topic        string    `json:"topic,omitempty"`
	To           []string  `json:"to,omitempty"`
	Re           int64     `json:"re,omitempty"`
	Resolves     int64     `json:"resolves,omitempty"`
	AlsoResolves []int64   `json:"also_resolves,omitempty"`
	Refs         Refs      `json:"refs"`
	Fields       Fields    `json:"fields"`
}

func (r Record) PRs() []int {
	numbers := make([]int, 0, len(r.Refs.PRs))
	for _, ref := range r.Refs.PRs {
		numbers = append(numbers, int(ref))
	}
	return numbers
}

func (r Record) Attr(name string) string {
	return r.Fields.Attrs[name]
}

func Addresses(to []string, lane string) bool {
	return slices.Contains(to, lane) || slices.Contains(to, client.Stem(lane))
}

func (r Record) Addressed() map[string]bool {
	addressed := make(map[string]bool, len(r.To))
	for _, to := range r.To {
		name, _, _ := strings.Cut(to, "@")
		addressed[strings.TrimRight(name, ":")] = true
	}
	return addressed
}

func (r Record) resolved() []int64 {
	var seqs []int64
	if r.Resolves != 0 {
		seqs = append(seqs, r.Resolves)
	}
	return append(seqs, r.AlsoResolves...)
}

type Spawn struct {
	Name        string
	Brief       string
	Model       string
	Effort      string
	Role        string
	Placement   string
	SuccessorOf string
}

func or(value, fallback string) string {
	if value != "" {
		return value
	}
	return fallback
}

func spawnedBy(r Record) (Spawn, bool) {
	var name, brief string
	switch {
	case r.Kind == kindSpawn && len(r.Refs.Lanes) > 0:
		name, brief = r.Refs.Lanes[0], r.Attr("brief")
	case r.Kind == "state" && strings.HasPrefix(r.Text, "SPAWNED "):
		words := strings.Fields(r.Text)
		if len(words) < 2 {
			return Spawn{}, false
		}
		name, brief = words[1], r.Attr("brief")
		if brief == "" && len(words) > 2 {
			brief = words[2]
		}
	default:
		return Spawn{}, false
	}
	if !strings.HasPrefix(brief, "/") || strings.HasSuffix(name, "-handoff") || (slices.Contains(helperRoles, r.Attr("role")) && must(BriefOf(brief)).Standing == "") {
		return Spawn{}, false
	}
	return Spawn{
		Name: name, Brief: brief,
		Model: or(r.Attr("model"), "opus"), Effort: or(r.Attr("effort"), "high"), Role: r.Attr("role"),
		Placement: r.Attr("placement"), SuccessorOf: r.Attr("successor-of"),
	}, true
}

func isRequest(r Record) bool {
	return r.Kind == kindRequest && r.Addressed()[Self] && slices.Contains(Verbs, r.Attr("verb")) && r.Attr("lane") != ""
}

func citedIn(r Record) string {
	if found := lastRead.FindStringSubmatch(r.Text); found != nil {
		return found[1]
	}
	return ""
}
