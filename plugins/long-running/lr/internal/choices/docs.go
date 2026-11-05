package choices

import (
	"context"
	"encoding/json"
	"path"
	"regexp"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	docsRef      = "refs/remotes/origin/main"
	docsMain     = "+refs/heads/main:" + docsRef
	docsWait     = 2 * time.Minute
	docsFresh    = 30 * 24 * time.Hour
	groupOwner   = "owner"
	groupPicks   = "picks"
	stateDone    = "resolved"
	stateDropped = "superseded"
)

var (
	registerFile = regexp.MustCompile(`^design-docs/[^/]+/registers\.json$`)
	onePagerFile = regexp.MustCompile(`^one-pagers/[^/]+/index\.html$`)
	stillOpen    = regexp.MustCompile(`(?i)\b(?:choice|pick) is still open\b`)
	onePagerAsk  = regexp.MustCompile(`(?i)\b(?:owner pick|your call|for you to pick)\b`)
	namedCard    = regexp.MustCompile(`\bcard-[a-z0-9-]+`)
	htmlTag      = regexp.MustCompile(`<[^>]*>`)
)

type DocChoices struct {
	Slug  string
	Items []string
}

type Docs struct {
	Runner shell.Runner
	Dir    string
	head   string
	open   []DocChoices
}

func (d *Docs) git(ctx context.Context, args ...string) (string, error) {
	done, err := d.Runner.Run(ctx, shell.Command{Argv: append([]string{"git", "-C", d.Dir}, args...), Timeout: docsWait})
	return done.Stdout, err
}

func (d *Docs) Open(ctx context.Context, now time.Time) []DocChoices {
	if _, err := d.git(ctx, "fetch", "--quiet", "origin", docsMain); err != nil {
		return d.open
	}
	head, err := d.git(ctx, "rev-parse", docsRef)
	if err != nil || head == d.head {
		return d.open
	}
	listed, err := d.git(ctx, "ls-tree", "-r", "--name-only", docsRef, "design-docs", "one-pagers")
	if err != nil {
		return d.open
	}
	var open []DocChoices
	for _, file := range strings.Split(strings.TrimSpace(listed), "\n") {
		if !registerFile.MatchString(file) && !onePagerFile.MatchString(file) {
			continue
		}
		text, err := d.git(ctx, "show", docsRef+":"+file)
		if err != nil {
			continue
		}
		items := onePagerChoices(text)
		if registerFile.MatchString(file) {
			items = registerChoices(text, now)
		}
		if len(items) > 0 {
			open = append(open, DocChoices{path.Base(path.Dir(file)), items})
		}
	}
	d.head, d.open = head, open
	return open
}

func registerChoices(text string, now time.Time) []string {
	var register struct {
		Meta struct {
			Date string `json:"date"`
		} `json:"meta"`
		Open []struct {
			Group string `json:"g"`
			ID    string `json:"id"`
			Text  string `json:"t"`
			State string `json:"s"`
		} `json:"open"`
		Decisions []struct {
			ID     string `json:"id"`
			Reason string `json:"r"`
			State  string `json:"s"`
		} `json:"decisions"`
	}
	if json.Unmarshal([]byte(text), &register) != nil {
		return nil
	}
	if dated, err := time.Parse(time.DateOnly, register.Meta.Date); err == nil && now.Sub(dated) > docsFresh {
		return nil
	}
	var items []string
	for _, item := range register.Open {
		if (item.Group == groupOwner || item.Group == groupPicks) && item.State != stateDone && !namedCard.MatchString(item.Text) {
			items = append(items, item.ID)
		}
	}
	for _, decision := range register.Decisions {
		if decision.State != stateDropped && stillOpen.MatchString(decision.Reason) && !namedCard.MatchString(decision.Reason) {
			items = append(items, decision.ID)
		}
	}
	return items
}

func onePagerChoices(text string) []string {
	var items []string
	for _, line := range strings.Split(text, "\n") {
		if onePagerAsk.MatchString(line) && !namedCard.MatchString(line) {
			items = append(items, excerpt(htmlTag.ReplaceAllString(line, " ")))
		}
	}
	return items
}
