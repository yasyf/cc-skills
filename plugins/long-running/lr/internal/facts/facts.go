package facts

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	BusCursor   = "bus"
	caller      = "lr-facts"
	wordsPrefix = "words-"
	openQuery   = `query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 100, after: $endCursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        headRefOid
        author { login }
        labels(first: 50) { nodes { name } }
        commits(first: 100) { nodes { commit { oid } } }
      }
    }
  }
}`
)

type Source struct {
	Repo    string
	Drive   string
	Author  string
	Label   string
	GoLanes []string
	CCX     ccx.Client
	Bus     bus.Bus
	Store   *store.Store
	Log     *slog.Logger
	Now     func() time.Time
}

func New(a *app.App, cfg config.Config, s *store.Store) Source {
	return Source{
		Repo:    cfg.Repo,
		Drive:   cfg.Drive,
		Author:  cfg.Author,
		Label:   cfg.Label,
		GoLanes: cfg.GoLaneNames(),
		CCX:     ccx.CLI{Runner: fromCheckout{a.Runner, cfg.Checkout, cfg.Repo}},
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
	rows, err := s.CCX.PRStatus(ctx, s.Repo, numbers)
	if err != nil {
		return nil, fmt.Errorf("read queue and CI: %w", err)
	}
	held, err := s.holds(ctx)
	if err != nil {
		return nil, fmt.Errorf("read holds: %w", err)
	}
	return s.assemble(rows, open.carries, held)
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
		if err := s.Store.Advance(bus.LegacyCursor, record.Seq); err != nil {
			return err
		}
	}
	return nil
}

type openRead struct {
	numbers []int
	carries map[int][]int
}

type label struct {
	Name string `json:"name"`
}

type pullRequest struct {
	Number     int    `json:"number"`
	HeadRefOid string `json:"headRefOid"`
	Author     struct {
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
}

func (s Source) readOpen(ctx context.Context) (openRead, error) {
	owner, name, found := strings.Cut(s.Repo, "/")
	if !found {
		return openRead{}, fmt.Errorf("repo %q is not owner/name", s.Repo)
	}
	out, err := s.CCX.GH(ctx, ccx.MaxAgeFresh, "api", "graphql", "--paginate", "-f", "owner="+owner, "-f", "name="+name, "-f", "query="+openQuery)
	if err != nil {
		return openRead{}, err
	}
	prs, err := decodePages(out)
	if err != nil {
		return openRead{}, err
	}
	admitted := slices.DeleteFunc(prs, func(pr pullRequest) bool { return !s.admits(pr) })
	return openRead{numbers(admitted), carried(admitted)}, nil
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

func (s Source) holds(ctx context.Context) (map[int]machine.Mover, error) {
	records, err := client.Open(ctx, s.Drive, "hold")
	if err != nil {
		return nil, err
	}
	held := make(map[int]machine.Mover)
	for _, record := range records {
		mover := machine.MoverRoot
		if record.Lane == string(machine.MoverOwner) {
			mover = machine.MoverOwner
		}
		for _, pr := range record.Refs.PRs {
			if pr.Repo == "" || strings.EqualFold(pr.Repo, s.Repo) {
				held[pr.Number] = mover
			}
		}
	}
	return held, nil
}

func (s Source) assemble(rows []ccx.PRStatus, carries map[int][]int, held map[int]machine.Mover) ([]machine.Facts, error) {
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
		f, err := s.factsOf(row, stored, owners[row.Number])
		if err != nil {
			return nil, err
		}
		f.Hold, f.Carries = held[row.Number], carries[row.Number]
		out = append(out, f)
	}
	return out, nil
}

func (s Source) factsOf(row ccx.PRStatus, stored map[string]string, owner string) (machine.Facts, error) {
	queue, err := queueOf(row)
	if err != nil {
		return machine.Facts{}, err
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
		Head:        row.Head,
		Base:        row.Base,
		Lane:        lane,
		Closed:      row.State != "OPEN",
		Merged:      row.State == "MERGED" || row.Queue == "landed",
		CI:          ciOf(row.CI.State),
		Approved:    row.Approval.State == "approved",
		Mergeable:   mergeableOf(row.Verdict),
		Queue:       queue,
		Resubmitted: resubmitted,
		Finder:      machine.Finder(stored["finder"]),
		Fidelity:    machine.Fidelity(stored["fidelity"]),
		Tap:         machine.Tap(stored["tap"]),
		Condition:   stored["condition"],
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

func ciOf(state string) machine.CI {
	switch state {
	case "green":
		return machine.CISuccess
	case "red":
		return machine.CIFailure
	}
	return machine.CIPending
}

func queueOf(row ccx.PRStatus) (machine.Queue, error) {
	switch row.Queue {
	case "queued":
		return machine.QueueQueued, nil
	case "evicted":
		return machine.QueueEvicted, nil
	case "not queued":
		if row.Mergeability == "failed" {
			return machine.QueueEvicted, nil
		}
		return machine.QueueNone, nil
	case "landed", "unknown":
		return machine.QueueNone, nil
	}
	return "", fmt.Errorf("#%d: unknown queue state %q", row.Number, row.Queue)
}

func mergeableOf(verdict string) machine.Mergeable {
	blocked, isBlocked := strings.CutPrefix(verdict, "blocked:")
	if !isBlocked {
		return machine.MergeableClean
	}
	causes := strings.Split(blocked, ",")
	switch {
	case slices.Contains(causes, "conflict"):
		return machine.MergeableConflict
	case slices.Contains(causes, "mergeable-unknown"), slices.Contains(causes, "queue-unknown"):
		return machine.MergeableUnknown
	case slices.ContainsFunc(causes, func(cause string) bool { return strings.HasPrefix(cause, "needs-restack") }):
		return machine.MergeableBehind
	}
	return machine.MergeableClean
}
