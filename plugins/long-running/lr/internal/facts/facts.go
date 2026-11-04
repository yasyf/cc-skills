package facts

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	BusCursor      = "bus"
	caller         = "lr-facts"
	wordsPrefix    = "words-"
	trunkFailed    = "trunk-failed"
	trunkCancelled = "trunk-cancelled"
	trunkMerged    = "trunk-merged"
	settleBatch    = 25
	settleFields   = `number headRefOid mergeable mergeStateStatus tip: commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }`
	openQuery      = `query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 100, after: $endCursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        isDraft
        headRefOid
        headRefName
        body
        author { login }
        labels(first: 50) { nodes { name } }
        commits(first: 100) { nodes { commit { oid } } }
      }
    }
  }
}`
)

var carriedAbove = regexp.MustCompile(`\(PR_NOT_ENQUEUEABLE\): PR #\d+, above this one in the same stack, is already in the merge queue`)

var trunkQueued = []string{"trunk-queued", "trunk-not-ready", "trunk-testing", "trunk-tests-passed", "trunk-queued (bisection)", "trunk-testing (bisection)", "trunk-tests-passed (bisection)"}

type Source struct {
	Repo    string
	Drive   string
	Author  string
	Label   string
	Trunk   string
	GoLanes []string
	Runner  shell.Runner
	CCX     ccx.Client
	Bus     bus.Bus
	Store   *store.Store
	Log     *slog.Logger
	Now     func() time.Time
}

func New(a *app.App, cfg config.Config, s *store.Store) Source {
	runner := fromCheckout{a.Runner, cfg.Checkout, cfg.Repo}
	return Source{
		Repo:    cfg.Repo,
		Drive:   cfg.Drive,
		Author:  cfg.Author,
		Label:   cfg.Label,
		Trunk:   cfg.Trunk,
		GoLanes: cfg.GoLaneNames(),
		Runner:  runner,
		CCX:     ccx.CLI{Runner: runner},
		Bus:     bus.Bus{Drive: cfg.Drive},
		Store:   s,
		Log:     a.Log,
		Now:     a.Now,
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
	if err := s.ingestBus(ctx); err != nil {
		return nil, fmt.Errorf("read the bus: %w", err)
	}
	if err := s.ingestLegacy(ctx); err != nil {
		return nil, fmt.Errorf("read today's records: %w", err)
	}
	open, err := s.readOpen(ctx)
	if err != nil {
		return nil, fmt.Errorf("read the open pull requests: %w", err)
	}
	numbers, err := s.withOpenItems(open.numbers)
	if err != nil {
		return nil, err
	}
	rows, err := s.statuses(ctx, numbers)
	if err != nil {
		return nil, fmt.Errorf("read queue and CI: %w", err)
	}
	if err := s.settle(ctx, rows, open.prs); err != nil {
		return nil, fmt.Errorf("read the heads the status read left unsettled: %w", err)
	}
	required, err := s.requiredChecks(ctx)
	if err != nil {
		return nil, fmt.Errorf("read required checks: %w", err)
	}
	held, err := s.holds(ctx)
	if err != nil {
		return nil, fmt.Errorf("read holds: %w", err)
	}
	return s.assemble(ctx, rows, open, required, held)
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

func (s Source) statuses(ctx context.Context, numbers []int) ([]statusRow, error) {
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

func (s Source) settle(ctx context.Context, rows []statusRow, prs map[int]pullRequest) error {
	unsettled := make([]int, 0, len(rows))
	for _, row := range rows {
		if row.State == "OPEN" && (row.CI.State == "pending" || strings.Contains(row.Verdict, "mergeable-unknown")) {
			unsettled = append(unsettled, row.Number)
		}
	}
	for batch := range slices.Chunk(unsettled, settleBatch) {
		out, err := s.graphQL(ctx, settleQuery(batch))
		if err != nil {
			return err
		}
		var reply struct {
			Data struct {
				Repository map[string]pullRequest `json:"repository"`
			} `json:"data"`
		}
		if err := json.Unmarshal([]byte(out), &reply); err != nil {
			return err
		}
		for _, read := range reply.Data.Repository {
			pr := prs[read.Number]
			pr.HeadRefOid, pr.Mergeable, pr.MergeStateStatus, pr.Tip = read.HeadRefOid, read.Mergeable, read.MergeStateStatus, read.Tip
			prs[read.Number] = pr
		}
	}
	return nil
}

func settleQuery(numbers []int) string {
	var aliases strings.Builder
	for _, number := range numbers {
		fmt.Fprintf(&aliases, "r%d: pullRequest(number: %d) { %s } ", number, number, settleFields)
	}
	return "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { " + aliases.String() + "} }"
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
	return "Trunk Merge Queue (" + s.Trunk + ")"
}

func (s Source) ingestBus(ctx context.Context) error {
	since, err := s.Store.Cursor(BusCursor)
	if err != nil {
		return err
	}
	records, _, err := s.Bus.Read(ctx, since, "head", "grade", "tap", "verdict")
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
		facts = tapFacts(body)
	case bus.Verdict:
		facts = []bus.Fact{{PR: body.PR, Head: body.Head, Kind: "verdict", Value: body.Verdict}}
	case bus.Head:
		facts = headFacts(body)
	}
	return s.putFacts(facts, record.At)
}

func headFacts(head bus.Head) []bus.Fact {
	facts := []bus.Fact{{PR: head.PR, Head: head.Head, Kind: "lane", Value: head.Lane}}
	for name, words := range head.Words() {
		if words != "" {
			facts = append(facts, bus.Fact{PR: head.PR, Head: head.Head, Kind: wordsPrefix + name, Value: words})
		}
	}
	return facts
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
	}
	return nil
}

func (s Source) ingestLegacy(ctx context.Context) error {
	since, err := s.Store.Cursor(bus.LegacyCursor)
	if err != nil {
		return err
	}
	records, err := s.Bus.ReadLegacy(ctx, since, s.Now())
	if err != nil {
		return err
	}
	for _, record := range records {
		if err := s.putFacts(bus.LegacyFacts(record, s.Repo, s.GoLanes), record.At.UTC()); err != nil {
			return err
		}
		if err := s.clearWithdrawn(ctx, record); err != nil {
			return err
		}
		if err := s.Store.Advance(bus.LegacyCursor, record.Seq); err != nil {
			return err
		}
	}
	return nil
}

func (s Source) clearWithdrawn(ctx context.Context, record client.Record) error {
	seqs := bus.ClosedGos(record, s.GoLanes)
	if len(seqs) == 0 {
		return nil
	}
	closed, err := client.Among(ctx, s.Drive, seqs)
	if err != nil {
		return err
	}
	for _, closedGo := range closed {
		for _, fact := range bus.LegacyFacts(closedGo, s.Repo, s.GoLanes) {
			if fact.Kind != "exempt" {
				continue
			}
			if err := s.Store.ClearExempt(s.Repo, fact.PR, record.At.UTC()); err != nil {
				return err
			}
		}
	}
	return nil
}

type openRead struct {
	numbers []int
	carries map[int][]int
	prs     map[int]pullRequest
	authors map[int]string
}

type label struct {
	Name string `json:"name"`
}

type pullRequest struct {
	Number      int    `json:"number"`
	Title       string `json:"title"`
	IsDraft     bool   `json:"isDraft"`
	HeadRefOid  string `json:"headRefOid"`
	HeadRefName string `json:"headRefName"`
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
	Mergeable        string `json:"mergeable"`
	MergeStateStatus string `json:"mergeStateStatus"`
	Tip              struct {
		Nodes []struct {
			Commit struct {
				StatusCheckRollup *struct {
					State string `json:"state"`
				} `json:"statusCheckRollup"`
			} `json:"commit"`
		} `json:"nodes"`
	} `json:"tip"`
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

func (pr pullRequest) mergeableAt(head string) bool {
	return pr.HeadRefOid == head && pr.Mergeable == "MERGEABLE" && pr.MergeStateStatus != "DIRTY"
}

func (s Source) readOpen(ctx context.Context) (openRead, error) {
	out, err := s.graphQL(ctx, openQuery, "--paginate")
	if err != nil {
		return openRead{}, err
	}
	prs, err := decodePages(out)
	if err != nil {
		return openRead{}, err
	}
	authors := make(map[int]string, len(prs))
	for _, pr := range prs {
		authors[pr.Number] = pr.Author.Login
	}
	admitted := slices.DeleteFunc(prs, func(pr pullRequest) bool { return !s.admits(pr) })
	byNumber := make(map[int]pullRequest, len(admitted))
	for _, pr := range admitted {
		byNumber[pr.Number] = pr
	}
	return openRead{numbers(admitted), carried(admitted), byNumber, authors}, nil
}

func decodePages(out string) ([]pullRequest, error) {
	dec := json.NewDecoder(strings.NewReader(out))
	var prs []pullRequest
	for {
		var page struct {
			Data struct {
				Repository struct {
					PullRequests struct {
						Nodes []pullRequest `json:"nodes"`
					} `json:"pullRequests"`
				} `json:"repository"`
			} `json:"data"`
		}
		err := dec.Decode(&page)
		if errors.Is(err, io.EOF) {
			return prs, nil
		}
		if err != nil {
			return nil, err
		}
		prs = append(prs, page.Data.Repository.PullRequests.Nodes...)
	}
}

func (s Source) admits(pr pullRequest) bool {
	return pr.Author.Login == s.Author || slices.Contains(pr.Labels.Nodes, label{s.Label})
}

func numbers(prs []pullRequest) []int {
	out := make([]int, 0, len(prs))
	for _, pr := range prs {
		out = append(out, pr.Number)
	}
	slices.Sort(out)
	return out
}

func carried(prs []pullRequest) map[int][]int {
	byHead := make(map[string]int, len(prs))
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

func (s Source) withOpenItems(open []int) ([]int, error) {
	items, err := s.Store.OpenItems(s.Repo, 0)
	if err != nil {
		return nil, err
	}
	all := slices.Clone(open)
	for _, item := range items {
		all = append(all, item.Number)
	}
	slices.Sort(all)
	return slices.Compact(all), nil
}

type hold struct {
	by  machine.Mover
	seq int64
}

func (s Source) holds(ctx context.Context) (map[int]hold, error) {
	records, err := client.Open(ctx, s.Drive, "hold")
	if err != nil || len(records) == 0 {
		return nil, err
	}
	first := slices.MinFunc(records, func(a, b client.Record) int { return cmp.Compare(a.Seq, b.Seq) })
	later, err := client.Tail(ctx, client.Query{Drive: s.Drive, After: first.Seq, Kinds: bus.GoReadKinds})
	if err != nil {
		return nil, err
	}
	gos := bus.RootGos(later)
	lifts, err := bus.HoldLifts(records, func(seqs []int64) ([]client.Record, error) { return client.Among(ctx, s.Drive, seqs) })
	if err != nil {
		s.Log.Warn("could not read what holds resolve, so each counts as a lift", "error", err)
	}
	held := make(map[int]hold)
	for _, record := range records {
		if lifts[record.Seq] {
			continue
		}
		byOwner := record.Lane == string(machine.MoverOwner)
		for _, pr := range bus.HeldBy(record, s.Repo) {
			lifted := slices.ContainsFunc(gos, func(g client.Record) bool { return g.Seq > record.Seq && bus.GoNames(g, pr) })
			switch {
			case byOwner:
				held[pr] = hold{machine.MoverOwner, record.Seq}
			case !lifted && held[pr].by != machine.MoverOwner:
				held[pr] = hold{machine.MoverRoot, record.Seq}
			}
		}
	}
	return held, nil
}

func (s Source) assemble(ctx context.Context, rows []statusRow, open openRead, required []string, held map[int]hold) ([]machine.Facts, error) {
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
	out := make([]machine.Facts, 0, len(rows))
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
		f.Hold, f.HoldSeq, f.Carries = held[row.Number].by, held[row.Number].seq, open.carries[row.Number]
		if author := open.authors[row.Number]; author != s.Author {
			f.ForeignAuthor = author
		}
		if err := s.carryTap(ctx, &f, stored); err != nil {
			return nil, fmt.Errorf("#%d tap: %w", row.Number, err)
		}
		out = append(out, f)
	}
	return out, nil
}

func (s Source) carryTap(ctx context.Context, f *machine.Facts, stored map[string]string) error {
	if f.Closed || f.Tap != "" && f.Tap != machine.TapTapped {
		return nil
	}
	tapped := f.Head
	if f.Tap == "" {
		var err error
		if tapped, err = s.Store.StandingTap(s.Repo, f.Number); err != nil || tapped == "" {
			return err
		}
	}
	if err := s.hashDiff(ctx, *f, stored); err != nil || tapped == f.Head {
		return err
	}
	atTap, err := s.Store.Facts(s.Repo, f.Number, tapped)
	if err != nil {
		return err
	}
	f.TapFrom, f.CarriedBy = tapped, gates.CarriedBy(tapped, atTap, stored)
	if f.CarriedBy != "" {
		f.Tap = machine.TapTapped
	}
	return nil
}

func (s Source) hashDiff(ctx context.Context, f machine.Facts, stored map[string]string) error {
	if stored[gates.DiffFact] != "" {
		return nil
	}
	base := "refs/remotes/origin/" + f.Base
	if _, err := s.Runner.Run(ctx, shell.Command{Argv: []string{"git", "fetch", "--no-write-fetch-head", "-q", "origin", f.Head, "+refs/heads/" + f.Base + ":" + base}}); err != nil {
		return err
	}
	diff, err := s.Runner.Run(ctx, shell.Command{Argv: []string{"git", "diff", "--no-color", "--no-ext-diff", base + "..." + f.Head}})
	if err != nil {
		return err
	}
	stored[gates.DiffFact] = gates.DiffHash(diff.Stdout)
	_, err = s.Store.PutFact(s.Repo, f.Number, f.Head, gates.DiffFact, stored[gates.DiffFact], s.Now())
	return err
}

func (s Source) factsOf(row statusRow, pr pullRequest, required []string, stored map[string]string, owner string) (machine.Facts, error) {
	ci := s.ciOf(row, pr, required)
	queue, err := queueOf(row, pr, ci)
	if err != nil {
		return machine.Facts{}, err
	}
	if queue == machine.QueueNone && carriedAbove.MatchString(stored["enqueued"]) {
		queue = machine.QueueQueued
	}
	lane := stored["lane"]
	if lane == "" {
		lane = owner
	}
	resubmitted, err := atoiOrZero(stored["resubmitted"])
	if err != nil {
		return machine.Facts{}, fmt.Errorf("#%d resubmitted: %w", row.Number, err)
	}
	f := machine.Facts{
		Repo:        s.Repo,
		Number:      row.Number,
		Title:       pr.Title,
		Head:        row.Head,
		Branch:      pr.HeadRefName,
		Base:        row.Base,
		Lane:        lane,
		Draft:       pr.IsDraft,
		Closed:      row.State != "OPEN",
		Merged:      row.State == "MERGED" || row.Queue == "landed" || row.Queue == "unknown" && pr.labelled(trunkMerged),
		CI:          ci,
		Failing:     row.CI.Failing,
		Approved:    row.Approval.State == "approved",
		Mergeable:   mergeableOf(row, pr),
		Queue:       queue,
		Resubmitted: resubmitted,
		Finder:      machine.Finder(stored["finder"]),
		Fidelity:    machine.Fidelity(stored["fidelity"]),
		Tap:         machine.Tap(stored["tap"]),
		Condition:   stored["condition"],
		UnsafeWait:  gates.UnsafeWait(pr.Body),
	}
	if queue == machine.QueueQueued {
		f.QueuedHead = row.Head
	}
	_, f.Verdict = stored["verdict"]
	_, f.Exempt = stored["exempt"]
	return f, nil
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
	case slices.Contains(causes, "queue-unknown"):
		return machine.MergeableUnknown
	case slices.Contains(causes, "mergeable-unknown") && !pr.mergeableAt(row.Head):
		return machine.MergeableUnknown
	case slices.ContainsFunc(causes, func(cause string) bool { return strings.HasPrefix(cause, "needs-restack") }):
		return machine.MergeableBehind
	}
	return machine.MergeableClean
}
