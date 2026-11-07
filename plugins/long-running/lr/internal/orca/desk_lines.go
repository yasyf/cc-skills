package orca

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/alert"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	relayGrammar    = "orca-desk: relay to <lane>[, <lane>…][ and <lane>]: <text>"
	launchGrammar   = "orca-desk: launch <lane> [NOW] <model> <effort> [role=<role>] brief=<absolute path>"
	alertGrammar    = "orca-desk: alert <slug> <link> :: <what fired>"
	incidentGrammar = "orca-desk: incident <slug> <link> :: <what fired>"
	holdGrammar     = "orca-desk: hold <slug> owner=<lane> :: <what is held, and on what>"
	unholdGrammar   = "orca-desk: unhold <slug>"
	withdrawGrammar = "orca-desk: withdraw <R<n> | unfixed:<msg>:<n> | incident-clock:<dispatch> | lane>"
	withdrawnFact   = "withdrawn"
	withdrawPending = "withdraw-pending"
	briefLineShown  = 160
	stopConfirm     = 30 * time.Second
	stopPoll        = 2 * time.Second
	verbLaunch      = "launch"
	verbRelay       = "relay"
	verbIncident    = "incident"
	verbWithdraw    = "withdraw"
	launchesMac     = "mac"
	launchesSprite  = "sprite"
	spawnedFix      = "-fix"
	spawnedEvidence = "-evidence"
	lookbackFirst   = 24 * time.Hour
	statusNames     = `(relay|launch|alert|incident|hold|unhold|withdraw)`
)

var (
	inboxDirective = regexp.MustCompile(`(?s)^(?:-\s+)?(?:(?:1[0-2]|0?[1-9]):[0-5]\d\s?[AP]M\s+)?(?:(R\d+)\s+(?:\([^)]*\)\s+)?)?orca-desk: ` + statusNames + `\b(.*)$`)
	relayTo        = regexp.MustCompile(`(?s)^ to ([\w.-]+(?:(?:, (?:and )?| and )[\w.-]+)*):\s+(\S.*)$`)
	laneList       = regexp.MustCompile(`, (?:and )?| and `)
	launchSpec     = regexp.MustCompile(`^ ([\w.-]+)( NOW)? (\S+) (\S+)(?: role=([\w-]+))? brief=(/\S+)$`)
	alertSpec      = regexp.MustCompile(`(?s)^ ([a-z0-9][a-z0-9.-]*) (\S+) :: (\S.*)$`)
	incidentSpec   = regexp.MustCompile(`(?s)^ ([a-z0-9][a-z0-9_.-]*) (\S+)(?: reported=(?:(?:[01]?\d|2[0-3]):[0-5]\d|(?:1[0-2]|0?[1-9]):[0-5]\d [AP]M))? :: (\S.*)$`)
	holdSpec       = regexp.MustCompile(`(?s)^ ([a-z0-9][a-z0-9.-]*) owner=([\w.-]+) :: (\S.*)$`)
	unholdSpec     = regexp.MustCompile(`^ ([a-z0-9][a-z0-9.-]*)$`)
	withdrawSpec   = regexp.MustCompile(`^ ([\w.:@-]+)$`)
	launchKey      = regexp.MustCompile(`^(?:R\d+(?:@\w+)?|unfixed:[\w-]+:\d+|inbox@\S+|incident-clock:ctx_[\w-]+)$`)
	decideLine     = regexp.MustCompile(`^DECIDE ((?:msg|relay)_\S+) ([\w.-]+): `)
	monitorID      = regexp.MustCompile(`(?:monitors/|\bmonitor |\bDatadog )(\d{4,})`)
)

type directive struct {
	Key, Verb, Rest string
}

func parseDirective(line string) *directive {
	found := inboxDirective.FindStringSubmatch(line)
	if found == nil {
		return nil
	}
	return &directive{Key: found[1], Verb: found[2], Rest: found[3]}
}

func expires(verb string) bool {
	return verb == verbLaunch || verb == verbIncident
}

func lineKey(parsed *directive, record client.Record) string {
	if expires(parsed.Verb) {
		return "R" + strings.TrimPrefix(record.Name(), "#")
	}
	if parsed.Key != "" {
		return parsed.Key
	}
	return fmt.Sprintf("inbox@cci#%d", record.Seq)
}

func base(address string) string {
	name, _, _ := strings.Cut(address, "@")
	return name
}

func toDesk(record client.Record) bool {
	return slices.ContainsFunc(record.To, func(to string) bool { return base(to) == deskLane })
}

func resolvedSeqs(record client.Record) []int64 {
	seqs := slices.Clone(record.AlsoResolves)
	if record.Resolves != 0 {
		seqs = append(seqs, record.Resolves)
	}
	slices.Sort(seqs)
	return seqs
}

type tailFailure struct{ cause error }

func (t *tailFailure) Error() string { return t.cause.Error() }

func (d *Desk) tail(ctx context.Context, query client.Query) ([]client.Record, error) {
	query.Drive = d.Config.Drive
	records, err := d.Bus.Tail(ctx, query)
	if err != nil {
		return nil, &tailFailure{cause: err}
	}
	return records, nil
}

func (d *Desk) noteTailFailure(err error) error {
	var failure *tailFailure
	if !errors.As(err, &failure) {
		return err
	}
	return d.escalate("cci-tail:"+store.Stamp(d.Now())[:15], "CCI-TAIL-FAILED", "runner", fmt.Sprintf("cci tail: %.300s", failure.Error()))
}

func (d *Desk) cursorFact() string {
	return "cci:" + d.Config.Drive + ":" + deskLane
}

func factInt(value any) int64 {
	number, _ := value.(float64)
	return int64(number)
}

func (d *Desk) readLines(ctx context.Context) error {
	d.reads.forgetQuestions()
	fact := d.cursorFact()
	value, found, err := d.factOf(deskRunner, fact)
	if err != nil {
		return err
	}
	if !found {
		records, err := d.tail(ctx, client.Query{Since: d.Now().Add(-lookbackFirst)})
		if err != nil {
			return d.noteTailFailure(err)
		}
		var newest int64
		if len(records) > 0 {
			newest = records[len(records)-1].Seq
		}
		return d.setFact(deskRunner, fact, newest)
	}
	records, err := d.tail(ctx, client.Query{After: factInt(value)})
	if err != nil {
		return d.noteTailFailure(err)
	}
	d.withdrawals = withdrawalsIn(records)
	if d.rulings, err = d.rulingsIn(ctx, records); err != nil {
		return err
	}
	var skipped int64
	for _, record := range records {
		for _, ruled := range d.rulings {
			if ruled.seq != record.Seq {
				continue
			}
			for _, slug := range ruled.slugs {
				if err := d.withdrawIncident(ctx, ruled, slug); err != nil {
					return err
				}
			}
		}
		if !toDesk(record) && !rootReplies(record) {
			skipped = record.Seq
			continue
		}
		if err := d.readLine(ctx, record); err != nil {
			return err
		}
		if err := d.setFact(deskRunner, fact, record.Seq); err != nil {
			return err
		}
		skipped = 0
	}
	if skipped != 0 {
		return d.setFact(deskRunner, fact, skipped)
	}
	return nil
}

func withdrawalsIn(records []client.Record) map[int64]string {
	read := map[int64]bool{}
	for _, record := range records {
		read[record.Seq] = toDesk(record) && parseDirective(strings.TrimSpace(record.Text)) != nil
	}
	withdrawn := map[int64]string{}
	for _, record := range records {
		for _, seq := range closedLines(record) {
			if read[seq] {
				withdrawn[seq] = record.Name()
			}
		}
	}
	return withdrawn
}

func closedLines(record client.Record) []int64 {
	root := record.Lane == coordinatorLane
	if !root && !toDesk(record) {
		return nil
	}
	seqs := resolvedSeqs(record)
	if root && string(record.Kind) == verbWithdraw && record.Re != 0 {
		seqs = append(seqs, record.Re)
	}
	return seqs
}

func (d *Desk) lineEnded(ctx context.Context, lane string, line client.Record) (string, error) {
	if by := d.withdrawals[line.Seq]; by != "" {
		return fmt.Sprintf("cci %s closed it", by), nil
	}
	if ruled := d.ruledAfter(lane, line.Seq); ruled != "" {
		return ruled, nil
	}
	return d.endedSince(ctx, lane, line.At)
}

func (d *Desk) endedSince(ctx context.Context, lane string, posted time.Time) (string, error) {
	row, found, err := d.Registry.Lane(lane)
	if err != nil {
		return "", err
	}
	if found && row.State == store.LaneRetired {
		return "its lane is retired", nil
	}
	names, err := d.names(lane)
	if err != nil {
		return "", err
	}
	posts, err := d.readStandDowns(ctx)
	if err != nil {
		return "", err
	}
	for _, record := range posts {
		if record.At.After(posted) && standsDown(record, names) {
			return fmt.Sprintf("it was stood down on cci %s", record.Name()), nil
		}
	}
	return "", nil
}

func rootReplies(record client.Record) bool {
	return record.Lane == coordinatorLane && (record.Re != 0 || len(resolvedSeqs(record)) > 0)
}

func (d *Desk) readLine(ctx context.Context, record client.Record) error {
	desk := toDesk(record)
	root := record.Lane == coordinatorLane
	isLine := parseDirective(strings.TrimSpace(record.Text)) != nil
	resolved, err := d.resolvedEarlier(ctx, record)
	if err != nil {
		return err
	}
	withdrew, err := d.closeGos(ctx, record, resolved)
	if err != nil {
		return err
	}
	replyTo := record.Re
	if replyTo == 0 && root {
		replyTo = resolvedDecide(resolved)
	}
	if replyTo == 0 || (desk && isLine) {
		if desk && (isLine || !withdrew) {
			return d.cciLine(ctx, record)
		}
		return nil
	}
	answered := false
	if root {
		closed, err := d.cciRecord(ctx, replyTo)
		if err != nil {
			return err
		}
		if answered, err = d.rootReply(ctx, record, closed); err != nil {
			return err
		}
	}
	if desk && !answered && !withdrew {
		return d.refuseLine(record)
	}
	return nil
}

func (d *Desk) resolvedEarlier(ctx context.Context, record client.Record) ([]client.Record, error) {
	var earlier []int64
	for _, seq := range resolvedSeqs(record) {
		if d.withdrawals[seq] == "" {
			earlier = append(earlier, seq)
		}
	}
	if len(earlier) == 0 {
		return nil, nil
	}
	records, err := d.Bus.Among(ctx, d.Config.Drive, earlier)
	if err != nil {
		return nil, d.noteTailFailure(&tailFailure{cause: err})
	}
	return records, nil
}

func (d *Desk) closeGos(ctx context.Context, record client.Record, resolved []client.Record) (bool, error) {
	closed := slices.ContainsFunc(resolvedSeqs(record), func(seq int64) bool { return d.withdrawals[seq] != "" })
	log := fmt.Sprintf("escalation:withdraw:cci#%d", record.Seq)
	by := "cci " + record.Name()
	for _, goLine := range resolved {
		parsed := parseDirective(strings.TrimSpace(goLine.Text))
		if !toDesk(goLine) || parsed == nil || parsed.Verb != verbLaunch {
			continue
		}
		spec := launchSpec.FindStringSubmatch(parsed.Rest)
		if spec == nil {
			continue
		}
		key, lane := lineKey(parsed, goLine), spec[1]
		action, err := d.known(laneBook(lane), key)
		if err != nil {
			return false, err
		}
		if action == nil || action.Kind != kindLaunch {
			continue
		}
		closed = true
		switch action.Status {
		case incident.ActionFailed:
		case incident.ActionAccepted, incident.ActionStarted:
			if err := d.withdrawLane(ctx, log, by, key, true, heldLaunches{lane: lane, launches: []incident.Action{*action}}); err != nil {
				return false, err
			}
		default:
			if err := d.recordTo(ctx, log+":"+lane, fmt.Sprintf("WITHDRAW-FAILED %s %s: %s closed its go once it was %s, which stops nothing; withdraw the lane with `orca-desk: withdraw %s`", key, lane, by, action.Status, lane), verdictTo(record.Lane)); err != nil {
				return false, err
			}
		}
	}
	return closed, nil
}

func resolvedDecide(resolved []client.Record) int64 {
	for _, closed := range resolved {
		if closed.Lane == deskRunner && decideLine.MatchString(closed.Text) {
			return closed.Seq
		}
	}
	return 0
}

func (d *Desk) cciRecord(ctx context.Context, seq int64) (*client.Record, error) {
	records, err := d.Bus.Among(ctx, d.Config.Drive, []int64{seq})
	if err != nil {
		return nil, d.noteTailFailure(&tailFailure{cause: err})
	}
	if len(records) == 0 {
		return nil, nil
	}
	return &records[0], nil
}

func (d *Desk) rootReply(ctx context.Context, record client.Record, closed *client.Record) (bool, error) {
	at := fmt.Sprintf("cci#%d", record.Seq)
	if closed == nil {
		return false, nil
	}
	if string(record.Kind) == verbWithdraw {
		parsed := parseDirective(strings.TrimSpace(closed.Text))
		if !slices.Contains(closed.To, deskLane) || parsed == nil || parsed.Verb != verbLaunch {
			return false, nil
		}
		return true, d.withdraw(ctx, at, "cci "+record.Name(), lineKey(parsed, *closed))
	}
	decide := decideLine.FindStringSubmatch(closed.Text)
	if closed.Lane != deskRunner || decide == nil {
		return false, nil
	}
	ended, err := d.laneFinished(ctx, decide[2])
	if err != nil {
		return true, err
	}
	if ended != "" {
		return true, d.record("escalation:ended:"+at, fmt.Sprintf("ENDED cci %s %s: %s, so root's answer to %s is not relayed to it", record.Name(), decide[2], ended, closed.Name()))
	}
	if strings.HasPrefix(closed.Text[len(decide[0]):], workerStopped) && len(record.To) > 0 && !lanes.Addresses(record.To, decide[2]) {
		return true, d.record("escalation:kept:"+at, fmt.Sprintf("KEPT cci %s %s: root's reply to its stop alarm %s is addressed to %s, not to the lane, so it is not relayed", record.Name(), decide[2], closed.Name(), strings.Join(record.To, ", ")))
	}
	if parked, err := d.parkedReply(ctx, at, decide[2], record, *closed); err != nil || parked {
		return true, err
	}
	text, err := d.wholeText(at, at, decide[2], record)
	if err != nil || text == "" {
		return true, err
	}
	question, err := d.unclaimed(at, decide[2], []string{decide[1]})
	if err != nil {
		return true, err
	}
	return true, d.relayToLane(ctx, at, at, decide[2], relayTarget{Text: text, Record: recordLabel(record)}, &question)
}

func recordLabel(record client.Record) string {
	return fmt.Sprintf("%s %s from %s", record.Name(), record.Kind, record.Lane)
}

func clippedByCCI(kept, written string) bool {
	stem, ellipsis := strings.CutSuffix(kept, "…")
	if !ellipsis {
		return false
	}
	return len([]rune(kept)) == textLimit || strings.HasPrefix(collapsed(written), strings.TrimRight(stem, " \t\n"))
}

func keptByCCI(text string) string {
	for line := range strings.SplitSeq(text, "\n") {
		if trimmed := strings.TrimSpace(line); trimmed != "" {
			return clipped(trimmed, textLimit)
		}
	}
	return ""
}

func (d *Desk) wholeText(at, key, about string, record client.Record) (string, error) {
	kept := strings.TrimSpace(record.Text)
	if record.Refs.Path == "" {
		return kept, nil
	}
	raw, err := os.ReadFile(record.Refs.Path)
	if err != nil {
		return "", d.record("escalation:relay:"+at, fmt.Sprintf("RELAY-FAILED %s %s: the record's full text at %s is not readable here; nothing was relayed; post it again on the machine this runner runs on", key, about, record.Refs.Path))
	}
	written := strings.TrimSpace(strings.ToValidUTF8(string(raw), "�"))
	if clippedByCCI(kept, written) || keptByCCI(written) == kept {
		return written, nil
	}
	return kept, nil
}

func (d *Desk) cciLine(ctx context.Context, record client.Record) error {
	at := fmt.Sprintf("cci#%d", record.Seq)
	line := strings.TrimSpace(record.Text)
	parsed := parseDirective(line)
	if parsed == nil {
		return d.refuseLine(record)
	}
	key := lineKey(parsed, record)
	carried := record.Refs.Path
	if parsed.Verb == verbRelay {
		carried = ""
		text, err := d.wholeText(at, key, "inbox", record)
		if err != nil || text == "" {
			return err
		}
		if parsed = parseDirective(text); parsed == nil {
			return d.refuseLine(record)
		}
	}
	if parsed.Verb == "alert" || parsed.Verb == verbIncident {
		if refusal, _, refused := config.RefusedLine(d.Config.RefusedBriefs, parsed.Rest); refused {
			slog.Debug("alert on a refused topic dropped", "line", key, "why", refusal.Why)
			return nil
		}
	}
	if taken, err := d.takes(ctx, parsed, key); !taken || err != nil {
		return err
	}
	if closer := d.withdrawals[record.Seq]; closer != "" && parsed.Verb == verbLaunch {
		lane := "inbox"
		if spec := launchSpec.FindStringSubmatch(parsed.Rest); spec != nil {
			lane = spec[1]
		}
		return d.recordTo(ctx, "escalation:launch:"+at, fmt.Sprintf("WITHDRAWN %s %s: cci %s closed its go in the read that brought it; nothing was launched", key, lane, closer), verdictTo(record.Lane))
	}
	switch parsed.Verb {
	case verbLaunch:
		return d.launchLine(ctx, at, key, parsed.Rest, carried, record.Lane, record.At)
	case verbRelay:
		return d.relayLine(ctx, at, key, parsed.Rest, record.Lane)
	case "alert":
		return d.alertLine(ctx, at, key, parsed.Rest)
	case verbIncident:
		return d.incidentLine(ctx, at, key, parsed.Rest, record)
	case kindHold:
		return d.holdLine(at, key, parsed.Rest)
	case "unhold":
		return d.unholdLine(at, key, parsed.Rest)
	}
	return d.withdrawLine(ctx, at, key, parsed.Rest)
}

func (d *Desk) warnUnheard(ctx context.Context, at, key, lane, sender string) error {
	dispatch, err := d.listed(ctx, lane)
	if err != nil || dispatch.active() {
		return err
	}
	rows, err := d.Registry.AllLanes()
	if err != nil || slices.ContainsFunc(rows, func(row store.Lane) bool {
		return row.State != store.LaneRetired && lanes.Stem(row.Name) == lanes.Stem(lane)
	}) {
		return err
	}
	return d.recordTo(ctx, "escalation:relay-unheard:"+at+":"+lane, fmt.Sprintf("RELAY-UNHEARD %s %s: no live dispatch and no live lane row, so nothing reads it; relaunch %s or send it to the lane that took over", key, lane, lane), sender)
}

func (d *Desk) refuseLine(record client.Record) error {
	line := collapsed(record.Text)
	if len([]rune(line)) > 80 {
		line = string([]rune(line)[:80]) + "…"
	}
	return d.record(fmt.Sprintf("escalation:cci:%d", record.Seq), fmt.Sprintf("DIRECTIVE-FAILED cci %s inbox: it opens `%s`, and a record addressed to %s is one `orca-desk: <relay|launch|alert|incident|hold|unhold|withdraw> …` line, with or without --re, or a root reply this desk acts on: a withdraw of a launch line or an answer to a desk-runner DECIDE; nothing was done", record.Name(), line, deskLane))
}

func (d *Desk) relayLine(ctx context.Context, at, key, rest, sender string) error {
	found := relayTo.FindStringSubmatch(rest)
	if found != nil {
		first, _, _ := strings.Cut(found[2], "\n")
		if strings.Contains(first, "; relay ") {
			found = nil
		}
	}
	if found == nil {
		return d.record("escalation:relay:"+at, fmt.Sprintf("RELAY-FAILED %s inbox: one relay per line, in the form `%s`; nothing was relayed", key, relayGrammar))
	}
	for _, named := range laneList.Split(found[1], -1) {
		lane, err := lanes.Resolve(ctx, d.Config.Drive, named)
		if err != nil {
			return err
		}
		target, err := d.pooled(ctx, key, lane, found[2], sender)
		if err != nil {
			return err
		}
		if target == "" {
			continue
		}
		if err := d.relayToLane(ctx, at, key, target, relayTarget{Text: found[2], From: sender}, nil); err != nil {
			return err
		}
		if err := d.warnUnheard(ctx, at, key, target, sender); err != nil {
			return err
		}
	}
	return nil
}

func (d *Desk) lane(lane string) (string, error) {
	container := laneBook(lane)
	exists, err := d.store.Exists(container)
	if err != nil || exists {
		return container, err
	}
	return container, d.ensure(container, or(d.receipt(lane), unlaunched))
}

func or(text, fallback string) string {
	if text == "" {
		return fallback
	}
	return text
}

type relayTarget struct {
	Text    string `json:"text"`
	ReplyTo string `json:"reply_to"`
	PR      string `json:"pr,omitempty"`
	From    string `json:"from,omitempty"`
	Record  string `json:"record,omitempty"`
}

func relayOf(action incident.Action) (relayTarget, error) {
	var target relayTarget
	return target, json.Unmarshal([]byte(action.Target), &target)
}

func (d *Desk) acceptRelay(key, lane string, relay relayTarget, minutes int) (incident.Action, bool, error) {
	container, err := d.lane(lane)
	if err != nil {
		return incident.Action{}, false, err
	}
	target, err := compactJSON(relay)
	if err != nil {
		return incident.Action{}, false, err
	}
	kind := kindRelay
	if relay.ReplyTo != "" {
		kind = kindReply
	}
	return d.accept(container, key, kind, target, key, d.Now().Add(time.Duration(minutes)*time.Minute))
}

func (d *Desk) relayToLane(ctx context.Context, at, key, lane string, relay relayTarget, question *string) error {
	log := "escalation:relay:" + at + ":" + lane
	dispatch, err := d.listed(ctx, lane)
	if err != nil {
		return err
	}
	if question != nil {
		relay.ReplyTo = *question
	} else if dispatch.active() {
		questions, err := d.openQuestions(ctx, dispatch)
		if err != nil {
			return err
		}
		if relay.ReplyTo, err = d.unclaimed(key, lane, questions); err != nil {
			return err
		}
	}
	action, created, err := d.acceptRelay(key, lane, relay, d.Config.StartMinutes)
	if err != nil {
		return err
	}
	if !created {
		held, err := relayOf(action)
		if err != nil {
			return err
		}
		if held.Text != relay.Text {
			return d.record(log, fmt.Sprintf("RELAY-FAILED %s %s: %s already holds a different relay to %s (%s)", key, lane, key, lane, action.Status))
		}
	}
	if !dispatch.active() {
		return nil
	}
	shape := "relay to"
	if relay.ReplyTo != "" {
		shape = "reply to question " + relay.ReplyTo + " of"
	}
	return d.record(log, fmt.Sprintf("RELAYED %s %s: %s dispatch %s", key, lane, shape, dispatch.ID))
}

func (d *Desk) runQuestions(ctx context.Context) ([]orcaMessage, error) {
	d.reads.mu.Lock()
	kept, found := d.reads.questions, d.reads.asked
	d.reads.mu.Unlock()
	if found {
		return kept, nil
	}
	asked, err := messagesOf(d.call(ctx, orchestration, "check", "--terminal", "run:"+d.Config.Run, "--all", "--types", "question"))
	if err != nil {
		return nil, err
	}
	d.reads.mu.Lock()
	defer d.reads.mu.Unlock()
	if d.reads.passing {
		d.reads.questions, d.reads.asked = asked, true
	}
	return asked, nil
}

func (d *Desk) askedBy(ctx context.Context, dispatch, terminal string) ([]orcaMessage, error) {
	asked, err := d.runQuestions(ctx)
	if err != nil {
		return nil, err
	}
	return slices.DeleteFunc(slices.Clone(asked), func(message orcaMessage) bool {
		return message.FromHandle != "dispatch:"+dispatch && message.FromHandle != terminal
	}), nil
}

func (d *Desk) openQuestions(ctx context.Context, dispatch *laneDispatch) ([]string, error) {
	mine, err := d.askedBy(ctx, dispatch.ID, dispatch.Terminal)
	if err != nil || len(mine) == 0 {
		return nil, err
	}
	replies, err := messagesOf(d.call(ctx, orchestration, "check", "--terminal", dispatch.Terminal, "--all"))
	if err != nil {
		return nil, err
	}
	answered := map[string]bool{}
	for _, message := range replies {
		answered[message.ThreadID] = true
	}
	slices.SortStableFunc(mine, func(a, b orcaMessage) int { return strings.Compare(b.CreatedAt, a.CreatedAt) })
	open := make([]string, 0, len(mine))
	for _, message := range mine {
		if !answered[message.ID] {
			open = append(open, message.ID)
		}
	}
	return open, nil
}

func (d *Desk) unclaimed(key, lane string, questions []string) (string, error) {
	container, err := d.lane(lane)
	if err != nil {
		return "", err
	}
	replies, err := d.store.Actions(container, kindReply)
	if err != nil {
		return "", err
	}
	claimed := map[string]bool{}
	for _, reply := range replies {
		if reply.ActionID == key || reply.Status == incident.ActionFailed {
			continue
		}
		target, err := relayOf(reply)
		if err != nil {
			return "", err
		}
		claimed[target.ReplyTo] = true
	}
	for _, question := range questions {
		if !claimed[question] {
			return question, nil
		}
	}
	return "", nil
}

type launchTarget struct {
	Model    string `json:"model"`
	Effort   string `json:"effort"`
	Brief    string `json:"brief"`
	Prior    string `json:"prior"`
	Role     string `json:"role"`
	Incident string `json:"incident"`
	By       string `json:"by"`
	Posted   string `json:"posted,omitempty"`
}

func launchOf(action incident.Action) (launchTarget, error) {
	var target launchTarget
	return target, json.Unmarshal([]byte(action.Target), &target)
}

func (d *Desk) takes(ctx context.Context, parsed *directive, key string) (bool, error) {
	if d.Config.Launches == "" || parsed.Verb == verbRelay || parsed.Verb == verbWithdraw {
		return true, nil
	}
	remote, err := d.spriteLaunch(ctx, parsed, key)
	return remote == (d.Config.Launches == launchesSprite), err
}

func (d *Desk) spriteLaunch(ctx context.Context, parsed *directive, key string) (bool, error) {
	spec := launchSpec.FindStringSubmatch(parsed.Rest)
	if parsed.Verb != verbLaunch || spec == nil {
		return false, nil
	}
	raw, _ := os.ReadFile(resolved(spec[6]))
	_, remote, err := d.onSprite(ctx, key, spec[1], spec[3], string(raw))
	return remote, err
}

func (d *Desk) launchLine(ctx context.Context, at, key, rest, carried, by string, posted time.Time) error {
	log := "escalation:launch:" + at
	spec := launchSpec.FindStringSubmatch(rest)
	if spec == nil {
		return d.recordTo(ctx, log, fmt.Sprintf("LAUNCH-FAILED %s inbox: one launch per line, in the form `%s`; nothing was launched", key, launchGrammar), verdictTo(by))
	}
	order := Order{Lane: spec[1], Now: spec[2] != "", Model: spec[3], Effort: spec[4], Role: spec[5], Brief: resolved(spec[6])}
	failed := func(why string) error {
		return d.recordTo(ctx, log, fmt.Sprintf("LAUNCH-FAILED %s %s: %s; nothing was launched", key, order.Lane, why), verdictTo(by))
	}
	if carried != "" && resolved(carried) != order.Brief {
		brief, err := d.carriedBrief(order.Lane, carried)
		if err != nil {
			return failed(fmt.Sprintf("brief %s was posted from another host, and the copy its go carries did not read: %v", order.Brief, err))
		}
		order.Brief = brief
	}
	if _, err := os.Stat(order.Brief); err != nil {
		return failed(fmt.Sprintf("brief %s is not a file on this Mac, and the go carries no copy of it; post the launch with `lr launch`, which carries the brief's body from any host", order.Brief))
	}
	if wrong := order.wrongLaneFault(); wrong != "" {
		return failed(wrong)
	}
	refusal, err := d.launchRefusal(ctx, key, order)
	if err != nil {
		return err
	}
	if refusal != "" {
		return failed(refusal)
	}
	raw, readErr := os.ReadFile(order.Brief)
	text := string(raw)
	if why := refusedBrief(d.Config.RefusedBriefs, text); why != "" {
		warned := fmt.Sprintf("BRIEF-NAMES-RULING %s %s: %s. It launches anyway, warned and not refused, with the ruling in its slice; withdraw it if this class is all its work", key, order.Lane, why)
		if err := d.recordTo(ctx, "escalation:ruling:"+at, warned, verdictTo(by)); err != nil {
			return err
		}
	}
	if sprite := unreasonedLocal(order.Lane, order.Model, text); readErr == nil && sprite != "" {
		if err := d.recordTo(ctx, "escalation:place:"+at, fmt.Sprintf("PLACE-UNREASONED %s %s: %s", key, order.Lane, sprite), verdictTo(by)); err != nil {
			return err
		}
	}
	if !order.Now {
		holder, err := d.duplicateHolder(ctx, order.Lane, text)
		if err != nil {
			return err
		}
		if holder != "" {
			return d.recordTo(ctx, log, fmt.Sprintf("LAUNCH-DUPLICATE %s %s: %s already holds this work; relay to it, or launch with NOW or --owner-directed when the owner directs a second lane; nothing was launched", key, order.Lane, holder), verdictTo(by))
		}
	}
	if readErr == nil {
		info, err := os.Stat(order.Brief)
		if err != nil {
			return err
		}
		if scale := order.scaleFault(text, info.ModTime()); scale != "" {
			if err := d.escalate("scale:"+key, "SCALE-MISSING", order.Lane, scale+"; "+order.Lane+" launches anyway"); err != nil {
				return err
			}
		}
		if missing := order.sliceWarning(text); missing != "" {
			if err := d.escalate("slice-missing:"+key, "SLICE-MISSING", order.Lane, missing); err != nil {
				return err
			}
		}
	}
	unread, err := d.refreshSlice(ctx, order.Lane, order.Brief, order.sliceStep(text), order.Model)
	if err != nil {
		return err
	}
	if err := d.sliceUnread(key, order.Lane, unread); err != nil {
		return err
	}
	_, _, err = d.acceptLaunch(key, order.Lane, launchTarget{Model: order.Model, Effort: order.Effort, Brief: order.Brief, Role: order.Role, By: by, Posted: store.Stamp(posted)})
	return err
}

func (d *Desk) carriedBrief(lane, carried string) (string, error) {
	raw, err := os.ReadFile(carried)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(raw)
	brief := filepath.Join(d.briefsDir(), fmt.Sprintf("%s-%x.md", lane, sum[:4]))
	if err := os.MkdirAll(filepath.Dir(brief), 0o750); err != nil {
		return "", err
	}
	return brief, os.WriteFile(brief, raw, 0o600)
}

func (d *Desk) acceptLaunch(key, lane string, spec launchTarget) (incident.Action, bool, error) {
	container, err := d.lane(lane)
	if err != nil {
		return incident.Action{}, false, err
	}
	spec.Prior = d.receipt(lane)
	target, err := compactJSON(spec)
	if err != nil {
		return incident.Action{}, false, err
	}
	if err := d.watchClockLane(lane); err != nil {
		return incident.Action{}, false, err
	}
	return d.accept(container, key, kindLaunch, target, key, d.Now().Add(time.Duration(d.Config.LaunchMinutes)*time.Minute))
}

func (d *Desk) dropClasses(ctx context.Context) []config.Refusal {
	listing, code, err := d.readBriefs(ctx, "answer", "list", "--label", config.DropClassLabel, "--limit", "0", "--json")
	if err == nil && code != 0 {
		err = errors.New(ccnFailure(listing, code))
	}
	var refusals []config.Refusal
	if err == nil {
		refusals, err = config.RefusalsOf(listing)
	}
	if err != nil {
		fmt.Fprintf(d.Stderr, "his drop classes did not read, so no alert line is dropped: %v\n", err)
	}
	return refusals
}

func refusedBrief(refusals []config.Refusal, brief string) string {
	refusal, line, refused := config.RefusedLine(refusals, brief)
	if !refused {
		return ""
	}
	line = strings.TrimSpace(line)
	return fmt.Sprintf("%s; brief line: %s", refusal.Why, line[:min(len(line), briefLineShown)])
}

func (d *Desk) launchRefusal(ctx context.Context, key string, order Order) (string, error) {
	raw, err := os.ReadFile(order.Brief)
	if faults := order.launchFaults(string(raw), err == nil); len(faults) > 0 {
		return strings.Join(faults, "; "), nil
	}
	container := laneBook(order.Lane)
	held, err := d.known(container, key)
	if err != nil {
		return "", err
	}
	if held != nil {
		return fmt.Sprintf("%s already holds a %s for %s (%s)", key, held.Kind, order.Lane, held.Status), nil
	}
	launches, err := d.laneActions(container, kindLaunch, incident.ActionAccepted, incident.ActionStarted)
	if err != nil {
		return "", err
	}
	if len(launches) > 0 {
		return fmt.Sprintf("launch %s for %s is %s", launches[0].ActionID, order.Lane, launches[0].Status), nil
	}
	dispatch, err := d.show(ctx, order.Lane, "")
	if err != nil {
		return "", err
	}
	if dispatch.active() && dispatch.Status != codeRemoteUnreachable {
		return fmt.Sprintf("dispatch %s is %s; relay to it instead", dispatch.ID, dispatch.Status), nil
	}
	return "", nil
}

func (d *Desk) refreshSlice(ctx context.Context, lane, brief, step, model string) (string, error) {
	raw, err := os.ReadFile(brief)
	if err != nil {
		return "", err
	}
	text := string(raw)
	switch at := briefSlice.FindStringIndex(text); {
	case at == nil:
		text = strings.TrimRight(text, " \t\r\n") + "\n\n" + sliceHeader(lane, step, nil, text) + "\n"
	case !parsedSlice.MatchString(text):
		text = text[:at[0]] + sliceHeader(lane, step, nil, text) + text[at[1]:]
	}
	if agent, _, _ := modelRoute(model); agent != agentClaude {
		text = uncodexSlice.ReplaceAllString(text, "$1 codex=yes")
	}
	if text != string(raw) {
		if err := os.WriteFile(brief, []byte(text), 0o600); err != nil {
			return "", err
		}
	}
	output, code, err := d.captureIdempotent(ctx, d.tool("rulings.py"), "slice", "--repo", d.Config.BriefsRepo, "--drive", d.Config.Drive, "--refresh", brief)
	if err != nil || code == 0 {
		return "", err
	}
	lines := strings.Split(strings.TrimSpace(output), "\n")
	return fmt.Sprintf("`rulings.py slice --refresh` exited %d: %s", code, or(lines[len(lines)-1], "no output")), nil
}

func (d *Desk) sliceUnread(key, lane, unread string) error {
	if unread == "" {
		return nil
	}
	return d.escalate("slice:"+key, "SLICE-UNREAD", lane, unread+"; "+lane+" launches on its brief without a fresh register slice")
}

func (d *Desk) withdrawLine(ctx context.Context, at, key, rest string) error {
	spec := withdrawSpec.FindStringSubmatch(rest)
	if spec == nil {
		return d.record("escalation:withdraw:"+at, fmt.Sprintf("WITHDRAW-FAILED %s inbox: one withdrawal per line, in the form `%s`; nothing was withdrawn", key, withdrawGrammar))
	}
	return d.withdraw(ctx, at, key, spec[1])
}

type heldLaunches struct {
	lane     string
	launches []incident.Action
}

func (d *Desk) launchesUnder(target string, keyed bool) ([]heldLaunches, error) {
	if !keyed {
		launches, err := d.laneActions(laneBook(target), kindLaunch)
		if err != nil {
			return nil, err
		}
		exists, err := d.store.Exists(laneBook(target))
		if err != nil {
			return nil, err
		}
		if exists || d.receipt(target) != "" {
			return []heldLaunches{{lane: target, launches: launches}}, nil
		}
		if client.Stem(target) != target {
			return nil, nil
		}
		newest, err := d.newestUnder(target)
		if err != nil || newest == "" {
			return nil, err
		}
		return d.launchesUnder(newest, false)
	}
	books, err := d.containers(lanePrefix)
	if err != nil {
		return nil, err
	}
	var held []heldLaunches
	for _, container := range books {
		action, err := d.known(container, target)
		if err != nil {
			return nil, err
		}
		if action != nil && action.Kind == kindLaunch {
			held = append(held, heldLaunches{lane: strings.TrimPrefix(container, lanePrefix), launches: []incident.Action{*action}})
		}
	}
	return held, nil
}

func (d *Desk) newestUnder(stem string) (string, error) {
	rows, err := d.Registry.AllLanes()
	if err != nil {
		return "", err
	}
	var names []string
	for _, row := range rows {
		if row.State != store.LaneRetired {
			names = append(names, row.Name)
		}
	}
	books, err := d.containers(lanePrefix + stem + "-")
	if err != nil {
		return "", err
	}
	for _, book := range books {
		held, err := d.laneActions(book, kindLaunch, incident.ActionAccepted, incident.ActionStarted)
		if err != nil {
			return "", err
		}
		if len(held) > 0 {
			names = append(names, strings.TrimPrefix(book, lanePrefix))
		}
	}
	newest, highest := "", 0
	for _, name := range names {
		number, under := strings.CutPrefix(name, stem+"-")
		if n, err := strconv.Atoi(number); under && err == nil && n > highest {
			newest, highest = name, n
		}
	}
	return newest, nil
}

func (d *Desk) withdraw(ctx context.Context, at, key, target string) error {
	log := "escalation:withdraw:" + at
	keyed := launchKey.MatchString(target)
	held, err := d.launchesUnder(target, keyed)
	if err != nil {
		return err
	}
	if len(held) == 0 {
		if seq, err := strconv.ParseInt(strings.TrimPrefix(target, "R"), 10, 64); err == nil && d.withdrawals[seq] != "" {
			return nil
		}
		return d.record(log, fmt.Sprintf("WITHDRAW-FAILED %s %s: the runner holds no launch or lane under %s; nothing was withdrawn", key, target, target))
	}
	for _, entry := range held {
		if err := d.withdrawLane(ctx, log, key, target, keyed, entry); err != nil {
			return err
		}
	}
	return nil
}

func (d *Desk) withdrawLane(ctx context.Context, log, key, target string, keyed bool, entry heldLaunches) error {
	lane := entry.lane
	container, err := d.lane(lane)
	if err != nil {
		return err
	}
	for _, action := range entry.launches {
		note := log + ":" + lane + ":" + action.ActionID
		switch action.Status {
		case incident.ActionAccepted:
			if err := d.store.Edit(container, func(tx *incident.Tx) error {
				_, err := tx.Fail(action.ActionID, "withdrawn by "+key)
				return err
			}); err != nil {
				return err
			}
			if err := d.record(note, fmt.Sprintf("WITHDRAWN %s %s: by %s; nothing was launched", action.ActionID, lane, key)); err != nil {
				return err
			}
		case incident.ActionStarted:
			if err := d.setFact(container, withdrawPending, map[string]any{kindLaunch: action.ActionID, "by": key}); err != nil {
				return err
			}
			mark := d.withdrawnMark(container, action.ActionID)
			if err := os.MkdirAll(filepath.Dir(mark), 0o750); err != nil {
				return err
			}
			if err := os.WriteFile(mark, []byte(key+"\n"), 0o600); err != nil {
				return err
			}
			if err := d.record(note, fmt.Sprintf("WITHDRAW-PENDING %s %s: by %s; it is still starting, so its launcher stops before worker-start, or the runner stops the worker once it logs LAUNCHED", action.ActionID, lane, key)); err != nil {
				return err
			}
		}
	}
	dispatch := d.receipt(lane)
	if keyed {
		dispatch = ""
		if entry.launches[0].Status == incident.ActionVerified {
			dispatch = string(entry.launches[0].DispatchID)
		}
	}
	if dispatch != "" {
		return d.stopWithdrawn(ctx, log+":"+lane, key, target, lane, dispatch)
	}
	status := incident.ActionAccepted
	if len(entry.launches) > 0 {
		status = entry.launches[0].Status
	}
	if keyed && status != incident.ActionAccepted && status != incident.ActionStarted {
		return d.record(log+":"+lane, fmt.Sprintf("WITHDRAW-FAILED %s %s: launch %s is %s; nothing was withdrawn", key, lane, target, status))
	}
	return nil
}

func (d *Desk) stopWithdrawn(ctx context.Context, log, key, target, lane, dispatch string) error {
	container, err := d.lane(lane)
	if err != nil {
		return err
	}
	if err := d.setFact(container, withdrawnFact, map[string]any{"dispatch": dispatch, "by": key}); err != nil {
		return err
	}
	shown, err := d.show(ctx, lane, dispatch)
	if err != nil {
		return err
	}
	if shown != nil && inactive(shown.Status) {
		return d.record(log, fmt.Sprintf("WITHDRAWN %s %s: by %s; dispatch %s was already %s, and no report of it launches a case lane", target, lane, key, dispatch, shown.Status))
	}
	state, failure := d.fence(ctx, dispatch)
	if state == "" {
		return d.record(log, fmt.Sprintf("WITHDRAW-FAILED %s %s: worker-stop of dispatch %s failed: %s; it is marked withdrawn, so no report of it launches a case lane; stop it with lr gc --done", key, lane, dispatch, failure))
	}
	seen, err := d.unconfirmedStop(ctx, lane, dispatch)
	if err != nil {
		return err
	}
	if seen != "" {
		return d.record(log, fmt.Sprintf("WITHDRAW-UNCONFIRMED %s %s: worker-stop of dispatch %s answered %s, but after %ds Orca shows %s; it is marked withdrawn, so no report of it launches a case lane; stop it with lr gc --done", key, lane, dispatch, state, int(stopConfirm.Seconds()), seen))
	}
	return d.record(log, fmt.Sprintf("WITHDRAWN %s %s: by %s; dispatch %s stopped (%s), and no report of it launches a case lane", target, lane, key, dispatch, state))
}

func (d *Desk) unconfirmedStop(ctx context.Context, lane, dispatch string) (string, error) {
	deadline := d.Now().Add(stopConfirm)
	for {
		shown, err := d.showNow(ctx, lane, dispatch)
		if err != nil {
			return "", err
		}
		var seen string
		switch {
		case shown == nil:
			seen = fmt.Sprintf("no dispatch %s, and terminal %s unchecked", dispatch, or(d.terminal(lane), "unknown"))
		case !inactive(shown.Status):
			seen = fmt.Sprintf("dispatch %s still %s on terminal %s", dispatch, shown.Status, shown.Terminal)
		case shown.Live && !d.atRest(ctx, shown.Terminal, shown.Environment):
			seen = fmt.Sprintf("dispatch %s %s, but terminal %s still at work", dispatch, shown.Status, shown.Terminal)
		default:
			return "", nil
		}
		if !d.Now().Before(deadline) {
			return seen, nil
		}
		d.Sleep(stopPoll)
	}
}

func (d *Desk) withdrawnBy(lane string) (string, error) {
	value, found, err := d.factOf(laneBook(lane), withdrawnFact)
	if err != nil || !found {
		return "", err
	}
	mark, _ := value.(map[string]any)
	if mark["dispatch"] != d.receipt(lane) {
		return "", nil
	}
	by, _ := mark["by"].(string)
	return by, nil
}

func (d *Desk) alertLine(ctx context.Context, at, key, rest string) error {
	log := "escalation:alert:" + at
	spec := alertSpec.FindStringSubmatch(rest)
	if spec == nil {
		return d.record(log, fmt.Sprintf("ALERT-FAILED %s inbox: one alert per line, in the form `%s`; nothing was recorded", key, alertGrammar))
	}
	slug, link, what := strings.TrimSuffix(spec[1], spawnedFix), spec[2], spec[3]
	lane := slug + spawnedFix
	dispatch, err := d.show(ctx, lane, "")
	if err != nil {
		return err
	}
	if dispatch.active() {
		return d.relayToLane(ctx, at, key+":again", lane, relayTarget{Text: fmt.Sprintf("The alert fired again: %s %s", what, link)}, nil)
	}
	if muted, err := d.ownerMuted(ctx, lineMonitors(link, what)); muted != "" || err != nil {
		return err
	}
	return d.record(log, fmt.Sprintf("ALERT %s: %s %s | no lane launched; the alerts desk writes `%s` when it judges one necessary", slug, what, link, incidentGrammar))
}

func (d *Desk) incidentLine(ctx context.Context, at, key, rest string, line client.Record) error {
	log := "escalation:incident:" + at
	spec := incidentSpec.FindStringSubmatch(rest)
	if spec == nil {
		return d.record(log, fmt.Sprintf("INCIDENT-FAILED %s inbox: one incident per line, in the form `%s`; nothing was launched", key, incidentGrammar))
	}
	slug, link, what := strings.TrimSuffix(spec[1], spawnedFix), spec[2], spec[3]
	lane := slug + spawnedFix
	dispatch, err := d.show(ctx, lane, "")
	if err != nil {
		return err
	}
	if dispatch.active() {
		return d.relayToLane(ctx, at, key+":again", lane, relayTarget{Text: fmt.Sprintf("The alert fired again: %s %s", what, link)}, nil)
	}
	ended, err := d.lineEnded(ctx, lane, line)
	if err != nil {
		return err
	}
	if ended != "" {
		return d.record(log, fmt.Sprintf("WITHDRAWN %s %s: %s after its incident line was posted; nothing was launched", key, lane, ended))
	}
	monitors := map[string]bool{}
	for _, monitor := range lineMonitors(link, what) {
		monitors[monitor] = true
	}
	muted, err := d.ownerMuted(ctx, sortedKeys(monitors))
	if err != nil {
		return err
	}
	if muted != "" {
		return d.record(log, fmt.Sprintf("INCIDENT-MUTED %s %s: %s; no launch", key, lane, muted))
	}
	if len(monitors) > 0 {
		peer, err := d.incidentLane(ctx, monitors, lane)
		if err != nil {
			return err
		}
		if peer != "" {
			return d.record(log, fmt.Sprintf("LAUNCH-SKIPPED %s %s: duplicate of %s, already on monitor %s", key, lane, peer, strings.Join(sortedKeys(monitors), ", ")))
		}
	}
	owner, err := d.incidentOwner(ctx, slug, lane)
	if err != nil {
		if err := d.noteTailFailure(err); err != nil {
			return err
		}
		owner = ""
	}
	if owner != "" {
		return d.escalate(key, "INCIDENT-OWNED", lane, fmt.Sprintf("incident %s owned by %s; no launch", slug, owner))
	}
	fired := lanes.Fired{Slug: slug, What: what, Link: link, At: d.Now()}
	d.background.Go(func() {
		if err := d.FireIncident(ctx, fired); err != nil {
			fmt.Fprintf(d.Stderr, "incident %s launch: %v\n", slug, err)
			if err := d.record(log+":failed", fmt.Sprintf("INCIDENT %s: %s %s | %s not launched: %v", slug, what, link, lane, err)); err != nil {
				fmt.Fprintf(d.Stderr, "incident %s record: %v\n", slug, err)
			}
		}
	})
	return d.record(log, fmt.Sprintf("INCIDENT %s: %s %s | fix lane %s launching on incident high with forward-fix authority, evidence lane %s%s beside it; root: ratify", slug, what, link, lane, slug, spawnedEvidence))
}

type holdTarget struct {
	Slug  string `json:"slug"`
	Owner string `json:"owner"`
	What  string `json:"what"`
}

func (d *Desk) holds(slug string) ([]incident.Action, error) {
	held, err := d.store.Actions(deskRunner, kindHold, incident.ActionAccepted)
	if err != nil {
		return nil, err
	}
	return slices.DeleteFunc(held, func(action incident.Action) bool {
		var target holdTarget
		return json.Unmarshal([]byte(action.Target), &target) != nil || target.Slug != slug
	}), nil
}

func (d *Desk) holdLine(at, key, rest string) error {
	spec := holdSpec.FindStringSubmatch(rest)
	if spec == nil {
		return d.record("escalation:hold:"+at, fmt.Sprintf("HOLD-FAILED %s inbox: one hold per line, in the form `%s`; no clock started", key, holdGrammar))
	}
	held, err := d.holds(spec[1])
	if err != nil || len(held) > 0 {
		return err
	}
	target, err := compactJSON(holdTarget{Slug: spec[1], Owner: spec[2], What: spec[3]})
	if err != nil {
		return err
	}
	_, _, err = d.accept(deskRunner, "hold:"+spec[1]+"@"+d.stamp(), kindHold, target, key, d.Now().Add(time.Duration(d.Config.HoldMinutes)*time.Minute))
	return err
}

func (d *Desk) unholdLine(at, key, rest string) error {
	spec := unholdSpec.FindStringSubmatch(rest)
	if spec == nil {
		return d.record("escalation:unhold:"+at, fmt.Sprintf("HOLD-FAILED %s inbox: lift a hold in the form `%s`", key, unholdGrammar))
	}
	held, err := d.holds(spec[1])
	if err != nil {
		return err
	}
	for _, action := range held {
		if err := d.store.Edit(deskRunner, func(tx *incident.Tx) error {
			_, err := tx.Verify(action.ActionID, map[string]any{"at": d.stamp(), "by": key})
			return err
		}); err != nil {
			return err
		}
	}
	return nil
}

func lineMonitors(link, what string) []string {
	matches := monitorID.FindAllStringSubmatch(link+" "+what, -1)
	monitors := make([]string, 0, len(matches))
	for _, found := range matches {
		monitors = append(monitors, found[1])
	}
	return monitors
}

func (d *Desk) ownerMuted(ctx context.Context, monitors []string) (string, error) {
	if len(monitors) == 0 {
		return "", nil
	}
	records, err := d.tail(ctx, alert.MutedQuery())
	if err != nil {
		return "", d.noteTailFailure(err)
	}
	if record, found := alert.Muted(records, monitors); found {
		return alert.MutedLine(record), nil
	}
	return "", nil
}
