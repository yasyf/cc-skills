package ghspend

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type sprites struct {
	ledgers map[string]string
}

func (s sprites) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	body, ok := s.ledgers[c.Argv[3]]
	if !ok {
		return shell.Result{Code: 1}, &shell.ExitError{Argv: c.Argv, Code: 1}
	}
	return shell.Result{Stdout: body}, nil
}

func ledgerOf(t *testing.T, pool string, callers map[string]int) string {
	t.Helper()
	raw, err := json.Marshal(map[string]map[string]map[string]int{pool: {"callers": callers}})
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func local(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "ledger.json")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

const appPool = "app#140742353:core"

var desk = []Host{{Name: "dar-desk-1", Path: "/tmp/ledger.json"}}

func TestABurstOnAnotherHostIsNamedAheadOfThisMac(t *testing.T) {
	t.Parallel()
	r := Reader{State: t.TempDir(), Runner: sprites{ledgers: map[string]string{"dar-desk-1": ledgerOf(t, appPool, map[string]int{"lr-facts": 100})}}}
	here := local(t, ledgerOf(t, appPool, map[string]int{"lr": 10}))
	if _, err := r.Read(t.Context(), here, desk); err != nil {
		t.Fatal(err)
	}

	r.Runner = sprites{ledgers: map[string]string{"dar-desk-1": ledgerOf(t, appPool, map[string]int{"lr-facts": 1100})}}
	here = local(t, ledgerOf(t, appPool, map[string]int{"lr": 16}))
	report, err := r.Read(t.Context(), here, desk)
	if err != nil {
		t.Fatal(err)
	}
	if len(report.Top) != 2 || report.Top[0] != (Spend{Host: "dar-desk-1", Pool: appPool, Caller: "lr-facts", Calls: 1000}) || report.Top[1].Host != thisHost || report.Top[1].Calls != 6 {
		t.Errorf("top = %+v, want the Sprite's 1,000 first and this Mac's 6 second", report.Top)
	}
	if report.Charged["app:core"] != 1116 {
		t.Errorf("charged = %v, want both hosts' app core calls summed", report.Charged)
	}
}

func TestAPoolThatResetCountsFromZeroNotNegative(t *testing.T) {
	t.Parallel()
	r := Reader{State: t.TempDir(), Runner: sprites{}}
	if _, err := r.Read(t.Context(), local(t, ledgerOf(t, appPool, map[string]int{"lr": 900})), nil); err != nil {
		t.Fatal(err)
	}

	report, err := r.Read(t.Context(), local(t, ledgerOf(t, appPool, map[string]int{"lr": 4})), nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(report.Top) != 1 || report.Top[0].Calls != 4 {
		t.Errorf("top = %+v, want the 4 calls since the reset", report.Top)
	}
}

func TestAnUnreadHostIsNamedAndKeepsItsLastChargeSoTheRateDoesNotDrop(t *testing.T) {
	t.Parallel()
	r := Reader{State: t.TempDir(), Runner: sprites{ledgers: map[string]string{"dar-desk-1": ledgerOf(t, appPool, map[string]int{"lr-facts": 500})}}}
	here := local(t, ledgerOf(t, appPool, map[string]int{"lr": 1}))
	if _, err := r.Read(t.Context(), here, desk); err != nil {
		t.Fatal(err)
	}

	r.Runner = sprites{}
	report, err := r.Read(t.Context(), here, desk)
	if err != nil {
		t.Fatal(err)
	}
	if len(report.Unread) != 1 || report.Unread[0] != "dar-desk-1" {
		t.Errorf("unread = %v, want the Sprite named", report.Unread)
	}
	if report.Charged["app:core"] != 501 || len(report.Top) != 0 {
		t.Errorf("charged = %v, top = %+v; want the last read carried and nothing new", report.Charged, report.Top)
	}
}

func TestAHostFlagWithoutAPathIsRefused(t *testing.T) {
	t.Parallel()
	if _, err := parseHosts([]string{"dar-desk-1"}); err == nil {
		t.Error("parseHosts accepted a host with no ledger path")
	}
}
