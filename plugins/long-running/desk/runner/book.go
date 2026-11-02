package runner

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/pyjson"
	"github.com/yasyf/cc-skills/plugins/long-running/desk/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/desk/store"
)

const (
	LanePrefix        = "desk-lane-"
	DeferredFact      = "deferred-escalations"
	ClockFact         = "incident-clock"
	UnreachableFact   = "orca-unreachable"
	UnreachableWait   = 2 * time.Minute
	RefusalsPerMinute = 10
)

var escalationKinds = map[string]string{"DECIDE": "decide", "INCIDENT": "incident", "INCIDENT-OWNED": "state", "UNBOUND": "blocker", "UNOWNED": "blocker", "CLASS-UNOWNED": "blocker", "UNACKED": "blocker", "INCIDENT-CLOCK": "blocker", "LOAD-HIGH": "blocker", "CUT-SILENT": "blocker", "PR-SILENT": "blocker", "RELEASE-HELD-AFTER-LANDING": "blocker", "LAUNCH-STALLED": "blocker", "LAUNCH-DUPLICATE": "blocker", "UNGRADED-30": "blocker", "SLICE-UNREAD": "blocker", "GITHUB-QUOTA-LOW": "urgent", "FINDER-DESK-STALLED": "blocker", "GO-TAP-MISSING": "blocker", "OVERRIDE-REFUSED": "blocker", "REFUSED-STOP": "incident", "MUTE-PUSHBACK": "incident"}

func must[T any](value T, err error) T {
	check(err)
	return value
}

func check(err error) {
	if err != nil {
		panic(err)
	}
}

func or(text, otherwise string) string {
	if text != "" {
		return text
	}
	return otherwise
}

func optional(text *string) string {
	if text == nil {
		return "None"
	}
	return *text
}

func receipt(seq int) string { return fmt.Sprintf("#%d", seq) }

func pacificClock(moment time.Time) string { return moment.In(pacific).Format("15:04") }

// SeedPolicy is seed_policy.
func (r *Runner) SeedPolicy() {
	landing := r.Config.Landing
	if len(landing) == 0 || r.LandingPolicy() != nil {
		return
	}
	policy := landing["policy"].(map[string]any)
	revision := policy["revision"].(string)
	r.AcceptPolicy("policy:"+revision, policy["rule"].(string), revision, policy["source"].(string), "")
}

// LandingPolicy is Runner.landing_policy.
func (r *Runner) LandingPolicy() *store.Action {
	check(r.Book.Ensure(r.ctx, LandingLane, LandingLane))
	return activePolicy(must(r.Book.Actions(r.ctx, LandingLane, store.Kind("policy"), store.Status("verified"))))
}

func policyTarget(action *store.Action) map[string]string {
	var target map[string]string
	check(json.Unmarshal([]byte(action.Target), &target))
	return target
}

func activePolicy(verified []*store.Action) *store.Action {
	superseded := map[string]bool{}
	for _, action := range verified {
		superseded[policyTarget(action)["supersedes"]] = true
	}
	for _, action := range verified {
		if !superseded[policyTarget(action)["revision"]] {
			return action
		}
	}
	return nil
}

// AcceptPolicy is Runner.accept_policy.
func (r *Runner) AcceptPolicy(key, rule, revision, source, supersedes string) (*store.Action, bool) {
	current := r.LandingPolicy()
	target := must(pyjson.Dumps(pyjson.ObjectOf("rule", rule, "revision", revision, "supersedes", supersedes)))
	action, created, err := r.Book.Accept(r.ctx, LandingLane, key, "policy", target, source, nil)
	check(err)
	if !created {
		return action, created
	}
	if current != nil && supersedes != policyTarget(current)["revision"] {
		held := policyTarget(current)
		reason := fmt.Sprintf("names %s predecessor; the accepted landing policy is %s at %s (%s)", or(supersedes, "no"), held["rule"], held["revision"], optional(current.AuthorityRef))
		must(r.Book.Attempt(r.ctx, LandingLane, func(tx *store.Transaction) error {
			_, err := tx.Fail(key, reason)
			return err
		}))
		r.Escalate(key, "STALE-POLICY", "landing", fmt.Sprintf("%s at %s from %s rejected: %s", rule, revision, source, reason), "", "")
	} else {
		must(r.Book.Attempt(r.ctx, LandingLane, func(tx *store.Transaction) error {
			_, err := tx.Verify(key, pyjson.ObjectOf("at", r.Book.Stamp(), "supersedes", supersedes))
			return err
		}))
	}
	return must(r.Book.Action(r.ctx, LandingLane, key)), created
}

// Escalate is Runner.escalate.
func (r *Runner) Escalate(key, kind, about, text, subject, to string) {
	actionID, line := "escalation:"+key, fmt.Sprintf("%s %s %s: %s", kind, or(subject, key), about, strings.Join(strings.Fields(text), " "))
	if to == "" {
		r.record(actionID, line)
		return
	}
	if must(r.Book.Known(r.ctx, RunnerLane, actionID)) != nil {
		return
	}
	done := r.postEscalation(line, to, actionID)
	if done.Code != 0 {
		said := strings.TrimSpace(or(done.Err, done.Out))
		r.Shell.Stderr(fmt.Sprintf("escalation %s to %s left for the next pass; cci post exited %d: %s\n", key, to, done.Code, shell.Clip(said, 300)))
		var recent []time.Time
		for _, at := range append(r.refused, r.Now()) {
			if r.Now().Sub(at) < time.Minute {
				recent = append(recent, at)
			}
		}
		r.refused = recent
		if len(r.refused) > RefusalsPerMinute {
			r.Escalate("refused:"+to, "POSTS-REFUSED", RunnerLane, fmt.Sprintf("%d cci posts refused in the last minute, the newest to %s: %s", len(r.refused), to, shell.Clip(said, 200)), "", "")
		}
		return
	}
	r.record(actionID, line)
	must(r.Book.Attempt(r.ctx, RunnerLane, func(tx *store.Transaction) error {
		if _, err := tx.Start(actionID, r.Now(), nil, ""); err != nil {
			return err
		}
		_, err := tx.Complete(actionID, pyjson.ObjectOf("at", r.Book.Stamp(), "posted", receipt(done.Records[0].Seq), "to", to), "")
		return err
	}))
}

func (r *Runner) record(actionID, line string) {
	must3(r.Book.Accept(r.ctx, RunnerLane, actionID, "escalation", line, "runner", nil))
}

func must3[A, B any](a A, b B, err error) (A, B) {
	check(err)
	return a, b
}

func (r *Runner) messageFile(topic string) string {
	return filepath.Join(r.Config.Receipts, "cci", "messages", topic+".md")
}

func (r *Runner) postEscalation(line, to, actionID string) PostResult {
	words := strings.Fields(line)
	label, topic := words[0], words[1]
	message := ""
	if info, err := os.Stat(r.messageFile(topic)); err == nil && info.Mode().IsRegular() {
		message = string(must(os.ReadFile(r.messageFile(topic))))
	}
	kind, named := escalationKinds[label]
	if !named {
		kind = "report"
		if strings.HasSuffix(label, "-FAILED") {
			kind = "defect"
		}
	}
	return r.CciPost(kind, line, to, topic, actionID, message, "", "", nil, "", "")
}

// Flush is Runner.flush.
func (r *Runner) Flush() bool {
	var failures []string
	if clocks := r.liveClocks(); len(clocks) > 0 {
		panic("TODO: port Runner.flush's deferral while an incident clock runs (DEFERRABLE, siblings, names, defer)")
	}
	r.releaseDeferred()
	for _, action := range must(r.Book.Actions(r.ctx, RunnerLane, store.Kind("escalation"), store.Status("accepted"))) {
		done := r.postEscalation(action.Target, Root, action.ActionID)
		if done.Code != 0 {
			failures = append(failures, fmt.Sprintf("cci post exited %d: %s", done.Code, shell.Clip(strings.TrimSpace(or(done.Err, done.Out)), 300)))
			continue
		}
		key := action.ActionID
		must(r.Book.Attempt(r.ctx, RunnerLane, func(tx *store.Transaction) error {
			if _, err := tx.Start(key, r.Now(), nil, ""); err != nil {
				return err
			}
			_, err := tx.Complete(key, pyjson.ObjectOf("at", r.Book.Stamp(), "posted", receipt(done.Records[0].Seq)), "")
			return err
		}))
	}
	if len(failures) > 0 {
		r.Shell.Stderr(fmt.Sprintf("%d escalation(s) left for the next pass; %s\n", len(failures), failures[0]))
	}
	return len(failures) == 0
}

func (r *Runner) containers(prefix string) []string {
	var named []string
	for _, name := range must(r.Book.Store.IDs()) {
		if strings.HasPrefix(name, prefix) {
			named = append(named, name)
		}
	}
	return named
}

func (r *Runner) liveClocks() []string {
	var clocks []string
	for _, container := range r.containers(LanePrefix) {
		clock, _ := must(r.Book.Fact(r.ctx, container, ClockFact, nil)).(*pyjson.Object)
		if clock == nil || clock.Len() == 0 {
			continue
		}
		if ended, _ := clock.Get("ended"); !truthy(ended) {
			clocks = append(clocks, strings.TrimPrefix(container, LanePrefix))
		}
	}
	return clocks
}

func truthy(value any) bool {
	switch value := value.(type) {
	case nil:
		return false
	case bool:
		return value
	case string:
		return value != ""
	case int64:
		return value != 0
	case float64:
		return value != 0
	case []any:
		return len(value) > 0
	case *pyjson.Object:
		return value.Len() > 0
	}
	panic(fmt.Sprintf("truthy: unexpected %T", value))
}

func (r *Runner) releaseDeferred() {
	held, _ := must(r.Book.Fact(r.ctx, RunnerLane, DeferredFact, nil)).(*pyjson.Object)
	if held == nil || held.Len() == 0 {
		return
	}
	value, _ := held.Get("ids")
	ids := value.([]any)
	var lines []string
	for _, key := range ids {
		lines = append(lines, must(r.Book.Action(r.ctx, RunnerLane, key.(string))).Target)
	}
	must(r.Book.Edit(r.ctx, RunnerLane, func(tx *store.Transaction) (any, error) {
		for _, key := range ids {
			action, err := tx.Action(key.(string))
			if err != nil {
				return nil, err
			}
			if action.Status != "accepted" {
				continue
			}
			if _, err := tx.Start(key.(string), r.Now(), nil, ""); err != nil {
				return nil, err
			}
			if _, err := tx.Complete(key.(string), pyjson.ObjectOf("at", r.Book.Stamp(), "deferred", true), ""); err != nil {
				return nil, err
			}
		}
		facts, err := tx.Facts()
		if err != nil {
			return nil, err
		}
		_, err = facts.Pop(DeferredFact)
		return nil, err
	}))
	incident, _ := held.Get("incident")
	r.record("escalation:deferred:"+ids[0].(string), fmt.Sprintf("DEFERRED %d escalations held during %s: %s", len(lines), incident, strings.Join(lines, " | ")))
}

// TailFailed is Runner.tail_failed.
func (r *Runner) TailFailed(failure CciTailFailed) {
	r.Escalate("cci-tail:"+store.Stamp(r.Now())[:15], "CCI-TAIL-FAILED", "runner", failure.Error(), "", "")
}

// OrcaDown is Runner.orca_down.
func (r *Runner) OrcaDown(down OrcaUnreachable) {
	r.unreachablePasses++
	streak := must(r.Book.Edit(r.ctx, RunnerLane, func(tx *store.Transaction) (any, error) {
		facts, err := tx.Facts()
		if err != nil {
			return nil, err
		}
		return facts.SetDefault(UnreachableFact, pyjson.ObjectOf("since", r.Book.Stamp(), "error", down.Error()))
	})).(*pyjson.Object)
	since, _ := streak.Get("since")
	said, _ := streak.Get("error")
	r.Escalate("orca:"+since.(string), "ORCA-UNREACHABLE", "runner", fmt.Sprintf("%s; since %s the runner reads no dispatch as gone and fails no relay, and retries each pass with backoff up to %ds; ORCA-BACK follows when Orca answers", said, pacificClock(must(store.ParseStamp(since.(string)))), int(UnreachableWait.Seconds())), "", "")
}
