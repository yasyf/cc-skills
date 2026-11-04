package deskhost

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

const deployedHost = `{"cci":{"url":"https://x/cci.tgz","sha256":"a","member":"cci"},"lr":{"url":"https://x/lr.tgz","sha256":"b","member":"lr"},"drive":"d","relay_url":"https://relay","relay_owner":"k"}`

type deskSyncRun struct {
	stdout, stderr, log string
	err                 error
}

func runDeskSync(t *testing.T, deployment string, sumExit string) deskSyncRun {
	t.Helper()
	dir := t.TempDir()
	home := filepath.Join(dir, "home")
	if err := os.MkdirAll(filepath.Join(home, ".local", "bin"), 0o750); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(dir, "log")
	for name, body := range map[string]string{
		"getent":    `echo "desk:x:1:1::` + home + `:/bin/bash"`,
		"aws":       `case "$*" in *deployment*) printf '%s' "$DEPLOYMENT";; *) echo key;; esac`,
		"curl":      `for last; do :; done; touch "$last"`,
		"sha256sum": `cat >/dev/null; exit ` + sumExit,
		"tar":       `printf '#!/bin/sh\necho v1\n'`,
		"runuser":   `echo "runuser $*" >>"$LOG"; if [ "$4" = install ]; then ls -ld "$(dirname "$7")" >>"$LOG"; fi`,
		"systemctl": `echo "systemctl $*" >>"$LOG"`,
	} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte("#!/bin/bash\n"+body+"\n"), 0o700); err != nil { //nolint:gosec // the stub has to execute
			t.Fatal(err)
		}
	}
	cmd := exec.Command("bash", "desk-sync.sh")
	cmd.Env = append(os.Environ(), "PATH="+dir+":"+os.Getenv("PATH"), "DEPLOYMENT="+deployment, "LOG="+log)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	written, readErr := os.ReadFile(log)
	if readErr != nil && !os.IsNotExist(readErr) {
		t.Fatal(readErr)
	}
	return deskSyncRun{stdout.String(), stderr.String(), string(written), err}
}

func TestDeskSyncInstallsThroughATempDirTheDeskUserCanEnter(t *testing.T) {
	run := runDeskSync(t, deployedHost, "0")
	if run.err != nil {
		t.Fatal(run.err, run.stderr)
	}
	if got := strings.Count(run.log, "drwx--x--x"); got != 2 {
		t.Fatalf("%d of 2 installs read from a directory the desk user can enter:\n%s", got, run.log)
	}
	for _, want := range []string{"relay join --instance", "systemctl restart cci.service", "systemctl try-restart desk@*"} {
		if !strings.Contains(run.log, want) {
			t.Fatalf("no %q in:\n%s", want, run.log)
		}
	}
}

func TestDeskSyncRefusesAnArchiveWhoseChecksumDiffers(t *testing.T) {
	run := runDeskSync(t, deployedHost, "1")
	if run.err == nil || !strings.Contains(run.stderr, "refused https://x/cci.tgz") {
		t.Fatal(run.err, run.stderr)
	}
	if strings.Contains(run.log, "install") {
		t.Fatalf("it installed an unchecked archive:\n%s", run.log)
	}
}

func TestDeskSyncWithNothingDeployedTouchesNothing(t *testing.T) {
	run := runDeskSync(t, "{}", "0")
	if run.err != nil || !strings.Contains(run.stdout, "nothing deployed yet") || run.log != "" {
		t.Fatal(run.err, run.stdout, run.log)
	}
}
