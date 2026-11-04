package facts

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
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
	memberBatch    = 25
	queueUnknown   = "queue-unknown"
	memberFields   = `number state title isDraft headRefOid headRefName baseRefName body author { login } labels(first: 50) { nodes { name } } commits(first: 100) { nodes { commit { oid } } } mergeable mergeStateStatus tip: commits(last: 1) { nodes { commit { statusCheckRollup { state } } } } reviews(last: 50, states: [APPROVED, CHANGES_REQUESTED]) { nodes { state author { login } commit { oid } } }`
)

var carriedAbove = regexp.MustCompile(`\(PR_NOT_ENQUEUEABLE\): PR #\d+, above this one in the same stack, is already in the merge queue`)

var trunkQueued = []string{"trunk-queued", "trunk-not-ready", "trunk-testing", "trunk-tests-passed", "trunk-queued (bisection)", "trunk-testing (bisection)", "trunk-tests-passed (bisection)"}

type Source struct {
	Repo    string
	Drive   string
	Author  string
	Trunk   string
	GoLanes []string
	Reads   bool
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
		Trunk:   cfg.Trunk,
		GoLanes: cfg.GoLaneNames(),
		Reads:   slices.Contains(cfg.Movers, config.MoverLanding),
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
	held, err := s.holds(ctx)
	if err != nil {
		return nil, fmt.Errorf("read holds: %w", err)
	}
	rows, err := s.Rows().Pass(ctx, heldWords(held))
	if err != nil {
		return nil, fmt.Errorf("fold the review rows: %w", err)
	}
	if !s.Reads {
		return s.fromRows(rows, held)
	}
	itemed, err := s.itemed()
	if err != nil {
		return nil, err
	}
	numbers := liveNumbers(rows)
	for number := range itemed {
		numbers = append(numbers, number)
	}
	slices.Sort(numbers)
	numbers = slices.Compact(numbers)
	plan, err := s.planReads(ctx, rows, numbers)
	if err != nil {
		return nil, fmt.Errorf("read the github records: %w", err)
	}
	numbers = plan.numbers
	members, err := s.readMembers(ctx, numbers, rows)
	if err != nil {
		return nil, fmt.Errorf("read the drive's pull requests: %w", err)
	}
	open, ended := s.splitEnded(numbers, members, itemed)
	if err := s.Rows().PostReads(ctx, ended); err != nil {
		s.Log.Warn("an ended pull request's read went unposted", "error", err)
	}
	statuses, err := s.statuses(ctx, open)
	if err != nil {
		return nil, fmt.Errorf("read queue and CI: %w", err)
	}
	required, err := s.requiredChecks(ctx)
	if err != nil {
		return nil, fmt.Errorf("read required checks: %w", err)
	}
	read, err := s.assemble(ctx, statuses, members, required, held, rows)
	if err != nil {
		return nil, err
	}
	unread, err := s.fromRows(unreadRows(rows, numbers), held)
	if err != nil {
		return nil, err
	}
	for at := range unread {
		unread[at].FromRow = true
	}
	return slices.Concat(read, unread), s.settleReads(plan)
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
	held, err := s.holds(ctx)
	if err != nil {
		return err
	}
	_, err = s.Rows().Pass(ctx, heldWords(held))
	return err
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
			Closed: true, Merged: pr.State == "MERGED" || pr.labelled(trunkMerged),
			CI: machine.CIPending, Mergeable: machine.MergeableUnknown, Queue: machine.QueueNone,
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
	atHeads, err := s.Store.FactsAtRowHeads(s.Repo)
	if err != nil {
		return nil, err
	}
	out := make([]machine.Facts, 0, len(rows))
	for _, row := range rows {
		if row.ReadHead == "" || prrow.Terminal(row.State) && !itemed[row.Number] {
			continue
		}
		f, err := factsOfRow(s.Repo, row, atHeads[row.Number])
		if err != nil {
			return nil, err
		}
		f.Hold, f.HoldSeq = held[row.Number].by, held[row.Number].seq
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
		facts = append(tapFacts(body), bus.Fact{PR: body.PR, Head: body.Head, Kind: prrow.FactTapBy, Value: body.By}, bus.Fact{PR: body.PR, Head: body.Head, Kind: prrow.FactTapAt, Value: store.Stamp(record.At)})
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
		facts := bus.LegacyFacts(record, s.Repo, s.GoLanes)
		if err := s.putFacts(append(facts, graderWords(facts, record.Text)...), record.At.UTC()); err != nil {
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

type memberRead struct {
	carries map[int][]int
	prs     map[int]pullRequest
	authors map[int]string
	missing map[int]bool
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

type graphError struct {
	Type string `json:"type"`
}

func (s Source) readMembers(ctx context.Context, numbers []int, rows []store.ReviewRow) (memberRead, error) {
	prs := make([]pullRequest, 0, len(numbers))
	missing := make(map[int]bool)
	for batch := range slices.Chunk(numbers, memberBatch) {
		out, err := s.graphQL(ctx, membersQuery(batch))
		var reply struct {
			Data struct {
				Repository map[string]*pullRequest `json:"repository"`
			} `json:"data"`
			Errors []graphError `json:"errors"`
		}
		decoded := json.Unmarshal([]byte(out), &reply)
		notFound := len(reply.Errors) > 0 && !slices.ContainsFunc(reply.Errors, func(one graphError) bool { return one.Type != "NOT_FOUND" })
		if err != nil && !notFound {
			return memberRead{}, err
		}
		if decoded != nil {
			return memberRead{}, decoded
		}
		for alias, pr := range reply.Data.Repository {
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
	}
	authors := make(map[int]string, len(prs))
	byNumber := make(map[int]pullRequest, len(prs))
	for _, pr := range prs {
		authors[pr.Number] = pr.Author.Login
		byNumber[pr.Number] = pr
	}
	return memberRead{carried(prs, rows), byNumber, authors, missing}, nil
}

func (s Source) ReadMergeable(ctx context.Context, numbers []int) (map[int]string, error) {
	members, err := s.readMembers(ctx, numbers, nil)
	if err != nil {
		return nil, err
	}
	read := make(map[int]string, len(members.prs))
	for number, pr := range members.prs {
		read[number] = fmt.Sprintf("%s %s at %s", pr.Mergeable, pr.MergeStateStatus, pr.HeadRefOid)
	}
	return read, nil
}

func membersQuery(numbers []int) string {
	var aliases strings.Builder
	for _, number := range numbers {
		fmt.Fprintf(&aliases, "r%d: pullRequest(number: %d) { %s } ", number, number, memberFields)
	}
	return "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { " + aliases.String() + "} }"
}

func carried(prs []pullRequest, rows []store.ReviewRow) map[int][]int {
	byHead := make(map[string]int, len(prs)+len(rows))
	for _, row := range rows {
		if !prrow.Terminal(row.State) {
			byHead[row.Head] = row.Number
		}
	}
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
	by  machine.Mover
	seq int64
	why string
}

var (
	holdOpening = regexp.MustCompile(`(?i)^\s*hold\s+#?\d+\s*[:,-]?\s*`)
	planNamed   = regexp.MustCompile(`(?i)\bplan (?:row|step|\d)`)
)

func holdWhy(record client.Record, by machine.Mover) string {
	line, _, _ := strings.Cut(record.Text, "\n")
	why := fmt.Sprintf("%s (record #%d)", holdOpening.ReplaceAllString(line, ""), record.Seq)
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

func heldWords(held map[int]hold) map[int]string {
	words := make(map[int]string, len(held))
	for number, one := range held {
		words[number] = one.why
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
		s.Log.Warn("could not read what holds resolve, so each counts as a lift", "error", err)
	}
	held := make(map[int]hold)
	for _, record := range records {
		if lifts[record.Seq] {
			continue
		}
		byOwner := record.Lane == string(machine.MoverOwner)
		for _, pr := range bus.HeldBy(record, s.Repo) {
			switch {
			case byOwner:
				held[pr] = hold{machine.MoverOwner, record.Seq, holdWhy(record, machine.MoverOwner)}
			case !bus.GoLifts(gos, record, pr) && held[pr].by != machine.MoverOwner:
				held[pr] = hold{machine.MoverRoot, record.Seq, holdWhy(record, machine.MoverRoot)}
			}
		}
	}
	return held, nil
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
	for _, row := range known {
		if owners[row.Number] == "" {
			owners[row.Number] = row.Lane
		}
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
		if err := s.carry(ctx, &f, stored); err != nil {
			if !errors.Is(err, errDiffUnread) || ctx.Err() != nil {
				return nil, fmt.Errorf("#%d carry: %w", row.Number, err)
			}
			s.Log.Warn("pull request unread this pass", "pr", row.Number, "err", err)
			continue
		}
		out = append(out, f)
	}
	readRows(out, known)
	return out, nil
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
	if tapped == "" && goHead == "" {
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
		f.TapFrom, f.CarriedBy = tapped, gates.CarriedBy(tapped, atTap, stored)
		if f.CarriedBy != "" {
			f.Tap = machine.TapTapped
		}
	}
	if goHead != "" && goHead != f.Head {
		atGo, err := s.Store.Facts(s.Repo, f.Number, goHead)
		if err != nil {
			return err
		}
		f.Exempt = gates.SameDiff(atGo, stored)
	}
	return nil
}

var errDiffUnread = errors.New("diff unread")

func (s Source) hashDiff(ctx context.Context, f machine.Facts, stored map[string]string) error {
	if stored[gates.DiffFact] != "" {
		return nil
	}
	fetch, below := []string{"git", "fetch", "--no-write-fetch-head", "-q", "origin", f.Head}, []string{"git", "merge-base", f.Head}
	for _, branch := range slices.Compact([]string{f.Base, s.Trunk}) {
		ref := "refs/remotes/origin/" + branch
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
		CI:         ci,
		Failing:    row.CI.Failing,
		Approved:   row.Approval.State == "approved",
		Approvers:  pr.approversAt(row.Head),
		Mergeable:  mergeable,
		Uncomputed: mergeable == machine.MergeableUnknown && !strings.Contains(row.Verdict, queueUnknown),
		Queue:      queue,
		UnsafeWait: gates.UnsafeWait(pr.Body),
		Body:       pr.Body,
	}
	if queue == machine.QueueQueued {
		f.QueuedHead = row.Head
	}
	err = fromStored(&f, stored, owner)
	return f, err
}

func fromStored(f *machine.Facts, stored map[string]string, owner string) error {
	f.Lane = stored["lane"]
	if f.Lane == "" {
		f.Lane = owner
	}
	resubmitted, err := atoiOrZero(stored["resubmitted"])
	if err != nil {
		return fmt.Errorf("#%d resubmitted: %w", f.Number, err)
	}
	f.Resubmitted = resubmitted
	f.Finder, f.Fidelity = machine.Finder(stored["finder"]), machine.Fidelity(stored["fidelity"])
	f.Tap, f.Condition = machine.Tap(stored["tap"]), stored["condition"]
	f.GradeStopped = stored[machine.GradeStoppedFact]
	f.TapRefused = stored[bus.TapRefusedFact]
	_, f.Verdict = stored["verdict"]
	_, f.Exempt = stored["exempt"]
	return nil
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
