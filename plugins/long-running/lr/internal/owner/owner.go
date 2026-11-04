package owner

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	opened     = "open"
	responded  = "responded"
	addressed  = "addressed"
	noOrder    = "no-order"
	dueState   = "due"
	duePrefix  = dueState + " "
	repliesDir = "replies"
	lockFile   = ".owner-ledger.lock"
	header     = "| id | time | message | routed | state |\n|---|---|---|---|---|\n"
	rowTime    = "Jan 2 3:04 PM"
	byTime     = "3:04 PM"

	carriedChars = 1500

	subscribeWait   = time.Minute
	communicator    = "owner-relay"
	broadcastsHeard = "incident"
)

// batchedKinds reach the communicator together, at most every five minutes.
// A kind off this list, the owner's taps and every ask among them, wakes it on
// arrival, so a kind nobody listed never waits.
var batchedKinds = []string{"report", "note", "answer", "done", "head", "state", "serving", "release", "evidence", "decision"}

var (
	contentAsk = regexp.MustCompile(`(?i)\b(?:recap|report|summary|(?:up|ip)d\w{0,2}t\w?)\b`)
	rowID      = regexp.MustCompile(`(?m)^\| m(\d+)(?:\.\d+)? \|`)
	rowState   = regexp.MustCompile(`(?m)^\| m\d+(?:\.\d+)? \| .* \| (open|responded|addressed|no-order|due [^|]*) \|$`)
	cellBreak  = strings.NewReplacer("|", `\|`, "\r\n", "<br>", "\n", "<br>", "\r", "<br>")
	pacific    = mustZone("America/Los_Angeles")
)

func mustZone(name string) *time.Location {
	zone, err := time.LoadLocation(name)
	if err != nil {
		panic(err)
	}
	return zone
}

func Ledger(state string) string {
	return filepath.Join(state, communicator, "owner-ledger.md")
}

func locked(path string, fn func() error) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	lock, err := os.OpenFile(filepath.Join(filepath.Dir(path), lockFile), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		return err
	}
	return fn()
}

func Add(path string, at time.Time, text, state string, routed int) (string, error) {
	var row string
	err := locked(path, func() error {
		ledger, err := os.ReadFile(path)
		if err != nil && !errors.Is(err, fs.ErrNotExist) {
			return err
		}
		next := 1
		for _, found := range rowID.FindAllSubmatch(ledger, -1) {
			seen, err := strconv.Atoi(string(found[1]))
			if err != nil {
				return err
			}
			next = max(next, seen+1)
		}
		row = "m" + strconv.Itoa(next)
		heading := ""
		if !bytes.Contains(ledger, []byte(header)) {
			heading = "\n" + header
		}
		record := ""
		if routed != 0 {
			record = strconv.Itoa(routed)
		}
		file, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
		if err != nil {
			return err
		}
		defer file.Close()
		_, err = fmt.Fprintf(file, "%s| %s | %s | %s | %s | %s |\n", heading, row, at.In(pacific).Format(rowTime), cellBreak.Replace(strings.TrimSpace(text)), record, state)
		return err
	})
	return row, err
}

func Mark(path, row, state string, routed int) error {
	return locked(path, func() error {
		ledger, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		found := regexp.MustCompile(`(?m)^\| ` + regexp.QuoteMeta(row) + ` \| .* \| ([^|]*) \| (?:open|responded|addressed|no-order|due [^|]*) \|$`).FindSubmatchIndex(ledger)
		if found == nil {
			return fmt.Errorf("no row %s in %s", row, path)
		}
		record := string(ledger[found[2]:found[3]])
		if routed != 0 {
			record = strconv.Itoa(routed)
		}
		marked := append(slices.Clone(ledger[:found[2]]), record+" | "+state+" |"...)
		staged := path + ".staged"
		if err := os.WriteFile(staged, append(marked, ledger[found[1]:]...), 0o600); err != nil {
			return err
		}
		return os.Rename(staged, path)
	})
}

func Answer(stateDir, row, by, reply string, routed int, now time.Time) (string, error) {
	if by == "" {
		return settle(stateDir, row, responded, by, reply, routed, now)
	}
	return settle(stateDir, row, dueState, by, reply, routed, now)
}

func settle(stateDir, row, state, by, reply string, routed int, now time.Time) (string, error) {
	var err error
	if state == dueState {
		if state, err = dueBy(by, now); err != nil {
			return "", fmt.Errorf("%s: %w", row, err)
		}
	}
	if state == responded {
		if err := carried(Ledger(stateDir), filepath.Join(stateDir, repliesDir), row, reply); err != nil {
			return "", err
		}
	}
	return state, Mark(Ledger(stateDir), row, state, routed)
}

func dueBy(by string, now time.Time) (string, error) {
	clock, err := time.ParseInLocation(byTime, by, pacific)
	if err != nil {
		return "", fmt.Errorf("due needs --by with a clock such as 6:40 PM, not %q", by)
	}
	local := now.In(pacific)
	at := time.Date(local.Year(), local.Month(), local.Day(), clock.Hour(), clock.Minute(), 0, 0, pacific)
	if at.Before(local) {
		return "", fmt.Errorf("--by %s has passed", by)
	}
	return duePrefix + at.Format(rowTime), nil
}

func carried(path, replies, row, reply string) error {
	ledger, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	i := slices.IndexFunc(Parse(ledger), func(r Row) bool { return r.ID == row })
	if i < 0 || !contentAsk.MatchString(hisWords(Parse(ledger)[i].Text)) {
		return nil
	}
	refusal := fmt.Sprintf("%s asks for an update, recap or report, so responded needs --reply with the reply that carries it; until then mark it due --by <clock>", row)
	if reply == "" {
		return errors.New(refusal)
	}
	sent, err := os.ReadFile(filepath.Join(replies, reply+".json"))
	if err != nil {
		return fmt.Errorf("%s: %w", refusal, err)
	}
	var body struct {
		Reply struct {
			MD string `json:"md"`
		} `json:"reply"`
	}
	if err := json.Unmarshal(sent, &body); err != nil {
		return err
	}
	if len(body.Reply.MD) < carriedChars {
		return fmt.Errorf("%s; reply %s is %d characters, a promise rather than the content", refusal, reply, len(body.Reply.MD))
	}
	return nil
}

func hisWords(text string) string {
	open, closed := strings.Index(text, `"`), strings.LastIndex(text, `"`)
	if open < 0 || open == closed {
		return text
	}
	return text[open+1 : closed]
}

func namedBy(replies, row, reply string) bool {
	sent, err := os.ReadFile(filepath.Join(replies, reply+".json"))
	if err != nil {
		return false
	}
	var body struct {
		Reply struct {
			Order string `json:"order"`
		} `json:"reply"`
	}
	return json.Unmarshal(sent, &body) == nil && body.Reply.Order == row
}

func Rows(path string, all bool) ([]string, error) {
	ledger, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var rows []string
	for _, found := range rowState.FindAllSubmatch(ledger, -1) {
		if all || (string(found[1]) != addressed && string(found[1]) != noOrder) {
			rows = append(rows, string(found[0]))
		}
	}
	return rows, nil
}

func Command(a *app.App) *cobra.Command {
	var driveName, addState, reply, by string
	var routed, addRouted int
	var listAll bool
	owner := &cobra.Command{Use: "owner", Short: "The communicator's ledger of every owner message"}
	owner.PersistentFlags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	mark := &cobra.Command{
		Use:   "mark ROW responded|addressed|no-order|due",
		Short: "Mark one ledger row, so the communicator's turn can end; no-order takes a question or remark off lr owner audit",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			row, state := args[0], args[1]
			if state != responded && state != addressed && state != noOrder && state != dueState {
				return fmt.Errorf("mark %s responded, addressed, no-order or due, not %s", row, state)
			}
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			if state, err = settle(entry.StateDir, row, state, by, reply, routed, a.Now()); err != nil {
				return err
			}
			if state == responded && !namedBy(filepath.Join(entry.StateDir, repliesDir), row, reply) {
				fmt.Fprintf(cmd.ErrOrStderr(), "warning: %s is responded with no reply naming it, so nothing told him whether he has anything to do; send the reply with order=%s and pass it as --reply\n", row, row)
			}
			fmt.Fprintf(cmd.OutOrStdout(), "%s %s\n", row, state)
			return nil
		},
	}
	mark.Flags().IntVar(&routed, "routed", 0, "the seq of the cci record that routed the message")
	mark.Flags().StringVar(&reply, "reply", "", "the reply that answers the row: it names the row as its order, or carries what a row asking for an update, recap or report asked for")
	mark.Flags().StringVar(&by, "by", "", "the clock a due row is promised by, such as 6:40 PM")
	add := &cobra.Command{
		Use:   "add",
		Short: "Add the owner message on stdin as one ledger row, and print its id",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if addState != opened && addState != addressed {
				return fmt.Errorf("add a row open or addressed, not %s", addState)
			}
			text, err := io.ReadAll(cmd.InOrStdin())
			if err != nil {
				return err
			}
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			row, err := Add(Ledger(entry.StateDir), a.Now(), string(text), addState, addRouted)
			if err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), row)
			return nil
		},
	}
	add.Flags().StringVar(&addState, "state", opened, "the row's state: open or addressed")
	add.Flags().IntVar(&addRouted, "routed", 0, "the seq of the cci record that routed the message")
	list := &cobra.Command{
		Use:   "list",
		Short: "Print the ledger's rows not yet addressed or marked no-order, oldest first, or every row with --all",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			rows, err := Rows(Ledger(entry.StateDir), listAll)
			if err != nil {
				return err
			}
			for _, row := range rows {
				fmt.Fprintln(cmd.OutOrStdout(), row)
			}
			return nil
		},
	}
	list.Flags().BoolVar(&listAll, "all", false, "print addressed and no-order rows too")
	start := &cobra.Command{
		Use:   "start",
		Short: "Subscribe this window as the communicator, with routine records batched",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			result, err := a.Runner.Run(cmd.Context(), shell.Command{
				Argv:    []string{"cci", "subscribe", "--drive", driveName, "--reader", communicator, "--kind", broadcastsHeard, "--batch-kinds", strings.Join(batchedKinds, ",")},
				Timeout: subscribeWait,
			})
			if err != nil {
				return err
			}
			_, err = io.WriteString(cmd.OutOrStdout(), result.Stdout)
			return err
		},
	}
	owner.AddCommand(mark, add, list, start)
	owner.AddCommand(questionCommands(a, &driveName)...)
	return owner
}
