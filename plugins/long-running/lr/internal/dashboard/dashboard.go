package dashboard

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/findings"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
)

const answerWait = 90

var driveName = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)

func Label(drive string) string {
	return "com.yasyf.long-running.dashboard." + drive
}

type Service struct {
	Drive  string
	State  string
	Claude string
	Python string
}

func (s Service) config() string { return filepath.Join(s.State, "dashboard.json") }

func (s Service) Job() launchd.Job {
	return launchd.Job{
		Label:     Label(s.Drive),
		Env:       [][2]string{{"CLAUDE_LONG_RUNNING_DRIVE", s.Drive}},
		Arguments: []string{s.Python, filepath.Join(s.Claude, "long-running", "current", "dashboard", "app", "server.py"), "--config", s.config()},
		Directory: s.State,
		Log:       filepath.Join(s.State, "server.log"),
	}
}

func (s Service) URL() (string, error) {
	return drive.DashboardURL(s.State)
}

func Restart(ctx context.Context, agents launchd.Launchd, s Service, answers func(string) bool) (string, error) {
	url, err := s.URL()
	if err != nil {
		return "", err
	}
	if err := agents.Restart(ctx, Label(s.Drive)); err != nil {
		return "", fmt.Errorf("%w; lr dashboard install writes it", err)
	}
	for range answerWait {
		if answers(url) {
			return url, nil
		}
		agents.Sleep(time.Second)
	}
	return "", fmt.Errorf("%s restarted but %s did not answer within %d s; see %s", Label(s.Drive), url, answerWait, s.Job().Log)
}

func answers(url string) bool {
	client := http.Client{Timeout: 5 * time.Second}
	response, err := client.Get(url)
	if err != nil {
		return false
	}
	_, _ = io.Copy(io.Discard, response.Body)
	_ = response.Body.Close()
	return response.StatusCode == http.StatusOK
}

func Command(a *app.App) *cobra.Command {
	service := func() (Service, error) {
		cfg, state, err := lanes.Loaded(a)
		if err != nil {
			return Service{}, err
		}
		if !driveName.MatchString(cfg.Drive) {
			return Service{}, fmt.Errorf("drive %q is not a drive name, so it names no drive's dashboard", cfg.Drive)
		}
		env, err := lanes.NewEnv(a.Runner, cfg, state, a.Now)
		if err != nil {
			return Service{}, err
		}
		return Service{Drive: cfg.Drive, State: state, Claude: env.Claude}, nil
	}
	var proof string
	var record int64
	restart := &cobra.Command{Use: "restart", Short: "Restart the drive dashboard's launchd agent and print its URL once it answers; a changed dashboard needs --proof or the owner record of his order in --record", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		s, err := service()
		if err != nil {
			return err
		}
		live, err := s.URL()
		if err != nil {
			return err
		}
		tree, err := Tree(s.live())
		if err != nil {
			return err
		}
		var ordered func() error
		if record != 0 {
			ordered = func() error {
				entry, err := drive.Named(s.Drive)
				if err != nil {
					return err
				}
				return findings.New(a, entry).Clears(cmd.Context(), record)
			}
		}
		if err := s.Clear(tree, live, answers(live), proof, ordered); err != nil {
			return err
		}
		agents, err := launchd.New(a.Runner, cmd.OutOrStdout())
		if err != nil {
			return err
		}
		url, err := Restart(cmd.Context(), agents, s, answers)
		if err != nil {
			return err
		}
		if err := os.WriteFile(s.served(), []byte(tree), 0o600); err != nil {
			return err
		}
		fmt.Fprintln(cmd.OutOrStdout(), url)
		return nil
	}}
	restart.Flags().StringVar(&proof, "proof", "", "the JSON proof that the commit going live was served off the live port, screenshotted and checked against the owner's answers")
	restart.Flags().Int64Var(&record, "record", 0, "the seq of the owner record holding his order to restart; one past the pass that read it waits on its lane's still-unmet record")
	cmd := &cobra.Command{Use: "dashboard", Short: "The drive dashboard's launchd agent, which hosts no session"}
	cmd.AddCommand(
		&cobra.Command{Use: "install", Short: "Write the drive dashboard's launchd agent and bootstrap it", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			s, err := service()
			if err != nil {
				return err
			}
			if s.Python, err = exec.LookPath("python3"); err != nil {
				return err
			}
			agents, err := launchd.New(a.Runner, cmd.OutOrStdout())
			if err != nil {
				return err
			}
			return agents.Install(cmd.Context(), s.Job())
		}},
		restart,
	)
	return cmd
}
