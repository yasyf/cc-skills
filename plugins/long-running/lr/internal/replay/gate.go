package replay

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	Refused    = 3
	lane       = "replay"
	day        = 24 * time.Hour
	history    = "72h"
	passed     = "REPLAY-PASSED"
	acceptAttr = "replay-accepted"
	pageBytes  = 4_000_000
	pageRows   = 500
	whoFields  = "lane,live,seen_at,announced_at,stood_down"
	filesJQ    = ".[] | [.filename, .patch] | @json"
	pinEnv     = "LONG_RUNNING_PIN"
	readLimit  = 2 * time.Minute
	runLimit   = 15 * time.Minute
	descriptor = "lr.binrun"
)

var classChecked = regexp.MustCompile(`(?i)\bTEST-ONLY-MISMATCH\b|\bEXEMPT-ADMITTED\b.*\b(?:test-only|ci-only|exempt: (?:tests?|ci|harness|fixtures?))\b`)

type Deps struct {
	Runner   shell.Runner
	CCX      ccx.Client
	Installs string
	Now      func() time.Time
	Stdout   io.Writer
	Tail     func(context.Context, client.Query) ([]client.Record, error)
}

type read struct {
	raw    json.RawMessage
	record lanes.Record
}

func iso(at time.Time) string { return at.UTC().Format("2006-01-02T15:04:05.000Z") }

func (d Deps) run(ctx context.Context, timeout time.Duration, argv ...string) (string, error) {
	result, err := d.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: timeout})
	return result.Stdout, err
}

func (d Deps) paged(ctx context.Context, drive, since string) ([]read, error) {
	var found []read
	for {
		out, err := d.run(ctx, readLimit, "cci", "tail", "--drive", drive, "--since", since, "--json", "--budget", strconv.Itoa(pageBytes))
		if err != nil {
			return nil, err
		}
		lines := nonEmptyLines(out)
		for _, line := range lines {
			var record lanes.Record
			if err := json.Unmarshal([]byte(line), &record); err != nil {
				return nil, fmt.Errorf("cci tail: %w", err)
			}
			found = append(found, read{json.RawMessage(line), record})
		}
		if len(lines) < pageRows && len(out) < pageBytes/2 {
			return found, nil
		}
		since = strconv.FormatInt(found[len(found)-1].record.Seq, 10)
	}
}

func nonEmptyLines(out string) []string {
	return slices.DeleteFunc(strings.Split(out, "\n"), func(line string) bool { return strings.TrimSpace(line) == "" })
}

func (d Deps) whoRows(ctx context.Context, drive string) ([]json.RawMessage, error) {
	out, err := d.run(ctx, readLimit, "cci", "who", "--drive", drive, "--all", "--json", "--fields", whoFields)
	if err != nil {
		return nil, err
	}
	lines := nonEmptyLines(out)
	rows := make([]json.RawMessage, 0, len(lines))
	for _, line := range lines {
		rows = append(rows, json.RawMessage(line))
	}
	return rows, nil
}

func (d Deps) changedFiles(ctx context.Context, repo string, pr int) (map[string]*string, error) {
	out, err := d.CCX.GH(ctx, ccx.MaxAgeFresh, "api", "--paginate", fmt.Sprintf("repos/%s/pulls/%d/files", repo, pr), "--jq", filesJQ)
	if err != nil {
		return nil, err
	}
	files := map[string]*string{}
	for _, line := range nonEmptyLines(out) {
		var pair []*string
		if err := json.Unmarshal([]byte(line), &pair); err != nil {
			return nil, fmt.Errorf("pull %d files: %w", pr, err)
		}
		if len(pair) != 2 || pair[0] == nil {
			return nil, fmt.Errorf("pull %d files: %q is no filename and patch", pr, line)
		}
		files[*pair[0]] = pair[1]
	}
	return files, nil
}

func (d Deps) install(version string) string { return filepath.Join(d.Installs, version) }

func (d Deps) runPack(ctx context.Context, version, input string) (Decided, error) {
	dir := d.install(version)
	result, err := d.Runner.Run(ctx, shell.Command{
		Argv:    []string{filepath.Join(dir, "bin", "replay.py"), "run", "--scripts", filepath.Join(dir, "skills", "long-running", "scripts"), "--input", input},
		Env:     []string{pinEnv + "=" + version},
		Timeout: runLimit,
	})
	if err != nil {
		return Decided{}, err
	}
	var decided Decided
	if err := json.Unmarshal([]byte(result.Stdout), &decided); err != nil {
		return Decided{}, fmt.Errorf("%s replay run: %w", version, err)
	}
	return decided, nil
}

func (d Deps) sameBinary(oldVersion, newVersion string) bool {
	return bytes.Equal(d.file(oldVersion, "bin", descriptor), d.file(newVersion, "bin", descriptor)) &&
		len(d.file(oldVersion, "bin", descriptor)) > 0 && d.shimmed(oldVersion) && d.shimmed(newVersion)
}

func (d Deps) shimmed(version string) bool {
	return bytes.Contains(d.file(version, "scripts", "bin-shim.sh"), []byte(`replay.py) exec "$ROOT/bin/lr" replay`))
}

func (d Deps) file(version string, parts ...string) []byte {
	data, err := os.ReadFile(filepath.Join(append([]string{d.install(version)}, parts...)...))
	if err != nil {
		return nil
	}
	return data
}

type Fixture struct {
	Class  string          `json:"class"`
	Defect int64           `json:"defect"`
	Fix    string          `json:"fix"`
	Recurs Recurs          `json:"recurs"`
	Input  json.RawMessage `json:"input"`
	Expect json.RawMessage `json:"expect"`
}

type Recurs struct {
	Kinds []string `json:"kinds"`
	Text  string   `json:"text"`
}

func (d Deps) classes(version string) (map[string]Fixture, error) {
	paths, err := filepath.Glob(filepath.Join(d.install(version), "tests", "fixtures", "replay", "*.json"))
	if err != nil {
		return nil, err
	}
	found := make(map[string]Fixture, len(paths))
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		var fixture Fixture
		if err := json.Unmarshal(data, &fixture); err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		found[strings.TrimSuffix(filepath.Base(path), ".json")] = fixture
	}
	return found, nil
}

func differences(oldDecided, newDecided Decided) []string {
	found := make([]string, 0, len(newDecided.Relaunched)+len(newDecided.Refused))
	sortedRelaunches := slices.Sorted(slices.Values(newDecided.Relaunched))
	for _, name := range sortedRelaunches {
		if !slices.Contains(oldDecided.Relaunched, name) {
			found = append(found, "relaunches "+name)
		}
	}
	sortedRefusals := slices.Sorted(slices.Values(newDecided.Refused))
	for _, pr := range sortedRefusals {
		if !slices.Contains(oldDecided.Refused, pr) {
			found = append(found, fmt.Sprintf("refuses #%d's test-only claim", pr))
		}
	}
	return found
}

func (d Deps) post(ctx context.Context, drive, kind, key, text string, flags ...string) error {
	argv := append([]string{"cci", "post", "--drive", drive, "--lane", lane, "--kind", kind, "--to", "root", "--id", key}, flags...)
	_, err := d.run(ctx, readLimit, append(argv, "--text", text)...)
	return err
}

func thousands(count int) string {
	digits := strconv.Itoa(count)
	var grouped strings.Builder
	for i, digit := range digits {
		if i > 0 && (len(digits)-i)%3 == 0 {
			grouped.WriteByte(',')
		}
		grouped.WriteRune(digit)
	}
	return grouped.String()
}

func (d Deps) Gate(ctx context.Context, drive, repo, oldVersion, newVersion string) (int, error) {
	verdict, code, err := d.judge(ctx, drive, repo, oldVersion, newVersion)
	if err != nil || code == Refused {
		return code, err
	}
	oldClasses, err := d.classes(oldVersion)
	if err != nil {
		return 0, err
	}
	newClasses, err := d.classes(newVersion)
	if err != nil {
		return 0, err
	}
	names := make([]string, 0, len(newClasses))
	for name := range newClasses {
		if _, known := oldClasses[name]; !known {
			names = append(names, name)
		}
	}
	slices.Sort(names)
	fixes := "no new fixture class"
	if len(names) > 0 {
		named := make([]string, 0, len(names))
		for _, name := range names {
			named = append(named, fmt.Sprintf("%s (defect #%d, %s)", name, newClasses[name].Defect, newClasses[name].Fix))
		}
		fixes = strings.Join(named, ", ")
	}
	return 0, d.post(ctx, drive, "state", "replay-passed:"+newVersion, fmt.Sprintf("%s %s %s; it installs %s", passed, newVersion, verdict, fixes), "--attr", "version="+newVersion, "--attr", "classes="+strings.Join(names, ","))
}

func (d Deps) judge(ctx context.Context, drive, repo, oldVersion, newVersion string) (string, int, error) {
	if d.sameBinary(oldVersion, newVersion) {
		return fmt.Sprintf("runs the binary %s runs, so it replays nothing", oldVersion), 0, nil
	}
	start := d.Now().Add(-day)
	records, err := d.paged(ctx, drive, history)
	if err != nil {
		return "", 0, err
	}
	raws := make([]json.RawMessage, 0, len(records))
	inWindow := 0
	judged := map[int]bool{}
	for _, one := range records {
		raws = append(raws, one.raw)
		if one.record.At.Before(start) {
			continue
		}
		inWindow++
		if repo != "" && classChecked.MatchString(one.record.Text) {
			for _, pr := range one.record.PRs() {
				judged[pr] = true
			}
		}
	}
	who, err := d.whoRows(ctx, drive)
	if err != nil {
		return "", 0, err
	}
	prs := make([]PR, 0, len(judged))
	for pr := range judged {
		patches, err := d.changedFiles(ctx, repo, pr)
		if err != nil {
			return "", 0, err
		}
		prs = append(prs, PR{pr, patches})
	}
	slices.SortFunc(prs, func(a, b PR) int { return a.PR - b.PR })
	input, err := writeInput(map[string]any{"records": raws, "since": iso(start), "who": who, "prs": prs})
	if err != nil {
		return "", 0, err
	}
	defer func() { _ = os.Remove(input) }()
	oldDecided, err := d.runPack(ctx, oldVersion, input)
	if err != nil {
		return "", 0, err
	}
	newDecided, err := d.runPack(ctx, newVersion, input)
	if err != nil {
		return "", 0, err
	}
	replayed := fmt.Sprintf("%s records and %d test-only class checks of the last 24 hours", thousands(inWindow), len(prs))
	if found := differences(oldDecided, newDecided); len(found) > 0 {
		if accepted := acceptedAt(records, oldVersion); accepted != "" {
			return fmt.Sprintf("replayed %s against %s and %s, which root accepted in %s", replayed, oldVersion, strings.Join(found, "; "), accepted), 0, nil
		}
		why := fmt.Sprintf("%s replayed %s against %s and %s, which %s did not, so it is not installed; root accepts a deliberate change with `cci post --kind decide --attr %s=%s`", newVersion, replayed, oldVersion, strings.Join(found, "; "), oldVersion, acceptAttr, oldVersion)
		err := d.post(ctx, drive, "blocker", refusalKey(oldVersion, found), "REPLAY-REFUSED "+why, "--attr", "version="+newVersion)
		_, printed := fmt.Fprintln(d.Stdout, why)
		return "", Refused, errors.Join(err, printed)
	}
	return fmt.Sprintf("replayed %s against %s with no new relaunch or refusal", replayed, oldVersion), 0, nil
}

func refusalKey(serving string, found []string) string {
	return fmt.Sprintf("replay-refused:%s:%x", serving, sha256.Sum256([]byte(strings.Join(found, ";"))))
}

func acceptedAt(records []read, serving string) string {
	for _, one := range records {
		if one.record.Lane == lanes.Root && one.record.Attr(acceptAttr) == serving {
			return one.record.Name()
		}
	}
	return ""
}

func writeInput(given map[string]any) (string, error) {
	file, err := os.CreateTemp("", "lr-replay-*.json")
	if err != nil {
		return "", err
	}
	defer func() { _ = file.Close() }()
	if err := json.NewEncoder(file).Encode(given); err != nil {
		return "", err
	}
	return file.Name(), nil
}

func matches(pattern Recurs, compiled *regexp.Regexp, record client.Record) bool {
	return record.Lane != lane && (len(pattern.Kinds) == 0 || slices.Contains(pattern.Kinds, string(record.Kind))) && compiled.MatchString(record.Text)
}

func (d Deps) Recur(ctx context.Context, drive string) error {
	now := d.Now()
	installs, err := d.Tail(ctx, client.Query{Drive: drive, Since: now.Add(-3 * day), Lanes: []string{lane}})
	if err != nil {
		return err
	}
	completed := map[string]bool{}
	for _, record := range installs {
		if record.Kind == "report" {
			completed[record.ID] = true
		}
	}
	for _, record := range installs {
		if record.Lane != lane || !strings.Contains(record.Text, passed) || now.Sub(record.At) < day {
			continue
		}
		version := record.Fields.Attrs["version"]
		names := slices.DeleteFunc(strings.Split(record.Fields.Attrs["classes"], ","), func(name string) bool {
			return name == "" || completed[recurrenceKey(version, name)]
		})
		if len(names) == 0 {
			continue
		}
		classes, err := d.classes(version)
		if err != nil {
			return err
		}
		var kinds []string
		for _, name := range names {
			fixture, found := classes[name]
			if !found {
				return fmt.Errorf("%s ships no fixture class %s", version, name)
			}
			if len(fixture.Recurs.Kinds) == 0 {
				kinds = nil
				break
			}
			kinds = append(kinds, fixture.Recurs.Kinds...)
		}
		slices.Sort(kinds)
		records, err := d.Tail(ctx, client.Query{Drive: drive, Since: record.At, Kinds: slices.Compact(kinds)})
		if err != nil {
			return err
		}
		for _, name := range names {
			if err := d.recurOne(ctx, drive, version, name, classes, records, record.At); err != nil {
				return err
			}
			completed[recurrenceKey(version, name)] = true
		}
	}
	return nil
}

func recurrenceKey(version, name string) string { return "recurrence:" + version + ":" + name }

func (d Deps) recurOne(ctx context.Context, drive, version, name string, classes map[string]Fixture, records []client.Record, start time.Time) error {
	fixture, found := classes[name]
	if !found {
		return fmt.Errorf("%s ships no fixture class %s", version, name)
	}
	compiled, err := regexp.Compile("(?i)" + fixture.Recurs.Text)
	if err != nil {
		return fmt.Errorf("class %s: %w", name, err)
	}
	seen := make([]string, 0, len(records))
	for _, other := range records {
		if !other.At.Before(start) && other.At.Before(start.Add(day)) && matches(fixture.Recurs, compiled, other) {
			seen = append(seen, other.Name())
		}
	}
	listed := ""
	if len(seen) > 0 {
		listed = ": " + strings.Join(seen, " ")
	}
	text := fmt.Sprintf("RECURRENCE %s: %d in the 24 hours after %s installed (defect #%d, fix %s)%s", name, len(seen), version, fixture.Defect, fixture.Fix, listed)
	return d.post(ctx, drive, "report", recurrenceKey(version, name), text, "--attr", "class="+name, "--count", fmt.Sprintf("recurrences=%d", len(seen)))
}
