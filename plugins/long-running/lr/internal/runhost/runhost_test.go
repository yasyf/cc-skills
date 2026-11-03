package runhost

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const pairingCode = "orca://pair?code=private-pairing-data"

var settings = Settings{Sprite: "dar-desk-1", Port: 7300, Environment: "desk-host"}

func readyLine(build string) string {
	return `{"type":"orca_server_ready","schemaVersion":1,"boundEndpoint":"ws://0.0.0.0:7300","advertisedEndpoint":"ws://127.0.0.1:7300","pairing":{"available":true,"url":"` + pairingCode + `","endpoint":"ws://127.0.0.1:7300"},"health":{"buildVersion":"` + build + `"}}` + "\n"
}

type agents struct{ jobs []launchd.Job }

func (a *agents) Install(_ context.Context, job launchd.Job) error {
	a.jobs = append(a.jobs, job)
	return nil
}

type world struct {
	runner *shelltest.Fake
	agents *agents
	dials  []string
	out    strings.Builder
}

func newWorld() *world { return &world{runner: &shelltest.Fake{}, agents: &agents{}} }

func (w *world) host(opened int) Host {
	return Host{
		Runner: w.runner,
		Agents: w.agents,
		Dial: func(address string) error {
			w.dials = append(w.dials, address)
			if len(w.dials) < opened {
				return errors.New("connection refused")
			}
			return nil
		},
		Sleep: func(time.Duration) {},
		Out:   &w.out,
	}
}

func (w *world) ran(prefix ...string) bool {
	return slices.ContainsFunc(w.runner.Calls(), func(c shell.Command) bool {
		return len(c.Argv) >= len(prefix) && slices.Equal(c.Argv[:len(prefix)], prefix)
	})
}

func TestInstallServesTheRunOnTheDeskHostForwardsItAndPairsTheMacWithoutPrintingTheCode(t *testing.T) {
	w := newWorld()
	w.runner.Reply(readyLine("0.1.0+15ec2904eb00"), "sprite", "exec")
	w.runner.Fail(1, "no such environment", "orca", "environment", "show")
	w.runner.Reply(`{"ok":true}`, "orca", "environment", "add")

	err := w.host(3).Install(context.Background(), settings, "d", "/drive/run-host-proxy.log", "/usr/bin:/opt/homebrew/bin")
	if err != nil {
		t.Fatal(err)
	}

	served := w.runner.Calls()[0].Argv
	if !slices.Equal(served[:4], []string{"sprite", "exec", "-s", "dar-desk-1"}) || !slices.Equal(served[len(served)-4:], []string{State, "7300", "180", Service}) {
		t.Fatalf("served %q", served)
	}
	if len(w.agents.jobs) != 1 {
		t.Fatalf("jobs %+v", w.agents.jobs)
	}
	job := w.agents.jobs[0]
	if job.Label != "com.long-running.d.run-host" || !slices.Equal(job.Arguments, []string{"/usr/bin/env", "sprite", "proxy", "-s", "dar-desk-1", "7300:7300"}) || job.Log != "/drive/run-host-proxy.log" {
		t.Fatalf("job %+v", job)
	}
	if len(w.dials) != 3 || w.dials[0] != "127.0.0.1:7300" {
		t.Fatalf("dials %q", w.dials)
	}
	if !w.ran("orca", "environment", "add", "--name", "desk-host", "--pairing-code", pairingCode, "--json") {
		t.Fatalf("not paired: %+v", w.runner.Calls())
	}
	if strings.Contains(w.out.String(), "private-pairing-data") {
		t.Fatalf("the pairing code was printed: %s", w.out.String())
	}
	if !strings.Contains(w.out.String(), "now reaches it as Orca environment desk-host") {
		t.Fatalf("said %q", w.out.String())
	}
}

func TestInstallLeavesAnEnvironmentTheMacAlreadyHas(t *testing.T) {
	w := newWorld()
	w.runner.Reply(readyLine("0.1.0+15ec2904eb00"), "sprite", "exec")
	w.runner.Reply(`{"ok":true}`, "orca", "environment", "show")

	if err := w.host(1).Install(context.Background(), settings, "d", "/drive/run-host-proxy.log", "/usr/bin"); err != nil {
		t.Fatal(err)
	}

	if w.ran("orca", "environment", "add") {
		t.Fatal("paired a second time")
	}
	if !strings.Contains(w.out.String(), "already reaches it") {
		t.Fatalf("said %q", w.out.String())
	}
}

func TestAServerThatFellBackToElectronOrNeverCameUpChangesNothingOnTheMac(t *testing.T) {
	for name, reply := range map[string]func(*shelltest.Fake){
		"electron": func(f *shelltest.Fake) { f.Reply(readyLine(""), "sprite", "exec") },
		"refused": func(f *shelltest.Fake) {
			f.Fail(3, "another Orca server runs on this host, and two would share its state:\n41 orca-ide serve --port 54749", "sprite", "exec")
		},
		"silent": func(f *shelltest.Fake) { f.Reply("", "sprite", "exec") },
	} {
		t.Run(name, func(t *testing.T) {
			w := newWorld()
			reply(w.runner)

			err := w.host(1).Install(context.Background(), settings, "d", "/drive/run-host-proxy.log", "/usr/bin")

			if err == nil || len(w.agents.jobs) != 0 || w.ran("orca") || len(w.dials) != 0 {
				t.Fatalf("err %v, jobs %+v, calls %+v", err, w.agents.jobs, w.runner.Calls())
			}
			if strings.Contains(err.Error(), "private-pairing-data") {
				t.Fatalf("the error carries the pairing code: %v", err)
			}
		})
	}
}

func TestAForwardThatNeverOpensRefusesBeforePairing(t *testing.T) {
	w := newWorld()
	w.runner.Reply(readyLine("0.1.0+15ec2904eb00"), "sprite", "exec")

	err := w.host(forwardTries+1).Install(context.Background(), settings, "d", "/drive/run-host-proxy.log", "/usr/bin")

	if err == nil || !strings.Contains(err.Error(), "read /drive/run-host-proxy.log") || w.ran("orca") {
		t.Fatalf("err %v, calls %+v", err, w.runner.Calls())
	}
}

func TestARefusedPairingNamesOrcasReasonAndNotTheCode(t *testing.T) {
	w := newWorld()
	w.runner.Reply(readyLine("0.1.0+15ec2904eb00"), "sprite", "exec")
	w.runner.Fail(1, "", "orca", "environment", "show")
	w.runner.FailOut(1, `{"ok":false,"error":{"code":"invalid_argument","message":"Invalid remote pairing code."}}`, "orca", "environment", "add")

	err := w.host(1).Install(context.Background(), settings, "d", "/drive/run-host-proxy.log", "/usr/bin")

	if err == nil || !strings.Contains(err.Error(), "invalid_argument: Invalid remote pairing code.") || strings.Contains(err.Error(), "private-pairing-data") {
		t.Fatalf("err %v", err)
	}
}

func TestLoadRefusesARunnerThatNamesNoServer(t *testing.T) {
	dir := t.TempDir()
	for name, host := range map[string]map[string]any{
		"no sprite": {"orca_server": map[string]any{"port": 7300, "environment": "desk-host"}},
		"no server": {"sprite": "dar-desk-1"},
		"no port":   {"sprite": "dar-desk-1", "orca_server": map[string]any{"environment": "desk-host"}},
	} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(dir, strings.ReplaceAll(name, " ", "-")+".json")
			data, err := json.Marshal(map[string]any{"drive": "d", "host": host})
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, data, 0o600); err != nil {
				t.Fatal(err)
			}
			if _, err := Load(path); err == nil {
				t.Fatal("loaded")
			}
		})
	}
}

func stub(t *testing.T, bin, name, body string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(bin, name), []byte("#!/bin/sh\n"+body+"\n"), 0o700); err != nil {
		t.Fatal(err)
	}
}

func serveUpIn(t *testing.T, others, services string) (string, string, error) {
	t.Helper()
	root := t.TempDir()
	bin, dir := filepath.Join(root, "bin"), filepath.Join(root, "run-host")
	if err := os.Mkdir(bin, 0o700); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(services, Service) {
		if err := os.Mkdir(dir, 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "serve.json"), []byte(readyLine("0.1.0+15ec2904eb00")), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	stub(t, bin, "pgrep", `printf '%s' "$PGREP_SAYS"`)
	stub(t, bin, "sprite-env", `case "$2" in
list) printf '%s' "$SERVICES" ;;
create) echo "$@" >>"$CREATED"; printf '%s\n' "$READY" >"$SERVE_DIR/serve.json" ;;
esac`)
	created := filepath.Join(root, "created")
	cmd := exec.Command("sh", "-c", serveUp, "sh", dir, "7300", "5", Service)
	cmd.Env = append(os.Environ(), "PATH="+bin+":/usr/bin:/bin", "PGREP_SAYS="+others, "SERVICES="+services, "CREATED="+created, "SERVE_DIR="+dir, "READY="+strings.TrimSpace(readyLine("0.1.0+15ec2904eb00")))
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	creates, _ := os.ReadFile(created)
	return string(out), string(creates) + stderr.String(), err
}

func TestTheHostScriptStartsOneServerAndRefusesBesideAnother(t *testing.T) {
	printed, said, err := serveUpIn(t, "", "[]")
	if err != nil || !strings.Contains(said, "services create "+Service) {
		t.Fatalf("err %v, said %q", err, said)
	}
	if _, err := readyOf(printed, 7300); err != nil {
		t.Fatalf("ready %v", err)
	}

	printed, said, err = serveUpIn(t, "", `[{"name":"`+Service+`","state":{"status":"running"}}]`)
	if err != nil || strings.Contains(said, "services create") {
		t.Fatalf("a running service was created again: err %v, said %q", err, said)
	}
	if _, err := readyOf(printed, 7300); err != nil {
		t.Fatalf("the running server's ready event: %v", err)
	}

	_, said, err = serveUpIn(t, "41 orca-ide serve --port 54749 --pairing-address 127.0.0.1 --json", "[]")
	if err == nil || !strings.Contains(said, "another Orca server runs on this host") || strings.Contains(said, "services create") {
		t.Fatalf("err %v, said %q", err, said)
	}

	_, said, err = serveUpIn(t, "41 orca serve --port 7300 --pairing-address 127.0.0.1 --json", "[]")
	if err != nil || !strings.Contains(said, "services create") {
		t.Fatalf("its own server refused it: err %v, said %q", err, said)
	}
}
