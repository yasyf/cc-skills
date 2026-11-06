package waste

import (
	"context"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const markPrefix = "waste/"

type Poster struct {
	Sources Sources
	Config  Config
	Store   *store.Store
	Drive   config.Config
	Now     func() time.Time
	Post    func(to []string, body bus.Body) error
}

func (p Poster) Pass(ctx context.Context) error {
	report, err := Read(ctx, p.Sources, p.Config, p.Drive, p.Now(), Window)
	if err != nil {
		return err
	}
	var fresh []Finding
	for _, finding := range report.Findings {
		mark, err := p.Store.Cursor(markPrefix + finding.Key)
		if err != nil {
			return err
		}
		if mark == 0 || float64(finding.Size) >= float64(mark)*p.Config.Growth {
			fresh = append(fresh, finding)
		}
	}
	if len(fresh) == 0 {
		return nil
	}
	text, shown := Render(fresh, report.note())
	note := bus.Note{Text: text}
	if shown < len(fresh) {
		note.Detail = every(fresh, report.note())
	}
	if err := p.Post([]string{p.Config.Lane}, note); err != nil {
		return err
	}
	for _, finding := range fresh[:shown] {
		if err := p.Store.Advance(markPrefix+finding.Key, finding.Size); err != nil {
			return err
		}
	}
	return nil
}
