package waste

import (
	"context"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

const shapeWords = 4

var quotedWords = regexp.MustCompile(`"([^"]{24,})"|“([^”]{24,})”`)

func deadRecipients(ctx context.Context, src Sources, cfg Config, drive string, records []client.Record) ([]Finding, error) {
	posted := map[string]bool{}
	for _, record := range records {
		posted[record.Lane] = true
	}
	silent := map[string]bool{}
	for _, record := range records {
		for _, to := range record.To {
			if !posted[to] && !slices.Contains(cfg.Ignore, to) {
				silent[to] = true
			}
		}
	}
	if len(silent) > 0 {
		earlier, err := src.Bus.Tail(ctx, client.Query{Drive: drive, Lanes: slices.Sorted(maps.Keys(silent))})
		if err != nil {
			return nil, err
		}
		for _, record := range earlier {
			delete(silent, record.Lane)
		}
	}
	rows, err := src.Retired()
	if err != nil {
		return nil, err
	}
	retired := make(map[string]time.Time, len(rows))
	for _, row := range rows {
		retired[row.Name] = row.Since
	}
	deliveries := map[string]map[string]int64{}
	for _, record := range records {
		for _, to := range record.To {
			at, gone := retired[to]
			if to == record.Lane || slices.Contains(cfg.Ignore, to) || !silent[to] && (!gone || record.At.Before(at)) {
				continue
			}
			if deliveries[to] == nil {
				deliveries[to] = map[string]int64{}
			}
			deliveries[to][record.Lane]++
		}
	}
	var findings []Finding
	for to, emitters := range deliveries {
		var size int64
		for _, count := range emitters {
			size += count
		}
		if size >= cfg.Floors.Dead {
			findings = append(findings, Finding{signalDead, signalDead + "/" + to, size, "deliveries", to, "from " + ranked(emitters, 3), cfg.Floors.Dead})
		}
	}
	return findings, nil
}

func fanOut(cfg Config, records []client.Record, window time.Duration) []Finding {
	type shaped struct{ emitter, words string }
	deliveries := map[shaped]int64{}
	wide := map[shaped]int64{}
	for _, record := range records {
		if len(record.To) >= cfg.FanOutRecipients {
			key := shaped{record.Lane, shape(spoken(record.Text), shapeWords)}
			deliveries[key] += int64(len(record.To))
			wide[key]++
		}
	}
	var findings []Finding
	for key, total := range deliveries {
		if size := perHour(total, window); size >= cfg.Floors.FanOut {
			findings = append(findings, Finding{signalFanOut, signalFanOut + "/" + key.emitter + "/" + key.words, size, "deliveries/h", key.emitter, fmt.Sprintf("%d records of %q", wide[key], key.words), cfg.Floors.FanOut})
		}
	}
	return findings
}

func duplicateAsks(cfg Config, records []client.Record) []Finding {
	copies := map[string]map[string]int64{}
	for _, record := range records {
		if strings.TrimPrefix(string(record.Kind), "lr.") != "ask" {
			continue
		}
		words := askedWords(record.Text)
		if copies[words] == nil {
			copies[words] = map[string]int64{}
		}
		copies[words][record.Lane]++
	}
	var findings []Finding
	for words, lanes := range copies {
		var size int64
		for _, count := range lanes {
			size += count
		}
		if len(lanes) > 1 && size >= cfg.Floors.Duplicates {
			findings = append(findings, Finding{signalDuplicate, fmt.Sprintf("%s/%.120s", signalDuplicate, words), size, "asks", ranked(lanes, 3), fmt.Sprintf("%.60q", words), cfg.Floors.Duplicates})
		}
	}
	return findings
}

func askedWords(text string) string {
	longest := ""
	for _, match := range quotedWords.FindAllStringSubmatch(text, -1) {
		if quoted := match[1] + match[2]; len(quoted) > len(longest) {
			longest = quoted
		}
	}
	if longest == "" {
		longest = spoken(text)
	}
	return strings.Join(strings.Fields(strings.ToLower(longest)), " ")
}
