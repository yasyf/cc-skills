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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	portInteractsUnstated = "unstated"
	headGrace             = 5 * time.Minute
	oldOverridesCursor    = "old-overrides"
	oldOverrideWindow     = 14 * 24 * time.Hour
	mislandedCursor       = "mislanded-rows"
	landingDesk           = "supervisor"
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
	pullsDrive  = "github-pr"
	readRecord  = "pr.read"
	trunkRecord = "trunk.read"
	trunkHead   = "trunk-mergeable"
	trunkMoved  = "moved"
	trunkRead   = "read"
	readLane    = "pr-read"
	readReader  = "supervisor"
	stateMerged = "merged"

	factCI        = "read-ci"
	requiredCheck = "required-check:"
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
	readAtAttr      = "read_at"
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
	if err := f.reopenMislanded(ctx); err != nil {
		return nil, err
	}
	records, err := f.tail(ctx, f.Drive, store.ReviewRowsCursor, foldKinds)
	if err != nil {
		return nil, err
	}
	pulls, err := f.tail(ctx, pullsDrive, store.PullRequestsCursor, []string{readKind})
	if err != nil {
		return nil, err
	}
	owned, err := f.Store.Owners()
	if err != nil {
		return nil, err
	}
	parked, err := f.Store.Lanes(store.LaneLaunching, store.LaneDesired)
	if err != nil {
		return nil, err
	}
	blocked := blockedRows(parked)
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
		if err := t.Advance(store.PullRequestsCursor, 0); err != nil {
			return err
		}
		pulled, err := t.Cursor(store.PullRequestsCursor)
		if err != nil {
			return err
		}
		for _, record := range pulls {
			if record.Seq <= pulled {
				continue
			}
			if err := f.follow(t, rows, record); err != nil {
				return err
			}
			if err := t.Advance(store.PullRequestsCursor, record.Seq); err != nil {
				return err
			}
		}
		for lane, numbers := range owned {
			for _, number := range numbers {
				if row, known := rows[number]; known {
					row.Lane = lane
					rows[number] = row
				}
			}
		}
		folded := values(rows)
		if err := t.PutReviewRows(folded); err != nil {
			return err
		}
		atHeads, err := t.FactsAtRowHeads(f.Repo, folded)
		if err != nil {
			return err
		}
		branches := make(map[string]store.ReviewRow, len(rows))
		for _, row := range rows {
			if kept, found := branches[row.Branch]; row.Branch != "" && (!found || holdsBranchOver(row, kept)) {
				branches[row.Branch] = row
			}
		}
		trunk, err := t.Facts(f.Repo, 0, trunkHead)
		if err != nil {
			return err
		}
		written := f.Now()
		for number, row := range rows {
			if Terminal(row.State) {
				row = withoutExemptTap(row, atHeads[number])
			} else {
				row = Materialize(row, atHeads[number], held[number], branches, f.Trunk)
				if waits := blocked[number]; waits != "" && !row.ReadyForTap && row.Hold == None {
					row.WaitsOn = waits
				}
				if row.ReadyForTap && trunk[trunkMoved] != trunk[trunkRead] {
					row.ReadyForTap, row.MergesAt = false, ""
					row.WaitsOn = fmt.Sprintf("dev moved to %s, and no host has read mergeability since", shortHead(trunk[trunkMoved]))
				}
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
			if source, said := cardtext.PortSource(row.Description); row.Port == None && said {
				row.Port = PortOf + strconv.Itoa(source)
			}
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

func blockedRows(lanes []store.Lane) map[int]string {
	blocked := map[int]string{}
	for _, lane := range lanes {
		blockers := lane.Blockers()
		if lane.Waiting == 0 || len(blockers) == 0 {
			continue
		}
		named := make([]string, len(blockers))
		for i, number := range blockers {
			named[i] = fmt.Sprintf("#%d", number)
		}
		for _, number := range lane.AwaitedPRs() {
			if slices.Contains(lane.PRs, number) {
				blocked[number] = "waits on " + strings.Join(named, " and ")
			}
		}
	}
	return blocked
}

func IsRead(kind, record string) bool {
	return kind == readKind && record == readRecord
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

// TODO: delete once every drive's store has advanced this cursor; it reopens the rows a landed record from a lane that neither lands nor owns the PR closed out before landsRow refused such records.
func (f Fold) reopenMislanded(ctx context.Context) error {
	read, err := f.Store.Cursor(mislandedCursor)
	if err != nil || read != 0 {
		return err
	}
	records, err := client.Tail(ctx, client.Query{Drive: f.Drive, Kinds: []string{"landed"}, Since: f.Now().Add(-foldWindow)})
	if err != nil {
		return err
	}
	return f.Store.Update(func(t *store.Tx) error {
		stored, err := t.ReviewRows(f.Repo)
		if err != nil {
			return err
		}
		rows := make(map[int]store.ReviewRow, len(stored))
		for _, row := range stored {
			rows[row.Number] = row
		}
		var reopened []store.ReviewRow
		for _, record := range records {
			for _, number := range bus.HeldBy(record, f.Repo) {
				row, known := rows[number]
				if !known || row.State != StateLanded || row.LandedAt != store.Stamp(record.At) || landsRow(record) {
					continue
				}
				row.State, row.LandedAt = StateOpen, NotYet
				reopened = append(reopened, row)
			}
		}
		if err := t.PutReviewRows(reopened); err != nil {
			return err
		}
		return t.Advance(mislandedCursor, 1)
	})
}

func landsRow(record client.Record) bool {
	return client.Stem(record.Lane) == landingDesk
}

func (f Fold) tail(ctx context.Context, drive, cursor string, kinds []string) ([]client.Record, error) {
	after, err := f.Store.Cursor(cursor)
	if err != nil {
		return nil, err
	}
	query := client.Query{Drive: drive, After: after, Kinds: kinds}
	if after == 0 {
		query.Since = f.Now().Add(-foldWindow)
	}
	return client.Tail(ctx, query)
}

func (f Fold) follow(t *store.Tx, rows map[int]store.ReviewRow, record client.Record) error {
	switch record.Topic {
	case "check":
		return f.checked(t, rows, record)
	case "push":
		attrs := record.Fields.Attrs
		if !strings.EqualFold(attrs["repo"], f.Repo) || attrs["branch"] != f.Trunk {
			return nil
		}
		return t.PutFact(f.Repo, 0, trunkHead, trunkMoved, record.Fields.Commit, record.At)
	}
	for _, number := range bus.HeldBy(record, f.Repo) {
		row, known := rows[number]
		if !known {
			continue
		}
		switch record.Fields.Outcome {
		case "opened", "reopened", "synchronize":
			pushed, err := f.pushed(t, row, record, record.Fields.Commit)
			if err != nil {
				return err
			}
			pushed.Lane = row.Lane
			if record.Fields.Outcome == "reopened" && pushed.State == StateClosed {
				pushed.State = StateOpen
			}
			row = pushed
		case "edited":
			if !record.At.After(wordsAt(row)) {
				continue
			}
			if body, carried := bodyOf(record); carried {
				ApplyBody(&row, body)
			}
			row = owesWords(row, record.At)
		case stateMerged:
			row = landed(row, record.At)
		case StateClosed:
			if row.State == StateLanded {
				continue
			}
			row.State = StateClosed
			row = Decide(row)
		default:
			continue
		}
		rows[number] = row
	}
	return nil
}

func (f Fold) checked(t *store.Tx, rows map[int]store.ReviewRow, record client.Record) error {
	attrs := record.Fields.Attrs
	if !strings.EqualFold(attrs["repo"], f.Repo) {
		return nil
	}
	for number, row := range rows {
		if !sameHead(row.Head, record.Fields.Commit) {
			continue
		}
		if err := t.PutFact(f.Repo, number, row.Head, requiredCheck+attrs["check"], record.Fields.Outcome, record.At); err != nil {
			return err
		}
		facts, err := t.Facts(f.Repo, number, row.Head)
		if err != nil {
			return err
		}
		ci, decided := requiredCI(strings.Split(attrs["required"], ","), facts)
		if !decided {
			continue
		}
		if err := t.PutFact(f.Repo, number, row.Head, factCI, ci, record.At); err != nil {
			return err
		}
	}
	return nil
}

func requiredCI(required []string, facts map[string]string) (string, bool) {
	passed, running := 0, false
	for _, check := range required {
		switch facts[requiredCheck+check] {
		case "success", "neutral", "skipped":
			passed++
		case "failure", "error", "cancelled", "timed_out", "action_required", "startup_failure":
			return CIRed, true
		case "pending", "queued", "in_progress":
			running = true
		}
	}
	if running {
		return CIPending, true
	}
	return CIGreen, passed == len(required)
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
	if string(record.Kind) == readKind && attrs["record"] == trunkRecord {
		for _, conflict := range strings.Fields(attrs["conflicts"]) {
			named, head, _ := strings.Cut(conflict, "@")
			number, err := strconv.Atoi(named)
			if err != nil {
				continue
			}
			if err := t.PutFact(f.Repo, number, head, factMergeable, string(machine.MergeableConflict), record.At); err != nil {
				return err
			}
		}
		return t.PutFact(f.Repo, 0, trunkHead, trunkRead, attrs["trunk"], record.At)
	}
	for _, number := range bus.HeldBy(record, f.Repo) {
		row, known := rows[number]
		before := row
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
			if !known || !landsRow(record) {
				continue
			}
			row = landed(row, record.At)
		}
		if err != nil {
			return err
		}
		if changesWords(record, before, row) {
			row = owesWords(row, record.At)
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

const (
	noReview      = "no approving review at this head"
	fullSHALength = 40
)

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
	if record.Kind == "opened" || head != "" && !sameHead(row.Head, head) {
		row.Lane = client.Stem(record.Lane)
	}
	attrs := record.Fields.Attrs
	for field, value := range map[*string]string{&row.Branch: attrs["branch"], &row.Base: attrs["base"], &row.Title: attrs["title"]} {
		if value != "" {
			*field = value
		}
	}
	switch {
	case head == "":
	case len(head) < fullSHALength:
		if err := t.AskRead(f.Repo, row.Number); err != nil {
			return row, err
		}
	default:
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
	from := cmp.Or(was, replaced)
	if closes, err := f.closesLoop(t, number, from, head); err != nil || closes {
		return err
	}
	said := map[string]string{gates.DeltaFromFact: from}
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

func (f Fold) closesLoop(t *store.Tx, number int, from, head string) (bool, error) {
	if from == "" {
		return false, nil
	}
	deltas, err := t.HeadFacts(f.Repo, number, []string{gates.DeltaFromFact})
	return gates.LeadsBack(from, head, deltas), err
}

func BodyRecord(body string) (json.RawMessage, error) {
	return json.Marshal(map[string]string{"body": strings.ToValidUTF8(body[:min(len(body), bodyBytes)], "")})
}

func ReadsNewBody(record client.Record) bool {
	_, carried := bodyOf(record)
	return carried && string(record.Kind) == readKind && record.Fields.Attrs["record"] == readRecord
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
	if !sameHead(row.Head, head) {
		seen, err := t.HeadSeen(f.Repo, row.Number, head, factSeen)
		if err != nil {
			return row, err
		}
		pushed, unstamped := store.Parse(row.HeadAt)
		if !seen || unstamped != nil || record.At.Sub(pushed) >= headGrace {
			closes, err := f.closesLoop(t, row.Number, row.Head, head)
			if err != nil {
				return row, err
			}
			if row.Head != "" && !closes {
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
	row.Base, row.Branch = attrs["base"], attrs["branch"]
	if readAt, _ := time.Parse(time.RFC3339Nano, attrs[readAtAttr]); !readAt.Before(wordsAt(row)) {
		row.Title, row.ReadHead, row.WordsAt = record.Text, head, attrs[readAtAttr]
		if body, carried := bodyOf(record); carried {
			ApplyBody(&row, body)
		}
	}
	landedAt, unstamped := store.Parse(row.LandedAt)
	switch {
	case attrs["state"] == stateMerged:
		return landed(row, record.At), nil
	case row.State == StateLanded && attrs["state"] == StateOpen && unstamped == nil && record.At.Sub(landedAt) >= headGrace:
		row.State, row.LandedAt = StateOpen, NotYet
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

func changesWords(record client.Record, before, after store.ReviewRow) bool {
	switch string(record.Kind) {
	case "opened", "head", "lr.head":
		return record.Fields.Attrs[deltaAttr] == gates.DeltaFundamental || after.Title != before.Title || after.Description != before.Description
	}
	return false
}

func owesWords(row store.ReviewRow, at time.Time) store.ReviewRow {
	if !at.After(wordsAt(row)) {
		return row
	}
	row.WordsAt, row.ReadHead = at.UTC().Format(time.RFC3339Nano), ""
	return row
}

func wordsAt(row store.ReviewRow) time.Time {
	at, _ := time.Parse(time.RFC3339Nano, row.WordsAt)
	return at
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
	row.MergesAt = ""
	if held.AwaitsTap != "" {
		return row
	}
	row = withStandingWord(row, facts)
	if row.ReadyForTap && !row.Tapped && row.State != StateExempt && machine.Tap(facts["tap"]) != machine.TapWithdrawn {
		row.MergesAt = facts[gates.MergesAtFact]
	}
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
	if !graded.FinderCleared() {
		said = append(said, orElse(facts["finder"+saidSuffix], "finder: "+facts["finder"]))
	}
	if !graded.FidelityCleared() {
		said = append(said, orElse(facts[machine.FidelityWaitsFact], orElse(facts["fidelity"+saidSuffix], "fidelity: "+facts["fidelity"])))
	}
	return GradeFail, strings.Join(said, "; ")
}

func holdsBranchOver(row, kept store.ReviewRow) bool {
	if Terminal(row.State) != Terminal(kept.State) {
		return Terminal(kept.State)
	}
	return row.Number > kept.Number
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
	values := maps.Clone(attrs)
	delete(values, readAtAttr)
	encoded, err := json.Marshal(values)
	if err != nil {
		panic(err)
	}
	return string(encoded)
}

func (f Fold) UnreadTrunk() (string, error) {
	trunk, err := f.Store.Facts(f.Repo, 0, trunkHead)
	if err != nil || trunk[trunkMoved] == trunk[trunkRead] {
		return "", err
	}
	return trunk[trunkMoved], nil
}

func (f Fold) PostTrunkRead(ctx context.Context, sha string, conflicts []string) error {
	_, err := client.Post(ctx, client.Draft{
		Drive: f.Drive, Lane: readLane, Kind: readKind, Text: "read every row's mergeability after dev moved to " + shortHead(sha), To: []string{readReader}, Force: true,
		ID:    trunkRecord + "/" + sha,
		Attrs: map[string]string{"record": trunkRecord, "repo": f.Repo, "trunk": sha, "conflicts": strings.Join(conflicts, " ")},
	})
	return err
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
		wordsRead := sameHead(row.ReadHead, pr.Head) && row.Title == titleOr(pr)
		if facts[factValues] == valuesOf(attrs) && row.Head == pr.Head && wordsRead && (row.State != StateLanded || attrs["state"] != StateOpen) {
			continue
		}
		var body json.RawMessage
		if attrs["body"] != "" && (facts[factBody] != attrs["body"] || !wordsRead) {
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
		"record": readRecord, "repo": repo, "head": pr.Head, "state": state, "base": pr.Base, "branch": pr.Branch, readAtAttr: pr.ReadAt.UTC().Format(time.RFC3339Nano),
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
