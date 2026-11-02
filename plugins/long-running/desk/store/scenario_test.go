package store

import (
	"context"
	"fmt"
	"math"
	"math/big"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/pyjson"
)

const (
	runner      = "desk-runner"
	landing     = "desk-landing"
	lane        = "lane-ñandú"
	unreachable = "orca-unreachable"
	deferredKey = "deferred-escalations"
)

var (
	start    = time.Date(2026, 11, 2, 8, 0, 0, 0, time.UTC)
	pacific  = time.FixedZone("", -8*3600)
	mountain = time.FixedZone("", -7*3600)
)

func numbers() *pyjson.Object {
	big70, _ := new(big.Int).SetString("1180591620717411303424", 10)
	return pyjson.ObjectOf(
		"int", 3,
		"big", big70,
		"negative", -12,
		"float", 1.5,
		"tiny", 1e-07,
		"huge", 1e22,
		"neg0", math.Copysign(0, -1),
		"inf", math.Inf(1),
		"list", []any{1, "two", nil, true, false, pyjson.ObjectOf("k", "v", "a", []any{})},
		"nested", pyjson.ObjectOf("deep", pyjson.ObjectOf("deeper", "ünïcode 🚀 <>& \u2028 \"q\" \\ /")),
		"control", "tab\there\nnewline\r\x01\x7f\x1f",
		"lone", "\xed\xa0\xbd",
		"empty", pyjson.NewObject(),
		"emptylist", []any{},
	)
}

type scenarioRun struct {
	t     *testing.T
	ctx   context.Context
	clock *fixedClock
	book  *Book
}

func (s *scenarioRun) must(err error) {
	s.t.Helper()
	if err != nil {
		s.t.Fatal(err)
	}
}

func (s *scenarioRun) facts(tx *Transaction) *Facts {
	facts, err := tx.Facts()
	s.must(err)
	return facts
}

func (s *scenarioRun) edit(container string, change func(*Transaction) (any, error)) any {
	value, err := s.book.Edit(s.ctx, container, change)
	s.must(err)
	return value
}

func (s *scenarioRun) attempt(container string, change func(*Transaction) error) bool {
	applied, err := s.book.Attempt(s.ctx, container, change)
	s.must(err)
	return applied
}

func (s *scenarioRun) fact(container, key string, def ...any) any {
	value, err := s.book.Fact(s.ctx, container, key, def...)
	s.must(err)
	return value
}

func (s *scenarioRun) factsOf(container string, keys []string) *pyjson.Object {
	facts, err := s.book.Facts(s.ctx, container, keys)
	s.must(err)
	return facts
}

func (s *scenarioRun) exists(container string) bool {
	held, err := s.book.Exists(container)
	s.must(err)
	return held
}

func (s *scenarioRun) known(container, actionID string) *Action {
	action, err := s.book.Known(s.ctx, container, actionID)
	s.must(err)
	return action
}

func (s *scenarioRun) actionIDs(filters ...Filter) []any {
	actions, err := s.book.Actions(s.ctx, runner, filters...)
	s.must(err)
	ids := []any{}
	for _, action := range actions {
		ids = append(ids, action.ActionID)
	}
	return ids
}

func (s *scenarioRun) record(actionID, line string) []any {
	action, created, err := s.book.Accept(s.ctx, runner, actionID, "escalation", line, "runner", nil)
	s.must(err)
	return []any{action.fields(), created}
}

func (s *scenarioRun) targetField(action *Action, key string) string {
	target, err := pyjson.Loads(action.Target)
	s.must(err)
	value, _ := target.(*pyjson.Object).Get(key)
	return value.(string)
}

func (s *scenarioRun) hold(tx *Transaction, key string) error {
	held, err := s.facts(tx).SetDefault(deferredKey, pyjson.ObjectOf("incident", "lane-a, lane-b", "ids", []any{}))
	if err != nil {
		return err
	}
	facts := held.(*Facts)
	value, err := facts.Get("ids")
	if err != nil {
		return err
	}
	ids := value.([]any)
	if slices.Contains(ids, any(key)) {
		return nil
	}
	return facts.Set("ids", append(ids, key))
}

func (s *scenarioRun) releaseDeferred() []any {
	held := s.fact(runner, deferredKey, nil).(*pyjson.Object)
	value, _ := held.Get("ids")
	ids := value.([]any)
	var lines []string
	for _, id := range ids {
		action, err := s.book.Action(s.ctx, runner, id.(string))
		s.must(err)
		lines = append(lines, action.Target)
	}
	s.edit(runner, func(tx *Transaction) (any, error) {
		for _, id := range ids {
			key := id.(string)
			action, err := tx.Action(key)
			if err != nil {
				return nil, err
			}
			if action.Status != "accepted" {
				continue
			}
			if _, err := tx.Start(key, s.clock.Now(), nil, ""); err != nil {
				return nil, err
			}
			if _, err := tx.Complete(key, pyjson.ObjectOf("at", s.book.Stamp(), "deferred", true), ""); err != nil {
				return nil, err
			}
		}
		_, err := s.facts(tx).Pop(deferredKey)
		return nil, err
	})
	incident, _ := held.Get("incident")
	return s.record("escalation:deferred:"+ids[0].(string), fmt.Sprintf("DEFERRED %d escalations held during %s: %s", len(lines), incident, strings.Join(lines, " | ")))
}

func (s *scenarioRun) acceptPolicy(key, rule, revision, source, supersedes string) []any {
	s.must(s.book.Ensure(s.ctx, landing, landing))
	verified, err := s.book.Actions(s.ctx, landing, Kind("policy"), Status("verified"))
	s.must(err)
	superseded := map[string]bool{}
	for _, action := range verified {
		superseded[s.targetField(action, "supersedes")] = true
	}
	var current *Action
	for _, action := range verified {
		if !superseded[s.targetField(action, "revision")] {
			current = action
			break
		}
	}
	target, err := pyjson.Dumps(pyjson.ObjectOf("rule", rule, "revision", revision, "supersedes", supersedes))
	s.must(err)
	action, created, err := s.book.Accept(s.ctx, landing, key, "policy", target, source, nil)
	s.must(err)
	if !created {
		return []any{action.fields(), created}
	}
	if current != nil && supersedes != s.targetField(current, "revision") {
		named := supersedes
		if named == "" {
			named = "no"
		}
		reason := fmt.Sprintf("names %s predecessor; the accepted landing policy is %s at %s (%s)", named, s.targetField(current, "rule"), s.targetField(current, "revision"), *current.AuthorityRef)
		s.attempt(landing, func(tx *Transaction) error { _, err := tx.Fail(key, reason); return err })
		s.record("escalation:"+key, fmt.Sprintf("STALE-POLICY %s landing: %s at %s from %s rejected: %s", key, rule, revision, source, reason))
	} else {
		s.attempt(landing, func(tx *Transaction) error {
			_, err := tx.Verify(key, pyjson.ObjectOf("at", s.book.Stamp(), "supersedes", supersedes))
			return err
		})
	}
	stored, err := s.book.Action(s.ctx, landing, key)
	s.must(err)
	return []any{stored.fields(), created}
}

func runScenario(t *testing.T, root string) []any {
	clock := &fixedClock{at: start}
	s := &scenarioRun{t: t, ctx: context.Background(), clock: clock, book: NewBook(New(root), clock)}
	book := s.book
	var seen []any
	s.must(book.Ensure(s.ctx, runner, runner))
	clock.at = time.Date(2026, 11, 2, 0, 0, 1, 0, pacific)
	s.must(book.Ensure(s.ctx, runner, "someone-else"))
	s.must(book.Ensure(s.ctx, landing, landing))
	clock.at = start.Add(2*time.Second + 999999*time.Microsecond)
	s.must(book.Ensure(s.ctx, lane, "dispatch-ü"))
	seen = append(seen, []any{s.exists(runner), s.exists("desk-missing"), s.known(runner, "escalation:none") == nil, s.known("desk-missing", "x") == nil})

	streakOf := func(def *pyjson.Object) *pyjson.Object {
		return s.edit(runner, func(tx *Transaction) (any, error) {
			held, err := s.facts(tx).SetDefault(unreachable, def)
			if err != nil {
				return nil, err
			}
			return held.(*Facts).Detach()
		}).(*pyjson.Object)
	}
	clock.at = start.Add(time.Minute)
	streak := streakOf(pyjson.ObjectOf("since", book.Stamp(), "error", "Orca gave no answer — curl: (7) ñ 🚀"))
	clock.at = start.Add(90 * time.Second)
	again := streakOf(pyjson.ObjectOf("since", book.Stamp(), "error", "later"))
	seen = append(seen, []any{streak, again, s.factsOf(runner, []string{unreachable, "absent"})})

	since, _ := streak.Get("since")
	reported, _ := streak.Get("error")
	escalation := fmt.Sprintf("escalation:orca:%s", since)
	line := fmt.Sprintf("ORCA-UNREACHABLE orca:%s runner: %s; since 12:01 AM PST the runner reads no dispatch as gone", since, reported)
	seen = append(seen, s.record(escalation, line), s.record(escalation, "ignored"))

	clock.at = start.Add(2 * time.Minute)
	posted := s.attempt(runner, func(tx *Transaction) error {
		if _, err := tx.Start(escalation, clock.Now(), nil, ""); err != nil {
			return err
		}
		_, err := tx.Complete(escalation, pyjson.ObjectOf("at", book.Stamp(), "posted", pyjson.ObjectOf("seq", 4217, "drive", "drive-ü", "ratio", 0.25)), "")
		return err
	})
	restarted := s.attempt(runner, func(tx *Transaction) error { _, err := tx.Start(escalation, clock.Now(), nil, ""); return err })
	unknown := s.attempt(runner, func(tx *Transaction) error { _, err := tx.Action("escalation:missing"); return err })
	rolledBack := s.attempt(runner, func(tx *Transaction) error {
		if err := s.facts(tx).Set("rolled-back", true); err != nil {
			return err
		}
		_, err := tx.Start(escalation, clock.Now(), nil, "")
		return err
	})
	seen = append(seen, []any{posted, restarted, unknown, rolledBack, s.factsOf(runner, []string{"rolled-back"})})

	refused := "escalation:refused:lane-a"
	seen = append(seen, s.record(refused, "POSTS-REFUSED refused:lane-a runner: 4 cci posts refused in the last minute, the newest to lane-a: exit 2"))
	seen = append(seen, s.attempt(runner, func(tx *Transaction) error {
		if _, err := tx.Start(refused, clock.Now(), nil, ""); err != nil {
			return err
		}
		_, err := tx.Complete(refused, pyjson.ObjectOf("at", book.Stamp(), "posted", pyjson.ObjectOf("seq", 4218), "to", "lane-a"), "")
		return err
	}))

	clock.at = start.Add(3 * time.Minute)
	deadline, stale := "escalation:deadline:lane-b", "escalation:stale-mail:lane-c"
	seen = append(seen, s.record(deadline, "DEADLINE launch:lane-b lane-b: no ack in 10 min on #1234 and #99"))
	seen = append(seen, s.record(stale, "STALE-MAIL mail:lane-c lane-c: 3 unread"))
	for _, key := range []string{deadline, stale, deadline} {
		s.edit(runner, func(tx *Transaction) (any, error) { return nil, s.hold(tx, key) })
	}
	seen = append(seen, s.fact(runner, deferredKey, nil))
	seen = append(seen, s.releaseDeferred())
	seen = append(seen, []any{s.fact(runner, deferredKey, nil), s.fact(runner, deferredKey, "absent")})

	seen = append(seen, s.acceptPolicy("policy:r1", "ready-prefix", "r1", "owner", ""))
	seen = append(seen, s.acceptPolicy("policy:r3", "ready-prefix", "r3", "owner-ü", "r2"))
	seen = append(seen, s.acceptPolicy("policy:r1", "ready-prefix", "r1", "owner", ""))

	clock.at = start.Add(4 * time.Minute)
	launchKey := "launch:lane-ñandú:1"
	launchTarget, err := pyjson.Dumps(pyjson.ObjectOf("model", "opus", "effort", "xhigh", "brief", "/briefs/ñandú.md", "prior", nil, "urgent", false, "fast", false, "role", "", "incident", ""))
	s.must(err)
	launchDeadline := time.Date(2026, 11, 2, 1, 30, 0, 0, mountain)
	launch, created, err := book.Accept(s.ctx, lane, launchKey, "launch", launchTarget, launchKey, &launchDeadline)
	s.must(err)
	seen = append(seen, []any{launch.fields(), created})
	replyTarget, err := pyjson.Dumps(pyjson.ObjectOf("reply_to", "thread-ü"))
	s.must(err)
	_, _, err = book.Accept(s.ctx, lane, "reply:ü", "reply", replyTarget, "auth-ü", nil)
	s.must(err)
	replyDeadline := start.Add(9 * time.Minute)
	seen = append(seen, s.attempt(lane, func(tx *Transaction) error {
		if _, err := tx.Start("reply:ü", clock.Now(), &replyDeadline, "dispatch-1"); err != nil {
			return err
		}
		_, err := tx.Complete("reply:ü", pyjson.ObjectOf("z", 1, "a", []any{1.25, nil, 1e-07, 1e22}), "")
		return err
	}))
	seen = append(seen, s.attempt(lane, func(tx *Transaction) error {
		_, err := tx.Verify("reply:ü", pyjson.ObjectOf("receipt", "ok"))
		return err
	}))
	seen = append(seen, s.attempt(lane, func(tx *Transaction) error { _, err := tx.Fail("reply:ü", "late"); return err }))
	s.edit(lane, func(tx *Transaction) (any, error) { return nil, s.facts(tx).Set("numbers", numbers()) })
	s.edit(lane, func(tx *Transaction) (any, error) { return nil, s.facts(tx).Set("path/with~tilde", "escaped") })
	seen = append(seen, s.factsOf(lane, nil))
	s.edit(lane, func(tx *Transaction) (any, error) {
		return nil, s.facts(tx).Set("numbers", pyjson.ObjectOf("list", []any{3, 2, 1}, "int", 4, "nested", "flattened", "empty", pyjson.ObjectOf("now", "filled")))
	})
	s.edit(lane, func(tx *Transaction) (any, error) {
		held, err := s.facts(tx).Get("numbers")
		if err != nil {
			return nil, err
		}
		list, err := held.(*Facts).Get("list")
		if err != nil {
			return nil, err
		}
		list.([]any)[0] = int64(99)
		return nil, nil
	})
	popped := s.edit(lane, func(tx *Transaction) (any, error) { return s.facts(tx).Pop("path/with~tilde") })
	fallback := s.edit(lane, func(tx *Transaction) (any, error) { return s.facts(tx).Pop("missing", "fallback") })
	seen = append(seen, []any{popped, fallback})
	s.edit(runner, func(tx *Transaction) (any, error) {
		action, err := tx.Action(escalation)
		if err != nil {
			return nil, err
		}
		posted, _ := action.Response.(*pyjson.Object).Get("posted")
		posted.(*pyjson.Object).Set("acked", true)
		return nil, nil
	})
	seen = append(seen, s.factsOf(lane, nil))
	escalations, err := book.Actions(s.ctx, runner, Kind("escalation"))
	s.must(err)
	recorded := []any{}
	for _, action := range escalations {
		recorded = append(recorded, action.fields())
	}
	seen = append(seen, recorded)
	seen = append(seen, s.actionIDs(Status("completed")))
	seen = append(seen, s.actionIDs(Kind("escalation"), Statuses("accepted", "completed")))
	seen = append(seen, s.actionIDs(Kind("escalation"), Statuses()))
	reply, err := book.Action(s.ctx, lane, "reply:ü")
	s.must(err)
	return append(seen, reply.fields())
}
