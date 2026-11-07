package board

import (
	"context"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	verdictHeld   = "held"
	cardAnswerCCN = 30 * time.Second
)

type cardAnswers struct {
	runner   shell.Runner
	checkout string
	warn     io.Writer
}

func (a cardAnswers) record(ctx context.Context, repository string, pr int, head, verdict string) {
	if err := a.write(ctx, repository, pr, head, verdict); err != nil {
		fmt.Fprintf(a.warn, "warning: his %s of #%d at %s is on the bus but not in ccn answers: %v\n", verdict, pr, short(head), err)
	}
}

func (a cardAnswers) write(ctx context.Context, repository string, pr int, head, verdict string) error {
	ccn := func(args ...string) (string, error) {
		done, err := a.runner.Run(ctx, shell.Command{Argv: append([]string{"ccn", "-R", a.checkout}, args...), Timeout: cardAnswerCCN})
		return done.Stdout, err
	}
	key := fmt.Sprintf("card-tap:%d/%s/%s", pr, head, verdict)
	listed, err := ccn("answer", "list", "--label", key, "--limit", "0", "--json")
	if err != nil {
		return err
	}
	if trimmed := strings.TrimSpace(listed); trimmed != "" && trimmed != "[]" {
		return nil
	}
	title := fmt.Sprintf("#%d %s at %s", pr, verdict, short(head))
	_, err = ccn("answer", "add", title,
		"--body", fmt.Sprintf("Owner on a PR card: %s#%d %s at %s.", repository, pr, verdict, head),
		"--label", "from:owner",
		"--label", "pr-card",
		"--label", fmt.Sprintf("pr:#%d", pr),
		"--label", "head:"+head,
		"--label", "verdict:"+verdict,
		"--label", key,
		"--json")
	return err
}
