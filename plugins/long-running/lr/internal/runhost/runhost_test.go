package runhost

import (
	"bytes"
	"context"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const pairing = "orca://pair/secret-code"

var settings = Settings{Host: "frg-desk-host", Profile: "devbox-machine", Region: "us-west-2", Environment: "desk-host", Port: 6768}

type agents struct{ jobs []launchd.Job }

func (a *agents) Install(_ context.Context, job launchd.Job) error {
	a.jobs = append(a.jobs, job)
	return nil
}

func readyLine(build, endpoint string) string {
	return `{"type":"orca_server_ready","boundEndpoint":"` + endpoint + `","pairing":{"available":true,"url":"` + pairing + `"},"health":{"buildVersion":"` + build + `"}}`
}

func TestTheHostsReadyEventMustComeFromOrcasOwnServerOnThePort(t *testing.T) {
	for _, tc := range []struct {
		name, printed, refusal string
	}{
		{"ready", readyLine("1.4.2", "127.0.0.1:6768"), ""},
		{"never started", "", "has logged no orca_server_ready event"},
		{"an Electron fallback", readyLine("", "127.0.0.1:6768"), "fell back to Electron"},
		{"another port", readyLine("1.4.2", "127.0.0.1:7000"), "not port 6768"},
		{"no pairing", strings.Replace(readyLine("1.4.2", "127.0.0.1:6768"), `"available":true`, `"available":false`, 1), "offers no pairing"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, err := readyOf(tc.printed, 6768)
			if tc.refusal == "" && err != nil || tc.refusal != "" && (err == nil || !strings.Contains(err.Error(), tc.refusal)) {
				t.Fatalf("readyOf = %v, want %q", err, tc.refusal)
			}
		})
	}
}

func TestInstallKeepsAForwardToTheHostAndPairsThisMacWithoutPrintingTheCode(t *testing.T) {
	fake := &shelltest.Fake{}
	aws := []string{"aws", "--profile", "devbox-machine", "--region", "us-west-2", "ssm"}
	fake.Reply("cmd-1\n", append(aws, "send-command")...)
	invocation := strings.NewReplacer(`"`, `\"`).Replace(readyLine("1.4.2", "127.0.0.1:6768"))
	fake.Reply(`{"status":"Success","output":"`+invocation+`\n"}`, append(aws, "list-command-invocations")...)
	fake.Fail(1, "no such environment", "orca", "environment", "show")
	fake.Reply("{}", "orca", "environment", "add")
	jobs := &agents{}
	var out bytes.Buffer
	host := Host{Runner: fake, Agents: jobs, Dial: func(string) error { return nil }, Sleep: func(time.Duration) {}, Out: &out}
	if err := host.Install(context.Background(), settings, "brook", "/lr", "/tmp/run-host.log"); err != nil {
		t.Fatal(err)
	}
	if len(jobs.jobs) != 1 || !slices.Equal(jobs.jobs[0].Arguments, []string{"/lr", "run-host", "forward", "--host", "frg-desk-host", "--profile", "devbox-machine", "--region", "us-west-2", "--port", "6768"}) {
		t.Fatalf("jobs = %+v", jobs.jobs)
	}
	var added []string
	for _, call := range fake.Calls() {
		if slices.Contains(call.Argv, "add") {
			added = call.Argv
		}
	}
	if !slices.Contains(added, pairing) || !slices.Contains(added, "desk-host") {
		t.Fatalf("orca environment add ran as %q", added)
	}
	if strings.Contains(out.String(), pairing) {
		t.Fatalf("the pairing code was printed: %s", out.String())
	}
}

func TestTheForwardTargetsTheOneRunningInstanceTheHostNameTags(t *testing.T) {
	describe := []string{"aws", "--profile", "devbox-machine", "--region", "us-west-2", "ec2", "describe-instances"}
	for _, tc := range []struct {
		name, listed, refusal string
	}{
		{"one instance", "i-0abc\n", ""},
		{"none running", "\n", "0 running instances"},
		{"a replacement still draining", "i-0abc\ti-0def\n", "2 running instances"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			fake := &shelltest.Fake{}
			fake.Reply(tc.listed, describe...)
			argv, err := ForwardArgv(context.Background(), fake, settings)
			if tc.refusal != "" {
				if err == nil || !strings.Contains(err.Error(), tc.refusal) {
					t.Fatalf("ForwardArgv = %q, %v, want %q", argv, err, tc.refusal)
				}
				return
			}
			if err != nil || !slices.Contains(argv, "i-0abc") || !slices.Contains(argv, `{"portNumber":["6768"],"localPortNumber":["6768"]}`) || !slices.Contains(argv, "AWS-StartPortForwardingSession") {
				t.Fatalf("ForwardArgv = %q, %v", argv, err)
			}
		})
	}
}
