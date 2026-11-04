package install

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	Handoff  = 15 * time.Minute
	poll     = 5 * time.Second
	lane     = "install"
	rootLane = "root"
)

type Refused struct {
	Version string
	Faults  []string
}

func (r *Refused) Error() string {
	return fmt.Sprintf("install %s refused before the swap: %s", r.Version, strings.Join(r.Faults, "; "))
}

type Steps struct {
	Preflight func(ctx context.Context) []string
	Swap      func(ctx context.Context, version string) error
	Post      func(ctx context.Context, to []string, body bus.Body) error
	Now       func() time.Time
	Sleep     func(time.Duration)
}

func Run(ctx context.Context, s *store.Store, drive, version string, steps Steps) (int64, error) {
	if failed := steps.Preflight(ctx); len(failed) > 0 {
		return 0, &Refused{version, failed}
	}
	if err := steps.announce(ctx, "installing "+version); err != nil {
		return 0, err
	}
	if err := steps.Swap(ctx, version); err != nil {
		return 0, steps.escalate(ctx, s, version, fmt.Sprintf("the install link did not move: %v", err), "")
	}
	var serving int64
	deadline := steps.Now().Add(Handoff)
	for steps.Now().Before(deadline) {
		lease, err := s.Lease(drive)
		if err != nil && !errors.Is(err, sql.ErrNoRows) {
			return 0, err
		}
		if err == nil && slices.Compare(versionKey(lease.Version), versionKey(version)) >= 0 {
			return lease.Generation, steps.announce(ctx, fmt.Sprintf("serving %s on generation %d", lease.Version, lease.Generation))
		}
		serving = lease.Generation
		steps.Sleep(poll)
	}
	return 0, steps.escalate(ctx, s, version, "no successor within 15 minutes", fmt.Sprintf("the successor did not take the lease within 15 minutes; generation %d still serves", serving))
}

func (st Steps) announce(ctx context.Context, text string) error {
	return st.Post(ctx, []string{"supervisor"}, bus.State{Lane: lane, Text: text})
}

func (st Steps) escalate(ctx context.Context, s *store.Store, version, cause, detail string) error {
	subject := "install:" + version
	_, stored := s.Escalate(subject, cause, rootLane, st.Now())
	posted := st.Post(ctx, []string{rootLane}, bus.Escalation{Subject: subject, Cause: cause})
	if detail == "" {
		detail = cause
	}
	return errors.Join(fmt.Errorf("install %s: %s", version, detail), stored, posted)
}
