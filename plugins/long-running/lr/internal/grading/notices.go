package grading

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const (
	beatsDir         = "sprite-beats"
	settledDir       = "sprite-settled"
	spriteBeatEvery  = 10 * time.Minute
	spriteStalledFor = 30 * time.Minute
	causeChars       = 200
	causeHash        = 12
	watchdogLane     = "drive-watchdog"
	kindState        = "state"
	kindReport       = "report"
	appealsDesk      = "grader-appeals"
	readerVerdict    = "reader"
)

var toolingCause = regexp.MustCompile(`[0-9a-f]{7,}|\d+|/\S+`)

func (p *JobPool) finished(ctx context.Context, ref workerRef, directory string, state jobState) error {
	switch state {
	case jobLost:
		if err := p.tellLost(ctx, ref.lane, directory); err != nil {
			return err
		}
		if ref.box != localBox {
			return p.stamp(failuresDir, ref.box)
		}
	case jobStalled:
		if err := p.tellStalled(ctx, ref.lane, directory); err != nil {
			return err
		}
		if ref.box != localBox {
			return p.stamp(failuresDir, ref.box)
		}
	case jobSettled, jobSkipped:
		if ref.lane == grader.FinderLane && ungraded(directory) {
			if err := p.queueUngraded(directory); err != nil {
				return err
			}
		}
		if ref.box != localBox {
			return p.stamp(settledDir, ref.box)
		}
	case jobFailed:
		if exists(filepath.Join(directory, grader.CancelledFile)) {
			return nil
		}
		return p.toolingFailed(ctx, ref.lane, directory)
	case jobRunning:
	}
	return nil
}

func failureOf(directory string) string {
	data, err := os.ReadFile(filepath.Join(directory, grader.ResultFile))
	if err != nil {
		return "the job exited and wrote no result"
	}
	var outcome struct {
		Error   string `json:"error"`
		Skipped string `json:"skipped"`
	}
	if err := json.Unmarshal(data, &outcome); err != nil || outcome.Error+outcome.Skipped == "" {
		return "its result.json is empty or does not parse"
	}
	return outcome.Error + outcome.Skipped
}

func jobNames(directory string) (grader.Job, error) {
	data, err := os.ReadFile(filepath.Join(directory, grader.JobFile))
	if err != nil {
		return grader.Job{}, err
	}
	var spec grader.Job
	err = json.Unmarshal(data, &spec)
	return spec, err
}

func (p *JobPool) tellLost(ctx context.Context, lane, directory string) error {
	spec, err := jobNames(directory)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	desk := graderOf(lane)
	to := spec.Lane
	if to == "" {
		to = rootLane
	}
	id := "grader-lost:" + desk + ":" + spec.PR + ":" + spec.Head
	text := fmt.Sprintf("GRADER-LOST #%s %s: its %s pass ended with no grade: %s; the pool runs it again, up to %d attempts in all, and the head has no %s grade until one completes", spec.PR, short(spec.Head), desk, squeezed(failureOf(directory)), maxAttempts, desk)
	return p.notice(ctx, client.Draft{Lane: runnerLane, Kind: kindNote, Text: text, To: []string{to}, Topic: id, ID: id, PRs: []string{spec.Repo + "#" + spec.PR}})
}

func (p *JobPool) tellStalled(ctx context.Context, lane, directory string) error {
	spec, err := jobNames(directory)
	if err != nil {
		return err
	}
	desk := graderOf(lane)
	to := spec.Lane
	if to == "" {
		to = rootLane
	}
	failed := "grader-failed:" + desk + ":" + spec.PR + ":" + spec.Head
	text := fmt.Sprintf("GRADER-FAILED #%s %s: its %s pass died: %s; it runs again at once, on another Sprite when one is free, up to %d attempts, and the head has no %s grade until one completes",
		spec.PR, short(spec.Head), desk, squeezed(failureOf(directory)), maxAttempts, desk)
	return p.notice(ctx, client.Draft{Lane: runnerLane, Kind: kindNote, Text: text, To: []string{to}, Topic: failed, ID: failed + ":" + filepath.Base(directory), PRs: []string{spec.Repo + "#" + spec.PR}})
}

func (p *JobPool) toolingFailed(ctx context.Context, lane, directory string) error {
	spec, err := jobNames(directory)
	if err != nil {
		return err
	}
	desk := graderOf(lane)
	why := failureOf(directory)
	to := spec.Lane
	if to == "" {
		to = rootLane
	}
	failed := "grader-failed:" + desk + ":" + spec.PR + ":" + spec.Head
	told := fmt.Sprintf("GRADER-FAILED #%s %s: its %s pass died: %s; it runs again after %d min, up to %d attempts, and the head has no %s grade until one completes",
		spec.PR, short(spec.Head), desk, squeezed(why), int(failedHold.Minutes()), maxAttempts, desk)
	if err := p.notice(ctx, client.Draft{Lane: runnerLane, Kind: kindNote, Text: told, To: []string{to}, Topic: failed, ID: failed, PRs: []string{spec.Repo + "#" + spec.PR}}); err != nil {
		return err
	}
	if p.tooling == "" {
		return nil
	}
	cause := toolingCause.ReplaceAllString(why, clipMark)
	cause = string([]rune(cause)[:min(causeChars, len([]rune(cause)))])
	sum := sha256.Sum256([]byte(cause))
	subject := "grader-tooling:" + hex.EncodeToString(sum[:])[:causeHash] + ":" + p.Now().UTC().Format("2006010215")
	text := fmt.Sprintf("GRADER-TOOLING %s %s: %s passes die on a tooling error, first on #%s %s: %s; each retries after %d min, up to %d attempts, and each PR's lane hears at its first failure",
		subject, desk, desk, spec.PR, short(spec.Head), squeezed(why), int(failedHold.Minutes()), maxAttempts)
	return p.notice(ctx, client.Draft{Lane: runnerLane, Kind: kindReport, Text: text, To: []string{p.tooling}, Topic: subject, ID: subject})
}

func (p *JobPool) exhausted(ctx context.Context, d desk, directory string) error {
	spec, err := jobNames(directory)
	if err != nil {
		return err
	}
	logPath := filepath.Join(directory, logFile)
	why := squeezed(failureOf(directory))
	head := short(spec.Head)
	if d.lane == grader.FinderLane {
		subject := "finder:" + spec.PR + ":" + spec.Head
		text := fmt.Sprintf("FINDER-FAILED %s %s: #%s %s: the finder desk's pass failed on the last of its %d attempts: %s; its log is %s; the coordinator asks %s `finder: whole` on #%s to run it again, since only its records grade a head",
			subject, spec.Lane, spec.PR, head, maxAttempts, why, logPath, grader.FinderLane, spec.PR)
		return p.notice(ctx, client.Draft{Lane: runnerLane, Kind: kindDefect, Text: text, To: []string{rootLane}, Topic: subject, ID: subject})
	}
	readers := slices.DeleteFunc(slices.Clone([]string(p.landing.FidelityLane)), func(lane string) bool { return lane == grader.FidelityLane })
	if len(readers) == 0 {
		readers = []string{rootLane}
	}
	subject := "fidelity:" + spec.PR + ":" + spec.Head
	text := fmt.Sprintf("FIDELITY-FAILED %s %s: #%s %s: the fidelity pass failed on the last of its %d attempts: %s; its log is %s; a fidelity desk grades it by hand, and the next head record for #%s queues the pass again",
		subject, spec.Lane, spec.PR, head, maxAttempts, why, logPath, spec.PR)
	return p.notice(ctx, client.Draft{Lane: runnerLane, Kind: kindDefect, Text: text, To: readers, Topic: subject, ID: subject})
}

func (p *JobPool) tellProofSkip(ctx context.Context, facts machine.Facts, pr string, live livePR) error {
	said := proofTitle.FindString(live.Title)
	if line := proofBody.FindString(live.Body); line != "" {
		said = line
	}
	to := facts.Lane
	if to == "" {
		to = rootLane
	}
	id := "grader-skipped:" + facts.Repo + "#" + pr + ":" + facts.Head
	text := fmt.Sprintf("GRADER-SKIPPED #%s %s: no finder or fidelity pass grades it, because its title or body marks it a proof PR, which never merges (%q); a real change drops those words", pr, short(facts.Head), said)
	if err := p.notice(ctx, client.Draft{Lane: runnerLane, Kind: kindNote, Text: text, To: []string{to}, Topic: "grader-skipped:" + pr, ID: id, PRs: []string{facts.Repo + "#" + pr}}); err != nil {
		return err
	}
	return &Stopped{"its title or body marks it a proof PR, which never merges"}
}

func (p *JobPool) TellStopped(ctx context.Context, facts machine.Facts, why string) error {
	pr := strconv.Itoa(facts.Number)
	to := []string{appealsDesk}
	for _, lane := range []string{p.tooling, facts.Lane} {
		if lane != "" && !slices.Contains(to, lane) {
			to = append(to, lane)
		}
	}
	text := fmt.Sprintf("GRADE-STOPPED #%s %s: no grader pass is left to run at this head, so its grade item closes and a verdict item opens for its lane. %s. A grade posted at this head, a desk's read or `finder: whole`, clears it, and so does root's `fidelity-override #%s at %s: <seq>` or `finder-override #%s at %s: <seq>` for the grader that stopped; a new head gets new passes.", pr, short(facts.Head), squeezed(why), pr, short(facts.Head), pr, short(facts.Head))
	id := "grade-stopped:" + facts.Repo + "#" + pr + ":" + facts.Head
	attrs := map[string]string{bus.GradeStoppedWhyAttr: why, bus.GradeStoppedHeadAttr: facts.Head}
	return p.notice(ctx, client.Draft{Lane: runnerLane, Kind: kindReport, Text: text, To: to, Topic: id, ID: id, PRs: []string{facts.Repo + "#" + pr}, Attrs: attrs})
}

func squeezed(text string) string {
	return strings.Join(strings.Fields(text), " ")
}

func (p *JobPool) notice(ctx context.Context, draft client.Draft) error {
	draft.Drive = p.drive
	if _, err := client.Post(ctx, draft); err != nil {
		p.Log.Warn("a grader notice did not post; the next pass tries again", "topic", draft.Topic, "error", err)
	}
	return nil
}

func (p *JobPool) postBeats(ctx context.Context) error {
	var listed []string
	for _, grading := range []string{GraderFinder, GraderFidelity} {
		listed = append(listed, p.desks[grading].spec.Sprite...)
	}
	listed = unique(listed)
	if len(listed) == 0 {
		return nil
	}
	workers, err := p.Workers()
	if err != nil {
		return err
	}
	for _, sprite := range listed {
		beaten, err := p.stampedWithin(beatsDir, sprite, spriteBeatEvery)
		if err != nil {
			return err
		}
		if beaten {
			continue
		}
		names := make([]string, 0, len(workers))
		for _, worker := range workers {
			if refOf(worker.Name).box == sprite {
				names = append(names, worker.Name)
			}
		}
		if err := p.beat(ctx, sprite, names); err != nil {
			return err
		}
	}
	return nil
}

func (p *JobPool) beat(ctx context.Context, sprite string, names []string) error {
	finders, fidelities := 0, 0
	var oldest time.Time
	for _, name := range names {
		ref := refOf(name)
		if ref.lane == grader.FinderLane {
			finders++
		} else {
			fidelities++
		}
		info, err := os.Stat(filepath.Join(p.root, ref.lane, ref.base, pidFile))
		if err == nil && (oldest.IsZero() || info.ModTime().Before(oldest)) {
			oldest = info.ModTime()
		}
	}
	age := "none running"
	if !oldest.IsZero() {
		age = "oldest " + strconv.Itoa(int(p.Now().Sub(oldest).Minutes())) + " min"
	}
	settled, err := p.stampedWithin(settledDir, sprite, spriteStalledFor)
	if err != nil {
		return err
	}
	topic := "sprite:" + sprite
	draft := client.Draft{Drive: p.drive, Lane: runnerLane, Kind: kindState, To: []string{watchdogLane + "@" + p.drive}, Topic: topic, Text: fmt.Sprintf("sprite %s: %d finder and %d fidelity passes, %s", sprite, finders, fidelities, age)}
	if !oldest.IsZero() && p.Now().Sub(oldest) >= spriteStalledFor && !settled {
		draft.Kind, draft.To = kindDefect, []string{rootLane}
		draft.Text = fmt.Sprintf("SPRITE-STALLED %s: %d passes running, %s, and none it ran settled in the last %d min; check the Sprite and each pass's job.log", sprite, len(names), age, int(spriteStalledFor.Minutes()))
	}
	if _, err := client.Post(ctx, draft); err != nil {
		p.Log.Warn("a Sprite heartbeat did not post; the next pass tries again", "sprite", sprite, "error", err)
		return nil
	}
	return p.stamp(beatsDir, sprite)
}
