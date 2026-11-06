package askdesk

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

const (
	StoreDesk   = "store-desk"
	StoreDuty   = "runs Sprite lanes' lr reads of the drive's store, runner.json and the owner's board over cci"
	answerWait  = 3 * time.Minute
	failedRead  = "store read failed: "
	exitAttr    = "exit"
	stderrAttr  = "stderr"
	stderrLimit = 4 << 10
	cardPaths   = "; a card is no store read: ask the owner with `lr ask-owner` from any host, and a landing tap card comes from the ready-cards pass once you post `cci post --kind ready --pr <n>` on a green head"
)

var StoreVerbs = []string{
	"asks",
	"board cards drawn",
	"board cards missing",
	"board cards ruled",
	"board ports",
	"board reds",
	"board rows",
	"board waits",
	"choices",
	"done-check",
	"drive sub list",
	"facts whose",
	"findings cited",
	"grade queue",
	"incident describe",
	"incident list",
	"incident status",
	"landing gate",
	"landing go",
	"landing order",
	"lanes",
	"owner audit",
	"request",
	"route",
	"rules settle",
	"sprites",
	"supervisor status",
	"tasks reconcile",
	"update",
	"update away",
	"update check",
	"update owners",
	"update status",
	"waste",
}

var errAnswered = errors.New("answered")

type Asked struct {
	Verb string   `json:"verb"`
	Args []string `json:"args"`
	Lane string   `json:"lane"`
}

type Run func(ctx context.Context, argv []string, lane string) (stdout, stderr string, exit int, err error)

type ExitError struct {
	Code int
}

func (e *ExitError) Error() string {
	return fmt.Sprintf("the Mac's run exited %d", e.Code)
}

type CCI interface {
	Post(context.Context, client.Draft) (client.Record, error)
	Follow(context.Context, client.Watch, func(client.Record) error) error
}

type LiveCCI struct{}

func (LiveCCI) Post(ctx context.Context, d client.Draft) (client.Record, error) {
	return client.Post(ctx, d)
}

func (LiveCCI) Follow(ctx context.Context, w client.Watch, emit func(client.Record) error) error {
	return client.Follow(ctx, w, emit)
}

func StoreRuns(config string, run Run) func(context.Context, string) Reply {
	return func(ctx context.Context, text string) Reply {
		var asked Asked
		if json.Unmarshal([]byte(text), &asked) != nil || !slices.Contains(StoreVerbs, asked.Verb) {
			why := "the store desk runs only lr " + strings.Join(StoreVerbs, ", lr ") + `, each asked as {"verb": ..., "args": [...]}`
			if strings.Contains(strings.ToLower(text), "card") {
				why += cardPaths
			}
			return Reply{Head: failedRead + why, Body: why}
		}
		argv := slices.Concat(strings.Fields(asked.Verb), []string{"--config", config}, asked.Args)
		stdout, stderr, exit, err := run(ctx, argv, asked.Lane)
		if err != nil {
			return Reply{Head: failedRead + err.Error(), Body: stderr + err.Error()}
		}
		return Reply{Head: fmt.Sprintf("lr %s: %d lines, exit %d", asked.Verb, strings.Count(stdout, "\n"), exit), Body: stdout, Exit: exit, Stderr: LastBytes(stderr, stderrLimit)}
	}
}

func RunOnMac(ctx context.Context, bus CCI, drive string, asked Asked, out, errs io.Writer) error {
	lane := asked.Lane
	text, err := json.Marshal(asked)
	if err != nil {
		return err
	}
	posted, err := bus.Post(ctx, client.Draft{Drive: drive, Lane: lane, Kind: AskKind, Text: string(text), To: []string{StoreDesk}, Force: true})
	if err != nil {
		return err
	}
	waiting, stop := context.WithTimeout(ctx, answerWait)
	defer stop()
	var answer client.Record
	err = bus.Follow(waiting, client.Watch{Drive: drive, Reader: client.Stem(lane), After: posted.Seq}, func(r client.Record) error {
		if r.Resolves != posted.Seq {
			return nil
		}
		answer = r
		return errAnswered
	})
	if err != nil && !errors.Is(err, errAnswered) {
		return err
	}
	if answer.Seq == 0 {
		return fmt.Errorf("this Sprite holds no copy of the drive's store or board, and the Mac's %s has not answered ask %s in %s: the supervisor there is down or runs an lr older than this one", StoreDesk, posted.Name(), answerWait)
	}
	whole, err := client.WholeText(answer)
	if err != nil {
		return err
	}
	body := strings.TrimPrefix(strings.TrimPrefix(whole, answer.Text), "\n")
	if failed, found := strings.CutPrefix(answer.Text, failedRead); found {
		return fmt.Errorf("the Mac's %s: %s", StoreDesk, cmp.Or(body, failed))
	}
	if _, err := io.WriteString(out, body); err != nil {
		return err
	}
	if _, err := io.WriteString(errs, answer.Fields.Attrs[stderrAttr]); err != nil {
		return err
	}
	if exit, _ := strconv.Atoi(answer.Fields.Attrs[exitAttr]); exit != 0 {
		return &ExitError{Code: exit}
	}
	return nil
}
