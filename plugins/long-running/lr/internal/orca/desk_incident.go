package orca

import (
	"bufio"
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	unfixedCases   = "unfixed-cases"
	cutClocksFact  = "cut-clocks"
	cutGrace       = time.Minute
	caseMark       = "-case"
	kindStopped    = "stopped"
	kindStandDown  = "stand-down"
	kindNotOurs    = "not-ours"
	incidentBar    = 20 * time.Minute
	ccnKept        = 300
	crossDrivePage = "4000000"
)

var (
	unfixedLine    = regexp.MustCompile("(?m)^[ \\t>*-]*(?:`UNFIXED ([\\w.-]+):[ \\t]*(\\S.*?)`|UNFIXED ([\\w.-]+):[ \\t]*(\\S.*?))[ \\t]*$")
	unfixedNone    = regexp.MustCompile(`(?i)^` + namesNothing + `\b`)
	fixLaneName    = regexp.MustCompile(`\b[a-z0-9]+(?:-[a-z0-9]+)*-fix\b`)
	unknownCause   = regexp.MustCompile(`\bUNKNOWN\b`)
	handedBack     = regexp.MustCompile(`(?i)^(?:[\w-]+:\s*)?(?:NOT-OURS|stood down|stopped)\b`)
	lineage        = regexp.MustCompile(`(?:-opus|-case\d+)+$`)
	incidentPart   = regexp.MustCompile(`-[^-]+(?:-\d+)?$`)
	prNamedInBrief = regexp.MustCompile(`(?:^|[^\w/])#(\d{3,})\b|/pull/(\d+)\b`)
	textMark       = regexp.MustCompile(`^(MECHANISM|READY)\b`)
	saysMechanism  = regexp.MustCompile(`(?i)\bmechanism\b`)
	clockKinds     = []string{"mechanism", "opened", "ready", "fix-live", kindNotOurs, kindStopped, kindStandDown}
	incidentEnds   = []string{kindNotOurs, kindStandDown}
	settlingKinds  = []string{"release", "fix-live"}
)

type readBriefText struct {
	modified time.Time
	size     int64
	text     string
}

var (
	readBriefsMu sync.Mutex
	readBriefs   = map[string]readBriefText{}
)

func readBrief(path string) string {
	info, err := os.Stat(path)
	if err != nil {
		return ""
	}
	readBriefsMu.Lock()
	defer readBriefsMu.Unlock()
	if kept, found := readBriefs[path]; found && kept.size == info.Size() && kept.modified.Equal(info.ModTime()) {
		return kept.text
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	readBriefs[path] = readBriefText{info.ModTime(), info.Size(), string(raw)}
	return string(raw)
}

func firstLane(lane string) string {
	return lineage.ReplaceAllString(lane, "")
}

func incidentName(lane string) string {
	first := firstLane(lane)
	head := incidentPart.ReplaceAllString(first, "")
	if strings.Contains(head, "-") {
		return head
	}
	return first
}

func isCaseLane(lane string) bool {
	return strings.Contains(strings.TrimPrefix(lane, firstLane(lane)), caseMark)
}

func (d *Desk) names(lane string) ([]string, error) {
	stem := strings.TrimSuffix(lane, spawnedFix)
	names := []string{stem, stem + spawnedFix}
	launches, err := d.laneActions(laneBook(lane), kindLaunch, incident.ActionVerified)
	if err != nil {
		return nil, err
	}
	if len(launches) == 0 {
		return names, nil
	}
	spec, err := launchOf(launches[len(launches)-1])
	if err != nil {
		return nil, err
	}
	found := submatches(poster, readBrief(spec.Brief))
	posters := make([]string, 0, len(found))
	for _, name := range found {
		if name != coordinatorLane && !slices.Contains(names, name) && !slices.Contains(posters, name) {
			posters = append(posters, name)
		}
	}
	slices.Sort(posters)
	return slices.Concat(names, posters), nil
}

func (d *Desk) fixLaunch(container string) (*incident.Action, error) {
	d.reads.mu.Lock()
	kept, found := d.reads.fixes[container]
	d.reads.mu.Unlock()
	if found {
		return kept, nil
	}
	launch, err := d.readFixLaunch(container)
	if err != nil {
		return nil, err
	}
	d.reads.mu.Lock()
	defer d.reads.mu.Unlock()
	if d.reads.passing {
		d.reads.fixes[container] = launch
	}
	return launch, nil
}

func (d *Desk) readFixLaunch(container string) (*incident.Action, error) {
	launches, err := d.laneActions(container, kindLaunch, incident.ActionVerified)
	if err != nil || len(launches) == 0 {
		return nil, err
	}
	newest := launches[len(launches)-1]
	spec, err := launchOf(newest)
	if err != nil {
		return nil, err
	}
	order := Order{Lane: strings.TrimPrefix(container, lanePrefix), Model: spec.Model, Role: spec.Role, Brief: spec.Brief}
	text := readBrief(spec.Brief)
	if briefStanding.MatchString(text) || !order.incidentFix(text) {
		return nil, nil
	}
	return &newest, nil
}

func briefIncidents(text string) []string {
	return slices.DeleteFunc(submatches(briefIncident, text), func(name string) bool { return name == namesNothing })
}

func incidentKeys(lane string, spec launchTarget) []string {
	keys := append(briefIncidents(readBrief(spec.Brief)), incidentName(lane))
	if spec.Incident != "" {
		keys = append(keys, spec.Incident)
	}
	return keys
}

func overlaps(a, b []string) bool {
	return slices.ContainsFunc(a, func(item string) bool { return slices.Contains(b, item) })
}

func (d *Desk) siblings(lane string) ([]string, error) {
	keys := []string{incidentName(lane)}
	own, err := d.fixLaunch(laneBook(lane))
	if err != nil {
		return nil, err
	}
	if own != nil {
		spec, err := launchOf(*own)
		if err != nil {
			return nil, err
		}
		keys = incidentKeys(lane, spec)
	}
	books, err := d.containers(lanePrefix)
	if err != nil {
		return nil, err
	}
	var family []string
	for _, container := range books {
		other := strings.TrimPrefix(container, lanePrefix)
		launch, err := d.fixLaunch(container)
		if err != nil {
			return nil, err
		}
		if other == lane || launch == nil {
			continue
		}
		spec, err := launchOf(*launch)
		if err != nil {
			return nil, err
		}
		if overlaps(keys, incidentKeys(other, spec)) {
			family = append(family, other)
		}
	}
	return family, nil
}

func (d *Desk) incidentLane(ctx context.Context, monitors map[string]bool, lane string) (string, error) {
	wanted := sortedKeys(monitors)
	books, err := d.containers(lanePrefix)
	if err != nil {
		return "", err
	}
	for _, container := range books {
		other := strings.TrimPrefix(container, lanePrefix)
		if other == lane {
			continue
		}
		on, err := d.onMonitors(ctx, container, other, wanted)
		if err != nil {
			return "", err
		}
		if on {
			return other, nil
		}
	}
	value, _, err := d.factOf(deskRunner, d.cursorFact())
	if err != nil {
		return "", err
	}
	records, err := d.tail(ctx, client.Query{After: factInt(value)})
	if err != nil {
		return "", d.noteTailFailure(err)
	}
	for _, record := range records {
		parsed := parseDirective(strings.TrimSpace(record.Text))
		if !toDesk(record) || parsed == nil || parsed.Verb != verbLaunch {
			continue
		}
		spec := launchSpec.FindStringSubmatch(parsed.Rest)
		if spec != nil && spec[1] != lane && overlaps(briefMonitors(readBrief(spec[6])), wanted) {
			return spec[1], nil
		}
	}
	return d.laneNamedOnMonitor(ctx, wanted, lane)
}

func (d *Desk) onMonitors(ctx context.Context, container, other string, wanted []string) (bool, error) {
	launches, err := d.store.Actions(container, kindLaunch)
	if err != nil {
		return false, err
	}
	var matched []incident.Action
	for _, action := range launches {
		spec, err := launchOf(action)
		if err != nil {
			return false, err
		}
		if overlaps(briefMonitors(readBrief(spec.Brief)), wanted) {
			matched = append(matched, action)
		}
	}
	if len(matched) == 0 {
		return false, nil
	}
	dispatch, err := d.show(ctx, other, "")
	if err != nil {
		return false, err
	}
	live := ""
	if dispatch.active() {
		live = dispatch.ID
	}
	return slices.ContainsFunc(matched, func(action incident.Action) bool {
		return action.Status == incident.ActionAccepted || action.Status == incident.ActionStarted || (live != "" && string(action.DispatchID) == live)
	}), nil
}

func briefMonitors(text string) []string {
	return append(submatches(briefIncident, text), submatches(briefMonitor, text)...)
}

func (d *Desk) laneNamedOnMonitor(ctx context.Context, wanted []string, lane string) (string, error) {
	opened, err := d.tail(ctx, client.Query{Kinds: []string{"incident"}})
	if err != nil {
		return "", d.noteTailFailure(err)
	}
	for _, record := range opened {
		if !slices.Contains(wanted, record.Fields.Attrs["monitor"]) {
			continue
		}
		for _, other := range record.Refs.Lanes {
			if other == lane {
				continue
			}
			dispatch, err := d.show(ctx, other, "")
			if err != nil {
				return "", err
			}
			if dispatch.active() {
				return other, nil
			}
		}
	}
	return "", nil
}

func addressed(record client.Record, names []string) bool {
	return slices.ContainsFunc(names, func(name string) bool { return lanes.Addresses(record.To, name) })
}

func (d *Desk) crossDriveAnswers(ctx context.Context) ([]client.Record, error) {
	result, err := d.Runner.Run(ctx, shell.Command{Argv: []string{"cci", "tail", "--drive", d.Config.Drive, "--reader", coordinatorLane, "--kind=answer", "--json", "--budget", crossDrivePage}})
	if err != nil {
		return nil, &tailFailure{cause: err}
	}
	var records []client.Record
	lines := bufio.NewScanner(strings.NewReader(result.Stdout))
	lines.Buffer(nil, 1<<24)
	for lines.Scan() {
		if strings.TrimSpace(lines.Text()) == "" {
			continue
		}
		var record client.Record
		if err := json.Unmarshal(lines.Bytes(), &record); err != nil {
			return nil, &tailFailure{cause: err}
		}
		records = append(records, record)
	}
	return records, lines.Err()
}

func (d *Desk) incidentOwner(ctx context.Context, slug, lane string) (string, error) {
	opened, err := d.tail(ctx, client.Query{Kinds: []string{"incident"}})
	if err != nil {
		return "", err
	}
	named := make([]int64, 0, len(opened))
	for _, record := range opened {
		if record.Topic == slug || strings.Contains(record.Text, slug) {
			named = append(named, record.Seq)
		}
	}
	names, err := d.names(lane)
	if err != nil {
		return "", err
	}
	var after int64
	if len(named) > 0 {
		after = named[0]
	}
	posted, err := d.tail(ctx, client.Query{After: after, Lanes: []string{coordinatorLane}, Kinds: []string{"done", kindStopped}})
	if err != nil {
		return "", err
	}
	for _, record := range posted {
		closes := string(record.Kind) == "done" && overlapsSeqs(resolvedSeqs(record), named)
		stops := string(record.Kind) == kindStopped && addressed(record, names)
		if closes || stops {
			return fmt.Sprintf("%s@%s %s %s", record.Lane, record.Drive, record.Kind, record.Name()), nil
		}
	}
	answered, err := d.crossDriveAnswers(ctx)
	if err != nil {
		return "", err
	}
	for _, record := range answered {
		if record.Drive != d.Config.Drive && record.Lane == coordinatorLane && string(record.Kind) == "answer" && slices.Contains(record.To, coordinatorLane+"@"+d.Config.Drive) && strings.Contains(record.Text, slug) {
			return fmt.Sprintf("%s@%s %s %s", record.Lane, record.Drive, record.Kind, record.Name()), nil
		}
	}
	return "", nil
}

func overlapsSeqs(a, b []int64) bool {
	return slices.ContainsFunc(a, func(seq int64) bool { return slices.Contains(b, seq) })
}

func (d *Desk) incidentEnd(ctx context.Context, first, lane string, reported time.Time) (string, error) {
	leads, err := d.names(first)
	if err != nil {
		return "", err
	}
	backups, err := d.names(first + "-opus")
	if err != nil {
		return "", err
	}
	owners := slices.Concat(leads, backups)
	own, err := d.names(lane)
	if err != nil {
		return "", err
	}
	var since time.Time
	launch, err := d.fixLaunch(laneBook(first))
	if err != nil {
		return "", err
	}
	if launch != nil {
		if since, err = verifiedAt(*launch); err != nil {
			return "", err
		}
	}
	posted, err := d.tail(ctx, client.Query{Lanes: slices.Concat(owners, own, []string{coordinatorLane}), Kinds: []string{kindNotOurs, kindStandDown, kindStopped}})
	if err != nil {
		return "", err
	}
	audience := slices.Concat(owners, own)
	for _, record := range posted {
		if record.At.Before(since) {
			continue
		}
		kind := string(record.Kind)
		ownersEnded := slices.Contains(owners, record.Lane) && slices.Contains(incidentEnds, kind)
		rootEnded := record.Lane == coordinatorLane && (kind == kindStandDown || kind == kindStopped) && standsDown(record, audience)
		laneStopped := slices.Contains(own, record.Lane) && kind == kindStopped && !reported.IsZero() && record.At.Before(reported)
		if ownersEnded || rootEnded || laneStopped {
			return fmt.Sprintf("%s %s %s", record.Name(), strings.ToUpper(kind), record.Lane), nil
		}
	}
	return "", nil
}

func verifiedAt(action incident.Action) (time.Time, error) {
	at, _ := action.VerificationReceipt["at"].(string)
	return store.Parse(at)
}

func (d *Desk) laneStoodDown(ctx context.Context, lane string) (string, error) {
	launches, err := d.laneActions(laneBook(lane), kindLaunch, incident.ActionVerified)
	if err != nil {
		return "", err
	}
	var began time.Time
	for _, launch := range launches {
		at, err := verifiedAt(launch)
		if err != nil {
			return "", err
		}
		if at.After(began) {
			began = at
		}
	}
	row, found, err := d.Registry.Lane(lane)
	if err != nil {
		return "", err
	}
	if found && slices.Contains([]store.LaneState{store.LaneLaunching, store.LaneDesired}, row.State) && row.Since.After(began) {
		began = row.Since
	}
	names, err := d.names(lane)
	if err != nil {
		return "", err
	}
	posted, err := d.readStandDowns(ctx)
	if err != nil {
		return "", err
	}
	for _, record := range posted {
		if standsDown(record, names) && !record.At.Before(began) {
			return record.Name(), nil
		}
	}
	return "", nil
}

type passReads struct {
	mu        sync.Mutex
	passing   bool
	records   map[string][]client.Record
	fixes     map[string]*incident.Action
	questions []orcaMessage
	asked     bool
	listing   sync.Mutex
	open      map[string]bool
	statuses  map[string]string
	shown     map[shownKey]*laneDispatch
	peeked    map[peekKey][]orcaMessage
}

type shownKey struct{ lane, dispatch string }

type peekKey struct{ terminal, environment string }

func (d *Desk) startPass() {
	d.store.StartPass()
	d.reads.reset(true)
}

func (d *Desk) endPass() {
	d.reads.reset(false)
}

func (r *passReads) reset(passing bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.passing = passing
	r.records = map[string][]client.Record{}
	r.fixes = map[string]*incident.Action{}
	r.questions, r.asked = nil, false
	r.open, r.statuses = nil, map[string]string{}
	r.shown = map[shownKey]*laneDispatch{}
	r.peeked = map[peekKey][]orcaMessage{}
}

func (r *passReads) forgetDispatch(dispatch string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	maps.DeleteFunc(r.shown, func(key shownKey, _ *laneDispatch) bool { return key.dispatch == dispatch })
}

func (r *passReads) forgetTerminal(terminal string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	maps.DeleteFunc(r.peeked, func(key peekKey, _ []orcaMessage) bool { return key.terminal == terminal })
}

func (r *passReads) forgetQuestions() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.questions, r.asked = nil, false
}

func (r *passReads) forgetWorkers() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.open, r.statuses = nil, map[string]string{}
}

func (d *Desk) passTail(ctx context.Context, query client.Query) ([]client.Record, error) {
	key := fmt.Sprint(query.Lanes, query.Kinds, query.Since)
	d.reads.mu.Lock()
	kept, found := d.reads.records[key]
	d.reads.mu.Unlock()
	if found {
		return slices.Clone(kept), nil
	}
	records, err := d.tail(ctx, query)
	if err != nil {
		return nil, err
	}
	d.reads.mu.Lock()
	defer d.reads.mu.Unlock()
	if d.reads.passing {
		d.reads.records[key] = records
	}
	return slices.Clone(records), nil
}

func (d *Desk) readStandDowns(ctx context.Context) ([]client.Record, error) {
	return d.passTail(ctx, client.Query{Lanes: []string{coordinatorLane, supervisorLane}, Kinds: []string{kindStandDown, kindStopped}})
}

func standsDown(record client.Record, names []string) bool {
	if string(record.Kind) == kindStandDown && len(record.To) > 0 {
		return slices.Contains(names, record.To[0]) && !slices.Contains(names, record.Fields.Attrs["successor"])
	}
	return addressed(record, names)
}

func (d *Desk) holdsEndedLaunch(ctx context.Context, container, lane string, action incident.Action) (bool, error) {
	spec, err := launchOf(action)
	if err != nil {
		return false, err
	}
	posted, err := store.Parse(cmp.Or(spec.Posted, action.AcceptedAt))
	if err != nil {
		return false, err
	}
	ended, err := d.endedSince(ctx, lane, posted)
	if err != nil || ended == "" {
		return false, err
	}
	if _, err := d.attempt(container, func(tx *incident.Tx) error {
		_, err := tx.Fail(action.ActionID, ended)
		return err
	}); err != nil {
		return false, err
	}
	return true, d.recordTo(ctx, fmt.Sprintf("escalation:%s/%s:ended", container, action.ActionID), fmt.Sprintf("WITHDRAWN %s %s: %s after its launch line was posted; nothing was launched", action.ActionID, lane, ended), verdictTo(spec.By))
}

func (d *Desk) holdsCaseLaunch(ctx context.Context, container, lane string, action incident.Action) (bool, error) {
	if !strings.HasPrefix(action.ActionID, unfixedKey) {
		return false, nil
	}
	ended, err := d.incidentEnd(ctx, firstLane(lane), lane, zeroTime)
	var failure *tailFailure
	if errors.As(err, &failure) {
		return true, d.noteTailFailure(err)
	}
	if err != nil || ended == "" {
		return false, err
	}
	if err := d.store.Edit(container, func(tx *incident.Tx) error {
		_, err := tx.Fail(action.ActionID, "the incident ended with "+ended)
		return err
	}); err != nil {
		return false, err
	}
	return true, d.record(fmt.Sprintf("escalation:%s/%s:ended", container, action.ActionID), fmt.Sprintf("WITHDRAWN %s %s: the incident ended with %s; nothing was launched", action.ActionID, lane, ended))
}

func (d *Desk) holdsOwnedLaunch(ctx context.Context, container, lane string, action incident.Action) (bool, error) {
	spec, err := launchOf(action)
	if err != nil {
		return false, err
	}
	if spec.Incident == "" {
		return false, nil
	}
	owner, err := d.incidentOwner(ctx, spec.Incident, lane)
	var failure *tailFailure
	if errors.As(err, &failure) {
		return true, d.noteTailFailure(err)
	}
	if err != nil || owner == "" {
		return false, err
	}
	why := fmt.Sprintf("incident %s owned by %s", spec.Incident, owner)
	if err := d.store.Edit(container, func(tx *incident.Tx) error {
		_, err := tx.Fail(action.ActionID, why)
		return err
	}); err != nil {
		return false, err
	}
	return true, d.escalate(action.ActionID, "INCIDENT-OWNED", lane, why+"; no launch")
}

type cutClock struct {
	Lane       string   `json:"lane"`
	PR         string   `json:"pr"`
	Commit     string   `json:"commit"`
	Components []string `json:"components"`
	PRs        []string `json:"prs"`
	Since      string   `json:"since"`
	Resolves   int64    `json:"resolves"`
}

func (d *Desk) cutClockOf(key string) (cutClock, map[string]any, error) {
	value, _, err := d.factOf(deskRunner, cutClocksFact)
	if err != nil {
		return cutClock{}, nil, err
	}
	clocks, _ := value.(map[string]any)
	held, _ := clocks[key].(map[string]any)
	raw, err := json.Marshal(held)
	if err != nil {
		return cutClock{}, nil, err
	}
	var clock cutClock
	return clock, clocks, json.Unmarshal(raw, &clock)
}

func (d *Desk) holdsCutLaunch(ctx context.Context, container, lane string, action incident.Action) (bool, error) {
	key, isCut := strings.CutPrefix(action.ActionID, cutLaunchKey)
	if !isCut {
		return false, nil
	}
	silent, err := d.store.Action(deskRunner, "escalation:cut:"+key)
	if err != nil {
		return false, err
	}
	if silent.Status == incident.ActionAccepted {
		return true, nil
	}
	clock, clocks, err := d.cutClockOf(key)
	if err != nil {
		return false, err
	}
	withdrawn, err := d.cutWithdrawn(ctx, key, clock, silent)
	var failure *tailFailure
	if errors.As(err, &failure) {
		return true, d.noteTailFailure(err)
	}
	if err != nil {
		return false, err
	}
	if withdrawn == "" {
		at, _ := silent.Response["at"].(string)
		posted, err := store.Parse(at)
		if err != nil {
			return false, err
		}
		return d.Now().Sub(posted) < cutGrace, nil
	}
	if err := d.store.Edit(container, func(tx *incident.Tx) error {
		_, err := tx.Fail(action.ActionID, withdrawn)
		return err
	}); err != nil {
		return false, err
	}
	clocks[key].(map[string]any)["ended"] = "withdrawn: " + withdrawn
	if err := d.setFact(deskRunner, cutClocksFact, clocks); err != nil {
		return false, err
	}
	return true, d.record(fmt.Sprintf("escalation:%s/%s:withdrawn", container, action.ActionID), fmt.Sprintf("WITHDRAWN %s %s: %s; nothing was launched", action.ActionID, lane, withdrawn))
}

func receiptSeq(value string) int64 {
	digits := strings.TrimPrefix(strings.TrimSuffix(value, " (duplicate)"), "#")
	seq, err := strconv.ParseInt(digits, 10, 64)
	if err != nil {
		return 0
	}
	return seq
}

func (d *Desk) rootResolved(ctx context.Context, seq int64) (*client.Record, error) {
	replies, err := d.tail(ctx, client.Query{After: seq, Lanes: []string{coordinatorLane}})
	if err != nil {
		return nil, err
	}
	for i := range replies {
		if slices.Contains(resolvedSeqs(replies[i]), seq) {
			return &replies[i], nil
		}
	}
	return nil, nil
}

func (d *Desk) cutWithdrawn(ctx context.Context, key string, clock cutClock, silent incident.Action) (string, error) {
	posted, _ := silent.Response["posted"].(string)
	if seq := receiptSeq(posted); seq != 0 {
		resolved, err := d.rootResolved(ctx, seq)
		if err != nil {
			return "", err
		}
		if resolved != nil {
			return fmt.Sprintf("%s resolves CUT-SILENT #%d", resolved.Name(), seq), nil
		}
	}
	closed, err := d.cutClosed(ctx, key, clock)
	if err != nil || closed != "" {
		return closed, err
	}
	return d.cutAnswer(ctx, clock)
}

func (d *Desk) cutRecords(ctx context.Context, clock cutClock, kinds ...string) ([]client.Record, error) {
	since, err := store.Parse(clock.Since)
	if err != nil {
		return nil, err
	}
	posted, err := d.tail(ctx, client.Query{Kinds: kinds})
	if err != nil {
		return nil, err
	}
	return slices.DeleteFunc(posted, func(record client.Record) bool {
		if record.At.Before(since) && record.Seq != clock.Resolves {
			return true
		}
		hitsTarget := overlaps(record.Refs.Targets, clock.Components)
		if record.Lane == clock.Lane {
			return len(record.Refs.Targets) > 0 && !hitsTarget
		}
		return clock.PR == "" || record.Lane == deskRunner || (!hitsTarget && !prNamed(record, clock.PR))
	}), nil
}

func (d *Desk) cutAnswer(ctx context.Context, clock cutClock) (string, error) {
	answers, err := d.cutRecords(ctx, clock, "release", "refuse")
	if err != nil {
		return "", err
	}
	for _, record := range answers {
		if string(record.Kind) == "refuse" || len(record.Refs.Builds) > 0 {
			return fmt.Sprintf("%s %s", record.Name(), record.Kind), nil
		}
	}
	return "", nil
}

func (d *Desk) inRepo(record client.Record, pr string) bool {
	for _, ref := range record.Refs.PRs {
		if ref.Repo == "" && strconv.Itoa(ref.Number) == pr {
			return true
		}
		if strings.EqualFold(ref.Repo, d.Config.LandingRepo) && strconv.Itoa(ref.Number) == pr {
			return true
		}
	}
	return false
}

func (d *Desk) releaseOwned(ctx context.Context, clock cutClock, since time.Time) (*client.Record, error) {
	posted, err := d.tail(ctx, client.Query{Since: since})
	if err != nil {
		return nil, err
	}
	for i := range posted {
		record := posted[i]
		if record.Lane == deskRunner || record.Lane == coordinatorLane || !lanes.Addresses(record.To, clock.Lane) {
			continue
		}
		named := slices.Clone(record.Refs.Targets)
		for _, stack := range record.Refs.Stacks {
			head, _, _ := strings.Cut(stack, "/")
			named = append(named, head)
		}
		commit := clock.Commit
		if len(commit) > 7 {
			commit = commit[:7]
		}
		if overlaps(named, clock.Components) || prNamed(record, clock.PR) || d.inRepo(record, clock.PR) || strings.Contains(record.Text, commit) {
			return &posted[i], nil
		}
	}
	return nil, nil
}

func (d *Desk) cutClosed(ctx context.Context, key string, clock cutClock) (string, error) {
	if clock.PR != "" && d.Config.LandingRepo != "" {
		landing, err := d.known(landingBook, "release:"+clock.PR)
		if err != nil {
			return "", err
		}
		if landing != nil && landing.Status == incident.ActionVerified {
			return "#" + clock.PR + " settled", nil
		}
	}
	since, err := store.Parse(clock.Since)
	if err != nil {
		return "", err
	}
	if digits, isOrder := strings.CutPrefix(key, "go:"); isOrder {
		seq, err := strconv.ParseInt(digits, 10, 64)
		if err != nil {
			return "", err
		}
		resolved, err := d.rootResolved(ctx, seq)
		if err != nil {
			return "", err
		}
		if resolved != nil {
			return fmt.Sprintf("%s resolves", resolved.Name()), nil
		}
	}
	if clock.PR != "" {
		owned, err := d.releaseOwned(ctx, clock, since)
		if err != nil {
			return "", err
		}
		if owned != nil {
			return fmt.Sprintf("%s owns the release on cci %s", owned.Lane, owned.Name()), nil
		}
	}
	if len(clock.PRs) == 0 {
		return "", nil
	}
	posted, err := d.tail(ctx, client.Query{Kinds: settlingKinds, Since: since})
	if err != nil {
		return "", err
	}
	for _, record := range posted {
		if record.Lane != deskRunner && slices.ContainsFunc(clock.PRs, func(pr string) bool { return prNamed(record, pr) || d.inRepo(record, pr) }) {
			return fmt.Sprintf("%s %s", record.Name(), record.Kind), nil
		}
	}
	return "", nil
}

func (d *Desk) heldWorktree(ctx context.Context, lane string, pushedHeads []string) (string, error) {
	raw, err := os.ReadFile(d.receiptPath(lane, ".worktree"))
	if err != nil {
		return "", nil
	}
	path := strings.TrimSpace(string(raw))
	if !isDir(path) {
		return "", nil
	}
	listed, code, err := d.capture(ctx, nil, "git", "-C", path, "rev-list", "origin/HEAD..HEAD")
	if err != nil {
		return "", err
	}
	var ahead []string
	if code == 0 {
		ahead = strings.Fields(listed)
	}
	if pushedHeads != nil && !slices.ContainsFunc(ahead, func(commit string) bool {
		return slices.ContainsFunc(pushedHeads, func(head string) bool { return strings.HasPrefix(commit, head) })
	}) {
		return "", nil
	}
	branch, _, err := d.capture(ctx, nil, "git", "-C", path, "branch", "--show-current")
	if err != nil {
		return "", err
	}
	if len(ahead) > 0 {
		return fmt.Sprintf("%s: %s, %d ahead of trunk", path, strings.TrimSpace(branch), len(ahead)), nil
	}
	upstream, code, err := d.capture(ctx, nil, "git", "-C", path, "rev-parse", "--abbrev-ref", "@{upstream}")
	if err != nil || code != 0 {
		return "", err
	}
	return fmt.Sprintf("%s: %s, pushed to %s", path, strings.TrimSpace(branch), strings.TrimSpace(upstream)), nil
}

func (d *Desk) duplicateHolder(ctx context.Context, lane, text string) (string, error) {
	if briefReader.MatchString(text) {
		return "", nil
	}
	keys := briefIncidents(text)
	pushed := map[string][]string{}
	seen := map[string]bool{}
	var named [][]string
	if !briefStanding.MatchString(text) {
		named = prNamedInBrief.FindAllStringSubmatch(briefOwnLines(text), -1)
	}
	for _, found := range named {
		pr := found[1] + found[2]
		if seen[pr] {
			continue
		}
		seen[pr] = true
		records, err := d.tail(ctx, client.Query{Kinds: []string{"head", "opened"}, PR: pr})
		if err != nil {
			return "", d.noteTailFailure(err)
		}
		number, err := strconv.Atoi(pr)
		if err != nil {
			return "", err
		}
		for _, record := range records {
			if head := d.pushedHead(record, number); head != "" {
				pushed[record.Lane] = append(pushed[record.Lane], head)
			}
		}
	}
	books, err := d.containers(lanePrefix)
	if err != nil {
		return "", err
	}
	for _, container := range books {
		other := strings.TrimPrefix(container, lanePrefix)
		holder, err := d.holderOf(ctx, container, other, lane, keys, pushed[other])
		if err != nil || holder != "" {
			return holder, err
		}
	}
	return "", nil
}

func (d *Desk) pushedHead(record client.Record, pr int) string {
	named, _, _ := strings.Cut(cmp.Or(bus.HeadNamed(record, d.Config.LandingRepo, pr), record.Fields.Attrs["head"]), " ")
	if _, moved, found := strings.Cut(named, ".."); found {
		named = moved
	}
	return strings.ToLower(named)
}

func (d *Desk) holderOf(ctx context.Context, container, other, lane string, keys, pushedHeads []string) (string, error) {
	launched, err := d.store.Actions(container, kindLaunch)
	if err != nil || other == lane || len(launched) == 0 {
		return "", err
	}
	ours := slices.Clone(keys)
	fix, err := d.fixLaunch(container)
	if err != nil {
		return "", err
	}
	if fix != nil {
		ours = append(ours, incidentName(lane))
	}
	spec, err := launchOf(launched[len(launched)-1])
	if err != nil {
		return "", err
	}
	if overlaps(ours, incidentKeys(other, spec)) {
		pushedHeads = nil
	} else if len(pushedHeads) == 0 {
		return "", nil
	}
	dispatch, err := d.show(ctx, other, "")
	if err != nil || !dispatch.active() {
		return "", err
	}
	held, err := d.heldWorktree(ctx, other, pushedHeads)
	if err != nil || held == "" {
		return "", err
	}
	return other + ": " + held, nil
}

func unfixedCasesOf(text, lane string, dropped []config.Refusal) ([]string, map[string]string) {
	var order []string
	cases := map[string]string{}
	for _, found := range unfixedLine.FindAllStringSubmatch(text, -1) {
		named, written := found[1]+found[3], found[2]+found[4]
		if _, _, ruled := config.RefusedLine(dropped, written); named != lane || unfixedNone.MatchString(written) || ownedElsewhere(written, lane) || ruled {
			continue
		}
		known := collapsed(written)
		if _, held := cases[known]; !held {
			cases[known] = written
			order = append(order, known)
		}
	}
	return order, cases
}

func ownedElsewhere(written, lane string) bool {
	return slices.ContainsFunc(fixLaneName.FindAllString(written, -1), func(named string) bool { return firstLane(named) != firstLane(lane) })
}

func isHandedBack(message orcaMessage) bool {
	first, _, _ := strings.Cut(strings.TrimSpace(message.Body), "\n")
	return handedBack.MatchString(strings.TrimSpace(message.Subject)) || handedBack.MatchString(strings.TrimSpace(first))
}

func (d *Desk) unfixed(ctx context.Context, lane string, message orcaMessage) error {
	known, cases := unfixedCasesOf(message.Body, lane, d.Config.RefusedBriefs)
	exists, err := d.store.Exists(laneBook(lane))
	if err != nil || len(known) == 0 || isHandedBack(message) || !exists {
		return err
	}
	launch, err := d.fixLaunch(laneBook(lane))
	if err != nil || launch == nil {
		return err
	}
	first := firstLane(lane)
	container, err := d.lane(first)
	if err != nil {
		return err
	}
	value, _, err := d.factOf(container, unfixedCases)
	if err != nil {
		return err
	}
	taken := stringList(value)
	fresh := make([]string, 0, len(known))
	indexes := map[string]int{}
	for i, name := range known {
		if !slices.Contains(taken, name) {
			fresh = append(fresh, name)
			indexes[name] = i + 1
		}
	}
	if len(fresh) == 0 {
		return nil
	}
	written := make([]string, len(fresh))
	for i, name := range fresh {
		written[i] = cases[name]
	}
	key := unfixedKey + message.ID
	unlaunched := fmt.Sprintf("UNFIXED %s: %s | no case lane launched", lane, strings.Join(written, "; "))
	by, err := d.withdrawnBy(lane)
	if err != nil {
		return err
	}
	if by != "" {
		return d.record(escalationPref+key, fmt.Sprintf("%s: the root withdrew %s (%s)", unlaunched, lane, by))
	}
	ended, unknown, err := d.caseGate(ctx, first, lane, *launch, message)
	if err != nil {
		return err
	}
	switch {
	case ended != "":
		return d.record(escalationPref+key, unlaunched+": the incident ended with "+ended)
	case isCaseLane(lane):
		return d.offerCases(ctx, key, first, lane, *launch, written, "a case lane reports new cases, past the one-generation cap")
	case unknown != "":
		return d.offerCases(ctx, key, first, lane, *launch, written, unknown+", so no case lane launches until the root ratifies it")
	}
	room := min(max(d.Config.CaseLanes-len(taken), 0), len(fresh))
	for _, name := range fresh[:room] {
		next, err := d.caseName(first)
		if err != nil {
			return err
		}
		took, err := d.caseLane(ctx, next, lane, *launch, cases[name], fmt.Sprintf("%s:%d", key, indexes[name]))
		if err != nil {
			return err
		}
		if took {
			taken = append(taken, name)
			if err := d.setFact(container, unfixedCases, taken); err != nil {
				return err
			}
		}
	}
	if room == len(fresh) {
		return nil
	}
	return d.offerCases(ctx, key, first, lane, *launch, written[room:], fmt.Sprintf("past the cap of %d case lanes per incident", d.Config.CaseLanes))
}

func (d *Desk) caseGate(ctx context.Context, first, lane string, launch incident.Action, message orcaMessage) (string, string, error) {
	created, err := store.Parse(message.CreatedAt)
	if err != nil {
		return "", "", err
	}
	ended, err := d.incidentEnd(ctx, first, lane, created)
	unknown := ""
	if err == nil && ended == "" {
		unknown, err = d.unknownCauseOf(ctx, lane, launch, message)
	}
	var failure *tailFailure
	if errors.As(err, &failure) {
		return "", "cci could not be read to tell whether the incident is open and its cause known", d.noteTailFailure(err)
	}
	return ended, unknown, err
}

func (d *Desk) caseName(first string) (string, error) {
	books, err := d.containers(lanePrefix)
	if err != nil {
		return "", err
	}
	for n := 1; ; n++ {
		name := fmt.Sprintf("%s%s%d", first, caseMark, n)
		if !slices.Contains(books, laneBook(name)) {
			return name, nil
		}
	}
}

func clockMark(record client.Record) string {
	kind := string(record.Kind)
	if slices.Contains(clockKinds, kind) {
		return kind
	}
	if kind == kindReport || kind == "incident" {
		if mark := textMark.FindStringSubmatch(record.Text); mark != nil {
			return strings.ToLower(mark[1])
		}
	}
	if saysMechanism.MatchString(record.Text) {
		return "mechanism"
	}
	return ""
}

func (d *Desk) unknownCauseOf(ctx context.Context, lane string, launch incident.Action, message orcaMessage) (string, error) {
	if unknownCause.MatchString(message.Subject + "\n" + message.Body) {
		return "its report " + message.ID + " says the cause is UNKNOWN", nil
	}
	launched, err := verifiedAt(launch)
	if err != nil {
		return "", err
	}
	type mark struct {
		at         time.Time
		name, text string
	}
	var newest *mark
	consider := func(candidate mark) {
		if !candidate.at.Before(launched) && (newest == nil || candidate.at.After(newest.at)) {
			newest = &candidate
		}
	}
	records, err := d.laneRecords(ctx, lane)
	if err != nil {
		return "", err
	}
	for _, record := range records {
		if clockMark(record) == "mechanism" {
			consider(mark{at: record.At, name: record.Name(), text: record.Text})
		}
	}
	value, _, err := d.factOf(laneBook(lane), milestoneFact)
	if err != nil {
		return "", err
	}
	statuses, _ := value.(map[string]any)
	for id, entry := range statuses {
		status, _ := entry.(map[string]any)
		if status["name"] != "mechanism" {
			continue
		}
		raw, err := os.ReadFile(d.messageFile(id))
		if err != nil {
			continue
		}
		stamp, _ := status["at"].(string)
		at, err := store.Parse(stamp)
		if err != nil {
			return "", err
		}
		consider(mark{at: at, name: "its status " + id, text: string(raw)})
	}
	if newest != nil && unknownCause.MatchString(newest.text) {
		return "its mechanism " + newest.name + " says the cause is UNKNOWN", nil
	}
	return "", nil
}

func (d *Desk) briefsDir() string {
	return filepath.Join(filepath.Dir(filepath.Dir(d.Config.DeskInbox)), "briefs")
}

func (d *Desk) reference(name string) string {
	return filepath.Join(d.Plugin, "skills", "long-running", "reference", name)
}

func (d *Desk) driveFacts() string {
	if d.Config.AlertFacts == "" {
		return "none recorded for this drive"
	}
	return strings.TrimSpace(readBrief(d.Config.AlertFacts))
}

func (d *Desk) openedPRs(ctx context.Context, lane string, launch incident.Action) (string, error) {
	launched, err := verifiedAt(launch)
	if err != nil {
		return "", err
	}
	opened, err := d.laneRecords(ctx, lane, "opened")
	if err != nil {
		return "unknown, cci could not be read", d.noteTailFailure(err)
	}
	seen := map[string]bool{}
	for _, record := range opened {
		if record.At.Before(launched) {
			continue
		}
		for _, ref := range record.Refs.PRs {
			name := ref.String()
			if !strings.Contains(name, "#") {
				name = "#" + name
			}
			seen[name] = true
		}
	}
	if len(seen) == 0 {
		return "none opened yet", nil
	}
	return strings.Join(sortedKeys(seen), " "), nil
}

func (d *Desk) caseBrief(ctx context.Context, key, lane, parent string, launch incident.Action, written string) (string, string, error) {
	prs, err := d.openedPRs(ctx, parent, launch)
	if err != nil {
		return "", "", err
	}
	spec, err := launchOf(launch)
	if err != nil {
		return "", "", err
	}
	template, err := os.ReadFile(d.reference("unfixed-case-brief.md"))
	if err != nil {
		return "", "", err
	}
	now := d.Now()
	entry := fmt.Sprintf("INCIDENT %s: unfixed case brief for %s: %s", parent, lane, written)
	return d.attachBrief(ctx, lane, entry, func(log string) (string, error) {
		text := strings.NewReplacer("{{", "{", "}}", "}", "{lane}", lane, "{parent}", parent, "{case}", written, "{parent_brief}", spec.Brief, "{prs}", prs,
			"{onset}", pacificClock(now), "{reported}", pacificClock(now), "{due}", pacificClock(now.Add(incidentBar)), "{log}", log, "{facts}", d.driveFacts()).Replace(string(template))
		return d.sliced(ctx, key, lane, text)
	})
}

func (d *Desk) caseLane(ctx context.Context, lane, parent string, launch incident.Action, written, key string) (bool, error) {
	log := escalationPref + key
	line := fmt.Sprintf("INCIDENT %s: its fix does not cover: %s", parent, written)
	refusal, err := d.launchRefusal(ctx, key, Order{Lane: lane, Model: agentIncident, Effort: "high", Brief: d.reference("unfixed-case-brief.md")})
	if err != nil {
		return false, err
	}
	if refusal != "" {
		return false, d.record(log, fmt.Sprintf("%s | case lane %s not launched: %s", line, lane, refusal))
	}
	brief, failure, err := d.caseBrief(ctx, key, lane, parent, launch, written)
	if err != nil {
		return false, err
	}
	if failure != "" {
		return false, d.record(log, fmt.Sprintf("%s | case lane %s not launched: %s", line, lane, failure))
	}
	if _, _, err := d.acceptLaunch(key, lane, launchTarget{Model: agentIncident, Effort: "high", Brief: brief, By: deskRunner}); err != nil {
		return false, err
	}
	return true, d.record(log, fmt.Sprintf("%s | case lane %s launching on incident high, brief %s; root: ratify, and have the fix's one post to the reporter say this case is still open", line, lane, brief))
}

func (d *Desk) offerCases(ctx context.Context, key, first, parent string, launch incident.Action, cases []string, reason string) error {
	held, err := d.known(deskRunner, escalationPref+key)
	if err != nil || held != nil {
		return err
	}
	offers := make([]string, 0, len(cases))
	books, err := d.containers(lanePrefix)
	if err != nil {
		return err
	}
	n := 0
	for _, written := range cases {
		lane := ""
		for lane == "" {
			n++
			if name := fmt.Sprintf("%s%s%d", first, caseMark, n); !slices.Contains(books, laneBook(name)) {
				lane = name
			}
		}
		brief, failure, err := d.caseBrief(ctx, key, lane, parent, launch, written)
		if err != nil {
			return err
		}
		if failure != "" {
			offers = append(offers, written+" | no launch line: "+failure)
			continue
		}
		offers = append(offers, fmt.Sprintf("%s | to launch its lane, post `orca-desk: launch %s NOW incident high brief=%s`", written, lane, brief))
	}
	return d.record(escalationPref+key, fmt.Sprintf("DECIDE %s %s: %s: %s", key, parent, reason, strings.Join(offers, "; ")))
}

func (d *Desk) briefs(ctx context.Context, args ...string) (string, int, error) {
	return d.capture(ctx, nil, append([]string{"ccn", "-R", d.Config.BriefsRepo}, args...)...)
}

func (d *Desk) readBriefs(ctx context.Context, args ...string) (string, int, error) {
	return d.captureIdempotent(ctx, append([]string{"ccn", "-R", d.Config.BriefsRepo}, args...)...)
}

func ccnFailure(said string, code int) string {
	trimmed := strings.TrimSpace(said)
	if trimmed == "" {
		return fmt.Sprintf("ccn exited %d", code)
	}
	if runes := []rune(trimmed); len(runes) > ccnKept {
		return string(runes[len(runes)-ccnKept:])
	}
	return trimmed
}

func (d *Desk) resolveBriefsLog(ctx context.Context) (string, error) {
	stale := d.Config.BriefsLog
	if stale != "" {
		_, code, err := d.readBriefs(ctx, "log", "show", stale)
		if err != nil || code == 0 {
			return "", err
		}
	}
	title := "briefs: " + d.Config.Drive
	listed, code, err := d.readBriefs(ctx, "log", "list", "--json")
	if err != nil {
		return "", err
	}
	if code != 0 {
		return ccnFailure(listed, code), nil
	}
	var logs []struct {
		ID    string `json:"id"`
		Title string `json:"title"`
	}
	if err := json.Unmarshal([]byte(listed), &logs); err != nil {
		return "", err
	}
	found := ""
	for _, log := range logs {
		if log.Title == title {
			found = log.ID
			break
		}
	}
	if found == "" {
		added, code, err := d.briefs(ctx, "log", "add", title, "--label", "program:"+d.Config.Drive, "--json")
		if err != nil {
			return "", err
		}
		if code != 0 {
			return ccnFailure(added, code), nil
		}
		var made struct {
			ID string `json:"id"`
		}
		if err := json.Unmarshal([]byte(added), &made); err != nil {
			return "", err
		}
		found = made.ID
	}
	d.Config.BriefsLog = found
	return "", d.escalate("briefs-log:"+or(stale, "unset"), "BRIEFS-LOG", "runner", fmt.Sprintf("briefs log %s does not resolve on %s; briefs now attach to %s (%s), so set orca.briefs.log to it", or(stale, "(unset)"), d.Config.BriefsRepo, found, title))
}

func (d *Desk) sliced(ctx context.Context, key, lane, text string) (string, error) {
	staging, err := os.MkdirTemp("", "lr-relay-brief")
	if err != nil {
		return "", err
	}
	defer func() { _ = os.RemoveAll(staging) }()
	brief := filepath.Join(staging, lane+".md")
	if err := os.WriteFile(brief, []byte(text), 0o600); err != nil {
		return "", err
	}
	unread, err := d.refreshSlice(ctx, lane, brief, stepIncident, agentIncident)
	if err != nil {
		return "", err
	}
	if err := d.sliceUnread(key, lane, unread); err != nil {
		return "", err
	}
	raw, err := os.ReadFile(brief)
	return string(raw), err
}

func (d *Desk) attachBrief(ctx context.Context, lane, entry string, render func(log string) (string, error)) (string, string, error) {
	failure, err := d.resolveBriefsLog(ctx)
	if err != nil {
		return "", "", err
	}
	if failure != "" {
		return "", "the briefs log did not resolve: " + failure, nil
	}
	text, err := render(d.Config.BriefsLog)
	if err != nil {
		return "", "", err
	}
	staging, err := os.MkdirTemp("", "lr-relay-attach")
	if err != nil {
		return "", "", err
	}
	defer func() { _ = os.RemoveAll(staging) }()
	name := lane + ".full.md"
	attached := filepath.Join(staging, name)
	if err := os.WriteFile(attached, []byte(text), 0o600); err != nil {
		return "", "", err
	}
	said, code, err := d.briefs(ctx, "log", "append", d.Config.BriefsLog, "--entry", entry, "--attach", attached, "--replace")
	if err != nil {
		return "", "", err
	}
	if code != 0 {
		return "", "the brief did not attach: " + ccnFailure(said, code), nil
	}
	brief := filepath.Join(d.briefsDir(), name)
	if err := os.MkdirAll(filepath.Dir(brief), 0o750); err != nil {
		return "", "", err
	}
	return brief, "", os.WriteFile(brief, []byte(text), 0o600)
}
