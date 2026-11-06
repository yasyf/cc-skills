package dashboard

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type ctl struct {
	calls   [][]string
	printed int
}

func (c *ctl) Run(_ context.Context, command shell.Command) (shell.Result, error) {
	c.calls = append(c.calls, command.Argv)
	if command.Argv[1] == "print" && c.printed != 0 {
		return shell.Result{Code: c.printed}, &shell.ExitError{Argv: command.Argv, Code: c.printed}
	}
	return shell.Result{}, nil
}

func service(t *testing.T) Service {
	s := Service{Drive: "d1", State: t.TempDir(), Claude: "/claude", Python: "/usr/bin/python3"}
	if err := os.WriteFile(s.config(), []byte(`{"drive": "d1", "host": "100.113.56.52", "port": 61210}`), 0o600); err != nil {
		t.Fatal(err)
	}
	return s
}

func agents(c *ctl) launchd.Launchd {
	return launchd.Launchd{Runner: c, Home: "/home", UID: 501, Sleep: func(time.Duration) {}, Out: &bytes.Buffer{}}
}

func TestRestartKickstartsOnlyTheDrivesDashboardAndReturnsItsURLOnceItAnswers(t *testing.T) {
	c := &ctl{}
	var asked []string
	tries := 0

	url, err := Restart(context.Background(), agents(c), service(t), func(url string) bool {
		asked = append(asked, url)
		tries++
		return tries == 3
	})
	if err != nil {
		t.Fatal(err)
	}
	if url != "http://100.113.56.52:61210/" {
		t.Fatalf("url %q", url)
	}
	want := [][]string{{"launchctl", "print", "gui/501/com.yasyf.long-running.dashboard.d1"}, {"launchctl", "kickstart", "-k", "gui/501/com.yasyf.long-running.dashboard.d1"}}
	if !slices.EqualFunc(c.calls, want, slices.Equal) {
		t.Fatalf("calls %v", c.calls)
	}
	if len(asked) != 3 || asked[2] != url {
		t.Fatalf("asked %v", asked)
	}
}

func TestRestartRefusesADashboardLaunchdDoesNotRunAndNamesTheInstall(t *testing.T) {
	c := &ctl{printed: 113}

	_, err := Restart(context.Background(), agents(c), service(t), func(string) bool { return true })

	if err == nil || !strings.Contains(err.Error(), "lr dashboard install") {
		t.Fatalf("err %v", err)
	}
	if len(c.calls) != 1 {
		t.Fatalf("calls %v", c.calls)
	}
}

func TestRestartFailsWhenTheDashboardNeverAnswers(t *testing.T) {
	_, err := Restart(context.Background(), agents(&ctl{}), service(t), func(string) bool { return false })

	if err == nil || !strings.Contains(err.Error(), "did not answer") {
		t.Fatalf("err %v", err)
	}
}

func TestInstallWritesNoCredentialIntoThePlistWhenTheShellHoldsOne(t *testing.T) {
	const key = "csk-sentinel-the-shell-holds"
	home, tools := t.TempDir(), t.TempDir()
	t.Setenv("CEREBRAS_API_KEY", key)
	t.Setenv("HOME", home)
	t.Setenv("PATH", tools)
	if err := os.WriteFile(filepath.Join(tools, "python3"), nil, 0o700); err != nil { //nolint:gosec
		t.Fatal(err)
	}
	config := filepath.Join(t.TempDir(), "lr.json")
	body := `{"repo":"org/repo","drive":"d1","checkout":"/checkouts/repo","author":"drive-bot","label":"drive","movers":["lanes"]}`
	if err := os.WriteFile(config, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	cmd := Command(&app.App{Runner: &ctl{printed: 113}, ConfigPath: config, Now: time.Now})
	cmd.SetArgs([]string{"install"})
	cmd.SetOut(&bytes.Buffer{})
	cmd.SetErr(&bytes.Buffer{})

	if err := cmd.Execute(); err != nil {
		t.Fatal(err)
	}

	written, err := os.ReadFile(filepath.Join(home, "Library", "LaunchAgents", Label("d1")+".plist"))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(written, []byte(key)) {
		t.Fatal("the plist holds the shell's key")
	}
	_, env, _ := strings.Cut(string(written), "<key>EnvironmentVariables</key>")
	env, _, _ = strings.Cut(env, "</dict>")
	keys := regexp.MustCompile(`<key>([^<]+)</key>`).FindAllStringSubmatch(env, -1)
	names := make([]string, 0, len(keys))
	for _, found := range keys {
		names = append(names, found[1])
	}
	if want := []string{"CLAUDE_LONG_RUNNING_DRIVE", "PATH"}; !slices.Equal(names, want) {
		t.Fatalf("plist environment %v, want %v", names, want)
	}
}

func TestTheVerbRefusesADriveThatWouldNameAnotherLabel(t *testing.T) {
	for _, drive := range []string{"d1.supervisor", "d1/../com.long-running.d1.supervisor", "d1 orca"} {
		config := filepath.Join(t.TempDir(), "lr.json")
		body := fmt.Sprintf(`{"repo":"org/repo","drive":%q,"checkout":"/checkouts/repo","author":"drive-bot","label":"drive","movers":["lanes"]}`, drive)
		if err := os.WriteFile(config, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		c := &ctl{}
		cmd := Command(&app.App{Runner: c, ConfigPath: config, Now: time.Now})
		cmd.SetArgs([]string{"restart"})
		cmd.SetOut(&bytes.Buffer{})
		cmd.SetErr(&bytes.Buffer{})

		if err := cmd.Execute(); err == nil || !strings.Contains(err.Error(), "names no drive's dashboard") {
			t.Fatalf("drive %q: err %v", drive, err)
		}
		if len(c.calls) != 0 {
			t.Fatalf("drive %q: calls %v", drive, c.calls)
		}
	}
}
