package prrow

import (
	"context"
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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	FactWithdrawn = "withdrawn"
	FactFailing   = "read-failing"
	FactDraft     = "read-draft"
	FactForeign   = "read-foreign"
	FactTapBy     = "tap-by"
	FactTapAt     = "tap-at"

	cursor      = "review-rows"
	foldWindow  = 72 * time.Hour
	readKind    = "github"
	readRecord  = "pr.read"
	readLane    = "supervisor"
	stateMerged = "merged"

	factCI        = "read-ci"
	factApprovers = "read-approvers"
	factMergeable = "read-mergeable"
	factQueue     = "read-queue"
	factValues    = "read-values"
	factSeen      = "seen"
	saidSuffix    = "-said"
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

func (f Fold) Pass(ctx context.Context, held map[int]string) ([]store.ReviewRow, error) {
	after, err := f.Store.Cursor(cursor)
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
		if err := t.Advance(cursor, 0); err != nil {
			return err
		}
		position, err := t.Cursor(cursor)
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
			if err := t.Advance(cursor, record.Seq); err != nil {
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
			row.Lane = record.Lane
			row, err = f.pushed(t, row, record, attrs["head"])
		case "head":
			if !known {
				continue
			}
			row, err = f.pushed(t, row, record, headOf(record, number))
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

func headOf(record client.Record, number int) string {
	attrs := record.Fields.Attrs
	if named := attrs["head:"+strconv.Itoa(number)]; named != "" {
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
	if at < row.HeadAt {
		return row, nil
	}
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
		FactFailing: attrs["failing"], FactDraft: attrs["draft"], FactForeign: attrs["foreign"], factValues: valuesOf(attrs),
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
		if !seen {
			row.Head, row.HeadAt = head, store.Stamp(record.At)
			if err := t.PutFact(f.Repo, row.Number, head, factSeen, "yes", record.At); err != nil {
				return row, err
			}
		}
	} else {
		row.Head = longer(row.Head, head)
	}
	row.Title, row.Base, row.Branch = record.Text, attrs["base"], attrs["branch"]
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

func Materialize(row store.ReviewRow, facts map[string]string, hold string, branches map[string]store.ReviewRow, trunk string) store.ReviewRow {
	row.CI, row.Approvers = orElse(facts[factCI], CIPending), orElse(facts[factApprovers], None)
	row.Review = noReview
	if row.Approvers != None {
		row.Review = "approved by " + row.Approvers
	}
	row.Mergeable = orElse(facts[factMergeable], string(machine.MergeableUnknown))
	row.Queue = orElse(facts[factQueue], string(machine.QueueNone))
	row.Grade, row.GradeFindings = gradeOf(facts)
	row.PlanRow = planRow(row.PlanLine, machine.Fidelity(facts["fidelity"]))
	row.Hold = orElse(hold, None)
	row.SupersededBy = orElse(facts[FactWithdrawn], None)
	row.Parent, row.ParentState = parentOf(row, branches, trunk)
	_, exempt := facts["exempt"]
	row.Tapped = machine.Tap(facts["tap"]) == machine.TapTapped || exempt
	row.TappedAt, row.TappedBy = NotYet, NotYet
	if row.Tapped {
		row.TappedAt, row.TappedBy = orElse(facts[FactTapAt], "not recorded"), orElse(facts[FactTapBy], "not recorded")
	}
	row.State = stateOf(row, facts)
	return Decide(row)
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
	graded := machine.Facts{Finder: machine.Finder(facts["finder"]), Fidelity: machine.Fidelity(facts["fidelity"]), Verdict: verdict}
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
		if _, known, err := f.Store.ReviewRow(f.Repo, pr.Number); err != nil || !known {
			failed = append(failed, err)
			continue
		}
		attrs := readAttrs(f.Repo, pr)
		facts, err := f.Store.Facts(f.Repo, pr.Number, pr.Head)
		if err != nil {
			failed = append(failed, err)
			continue
		}
		if facts[factValues] == valuesOf(attrs) {
			continue
		}
		_, err = client.Post(ctx, client.Draft{
			Drive: f.Drive, Lane: readLane, Kind: readKind, Text: titleOr(pr),
			ID:    fmt.Sprintf("%s/%d/%d", readRecord, pr.Number, f.Now().UnixNano()),
			PRs:   []string{fmt.Sprintf("%s#%d", f.Repo, pr.Number)},
			Attrs: attrs,
		})
		failed = append(failed, err)
	}
	return errors.Join(failed...)
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
