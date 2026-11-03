package owner

import (
	"bytes"
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
)

const (
	opened    = "open"
	responded = "responded"
	addressed = "addressed"
	lockFile  = ".owner-ledger.lock"
	header    = "| id | time | message | routed | state |\n|---|---|---|---|---|\n"
	rowTime   = "Jan 2 3:04 PM"
)

var (
	rowID     = regexp.MustCompile(`(?m)^\| m(\d+) \|`)
	cellBreak = strings.NewReplacer("|", `\|`, "\r\n", "<br>", "\n", "<br>", "\r", "<br>")
	pacific   = mustZone("America/Los_Angeles")
)

func mustZone(name string) *time.Location {
	zone, err := time.LoadLocation(name)
	if err != nil {
		panic(err)
	}
	return zone
}

func Ledger(entry drive.Entry) string {
	return filepath.Join(entry.StateDir, "owner-relay", "owner-ledger.md")
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
		found := regexp.MustCompile(`(?m)^\| ` + regexp.QuoteMeta(row) + ` \| .* \| ([^|]*) \| (?:open|responded|addressed) \|$`).FindSubmatchIndex(ledger)
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

func Command(a *app.App) *cobra.Command {
	var driveName, addState string
	var routed, addRouted int
	owner := &cobra.Command{Use: "owner", Short: "The communicator's ledger of every owner message"}
	owner.PersistentFlags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	mark := &cobra.Command{
		Use:   "mark ROW responded|addressed",
		Short: "Mark one ledger row, so the communicator's turn can end",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			row, state := args[0], args[1]
			if state != responded && state != addressed {
				return fmt.Errorf("mark %s responded or addressed, not %s", row, state)
			}
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			if err := Mark(Ledger(entry), row, state, routed); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "%s %s\n", row, state)
			return nil
		},
	}
	mark.Flags().IntVar(&routed, "routed", 0, "the seq of the cci record that routed the message")
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
			row, err := Add(Ledger(entry), a.Now(), string(text), addState, addRouted)
			if err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), row)
			return nil
		},
	}
	add.Flags().StringVar(&addState, "state", opened, "the row's state: open or addressed")
	add.Flags().IntVar(&addRouted, "routed", 0, "the seq of the cci record that routed the message")
	owner.AddCommand(mark, add)
	return owner
}
