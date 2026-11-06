package orca

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"os"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	clockFacts    = "incident-clocks"
	clockWork     = "incident-clock-work"
	clockBackup   = "-opus"
	clockRetry    = ":retry"
	clockLookback = 14 * 24 * time.Hour
	clockFixLive  = "fix-live"
	clockIncident = "incident"
	clockDecide   = "decide"
	clockOpen     = "open"
	clockReady    = "ready"
	clockEffort   = "xhigh"
	fallbackBrief = `ccx: lane=%[1]s role=incident effort=xhigh

# %[1]s: Opus fallback for %[2]s

You are the Opus fallback for ` + "`%[2]s`" + `. The orca desk's incident clock started you
%[3]d minutes after ` + "`%[2]s`" + ` launched, because ` + "`%[2]s`" + ` had posted no %[4]s.
Work the incident in the brief below in parallel with ` + "`%[2]s`" + `; the first PR wins.
Before you start, read what ` + "`%[2]s`" + ` has posted with
` + "`cci tail --drive %[5]s --lane %[2]s --since 2h`" + `, so you repeat none of it.
Post on cci as ` + "`%[1]s`" + `, and address ` + "`%[2]s`" + ` and ` + "`root`" + ` on every record.
Wherever the brief below tells its lane to post or report as ` + "`%[2]s`" + `, post as
` + "`%[1]s`" + `.

The brief ` + "`%[2]s`" + ` was launched on follows, unchanged.

%[6]s`
)

var (
	clockClosed     = regexp.MustCompile(`\bINCIDENT (?:\S+ CLOSED|CLOSED \S+)\b|\bis a closed incident\b`)
	clockGates      = []string{"dev-red", "smoke", "watch", "red"}
	clockScopedOut  = regexp.MustCompile(`(?i)\bactive no\b|\b(?:no|zero) customer\b|\bfalse alarm\b`)
	clockRuledOut   = regexp.MustCompile(`(?i)\bactive no\b`)
	clockStopped    = regexp.MustCompile(`^(?:\d{1,2}:\d{2} [AP]M )?STOPPED\b`)
	clockEnds       = []string{clockFixLive, kindNotOurs, kindStopped, kindStandDown, clockReady}
	clockMilestones = []string{"mechanism", "opened", clockReady}
	clockLabels     = map[string]string{"mechanism": "mechanism", "opened": "PR", clockReady: "ready"}
)

type incidentClockState struct {
	Key      string            `json:"key"`
	Launched string            `json:"launched"`
	Reached  map[string]string `json:"reached"`
	Ended    string            `json:"ended"`
}

type clockStamp struct {
	name string
	at   time.Time
}

func (d *Desk) clockPass(ctx context.Context) error {
	err := d.incidentClock(ctx)
	if err == nil {
		err = d.inProcess(ctx)
	}
	return d.noteTailFailure(err)
}

func (d *Desk) incidentClock(ctx context.Context) error {
	watching, err := d.clockLanes()
	if err != nil {
		return err
	}
	var incidents []client.Record
	for _, lane := range watching {
		container := laneBook(lane)
		kept, err := d.clockOf(lane)
		if err != nil {
			return err
		}
		launch, err := d.fixLaunch(container)
		if err != nil {
			return err
		}
		if launch == nil {
			if err := d.pruneClockLane(lane, kept); err != nil {
				return err
			}
			continue
		}
		current := kept.Key == launch.ActionID
		if current && kept.Ended != "" {
			if err := d.pruneClockLane(lane, kept); err != nil {
				return err
			}
			continue
		}
		if incidents == nil {
			if incidents, err = d.clockIncidents(ctx); err != nil {
				return err
			}
		}
		clock := &incidentClockState{Key: launch.ActionID, Reached: map[string]string{}}
		if current {
			clock.Reached = kept.Reached
		}
		tended, err := d.tendClock(ctx, lane, *launch, clock, incidents)
		if err != nil {
			return err
		}
		if tended && (clock.Key != kept.Key || clock.Ended != kept.Ended || !maps.Equal(clock.Reached, kept.Reached)) {
			if err := d.setClock(lane, *clock); err != nil {
				return err
			}
		}
	}
	return nil
}

func (d *Desk) clockOf(lane string) (incidentClockState, error) {
	var clock incidentClockState
	value, found, err := d.store.FactEntry(deskRunner, clockFacts, lane)
	if err != nil || !found {
		return clock, err
	}
	raw, err := json.Marshal(value)
	if err != nil {
		return clock, err
	}
	return clock, json.Unmarshal(raw, &clock)
}

func (d *Desk) clockLanes() ([]string, error) {
	return d.store.FactEntryKeys(deskRunner, clockWork)
}

func (d *Desk) watchClockLane(lane string) error {
	return d.store.Edit(deskRunner, func(tx *incident.Tx) error {
		return tx.SetFactEntry(clockWork, lane, true)
	})
}

func (d *Desk) pendingClockLane(lane string) (bool, error) {
	launches, err := d.laneActions(laneBook(lane), kindLaunch, incident.ActionAccepted, incident.ActionStarted)
	return len(launches) > 0, err
}

func (d *Desk) pruneClockLane(lane string, clock incidentClockState) error {
	if clock.Key != "" && clock.Ended == "" {
		return nil
	}
	pending, err := d.pendingClockLane(lane)
	if err != nil || pending {
		return err
	}
	return d.store.Edit(deskRunner, func(tx *incident.Tx) error {
		return tx.DropFactEntry(clockWork, lane)
	})
}

func (d *Desk) setClock(lane string, clock incidentClockState) error {
	pending := false
	if clock.Ended != "" {
		var err error
		pending, err = d.pendingClockLane(lane)
		if err != nil {
			return err
		}
	}
	return d.store.Edit(deskRunner, func(tx *incident.Tx) error {
		if err := tx.SetFactEntry(clockFacts, lane, clock); err != nil {
			return err
		}
		if clock.Ended == "" || pending {
			return tx.SetFactEntry(clockWork, lane, true)
		}
		return tx.DropFactEntry(clockWork, lane)
	})
}

func (d *Desk) clockIncidents(ctx context.Context) ([]client.Record, error) {
	since := d.Now().Add(-clockLookback)
	opened, err := d.tail(ctx, client.Query{Kinds: []string{clockIncident}, Since: since})
	if err != nil {
		return nil, err
	}
	decides, err := d.tail(ctx, client.Query{Kinds: []string{clockDecide}, Lanes: []string{coordinatorLane}, Since: since})
	if err != nil {
		return nil, err
	}
	return append(slices.DeleteFunc(decides, func(record client.Record) bool { return !clockCloses(record) }), opened...), nil
}

func clockCloses(record client.Record) bool {
	return record.Lane == coordinatorLane && clockClosed.MatchString(record.Text)
}

func clockKey(launch incident.Action) string {
	return clockFact + ":" + string(launch.DispatchID)
}

func (d *Desk) tendClock(ctx context.Context, lane string, launch incident.Action, clock *incidentClockState, incidents []client.Record) (bool, error) {
	launched, err := verifiedAt(launch)
	if err != nil {
		return false, err
	}
	clock.Launched = store.Stamp(launched)
	names, err := d.names(lane)
	if err != nil {
		return false, err
	}
	for _, record := range incidents {
		if !clockCloses(record) || !namesLane(record, names) || record.At.Before(launched) {
			continue
		}
		closed := fmt.Sprintf("#%d", record.Seq)
		clock.Ended = "incident closed by " + record.Name()
		held, err := d.known(laneBook(lane+clockBackup), clockKey(launch))
		if err != nil {
			return false, err
		}
		if held != nil {
			if err := d.withdraw(ctx, "close"+closed, record.Name(), clockKey(launch)); err != nil {
				return false, err
			}
		}
		return true, nil
	}
	withdrawn, err := d.withdrawnBy(lane)
	if err != nil {
		return false, err
	}
	if withdrawn != "" {
		clock.Ended = "withdrawn by " + withdrawn
		return true, nil
	}
	dispatch, err := d.show(ctx, lane, "")
	if err != nil || dispatch == nil {
		return false, err
	}
	if dispatch.ID != string(launch.DispatchID) {
		clock.Ended = "relaunched as dispatch " + dispatch.ID
		return true, nil
	}
	return true, d.holdClock(ctx, lane, launch, launched, names, clock, dispatch, incidents)
}

func (d *Desk) holdClock(ctx context.Context, lane string, launch incident.Action, launched time.Time, names []string, clock *incidentClockState, dispatch *laneDispatch, incidents []client.Record) error {
	stoodDown, err := d.laneStoodDown(ctx, lane)
	if err != nil {
		return err
	}
	holds, err := d.passTail(ctx, client.Query{Lanes: []string{coordinatorLane}, Kinds: []string{kindHold}})
	if err != nil {
		return err
	}
	stops, err := d.laneRecordsSince(ctx, lane, launched, kindStopped, clockIncident)
	if err != nil {
		return err
	}
	if clock.Reached, err = d.clockReached(ctx, lane, launched); err != nil {
		return err
	}
	held := slices.IndexFunc(holds, func(record client.Record) bool { return namesLane(record, names) && !record.At.Before(launched) })
	stop := slices.IndexFunc(stops, func(record client.Record) bool {
		kindStops := string(record.Kind) == kindStopped || clockStopped.MatchString(record.Text)
		return !record.At.Before(launched) && kindStops && clockScopedOut.MatchString(record.Text)
	})
	gate := ""
	if stop >= 0 {
		if gate, err = d.refusedGate(ctx, lane, launch, launched, stops[stop]); err != nil {
			return err
		}
	}
	switch {
	case stoodDown != "":
		clock.Ended = "withdrawn by " + stoodDown
	case held >= 0:
		clock.Ended = fmt.Sprintf("held by %s", holds[held].Name())
	case gate != "":
		clock.Ended, err = d.refuseStop(ctx, lane, launch, launched, stops[stop], gate, incidents)
		return err
	case !dispatch.active():
		clock.Ended = "dispatch " + dispatch.Status
	default:
		for _, name := range clockEnds {
			if _, hit := clock.Reached[name]; hit {
				clock.Ended = name
				break
			}
		}
	}
	if clock.Ended != "" {
		return nil
	}
	return d.overrun(ctx, lane, launch, launched, clock.Reached, incidents)
}

func (d *Desk) clockReached(ctx context.Context, lane string, launched time.Time) (map[string]string, error) {
	var marks []clockStamp
	own, err := d.laneRecordsSince(ctx, lane, launched)
	if err != nil {
		return nil, err
	}
	for _, record := range own {
		if mark := clockMark(record); mark != "" {
			marks = append(marks, clockStamp{mark, record.At})
		}
	}
	statuses, err := d.clockStatuses(laneBook(lane))
	if err != nil {
		return nil, err
	}
	marks = append(marks, statuses...)
	siblings, err := d.siblingMarks(ctx, lane)
	if err != nil {
		return nil, err
	}
	marks = append(marks, siblings...)
	slices.SortStableFunc(marks, func(a, b clockStamp) int { return a.at.Compare(b.at) })
	reached := map[string]string{}
	for _, mark := range marks {
		if _, hit := reached[mark.name]; !hit && !mark.at.Before(launched) {
			reached[mark.name] = store.Stamp(mark.at)
		}
	}
	return reached, nil
}

func (d *Desk) clockStatuses(container string) ([]clockStamp, error) {
	value, found, err := d.factOf(container, milestoneFact)
	if err != nil || !found {
		return nil, err
	}
	statuses, _ := value.(map[string]any)
	marks := make([]clockStamp, 0, len(statuses))
	for _, status := range statuses {
		fields, _ := status.(map[string]any)
		name, _ := fields["name"].(string)
		stamp, _ := fields["at"].(string)
		at, err := store.Parse(stamp)
		if err != nil {
			return nil, err
		}
		marks = append(marks, clockStamp{name, at})
	}
	return marks, nil
}

func (d *Desk) siblingMarks(ctx context.Context, lane string) ([]clockStamp, error) {
	siblings, err := d.siblings(lane)
	if err != nil || len(siblings) == 0 {
		return nil, err
	}
	var names []string
	var marks []clockStamp
	for _, sibling := range siblings {
		own, err := d.names(sibling)
		if err != nil {
			return nil, err
		}
		names = append(names, own...)
		statuses, err := d.clockStatuses(laneBook(sibling))
		if err != nil {
			return nil, err
		}
		for _, status := range statuses {
			marks = append(marks, clockStamp{siblingMark(status.name), status.at})
		}
	}
	posted, err := d.tail(ctx, client.Query{Lanes: names})
	if err != nil {
		return nil, err
	}
	for _, record := range posted {
		if mark := clockMark(record); slices.Contains(clockMilestones, mark) || mark == clockFixLive {
			marks = append(marks, clockStamp{siblingMark(mark), record.At})
		}
	}
	return marks, nil
}

func siblingMark(mark string) string {
	if mark == clockFixLive {
		return mark
	}
	return "mechanism"
}

func (d *Desk) overrun(ctx context.Context, lane string, launch incident.Action, launched time.Time, reached map[string]string, incidents []client.Record) error {
	elapsed := int(d.Now().Sub(launched).Minutes())
	limits := map[string]int{"mechanism": d.Config.IncidentMechanismMinutes, "opened": d.Config.IncidentPRMinutes, clockReady: d.Config.IncidentReadyMinutes}
	start := 0
	for i, name := range clockMilestones {
		if _, hit := reached[name]; hit {
			start = i + 1
		}
	}
	missing := clockMilestones[start:]
	at := launched.In(pacificTime).Format("15:04")
	for _, name := range missing {
		log := "escalation:" + clockKey(launch) + ":" + name
		held, err := d.known(deskRunner, log)
		if err != nil {
			return err
		}
		if elapsed < limits[name] || held != nil {
			continue
		}
		phases, err := clockPhases(launched, reached, d.Now())
		if err != nil {
			return err
		}
		label := clockLabels[name]
		owner, err := d.clockFallback(ctx, lane, launch, label, elapsed, incidents)
		if err != nil {
			return err
		}
		text := fmt.Sprintf("no %s %d min after its launch at %s, limit %d min; %s | %s", label, elapsed, at, limits[name], phases, owner)
		if name == clockReady {
			gaps := make([]string, 0, len(missing))
			for _, gap := range missing {
				gaps = append(gaps, clockLabels[gap])
			}
			text = fmt.Sprintf("not ready %d min after its launch at %s, limit %d min; missing %s; %s | %s", elapsed, at, limits[name], strings.Join(gaps, ", "), phases, owner)
		}
		if err := d.record(log, fmt.Sprintf("INCIDENT-CLOCK %s %s: %s", lane, strings.ToLower(label), text)); err != nil {
			return err
		}
	}
	return nil
}

func clockPhases(launched time.Time, reached map[string]string, now time.Time) (string, error) {
	points := []clockStamp{{"launch", launched}}
	for _, name := range clockMilestones {
		stamp, hit := reached[name]
		if !hit {
			continue
		}
		at, err := store.Parse(stamp)
		if err != nil {
			return "", err
		}
		points = append(points, clockStamp{clockLabels[name], at})
	}
	slices.SortStableFunc(points, func(a, b clockStamp) int { return a.at.Compare(b.at) })
	points = append(points, clockStamp{"now", now})
	phases := make([]string, 0, len(points)-1)
	for i := range len(points) - 1 {
		phases = append(phases, fmt.Sprintf("%s to %s %d min", points[i].name, points[i+1].name, int(points[i+1].at.Sub(points[i].at).Minutes())))
	}
	return strings.Join(phases, ", "), nil
}

func (d *Desk) clockFallback(ctx context.Context, lane string, launch incident.Action, missing string, minutes int, incidents []client.Record) (string, error) {
	backup := lane + clockBackup
	key := clockKey(launch)
	if strings.HasSuffix(lane, clockBackup) {
		return lane + " is itself an Opus fallback, so none is launched for it", nil
	}
	if open := openIncidentOf(lane, incidents); open != nil {
		return fmt.Sprintf("no fallback launched for %s: open incident %s names %s, so the drive's clocks warn and stand aside (owner, 2:21 PM Nov 1); the coordinator launches one by hand if the fix needs it", backup, open.Name(), lane), nil
	}
	held, err := d.known(laneBook(backup), key)
	if err != nil {
		return "", err
	}
	if held != nil {
		return "the Opus fallback " + backup + " is already launched", nil
	}
	brief, err := d.briefFor(ctx, lane)
	if err != nil {
		return "", err
	}
	if brief == "" {
		return fmt.Sprintf("no fallback launched for %s: %s has no brief file", backup, lane), nil
	}
	refusal, err := d.launchRefusal(ctx, key, Order{Lane: backup, Model: modelIncidentOpus, Effort: clockEffort, Brief: brief, Role: agentIncident})
	if err != nil {
		return "", err
	}
	if refusal != "" {
		return fmt.Sprintf("no fallback launched for %s: %s", backup, refusal), nil
	}
	text, err := d.sliced(ctx, key, backup, fmt.Sprintf(fallbackBrief, backup, lane, minutes, missing, d.Config.Drive, readBrief(brief)))
	if err != nil {
		return "", err
	}
	entry := fmt.Sprintf("INCIDENT-CLOCK %s: Opus fallback brief for %s, no %s at +%d min", lane, backup, missing, minutes)
	attached, failure, err := d.attachBrief(ctx, backup, entry, func(string) (string, error) { return text, nil })
	if err != nil {
		return "", err
	}
	if attached == "" {
		return fmt.Sprintf("no fallback launched for %s: %s", backup, failure), nil
	}
	if _, _, err := d.acceptLaunch(key, backup, launchTarget{Model: modelIncidentOpus, Effort: clockEffort, Brief: attached, By: deskRunner}); err != nil {
		return "", err
	}
	return fmt.Sprintf("the Opus fallback %s is launching on %s xhigh, brief %s", backup, modelIncidentOpus, attached), nil
}

func openIncidentOf(lane string, incidents []client.Record) *client.Record {
	ended := map[string]int64{}
	for _, record := range incidents {
		if clockCloses(record) {
			for _, closed := range record.To {
				ended[client.Stem(closed)] = max(ended[client.Stem(closed)], record.Seq)
			}
		}
	}
	var newest *client.Record
	for i, record := range incidents {
		if string(record.Kind) != clockIncident || record.Status != clockOpen || clockCloses(record) || (record.Lane != lane && !lanes.Addresses(record.To, lane)) {
			continue
		}
		named := slices.DeleteFunc(append([]string{record.Lane}, record.To...), func(name string) bool { return name == coordinatorLane })
		superseded := len(named) > 0 && !slices.ContainsFunc(named, func(name string) bool { return ended[client.Stem(name)] <= record.Seq })
		if !superseded && (newest == nil || record.Seq > newest.Seq) {
			newest = &incidents[i]
		}
	}
	return newest
}

func (d *Desk) briefFor(ctx context.Context, lane string) (string, error) {
	launches, err := d.laneActions(laneBook(lane), kindLaunch, incident.ActionVerified)
	if err != nil {
		return "", err
	}
	if len(launches) > 0 {
		spec, err := launchOf(launches[len(launches)-1])
		if err != nil {
			return "", err
		}
		if info, err := os.Stat(spec.Brief); err == nil && !info.IsDir() {
			return spec.Brief, nil
		}
	}
	for _, name := range []string{lane + ".full.md", lane + ".md"} {
		said, code, err := d.readBriefs(ctx, "attachment", "path", d.Config.BriefsLog, name)
		if err != nil {
			return "", err
		}
		path := strings.TrimSpace(said)
		if code != 0 {
			continue
		}
		if info, err := os.Stat(path); err == nil && !info.IsDir() {
			return path, nil
		}
	}
	return "", nil
}

func (d *Desk) refusedGate(ctx context.Context, lane string, launch incident.Action, launched time.Time, stop client.Record) (string, error) {
	spec, err := launchOf(launch)
	if err != nil {
		return "", err
	}
	keys := incidentKeys(lane, spec)
	gate := slices.IndexFunc(keys, func(key string) bool {
		return slices.ContainsFunc(clockGates, func(kind string) bool { return strings.HasPrefix(key, kind+"-") })
	})
	if gate < 0 {
		return "", nil
	}
	first := firstLane(lane)
	opened, err := d.fixLaunch(laneBook(first))
	if err != nil {
		return "", err
	}
	since := launched
	if opened != nil {
		if since, err = verifiedAt(*opened); err != nil {
			return "", err
		}
	}
	own, err := d.names(lane)
	if err != nil {
		return "", err
	}
	parent, err := d.names(first)
	if err != nil {
		return "", err
	}
	rulings, err := d.tail(ctx, client.Query{Lanes: []string{coordinatorLane}, Since: since})
	if err != nil {
		return "", err
	}
	audience := slices.Concat(own, parent)
	if slices.ContainsFunc(rulings, func(ruling client.Record) bool {
		return (ruling.Seq == stop.Re || namesLane(ruling, audience)) && clockRuledOut.MatchString(ruling.Text)
	}) {
		return "", nil
	}
	return keys[gate], nil
}

func (d *Desk) refuseStop(ctx context.Context, lane string, launch incident.Action, launched time.Time, stop client.Record, gate string, incidents []client.Record) (string, error) {
	owner, err := d.clockFallback(ctx, lane, launch, "fix", int(d.Now().Sub(launched).Minutes()), incidents)
	if err != nil {
		return "", err
	}
	text := fmt.Sprintf("%s scoped the incident out, but %s is a release gate and the earliest canary: active yes always, and no customer impact is its scope, never a stop (owner, Nov 1 3:04 PM); no root record rules it out with active no | %s; coordinator: keep the incident open and name its fix owner", stop.Name(), gate, owner)
	if err := d.escalateTo(ctx, "refused-stop:"+strconv.FormatInt(stop.Seq, 10), "REFUSED-STOP", lane, text, coordinatorLane); err != nil {
		return "", err
	}
	return fmt.Sprintf("stop %s refused: %s is a release gate", stop.Name(), gate), nil
}

func (d *Desk) inProcess(ctx context.Context) error {
	watching, err := d.liveClocks()
	if err != nil {
		return err
	}
	for _, lane := range watching {
		launch, err := d.fixLaunch(laneBook(lane))
		if err != nil {
			return err
		}
		if launch == nil {
			continue
		}
		clock, err := d.clockOf(lane)
		if err != nil {
			return err
		}
		if clock.Key != launch.ActionID || clock.Ended != "" {
			continue
		}
		if err := d.retryFallback(ctx, lane, *launch); err != nil {
			return err
		}
	}
	return nil
}

func (d *Desk) retryFallback(ctx context.Context, lane string, launch incident.Action) error {
	backup := lane + clockBackup
	key := clockKey(launch)
	launches, err := d.laneActions(laneBook(backup), kindLaunch)
	if err != nil {
		return err
	}
	tries := slices.DeleteFunc(launches, func(action incident.Action) bool {
		return action.ActionID != key && action.ActionID != key+clockRetry
	})
	failed := func(action incident.Action) bool { return action.Status == incident.ActionFailed }
	if len(tries) == 0 || slices.ContainsFunc(tries, func(action incident.Action) bool { return !failed(action) }) {
		return nil
	}
	spec, err := launchOf(tries[0])
	if err != nil {
		return err
	}
	if len(tries) == 1 {
		refusal, err := d.launchRefusal(ctx, key+clockRetry, Order{Lane: backup, Model: modelIncidentOpus, Effort: clockEffort, Brief: spec.Brief, Role: agentIncident})
		if err != nil || refusal != "" {
			return err
		}
		_, _, err = d.acceptLaunch(key+clockRetry, backup, launchTarget{Model: modelIncidentOpus, Effort: clockEffort, Brief: spec.Brief, By: deskRunner})
		return err
	}
	reasons := make([]string, 0, len(tries))
	for _, try := range tries {
		reasons = append(reasons, string(try.Reason))
	}
	return d.record("escalation:"+key+":in-process", fmt.Sprintf("INCIDENT-CLOCK %s in-process: the Orca launch of the Opus fallback %s failed twice: %s | coordinator: start %s in-process now, an Agent of type long-running:lane-ship named %s on opus in fast mode with the brief %s", lane, backup, strings.Join(reasons, " | "), backup, backup, spec.Brief))
}

func namesLane(record client.Record, names []string) bool {
	return addressed(record, names) || slices.ContainsFunc(names, func(name string) bool { return mentionsLane(record.Text, name) })
}

func mentionsLane(text, name string) bool {
	return regexp.MustCompile(`(?:^|[^\w.-])` + regexp.QuoteMeta(name) + `(?:$|[^\w.-]|\.(?:\W|$))`).MatchString(text)
}
