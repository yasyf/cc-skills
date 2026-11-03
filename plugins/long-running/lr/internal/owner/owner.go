package owner

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"syscall"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

const (
	responded = "responded"
	addressed = "addressed"
	lockFile  = ".owner-ledger.lock"
)

func Ledger(entry drive.Entry) string {
	return filepath.Join(entry.StateDir, "owner-relay", "owner-ledger.md")
}

func Mark(path, row, state string, routed int) error {
	lock, err := os.OpenFile(filepath.Join(filepath.Dir(path), lockFile), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		return err
	}
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
}

func Command(*app.App) *cobra.Command {
	var driveName string
	var routed int
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
	owner.AddCommand(mark)
	return owner
}
