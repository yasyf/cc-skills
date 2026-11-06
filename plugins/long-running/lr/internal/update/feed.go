package update

import (
	"cmp"
	"context"
	"fmt"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	bridge       = "owner-relay"
	fyiTopic     = "fyi"
	otherGroup   = "Other"
	groupChars   = 40
	feedWindow   = 24 * time.Hour
	noCarrier    = "**no record carries this yet**"
	retroFile    = "retro.json"
	retroKey     = "retro/"
	retroLive    = "live"
	noPrevention = "no prevention yet"
)

type FYI struct {
	Seq   int64     `json:"seq"`
	At    time.Time `json:"at"`
	Group string    `json:"group"`
	Text  string    `json:"text"`
}

func fyiEntries(records []Record) []FYI {
	out := []FYI{}
	for _, record := range records {
		if record.Lane != bridge || record.Kind != "note" || record.Topic != fyiTopic {
			continue
		}
		text := strings.TrimSpace(record.Text)
		group, rest, found := strings.Cut(text, ": ")
		if !found || len([]rune(group)) > groupChars {
			group, rest = otherGroup, text
		}
		out = append(out, FYI{record.Seq, record.At, group, rest})
	}
	slices.SortFunc(out, func(one, other FYI) int { return cmp.Compare(other.Seq, one.Seq) })
	return out
}

func fyiLines(entries []FYI, perGroup int) []string {
	var groups []string
	byGroup := map[string][]FYI{}
	for _, entry := range entries {
		if _, seen := byGroup[entry.Group]; !seen {
			groups = append(groups, entry.Group)
		}
		byGroup[entry.Group] = append(byGroup[entry.Group], entry)
	}
	lines := make([]string, 0, len(groups)+len(entries))
	for _, group := range groups {
		lines = append(lines, "- **"+group+"**")
		kept := byGroup[group]
		if perGroup > 0 && len(kept) > perGroup {
			kept = kept[:perGroup]
		}
		for _, entry := range kept {
			text := entry.Text
			if perGroup > 0 {
				text = clipTo(text, fyiChars)
			}
			lines = append(lines, fmt.Sprintf("  - %s: %s", pacific(entry.At), text))
		}
		if older := len(byGroup[group]) - len(kept); older > 0 {
			lines = append(lines, fmt.Sprintf("  - and %d older", older))
		}
	}
	return lines
}

func carrierOf(ordered []Record, ask Record) (Record, bool) {
	at := slices.IndexFunc(ordered, func(other Record) bool {
		return other.Seq > ask.Seq && (other.Re == ask.Seq || slices.Contains(other.Resolves, ask.Seq))
	})
	if at < 0 {
		return Record{}, false
	}
	return ordered[at], true
}

func ownerWords(records []Record) (lines, gaps []string) {
	ordered := bySeq(records)
	for _, record := range ordered {
		if record.Lane != bridge || record.Kind != "ask" {
			continue
		}
		status := noCarrier
		if carrier, found := carrierOf(ordered, record); found {
			status = fmt.Sprintf("carried by %s %s", carrier.Lane, carrier.Name())
		} else {
			gaps = append(gaps, fmt.Sprintf("owner words no record carries: %s", record.Name()))
		}
		lines = append(lines, fmt.Sprintf("- %s: \"%s\" | %s", pacific(record.At), strings.Join(strings.Fields(record.Text), " "), status))
	}
	return lines, gaps
}

func recordsFrom(records []Record, start time.Time) []Record {
	return slices.DeleteFunc(slices.Clone(records), func(record Record) bool { return record.At.Before(start) })
}

type Retro struct {
	Owner     string
	Failure   string
	Mechanism string
	State     string
	Due       time.Time
}

func (r Retro) line() string {
	return fmt.Sprintf("%s, prevented by %s (%s, due %s)", r.Failure, orWord(r.Mechanism, noPrevention), orWord(r.State, "?"), pacificText(r.Due))
}

func ReadRetro(ctx context.Context, source ledger.Store, stateDir string) ([]Retro, error) {
	rows, err := ledger.FromState(ctx, source, filepath.Join(stateDir, retroFile))
	if err != nil {
		return nil, err
	}
	var out []Retro
	for _, row := range rows {
		if strings.HasPrefix(row.Key, retroKey) && row.Fields["dup_of"] == "" {
			out = append(out, Retro{row.Fields["owner"], row.Fields["failure"], row.Fields["mechanism"], row.Fields["state"], dueOf(row.Fields["due"])})
		}
	}
	return out, nil
}

func dueOf(text string) time.Time {
	due, err := store.Parse(text)
	if err != nil {
		return time.Time{}
	}
	return due
}
