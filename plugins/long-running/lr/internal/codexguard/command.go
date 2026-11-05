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
		Short: "Refuse a Codex lane driving the owner's signed-in Orca browser on a production host, launching Iris on a test Mac signed in as him, or reading a production secret, pod or database without his record",
		Long:  "Codex runs no capt-hook pack, so this holds the no_production_as_him and production_reads rules through its own PreToolUse hook. The hosts come from the global git config key long-running.productionHost, the test Macs signed in as him from long-running.signedInBox, and production kube contexts, namespaces and database hosts from long-running.productionKubeContext, long-running.productionKubeNamespace and long-running.productionDbHost.",
	}
	cmd.AddCommand(checkCommand(a), installCommand())
	return cmd
}

func checkCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "check",
		Short: "Read one PreToolUse call on stdin and print a deny when it drives a production page, launches Iris as the owner, or reads production without his record",
		Long:  "Every read that fails lets the call through, except the owner record a production read cites, whose failed read refuses. Codex runs this from hooks.json, so nothing else needs to call it.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			payload, err := io.ReadAll(cmd.InOrStdin())
			if err != nil {
				return nil
			}
			reason := Guard{Runner: a.Runner, Getenv: os.Getenv}.Refusal(cmd.Context(), payload)
			if reason == "" {
				return nil
			}
			return json.NewEncoder(cmd.OutOrStdout()).Encode(denial(reason))
		},
	}
}

func installCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "install",
		Short: "Add the check to this machine's Codex hooks.json and trust it in config.toml",
		Long:  "Safe to rerun. A running Codex session picks the hook up at its next start. Run it on the Mac and on every Sprite that runs Codex lanes: a Sprite has no orca or signed-in box, but its production reads need the same refusal.",
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
