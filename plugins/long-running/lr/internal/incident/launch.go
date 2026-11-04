package incident

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

const (
	RoleFix         = "fix"
	RoleEvidence    = "evidence"
	RoleComms       = "comms"
	routeIncident   = "incident"
	routeWriter     = "incident-opus"
	effortXHigh     = "xhigh"
	deskLane        = "orca-desk"
	deskRunner      = "desk-runner"
	kindGo          = "go"
	kindState       = "state"
	topicOrca       = "orca"
	fixAlias        = "-fix"
	sessionEnv      = "CLAUDE_CODE_SESSION_ID"
	aliveWindow     = 5 * time.Minute
	aliveLookback   = 24 * time.Hour
	verdictPoll     = 2 * time.Second
	verdictLaunched = "LAUNCHED"
	DefaultTimeout  = 600 * time.Second
	RunnerAlive     = "orca alive"
	PassSecondsAttr = "pass_seconds"
	PassStepAttr    = "pass_step"
	StuckPass       = 20 * time.Minute
)

var (
	pacificZone  = mustZone("America/Los_Angeles")
	ErrUnsettled = errors.New("a launch has no runner verdict")

	verdictLine     = regexp.MustCompile(`^(LAUNCHED|LAUNCH-FAILED|LAUNCH-DUPLICATE|LAUNCH-EXPIRED|LAUNCH-HELD|UNVERIFIABLE|SPRITE-UNVERIFIABLE) (?:desk-lane-[\w.-]+/)?(R\d+(?:@\w+)?) ([\w.-]+): `)
	rootLines       = regexp.MustCompile(`(?m)^Root:.*\n`)
	alertLine       = regexp.MustCompile(`(?m)^Alert:.*$`)
	rootPlaceholder = regexp.MustCompile(`<(?:lane|state|\.\.\.)>`)
	anyPlaceholder  = regexp.MustCompile(`<[^>]+>`)
	extraBlankLines = regexp.MustCompile(`\n{3,}`)
)

func mustZone(name string) *time.Location {
	zone, err := time.LoadLocation(name)
	if err != nil {
		panic(err)
	}
	return zone
}

type DeskLaunch struct {
	Bus           Bus
	Drive         string
	Author        string
	Name          string
	FixBrief      string
	EvidenceBrief string
	CommsBrief    string
	Alert         string
	Claude        string
	Timeout       time.Duration
	Poll          time.Duration
	Now           func() time.Time
	Out           io.Writer
}

type plannedLane struct {
	lane, role, brief string
}

type driveEntry struct {
	Drive    string   `json:"drive"`
	CCIDrive string   `json:"cci_drive"`
	Program  string   `json:"program"`
	Sessions []string `json:"sessions"`
	StateDir string   `json:"state_dir"`
}

func (e driveEntry) cciDrive() string {
	return cmp.Or(e.CCIDrive, e.Program, filepath.Base(e.StateDir))
}

func (d DeskLaunch) entry() (driveEntry, error) {
	paths, err := filepath.Glob(filepath.Join(d.Claude, "long-running", "drives", "*.json"))
	if err != nil {
		return driveEntry{}, err
	}
	slices.Sort(paths)
	session := os.Getenv(sessionEnv)
	for _, path := range paths {
		raw, err := os.ReadFile(path)
		if err != nil {
			return driveEntry{}, err
		}
		var entry driveEntry
		if err := json.Unmarshal(raw, &entry); err != nil {
			return driveEntry{}, fmt.Errorf("drive entry %s: %w", path, err)
		}
		if entry.cciDrive() == d.Drive || (d.Drive == "" && slices.Contains(entry.Sessions, session)) {
			return entry, nil
		}
	}
	if d.Drive == "" {
		return driveEntry{}, fmt.Errorf("no drive in the registry is bound to session %s, so its orca desk runner is unknown and nothing is posted", session)
	}
	return driveEntry{}, fmt.Errorf("no drive in the registry is cci drive %s, so its orca desk runner is unknown and nothing is posted", d.Drive)
}

func filled(template, lane, alert string) (string, error) {
	text := strings.ReplaceAll(rootLines.ReplaceAllString(template, ""), "<lane>", lane)
	location := alertLine.FindStringIndex(text)
	if location == nil {
		return "", fmt.Errorf("the template for %s has no Alert: line for --alert to fill, so nothing is posted", lane)
	}
	text = text[:location[0]] + "Alert: " + alert + text[location[1]:]
	return extraBlankLines.ReplaceAllString(text, "\n\n"), nil
}

func unfilled(text, alert string) string {
	for line := range strings.SplitSeq(text, "\n") {
		if line == "Alert: "+alert {
			continue
		}
		if rootPlaceholder.MatchString(line) || (strings.HasPrefix(line, "Alert:") && anyPlaceholder.MatchString(line)) {
			return strings.TrimSpace(line)
		}
	}
	return ""
}

func (d DeskLaunch) plan() ([]plannedLane, error) {
	name := strings.TrimSuffix(d.Name, fixAlias)
	planned := []plannedLane{{name + "-" + RoleFix, RoleFix, d.FixBrief}}
	if d.EvidenceBrief != "" {
		planned = append(planned, plannedLane{name + "-" + RoleEvidence, RoleEvidence, d.EvidenceBrief})
	}
	if d.CommsBrief != "" {
		planned = append(planned, plannedLane{name + "-" + RoleComms, RoleComms, d.CommsBrief})
	}
	for _, lane := range planned {
		if info, err := os.Stat(lane.brief); err != nil || info.IsDir() {
			return nil, fmt.Errorf("brief %s is not a file", lane.brief)
		}
	}
	return planned, nil
}

func written(entry driveEntry, lane plannedLane, alert string) (string, error) {
	template, err := os.ReadFile(lane.brief)
	if err != nil {
		return "", err
	}
	text, err := filled(string(template), lane.lane, alert)
	if err != nil {
		return "", err
	}
	dir := filepath.Join(entry.StateDir, lane.lane)
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return "", err
	}
	path := filepath.Join(dir, "brief.md")
	return path, os.WriteFile(path, []byte(text), 0o600)
}

func (d DeskLaunch) fallback(planned []plannedLane) string {
	text := fmt.Sprintf("spawn an Agent named `%s`", planned[0].lane)
	for _, lane := range planned[1:] {
		text += fmt.Sprintf(" and one named `%s`", lane.lane)
	}
	return text + " instead"
}

func RunnerFault(records []client.Record, drive string, now time.Time) string {
	silent := func(last string) string {
		return fmt.Sprintf("no orca desk runner has posted an orca alive record on cci drive %s in the last five minutes (%s)", drive, last)
	}
	for _, record := range slices.Backward(records) {
		if record.Lane != deskRunner || record.Topic != topicOrca || !strings.HasPrefix(record.Text, RunnerAlive) {
			continue
		}
		if now.Sub(record.At) >= aliveWindow {
			return silent("last at " + record.At.In(pacificZone).Format("3:04 PM"))
		}
		seconds, _ := strconv.Atoi(record.Fields.Attrs[PassSecondsAttr])
		if running := time.Duration(seconds) * time.Second; running >= StuckPass {
			return fmt.Sprintf("the orca desk runner on cci drive %s is alive but stuck: %s has run %d minutes, at step %s", drive, strings.TrimSpace(strings.TrimPrefix(record.Text, RunnerAlive)), int(running.Minutes()), record.Fields.Attrs[PassStepAttr])
		}
		return ""
	}
	return silent("never")
}

func (d DeskLaunch) runnerFault(ctx context.Context, drive string) (string, error) {
	now := d.Now()
	records, err := d.Bus.Tail(ctx, client.Query{Drive: drive, Since: now.Add(-aliveLookback), Kinds: []string{kindState}})
	if err != nil {
		return "", err
	}
	return RunnerFault(records, drive, now), nil
}

func (d DeskLaunch) requireRunner(ctx context.Context, drive string, planned []plannedLane) error {
	fault, err := d.runnerFault(ctx, drive)
	if err != nil || fault == "" {
		return err
	}
	return fmt.Errorf("refused: %s, so nothing reads %s lines; %s", fault, deskLane, d.fallback(planned))
}

func launchDirective(lane plannedLane, brief string) string {
	route := routeIncident
	if lane.role == RoleComms {
		route = routeWriter
	}
	return fmt.Sprintf("launch %s NOW %s %s role=%s brief=%s", lane.lane, route, effortXHigh, lane.role, brief)
}

func (d DeskLaunch) post(ctx context.Context, drive, topic, directive string) (client.Record, error) {
	stored, err := d.Bus.Post(ctx, client.Draft{Drive: drive, Lane: d.Author, Kind: kindGo, To: []string{deskLane}, Topic: topic, Text: deskLane + ": " + directive})
	if err != nil {
		return client.Record{}, err
	}
	fmt.Fprintf(d.Out, "posted cci %s: %s\n", stored.Name(), stored.Text)
	return stored, nil
}

type settledLaunch struct {
	label, text string
}

func (d DeskLaunch) wait(ctx context.Context, drive string, since int64, lanes map[string]string) (map[string]settledLaunch, error) {
	settled := map[string]settledLaunch{}
	deadline := time.Now().Add(d.Timeout)
	for len(settled) < len(lanes) && time.Now().Before(deadline) {
		records, err := d.Bus.Tail(ctx, client.Query{Drive: drive, After: since})
		if err != nil {
			return nil, err
		}
		for _, record := range records {
			since = max(since, record.Seq)
			if record.Lane != deskRunner {
				continue
			}
			match := verdictLine.FindStringSubmatch(record.Text)
			if match == nil || lanes[match[2]] != match[3] {
				continue
			}
			if _, seen := settled[match[2]]; !seen {
				settled[match[2]] = settledLaunch{match[1], record.Text}
				fmt.Fprintf(d.Out, "%s %s %s\n", match[3], match[2], record.Text)
			}
		}
		if len(settled) == len(lanes) {
			break
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(d.Poll):
		}
	}
	return settled, nil
}

func (d DeskLaunch) Run(ctx context.Context) error {
	planned, err := d.plan()
	if err != nil {
		return err
	}
	entry, err := d.entry()
	if err != nil {
		return err
	}
	drive := cmp.Or(d.Drive, entry.cciDrive())
	if d.Alert != "" {
		for i, lane := range planned {
			if planned[i].brief, err = written(entry, lane, d.Alert); err != nil {
				return err
			}
		}
	}
	for _, lane := range planned {
		text, err := os.ReadFile(lane.brief)
		if err != nil {
			return err
		}
		if line := unfilled(string(text), d.Alert); line != "" {
			return fmt.Errorf("brief %s for %s still holds a placeholder the coordinator fills, so nothing is posted: %s", lane.brief, lane.lane, line)
		}
	}
	if err := d.requireRunner(ctx, drive, planned); err != nil {
		return err
	}
	keys := make(map[string]string, len(planned))
	first := int64(0)
	for _, lane := range planned {
		brief, err := filepath.Abs(lane.brief)
		if err != nil {
			return err
		}
		stored, err := d.post(ctx, drive, strings.TrimSuffix(d.Name, fixAlias), launchDirective(lane, brief))
		if err != nil {
			return err
		}
		keys["R"+strings.TrimPrefix(stored.Name(), "#")] = lane.lane
		if first == 0 {
			first = stored.Seq
		}
	}
	settled, err := d.wait(ctx, drive, first-1, keys)
	if err != nil {
		return err
	}
	return d.outcome(ctx, drive, keys, settled)
}

func (d DeskLaunch) outcome(ctx context.Context, drive string, keys map[string]string, settled map[string]settledLaunch) error {
	fault := ""
	if len(settled) < len(keys) {
		var err error
		if fault, err = d.runnerFault(ctx, drive); err != nil {
			return err
		}
	}
	for _, key := range slices.Sorted(maps.Keys(keys)) {
		if _, done := settled[key]; done {
			continue
		}
		if fault == "" {
			fmt.Fprintf(d.Out, "%s %s posted with no verdict after %.0fs; the orca runner is alive, so its verdict arrives as a %s record\n", keys[key], key, d.Timeout.Seconds(), deskRunner)
			continue
		}
		fmt.Fprintf(d.Out, "%s %s unsettled after %.0fs: %s; check it with `cci tail --drive %s --lane %s --kind state --topic orca -n 1`\n", keys[key], key, d.Timeout.Seconds(), fault, drive, deskRunner)
	}
	if fault != "" {
		return ErrUnsettled
	}
	for _, key := range slices.Sorted(maps.Keys(keys)) {
		if verdict, done := settled[key]; done && verdict.label != verdictLaunched {
			return fmt.Errorf("%s did not launch: %s", keys[key], verdict.text)
		}
	}
	return nil
}
