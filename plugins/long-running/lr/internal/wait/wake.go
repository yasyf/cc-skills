package wait

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"syscall"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch/screen"
)

const (
	armedTag    = "LR_WAIT_ARMED"
	terminalKey = "ORCA_TERMINAL_HANDLE"
)

// Arm starts this same wait detached in its own session with its output in
// output, and returns at once, so the wait outlives the call that armed it.
func Arm(o Options, executable string, args []string, output string) error {
	if _, err := (&waiter{Options: o}).parse(); err != nil {
		return err
	}
	file, err := os.OpenFile(output, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	defer func() { _ = file.Close() }()
	cmd := exec.Command(executable, args...) //nolint:gosec // G702: re-executes this binary with the arguments it was given.
	cmd.Stdout, cmd.Stderr = file, file
	cmd.Env = append(os.Environ(), armedTag+"=1")
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		return err
	}
	if err := recordArmed(Armed{PID: cmd.Process.Pid, Terminal: os.Getenv(terminalKey), Output: output}); err != nil {
		return err
	}
	fmt.Fprintf(o.Stdout, "ARMED %d %s\n", cmd.Process.Pid, output)
	return cmd.Process.Release()
}

// Wake delivers a finished wait to the terminal that armed it the way a relay
// reaches a lane: Orca mail, then a typed wake when the screen is at rest. A
// lane not at rest reads the mail, and the desk's unread sweep wakes it if not.
func Wake(ctx context.Context, runner shell.Runner, terminal, output string, code int) error {
	subject := fmt.Sprintf("lr wait exited %d", code)
	sent, err := runner.Run(ctx, shell.Command{Argv: []string{"orca", "orchestration", "send", "--to", terminal, "--from", terminal, "--type", "dispatch", "--subject", subject, "--body", output + "\n" + clip(readText(output)), "--json"}})
	if err != nil {
		return err
	}
	var mail struct {
		Result struct {
			MessageID string `json:"messageId"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(sent.Stdout), &mail); err != nil {
		return err
	}
	read, err := runner.Run(ctx, shell.Command{Argv: []string{"orca", "terminal", "read", "--terminal", terminal, "--screen", "--json"}})
	if err != nil {
		return err
	}
	var shown struct {
		Result struct {
			Terminal screen.Terminal `json:"terminal"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(read.Stdout), &shown); err != nil {
		return err
	}
	if !screen.Wakeable(shown.Result.Terminal) {
		return nil
	}
	if _, err := runner.Run(ctx, shell.Command{Argv: []string{"orca", "terminal", "send", "--terminal", terminal, "--text", fmt.Sprintf("%s: read Orca message %s now", subject, mail.Result.MessageID), "--enter", "--json"}}); err != nil {
		return fmt.Errorf("the Orca message %s reached %s and only the typed wake failed, so the lane reads the message at its next check: %w", mail.Result.MessageID, terminal, err)
	}
	return nil
}
