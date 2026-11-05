package codexguard

import (
	"encoding/json"
	"io"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "codex-guard",
		Short: "Refuse a Codex lane driving the owner's signed-in Orca browser on a production host",
		Long:  "Codex runs no capt-hook pack, so this holds the no_production_as_him rule through its own PreToolUse hook. The hosts come from the global git config key long-running.productionHost.",
	}
	cmd.AddCommand(checkCommand(a), installCommand())
	return cmd
}

func checkCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "check",
		Short: "Read one PreToolUse call on stdin and print a deny when it drives a production page",
		Long:  "Every read that fails lets the call through. Codex runs this from hooks.json, so nothing else needs to call it.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			payload, err := io.ReadAll(cmd.InOrStdin())
			if err != nil {
				return nil
			}
			if !(Guard{Runner: a.Runner, Getenv: os.Getenv}).Refuses(cmd.Context(), payload) {
				return nil
			}
			return json.NewEncoder(cmd.OutOrStdout()).Encode(denial())
		},
	}
}

func installCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "install",
		Short: "Add the check to this machine's Codex hooks.json and trust it in config.toml",
		Long:  "Safe to rerun. A running Codex session picks the hook up at its next start. Run it on the Mac only: a Sprite has no orca command and no signed-in browser to guard.",
		Args:  cobra.NoArgs,
		RunE: func(*cobra.Command, []string) error {
			bin, err := exec.LookPath("lr")
			if err != nil {
				return err
			}
			home := os.Getenv("CODEX_HOME")
			if home == "" {
				user, err := os.UserHomeDir()
				if err != nil {
					return err
				}
				home = filepath.Join(user, ".codex")
			}
			return Install(home, checkLine(bin))
		},
	}
}
