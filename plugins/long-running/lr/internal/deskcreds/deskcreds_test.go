package deskcreds

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/install"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var t0 = time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC)

func clock() time.Time { return t0 }

func credentials(hours int) map[string]any {
	return map[string]any{"Version": 1, "AccessKeyId": "AKIA", "SecretAccessKey": "secret", "SessionToken": "token", "Expiration": t0.Add(time.Duration(hours) * time.Hour).Format(time.RFC3339)}
}

func serving(t *testing.T, shm string) int {
	t.Helper()
	server := httptest.NewServer(Handler(shm, clock, io.Discard))
	t.Cleanup(server.Close)
	_, port, err := net.SplitHostPort(server.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	number, err := strconv.Atoi(port)
	if err != nil {
		t.Fatal(err)
	}
	return number
}

func put(t *testing.T, port int, key string, body any) int {
	t.Helper()
	encoded, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	request, err := http.NewRequestWithContext(context.Background(), http.MethodPut, fmt.Sprintf("http://127.0.0.1:%d/%s", port, key), strings.NewReader(string(encoded)))
	if err != nil {
		t.Fatal(err)
	}
	answer, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = answer.Body.Close() }()
	return answer.StatusCode
}

func TestAPushedProfileReadsBackForItsCredentialProcess(t *testing.T) {
	port := serving(t, t.TempDir())
	if status := put(t, port, "release-operator", credentials(1)); status != http.StatusNoContent {
		t.Fatalf("put %d", status)
	}
	var whole, field strings.Builder
	if err := Get(context.Background(), "release-operator", "", port, &whole); err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal([]byte(whole.String()), &got); err != nil {
		t.Fatal(err)
	}
	if got["AccessKeyId"] != "AKIA" || got["Version"] != float64(1) || len(got) != 5 {
		t.Fatalf("got %v", got)
	}
	if err := Get(context.Background(), "release-operator", "AccessKeyId", port, &field); err != nil || field.String() != "AKIA\n" {
		t.Fatalf("got %q %v", field.String(), err)
	}
}

func TestExpiredOrUnpushedCredentialsAreNeverServed(t *testing.T) {
	port := serving(t, t.TempDir())
	if status := put(t, port, "core-auto", credentials(-1)); status != http.StatusBadRequest {
		t.Fatalf("put %d", status)
	}
	var out strings.Builder
	if err := Get(context.Background(), "core-auto", "", port, &out); err == nil {
		t.Fatalf("served %q", out.String())
	}
}

func TestAPushedSecretReadsBackUntilItExpires(t *testing.T) {
	port := serving(t, t.TempDir())
	ahead := t0.Add(45 * time.Minute).Format(time.RFC3339)
	if status := put(t, port, "secret/GH_TOKEN", map[string]string{"Value": "gho_user", "Expiration": ahead}); status != http.StatusNoContent {
		t.Fatalf("put %d", status)
	}
	held, err := Fetch(context.Background(), "secret/GH_TOKEN", port)
	if err != nil || string(held["Value"]) != `"gho_user"` {
		t.Fatalf("held %v %v", held, err)
	}
	if status := put(t, port, "secret/GH_TOKEN", map[string]string{"Value": "gho_old", "Expiration": t0.Add(-time.Minute).Format(time.RFC3339)}); status != http.StatusBadRequest {
		t.Fatalf("expired put %d", status)
	}
	if status := put(t, port, "secret/BUILDKITE_TOKEN", map[string]string{"Expiration": ahead}); status != http.StatusBadRequest {
		t.Fatalf("valueless put %d", status)
	}
	if _, err := Fetch(context.Background(), "secret/BUILDKITE_TOKEN", port); err == nil {
		t.Fatal("served a secret nobody pushed")
	}
}

func TestAPushedUserTokenReplacesTheOneEachRunningDeskReadsGHWith(t *testing.T) {
	shm := t.TempDir()
	hosts := filepath.Join(shm, "d-gh", "hosts.yml")
	before := "github.com:\n    users:\n        kennedy94:\n            oauth_token: gho_old\n    git_protocol: https\n    oauth_token: gho_old\n    user: kennedy94\n"
	if err := writeBeside(hosts, before); err != nil {
		t.Fatal(err)
	}
	port := serving(t, shm)
	ahead := t0.Add(45 * time.Minute).Format(time.RFC3339)
	if status := put(t, port, "secret/BUILDKITE_TOKEN", map[string]string{"Value": "bk", "Expiration": ahead}); status != http.StatusNoContent {
		t.Fatalf("put %d", status)
	}
	if data, err := os.ReadFile(hosts); err != nil || string(data) != before {
		t.Fatalf("another secret moved the token: %s %v", data, err)
	}
	if status := put(t, port, "secret/GH_TOKEN", map[string]string{"Value": "gho_$1new", "Expiration": ahead}); status != http.StatusNoContent {
		t.Fatalf("put %d", status)
	}
	data, err := os.ReadFile(hosts)
	if err != nil {
		t.Fatal(err)
	}
	if want := strings.ReplaceAll(before, "gho_old", "gho_$1new"); string(data) != want {
		t.Fatalf("hosts %s", data)
	}
	info, err := os.Stat(hosts)
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("mode %v %v", info, err)
	}
	entries, err := os.ReadDir(filepath.Dir(hosts))
	if err != nil || len(entries) != 1 {
		t.Fatalf("entries %v %v", entries, err)
	}
}

type fakeHost struct {
	calls   []shell.Command
	replies map[string]shell.Result
	fail    map[string]bool
}

func (f *fakeHost) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	f.calls = append(f.calls, c)
	joined := strings.Join(c.Argv, " ")
	for needle := range f.fail {
		if strings.Contains(joined, needle) {
			return shell.Result{Code: 1, Stderr: "refused\n"}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: "refused\n"}
		}
	}
	for needle, reply := range f.replies {
		if strings.Contains(joined, needle) {
			return reply, nil
		}
	}
	return shell.Result{}, nil
}

func (f *fakeHost) index(needle string) int {
	return slices.IndexFunc(f.calls, func(c shell.Command) bool { return strings.Contains(strings.Join(c.Argv, " "), needle) })
}

func writeConfig(t *testing.T, dir string, config map[string]any) string {
	t.Helper()
	data, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "runner.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func envFor(t *testing.T, runner shell.Runner) (Env, *strings.Builder) {
	t.Helper()
	var out strings.Builder
	return Env{Runner: runner, Out: &out, Err: &out, Now: clock, Plugin: filepath.Join(t.TempDir(), "long-running", "0.7.99"), Home: t.TempDir(), UID: 501, Path: "/usr/bin", SHM: t.TempDir()}, &out
}

func TestPushHandsEachSecretOverStandardInputWithAnExpiryThreePushesOut(t *testing.T) {
	exported, err := json.Marshal(credentials(1))
	if err != nil {
		t.Fatal(err)
	}
	host := &fakeHost{replies: map[string]shell.Result{"aws configure": {Stdout: string(exported)}, "gh auth token": {Stdout: "gho_user\n"}}}
	e, _ := envFor(t, host)
	config := writeConfig(t, t.TempDir(), map[string]any{"host": map[string]any{"sprite": "dar-desk-1", "aws_profiles": []string{"core-auto"}, "secrets": map[string][]string{"GH_TOKEN": {"gh", "auth", "token"}}}})

	if err := e.Push(context.Background(), config, 9181); err != nil {
		t.Fatal(err)
	}

	sent := map[string]map[string]any{}
	for _, call := range host.calls {
		if call.Argv[0] != "sprite" {
			continue
		}
		var body map[string]any
		if err := json.Unmarshal([]byte(call.Stdin), &body); err != nil {
			t.Fatal(err)
		}
		_, key, _ := strings.Cut(call.Argv[len(call.Argv)-1], "9181/")
		sent[key] = body
		if strings.Contains(strings.Join(call.Argv, " "), "gho_user") {
			t.Fatalf("a secret reached an argument: %v", call.Argv)
		}
	}
	if sent["core-auto"]["AccessKeyId"] != "AKIA" || sent["secret/GH_TOKEN"]["Value"] != "gho_user" {
		t.Fatalf("sent %v", sent)
	}
	if sent["secret/GH_TOKEN"]["Expiration"] != t0.Add(45*time.Minute).Format(time.RFC3339) {
		t.Fatalf("expiry %v", sent["secret/GH_TOKEN"]["Expiration"])
	}
}

func TestAFailedPushNamesWhatDidNotArriveAndStillSendsTheRest(t *testing.T) {
	host := &fakeHost{fail: map[string]bool{"--profile core-auto": true}, replies: map[string]shell.Result{"gh auth token": {Stdout: "gho_user\n"}}}
	e, out := envFor(t, host)
	config := writeConfig(t, t.TempDir(), map[string]any{"host": map[string]any{"sprite": "s", "aws_profiles": []string{"core-auto"}, "secrets": map[string][]string{"GH_TOKEN": {"gh", "auth", "token"}}}})

	err := e.Push(context.Background(), config, 9181)

	if err == nil || !strings.Contains(out.String(), "core-auto did not export: refused") {
		t.Fatalf("got %v, said %q", err, out.String())
	}
	if host.index("secret/GH_TOKEN") < 0 {
		t.Fatalf("the secret was not sent: %+v", host.calls)
	}
}

func runConfig(t *testing.T, dir string) string {
	t.Helper()
	return writeConfig(t, dir, map[string]any{"drive": "d", "host": map[string]any{
		"aws_profiles": []string{"core-auto"}, "region": "us-west-2", "github_app": map[string]any{"name": "app", "client_id": "Iv1", "private_key_command": []string{"aws", "ssm", "get-parameter"}},
		"gh_user": "kennedy94", "tz": "America/Los_Angeles", "secrets": map[string][]string{"GH_TOKEN": {"gh"}, "TRUNK_TOKEN": {"x"}},
		"ssm": map[string]string{"BUILDKITE_READ": "/ci/tools/buildkite-read"}, "ssm_profile": "core-auto",
	}})
}

func pushedSecrets(t *testing.T, shm string) int {
	t.Helper()
	port := serving(t, shm)
	ahead := t0.Add(time.Hour).Format(time.RFC3339)
	for name, value := range map[string]string{"GH_TOKEN": "gho_user", "TRUNK_TOKEN": "trunk"} {
		if status := put(t, port, "secret/"+name, map[string]string{"Value": value, "Expiration": ahead}); status != http.StatusNoContent {
			t.Fatalf("put %d", status)
		}
	}
	return port
}

type execed struct {
	path string
	argv []string
	env  []string
}

func TestRunKeepsTheUserTokenOutOfTheEnvironmentAndExportsTheRest(t *testing.T) {
	host := &fakeHost{replies: map[string]shell.Result{"aws ssm": {Stdout: "bk-read\n"}}}
	e, _ := envFor(t, host)
	dir := t.TempDir()
	config := runConfig(t, dir)
	e.Port = pushedSecrets(t, t.TempDir())
	var ran execed
	e.Exec = func(path string, argv, env []string) error {
		ran = execed{path, argv, env}
		return nil
	}

	if err := e.Run(context.Background(), config, "landing", false); err != nil {
		t.Fatal(err)
	}

	joined := strings.Join(ran.env, "\n")
	if strings.Contains(joined, "GH_TOKEN=") || strings.Contains(joined, "gho_user") {
		t.Fatal("the user token reached the environment")
	}
	for _, want := range []string{"TRUNK_TOKEN=trunk", "BUILDKITE_READ=bk-read", "TZ=America/Los_Angeles", "GH_CONFIG_DIR=" + filepath.Join(e.SHM, "d-gh"), "HOME=" + e.Home} {
		if !slices.Contains(ran.env, want) {
			t.Fatalf("no %q in the environment", want)
		}
	}
	if count := len(slices.DeleteFunc(slices.Clone(ran.env), func(pair string) bool { return !strings.HasPrefix(pair, "PATH=") })); count != 1 {
		t.Fatalf("%d PATH entries", count)
	}
	hosts, err := os.ReadFile(filepath.Join(e.SHM, "d-gh", "hosts.yml"))
	if err != nil || !strings.Contains(string(hosts), "oauth_token: gho_user") {
		t.Fatalf("hosts %s %v", hosts, err)
	}
	aws, err := os.ReadFile(filepath.Join(e.Home, ".aws", "config"))
	if err != nil || !strings.Contains(string(aws), "credential_process = ") || !strings.Contains(string(aws), " get core-auto\nregion = us-west-2\n") {
		t.Fatalf("aws %s %v", aws, err)
	}
	app, err := os.ReadFile(filepath.Join(e.Home, ".config", "ccx", "github-app.toml"))
	if err != nil || !strings.Contains(string(app), `private_key_command = ["aws","ssm","get-parameter"]`) {
		t.Fatalf("app %s %v", app, err)
	}
	if _, err := os.Stat(filepath.Join(dir, "landing-runner.log")); err != nil {
		t.Fatal(err)
	}
	if ran.path != "/bin/sh" || !slices.Equal(ran.argv[len(ran.argv)-5:], []string{"run", "--config", config, "--desk", "landing"}) {
		t.Fatalf("ran %+v", ran)
	}
}

func TestRunPreflightAsksTheDeskRunnerUnderTheSameEnvironmentAndOpensNoLog(t *testing.T) {
	host := &fakeHost{replies: map[string]shell.Result{"aws ssm": {Stdout: "bk-read\n"}}}
	e, _ := envFor(t, host)
	dir := t.TempDir()
	config := runConfig(t, dir)
	e.Port = pushedSecrets(t, t.TempDir())
	var ran execed
	e.Exec = func(path string, argv, env []string) error {
		ran = execed{path, argv, env}
		return nil
	}

	if err := e.Run(context.Background(), config, "landing", true); err != nil {
		t.Fatal(err)
	}

	if !slices.Equal(ran.argv[1:], []string{"preflight", "--config", config, "--desk", "landing", "--json"}) || !slices.Contains(ran.env, "TRUNK_TOKEN=trunk") {
		t.Fatalf("ran %+v", ran.argv)
	}
	if _, err := os.Stat(filepath.Join(dir, "landing-runner.log")); err == nil {
		t.Fatal("a preflight opened the desk's log")
	}
}

func TestRunRefusesAHostWithNoUserToken(t *testing.T) {
	host := &fakeHost{}
	e, _ := envFor(t, host)
	config := writeConfig(t, t.TempDir(), map[string]any{"drive": "d", "host": map[string]any{"secrets": map[string][]string{}}})
	e.Exec = func(string, []string, []string) error {
		t.Fatal("started a desk with no token")
		return nil
	}

	if err := e.Run(context.Background(), config, "landing", false); err == nil || !strings.Contains(err.Error(), "names no GH_TOKEN") {
		t.Fatalf("got %v", err)
	}
}

func installWorld(t *testing.T, host *fakeHost) (Env, string) {
	t.Helper()
	e, _ := envFor(t, host)
	if err := writeBeside(filepath.Join(e.Home, ".claude", "long-running", "drives", "d.json"), `{"checkout":"/co"}`); err != nil {
		t.Fatal(err)
	}
	config := writeConfig(t, t.TempDir(), map[string]any{"drive": "d", "version": "0.7.99", "landing": map[string]any{"repo": "o/r"}, "host": map[string]any{"sprite": "dar-desk-1", "aws_profiles": []string{}, "secrets": map[string][]string{}}})
	return e, config
}

func TestInstallCopiesByFileThenStartsTheCredentialServiceBeforeTheDesk(t *testing.T) {
	host := &fakeHost{fail: map[string]bool{"launchctl print": true}}
	e, config := installWorld(t, host)

	if err := e.Install(context.Background(), config, "landing"); err != nil {
		t.Fatal(err)
	}

	order := []string{"tar --no-xattrs", "--file", "drives/d.json", "test -f", servicesUp, "launchctl bootstrap", deskService}
	last := -1
	for _, needle := range order {
		at := host.index(needle)
		if at <= last && needle != "--file" {
			t.Fatalf("%q at %d after %d: %+v", needle, at, last, host.calls)
		}
		last = max(last, at)
	}
	copying := host.calls[host.index(install.SpritePlugins)]
	if copying.Stdin != "" || !slices.Contains(copying.Argv, "--no-stdin") {
		t.Fatalf("the plugin copy streams over stdin: %+v", copying)
	}
	plistData, err := os.ReadFile(filepath.Join(e.Home, "Library", "LaunchAgents", "com.long-running.d.desk-creds.plist"))
	if err != nil || !strings.Contains(string(plistData), "<integer>900</integer>") || !strings.Contains(string(plistData), filepath.Join(e.Plugin, "bin", "desk-creds.py")) {
		t.Fatalf("plist %s %v", plistData, err)
	}
}

func TestInstallCopiesTheStateOnlyToAHostWithNoRunnerJSONYet(t *testing.T) {
	missing := &fakeHost{fail: map[string]bool{"launchctl print": true, "test -f": true}}
	e, config := installWorld(t, missing)
	state := filepath.Dir(config)
	if err := writeBeside(filepath.Join(state, "lr.sqlite"), "x"); err != nil {
		t.Fatal(err)
	}
	if err := writeBeside(filepath.Join(state, "desk.log"), "x"); err != nil {
		t.Fatal(err)
	}
	if err := e.Install(context.Background(), config, "landing"); err != nil {
		t.Fatal(err)
	}
	at := slices.IndexFunc(missing.calls, func(c shell.Command) bool { return c.Argv[0] == "tar" && slices.Contains(c.Argv, "lr.sqlite") })
	if at < 0 || slices.Contains(missing.calls[at].Argv, "desk.log") || slices.Contains(missing.calls[at].Argv, "runner.json") {
		t.Fatalf("calls %+v", missing.calls)
	}

	present := &fakeHost{fail: map[string]bool{"launchctl print": true}}
	e, config = installWorld(t, present)
	if err := e.Install(context.Background(), config, "landing"); err != nil {
		t.Fatal(err)
	}
	tars := slices.DeleteFunc(slices.Clone(present.calls), func(c shell.Command) bool { return c.Argv[0] != "tar" })
	if len(tars) != 1 {
		t.Fatalf("tars %+v", tars)
	}
}

func TestAnUnchangedPushAgentIsLeftLoaded(t *testing.T) {
	host := &fakeHost{}
	e, _ := envFor(t, host)
	config := "/s/runner.json"
	wanted := plist("com.long-running.d.desk-creds", filepath.Join(e.Plugin, "bin", "desk-creds.py"), config, e.Path, filepath.Join(e.Home, ".claude", "long-running", "com.long-running.d.desk-creds.log"))
	if err := writeBeside(filepath.Join(e.Home, "Library", "LaunchAgents", "com.long-running.d.desk-creds.plist"), string(wanted)); err != nil {
		t.Fatal(err)
	}

	if err := e.keepPushing(context.Background(), "d", config); err != nil {
		t.Fatal(err)
	}

	if host.index("bootout") >= 0 || host.index("bootstrap") >= 0 {
		t.Fatalf("calls %+v", host.calls)
	}
}

func TestShellQuoteOnlyQuotesWhatNeedsIt(t *testing.T) {
	if got := shellQuote("/a/b-c/desk-creds.py"); got != "/a/b-c/desk-creds.py" {
		t.Fatal(got)
	}
	if got := shellQuote("/a b/c's"); got != `'/a b/c'"'"'s'` {
		t.Fatal(got)
	}
}
