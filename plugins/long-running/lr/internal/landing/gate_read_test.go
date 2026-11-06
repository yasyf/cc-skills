package landing

import (
	"bytes"
	"context"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func TestAGateWhoseLrJSONIsMissingExitsNonZeroNamingTheFile(t *testing.T) {
	var errs, out bytes.Buffer
	a := app.New(&errs)
	a.ConfigPath = filepath.Join(t.TempDir(), "lr.json")
	err := runGate(context.Background(), a, []string{"5"}, false, false, &out)
	if err == nil || !strings.Contains(err.Error(), a.ConfigPath) {
		t.Fatalf("got %v, want an error naming %s", err, a.ConfigPath)
	}
	if out.Len() != 0 || strings.Contains(errs.String(), gateUnread) {
		t.Fatalf("printed %q and warned %q", out.String(), errs.String())
	}
}
