package wait

import (
	"errors"
	"fmt"
	"os"
	"strconv"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func Command(a *app.App) *cobra.Command {
	var output string
	cmd := &cobra.Command{
		Use:   "wait SECONDS [SOURCE...] [-- COMMAND...]",
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
A wait with a command needs no source.
Exit 2 is a usage error, 1 names a lane no roster lists, 127 a cci missing for 30 s.

--wake <file> is for a wait that outlasts the call: it prints ARMED <pid> <file> and returns at
once, the wait runs detached with its output in <file>, and when it ends it mails the terminal
that armed it and types a wake there if that terminal is at rest. Nothing is posted to any desk.
A Codex lane waits this way, since nothing else wakes an idle Codex terminal.`,
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
			options := Options{
				Seconds: seconds,
				Sources: sources,
				Command: command,
				Stdout:  os.Stdout,
				Stderr:  a.Stderr,
				Runner:  a.Runner,
				Now:     a.Now,
			}
			terminal := os.Getenv(terminalKey)
			if output != "" && terminal == "" {
				return fmt.Errorf("lr wait --wake wakes the terminal that armed it, and %s is unset", terminalKey)
			}
			if output != "" && os.Getenv(armedTag) == "" {
				executable, err := os.Executable()
				if err != nil {
					return err
				}
				return Arm(options, executable, os.Args[1:], output)
			}
			code := Run(cmd.Context(), options)
			if output != "" {
				if err := Wake(cmd.Context(), a.Runner, terminal, output, code); err != nil {
					return err
				}
			}
			if code != 0 {
				return &ExitError{code}
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&output, "wake", "", "run detached with output in this file, and wake the arming terminal when the wait ends")
	return cmd
}
