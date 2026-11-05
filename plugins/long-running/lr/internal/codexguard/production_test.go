package codexguard

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	productionConfig = "long-running.productionkubecontext plat-usw2-prod\nlong-running.productionkubenamespace plat-prod\nlong-running.productiondbhost prod-db.internal\n"
	podExec          = "kubectl --context plat-usw2-prod exec api-runtime-0 -- ls /traces"
	dbReadCall       = `ci db-read --sql "SELECT id FROM iris_sessions" --reason count`
	approvalNote     = "  # ccx:owner-approval=491400"
)

func productionFake(currentContext string) *shelltest.Fake {
	runner := &shelltest.Fake{}
	runner.Reply(productionConfig, productionConfigRead...)
	runner.Reply(currentContext+"\n", currentContextRead...)
	return runner
}

func withApproval(runner *shelltest.Fake, lane, kind, text string) *shelltest.Fake {
	runner.Reply(`{"lane":"`+lane+`","kind":"`+kind+`","text":"`+text+`"}`+"\n", "cci", "show", "491400", "--json")
	runner.Reply(text, "cci", "show", "491400", "--body")
	return runner
}

func TestProductionReadRefusals(t *testing.T) {
	cases := []struct {
		command string
		current string
		refused bool
	}{
		{"kubectl --context plat-usw2-prod -n plat exec -it api-runtime-0 -- sh", "plat-usw2-staging", true},
		{"kubectl exec api-runtime-0 -- cat /traces | ssh pass-1 'cat > /tmp/traces'", "plat-usw2-prod", true},
		{"kubectl -n plat-prod cp api-runtime-0:/tmp/traces ./traces", "plat-usw2-staging", true},
		{"kubectl --context=plat-usw2-prod port-forward pod/api-runtime-0 5432:5432", "plat-usw2-staging", true},
		{"aws ssm get-parameter --name /app/forge/api/turbopuffer_api_key --with-decryption --profile plat-prod", "plat-usw2-staging", true},
		{"ssh -i ~/.ssh/pass pass-1 'aws ssm get-parameter --name /app/forge/api/turbopuffer_api_key --with-decryption'", "plat-usw2-staging", true},
		{`ssh pass-1 "bash -c 'aws ssm get-parameters --names /a /b --with-decryption'"`, "plat-usw2-staging", true},
		{"sprite exec -s desk -- aws secretsmanager get-secret-value --secret-id prod/db", "plat-usw2-staging", true},
		{"nsc ssh box-1 -- security find-generic-password -s cci-service -w", "plat-usw2-staging", true},
		{"ssh pass-1 <<'EOF'\nop read op://Private/turbopuffer/key\nEOF", "plat-usw2-staging", true},
		{"op item get Turbopuffer --fields credential --reveal", "plat-usw2-staging", true},
		{"cat > /tmp/stream.sh <<'EOF'\n" + podExec + "\nEOF\nbash /tmp/stream.sh", "plat-usw2-staging", true},
		{dbReadCall, "plat-usw2-staging", true},
		{"tools/ci bk api -X POST /organizations/forge/pipelines/db-read/builds", "plat-usw2-staging", true},
		{"bk build create --pipeline forge/db-read --env SQL='select id from teams'", "plat-usw2-staging", true},
		{"PGHOST=prod-db.internal psql -c 'select id from users'", "plat-usw2-staging", true},
		{"psql postgres://reader@prod-db.internal:5432/forge -c 'select * from teams'", "plat-usw2-staging", true},
		{"kubectl --context plat-usw2-staging exec api-runtime-0 -- ls", "plat-usw2-staging", false},
		{"kubectl exec api-runtime-0 -- ls", "plat-usw2-staging", false},
		{"kubectl --context plat-usw2-prod logs api-runtime-0", "plat-usw2-staging", false},
		{"kubectl --context plat-usw2-prod get pods", "plat-usw2-staging", false},
		{"ssh pass-1 'aws ssm get-parameter --name /config/region'", "plat-usw2-staging", false},
		{"aws ssm describe-parameters --profile plat-prod", "plat-usw2-staging", false},
		{"security find-generic-password -s cci-service", "plat-usw2-staging", false},
		{"op item get Turbopuffer", "plat-usw2-staging", false},
		{"aws sso login --profile plat-prod && desk-creds", "plat-usw2-staging", false},
		{"ci break-glass --reason 'apply the blocked stack'", "plat-usw2-staging", false},
		{"bk build create --pipeline forge/monorepo", "plat-usw2-staging", false},
		{"psql -h localhost -c 'select id from users'", "plat-usw2-staging", false},
		{"echo '" + podExec + "'", "plat-usw2-staging", false},
	}
	for _, c := range cases {
		t.Run(c.command, func(t *testing.T) {
			got := Guard{Runner: productionFake(c.current), Getenv: lane}.Refusal(context.Background(), payload(t, c.command))
			if (got != "") != c.refused {
				t.Fatalf("Refusal(%q) = %q, want refused = %v", c.command, got, c.refused)
			}
			if c.refused && got != productionRefusal {
				t.Fatalf("Refusal(%q) = %q, want the production read refusal", c.command, got)
			}
		})
	}
}

func TestOnlyHisRecordNamingTheReadPasses(t *testing.T) {
	cases := []struct {
		name, command, lane, kind, text string
		refused                         bool
	}{
		{"names the pod", podExec, "owner-relay", "owner", "Owner tapped: exec into api-runtime-0 for the trace stream", false},
		{"names the table", dbReadCall, "owner-relay", "owner", "Owner tapped: one db-read count on iris_sessions", false},
		{"names another pod", podExec, "owner-relay", "owner", "Owner tapped: exec into api-runtime-1 once", true},
		{"another lane", podExec, "meta-desk", "owner", "exec into api-runtime-0", true},
		{"not his word", podExec, "owner-relay", "note", "exec into api-runtime-0", true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			runner := withApproval(productionFake("plat-usw2-staging"), c.lane, c.kind, c.text)
			got := Guard{Runner: runner, Getenv: lane}.Refusal(context.Background(), payload(t, c.command+approvalNote)) != ""
			if got != c.refused {
				t.Fatalf("refused = %v, want %v", got, c.refused)
			}
		})
	}
}

func TestAFailedApprovalReadRefuses(t *testing.T) {
	runner := productionFake("plat-usw2-staging")
	runner.Fail(1, "bus down", "cci", "show")
	if (Guard{Runner: runner, Getenv: lane}).Refusal(context.Background(), payload(t, podExec+approvalNote)) == "" {
		t.Fatal("passed a production read whose approval did not read")
	}
}

func TestAFailedConfigReadPasses(t *testing.T) {
	runner := &shelltest.Fake{}
	runner.Fail(1, "unset", "git", "config")
	if (Guard{Runner: runner, Getenv: lane}).Refusal(context.Background(), payload(t, podExec)) != "" {
		t.Fatal("refused with no production configured")
	}
}

func TestAScriptTheCallRunsIsRead(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "stream.sh"), []byte(podExec+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, command := range []string{"bash " + filepath.Join(dir, "stream.sh"), filepath.Join(dir, "stream.sh")} {
		if (Guard{Runner: productionFake("plat-usw2-staging"), Getenv: lane}).Refusal(context.Background(), payload(t, command)) != productionRefusal {
			t.Fatalf("Refusal(%q) passed a script that execs into a production pod", command)
		}
	}
}

func TestReadsNothingForAnUnguardedCall(t *testing.T) {
	runner := productionFake("plat-usw2-prod")
	Guard{Runner: runner, Getenv: lane}.Refusal(context.Background(), payload(t, "git status && kubectl get pods"))
	if calls := runner.Calls(); len(calls) != 0 {
		t.Fatalf("read on a call that reads no production: %v", calls)
	}
}
