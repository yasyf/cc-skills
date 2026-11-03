package prompts

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch/screen"
)

const (
	settleTries = 5
	keyGap      = time.Second
	submitKey   = "\r"
)

var (
	optionRow = regexp.MustCompile(`^[❯›]?\s*(\d+)\.\s+(.+)$`)
	choices   = map[string]*regexp.Regexp{
		"yes": regexp.MustCompile(`^(?:Yes|Allow)\b`),
		"no":  regexp.MustCompile(`^(?:No|Deny)\b`),
	}
)

type AnswerArgs struct {
	Dispatch    string
	Terminal    string
	Environment string
	By          string
	Episode     string
	Choice      string
	Instead     string
}

type Answered struct {
	Line string
	OK   bool
}

func pickOption(asked, choice string) (number, label string, found bool) {
	var numbered [][2]string
	for row := range strings.SplitSeq(asked, " / ") {
		if match := optionRow.FindStringSubmatch(row); match != nil {
			numbered = append(numbered, [2]string{match[1], match[2]})
		}
	}
	start := len(numbered)
	for i, option := range numbered {
		if option[0] == "1" {
			start = i
		}
	}
	for _, option := range numbered[start:] {
		if choices[choice].MatchString(option[1]) {
			return option[0], option[1], true
		}
	}
	return "", "", false
}

func (w *Watch) runOf(ctx context.Context, s subject) (run, dispatch string, err error) {
	if s.dispatch != "" {
		shown := w.orca(ctx, "", nil, "orchestration", "worker-show", "--dispatch", s.dispatch)
		var result struct {
			Dispatch struct {
				RunID string `json:"runId"`
			} `json:"dispatch"`
		}
		if !shown.OK {
			return "", "", fmt.Errorf("worker-show %s: %s", s.dispatch, shown.Error.Message)
		}
		if err := json.Unmarshal(shown.Result, &result); err != nil {
			return "", "", err
		}
		return result.Dispatch.RunID, s.dispatch, nil
	}
	refused := fmt.Errorf("terminal %s is no worker and coordinates no Run, so no Run's mail could record the answer", s.terminal)
	env := []string{terminalEnv + "=" + s.terminal}
	var cursor []string
	for {
		listed := w.orca(ctx, "", env, slices.Concat([]string{"orchestration", "worker-list", "--include-remote"}, cursor)...)
		if !listed.OK {
			return "", "", refused
		}
		var result workerList
		if err := json.Unmarshal(listed.Result, &result); err != nil {
			return "", "", err
		}
		for _, worker := range result.Workers {
			if worker.Terminal == s.terminal {
				return worker.RunID, worker.DispatchID, nil
			}
		}
		if result.Scope.Source == "bound" {
			return result.Scope.Run, "", nil
		}
		if !result.Page.HasMore {
			return "", "", refused
		}
		cursor = []string{"--cursor", result.Page.NextCursor}
	}
}

func (w *Watch) typed(ctx context.Context, s subject, keys ...string) string {
	for i, text := range keys {
		if i > 0 {
			w.Sleep(keyGap)
		}
		sent := w.orca(ctx, s.environment, nil, "terminal", "send", "--terminal", s.terminal, "--text", text)
		if !sent.OK {
			return sent.Error.Code + ": " + sent.Error.Message
		}
		var result struct {
			Send struct {
				Accepted bool `json:"accepted"`
			} `json:"send"`
		}
		if err := json.Unmarshal(sent.Result, &result); err != nil || !result.Send.Accepted {
			return string(sent.Result)
		}
	}
	return ""
}

func (w *Watch) idleInput(ctx context.Context, s subject) bool {
	read := w.orca(ctx, s.environment, nil, "terminal", "read", "--terminal", s.terminal, "--screen")
	if !read.OK {
		return false
	}
	var result struct {
		Terminal screen.Terminal `json:"terminal"`
	}
	return json.Unmarshal(read.Result, &result) == nil && screen.InputState(result.Terminal) == screen.Idle
}

func (w *Watch) Answer(ctx context.Context, args AnswerArgs) (Answered, error) {
	if (args.Choice == "no") != (args.Instead != "") {
		return Answered{}, errors.New("--instead goes with no and only with no: a denied agent ends its turn, and Orca refuses every prompt to it until one is typed")
	}
	asked := subject{key: args.Terminal, role: roleDesk, name: args.Terminal, terminal: args.Terminal, environment: args.Environment}
	if args.Dispatch != "" {
		asked = subject{key: "dispatch:" + args.Dispatch, role: roleWorker, name: args.Dispatch, dispatch: args.Dispatch}
	}
	run, dispatch, err := w.runOf(ctx, asked)
	if err != nil {
		return Answered{}, err
	}
	shown := w.observe(ctx, asked, true)
	s, episode := shown.subject, episodeOf(shown)
	if shown.state != Approval {
		detail := ""
		if shown.detail != "" {
			detail = " (" + shown.detail + ")"
		}
		return Answered{}, fmt.Errorf("%s shows no approval dialog: it reads %s%s", s.name, shown.state, detail)
	}
	if args.Episode != "" && args.Episode != episode {
		return Answered{}, fmt.Errorf("%s shows episode %s, not %s: %s", s.name, episode, args.Episode, shown.title)
	}
	number, label, found := pickOption(shown.asked, args.Choice)
	if !found {
		return Answered{}, fmt.Errorf("no numbered option of %s's dialog answers %s: %s", s.name, args.Choice, shown.asked)
	}
	if refused := w.typed(ctx, s, number); refused != "" {
		return Answered{}, fmt.Errorf("orca refused the keys for %s: %s", s.name, refused)
	}
	after := shown
	for range settleTries {
		w.Sleep(time.Second)
		if after = w.observe(ctx, s, true); after.state == Clear || after.state == Stale {
			break
		}
	}
	cleared := after.state == Clear || after.state == Stale
	outcome := "the dialog closed"
	if !cleared {
		outcome = fmt.Sprintf("after the keys the terminal reads %s", after.state)
		if after.title != "" {
			outcome += " (" + after.title + ")"
		}
	}
	told := args.Instead == ""
	if cleared && args.Instead != "" {
		switch {
		case !w.idleInput(ctx, s):
			outcome += "; its input box is not empty and idle, so nothing was typed instead"
		default:
			if refused := w.typed(ctx, s, args.Instead, submitKey); refused != "" {
				outcome += "; Orca refused the text to do instead: " + refused
			} else {
				told = true
				outcome += fmt.Sprintf("; told it instead: \"%s\"", args.Instead)
			}
		}
	}
	chose := fmt.Sprintf("%s chose \"%s. %s\" for %s on %s", args.By, number, label, s.name, s.terminal)
	mail := []string{"orchestration", "send", "--to", "run:" + run}
	if dispatch != "" {
		mail = append(mail, "--dispatch-id", dispatch)
	}
	mail = append(mail, "--type", "status", "--subject", fmt.Sprintf("%s answered %s: %s", args.By, args.Choice, shown.title), "--body", fmt.Sprintf("%s; %s. Prompt episode %s.", chose, outcome, episode))
	audit := w.orca(ctx, "", nil, mail...)
	recorded := "recorded in run:" + run
	if !audit.OK {
		recorded = fmt.Sprintf("NOT recorded in run:%s: %s", run, audit.Error.Message)
	}
	return Answered{fmt.Sprintf("ANSWERED %s; %s; %s", chose, outcome, recorded), cleared && told && audit.OK}, nil
}
