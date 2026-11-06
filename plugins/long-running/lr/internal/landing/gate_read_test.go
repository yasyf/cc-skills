package landing

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
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

func TestASpriteGateAsksTheMacsStoreDeskAndRefusesWithItsLines(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	t.Setenv("CLAUDE_CONFIG_DIR", t.TempDir())
	t.Setenv(lanes.LaneEnv, "lane-a")
	state := t.TempDir()
	for path, text := range map[string]string{
		filepath.Join(os.Getenv("CLAUDE_CONFIG_DIR"), "long-running", "drives", "d.json"): `{"drive":"d","cci_drive":"d","state_dir":"` + state + `","sessions":["s"],"host":"` + drive.HostSprite + `"}`,
		filepath.Join(state, "lr.json"):             `{"repo":"o/r","drive":"d","checkout":"/repo","author":"a","label":"l","movers":["lanes"]}`,
		filepath.Join(state, storeFile+drive.OnMac): "",
	} {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	refusals := "#5 TAP-REQUIRED no approval on the owner's board at this head\n#4 is no open PR of this drive\n"
	ctx, cancel := context.WithCancel(context.Background())
	stopped := make(chan struct{})
	go func() {
		askdesk.Desk{Lane: askdesk.StoreDesk, Drive: "d", Bodies: t.TempDir(), Now: time.Now, Log: io.Discard, Read: askdesk.StoreReads(map[string]askdesk.Serve{gateRead: func(_ context.Context, args []string, out, _ io.Writer) error {
			if got := strings.Join(args, " "); got != "5 --partial" {
				return fmt.Errorf("the desk passed %q", got)
			}
			_, err := io.WriteString(out, refusals)
			return err
		}})}.Follow(ctx)
		close(stopped)
	}()
	t.Cleanup(func() {
		cancel()
		<-stopped
	})

	var errs, out bytes.Buffer
	a := app.New(&errs)
	a.ConfigPath = filepath.Join(state, "lr.json")
	err := runGate(context.Background(), a, []string{"5"}, true, false, &out)
	var exit *wait.ExitError
	if !errors.As(err, &exit) || exit.Code != 1 {
		t.Fatalf("got %v and stderr %q, want exit 1", err, errs.String())
	}
	if out.String() != refusals {
		t.Fatalf("printed %q, want the Mac's %q", out.String(), refusals)
	}
}
