package facts

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	BusCursor        = "bus"
	lostGrades       = "lost-grades"
	lostGradesSpan   = 14 * 24 * time.Hour
	caller           = "lr-facts"
	trunkFailed      = "trunk-failed"
	trunkCancelled   = "trunk-cancelled"
	trunkMerged      = "trunk-merged"
	memberBatch      = 25
	mergeableFields  = "number headRefOid mergeable"
	queueUnknown     = "queue-unknown"
	graphiteCheck    = "Graphite / mergeability_check"
	readersFirstHeld = "readers-first-held"
	readersFirstWhy  = "readers-first:"
	openHeadsQuery   = `query($owner: String!, $name: String!, $endCursor: String) { repository(owner: $owner, name: $name) { pullRequests(states: OPEN, first: 100, after: $endCursor) { nodes { number headRefOid } pageInfo { hasNextPage endCursor } } } }`
	memberFields     = `number state title isDraft mergedBy { login } headRefOid headRefName baseRefName body author { login } labels(first: 50) { nodes { name } } commits(first: 100) { nodes { commit { oid } } } files(first: 100) { nodes { path } } mergeable mergeStateStatus reviewDecision tip: commits(last: 1) { nodes { commit { statusCheckRollup { state contexts(first: 100) { nodes { __typename ... on CheckRun { name status conclusion startedAt databaseId checkSuite { databaseId workflowRun { workflow { databaseId } } } } ... on StatusContext { context state } } } } } } } reviews(last: 50, states: [APPROVED, CHANGES_REQUESTED]) { nodes { state author { login } commit { oid } } }`
)

var (
	carriedAbove = regexp.MustCompile(`\(PR_NOT_ENQUEUEABLE\): PR #\d+, above this one in the same stack, is already in the merge queue`)
	readersFirst = regexp.MustCompile(`(?mi)^Readers-First:[ \t]*#(\d+)\b`)
)

var trunkQueued = []string{"trunk-queued", "trunk-not-ready", "trunk-testing", "trunk-tests-passed", "trunk-queued (bisection)", "trunk-testing (bisection)", "trunk-tests-passed (bisection)"}

var folding = make(chan struct{}, 1)

type Source struct {
	Repo      string
	Drive     string
	Author    string
	Trunk     string
	Checkout  string
	QueueKey  string
	GoLanes   []string
	Refs      string
	Reads     bool
	ReadsRows bool
	Runner    shell.Runner
	CCX       ccx.Client
	Bus       bus.Bus
	Store     *store.Store
	Log       *slog.Logger
	Now       func() time.Time
}

func New(a *app.App, cfg config.Config, s *store.Store) Source {
	runner := fromCheckout{a.Runner, cfg.Checkout, cfg.Repo}
	return Source{
		Repo:     cfg.Repo,
		Drive:    cfg.Drive,
		Author:   cfg.Author,
		Trunk:    cfg.Trunk,
		Checkout: cfg.Checkout,
		QueueKey: cfg.QueueKey,
		GoLanes:  cfg.GoLaneNames(),
		Refs:     cfg.Refs(),
		Reads:    slices.Contains(cfg.Movers, config.MoverLanding),
		Runner:   runner,
		CCX:      ccx.CLI{Runner: runner},
		Bus:      bus.Bus{Drive: cfg.Drive},
		Store:    s,
		Log:      a.Log,
		Now:      a.Now,
	}
}

type fromCheckout struct {
	shell.Runner
	dir  string
	repo string
}

func (f fromCheckout) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	c.Dir = f.dir
	c.Env = append(c.Env, "GH_REPO="+f.repo, "CCX_CALLER="+caller)
	return f.Runner.Run(ctx, c)
}

func (s Source) Snapshot(ctx context.Context) ([]machine.Facts, error) {
	if err := s.WriteOrders(ctx); err != nil {
		return nil, fmt.Errorf("write his land orders: %w", err)
	}
	held, rows, err := s.foldRecords(ctx)
	if err != nil {
		return nil, err
	}
	if !s.Reads && s.ReadsRows {
		if err := s.readRows(ctx, rows, held); err != nil {
			s.Log.Warn("the read of the open rows failed; the next pass reads again", "error", err)
		}
		return s.fromRows(rows, held)
	}
	if !s.Reads {
		if err := s.readTrunk(ctx, rows); err != nil {
			s.Log.Warn("the read of mergeability after dev moved failed", "error", err)
		}
		return s.fromRows(rows, held)
	}
	if err := s.admitOutside(ctx, rows); err != nil {
		s.Log.Warn("the search for old-tree pull requests no drive lane opened failed", "error", err)
	}
	unread, err := s.Rows().UnreadTrunk()
	if err != nil {
		return nil, err
	}
	snapshot, err := s.readGitHub(ctx, rows, held, s.trunkStatuses)
	if err != nil || unread == "" {
		return snapshot, err
	}
	return snapshot, s.Rows().PostTrunkRead(ctx, unread, nil)
}

func (s Source) readTrunk(ctx context.Context, rows []store.ReviewRow) error {
	unread, err := s.Rows().UnreadTrunk()
	if err != nil || unread == "" {
		return err
	}
	read, err := s.readAliased(ctx, liveNumbers(rows), memberBatch, mergeableFields)
	if err != nil {
		return err
	}
	heads := make(map[int]string, len(rows))
	for _, row := range rows {
		heads[row.Number] = row.Head
	}
	var conflicts []string
	for _, pr := range read {
		if pr != nil && pr.Mergeable == "CONFLICTING" && heads[pr.Number] != "" && strings.HasPrefix(pr.HeadRefOid, heads[pr.Number]) {
			conflicts = append(conflicts, fmt.Sprintf("%d@%s", pr.Number, pr.HeadRefOid))
		}
	}
	slices.Sort(conflicts)
	return s.Rows().PostTrunkRead(ctx, unread, conflicts)
}

func (s Source) readRows(ctx context.Context, rows []store.ReviewRow, held map[int]hold) error {
	unread, err := s.Rows().UnreadTrunk()
	if err != nil {
		return err
	}
	snapshot, err := s.readGitHub(ctx, rows, held, s.graphStatuses)
	if err != nil {
		return err
	}
	if err := s.Rows().PostReads(ctx, snapshot); err != nil || unread == "" {
		return err
	}
	return s.Rows().PostTrunkRead(ctx, unread, nil)
}

func (s Source) ReadOnce(ctx context.Context, named []int) (int, error) {
	held, rows, err := s.foldRecords(ctx)
	if err != nil {
		return 0, err
	}
	itemed, err := s.itemed()
	if err != nil {
		return 0, err
	}
	numbers := named
	if len(numbers) == 0 {
		numbers = readable(rows, itemed)
	}
	snapshot, err := s.readNumbers(ctx, numbers, itemed, rows, held, s.graphStatuses)
	if err != nil {
		return 0, err
	}
	posted := s.Rows().PostReads(ctx, snapshot)
	_, folded := s.Rows().Pass(ctx, heldWords(held))
	return len(snapshot), errors.Join(posted, folded)
}

func (s Source) foldRecords(ctx context.Context) (map[int]hold, []store.ReviewRow, error) {
	select {
	case folding <- struct{}{}:
	case <-ctx.Done():
		return nil, nil, ctx.Err()
	}
	defer func() { <-folding }()
	if err := s.ingestBus(ctx); err != nil {
		return nil, nil, fmt.Errorf("read the bus: %w", err)
	}
	if _, err := s.ingestLegacy(ctx); err != nil {
		return nil, nil, fmt.Errorf("read today's records: %w", err)
	}
	if err := s.readLostGrades(ctx); err != nil {
		return nil, nil, fmt.Errorf("read the grades behind the cursor: %w", err)
	}
	held, err := s.holds(ctx)
	if err != nil {
		return nil, nil, fmt.Errorf("read holds: %w", err)
	}
	rows, err := s.Rows().Pass(ctx, heldWords(held))
	if err != nil {
		return nil, nil, fmt.Errorf("fold the review rows: %w", err)
	}
	return held, rows, nil
}

func (s Source) readGitHub(ctx context.Context, rows []store.ReviewRow, held map[int]hold, statusesOf statusRead) ([]machine.Facts, error) {
	itemed, err := s.itemed()
	if err != nil {
		return nil, err
	}
	return s.readNumbers(ctx, readable(rows, itemed), itemed, rows, held, statusesOf)
}

func readable(rows []store.ReviewRow, itemed map[int]bool) []int {
	numbers := liveNumbers(rows)
	for number := range itemed {
		numbers = append(numbers, number)
	}
	slices.Sort(numbers)
	return slices.Compact(numbers)
}

type statusRead func(ctx context.Context, numbers []int, members memberRead) ([]statusRow, error)

func (s Source) readNumbers(ctx context.Context, numbers []int, itemed map[int]bool, rows []store.ReviewRow, held map[int]hold, statusesOf statusRead) ([]machine.Facts, error) {
	members, err := s.readMembers(ctx, numbers)
	if err != nil {
		return nil, fmt.Errorf("read the drive's pull requests: %w", err)
	}
	open, ended := s.splitEnded(numbers, members, itemed)
	if err := s.Rows().PostReads(ctx, ended); err != nil {
		s.Log.Warn("an ended pull request's read went unposted", "error", err)
	}
	statuses, err := statusesOf(ctx, open, members)
	if err != nil {
		return nil, fmt.Errorf("read queue and CI: %w", err)
	}
	required, err := s.requiredChecks(ctx)
	if err != nil {
		return nil, fmt.Errorf("read required checks: %w", err)
	}
	return s.assemble(ctx, statuses, members, required, held, rows)
}

func (s Source) HeldAndQueued(ctx context.Context, named []int) ([]machine.Facts, error) {
	known, err := s.Store.ReviewRows(s.Repo)
	if err != nil {
		return nil, err
	}
	numbers := slices.Clone(named)
	for _, row := range known {
		if machine.Queue(row.Queue) == machine.QueueQueued && !prrow.Terminal(row.State) {
			numbers = append(numbers, row.Number)
		}
	}
	slices.Sort(numbers)
	numbers = slices.Compact(numbers)
	held, err := s.holds(ctx)
	if err != nil {
		return nil, fmt.Errorf("read holds: %w", err)
	}
	members, err := s.readMembers(ctx, numbers)
	if err != nil {
		return nil, fmt.Errorf("read the held pull requests: %w", err)
	}
	statuses, err := s.statuses(ctx, numbers, members)
	if err != nil {
		return nil, fmt.Errorf("read queue and CI: %w", err)
	}
	required, err := s.requiredChecks(ctx)
	if err != nil {
		return nil, fmt.Errorf("read required checks: %w", err)
	}
	return s.assemble(ctx, statuses, members, required, held, known)
}

func (s Source) Rows() prrow.Fold {
	return prrow.Fold{Drive: s.Drive, Repo: s.Repo, Trunk: s.Trunk, Store: s.Store, Now: s.Now}
}

func (s Source) WriteRows(ctx context.Context, snapshot []machine.Facts) error {
	var posted error
	if s.Reads {
		posted = s.Rows().PostReads(ctx, snapshot)
	}
	held, err := s.holds(ctx)
	if err != nil {
		return errors.Join(posted, err)
	}
	rows, err := s.Rows().Pass(ctx, heldWords(held))
	if err != nil {
		return errors.Join(posted, err)
	}
	readRows(snapshot, rows)
	return posted
}

func (s Source) FoldRows(ctx context.Context) error {
	held, rows, err := s.foldRecords(ctx)
	if err != nil {
		return err
	}
	if err := s.answerReadAsks(ctx, rows, held); err != nil {
		s.Log.Warn("the read a short head asked for failed; the next pass asks again", "error", err)
	}
	return nil
}

func (s Source) answerReadAsks(ctx context.Context, rows []store.ReviewRow, held map[int]hold) error {
	asked, err := s.Store.ReadAsks(s.Repo)
	if err != nil || len(asked) == 0 {
		return err
	}
	itemed, err := s.itemed()
	if err != nil {
		return err
	}
	snapshot, err := s.readNumbers(ctx, asked, itemed, rows, held, s.statuses)
	if err != nil {
		return err
	}
	if err := s.Rows().PostReads(ctx, snapshot); err != nil {
		return err
	}
	return s.Store.AnswerReadAsks(s.Repo, asked)
}

func (s Source) splitEnded(numbers []int, members memberRead, itemed map[int]bool) ([]int, []machine.Facts) {
	open := make([]int, 0, len(numbers))
	var ended []machine.Facts
	for _, number := range numbers {
		pr, read := members.prs[number]
		if itemed[number] || pr.State == "OPEN" || !read && !members.missing[number] {
			open = append(open, number)
			continue
		}
		ended = append(ended, machine.Facts{
			Repo: s.Repo, Number: number, Title: pr.Title, Head: pr.HeadRefOid, Branch: pr.HeadRefName, Base: pr.BaseRefName,
			Closed: true, Merged: pr.State == "MERGED" || pr.labelled(trunkMerged), OldTree: pr.oldTree(), NextTree: pr.nextTree(),
			CI: machine.CIPending, Mergeable: machine.MergeableUnknown, Queue: machine.QueueNone, ReadAt: members.at,
		})
	}
	return open, ended
}

func (s Source) itemed() (map[int]bool, error) {
	items, err := s.Store.OpenItems(s.Repo, 0)
	if err != nil {
		return nil, err
	}
	itemed := make(map[int]bool, len(items))
	for _, item := range items {
		itemed[item.Number] = true
	}
	return itemed, nil
}

func liveNumbers(rows []store.ReviewRow) []int {
	numbers := make([]int, 0, len(rows))
	for _, row := range rows {
		if !prrow.Terminal(row.State) {
			numbers = append(numbers, row.Number)
		}
	}
	return numbers
}

func (s Source) fromRows(rows []store.ReviewRow, held map[int]hold) ([]machine.Facts, error) {
	itemed, err := s.itemed()
	if err != nil {
		return nil, err
	}
	atHeads, err := s.Store.FactsAtRowHeads(s.Repo, rows)
	if err != nil {
		return nil, err
	}
	out := make([]machine.Facts, 0, len(rows))
	for _, row := range rows {
		if row.Head == "" || prrow.Terminal(row.State) && !itemed[row.Number] {
			continue
		}
		f, err := factsOfRow(s.Repo, row, atHeads[row.Number])
		if err != nil {
			return nil, err
		}
		held[row.Number].onto(&f)
		out = append(out, f)
	}
	readRows(out, rows)
	return out, nil
}

func readRows(snapshot []machine.Facts, rows []store.ReviewRow) {
	drawn := make(map[int]store.ReviewRow, len(rows))
	for _, row := range rows {
		drawn[row.Number] = row
	}
	for at := range snapshot {
		row, found := drawn[snapshot[at].Number]
		snapshot[at].RowWait = gates.RowWait(row, found, snapshot[at].Head)
		if row.AwaitsTap != "" && snapshot[at].Hold == "" {
			snapshot[at].Hold, snapshot[at].HoldAwaitsTap = machine.MoverRoot, true
		}
	}
}

func factsOfRow(repo string, row store.ReviewRow, stored map[string]string) (machine.Facts, error) {
	queue := machine.Queue(row.Queue)
	f := machine.Facts{
		Repo: repo, Number: row.Number, Title: row.Title, Head: row.Head, Branch: row.Branch, Base: row.Base,
		ForeignAuthor: stored[prrow.FactForeign], Draft: stored[prrow.FactDraft] == "yes",
		Closed: prrow.Terminal(row.State), Merged: row.State == prrow.StateLanded,
		CI: prrow.CIFromWords(row.CI), Approved: row.Approvers != prrow.None, Mergeable: machine.Mergeable(row.Mergeable), Queue: queue,
		UnsafeWait: row.UnsafeWait,
	}
	if row.Approvers != prrow.None {
		f.Approvers = strings.Split(row.Approvers, ", ")
	}
	if failing := stored[prrow.FactFailing]; failing != "" {
		f.Failing = strings.Split(failing, "; ")
	}
	if queue == machine.QueueQueued {
		f.QueuedHead = row.Head
	}
	err := fromStored(&f, stored, row.Lane)
	if f.Tap == "" && (row.Tapped || prrow.Exempted(row, stored)) {
		f.Tap = machine.TapTapped
	}
	return f, err
}

type ciRead struct {
	State   string   `json:"state"`
	Failing []string `json:"failing"`
	Running int      `json:"running"`
}

type statusRow struct {
	ccx.PRStatus
	CI ciRead `json:"ci"`
}

func (s Source) statuses(ctx context.Context, numbers []int, _ memberRead) ([]statusRow, error) {
	if len(numbers) == 0 {
		return nil, nil
	}
	argv := []string{"ccx", "vcs", "pr", "status", "--repo", s.Repo, "--json"}
	for _, number := range numbers {
		argv = append(argv, strconv.Itoa(number))
	}
	result, err := s.Runner.Run(ctx, shell.Command{Argv: argv})
	if err != nil {
		return nil, err
	}
	var rows []statusRow
	return rows, json.Unmarshal([]byte(result.Stdout), &rows)
}

func (s Source) graphStatuses(_ context.Context, numbers []int, members memberRead) ([]statusRow, error) {
	rows := make([]statusRow, 0, len(numbers))
	for _, number := range numbers {
		pr, read := members.prs[number]
		if !read {
			rows = append(rows, statusRow{PRStatus: ccx.PRStatus{Number: number, Verdict: "unread"}})
			continue
		}
		row := statusRow{PRStatus: ccx.PRStatus{Number: number, Head: pr.HeadRefOid, Base: pr.BaseRefName, State: pr.State, Queue: "unknown", Verdict: pr.verdict()}, CI: pr.ciRead()}
		if pr.ReviewDecision == "APPROVED" {
			row.Approval.State = "approved"
		}
		rows = append(rows, row)
	}
	return rows, nil
}

func (s Source) graphQL(ctx context.Context, query string, flags ...string) (string, error) {
	owner, name, found := strings.Cut(s.Repo, "/")
	if !found {
		return "", fmt.Errorf("repo %q is not owner/name", s.Repo)
	}
	args := append(append([]string{"api", "graphql"}, flags...), "-f", "owner="+owner, "-f", "name="+name, "-f", "query="+query)
	return s.CCX.GH(ctx, ccx.MaxAgeFresh, args...)
}

func (s Source) requiredChecks(ctx context.Context) ([]string, error) {
	out, err := s.CCX.GH(ctx, ccx.MaxAgeCached, "api", "repos/"+s.Repo+"/rules/branches/"+s.Trunk)
	if err != nil {
		return nil, err
	}
	var rules []struct {
		Type       string `json:"type"`
		Parameters struct {
			Checks []struct {
				Context string `json:"context"`
			} `json:"required_status_checks"`
		} `json:"parameters"`
	}
	if err := json.Unmarshal([]byte(out), &rules); err != nil {
		return nil, err
	}
	required := []string{s.queueCheck()}
	for _, rule := range rules {
		if rule.Type == "required_status_checks" {
			for _, check := range rule.Parameters.Checks {
				required = append(required, check.Context)
			}
		}
	}
	return required, nil
}

func (s Source) queueCheck() string {
	return QueueCheck(s.Trunk)
}

func QueueCheck(trunk string) string {
	return "Trunk Merge Queue (" + trunk + ")"
}

func (s Source) ingestBus(ctx context.Context) error {
	since, err := s.Store.Cursor(BusCursor)
	if err != nil {
		return err
	}
	records, _, err := s.Bus.Read(ctx, since, "head", "grade", "tap")
	if err != nil {
		return err
	}
	for _, record := range records {
		if err := s.ingestRecord(record); err != nil {
			return err
		}
		if err := s.Store.Advance(BusCursor, record.Seq); err != nil {
			return err
		}
	}
	return nil
}

func (s Source) ingestRecord(record bus.Record) error {
	var facts []bus.Fact
	switch body := record.Body.(type) {
	case bus.Grade:
		facts = []bus.Fact{{PR: body.PR, Head: body.Head, Kind: body.Grader, Value: body.Verdict}}
	case bus.Tap:
		facts = append(tapFacts(body), bus.Fact{PR: body.PR, Head: body.Head, Kind: prrow.FactTapBy, Value: body.By}, bus.Fact{PR: body.PR, Head: body.Head, Kind: prrow.FactTapAt, Value: store.Stamp(record.At)})
	case bus.Head:
		facts = headFacts(body)
	}
	return s.putFacts(facts, record.At)
}

func headFacts(head bus.Head) []bus.Fact {
	return []bus.Fact{{PR: head.PR, Head: head.Head, Kind: "lane", Value: head.Lane}}
}

func tapFacts(tap bus.Tap) []bus.Fact {
	value := tap.Value
	if tap.Condition != "" {
		value = string(machine.TapConditional)
	}
	facts := []bus.Fact{{PR: tap.PR, Head: tap.Head, Kind: "tap", Value: value}}
	if tap.Condition != "" {
		facts = append(facts, bus.Fact{PR: tap.PR, Head: tap.Head, Kind: "condition", Value: tap.Condition})
	}
	if tap.By == "rule" {
		facts = append(facts, bus.Fact{PR: tap.PR, Head: tap.Head, Kind: "exempt", Value: tap.Rule})
	}
	return facts
}

func (s Source) putFacts(facts []bus.Fact, at time.Time) error {
	for _, fact := range facts {
		if _, err := s.Store.PutFact(s.Repo, fact.PR, fact.Head, fact.Kind, fact.Value, at); err != nil {
			return err
		}
		if fact.Kind != "finder" && fact.Kind != "fidelity" {
			continue
		}
		if err := s.Store.DeleteFactAtHead(s.Repo, fact.PR, fact.Head, machine.VoidFact(fact.Kind)); err != nil {
			return err
		}
	}
	return nil
}

func (s Source) ReadLateGrades(ctx context.Context, snapshot []machine.Facts) ([]machine.Facts, error) {
	select {
	case folding <- struct{}{}:
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	defer func() { <-folding }()
	named, err := s.ingestLegacy(ctx)
	if err != nil {
		return nil, err
	}
	fresh := slices.Clone(snapshot)
	for i := range fresh {
		f := &fresh[i]
		if !slices.Contains(named, f.Number) {
			continue
		}
		stored, err := s.Store.Facts(s.Repo, f.Number, f.Head)
		if err != nil {
			return nil, err
		}
		finder, fidelity := machine.GradesOf(stored)
		f.Finder, f.Fidelity = cmp.Or(f.Finder, finder), cmp.Or(f.Fidelity, fidelity)
		finderOverride, fidelityOverride := machine.OverridesOf(stored)
		f.FinderOverride, f.FidelityOverride = f.FinderOverride || finderOverride, f.FidelityOverride || fidelityOverride
	}
	return fresh, nil
}

func (s Source) ingestLegacy(ctx context.Context) ([]int, error) {
	since, err := s.Store.Cursor(bus.LegacyCursor)
	if err != nil {
		return nil, err
	}
	records, err := s.Bus.ReadLegacy(ctx, since, s.Now())
	if err != nil {
		return nil, err
	}
	var named []int
	for _, record := range records {
		facts, err := s.withoutWithdrawn(bus.LegacyFacts(record, s.Repo, s.GoLanes))
		if err != nil {
			return nil, err
		}
		if err := s.putFacts(append(facts, graderWords(facts, record.Text)...), record.At.UTC()); err != nil {
			return nil, err
		}
		if err := s.clearWithdrawn(ctx, record); err != nil {
			return nil, err
		}
		if err := s.Store.Advance(bus.LegacyCursor, record.Seq); err != nil {
			return nil, err
		}
		for _, fact := range facts {
			named = append(named, fact.PR)
		}
	}
	return named, nil
}

// TODO: delete once every drive's store has advanced this cursor; it reads the fidelity-shard grades the legacy cursor passed before they counted.
func (s Source) readLostGrades(ctx context.Context) error {
	read, err := s.Store.Cursor(lostGrades)
	if err != nil || read != 0 {
		return err
	}
	records, err := client.Tail(ctx, client.Query{Drive: s.Drive, Kinds: []string{"review"}, Since: s.Now().Add(-lostGradesSpan)})
	if err != nil {
		return err
	}
	for _, record := range slices.Backward(records) {
		facts := bus.LegacyFacts(record, s.Repo, s.GoLanes)
		for _, fact := range append(facts, graderWords(facts, record.Text)...) {
			if fact.Kind != "fidelity" && fact.Kind != prrow.SaidFact("fidelity") {
				continue
			}
			stored, err := s.Store.Facts(s.Repo, fact.PR, fact.Head)
			if err != nil {
				return err
			}
			if stored[fact.Kind] != "" {
				continue
			}
			if _, err := s.Store.PutFact(s.Repo, fact.PR, fact.Head, fact.Kind, fact.Value, record.At.UTC()); err != nil {
				return err
			}
		}
	}
	return s.Store.Advance(lostGrades, 1)
}

func (s Source) withoutWithdrawn(facts []bus.Fact) ([]bus.Fact, error) {
	var live []bus.Fact
	for _, fact := range facts {
		if grader, override := strings.CutPrefix(fact.Kind, machine.OverrideFact("")); override {
			stored, err := s.Store.Facts(s.Repo, fact.PR, fact.Head)
			if err != nil {
				return nil, err
			}
			if machine.OverrideWithdrawn(stored, grader, fact.Value) {
				continue
			}
		}
		live = append(live, fact)
	}
	return live, nil
}

func graderWords(facts []bus.Fact, text string) []bus.Fact {
	var said []bus.Fact
	for _, fact := range facts {
		if fact.Kind == "finder" || fact.Kind == "fidelity" {
			said = append(said, bus.Fact{PR: fact.PR, Head: fact.Head, Kind: prrow.SaidFact(fact.Kind), Value: text})
		}
	}
	return said
}

func (s Source) clearWithdrawn(ctx context.Context, record client.Record) error {
	gos := bus.ClosedGos(record, s.GoLanes)
	withdrawn := bus.Withdraws(record)
	if len(gos) == 0 && len(withdrawn) == 0 {
		return nil
	}
	closed, err := client.Among(ctx, s.Drive, slices.Concat(gos, withdrawn))
	if err != nil {
		return err
	}
	for _, closedGo := range closed {
		if !slices.Contains(gos, closedGo.Seq) || !bus.GoCounts(closedGo.Lane, s.GoLanes) {
			continue
		}
		for _, fact := range bus.ExemptClaims(closedGo) {
			if err := s.Store.ClearExempt(s.Repo, fact.PR, record.At.UTC()); err != nil {
				return err
			}
		}
	}
	for _, override := range bus.WithdrawnOverrides(record, closed) {
		if _, err := s.Store.PutFact(s.Repo, override.PR, override.Head, machine.WithdrawnOverrideFact(override.Grader), override.Value(), record.At.UTC()); err != nil {
			return err
		}
	}
	return nil
}

type memberRead struct {
	prs     map[int]pullRequest
	authors map[int]string
	missing map[int]bool
	at      time.Time
}

type label struct {
	Name string `json:"name"`
}

type pullRequest struct {
	Number      int    `json:"number"`
	State       string `json:"state"`
	Title       string `json:"title"`
	IsDraft     bool   `json:"isDraft"`
	HeadRefOid  string `json:"headRefOid"`
	HeadRefName string `json:"headRefName"`
	BaseRefName string `json:"baseRefName"`
	Body        string `json:"body"`
	Author      struct {
		Login string `json:"login"`
	} `json:"author"`
	Labels struct {
		Nodes []label `json:"nodes"`
	} `json:"labels"`
	Commits struct {
		Nodes []struct {
			Commit struct {
				OID string `json:"oid"`
			} `json:"commit"`
		} `json:"nodes"`
	} `json:"commits"`
	Files struct {
		Nodes []struct {
			Path string `json:"path"`
		} `json:"nodes"`
	} `json:"files"`
	Mergeable        string `json:"mergeable"`
	MergeStateStatus string `json:"mergeStateStatus"`
	ReviewDecision   string `json:"reviewDecision"`
	Tip              struct {
		Nodes []struct {
			Commit struct {
				StatusCheckRollup *struct {
					State    string `json:"state"`
					Contexts struct {
						Nodes []checkContext `json:"nodes"`
					} `json:"contexts"`
				} `json:"statusCheckRollup"`
			} `json:"commit"`
		} `json:"nodes"`
	} `json:"tip"`
	Reviews struct {
		Nodes []struct {
			State  string `json:"state"`
			Author struct {
				Login string `json:"login"`
			} `json:"author"`
			Commit struct {
				OID string `json:"oid"`
			} `json:"commit"`
		} `json:"nodes"`
	} `json:"reviews"`
	MergedBy struct {
		Login string `json:"login"`
	} `json:"mergedBy"`
}

func (pr pullRequest) approversAt(head string) []string {
	verdicts := map[string]string{}
	for _, review := range pr.Reviews.Nodes {
		if review.Commit.OID == head {
			verdicts[review.Author.Login] = review.State
		}
	}
	var approvers []string
	for login, verdict := range verdicts {
		if verdict == "APPROVED" {
			approvers = append(approvers, login)
		}
	}
	slices.Sort(approvers)
	return approvers
}

func (pr pullRequest) oldTree() string {
	return gates.OldTreeFile(pr.paths())
}

func (pr pullRequest) nextTree() string {
	return gates.NextTreeFile(pr.paths())
}

func (pr pullRequest) paths() []string {
	paths := make([]string, len(pr.Files.Nodes))
	for at, file := range pr.Files.Nodes {
		paths[at] = file.Path
	}
	return paths
}

func (pr pullRequest) labelled(names ...string) bool {
	return slices.ContainsFunc(pr.Labels.Nodes, func(found label) bool { return slices.Contains(names, found.Name) })
}

func (pr pullRequest) greenAt(head string) bool {
	if pr.HeadRefOid != head || len(pr.Tip.Nodes) == 0 {
		return false
	}
	rollup := pr.Tip.Nodes[0].Commit.StatusCheckRollup
	return rollup != nil && rollup.State == "SUCCESS"
}

func (pr pullRequest) verdict() string {
	switch {
	case pr.Mergeable == "CONFLICTING" || pr.MergeStateStatus == "DIRTY":
		return "blocked:conflict"
	case pr.Mergeable == "UNKNOWN":
		return "blocked:mergeable-unknown"
	}
	return "landable"
}

func (pr pullRequest) ciRead() ciRead {
	if len(pr.Tip.Nodes) == 0 || pr.Tip.Nodes[0].Commit.StatusCheckRollup == nil {
		return ciRead{State: "none"}
	}
	contexts := pr.Tip.Nodes[0].Commit.StatusCheckRollup.Contexts.Nodes
	read, graded := ciRead{}, 0
	for _, c := range contexts {
		if c.label() == graphiteCheck || slices.ContainsFunc(contexts, func(later checkContext) bool { return later.supersedes(c) }) {
			continue
		}
		switch c.outcome() {
		case "SUCCESS", "SKIPPED", "NEUTRAL":
			graded++
		case "QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED":
			read.Running++
		default:
			read.Failing = append(read.Failing, c.label())
		}
	}
	switch {
	case len(read.Failing) > 0:
		read.State = "red"
	case read.Running > 0:
		read.State = "pending"
	case graded > 0:
		read.State = "green"
	default:
		read.State = "none"
	}
	return read
}

type checkContext struct {
	Typename   string    `json:"__typename"`
	Name       string    `json:"name"`
	Status     string    `json:"status"`
	Conclusion string    `json:"conclusion"`
	StartedAt  time.Time `json:"startedAt"`
	DatabaseID int64     `json:"databaseId"`
	CheckSuite struct {
		DatabaseID  int64 `json:"databaseId"`
		WorkflowRun struct {
			Workflow struct {
				DatabaseID int64 `json:"databaseId"`
			} `json:"workflow"`
		} `json:"workflowRun"`
	} `json:"checkSuite"`
	Context string `json:"context"`
	State   string `json:"state"`
}

func (c checkContext) label() string {
	if c.Typename == "CheckRun" {
		return c.Name
	}
	return c.Context
}

func (c checkContext) outcome() string {
	if c.Typename != "CheckRun" {
		return c.State
	}
	return cmp.Or(c.Conclusion, c.Status)
}

func (c checkContext) supersedes(prior checkContext) bool {
	if c.Typename != "CheckRun" || prior.Typename != "CheckRun" || c.Name != prior.Name {
		return false
	}
	sameSuite := c.CheckSuite.DatabaseID == prior.CheckSuite.DatabaseID
	sameWorkflow := c.CheckSuite.WorkflowRun.Workflow.DatabaseID == prior.CheckSuite.WorkflowRun.Workflow.DatabaseID
	return (sameSuite || sameWorkflow && prior.Conclusion == "CANCELLED") && c.replaces(prior)
}

func (c checkContext) replaces(prior checkContext) bool {
	if skipped, priorSkipped := c.Conclusion == "SKIPPED", prior.Conclusion == "SKIPPED"; skipped != priorSkipped {
		return priorSkipped
	}
	if !c.StartedAt.Equal(prior.StartedAt) {
		return c.StartedAt.IsZero() || !prior.StartedAt.IsZero() && c.StartedAt.After(prior.StartedAt)
	}
	return c.DatabaseID > prior.DatabaseID
}

func (pr pullRequest) mergeableAt(head string) bool {
	return pr.HeadRefOid == head && pr.Mergeable == "MERGEABLE" && pr.MergeStateStatus != "DIRTY"
}

type graphError struct {
	Type string `json:"type"`
}

func (s Source) readMembers(ctx context.Context, numbers []int) (memberRead, error) {
	at := s.Now()
	repository, err := s.readAliased(ctx, numbers, memberBatch, memberFields)
	if err != nil {
		return memberRead{}, err
	}
	prs := make([]pullRequest, 0, len(numbers))
	missing := make(map[int]bool)
	for alias, pr := range repository {
		if pr != nil {
			prs = append(prs, *pr)
			continue
		}
		number, err := strconv.Atoi(strings.TrimPrefix(alias, "r"))
		if err != nil {
			return memberRead{}, err
		}
		missing[number] = true
	}
	authors := make(map[int]string, len(prs))
	byNumber := make(map[int]pullRequest, len(prs))
	for _, pr := range prs {
		authors[pr.Number] = pr.Author.Login
		byNumber[pr.Number] = pr
	}
	return memberRead{byNumber, authors, missing, at}, nil
}

func (s Source) ReadMergeable(ctx context.Context, numbers []int) (map[int]string, error) {
	members, err := s.readMembers(ctx, numbers)
	if err != nil {
		return nil, err
	}
	read := make(map[int]string, len(members.prs))
	for number, pr := range members.prs {
		read[number] = fmt.Sprintf("%s %s at %s", pr.Mergeable, pr.MergeStateStatus, pr.HeadRefOid)
	}
	return read, nil
}

func (s Source) readAliased(ctx context.Context, numbers []int, batch int, fields string) (map[string]*pullRequest, error) {
	repository := make(map[string]*pullRequest, len(numbers))
	for chunk := range slices.Chunk(numbers, batch) {
		out, err := s.graphQL(ctx, aliasedQuery(chunk, fields))
		var reply struct {
			Data struct {
				Repository map[string]*pullRequest `json:"repository"`
			} `json:"data"`
			Errors []graphError `json:"errors"`
		}
		decoded := json.Unmarshal([]byte(out), &reply)
		notFound := len(reply.Errors) > 0 && !slices.ContainsFunc(reply.Errors, func(one graphError) bool { return one.Type != "NOT_FOUND" })
		if err != nil && !notFound {
			return nil, err
		}
		if decoded != nil {
			return nil, decoded
		}
		maps.Copy(repository, reply.Data.Repository)
	}
	return repository, nil
}

func aliasedQuery(numbers []int, fields string) string {
	var aliases strings.Builder
	for _, number := range numbers {
		fmt.Fprintf(&aliases, "r%d: pullRequest(number: %d) { %s } ", number, number, fields)
	}
	return "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { " + aliases.String() + "} }"
}

func (s Source) openHeads(ctx context.Context) (map[string]int, error) {
	out, err := s.graphQL(ctx, openHeadsQuery, "--paginate", "--slurp")
	if err != nil {
		return nil, err
	}
	var pages []struct {
		Data struct {
			Repository struct {
				PullRequests struct {
					Nodes []pullRequest `json:"nodes"`
				} `json:"pullRequests"`
			} `json:"repository"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(out), &pages); err != nil {
		return nil, err
	}
	heads := make(map[string]int)
	for _, page := range pages {
		for _, pr := range page.Data.Repository.PullRequests.Nodes {
			heads[pr.HeadRefOid] = pr.Number
		}
	}
	return heads, nil
}

func (s Source) askReadOfReopened(rows []store.ReviewRow, open map[string]int) error {
	reopened := make(map[int]bool, len(open))
	for _, number := range open {
		reopened[number] = true
	}
	return s.Store.Update(func(t *store.Tx) error {
		for _, row := range rows {
			if row.State != prrow.StateClosed || !reopened[row.Number] {
				continue
			}
			if err := t.AskRead(s.Repo, row.Number); err != nil {
				return err
			}
		}
		return nil
	})
}

func carried(prs map[int]pullRequest, open map[string]int) map[int][]int {
	byHead := maps.Clone(open)
	for _, pr := range prs {
		byHead[pr.HeadRefOid] = pr.Number
	}
	carries := make(map[int][]int)
	for _, pr := range prs {
		for _, node := range pr.Commits.Nodes {
			if other, ok := byHead[node.Commit.OID]; ok && other != pr.Number {
				carries[pr.Number] = append(carries[pr.Number], other)
			}
		}
		slices.Sort(carries[pr.Number])
	}
	return carries
}

type hold struct {
	by         machine.Mover
	record     string
	why        string
	asks       []string
	asksRecord string
}

func (h hold) onto(f *machine.Facts) {
	f.Hold, f.HoldRecord = h.by, h.record
	if h.by == "" && len(h.asks) > 0 {
		f.Hold, f.HoldRecord, f.HoldAwaitsTap = machine.MoverRoot, h.asksRecord, true
	}
}

var (
	holdOpening = regexp.MustCompile(`(?i)^\s*hold\s+#?\d+\s*[:,-]?\s*`)
	planNamed   = regexp.MustCompile(`(?i)\bplan (?:row|step|\d)`)
)

func holdSaid(record client.Record) string {
	line, _, _ := strings.Cut(record.Text, "\n")
	return fmt.Sprintf("%s (record %s)", holdOpening.ReplaceAllString(line, ""), record.Name())
}

func holdWhy(record client.Record, by machine.Mover) string {
	line, _, _ := strings.Cut(record.Text, "\n")
	why := holdSaid(record)
	switch {
	case by == machine.MoverOwner:
		return "owner:" + why
	case strings.Contains(strings.ToLower(record.Lane+" "+line), "cutover"):
		return "cutover:" + why
	case planNamed.MatchString(line):
		return "plan:" + why
	}
	return "root:" + why
}

func heldWords(held map[int]hold) map[int]prrow.Held {
	words := make(map[int]prrow.Held, len(held))
	for number, one := range held {
		words[number] = prrow.Held{Why: one.why, AwaitsTap: strings.Join(one.asks, "; ")}
	}
	return words
}

func (s Source) holds(ctx context.Context) (map[int]hold, error) {
	records, err := client.Open(ctx, s.Drive, "hold")
	if err != nil || len(records) == 0 {
		return nil, err
	}
	first := slices.MinFunc(records, func(a, b client.Record) int { return a.At.Compare(b.At) })
	later, err := client.Tail(ctx, client.Query{Drive: s.Drive, Since: first.At, Kinds: bus.GoReadKinds})
	if err != nil {
		return nil, err
	}
	gos := bus.RootGos(later)
	lifts, err := bus.HoldLifts(records, func(seqs []int64) ([]client.Record, error) { return client.Among(ctx, s.Drive, seqs) })
	if err != nil {
		return nil, fmt.Errorf("read what the holds resolve: %w", err)
	}
	held := make(map[int]hold)
	for _, record := range records {
		if lifts[record.Seq] {
			continue
		}
		byOwner := record.Lane == string(machine.MoverOwner)
		for _, pr := range bus.HeldBy(record, s.Repo) {
			one := held[pr]
			switch {
			case bus.HoldsUntilTap(record):
				one.asks, one.asksRecord = append(one.asks, holdSaid(record)), record.Name()
			case byOwner:
				one.by, one.record, one.why = machine.MoverOwner, record.Name(), holdWhy(record, machine.MoverOwner)
			case (strings.HasPrefix(record.Text, readersFirstWhy) || !bus.GoLifts(gos, record, pr)) && one.by != machine.MoverOwner:
				one.by, one.record, one.why = machine.MoverRoot, record.Name(), holdWhy(record, machine.MoverRoot)
			}
			held[pr] = one
		}
	}
	return held, nil
}

func (s Source) holdReadersFirst(ctx context.Context, f machine.Facts, stored map[string]string) (bool, error) {
	named := readersFirst.FindAllStringSubmatch(f.Body, -1)
	readers := make([]string, 0, len(named))
	for _, reader := range named {
		readers = append(readers, reader[1])
	}
	if f.Closed || len(readers) == 0 || stored[readersFirstHeld] == strings.Join(readers, ",") {
		return false, nil
	}
	for _, reader := range readers {
		if _, err := client.Post(ctx, client.Draft{
			Drive: s.Drive, Lane: bus.SweepLane, Kind: "hold", ID: fmt.Sprintf("readers-first/%d/%s", f.Number, reader), PRs: []string{strconv.Itoa(f.Number)},
			Text: fmt.Sprintf("%s #%s not live on every client. Only the coordinator lifts this, naming the live release that carries #%s.", readersFirstWhy, reader, reader),
		}); err != nil {
			return false, err
		}
	}
	_, err := s.Store.PutFact(s.Repo, f.Number, f.Head, readersFirstHeld, strings.Join(readers, ","), s.Now())
	return true, err
}

func (s Source) HeldPRs(ctx context.Context) (map[int]bool, error) {
	held, err := s.holds(ctx)
	if err != nil {
		return nil, err
	}
	numbers := make(map[int]bool, len(held))
	for number, one := range held {
		numbers[number] = one.by != "" || len(one.asks) > 0
	}
	return numbers, nil
}

func (s Source) assemble(ctx context.Context, rows []statusRow, open memberRead, required []string, held map[int]hold, known []store.ReviewRow) ([]machine.Facts, error) {
	if len(rows) == 0 {
		return nil, nil
	}
	numbers := make([]int, 0, len(rows))
	for _, row := range rows {
		numbers = append(numbers, row.Number)
	}
	owners, err := s.Store.OwnerOf(numbers)
	if err != nil {
		return nil, err
	}
	rowLanes := make(map[int]string, len(known))
	for _, row := range known {
		rowLanes[row.Number] = row.Lane
	}
	enqueuedAt, err := s.Store.FactsOfKind(s.Repo, "enqueued")
	if err != nil {
		return nil, err
	}
	enqueued := make(map[int]bool, len(enqueuedAt))
	for key := range enqueuedAt {
		enqueued[key.Number] = true
	}
	heads, err := s.openHeads(ctx)
	if err != nil {
		return nil, fmt.Errorf("read the open pull requests' heads: %w", err)
	}
	if err := s.askReadOfReopened(known, heads); err != nil {
		return nil, err
	}
	carries := carried(open.prs, heads)
	out := make([]machine.Facts, 0, len(rows))
	var diffUnread []machine.Facts
	newlyHeld := map[int]bool{}
	for _, row := range rows {
		if row.State == "" {
			s.Log.Warn("pull request unread this pass", "pr", row.Number, "verdict", row.Verdict)
			continue
		}
		stored, err := s.Store.Facts(s.Repo, row.Number, row.Head)
		if err != nil {
			return nil, err
		}
		f, err := s.factsOf(row, open.prs[row.Number], required, stored, owners[row.Number])
		if err != nil {
			return nil, err
		}
		f.Lane = cmp.Or(f.Lane, rowLanes[row.Number])
		f.ReadAt = open.at
		f.PersonQueued = f.Queue == machine.QueueQueued && !enqueued[f.Number]
		held[row.Number].onto(&f)
		posted, err := s.holdReadersFirst(ctx, f, stored)
		if err != nil {
			return nil, fmt.Errorf("#%d readers-first hold: %w", row.Number, err)
		}
		if posted {
			newlyHeld[f.Number] = true
		}
		f.Carries = carries[row.Number]
		if author := open.authors[row.Number]; author != s.Author {
			f.ForeignAuthor = author
		}
		if err := s.carry(ctx, &f, stored); err != nil {
			if !errors.Is(err, errDiffUnread) || ctx.Err() != nil {
				return nil, fmt.Errorf("#%d carry: %w", row.Number, err)
			}
			s.Log.Warn("pull request unread this pass", "pr", row.Number, "err", err)
			diffUnread = append(diffUnread, f)
			continue
		}
		out = append(out, f)
	}
	if err := s.Rows().PostReads(ctx, diffUnread); err != nil {
		s.Log.Warn("a pull request whose diff went unread had its read unposted", "error", err)
	}
	if len(newlyHeld) > 0 {
		if held, err = s.holds(ctx); err != nil {
			return nil, fmt.Errorf("read the readers-first holds: %w", err)
		}
		for i := range out {
			if newlyHeld[out[i].Number] {
				held[out[i].Number].onto(&out[i])
			}
		}
	}
	queuedByTheDrive(out, enqueued)
	readRows(out, known)
	return out, nil
}

func queuedByTheDrive(snapshot []machine.Facts, enqueued map[int]bool) {
	byBranch := make(map[string]machine.Facts, len(snapshot))
	for _, f := range snapshot {
		byBranch[f.Branch] = f
	}
	for i := range snapshot {
		f := snapshot[i]
		for range len(snapshot) {
			below, open := byBranch[f.Base]
			if !open || !snapshot[i].PersonQueued {
				break
			}
			snapshot[i].PersonQueued, f = !enqueued[below.Number], below
		}
	}
}

func (s Source) carry(ctx context.Context, f *machine.Facts, stored map[string]string) error {
	if f.Closed {
		return nil
	}
	var tapped, goHead string
	var err error
	switch f.Tap {
	case machine.TapTapped:
		tapped = f.Head
	case "":
		if tapped, err = s.Store.StandingTap(s.Repo, f.Number); err != nil {
			return err
		}
	}
	if f.Exempt {
		goHead = f.Head
	} else if goHead, err = s.Store.StandingGo(s.Repo, f.Number); err != nil {
		return err
	}
	overrides, err := s.Store.HeadFacts(s.Repo, f.Number, gates.OverrideFacts)
	if err != nil {
		return err
	}
	claims, err := s.Store.HeadFacts(s.Repo, f.Number, []string{gates.PortOfAttr, gates.FlakeFixAttr})
	if err != nil {
		return err
	}
	shown, err := s.Store.HeadFacts(s.Repo, f.Number, []string{gates.ShownFact})
	if err != nil {
		return err
	}
	if tapped == "" && goHead == "" && len(overrides) == 0 && len(claims) == 0 && len(shown) == 0 {
		return nil
	}
	if err := s.hashDiff(ctx, *f, stored); err != nil {
		return err
	}
	if tapped != "" && tapped != f.Head {
		atTap, err := s.Store.Facts(s.Repo, f.Number, tapped)
		if err != nil {
			return err
		}
		deltas, err := s.Store.HeadFacts(s.Repo, f.Number, gates.DeltaFacts)
		if err != nil {
			return err
		}
		f.TapFrom = tapped
		f.CarriedBy, f.TapReturned = gates.CarriedBy(tapped, f.Head, atTap, stored, deltas)
		if f.CarriedBy != "" {
			f.Tap = machine.TapTapped
		}
	}
	if goHead != "" && goHead != f.Head {
		atGo, err := s.Store.Facts(s.Repo, f.Number, goHead)
		if err != nil {
			return err
		}
		f.Exempt = gates.SameDiff(atGo, stored) && gates.TaplessClass(atGo["exempt"])
	}
	if err := s.carryClaim(ctx, *f, stored, claims); err != nil {
		return err
	}
	if err := s.carryShown(*f, stored, shown); err != nil {
		return err
	}
	return s.carryOverrides(ctx, *f, stored, overrides)
}

func (s Source) carryShown(f machine.Facts, stored map[string]string, shown []store.HeadFact) error {
	if len(shown) == 0 || stored[gates.ShownFact] != "" {
		return nil
	}
	from := shown[len(shown)-1]
	atFrom, err := s.Store.Facts(s.Repo, f.Number, from.Head)
	if err != nil {
		return err
	}
	deltas, err := s.Store.HeadFacts(s.Repo, f.Number, gates.DeltaFacts)
	if err != nil {
		return err
	}
	if by, _ := gates.CarriedBy(from.Head, f.Head, atFrom, stored, deltas); by == "" {
		return nil
	}
	stored[gates.ShownFact] = from.Value
	_, err = s.Store.PutFact(s.Repo, f.Number, f.Head, gates.ShownFact, from.Value, s.Now())
	return err
}

func (s Source) carryClaim(ctx context.Context, f machine.Facts, stored map[string]string, claims []store.HeadFact) error {
	for _, from := range gates.OverrideHeads(claims, f.Head) {
		atFrom, err := s.Store.Facts(s.Repo, f.Number, from)
		if err != nil {
			return err
		}
		attrs, text := gates.CarriedClaim(from, f.Head, atFrom, stored)
		if attrs == nil {
			continue
		}
		_, err = client.Post(ctx, client.Draft{Drive: s.Drive, Lane: bus.SweepLane, Kind: "decide", ID: fmt.Sprintf("claim-carried/%d/%s", f.Number, f.Head), PRs: []string{strconv.Itoa(f.Number)}, Attrs: attrs, Text: text})
		return err
	}
	return nil
}

func (s Source) carryOverrides(ctx context.Context, f machine.Facts, stored map[string]string, overrides []store.HeadFact) error {
	for _, from := range gates.OverrideHeads(overrides, f.Head) {
		atFrom, err := s.Store.Facts(s.Repo, f.Number, from)
		if err != nil {
			return err
		}
		carried := gates.CarriedOverrides(atFrom, stored)
		for _, grader := range carried {
			text := gates.CarriedOverride(f.Number, f.Head, from, grader, atFrom[machine.OverrideFact(grader)])
			if _, err := client.Post(ctx, client.Draft{Drive: s.Drive, Lane: bus.SweepLane, Kind: "decide", PRs: []string{strconv.Itoa(f.Number)}, Text: text}); err != nil {
				return err
			}
		}
		if len(carried) > 0 {
			return nil
		}
	}
	return nil
}

var errDiffUnread = errors.New("diff unread")

func (s Source) hashDiff(ctx context.Context, f machine.Facts, stored map[string]string) error {
	if stored[gates.DiffFact] != "" {
		return nil
	}
	fetch, below := []string{"git", "fetch", "--no-write-fetch-head", "--refmap=", "-q", "origin", f.Head}, []string{"git", "merge-base", f.Head}
	for _, branch := range slices.Compact([]string{f.Base, s.Trunk}) {
		ref := s.Refs + "diff/" + branch
		fetch, below = append(fetch, "+refs/heads/"+branch+":"+ref), append(below, ref)
	}
	if _, err := s.Runner.Run(ctx, shell.Command{Argv: fetch}); err != nil {
		return fmt.Errorf("%w: %w", errDiffUnread, err)
	}
	base, err := s.Runner.Run(ctx, shell.Command{Argv: below})
	if err != nil {
		return fmt.Errorf("%w: %w", errDiffUnread, err)
	}
	diff, err := s.Runner.Run(ctx, shell.Command{Argv: []string{"git", "diff", "--no-color", "--no-ext-diff", strings.TrimSpace(base.Stdout), f.Head}})
	if err != nil {
		return fmt.Errorf("%w: %w", errDiffUnread, err)
	}
	stored[gates.DiffFact] = gates.DiffHash(diff.Stdout)
	_, err = s.Store.PutFact(s.Repo, f.Number, f.Head, gates.DiffFact, stored[gates.DiffFact], s.Now())
	return err
}

func (s Source) factsOf(row statusRow, pr pullRequest, required []string, stored map[string]string, owner string) (machine.Facts, error) {
	ci := s.ciOf(row, pr, required)
	mergeable := mergeableOf(row, pr)
	queue, err := queueOf(row, pr, ci)
	if err != nil {
		return machine.Facts{}, err
	}
	if s.queueRunRetries(row, ci, stored) {
		ci, queue = machine.CISuccess, machine.QueueEvicted
	}
	if queue == machine.QueueNone && carriedAbove.MatchString(stored["enqueued"]) {
		queue = machine.QueueQueued
	}
	f := machine.Facts{
		Repo:       s.Repo,
		Number:     row.Number,
		Title:      pr.Title,
		Head:       row.Head,
		Branch:     pr.HeadRefName,
		Base:       row.Base,
		Draft:      pr.IsDraft,
		Closed:     row.State != "OPEN",
		Merged:     row.State == "MERGED" || row.Queue == "landed" || row.Queue == "unknown" && pr.labelled(trunkMerged),
		MergedBy:   pr.MergedBy.Login,
		CI:         ci,
		Failing:    row.CI.Failing,
		Approved:   row.Approval.State == "approved",
		Approvers:  pr.approversAt(row.Head),
		Mergeable:  mergeable,
		Uncomputed: mergeable == machine.MergeableUnknown && !strings.Contains(row.Verdict, queueUnknown),
		Queue:      queue,
		UnsafeWait: gates.UnsafeWait(pr.Body),
		Body:       pr.Body,
		OldTree:    pr.oldTree(),
		NextTree:   pr.nextTree(),
	}
	if queue == machine.QueueQueued {
		f.QueuedHead = row.Head
	}
	err = fromStored(&f, stored, owner)
	return f, err
}

func fromStored(f *machine.Facts, stored map[string]string, owner string) error {
	f.Lane = cmp.Or(owner, stored["lane"])
	resubmitted, err := atoiOrZero(stored["resubmitted"])
	if err != nil {
		return fmt.Errorf("#%d resubmitted: %w", f.Number, err)
	}
	f.Resubmitted = resubmitted
	f.Eviction, f.EvictionOwn = stored[machine.EvictionFact], stored[machine.EvictionOwnFact] != ""
	f.Finder, f.Fidelity = machine.GradesOf(stored)
	f.Tap, f.Condition = machine.Tap(stored["tap"]), stored["condition"]
	f.GradeStopped = stored[machine.GradeStoppedFact]
	f.TapRefused = stored[bus.TapRefusedFact]
	f.Stuck = stored[bus.StuckFact]
	f.FinderOverride, f.FidelityOverride = machine.OverridesOf(stored)
	if f.FinderVoid, err = seqOrZero(stored[machine.VoidFact("finder")]); err != nil {
		return fmt.Errorf("#%d finder void: %w", f.Number, err)
	}
	if f.FidelityVoid, err = seqOrZero(stored[machine.VoidFact("fidelity")]); err != nil {
		return fmt.Errorf("#%d fidelity void: %w", f.Number, err)
	}
	f.Exempt = gates.TaplessClass(stored["exempt"])
	f.Port = stored[gates.PortOfAttr] != ""
	f.StandingWord, _ = gates.LandsOnWord(stored)
	return nil
}

func seqOrZero(text string) (int64, error) {
	if text == "" {
		return 0, nil
	}
	return strconv.ParseInt(text, 10, 64)
}

func atoiOrZero(text string) (int, error) {
	if text == "" {
		return 0, nil
	}
	return strconv.Atoi(text)
}

func (s Source) ciOf(row statusRow, pr pullRequest, required []string) machine.CI {
	switch row.CI.State {
	case "green":
		return machine.CISuccess
	case "red":
		if s.redCounts(row.CI.Failing, pr, required) {
			return machine.CIFailure
		}
		if row.CI.Running > 0 {
			return machine.CIPending
		}
		return machine.CISuccess
	case "pending":
		if pr.greenAt(row.Head) {
			return machine.CISuccess
		}
	}
	return machine.CIPending
}

func (s Source) redCounts(failing []string, pr pullRequest, required []string) bool {
	if len(failing) == 0 {
		return true
	}
	if pr.labelled(trunkCancelled) && !pr.labelled(trunkFailed) {
		required = slices.DeleteFunc(slices.Clone(required), func(check string) bool { return check == s.queueCheck() })
	}
	return slices.ContainsFunc(failing, func(check string) bool { return slices.Contains(required, check) })
}

func (s Source) queueRunRetries(row statusRow, ci machine.CI, stored map[string]string) bool {
	return ci == machine.CIFailure && row.Queue != "queued" && row.CI.Running == 0 && stored["resubmitted"] == "" && slices.Equal(row.CI.Failing, []string{s.queueCheck()})
}

func queueOf(row statusRow, pr pullRequest, ci machine.CI) (machine.Queue, error) {
	fell := ci != machine.CISuccess && pr.labelled(trunkFailed, trunkCancelled)
	switch row.Queue {
	case "queued":
		return machine.QueueQueued, nil
	case "evicted":
		return machine.QueueEvicted, nil
	case "not queued":
		if row.Mergeability == "failed" || fell {
			return machine.QueueEvicted, nil
		}
		return machine.QueueNone, nil
	case "unknown":
		switch {
		case pr.labelled(trunkMerged):
			return machine.QueueNone, nil
		case pr.labelled(trunkQueued...):
			return machine.QueueQueued, nil
		case fell:
			return machine.QueueEvicted, nil
		}
		return machine.QueueNone, nil
	case "landed":
		return machine.QueueNone, nil
	}
	return "", fmt.Errorf("#%d: unknown queue state %q", row.Number, row.Queue)
}

func mergeableOf(row statusRow, pr pullRequest) machine.Mergeable {
	blocked, isBlocked := strings.CutPrefix(row.Verdict, "blocked:")
	if !isBlocked {
		return machine.MergeableClean
	}
	causes := strings.Split(blocked, ",")
	switch {
	case slices.Contains(causes, "conflict"):
		return machine.MergeableConflict
	case slices.Contains(causes, queueUnknown):
		return machine.MergeableUnknown
	case slices.Contains(causes, "mergeable-unknown") && !pr.mergeableAt(row.Head):
		return machine.MergeableUnknown
	case slices.ContainsFunc(causes, func(cause string) bool { return strings.HasPrefix(cause, "needs-restack") }):
		return machine.MergeableBehind
	}
	return machine.MergeableClean
}
