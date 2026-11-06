package waste

import (
	"context"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const shapeWords = 4

var (
	quotedWords  = regexp.MustCompile(`"([^"]{24,})"|“([^”]{24,})”`)
	raceWords    = regexp.MustCompile(`(?i)(?:^|[^\w-])(?:clobber\w*|rac(?:e|es|ed|ing))(?:[^\w-]|$)`)
	warningKinds = []string{"report", "note", "state", "blocker", "blocked", "defect", "evidence", "mechanism"}
)

func deadRecipients(ctx context.Context, src Sources, cfg Config, drive string, records []client.Record) ([]Finding, error) {
	lastPost := map[string]time.Time{}
	for _, record := range records {
		if record.At.After(lastPost[record.Lane]) {
			lastPost[record.Lane] = record.At
		}
	}
	reads := map[string]string{}
	silent := map[string]bool{}
	for _, record := range records {
		for _, to := range record.To {
			if _, resolved := reads[to]; !resolved {
				lane, err := lanes.Resolve(ctx, drive, to)
				if err != nil {
					return nil, err
				}
				reads[to] = lane
			}
			if _, posted := lastPost[reads[to]]; !posted && !slices.Contains(cfg.Ignore, to) {
				silent[reads[to]] = true
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
			at, gone := retired[reads[to]]
			stoodDown := gone && !record.At.Before(at) && !lastPost[reads[to]].After(at)
			if reads[to] == record.Lane || slices.Contains(cfg.Ignore, to) || !silent[reads[to]] && !stoodDown {
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

func raceWarnings(cfg Config, records []client.Record) []Finding {
	type warning struct{ lane, words string }
	counts := map[warning]int64{}
	newest := map[warning]client.Record{}
	for _, record := range records {
		if record.Lane == cfg.Lane || !slices.Contains(warningKinds, string(record.Kind)) || !raceWords.MatchString(record.Text) {
			continue
		}
		key := warning{record.Lane, shape(spoken(record.Text), shapeWords)}
		counts[key]++
		newest[key] = record
	}
	var findings []Finding
	for key, size := range counts {
		if size >= cfg.Floors.Races {
			findings = append(findings, Finding{signalRace, signalRace + "/" + key.lane + "/" + key.words, size, "warnings", key.lane, fmt.Sprintf("open a fix, not a report: %s %.100q", newest[key].Name(), spoken(newest[key].Text)), cfg.Floors.Races})
		}
	}
	return findings
}

func uninstalled(ctx context.Context, src Sources, cfg Config, drive config.Config, now time.Time) ([]Finding, error) {
	lookback := time.Duration(cfg.MergeLookbackHours) * time.Hour
	records, err := src.Bus.Tail(ctx, client.Query{Drive: drive.Drive, Since: now.Add(-lookback), Kinds: []string{"landed", "done"}})
	if err != nil {
		return nil, err
	}
	type merge struct {
		lane   string
		at     time.Time
		repo   string
		number int
	}
	merges := map[string]merge{}
	installed := map[string]bool{}
	for _, record := range records {
		for _, ref := range record.Refs.PRs {
			if record.Fields.Attrs["installed"] != "" {
				installed[ref.String()] = true
				continue
			}
			if _, seen := merges[ref.String()]; !seen && ref.Repo != "" && ref.Repo != drive.Repo {
				merges[ref.String()] = merge{record.Lane, record.At, ref.Repo, ref.Number}
			}
		}
	}
	var findings []Finding
	for pr, merged := range merges {
		age := int64(now.Sub(merged.at).Minutes())
		if installed[pr] || installed[strconv.Itoa(merged.number)] || age < cfg.Floors.Uninstalled {
			continue
		}
		if merged.repo == src.Running.Repo {
			held, err := src.Running.holds(ctx, merged.number, now.Add(-2*lookback))
			if err != nil {
				return nil, err
			}
			if held {
				findings = append(findings, Finding{signalUnproven, signalUnproven + "/" + pr, age, "minutes", merged.lane, src.Running.unproven(pr), cfg.Floors.Uninstalled})
				continue
			}
		}
		findings = append(findings, Finding{signalUninstalled, signalUninstalled + "/" + pr, age, "minutes", merged.lane, pr + " merged, and no done record names its installed version and proof", cfg.Floors.Uninstalled})
	}
	return findings, nil
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
