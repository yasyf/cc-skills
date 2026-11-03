package incident

import (
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"maps"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	routeIncident     = "incident"
	routeIncidentOpus = "incident-opus"
	effortXHigh       = "xhigh"
	authorityR16      = "R16"
	authorityAdopt    = "adopted"
	backupAfter       = 15 * time.Minute
	clockFallback     = "-opus"
	commsDeadline     = 2 * time.Minute
	syncDeadline      = 10 * time.Minute
	rebuildDeadline   = 45 * time.Minute
	DefaultInterval   = 30 * time.Second
	reconciled        = "reconciled:"
	maxAttempts       = 3
	green             = "passed"
	accountOpen       = "candidate"
	surfaceThread     = "thread"
)

var (
	terminalBuilds  = []string{"passed", "failed", "canceled", "skipped", "not_run"}
	terminalJobs    = append(slices.Clone(terminalBuilds), "broken", "timed_out", "expired")
	forbiddenModels = []string{"fable", "astra", "sol", "codex", "claude-fable-5-1", "gpt-6-astra"}
)

type RouteRefused struct {
	Reason string
}

func (e *RouteRefused) Error() string {
	return e.Reason
}

func assertRoute(role, model string) error {
	if (role == RoleFix || role == RoleEvidence) && model != routeIncident {
		return &RouteRefused{fmt.Sprintf("the %s lane runs on incident (gpt-6.1-sol, fast tier), never %s", role, model)}
	}
	if slices.Contains(forbiddenModels, model) {
		return &RouteRefused{"no incident lane runs on " + model}
	}
	return nil
}

func routes() map[string][]string {
	return map[string][]string{
		RoleFix:      {routeIncident, effortXHigh},
		RoleEvidence: {routeIncident, effortXHigh},
		RoleBackup:   {routeIncidentOpus, effortXHigh},
	}
}

func retryable(action *Action) bool {
	if action == nil {
		return true
	}
	return action.Status == ActionFailed && strings.HasPrefix(string(action.Reason), reconciled) && !strings.HasSuffix(action.ActionID, fmt.Sprintf("#%d", maxAttempts))
}

func short(text string, length int) string {
	return text[:min(length, len(text))]
}

func toMap(value any) (map[string]any, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	return out, json.Unmarshal(raw, &out)
}

func asInt(value any) int {
	switch number := value.(type) {
	case float64:
		return int(number)
	case int:
		return number
	case int64:
		return int(number)
	}
	return 0
}

func textOf(value any) string {
	held, _ := value.(string)
	return held
}

func dispatchOf(response map[string]any) string {
	for _, key := range []string{"dispatch", "seq"} {
		switch value := response[key].(type) {
		case string:
			if value != "" {
				return value
			}
		case int64:
			return strconv.FormatInt(value, 10)
		}
	}
	return ""
}

type Runner struct {
	Store      Store
	ID         string
	World      World
	Briefs     string
	Now        func() time.Time
	Log        *slog.Logger
	generation int64
}

func NewRunner(s Store, id string, world World, now func() time.Time, log *slog.Logger) (*Runner, error) {
	header, err := s.Header(id)
	if err != nil {
		return nil, err
	}
	return &Runner{Store: s, ID: id, World: world, Briefs: filepath.Join(s.Root, id), Now: now, Log: log, generation: header.OwnerGeneration}, nil
}

func (r *Runner) owned(fn func(*Tx) error) error {
	return r.Store.Owned(r.ID, r.generation, fn)
}

func (r *Runner) facts(keys ...string) (Facts, error) {
	return r.Store.Facts(r.ID, keys...)
}

func (r *Runner) latest(base string) (*Action, error) {
	found, err := r.Store.ActionFamily(r.ID, base)
	if err != nil || len(found) == 0 {
		return nil, err
	}
	return &found[len(found)-1], nil
}

func (r *Runner) nextID(base string) (string, error) {
	found, err := r.Store.ActionFamily(r.ID, base)
	if err != nil {
		return "", err
	}
	return familyKey(base, len(found)), nil
}

func familyKey(base string, count int) string {
	if count == 0 {
		return base
	}
	return fmt.Sprintf("%s#%d", base, count+1)
}

func (r *Runner) reached(name string) (Milestone, bool, error) {
	milestones, err := r.Store.Milestones(r.ID)
	if err != nil {
		return Milestone{}, false, err
	}
	entry, found := reached(milestones, name)
	return entry, found, nil
}

func (r *Runner) Advance(ctx context.Context) error {
	header, err := r.Store.Header(r.ID)
	if err != nil {
		return err
	}
	if header.Status == StatusClosed {
		return nil
	}
	for _, step := range []func(context.Context) error{
		r.markLost, r.intake, r.mechanism, r.dispatch, r.design, r.review, r.landing, r.notOurs,
		r.activate, r.confirmLive, r.recoverWork, r.settleComms, r.exhausted, r.deadlines, r.finish,
	} {
		if err := step(ctx); err != nil {
			return err
		}
	}
	return nil
}

func (r *Runner) Run(ctx context.Context, interval time.Duration) (Header, error) {
	for {
		if err := r.Advance(ctx); err != nil {
			if !isLost(err) {
				return Header{}, err
			}
			r.Log.Warn("a read lost its response; the next pass derives it again", "incident", r.ID, "error", err)
		}
		header, err := r.Store.Header(r.ID)
		if err != nil {
			return Header{}, err
		}
		if header.Status == StatusClosed {
			return header, nil
		}
		select {
		case <-ctx.Done():
			return Header{}, ctx.Err()
		case <-time.After(interval):
		}
	}
}

func (r *Runner) decide(ctx context.Context, key, text string) error {
	var fresh bool
	err := r.owned(func(tx *Tx) (err error) {
		fresh, err = tx.Decide(key, text, r.Now())
		return err
	})
	if err != nil || !fresh {
		return err
	}
	return r.World.Comms.AskRoot(ctx, r.ID+": "+text)
}

func (r *Runner) resolve(key string) error {
	return r.owned(func(tx *Tx) error { return tx.Resolve(key, r.Now()) })
}

func (r *Runner) milestone(ctx context.Context, name, text string, status Status, facts map[string]any) (bool, error) {
	var fresh bool
	err := r.owned(func(tx *Tx) (err error) {
		if fresh, err = tx.Milestone(name, text, r.Now()); err != nil {
			return err
		}
		if status != "" {
			tx.Status = status
		}
		for _, key := range slices.Sorted(maps.Keys(facts)) {
			if err := tx.SetFact(key, facts[key]); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil || !fresh {
		return false, err
	}
	if err := r.World.Comms.Report(ctx, fmt.Sprintf("%s %s: %s", r.ID, name, text)); err != nil {
		return true, err
	}
	return true, r.record(ctx, name, text)
}

func (r *Runner) keep(ctx context.Context, key string, call func() error) error {
	err := call()
	var failed *EffectFailed
	if errors.As(err, &failed) || isLost(err) {
		return r.decide(ctx, "record:"+key, fmt.Sprintf("cc-notes refused the incident record (%s): %v", key, err))
	}
	return err
}

func (r *Runner) record(ctx context.Context, name, text string) error {
	facts, err := r.facts("records", "landed_sha")
	if err != nil || facts.Records == nil {
		return err
	}
	records := *facts.Records
	entry := fmt.Sprintf("%s at %s: %s", name, pacificAt(r.Now()), text)
	if err := r.keep(ctx, name, func() error { return r.World.Records.Entry(ctx, records.Log, entry, "") }); err != nil {
		return err
	}
	verb, commit := "", ""
	switch name {
	case "mechanism":
		verb = "root-cause"
	case "landed":
		verb, commit = "fix", facts.LandedSHA
	case "not-ours":
		verb = "exonerate"
	default:
		return nil
	}
	return r.keep(ctx, name+"-verdict", func() error {
		return r.World.Records.Verdict(ctx, records.Investigation, verb, text, commit)
	})
}

func (r *Runner) openRecords(ctx context.Context) error {
	facts, err := r.facts("records", "kind", "target", "alert", "code_path", "onset", "thread")
	if err != nil || facts.Records != nil {
		return err
	}
	title := fmt.Sprintf("Incident %s: %s outage on %s", r.ID, facts.Kind, facts.Target)
	premise := fmt.Sprintf("%s reports a %s outage on %s since %s; thread %s.", facts.Alert, facts.Kind, facts.CodePath, pacific(facts.Onset), facts.Thread)
	records, err := r.World.Records.Open(ctx, r.ID, title, premise)
	var failed *EffectFailed
	if errors.As(err, &failed) || isLost(err) {
		return r.decide(ctx, "record:open", fmt.Sprintf("cc-notes refused the incident record: %v; lanes launch without record ids", err))
	}
	if err != nil {
		return err
	}
	return r.owned(func(tx *Tx) error { return tx.SetFact("records", records) })
}

func (r *Runner) effect(ctx context.Context, id, kind, target, authority string, deadline time.Duration, call func() (map[string]any, error)) (map[string]any, error) {
	now := r.Now()
	started := false
	err := r.owned(func(tx *Tx) error {
		action, err := tx.Accept(id, kind, target, authority, now)
		if err != nil || action.Status != ActionAccepted {
			return err
		}
		started = true
		_, err = tx.Start(id, now, deadline)
		return err
	})
	if err != nil || !started {
		return nil, err
	}
	response, err := call()
	var failed *EffectFailed
	switch {
	case errors.As(err, &failed):
		if err := r.owned(func(tx *Tx) error { _, err := tx.Fail(id, failed.Reason); return err }); err != nil {
			return nil, err
		}
		return nil, r.decide(ctx, "failed:"+id, fmt.Sprintf("%s %s failed: %s", kind, target, failed.Reason))
	case isLost(err):
		return nil, nil
	case err != nil:
		return nil, err
	}
	err = r.owned(func(tx *Tx) error { _, err := tx.Complete(id, response, dispatchOf(response)); return err })
	return response, err
}

func (r *Runner) exhausted(ctx context.Context) error {
	failed, err := r.Store.Actions(r.ID, "", ActionFailed)
	if err != nil {
		return err
	}
	suffix := fmt.Sprintf("#%d", maxAttempts)
	for _, action := range failed {
		if !strings.HasSuffix(action.ActionID, suffix) {
			continue
		}
		text := fmt.Sprintf("%s %s failed %d times: %s", action.Kind, action.Target, maxAttempts, action.Reason)
		if strings.HasPrefix(action.ActionID, "dispatch:"+RoleBackup) {
			text += fmt.Sprintf("; start %s in-process now, an Agent of type long-running:lane-ship on opus in fast mode", action.Target)
		}
		if err := r.decide(ctx, "exhausted:"+action.ActionID, text); err != nil {
			return err
		}
	}
	return nil
}

func (r *Runner) markLost(context.Context) error {
	return r.owned(func(tx *Tx) error { return tx.LoseStarted() })
}

func (r *Runner) grant(ctx context.Context, name string) (string, error) {
	facts, err := r.facts("grants")
	if err != nil {
		return "", err
	}
	if ref := facts.Grants[name]; ref != "" {
		return ref, r.resolve("grant:" + name)
	}
	text := fmt.Sprintf("needs the %s grant: lr incident grant --incident %s --name %s --ref <grant id>", name, r.ID, name)
	return "", r.decide(ctx, "grant:"+name, text)
}

func (r *Runner) comms(ctx context.Context, event string, facts map[string]any, surface string) error {
	base := "comms:" + event
	current, err := r.latest(base)
	if err != nil {
		return err
	}
	if current != nil && current.Status == ActionUnverifiable {
		seq, err := r.World.Comms.Find(ctx, event)
		if err != nil {
			return err
		}
		err = r.owned(func(tx *Tx) error {
			if seq != 0 {
				_, err := tx.Complete(current.ActionID, map[string]any{"seq": seq}, strconv.FormatInt(seq, 10))
				return err
			}
			_, err := tx.Fail(current.ActionID, fmt.Sprintf("%s no %s post on the bus", reconciled, event))
			return err
		})
		if err != nil {
			return err
		}
		if current, err = r.latest(base); err != nil {
			return err
		}
	}
	if !retryable(current) {
		return nil
	}
	ref, err := r.grant(ctx, surface)
	if err != nil || ref == "" {
		return err
	}
	thread, err := r.facts("thread")
	if err != nil {
		return err
	}
	payload := map[string]any{"surface": surface, "thread": thread.Thread}
	maps.Copy(payload, facts)
	id, err := r.nextID(base)
	if err != nil {
		return err
	}
	_, err = r.effect(ctx, id, "comms", surface+":"+event, ref, commsDeadline, func() (map[string]any, error) {
		seq, err := r.World.Comms.Event(ctx, event, payload, ref)
		return map[string]any{"seq": seq}, err
	})
	return err
}

func (r *Runner) intake(ctx context.Context) error {
	if err := r.openRecords(ctx); err != nil {
		return err
	}
	facts, err := r.facts("kind", "thread", "target", "onset", "adopted")
	if err != nil {
		return err
	}
	fresh, err := r.milestone(ctx, "opened", fmt.Sprintf("%s outage from %s; executor owns it", facts.Kind, facts.Thread), "", nil)
	if err != nil {
		return err
	}
	if fresh {
		text := fmt.Sprintf("fence %s from applies and deploys except %s", facts.Target, incidentLane(r.ID, facts, RoleFix))
		if err := r.World.Comms.Fence(ctx, text); err != nil {
			return err
		}
	}
	return r.comms(ctx, "ack", map[string]any{"reaction": "eyes", "onset": pacific(facts.Onset)}, surfaceThread)
}

func (r *Runner) mechanism(ctx context.Context) error {
	facts, err := r.facts("mechanism")
	if err != nil || facts.Mechanism == "" {
		return err
	}
	_, err = r.milestone(ctx, "mechanism", facts.Mechanism, "", nil)
	return err
}

func (r *Runner) laneFor(role string) (string, error) {
	facts, err := r.facts("adopted")
	if err != nil {
		return "", err
	}
	return incidentLane(r.ID, facts, role), nil
}

func (r *Runner) launch(ctx context.Context, role string) error {
	base := "dispatch:" + role
	facts, err := r.facts(briefFields...)
	if err != nil {
		return err
	}
	lane := incidentLane(r.ID, facts, role)
	current, err := r.latest(base)
	if err != nil {
		return err
	}
	if _, adopted := facts.Adopted[role]; current == nil && adopted {
		now := r.Now()
		receipt := map[string]any{"lane": lane, "adopted": true}
		return r.owned(func(tx *Tx) error {
			if _, err := tx.Accept(base, "dispatch", lane, authorityAdopt, now); err != nil {
				return err
			}
			if _, err := tx.Start(base, now, 0); err != nil {
				return err
			}
			if _, err := tx.Complete(base, receipt, ""); err != nil {
				return err
			}
			_, err := tx.Verify(base, receipt)
			return err
		})
	}
	if current != nil && current.Status == ActionUnverifiable {
		return r.settleLaunch(ctx, *current, lane)
	}
	if current != nil && current.Status == ActionCompleted {
		return r.owned(func(tx *Tx) error { _, err := tx.Verify(current.ActionID, current.Response); return err })
	}
	if !retryable(current) {
		return nil
	}
	route := facts.Routes[role]
	if len(route) != 2 {
		return fmt.Errorf("incident %s has no route for %s", r.ID, role)
	}
	if err := assertRoute(role, route[0]); err != nil {
		return err
	}
	brief, err := r.writeBrief(ctx, facts, role)
	if err != nil {
		return err
	}
	id, err := r.nextID(base)
	if err != nil {
		return err
	}
	response, err := r.effect(ctx, id, "dispatch", lane, authorityR16, 0, func() (map[string]any, error) {
		receipt, err := r.World.Orca.Launch(ctx, lane, route[0], route[1], brief)
		if err != nil {
			return nil, err
		}
		return toMap(receipt)
	})
	if err != nil || response == nil {
		return err
	}
	return r.owned(func(tx *Tx) error { _, err := tx.Verify(id, response); return err })
}

func (r *Runner) settleLaunch(ctx context.Context, current Action, lane string) error {
	receipt, err := r.World.Orca.Receipt(lane)
	if err != nil {
		return err
	}
	if receipt == nil {
		text := fmt.Sprintf("launch of %s lost its response and left no receipt; check Orca, then `lr incident note` its outcome", lane)
		return r.decide(ctx, "launch:"+current.ActionID, text)
	}
	response, err := toMap(receipt)
	if err != nil {
		return err
	}
	return r.owned(func(tx *Tx) error {
		if _, err := tx.Complete(current.ActionID, response, receipt.Dispatch); err != nil {
			return err
		}
		_, err := tx.Verify(current.ActionID, response)
		return err
	})
}

func (r *Runner) writeBrief(ctx context.Context, facts Facts, role string) (string, error) {
	path, err := writeBrief(r.ID, facts, role, r.Briefs)
	if err != nil || facts.Records == nil {
		return path, err
	}
	name := filepath.Base(path)
	log := facts.Records.Log
	return path, r.keep(ctx, "brief:"+strings.TrimSuffix(name, ".md"), func() error {
		return r.World.Records.Entry(ctx, log, "brief "+strings.TrimSuffix(name, briefSuffix), path)
	})
}

func (r *Runner) dispatch(ctx context.Context) error {
	facts, err := r.facts("repair_pr", "not_ours")
	if err != nil || facts.RepairPR != 0 {
		return err
	}
	if err := r.launch(ctx, RoleFix); err != nil {
		return err
	}
	if err := r.launch(ctx, RoleEvidence); err != nil {
		return err
	}
	fix, err := r.latest("dispatch:" + RoleFix)
	if err != nil || fix == nil || fix.Status != ActionVerified {
		return err
	}
	started, err := store.Parse(string(fix.StartedAt))
	if err != nil {
		return err
	}
	if facts.NotOurs != "" || r.Now().Sub(started) < backupAfter {
		return nil
	}
	backup, err := r.latest("dispatch:" + RoleBackup)
	if err != nil {
		return err
	}
	if retryable(backup) {
		lane, err := r.laneFor(RoleFix)
		if err != nil {
			return err
		}
		engaged, err := r.World.Orca.Engaged(ctx, lane+clockFallback, r.Now())
		if err != nil || engaged {
			return err
		}
	}
	return r.launch(ctx, RoleBackup)
}

func (r *Runner) design(ctx context.Context) error {
	facts, err := r.facts("rulings", "design_check", "design_ok", "repair_pr", "entry_point")
	if err != nil || len(facts.Rulings) == 0 {
		return err
	}
	if facts.DesignOK {
		if err := r.resolve("design"); err != nil {
			return err
		}
		check := facts.DesignCheck
		if check == "" {
			check = fmt.Sprintf("#%d as opened", facts.RepairPR)
		}
		_, err := r.milestone(ctx, "design", "confirmed: "+check, "", nil)
		return err
	}
	entry := facts.EntryPoint
	if entry == "" {
		entry = "the agreed entry point"
	}
	confirm := fmt.Sprintf("confirm it calls %s and meets %s with `lr incident note --incident %s --design-ok`, or redirect the fix lane", entry, strings.Join(facts.Rulings, "; "), r.ID)
	switch {
	case facts.DesignCheck != "":
		return r.decide(ctx, "design", fmt.Sprintf("design check before the PR: %s; %s", facts.DesignCheck, confirm))
	case facts.RepairPR != 0:
		return r.decide(ctx, "design", fmt.Sprintf("#%d opened with no design check; hold it, then %s", facts.RepairPR, confirm))
	}
	return nil
}

func (r *Runner) review(ctx context.Context) error {
	facts, err := r.facts("repair_pr", "repo", "mechanism", "human_review", "review_surface")
	if err != nil || facts.RepairPR == 0 {
		return err
	}
	url := fmt.Sprintf("https://github.com/%s/pull/%d", facts.Repo, facts.RepairPR)
	if _, err := r.milestone(ctx, "pr", url, "", nil); err != nil {
		return err
	}
	var mechanism any
	if facts.Mechanism != "" {
		mechanism = facts.Mechanism
	}
	if err := r.comms(ctx, "pr", map[string]any{"pr": url, "mechanism": mechanism}, surfaceThread); err != nil {
		return err
	}
	if !facts.HumanReview {
		return nil
	}
	return r.comms(ctx, "review-request", map[string]any{"pr": url, "why": "the reviewer is the broken pipeline, so its fix needs a human approval"}, facts.ReviewSurface)
}

func (r *Runner) landing(ctx context.Context) error {
	facts, err := r.facts("repair_pr", "landed_sha", "target")
	if err != nil || facts.RepairPR == 0 || facts.LandedSHA != "" {
		return err
	}
	pr := facts.RepairPR
	sha, err := r.World.GitHub.Landed(ctx, pr)
	if err != nil || sha == "" {
		return err
	}
	key := fmt.Sprintf("land:%d", pr)
	err = r.owned(func(tx *Tx) error {
		if err := tx.SetFact("landed_sha", sha); err != nil {
			return err
		}
		tx.Status = StatusActivationPending
		if _, err := tx.Accept(key, "land", fmt.Sprintf("#%d", pr), "", r.Now()); err != nil {
			return err
		}
		_, err := tx.Verify(key, map[string]any{"sha": sha})
		return err
	})
	if err != nil {
		return err
	}
	text := fmt.Sprintf("#%d landed as %s; not live until %s is verified", pr, short(sha, 10), facts.Target)
	if _, err := r.milestone(ctx, "landed", text, "", nil); err != nil {
		return err
	}
	return r.comms(ctx, "landed", map[string]any{"pr": pr, "sha": sha, "live": false}, surfaceThread)
}

func (r *Runner) configMatches(ctx context.Context, tree string) (map[string]any, error) {
	facts, err := r.facts("expect_config")
	if err != nil {
		return nil, err
	}
	drifted, err := r.World.Activation.Drifted(ctx, tree)
	if err != nil || drifted {
		return nil, err
	}
	configuration, err := r.World.Buildkite.Configuration(ctx)
	if err != nil {
		return nil, err
	}
	if facts.ExpectConfig != "" && !strings.Contains(configuration, facts.ExpectConfig) {
		return nil, nil
	}
	digest := sha256.Sum256([]byte(configuration))
	var expected any
	if facts.ExpectConfig != "" {
		expected = facts.ExpectConfig
	}
	return map[string]any{"digest": hex.EncodeToString(digest[:]), "expected": expected, "at": store.Stamp(r.Now())}, nil
}

func (r *Runner) activate(ctx context.Context) error {
	facts, err := r.facts("pipeline", "landed_sha")
	if err != nil || facts.Pipeline == "" || facts.LandedSHA == "" {
		return err
	}
	_, done, err := r.reached("activated")
	if err != nil || done {
		return err
	}
	err = r.activateAt(ctx, facts.Pipeline, facts.LandedSHA)
	var failed *EffectFailed
	if errors.As(err, &failed) {
		return r.decide(ctx, "activation:"+facts.LandedSHA, "activation blocked: "+failed.Reason)
	}
	return err
}

func (r *Runner) activateAt(ctx context.Context, pipeline, sha string) error {
	base := fmt.Sprintf("sync:%s@%s", pipeline, short(sha, 12))
	tree, err := r.World.Activation.Prepare(ctx, sha)
	if err != nil {
		return err
	}
	current, err := r.latest(base)
	if err != nil {
		return err
	}
	if current != nil {
		switch current.Status {
		case ActionVerified:
			return r.activated(ctx, current.ActionID, string(current.AuthorityRef), current.VerificationReceipt)
		case ActionCompleted, ActionUnverifiable:
			return r.readBack(ctx, *current, tree)
		}
	}
	if !retryable(current) {
		return nil
	}
	receipt, err := r.configMatches(ctx, tree)
	if err != nil {
		return err
	}
	id, err := r.nextID(base)
	if err != nil {
		return err
	}
	if receipt != nil {
		return r.activated(ctx, id, "", receipt)
	}
	ref, err := r.grant(ctx, "sync")
	if err != nil || ref == "" {
		return err
	}
	response, err := r.effect(ctx, id, "sync", pipeline, ref, syncDeadline, func() (map[string]any, error) {
		applied, err := r.World.Activation.Apply(ctx, tree)
		return map[string]any{"applied": applied}, err
	})
	if err != nil || response == nil {
		return err
	}
	action, err := r.Store.Action(r.ID, id)
	if err != nil {
		return err
	}
	return r.readBack(ctx, action, tree)
}

func (r *Runner) readBack(ctx context.Context, action Action, tree string) error {
	receipt, err := r.configMatches(ctx, tree)
	if err != nil {
		return err
	}
	if receipt != nil {
		return r.activated(ctx, action.ActionID, string(action.AuthorityRef), receipt)
	}
	if action.Status == ActionUnverifiable {
		return r.owned(func(tx *Tx) error {
			_, err := tx.Fail(action.ActionID, reconciled+" stored configuration still drifts")
			return err
		})
	}
	return r.decide(ctx, "activation:"+action.ActionID, fmt.Sprintf("applied %s but the read-back still drifts", action.Target))
}

func (r *Runner) activated(ctx context.Context, id, authority string, receipt map[string]any) error {
	var fresh bool
	var message string
	err := r.owned(func(tx *Tx) error {
		facts, err := tx.Facts("pipeline", "activated_at")
		if err != nil {
			return err
		}
		action, err := tx.Accept(id, "sync", facts.Pipeline, authority, r.Now())
		if err != nil {
			return err
		}
		if action.Status != ActionVerified {
			if _, err := tx.Verify(id, receipt); err != nil {
				return err
			}
		}
		tx.Status = StatusRecovering
		if facts.ActivatedAt == "" {
			if err := tx.SetFact("activated_at", receipt["at"]); err != nil {
				return err
			}
		}
		message = fmt.Sprintf("%s configuration matches the landed tree (sha256 %s)", facts.Pipeline, short(textOf(receipt["digest"]), 12))
		fresh, err = tx.Milestone("activated", message, r.Now())
		return err
	})
	if err != nil || !fresh {
		return err
	}
	if err := r.World.Comms.Report(ctx, fmt.Sprintf("%s activated: %s", r.ID, message)); err != nil {
		return err
	}
	return r.record(ctx, "activated", message)
}

func (r *Runner) unfinished(ctx context.Context) (bool, error) {
	facts, err := r.facts("onset", "activated_at")
	if err != nil {
		return false, err
	}
	builds, err := r.World.Buildkite.Unfinished(ctx, facts.Onset, facts.ActivatedAt)
	return len(builds) > 0, err
}

type accountEntry struct {
	PR      int
	Account string
	Source  int
	Head    string
}

func (e accountEntry) rebuildKey() string {
	return fmt.Sprintf("rebuild:%d@%s", e.PR, short(e.Head, 12))
}

func (r *Runner) inventory(ctx context.Context) ([]accountEntry, error) {
	facts, err := r.facts("onset", "activated_at")
	if err != nil {
		return nil, err
	}
	failed, err := r.World.Buildkite.Failed(ctx, facts.Onset, facts.ActivatedAt)
	if err != nil {
		return nil, err
	}
	byPR := map[int][]Build{}
	for _, build := range failed {
		if build.PullRequest == nil {
			continue
		}
		pr, err := strconv.Atoi(build.PullRequest.ID)
		if err != nil {
			return nil, fmt.Errorf("build %d pull request id: %w", build.Number, err)
		}
		byPR[pr] = append(byPR[pr], build)
	}
	numbers := slices.Sorted(maps.Keys(byPR))
	views, err := r.World.GitHub.PRs(ctx, numbers)
	if err != nil {
		return nil, err
	}
	entries := make([]accountEntry, 0, len(numbers))
	byNumber := func(a, b Build) int { return cmp.Compare(a.Number, b.Number) }
	for _, pr := range numbers {
		newest := slices.MaxFunc(byPR[pr], byNumber).Number
		view, known := views[pr]
		if !known || view.State != "OPEN" {
			entries = append(entries, accountEntry{PR: pr, Account: "closed", Source: newest})
			continue
		}
		var atHead []Build
		for _, build := range byPR[pr] {
			if build.Commit == view.Head {
				atHead = append(atHead, build)
			}
		}
		if len(atHead) == 0 {
			entries = append(entries, accountEntry{PR: pr, Account: "superseded", Source: newest})
			continue
		}
		entries = append(entries, accountEntry{PR: pr, Account: accountOpen, Source: slices.MaxFunc(atHead, byNumber).Number, Head: view.Head})
	}
	return entries, nil
}

func (r *Runner) recoverWork(ctx context.Context) error {
	if _, activated, err := r.reached("activated"); err != nil || !activated {
		return err
	}
	if _, recovered, err := r.reached("recovered"); err != nil || recovered {
		return err
	}
	entries, err := r.inventory(ctx)
	if err != nil {
		return err
	}
	if err := r.reconcileRebuilds(ctx); err != nil {
		return err
	}
	facts, err := r.facts("canary", "pipeline")
	if err != nil {
		return err
	}
	candidates := map[string]accountEntry{}
	var pending []accountEntry
	for _, entry := range entries {
		if entry.Account != accountOpen {
			continue
		}
		candidates[entry.rebuildKey()] = entry
		latest, err := r.latest(entry.rebuildKey())
		if err != nil {
			return err
		}
		if retryable(latest) {
			pending = append(pending, entry)
		}
	}
	canary := facts.Canary
	if canary != "" {
		latest, err := r.latest(canary)
		if err != nil {
			return err
		}
		if _, known := candidates[canary]; !known && retryable(latest) {
			canary = ""
		}
	}
	if canary == "" {
		return r.pickCanary(ctx, facts.Pipeline, entries, pending)
	}
	latest, err := r.latest(canary)
	if err != nil {
		return err
	}
	if retryable(latest) {
		return r.rebuild(ctx, candidates[canary])
	}
	passed, err := r.canaryPassed(ctx, canary)
	if err != nil || !passed {
		return err
	}
	for _, entry := range pending {
		if err := r.rebuild(ctx, entry); err != nil {
			return err
		}
	}
	busy, err := r.unfinished(ctx)
	if err != nil || busy {
		return err
	}
	return r.account(ctx, entries)
}

func (r *Runner) pickCanary(ctx context.Context, pipeline string, entries, pending []accountEntry) error {
	if len(pending) > 0 {
		ref, err := r.grant(ctx, "rebuild")
		if err != nil || ref == "" {
			return err
		}
		if err := r.owned(func(tx *Tx) error { return tx.SetFact("canary", pending[0].rebuildKey()) }); err != nil {
			return err
		}
		return r.rebuild(ctx, pending[0])
	}
	busy, err := r.unfinished(ctx)
	if err != nil || busy {
		return err
	}
	if _, err := r.milestone(ctx, "live", pipeline+" configuration verified; no failed work to re-run", "", nil); err != nil {
		return err
	}
	live, _, err := r.reached("live")
	if err != nil {
		return err
	}
	if err := r.comms(ctx, "live", map[string]any{"canary": nil, "at": pacific(live.At)}, surfaceThread); err != nil {
		return err
	}
	return r.account(ctx, entries)
}

func (r *Runner) rebuild(ctx context.Context, entry accountEntry) error {
	ref, err := r.grant(ctx, "rebuild")
	if err != nil || ref == "" {
		return err
	}
	key := entry.rebuildKey()
	var id string
	err = r.owned(func(tx *Tx) error {
		if err := tx.SetChild("sources", key, entry.Source); err != nil {
			return err
		}
		count, err := tx.ActionFamilySize(key)
		id = familyKey(key, count)
		return err
	})
	if err != nil {
		return err
	}
	target := fmt.Sprintf("#%d@%s", entry.PR, short(entry.Head, 10))
	_, err = r.effect(ctx, id, "rebuild", target, ref, rebuildDeadline, func() (map[string]any, error) {
		build, err := r.World.Buildkite.Rebuild(ctx, entry.Source)
		if err != nil {
			return nil, err
		}
		return rebuildResponse(build, entry.PR, entry.Source), nil
	})
	return err
}

func rebuildResponse(build Build, pr, source int) map[string]any {
	var url any
	if build.WebURL != "" {
		url = build.WebURL
	}
	return map[string]any{"build": build.Number, "pr": pr, "source": source, "url": url}
}

func (r *Runner) reconcileRebuilds(ctx context.Context) error {
	stale, err := r.Store.Actions(r.ID, "rebuild", ActionCompleted, ActionUnverifiable)
	if err != nil || len(stale) == 0 {
		return err
	}
	facts, err := r.facts("activated_at")
	if err != nil {
		return err
	}
	builds, err := r.World.Buildkite.Since(ctx, facts.ActivatedAt)
	if err != nil {
		return err
	}
	slices.SortFunc(builds, func(a, b Build) int { return cmp.Compare(a.Number, b.Number) })
	byNumber := make(map[int]Build, len(builds))
	bySource := map[int]Build{}
	for _, build := range builds {
		byNumber[build.Number] = build
		if build.RebuiltFrom != nil {
			if _, seen := bySource[build.RebuiltFrom.Number]; !seen {
				bySource[build.RebuiltFrom.Number] = build
			}
		}
	}
	return r.owned(func(tx *Tx) error {
		held, err := tx.Facts("sources")
		if err != nil {
			return err
		}
		for _, old := range stale {
			action, err := tx.Action(old.ActionID)
			if err != nil {
				return err
			}
			if action.Status == ActionUnverifiable {
				base, _, _ := strings.Cut(action.ActionID, "#")
				source := held.Sources[base]
				found, known := bySource[source]
				if !known {
					if _, err := tx.Fail(action.ActionID, fmt.Sprintf("%s no rebuild of build %d", reconciled, source)); err != nil {
						return err
					}
					continue
				}
				pr, err := rebuildPR(action.ActionID)
				if err != nil {
					return err
				}
				if action, err = tx.Complete(action.ActionID, rebuildResponse(found, pr, source), ""); err != nil {
					return err
				}
			}
			build, known := byNumber[asInt(action.Response["build"])]
			if known && slices.Contains(terminalBuilds, build.State) {
				receipt := map[string]any{"build": build.Number, "state": build.State, "jobs": jobStates(build)}
				if _, err := tx.Verify(action.ActionID, receipt); err != nil {
					return err
				}
			}
		}
		return nil
	})
}

func rebuildPR(id string) (int, error) {
	_, rest, _ := strings.Cut(id, ":")
	number, _, _ := strings.Cut(rest, "@")
	return strconv.Atoi(number)
}

func jobStates(build Build) map[string]string {
	states := make(map[string]string, len(build.Jobs))
	for _, job := range build.Jobs {
		states[cmp.Or(job.Name, job.StepKey, job.ID)] = job.State
	}
	return states
}

func (r *Runner) canaryPassed(ctx context.Context, key string) (bool, error) {
	facts, err := r.facts("canary_job", "activated_at")
	if err != nil {
		return false, err
	}
	action, err := r.latest(key)
	if err != nil || action == nil {
		return false, err
	}
	switch action.Status {
	case ActionAccepted, ActionStarted, ActionUnverifiable:
		return false, nil
	case ActionFailed:
		return false, r.decide(ctx, "canary:"+key, fmt.Sprintf("canary rebuild %s failed to start: %s", key, action.Reason))
	}
	build, err := r.findBuild(ctx, facts.ActivatedAt, asInt(action.Response["build"]))
	if err != nil {
		return false, err
	}
	index := slices.IndexFunc(build.Jobs, func(job Job) bool { return strings.Contains(job.Name, facts.CanaryJob) })
	if index < 0 {
		if slices.Contains(terminalBuilds, build.State) {
			return false, r.decide(ctx, "canary:"+key, fmt.Sprintf("canary build %d finished %s without a %s job", build.Number, build.State, facts.CanaryJob))
		}
		return false, nil
	}
	job := build.Jobs[index]
	if !slices.Contains(terminalJobs, job.State) {
		return false, nil
	}
	if job.State != green {
		return false, r.decide(ctx, "canary:"+key, fmt.Sprintf("canary build %d %s is %s: the landed fix is not working", build.Number, facts.CanaryJob, job.State))
	}
	var link any = build.Number
	if build.WebURL != "" {
		link = build.WebURL
	}
	if _, err := r.milestone(ctx, "live", fmt.Sprintf("canary %v passed %s", link, facts.CanaryJob), "", nil); err != nil {
		return false, err
	}
	live, _, err := r.reached("live")
	if err != nil {
		return false, err
	}
	return true, r.comms(ctx, "live", map[string]any{"canary": link, "at": pacific(live.At)}, surfaceThread)
}

func (r *Runner) findBuild(ctx context.Context, since string, number int) (Build, error) {
	builds, err := r.World.Buildkite.Since(ctx, since)
	if err != nil {
		return Build{}, err
	}
	index := slices.IndexFunc(builds, func(build Build) bool { return build.Number == number })
	if index < 0 {
		return Build{}, fmt.Errorf("build %d is not among the builds since %s", number, since)
	}
	return builds[index], nil
}

func (r *Runner) notOurs(ctx context.Context) error {
	facts, err := r.facts("not_ours")
	if err != nil || facts.NotOurs == "" {
		return err
	}
	header, err := r.Store.Header(r.ID)
	if err != nil || header.Status == StatusRecovered || header.Status == StatusClosed {
		return err
	}
	_, err = r.milestone(ctx, "not-ours", facts.NotOurs, StatusRecovered, map[string]any{"accounting": map[string]any{"not_ours": facts.NotOurs}})
	return err
}

func (r *Runner) confirmLive(ctx context.Context) error {
	facts, err := r.facts("pipeline", "not_ours", "live")
	if err != nil || facts.Pipeline != "" || facts.NotOurs != "" || facts.Live == "" {
		return err
	}
	if _, err := r.milestone(ctx, "live", facts.Live, "", nil); err != nil {
		return err
	}
	if _, err := r.milestone(ctx, "recovered", facts.Live, StatusRecovered, map[string]any{"accounting": map[string]any{"live": facts.Live}}); err != nil {
		return err
	}
	live, _, err := r.reached("live")
	if err != nil {
		return err
	}
	return r.comms(ctx, "live", map[string]any{"evidence": facts.Live, "at": pacific(live.At)}, surfaceThread)
}

func (r *Runner) account(ctx context.Context, entries []accountEntry) error {
	rebuilds, err := r.Store.Actions(r.ID, "rebuild")
	if err != nil {
		return err
	}
	for _, action := range rebuilds {
		if !action.terminal() {
			return nil
		}
	}
	skipped := []map[string]any{}
	for _, entry := range entries {
		if entry.Account != accountOpen {
			skipped = append(skipped, map[string]any{"pr": entry.PR, "account": entry.Account})
			continue
		}
		latest, err := r.latest(entry.rebuildKey())
		if err != nil {
			return err
		}
		if retryable(latest) {
			return nil
		}
	}
	rerun, passed := 0, 0
	red, unlaunched := []map[string]any{}, []map[string]any{}
	for _, action := range rebuilds {
		if action.Status == ActionFailed {
			if !retryable(&action) {
				unlaunched = append(unlaunched, map[string]any{"key": action.ActionID, "reason": string(action.Reason)})
			}
			continue
		}
		rerun++
		state := textOf(action.VerificationReceipt["state"])
		if state == green {
			passed++
			continue
		}
		red = append(red, map[string]any{"pr": action.Response["pr"], "build": action.Response["build"], "state": state})
	}
	accounting := map[string]any{"rerun": rerun, "green": passed, "red": red, "unlaunched": unlaunched, "skipped": skipped}
	summary := fmt.Sprintf("re-run %d, green %d, red %d", rerun, passed, len(red))
	_, err = r.milestone(ctx, "recovered", summary, StatusRecovered, map[string]any{"accounting": accounting})
	return err
}

func (r *Runner) settleComms(ctx context.Context) error {
	completed, err := r.Store.Actions(r.ID, "comms", ActionCompleted)
	if err != nil {
		return err
	}
	for _, action := range completed {
		seq, err := strconv.ParseInt(string(action.DispatchID), 10, 64)
		if err != nil {
			return fmt.Errorf("comms action %s dispatch: %w", action.ActionID, err)
		}
		ts, err := r.World.Comms.Posted(ctx, seq)
		if err != nil {
			return err
		}
		if ts == "" {
			continue
		}
		err = r.owned(func(tx *Tx) error { _, err := tx.Verify(action.ActionID, map[string]any{"ts": ts}); return err })
		if err != nil {
			return err
		}
	}
	return nil
}

func (r *Runner) deadlines(ctx context.Context) error {
	open, err := r.Store.Actions(r.ID, "", openStatuses...)
	if err != nil {
		return err
	}
	for _, action := range open {
		overdue, err := action.Overdue(r.Now())
		if err != nil {
			return err
		}
		if !overdue {
			continue
		}
		started, err := store.Parse(string(action.StartedAt))
		if err != nil {
			return err
		}
		deadline, err := store.Parse(string(action.Deadline))
		if err != nil {
			return err
		}
		message := fmt.Sprintf("%s %s is overdue by %d min, started %d min ago, still %s", action.Kind, action.Target, int(r.Now().Sub(deadline).Minutes()), int(r.Now().Sub(started).Minutes()), action.Status)
		if err := r.decide(ctx, "overdue:"+action.ActionID, message); err != nil {
			return err
		}
	}
	return nil
}

func (r *Runner) finish(ctx context.Context) error {
	header, err := r.Store.Header(r.ID)
	if err != nil || header.Status != StatusRecovered {
		return err
	}
	facts, err := r.facts("accounting", "repo")
	if err != nil {
		return err
	}
	final := maps.Clone(facts.Accounting)
	if err := linkRed(final, facts.Repo); err != nil {
		return err
	}
	live, wasLive, err := r.reached("live")
	if err != nil {
		return err
	}
	if wasLive {
		final["live_at"] = pacific(live.At)
	}
	if err := r.comms(ctx, "recovered", final, surfaceThread); err != nil {
		return err
	}
	last, err := r.latest("comms:recovered")
	if err != nil || last == nil || last.Status != ActionVerified {
		return err
	}
	if err := r.owned(func(tx *Tx) error { tx.Status = StatusClosed; return nil }); err != nil {
		return err
	}
	_, err = r.milestone(ctx, "closed", "final reply posted ts="+textOf(last.VerificationReceipt["ts"]), "", nil)
	return err
}

func linkRed(accounting map[string]any, repo string) error {
	held, ok := accounting["red"]
	if !ok {
		return nil
	}
	raw, err := json.Marshal(held)
	if err != nil {
		return err
	}
	var red []struct {
		PR    int    `json:"pr"`
		State string `json:"state"`
	}
	if err := json.Unmarshal(raw, &red); err != nil {
		return err
	}
	linked := make([]map[string]any, len(red))
	for i, entry := range red {
		linked[i] = map[string]any{"pr": fmt.Sprintf("https://github.com/%s/pull/%d", repo, entry.PR), "state": entry.State}
	}
	accounting["red"] = linked
	return nil
}
