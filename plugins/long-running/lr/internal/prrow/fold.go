package prrow

import (
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
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
	unknownGrace          = 10 * time.Minute
	portInteractsUnstated = "unstated"
	headGrace             = 5 * time.Minute
)

const (
	FactWithdrawn = "withdrawn"
	FactFailing   = "read-failing"
	FactDraft     = "read-draft"
	FactForeign   = "read-foreign"
	FactTapBy     = "tap-by"
	FactTapAt     = "tap-at"

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
	carriedByAttr   = "carried_by"
	tapFromAttr     = "tap_from"
	tapReturnedAttr = "tap_returned"
	notRecorded     = "not recorded"
)

var (
	foldKinds = []string{"opened", "head", "lr.head", readKind, "duplicate"}
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
		for _, record := range records {
			if record.Seq <= position {
				continue
			}
			if err := f.apply(t, rows, record); err != nil {
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
			if !Terminal(row.State) {
				row = Materialize(row, atHeads[number], held[number], branches, f.Trunk)
			}
			row.WrittenAt = store.Stamp(written)
			rows[number] = row
		}
		out = values(rows)
		return t.PutReviewRows(out)
	})
	return out, err
}

func values(rows map[int]store.ReviewRow) []store.ReviewRow {
	out := make([]store.ReviewRow, 0, len(rows))
	for _, row := range rows {
		out = append(out, row)
	}
	slices.SortFunc(out, func(a, b store.ReviewRow) int { return a.Number - b.Number })
	return out
}

func (f Fold) apply(t *store.Tx, rows map[int]store.ReviewRow, record client.Record) error {
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
		factCarriedBy: attrs[carriedByAttr], factTapFrom: attrs[tapFromAttr], factReturned: attrs[tapReturnedAttr],
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
	switch attrs["state"] {
	case stateMerged:
		row.State, row.LandedAt = StateLanded, store.Stamp(record.At)
	case StateClosed:
		row.State = StateClosed
	default:
		if Terminal(row.State) {
			row.State = StateOpen
		}
	}
	if Terminal(row.State) {
		row = Decide(row)
	}
	return row, nil
}

func sameHead(a, b string) bool {
	return a != "" && b != "" && (strings.HasPrefix(a, b) || strings.HasPrefix(b, a))
}

func longer(a, b string) string {
	if len(b) > len(a) {
		return b
	}
	return a
}

func Materialize(row store.ReviewRow, facts map[string]string, held Held, branches map[string]store.ReviewRow, trunk string) store.ReviewRow {
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
	row.PlanRow = planRow(row.PlanLine, machine.Fidelity(facts["fidelity"]))
	row.Hold, row.AwaitsTap = orElse(held.Why, None), held.AwaitsTap
	row.SupersededBy = orElse(facts[FactWithdrawn], None)
	row.Parent, row.ParentState = parentOf(row, branches, trunk)
	_, exempt := facts["exempt"]
	tappedHere := machine.Tap(facts["tap"]) == machine.TapTapped
	carried := facts[factCarriedBy] != "" && !tappedHere
	row.Tapped = (tappedHere || exempt || carried) && held.AwaitsTap == ""
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
	if tappedHere || row.SupersededBy != None {
		return row
	}
	return withCarry(row, facts)
}

func withStandingWord(row store.ReviewRow, facts map[string]string) store.ReviewRow {
	if !row.ReadyForTap || row.Tapped {
		return row
	}
	class, refused := gates.LandsOnWord(facts)
	switch {
	case refused != "":
		row.WaitsOn = strings.TrimSuffix(row.WaitsOn, ".") + "; the gate refused it: " + clip(refused, fieldChars)
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
	}
	return StateOpen
}

func gradeOf(facts map[string]string) (string, string) {
	_, verdict := facts["verdict"]
	finder, fidelity := machine.GradesOf(facts)
	graded := machine.Facts{Finder: finder, Fidelity: fidelity, Verdict: verdict}
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
		if facts[factValues] == valuesOf(attrs) && sameHead(row.Head, pr.Head) {
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

// steadyMergeable keeps a head's last known mergeability through the minutes GitHub recomputes it after its base moves, so one landing does not empty his tab. Landing and stack-enqueue keep reading GitHub's own answer.
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
	began, err := time.Parse(time.RFC3339, since)
	if err != nil {
		began = f.Now()
		if _, err := f.Store.PutFact(f.Repo, pr.Number, pr.Head, FactUnknownSince, began.Format(time.RFC3339), began); err != nil {
			return err
		}
	}
	known := facts[factMergeable]
	if known != "" && known != string(machine.MergeableUnknown) && f.Now().Sub(began) < unknownGrace {
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
