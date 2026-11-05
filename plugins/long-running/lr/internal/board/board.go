package board

import (
	"fmt"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	MergeBlock = "merge-prs"

	FactWithdrawn = prrow.FactWithdrawn

	tapFact  = "tap"
	noTap    = gates.NoTap
	byOwner  = "owner"
	shortSHA = 12
)

type TapRecord struct {
	PR    int
	Head  string
	By    string
	Value machine.Tap
}

type Hooks struct {
	Tap    func(TapRecord) error
	Refuse func(tap TapRecord, reason string) error
	Hold   func(pr int, head string) error
	Note   func(lane, text string) error
}

func short(head string) string {
	return head[:min(shortSHA, len(head))]
}

// Standing is the row's one line of CI, review, grade and mergeability, as every
// PR card shows it.
func Standing(row store.ReviewRow) string {
	return fmt.Sprintf("CI %s; %s; grade %s; mergeable %s", row.CI, row.Review, row.Grade, row.Mergeable)
}

func Answer(s *store.Store, repo string, hooks Hooks, row store.ReviewRow, verdict string, at time.Time) error {
	switch verdict {
	case verdictApproved:
		tap := TapRecord{row.Number, row.Head, byOwner, machine.TapTapped}
		_, refusal, err := prrow.TapAt(s, repo, bus.Tap{PR: row.Number, Head: row.Head})
		if err != nil {
			return err
		}
		if refusal == prrow.AlreadyLanded {
			return nil
		}
		if refusal != "" {
			if _, err := s.PutFact(repo, row.Number, row.Head, bus.TapRefusedFact, refusal, at); err != nil {
				return err
			}
			if err := hooks.Refuse(tap, refusal); err != nil {
				return err
			}
			return fmt.Errorf("#%d: %s", row.Number, refusal)
		}
		if err := prrow.Tap(s, repo, row.Number, row.Head, byOwner, at); err != nil {
			return err
		}
		return hooks.Tap(tap)
	case verdictRejected:
		return hooks.Hold(row.Number, row.Head)
	}
	return fmt.Errorf("not a board answer: %q", verdict)
}
