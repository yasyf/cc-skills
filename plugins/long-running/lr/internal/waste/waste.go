package waste

import (
	"cmp"
	"context"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	Window  = time.Hour
	ByteCap = 4096
	lineCap = 240

	signalDead        = "dead-recipient"
	signalFanOut      = "fan-out"
	signalInjected    = "injected"
	signalSkips       = "hook-skips"
	signalChore       = "chore"
	signalRepeatRead  = "repeat-read"
	signalCost        = "cost"
	signalDuplicate   = "duplicate-ask"
	signalRace        = "race-warning"
	signalUninstalled = "uninstalled"
	signalUnproven    = "unproven"
	signalOwedReply   = "owed-reply"
	signalCompactions = "compactions"
)

type Bus interface {
	Tail(ctx context.Context, q client.Query) ([]client.Record, error)
	Who(ctx context.Context, drive string) ([]client.Peer, error)
}

type Sources struct {
	Bus     Bus
	Runner  shell.Runner
	Retired func() ([]store.Lane, error)
	Running *Running
}

func Live(bus Bus, runner shell.Runner, s *store.Store, running *Running) Sources {
	return Sources{
		Bus:     bus,
		Runner:  runner,
		Retired: func() ([]store.Lane, error) { return s.Lanes(store.LaneRetired) },
		Running: running,
	}
}

type Finding struct {
	Signal string
	Key    string
	Size   int64
	Unit   string
	Who    string
	Detail string
	floor  int64
}

func (f Finding) weight() float64 {
	return float64(f.Size) / float64(f.floor)
}

func (f Finding) line() string {
	line := fmt.Sprintf("%s %d %s %s: %s", f.Signal, f.Size, f.Unit, f.Who, f.Detail)
	if len(line) > lineCap {
		line = strings.ToValidUTF8(line[:lineCap], "")
	}
	return line
}

type Report struct {
	Findings []Finding
	Changed  int
	Read     int
}

func (r Report) note() string {
	if r.Read == r.Changed {
		return ""
	}
	return fmt.Sprintf("read the %d largest of %d changed transcripts", r.Read, r.Changed)
}

func Read(ctx context.Context, src Sources, cfg Config, drive config.Config, now time.Time, window time.Duration) (Report, error) {
	since := now.Add(-window)
	records, err := src.Bus.Tail(ctx, client.Query{Drive: drive.Drive, Since: since})
	if err != nil {
		return Report{}, err
	}
	dead, err := deadRecipients(ctx, src, cfg, drive.Drive, records)
	if err != nil {
		return Report{}, err
	}
	peers, err := src.Bus.Who(ctx, drive.Drive)
	if err != nil {
		return Report{}, err
	}
	owed, err := owedReplies(ctx, src.Runner, cfg, peers, now)
	if err != nil {
		return Report{}, err
	}
	files, changed, err := changedTranscripts(ctx, src.Runner, cfg, peers, since)
	if err != nil {
		return Report{}, err
	}
	used, err := readTranscripts(ctx, src.Runner, files, since, window)
	if err != nil {
		return Report{}, err
	}
	skips, err := hookSkips(cfg, files, since)
	if err != nil {
		return Report{}, err
	}
	compacted, err := codexCompactions(cfg, peers, since, now)
	if err != nil {
		return Report{}, err
	}
	unfinished, err := uninstalled(ctx, src, cfg, drive, now)
	if err != nil {
		return Report{}, err
	}
	findings := slices.Concat(dead, unfinished, owed, fanOut(cfg, records, window), duplicateAsks(cfg, records), raceWarnings(cfg, records), used.findings(cfg, records, window), skips, compacted)
	slices.SortFunc(findings, func(a, b Finding) int {
		return cmp.Or(cmp.Compare(b.weight(), a.weight()), cmp.Compare(a.Key, b.Key))
	})
	return Report{Findings: findings, Changed: changed, Read: len(files)}, nil
}

func Render(findings []Finding, note string) (string, int) {
	var text strings.Builder
	reserved := len(note) + len("... 1000000 more\n") + 1
	shown := 0
	for _, finding := range findings {
		line := finding.line()
		if text.Len()+len(line)+1+reserved >= ByteCap {
			break
		}
		text.WriteString(line + "\n")
		shown++
	}
	if cut := len(findings) - shown; cut > 0 {
		fmt.Fprintf(&text, "... %d more\n", cut)
	}
	if note != "" {
		text.WriteString(note + "\n")
	}
	return text.String(), shown
}

var (
	clockPrefix   = regexp.MustCompile(`^\d{1,2}:\d{2}(:\d{2})? [AP]M `)
	speakerPrefix = regexp.MustCompile(`^[\w@.-]+: `)
	digits        = regexp.MustCompile(`\d+`)
)

func spoken(text string) string {
	return speakerPrefix.ReplaceAllString(clockPrefix.ReplaceAllString(strings.TrimSpace(text), ""), "")
}

func shape(text string, words int) string {
	fields := strings.Fields(digits.ReplaceAllString(text, "N"))
	return strings.Join(fields[:min(words, len(fields))], " ")
}

func ranked(counts map[string]int64, shown int) string {
	names := slices.SortedFunc(maps.Keys(counts), func(a, b string) int {
		return cmp.Or(cmp.Compare(counts[b], counts[a]), cmp.Compare(a, b))
	})
	parts := make([]string, 0, shown+1)
	for _, name := range names[:min(shown, len(names))] {
		parts = append(parts, fmt.Sprintf("%s %d", name, counts[name]))
	}
	text := strings.Join(parts, ", ")
	if more := len(names) - shown; more > 0 {
		text += fmt.Sprintf(" +%d", more)
	}
	return text
}

func perHour(size int64, window time.Duration) int64 {
	return int64(float64(size) / window.Hours())
}
