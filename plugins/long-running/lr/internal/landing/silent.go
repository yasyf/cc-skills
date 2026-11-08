package landing

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	PassKind    = "landing.pass"
	SilentKind  = "landing.silent"
	SilentAfter = 15 * time.Minute

	silentSubject  = "landing-pass-silent"
	silentCause    = "no landing pass ended"
	passSeenCursor = "landing-pass-seen"
	metaDesk       = "meta-desk"
)

func SayPassEnded(ctx context.Context, drive string, prs int, admissions []string, took time.Duration) error {
	text := fmt.Sprintf("a landing pass ended: %d PRs read, %d enqueued in %s", prs, len(admissions), took.Round(time.Second))
	if len(admissions) > 0 {
		text += ": " + strings.Join(admissions, "; ")
	}
	_, err := client.Post(ctx, client.Draft{Drive: drive, Lane: self, Kind: PassKind, Text: text})
	return err
}

type Silence struct {
	Store *store.Store
	Drive string
	Now   func() time.Time
}

func (s Silence) Check(ctx context.Context) error {
	now := s.Now()
	passes, err := client.Tail(ctx, client.Query{Drive: s.Drive, Kinds: []string{PassKind}, Since: now.Add(-SilentAfter)})
	if err != nil {
		return err
	}
	if len(passes) > 0 {
		return s.heard(ctx, passes[len(passes)-1].At)
	}
	seen, err := s.Store.Cursor(passSeenCursor)
	if err != nil || seen == 0 {
		return err
	}
	raised, err := s.Store.Escalate(silentSubject, silentCause, "", now)
	if err != nil || !raised {
		return err
	}
	last := time.UnixMicro(seen)
	return s.say(ctx, "silent", fmt.Sprintf("LANDING-SILENT: no landing pass has ended for %d minutes, since %s, so no tap of his becomes a land order and nothing enters the merge queue until the landing host runs a pass again", int(now.Sub(last).Minutes()), last.In(pacificZone()).Format("3:04 PM")))
}

func (s Silence) heard(ctx context.Context, at time.Time) error {
	if err := s.Store.Advance(passSeenCursor, at.UnixMicro()); err != nil {
		return err
	}
	raised, err := s.Store.Escalations(silentSubject)
	if err != nil || len(raised) == 0 {
		return err
	}
	if err := s.say(ctx, "recovered", fmt.Sprintf("LANDING-SILENT recovered: a landing pass ended at %s", at.In(pacificZone()).Format("3:04 PM"))); err != nil {
		return err
	}
	return s.Store.ClearEscalation(silentSubject)
}

func (s Silence) say(ctx context.Context, state, text string) error {
	_, err := client.Post(ctx, client.Draft{Drive: s.Drive, Lane: self, Kind: SilentKind, To: []string{rootLane, metaDesk}, Attrs: map[string]string{"state": state}, Text: text})
	return err
}
