package bk

import (
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

func Label(drive string) string {
	return "com.long-running." + drive + ".bk"
}

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "bk", Short: "Buildkite reads for Sprite lanes, run by the drive host's own tools/ci bk behind tailscale serve"}
	for _, verb := range []string{"api", "log", "graphql"} {
		cmd.AddCommand(&cobra.Command{
			Use:                verb + " ARGS...",
			Short:              "Run tools/ci bk " + verb + " on the drive host through the proxy ~/" + urlFile + " names",
			DisableFlagParsing: true,
			RunE: func(cmd *cobra.Command, args []string) error {
				home, err := os.UserHomeDir()
				if err != nil {
					return err
				}
				code, err := Forward(cmd.Context(), home, append([]string{verb}, args...), cmd.OutOrStdout(), cmd.ErrOrStderr())
				if err != nil {
					return err
				}
				if code != 0 {
					return &wait.ExitError{Code: code}
				}
				return nil
			},
		})
	}
	cmd.AddCommand(
		&cobra.Command{Use: "serve", Short: "Answer bk reads on loopback for the callers tailscale serve forwards", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			cfg, state, err := lanes.Loaded(a)
			if err != nil {
				return err
			}
			settings, err := LoadSettings(filepath.Join(state, "bk.json"))
			if err != nil {
				return err
			}
			if strings.TrimSpace(cfg.BuildkiteCLI) == "" {
				return errors.New("lr.json names no buildkite_cli, the Buildkite client the proxy runs from the checkout")
			}
			addr := net.JoinHostPort("127.0.0.1", strconv.Itoa(settings.Port))
			server := &http.Server{
				Addr:              addr,
				Handler:           Proxy{Runner: a.Runner, CLI: strings.Fields(cfg.BuildkiteCLI), Checkout: cfg.Checkout, AdmitTag: settings.AdmitTag},
				ReadHeaderTimeout: 10 * time.Second,
			}
			fmt.Fprintf(cmd.OutOrStdout(), "lr bk: answering %s callers on %s\n", settings.AdmitTag, addr)
			return server.ListenAndServe()
		}},
		&cobra.Command{Use: "install", Short: "Write the proxy's launchd agent, publish it on the tailnet with tailscale serve, and print the WORKER_BK_URL Sprites use", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			cfg, state, err := lanes.Loaded(a)
			if err != nil {
				return err
			}
			settings, err := LoadSettings(filepath.Join(state, "bk.json"))
			if err != nil {
				return err
			}
			env, err := lanes.NewEnv(a.Runner, cfg, state, a.Now)
			if err != nil {
				return err
			}
			config, err := filepath.Abs(a.ConfigPath)
			if err != nil {
				return err
			}
			path := os.Getenv("PATH")
			if path == "" {
				return errors.New("PATH is empty; launchd would run the proxy with no tailscale or tools/ci")
			}
			agents, err := launchd.New(a.Runner, cmd.OutOrStdout())
			if err != nil {
				return err
			}
			job := launchd.Job{
				Label:     Label(cfg.Drive),
				Env:       [][2]string{{"PATH", path}},
				Arguments: []string{filepath.Join(env.Claude, "long-running", "current", "bin", "lr"), "bk", "serve", "--config", config},
				Directory: state,
				Log:       filepath.Join(state, "bk.log"),
			}
			if err := agents.Install(cmd.Context(), job); err != nil {
				return err
			}
			ctx := cmd.Context()
			if _, err := a.Runner.Run(ctx, shell.Command{Argv: []string{"tailscale", "serve", "--bg", fmt.Sprintf("--https=%d", settings.ServePort), fmt.Sprintf("http://127.0.0.1:%d", settings.Port)}, Timeout: time.Minute}); err != nil {
				return err
			}
			status, err := a.Runner.Run(ctx, shell.Command{Argv: []string{"tailscale", "status", "--json"}, Timeout: time.Minute})
			if err != nil {
				return err
			}
			var self struct {
				Self struct {
					DNSName string `json:"DNSName"`
				} `json:"Self"`
			}
			if err := json.Unmarshal([]byte(status.Stdout), &self); err != nil || self.Self.DNSName == "" {
				return errors.New("tailscale status --json names no DNSName for this machine")
			}
			fmt.Fprintf(cmd.OutOrStdout(), "WORKER_BK_URL=https://%s:%d/\n", strings.TrimSuffix(self.Self.DNSName, "."), settings.ServePort)
			return nil
		}},
	)
	return cmd
}
