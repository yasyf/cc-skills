package update

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
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

func fyiLines(entries []FYI) []string {
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
		for _, entry := range byGroup[group] {
			lines = append(lines, fmt.Sprintf("  - %s: %s", pacific(entry.At), entry.Text))
		}
	}
	return lines
}

func carrierOf(ordered []Record, ask Record) (Record, bool) {
	named := regexp.MustCompile(`#` + strconv.FormatInt(ask.Seq, 10) + `\b`)
	at := slices.IndexFunc(ordered, func(other Record) bool {
		return other.Seq > ask.Seq && (other.Re == ask.Seq || slices.Contains(other.Resolves, ask.Seq) || named.MatchString(other.Text))
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
			status = fmt.Sprintf("carried by %s #%d", carrier.Lane, carrier.Seq)
		} else {
			gaps = append(gaps, fmt.Sprintf("owner words no record carries: #%d", record.Seq))
		}
		lines = append(lines, fmt.Sprintf("- %s: \"%s\" | %s", pacific(record.At), clip(record.Text), status))
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
	data, err := os.ReadFile(filepath.Join(stateDir, retroFile))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var state struct {
		Ledger string `json:"ledger"`
	}
	if err := json.Unmarshal(data, &state); err != nil {
		return nil, fmt.Errorf("%s: %w", retroFile, err)
	}
	if state.Ledger == "" {
		return nil, nil
	}
	rows, err := source.Rows(ctx, state.Ledger)
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
