package prrow

import (
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	portInteractsUnstated = "unstated"
	headGrace             = 5 * time.Minute
	oldOverridesCursor    = "old-overrides"
	oldOverrideWindow     = 14 * 24 * time.Hour
)

const (
	FactWithdrawn = "withdrawn"
	FactFailing   = "read-failing"
	FactDraft     = "read-draft"
	FactForeign   = "read-foreign"
	FactTapBy     = "tap-by"
	FactTapAt     = "tap-at"

	FactOldTree  = "read-old-tree"
	FactNextTree = "read-next-tree"
	NoPortAttr   = "no-port"
	PortOwed     = "owed"
	PortOf       = "port of #"
	PortInPR     = "in this PR"

	FactUnknownSince = "unknown-since"
	FactBumps        = "mergeable-bumps"

	shortSHA    = 7
	foldWindow  = 72 * time.Hour
	bodyBytes   = 60_000
	readKind    = "github"
	readRecord  = "pr.read"
	readLane    = "pr-read"
	readReader  = "supervisor"
	stateMerged = "merged"

	factCI        = "read-ci"
	factApprovers = "read-approvers"
	factMergeable = "read-mergeable"
	factQueue     = "read-queue"
	factValues    = "read-values"
	factBody      = "read-body"
	factSeen      = "seen"
	factCarriedBy = "read-carried-by"
	factTapFrom   = "read-tap-from"
	factReturned  = "read-tap-returned"
	saidSuffix    = "-said"

	deltaAttr       = "delta"
	oldTreeAttr     = "old-tree"
	nextTreeAttr    = "next-tree"
	carriedByAttr   = "carried_by"
	tapFromAttr     = "tap_from"
	tapReturnedAttr = "tap_returned"
	notRecorded     = "not recorded"
)

var (
	foldKinds = []string{"opened", "head", "lr.head", readKind, "duplicate", "decide", "landed"}
	anySHA    = regexp.MustCompile(`\b[0-9a-f]{7,40}\b`)
)

type Fold struct {
	Drive string
	Repo  string
	Trunk string
	Store *store.Store
	Now   func() time.Time
}

type Held struct {
	Why       string
	AwaitsTap string
}

func (f Fold) Pass(ctx context.Context, held map[int]Held) ([]store.ReviewRow, error) {
	if err := f.readOldOverrides(ctx); err != nil {
		return nil, err
	}
	after, err := f.Store.Cursor(store.ReviewRowsCursor)
	if err != nil {
		return nil, err
	}
	query := client.Query{Drive: f.Drive, After: after, Kinds: foldKinds}
	if after == 0 {
		query.Since = f.Now().Add(-foldWindow)
	}
	records, err := client.Tail(ctx, query)
	if err != nil {
		return nil, err
	}
	var out []store.ReviewRow
	err = f.Store.Update(func(t *store.Tx) error {
		if err := t.Advance(store.ReviewRowsCursor, 0); err != nil {
			return err
		}
		position, err := t.Cursor(store.ReviewRowsCursor)
		if err != nil {
			return err
		}
		stored, err := t.ReviewRows(f.Repo)
		if err != nil {
			return err
		}
		rows := make(map[int]store.ReviewRow, len(stored))
		for _, row := range stored {
			rows[row.Number] = row
		}
		before := maps.Clone(rows)
		outside, err := t.OutsidePRs(f.Repo)
		if err != nil {
			return err
		}
		for _, record := range records {
			if record.Seq <= position {
				continue
			}
			if err := f.apply(t, rows, outside, record); err != nil {
				return err
			}
			if err := t.Advance(store.ReviewRowsCursor, record.Seq); err != nil {
				return err
			}
		}
		if err := t.PutReviewRows(values(rows)); err != nil {
			return err
		}
		atHeads, err := t.FactsAtRowHeads(f.Repo)
		if err != nil {
			return err
		}
		branches := make(map[string]store.ReviewRow, len(rows))
		for _, row := range rows {
			if row.Branch != "" {
				branches[row.Branch] = row
			}
		}
		written := f.Now()
		for number, row := range rows {
			if Terminal(row.State) {
				row = withoutExemptTap(row, atHeads[number])
			} else {
				row = Materialize(row, atHeads[number], held[number], branches, f.Trunk)
				if atHeads[number][FactForeign] != "" && !row.Tapped && !Exempted(row, atHeads[number]) {
					delete(rows, number)
					if err := t.DeleteReviewRow(f.Repo, number); err != nil {
						return err
					}
					continue
				}
			}
			row.WrittenAt = store.Stamp(written)
			rows[number] = row
		}
		ports := portsOf(rows, atHeads)
		for number, row := range rows {
			row.Port = portState(atHeads[number], ports[number])
			row.MovedAt = movedAt(before[number], row, written)
			rows[number] = row
		}
		for number, pr := range outside {
			if _, driven := rows[number]; driven {
				delete(outside, number)
				if err := t.DeleteOutsidePR(f.Repo, number); err != nil {
					return err
				}
				continue
			}
			pr.Port = portState(map[string]string{FactOldTree: pr.OldTree, NoPortAttr: pr.NoPort}, ports[number])
			outside[number] = pr
		}
		if err := t.PutOutsidePRs(outside); err != nil {
			return err
		}
		out = values(rows)
		return t.PutReviewRows(out)
	})
	return out, err
}

func portsOf(rows map[int]store.ReviewRow, atHeads map[int]map[string]string) map[int]store.ReviewRow {
	ports := map[int]store.ReviewRow{}
	for number, row := range rows {
		source, err := strconv.Atoi(atHeads[number][gates.PortOfAttr])
		if err != nil || row.State == StateClosed || row.State == StateSuperseded {
			continue
		}
		if known, found := ports[source]; !found || number < known.Number {
			ports[source] = row
		}
	}
	return ports
}

func movedAt(before, after store.ReviewRow, now time.Time) string {
	if before.MovedAt != "" && before.Head == after.Head && before.Grade == after.Grade && before.Queue == after.Queue {
		return before.MovedAt
	}
	return store.Stamp(now)
}

func portState(facts map[string]string, port store.ReviewRow) string {
	switch {
	case facts[FactOldTree] == "" && facts[gates.PortOfAttr] != "":
		return PortOf + facts[gates.PortOfAttr]
	case facts[FactOldTree] == "":
		return None
	case port.Number != 0:
		return fmt.Sprintf("port #%d, %s", port.Number, port.State)
	case facts[FactNextTree] != "":
		return fmt.Sprintf("%s: it changes %s in the next tree too", PortInPR, facts[FactNextTree])
	case facts[NoPortAttr] != "":
		return "no port: " + clip(facts[NoPortAttr], fieldChars)
	}
	return fmt.Sprintf("%s: it changes %s in the old tree, and no port PR's head record or no-port decide names it", PortOwed, facts[FactOldTree])
}

// TODO: delete once every drive's store has advanced this cursor; it reads the overrides posted before each one named its grader.
func (f Fold) readOldOverrides(ctx context.Context) error {
	read, err := f.Store.Cursor(oldOverridesCursor)
	if err != nil || read != 0 {
		return err
	}
	records, err := client.Tail(ctx, client.Query{Drive: f.Drive, Kinds: []string{"decide"}, Since: f.Now().Add(-oldOverrideWindow)})
	if err != nil {
		return err
	}
	for _, record := range records {
		for _, fact := range bus.LegacyFacts(record, f.Repo, nil) {
			if !strings.HasPrefix(fact.Kind, machine.OverrideFact("")) {
				continue
			}
			if _, err := f.Store.PutFact(f.Repo, fact.PR, fact.Head, fact.Kind, fact.Value, record.At); err != nil {
				return err
			}
		}
	}
	return f.Store.Advance(oldOverridesCursor, 1)
}

func values(rows map[int]store.ReviewRow) []store.ReviewRow {
	out := make([]store.ReviewRow, 0, len(rows))
	for _, row := range rows {
		out = append(out, row)
	}
	slices.SortFunc(out, func(a, b store.ReviewRow) int { return a.Number - b.Number })
	return out
}

func (f Fold) apply(t *store.Tx, rows map[int]store.ReviewRow, outside map[int]store.OutsidePR, record client.Record) error {
	attrs := record.Fields.Attrs
	if repo := attrs["repo"]; repo != "" && !strings.EqualFold(repo, f.Repo) {
		return nil
	}
	for _, number := range bus.HeldBy(record, f.Repo) {
		row, known := rows[number]
		var err error
		switch string(record.Kind) {
		case "opened":
			if !known {
				row = newRow(f.Repo, number)
			}
			row, err = f.pushed(t, row, record, attrs["head"])
		case "head":
			if !known {
				if head := f.headOf(record, number); head != "" {
					err = f.putClaims(t, number, record, head)
				}
				if err != nil {
					return err
				}
				continue
			}
			row, err = f.pushed(t, row, record, f.headOf(record, number))
		case "lr.head":
			if !known {
				continue
			}
			row, err = f.typedHead(t, row, record)
		case readKind:
			if !known || attrs["record"] != readRecord {
				continue
			}
			row, err = f.read(t, row, record)
		case "duplicate":
			if !known || attrs["superseded_by"] == "" {
				continue
			}
			err = t.PutFact(f.Repo, number, row.Head, FactWithdrawn, "replaced by #"+attrs["superseded_by"], record.At)
		case "decide":
			if pr, admitted := outside[number]; admitted && attrs[NoPortAttr] != "" {
				pr.NoPort = attrs[NoPortAttr]
				outside[number] = pr
			}
			if !known || attrs[NoPortAttr] == "" {
				continue
			}
			err = t.PutFact(f.Repo, number, row.Head, NoPortAttr, attrs[NoPortAttr], record.At)
		case "landed":
			if !known {
				continue
			}
			row = landed(row, record.At)
		}
		if err != nil {
			return err
		}
		row.LastRecord = record.Seq
		rows[number] = row
	}
	return nil
}

func newRow(repo string, number int) store.ReviewRow {
	return Decide(store.ReviewRow{
		Repo: repo, Number: number, Parent: None, ParentState: None, State: StateOpen, CI: CIPending, Approvers: None,
		Review: noReview, Grade: GradePending, GradeFindings: None, Mergeable: string(machine.MergeableUnknown),
		Queue: string(machine.QueueNone), Hold: None, SupersededBy: None, TappedAt: NotYet, TappedBy: NotYet, LandedAt: NotYet,
	})
}

const noReview = "no approving review at this head"

func (f Fold) headNamed(record client.Record, number int) string {
	return bus.HeadNamed(record, f.Repo, number)
}

func (f Fold) headOf(record client.Record, number int) string {
	attrs := record.Fields.Attrs
	if named := f.headNamed(record, number); named != "" {
		_, sha, moved := strings.Cut(strings.Fields(named)[0], "..")
		if moved {
			return sha
		}
		return strings.Fields(named)[0]
	}
	if attrs["head"] != "" {
		return attrs["head"]
	}
	return anySHA.FindString(record.Text)
}

func (f Fold) pushed(t *store.Tx, row store.ReviewRow, record client.Record, head string) (store.ReviewRow, error) {
	at := store.Stamp(record.At)
	if head != "" {
		head = fullHead(head, row)
		replaced := ""
		if at >= row.HeadAt && !sameHead(row.Head, head) {
			replaced = row.Head
		}
		if err := f.putDelta(t, row.Number, record, head, replaced); err != nil {
			return row, err
		}
		if err := f.putClaims(t, row.Number, record, head); err != nil {
			return row, err
		}
	}
	if at < row.HeadAt {
		return row, nil
	}
	if record.Kind == "opened" && row.State == StateClosed {
		row.State = StateOpen
	}
	row.Lane = client.Stem(record.Lane)
	attrs := record.Fields.Attrs
	for field, value := range map[*string]string{&row.Branch: attrs["branch"], &row.Base: attrs["base"], &row.Title: attrs["title"]} {
		if value != "" {
			*field = value
		}
	}
	if head != "" {
		row.Head, row.HeadAt = head, at
		if err := t.PutFact(f.Repo, row.Number, head, factSeen, "yes", record.At); err != nil {
			return row, err
		}
	}
	if body, carried := bodyOf(record); carried {
		ApplyBody(&row, body)
	}
	return row, nil
}

func (f Fold) putClaims(t *store.Tx, number int, record client.Record, head string) error {
	attrs := record.Fields.Attrs
	claims := map[string]string{gates.FlakeFixAttr: attrs[gates.FlakeFixAttr]}
	if _, err := strconv.Atoi(attrs[gates.PortOfAttr]); err == nil {
		claims[gates.PortOfAttr] = attrs[gates.PortOfAttr]
		claims[gates.PortInteractsAttr] = cmp.Or(attrs[gates.PortInteractsAttr], portInteractsUnstated)
	}
	for attr, value := range claims {
		if value == "" {
			continue
		}
		if err := t.PutFact(f.Repo, number, head, attr, value, record.At); err != nil {
			return err
		}
	}
	return nil
}

func (f Fold) putDelta(t *store.Tx, number int, record client.Record, head, replaced string) error {
	was, why := "", ""
	if fields := strings.Fields(f.headNamed(record, number)); len(fields) > 0 {
		named, _, moved := strings.Cut(fields[0], "..")
		if moved {
			was = named
		}
		why = strings.Join(fields[1:], " ")
	}
	said := map[string]string{gates.DeltaFromFact: cmp.Or(was, replaced)}
	if class := record.Fields.Attrs[deltaAttr]; class != "" {
		said[gates.DeltaFact], said[gates.DeltaWhyFact] = class, clip(cmp.Or(why, record.Text), fieldChars)
	}
	for kind, value := range said {
		if value == "" {
			continue
		}
		if err := t.PutFact(f.Repo, number, head, kind, value, record.At); err != nil {
			return err
		}
	}
	return nil
}

func BodyRecord(body string) (json.RawMessage, error) {
	return json.Marshal(map[string]string{"body": strings.ToValidUTF8(body[:min(len(body), bodyBytes)], "")})
}

func bodyOf(record client.Record) (string, bool) {
	var carried struct {
		Body *string `json:"body"`
	}
	if len(record.Body) == 0 || json.Unmarshal(record.Body, &carried) != nil || carried.Body == nil {
		return "", false
	}
	return *carried.Body, true
}

func (f Fold) typedHead(t *store.Tx, row store.ReviewRow, record client.Record) (store.ReviewRow, error) {
	parsed, err := bus.Parse(record)
	if err != nil {
		return row, nil
	}
	head, isHead := parsed.Body.(bus.Head)
	if !isHead {
		return row, nil
	}
	if head.Title != "" {
		row.Title = head.Title
	}
	return f.pushed(t, row, record, head.Head)
}

func (f Fold) read(t *store.Tx, row store.ReviewRow, record client.Record) (store.ReviewRow, error) {
	attrs := record.Fields.Attrs
	head := attrs["head"]
	for kind, value := range map[string]string{
		factCI: attrs["ci"], factApprovers: attrs["approvers"], factMergeable: attrs["mergeable"], factQueue: attrs["queue"],
		FactFailing: attrs["failing"], FactDraft: attrs["draft"], FactForeign: attrs["foreign"], factValues: valuesOf(attrs), factBody: attrs["body"],
		factCarriedBy: attrs[carriedByAttr], factTapFrom: attrs[tapFromAttr], factReturned: attrs[tapReturnedAttr], FactOldTree: attrs[oldTreeAttr], FactNextTree: attrs[nextTreeAttr],
	} {
		if err := t.PutFact(f.Repo, row.Number, head, kind, value, record.At); err != nil {
			return row, err
		}
	}
	row.ReadHead = head
	if !sameHead(row.Head, head) {
		seen, err := t.HeadSeen(f.Repo, row.Number, head, factSeen)
		if err != nil {
			return row, err
		}
		pushed, unstamped := store.Parse(row.HeadAt)
		if !seen || unstamped != nil || record.At.Sub(pushed) >= headGrace {
			if row.Head != "" {
				if err := t.PutFact(f.Repo, row.Number, head, gates.DeltaFromFact, row.Head, record.At); err != nil {
					return row, err
				}
			}
			row.Head, row.HeadAt = head, store.Stamp(record.At)
			if err := t.PutFact(f.Repo, row.Number, head, factSeen, "yes", record.At); err != nil {
				return row, err
			}
		}
	} else {
		row.Head = longer(row.Head, head)
	}
	row.Title, row.Base, row.Branch = record.Text, attrs["base"], attrs["branch"]
	if body, carried := bodyOf(record); carried {
		ApplyBody(&row, body)
	}
	switch {
	case attrs["state"] == stateMerged:
		return landed(row, record.At), nil
	case row.State == StateLanded:
	case attrs["state"] == StateClosed:
		row.State = StateClosed
	case row.State == StateClosed:
		row.State = StateOpen
	}
	if Terminal(row.State) {
		row = Decide(row)
	}
	return row, nil
}

func landed(row store.ReviewRow, at time.Time) store.ReviewRow {
	if row.State != StateLanded {
		row.State, row.LandedAt = StateLanded, store.Stamp(at)
	}
	row.Queue = string(machine.QueueNone)
	return Decide(row)
}

func sameHead(a, b string) bool {
	return a != "" && b != "" && (strings.HasPrefix(a, b) || strings.HasPrefix(b, a))
}

func fullHead(head string, row store.ReviewRow) string {
	for _, known := range []string{row.ReadHead, row.Head} {
		if strings.HasPrefix(known, head) {
			return known
		}
	}
	return head
}

func longer(a, b string) string {
	if len(b) > len(a) {
		return b
	}
	return a
}

func Materialize(row store.ReviewRow, facts map[string]string, held Held, branches map[string]store.ReviewRow, trunk string) store.ReviewRow {
	row = materialized(row, facts, held, branches, trunk)
	why := facts[machine.EvictionFact]
	if why == "" || Terminal(row.State) || row.State == StateQueued || row.SupersededBy != None {
		return row
	}
	row.State, row.WaitsOn = StateEvicted, "It fell out of the merge queue: "+clip(why, fieldChars)
	return row
}

func materialized(row store.ReviewRow, facts map[string]string, held Held, branches map[string]store.ReviewRow, trunk string) store.ReviewRow {
	row.Lane = client.Stem(row.Lane)
	row.CI, row.Approvers = orElse(facts[factCI], CIPending), orElse(facts[factApprovers], None)
	row.Review = noReview
	if row.Approvers != None {
		row.Review = "approved by " + row.Approvers
	}
	row.Mergeable = orElse(facts[factMergeable], string(machine.MergeableUnknown))
	row.Queue = orElse(facts[factQueue], string(machine.QueueNone))
	row.Grade, row.GradeFindings = gradeOf(facts)
	if facts[gates.BehaviorFact] == gates.BehaviorChanged {
		row.Review += fmt.Sprintf("; changed since the head he tapped, %s: %s", facts[gates.SinceFact], facts[gates.ChangedFact])
	}
	row.PlanRow = planRow(row.PlanLine, machine.Fidelity(facts["fidelity"]), facts[machine.OverrideFact("fidelity")])
	row.Hold, row.AwaitsTap = orElse(held.Why, None), held.AwaitsTap
	row.SupersededBy = orElse(facts[FactWithdrawn], None)
	row.Parent, row.ParentState = parentOf(row, branches, trunk)
	tappedHere := machine.Tap(facts["tap"]) == machine.TapTapped
	carried := facts[factCarriedBy] != "" && !tappedHere
	row.Tapped = (tappedHere || carried) && held.AwaitsTap == ""
	row.TappedAt, row.TappedBy = NotYet, NotYet
	row.TapRefused = orElse(facts[bus.TapRefusedFact], None)
	if row.Tapped {
		row.TapRefused = None
		unstated := notRecorded
		if carried {
			unstated = "carried from " + shortHead(facts[factTapFrom])
		}
		row.TappedAt, row.TappedBy = orElse(facts[FactTapAt], unstated), orElse(facts[FactTapBy], unstated)
	}
	row.State = stateOf(row, facts)
	row = Decide(row)
	if held.AwaitsTap != "" {
		return row
	}
	row = withStandingWord(row, facts)
	if (row.State == StateTapped || row.State == StateExempt) && facts[bus.StuckFact] != "" {
		row.WaitsOn = strings.TrimSuffix(row.WaitsOn, ".") + "; " + clip(facts[bus.StuckFact], fieldChars)
	}
	if tappedHere || row.SupersededBy != None {
		return row
	}
	return withCarry(row, facts)
}

// TODO: delete once every drive's store has folded once; it clears the landed and closed exempt heads written as tapped, which no later fold rewrites.
func withoutExemptTap(row store.ReviewRow, facts map[string]string) store.ReviewRow {
	if _, exempt := facts["exempt"]; !exempt || machine.Tap(facts["tap"]) == machine.TapTapped || facts[factCarriedBy] != "" {
		return row
	}
	row.Tapped, row.TappedAt, row.TappedBy = false, NotYet, NotYet
	return row
}

func withStandingWord(row store.ReviewRow, facts map[string]string) store.ReviewRow {
	if !row.ReadyForTap || row.Tapped {
		return row
	}
	if Exempted(row, facts) {
		row.ReadyForTap, row.WaitsOn = false, gates.ExemptWaits(facts["exempt"])
		return row
	}
	class, refused := gates.LandsOnWord(facts)
	switch {
	case refused != "":
		row.WaitsOn = strings.TrimSuffix(row.WaitsOn, ".") + ": " + strings.TrimSuffix(clip(gates.RefusalWords(refused), fieldChars), ".") + "."
	case class == gates.FlakeFixClass:
		row.ReadyForTap, row.WaitsOn = false, FlakeLands
	case class == gates.OldTreePortClass:
		row.ReadyForTap, row.WaitsOn = false, PortLands
	}
	return row
}

func withCarry(row store.ReviewRow, facts map[string]string) store.ReviewRow {
	from := shortHead(facts[factTapFrom])
	said := ""
	switch {
	case facts[factCarriedBy] != "":
		said = fmt.Sprintf("your tap at %s carried to this head: %s", from, gates.CarryWhy(facts[factCarriedBy]))
	case facts[factReturned] != "":
		said = fmt.Sprintf("your tap at %s does not carry to this head: %s", from, facts[factReturned])
	default:
		return row
	}
	row.WaitsOn = strings.TrimSuffix(row.WaitsOn, ".") + "; " + said
	return row
}

func shortHead(head string) string {
	return head[:min(shortSHA, len(head))]
}

func stateOf(row store.ReviewRow, facts map[string]string) string {
	switch {
	case row.SupersededBy != None:
		return StateSuperseded
	case facts[FactDraft] == "yes":
		return StateDraft
	case row.Queue == string(machine.QueueQueued):
		return StateQueued
	case row.Tapped:
		return StateTapped
	case Exempted(row, facts):
		return StateExempt
	}
	return StateOpen
}

func gradeOf(facts map[string]string) (string, string) {
	finder, fidelity := machine.GradesOf(facts)
	overrideFinder, overrideFidelity := machine.OverridesOf(facts)
	graded := machine.Facts{Finder: finder, Fidelity: fidelity, FinderOverride: overrideFinder, FidelityOverride: overrideFidelity}
	switch {
	case !graded.Graded():
		return GradePending, None
	case graded.Cleared():
		return GradePass, None
	}
	var said []string
	for _, grader := range []string{"finder", "fidelity"} {
		said = append(said, orElse(facts[grader+saidSuffix], grader+": "+facts[grader]))
	}
	if waits := facts[machine.FidelityWaitsFact]; waits != "" {
		said[1] = waits
	}
	return GradeFail, strings.Join(said, "; ")
}

func parentOf(row store.ReviewRow, branches map[string]store.ReviewRow, trunk string) (string, string) {
	if row.Base == "" || row.Base == trunk {
		return None, None
	}
	below, known := branches[row.Base]
	if !known {
		return row.Base, ParentUnknown
	}
	name := "#" + strconv.Itoa(below.Number)
	switch below.State {
	case StateLanded:
		return name, ParentLanded
	case StateClosed:
		return name, ParentClosed
	}
	return name, ParentOpen
}

func orElse(value, otherwise string) string {
	if value == "" {
		return otherwise
	}
	return value
}

func valuesOf(attrs map[string]string) string {
	encoded, err := json.Marshal(attrs)
	if err != nil {
		panic(err)
	}
	return string(encoded)
}

func SaidFact(grader string) string {
	return grader + saidSuffix
}

func (f Fold) PostReads(ctx context.Context, snapshot []machine.Facts) error {
	var failed []error
	for _, pr := range snapshot {
		row, known, err := f.Store.ReviewRow(f.Repo, pr.Number)
		if err != nil || !known {
			failed = append(failed, err)
			continue
		}
		attrs := readAttrs(f.Repo, pr)
		facts, err := f.Store.Facts(f.Repo, pr.Number, pr.Head)
		if err != nil {
			failed = append(failed, err)
			continue
		}
		if err := f.steadyMergeable(attrs, pr, facts); err != nil {
			failed = append(failed, err)
			continue
		}
		if facts[factValues] == valuesOf(attrs) && row.Head == pr.Head {
			continue
		}
		var body json.RawMessage
		if attrs["body"] != "" && facts[factBody] != attrs["body"] {
			if body, err = BodyRecord(pr.Body); err != nil {
				failed = append(failed, err)
				continue
			}
		}
		_, err = client.Post(ctx, client.Draft{
			Drive: f.Drive, Lane: readLane, Kind: readKind, Text: titleOr(pr), To: []string{readReader}, Force: true,
			ID:    fmt.Sprintf("%s/%d/%d", readRecord, pr.Number, f.Now().UnixNano()),
			PRs:   []string{fmt.Sprintf("%s#%d", f.Repo, pr.Number)},
			Attrs: attrs,
			Body:  body,
		})
		failed = append(failed, err)
	}
	return errors.Join(failed...)
}

// steadyMergeable keeps a head's last known mergeability until GitHub answers for that head, so dev moving does not empty his tab however long the recompute runs. Landing and stack-enqueue keep reading GitHub's own answer.
func (f Fold) steadyMergeable(attrs map[string]string, pr machine.Facts, facts map[string]string) error {
	since := facts[FactUnknownSince]
	if attrs["mergeable"] != string(machine.MergeableUnknown) {
		if since == "" {
			return nil
		}
		for _, kind := range []string{FactUnknownSince, FactBumps} {
			if _, err := f.Store.PutFact(f.Repo, pr.Number, pr.Head, kind, "", f.Now()); err != nil {
				return err
			}
		}
		return nil
	}
	if since == "" {
		now := f.Now()
		if _, err := f.Store.PutFact(f.Repo, pr.Number, pr.Head, FactUnknownSince, now.Format(time.RFC3339), now); err != nil {
			return err
		}
	}
	if known := facts[factMergeable]; known != "" {
		attrs["mergeable"] = known
	}
	return nil
}

func titleOr(pr machine.Facts) string {
	if pr.Title != "" {
		return pr.Title
	}
	return fmt.Sprintf("#%d", pr.Number)
}

func readAttrs(repo string, pr machine.Facts) map[string]string {
	state := StateOpen
	switch {
	case pr.Merged:
		state = stateMerged
	case pr.Closed:
		state = StateClosed
	}
	mergeable := pr.Mergeable
	if mergeable == machine.MergeableClean && len(pr.Carries) > 0 {
		mergeable = machine.MergeableBehind
	}
	attrs := map[string]string{
		"record": readRecord, "repo": repo, "head": pr.Head, "state": state, "base": pr.Base, "branch": pr.Branch,
		"ci": ciWords(pr.CI), "approvers": orElse(strings.Join(pr.Approvers, ", "), None), "mergeable": string(mergeable), "queue": string(pr.Queue),
	}
	if len(pr.Failing) > 0 {
		attrs["failing"] = strings.ReplaceAll(strings.Join(pr.Failing, "; "), "=", " ")
	}
	if pr.Draft {
		attrs["draft"] = "yes"
	}
	if pr.ForeignAuthor != "" {
		attrs["foreign"] = pr.ForeignAuthor
	}
	if pr.TapFrom != "" {
		attrs[tapFromAttr] = pr.TapFrom
	}
	if pr.CarriedBy != "" {
		attrs[carriedByAttr] = pr.CarriedBy
	}
	if pr.NextTree != "" {
		attrs[nextTreeAttr] = pr.NextTree
	}
	if pr.OldTree != "" {
		attrs[oldTreeAttr] = pr.OldTree
	}
	if pr.TapReturned != "" {
		attrs[tapReturnedAttr] = pr.TapReturned
	}
	if pr.Body != "" {
		sum := sha256.Sum256([]byte(pr.Body))
		attrs["body"] = hex.EncodeToString(sum[:8])
	}
	return attrs
}

func ciWords(ci machine.CI) string {
	switch ci {
	case machine.CISuccess:
		return CIGreen
	case machine.CIFailure:
		return CIRed
	}
	return CIPending
}

func CIFromWords(words string) machine.CI {
	switch words {
	case CIGreen:
		return machine.CISuccess
	case CIRed:
		return machine.CIFailure
	}
	return machine.CIPending
}
