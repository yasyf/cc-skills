package wait

import (
	"errors"
	"os"
	"strconv"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func Command(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "wait SECONDS SOURCE... [-- COMMAND...]",
		Short: "Block until a source wakes the lane, its guarded command exits, or the deadline passes",
		Long: `The one way a lane waits. A source is <file>=<cursor-file>, mailbox:<lane>=<cursor-file>, or
cci:<drive>:<lane>. The mailbox and file sources are followed through the file system, the cci
source through the cci daemon's stream, so nothing polls. New file lines print clipped to 400
characters and advance the count the cursor holds; unread mailbox entries print as MAILBOX <n>
unread, then == <from> <timestamp> and the text, are marked read under the harness lock, and
their keys join the cursor; cci records addressed to the lane past the <lane>-wake cursor print
as cci tail prints them, and a first wait starts that cursor where the lane's own <lane> cursor
stands, or at the newest record when the lane has none. At the deadline it prints QUIET <Pacific time>. A command after -- runs
in its own session and process group; its output and exit status pass through when it exits
first, and a wake or the deadline ends the whole group with SIGTERM, then SIGKILL after 5 s.
Exit 2 is a usage error, 1 names a lane no roster lists, 127 a cci missing for 30 s.`,
		Args: cobra.MinimumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			seconds, err := strconv.Atoi(args[0])
			if err != nil || seconds < 0 {
				return errors.New(Usage)
			}
			sources, command := args[1:], []string(nil)
			if dash := cmd.ArgsLenAtDash(); dash >= 0 {
				sources, command = args[1:dash], args[dash:]
				if len(command) == 0 {
					return errors.New(Usage)
				}
			}
			code := Run(cmd.Context(), Options{
				Seconds: seconds,
				Sources: sources,
				Command: command,
				Stdout:  os.Stdout,
				Stderr:  a.Stderr,
				Runner:  a.Runner,
				Now:     a.Now,
			})
			if code != 0 {
				return &ExitError{code}
			}
			return nil
		},
	}
}
