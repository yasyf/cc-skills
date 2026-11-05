package grader

import (
	_ "embed"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	regradeModel    = "sonnet"
	regradeEffort   = "high"
	regradeTools    = readerTools + ",Bash(git range-diff:*)"
	regradeTimeout  = 600 * time.Second
	regradeQuestion = "regrade-question.md"
	carryAnswer     = "carry"
	solAnswer       = "sol"
	finderMeans     = "a finder grade says the PR's change holds no blocker or major defect"
	fidelityMeans   = "a fidelity grade says the PR's change does what the owner's words, the plan and the repository's rules ask, in the class it names"
)

//go:embed briefs/regrade-brief.md
var regradeBrief string

type regradeAnswer struct {
	Answer string `json:"answer"`
	Why    string `json:"why"`
}

func (a regradeAnswer) String() string {
	return a.Answer + ": " + a.Why
}

func (a regradeAnswer) carries() bool {
	return a.Answer == carryAnswer
}

func (a regradeAnswer) reason() string {
	return strings.TrimRight(squeezed(a.Why), ".")
}

func (p *pass) regraded(view pullRequest, means string, earlier Record, last, pairs string, changed []pair) (regradeAnswer, error) {
	question := fill(regradeBrief, map[string]string{
		"repo":     p.job.Repo,
		"pr":       p.job.PR,
		"head":     p.job.Head,
		"last":     last,
		"means":    means,
		"grade":    listed([]Record{earlier}),
		"pairs":    pairs,
		"amended":  marked(changed, "!"),
		"added":    marked(changed, ">"),
		"dropped":  marked(changed, "<"),
		"checkout": p.job.Checkout,
		"title":    view.Title,
		"body":     orNone(strings.TrimSpace(view.Body)),
	})
	if err := os.WriteFile(filepath.Join(p.dir, regradeQuestion), []byte(question), 0o600); err != nil {
		return regradeAnswer{}, err
	}
	said, err := p.claudeRead(regradeModel, regradeEffort, regradeTools, question, regradeTimeout)
	if err != nil {
		return regradeAnswer{solAnswer, "the read failed: " + clipped(squeezed(err.Error()))}, nil
	}
	var answer regradeAnswer
	start, end := strings.Index(said, "{"), strings.LastIndex(said, "}")
	if start < 0 || end < start || json.Unmarshal([]byte(said[start:end+1]), &answer) != nil || (answer.Answer != carryAnswer && answer.Answer != solAnswer) || answer.reason() == "" {
		return regradeAnswer{solAnswer, "the read gave no answer: " + clipped(squeezed(said))}, nil
	}
	return answer, nil
}

func (p *pass) claudeRead(model, effort, tools, question string, timeout time.Duration) (string, error) {
	out, err := p.execute(shell.Command{
		Argv:    []string{"claude", "-p", "--model", model, "--effort", effort, "--output-format", "json", "--add-dir", p.dir, "--allowedTools", tools},
		Dir:     p.job.Checkout,
		Env:     []string{"CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR=1"},
		Stdin:   question,
		Timeout: timeout,
	})
	var said struct {
		Result string `json:"result"`
	}
	var exit *ExitError
	if errors.As(err, &exit) && json.Unmarshal([]byte(exit.Stdout), &said) == nil && said.Result != "" {
		return "", &JobFailed{"the reader exited " + strconv.Itoa(exit.Code) + ": " + squeezed(said.Result)}
	}
	if err != nil {
		return "", err
	}
	if err := json.Unmarshal([]byte(out), &said); err != nil {
		return "", err
	}
	return said.Result, nil
}
