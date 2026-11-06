package landing

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
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

func spriteState(t *testing.T, host, marker string) string {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	t.Setenv("CLAUDE_CONFIG_DIR", t.TempDir())
	t.Setenv(lanes.LaneEnv, "lane-a")
	state := t.TempDir()
	files := map[string]string{
		filepath.Join(os.Getenv("CLAUDE_CONFIG_DIR"), "long-running", "drives", "d.json"): `{"drive":"d","cci_drive":"d","state_dir":"` + state + `","sessions":["s"],"host":"` + host + `"}`,
		filepath.Join(state, "lr.json"): `{"repo":"o/r","drive":"d","checkout":"/repo","author":"a","label":"l","movers":["lanes"]}`,
	}
	if marker != "" {
		files[filepath.Join(state, marker)] = ""
	}
	for path, text := range files {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return state
}

func serveStoreDesk(t *testing.T, read string, wantArgs, answer string) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	stopped := make(chan struct{})
	go func() {
		askdesk.Desk{Lane: askdesk.StoreDesk, Drive: "d", Bodies: t.TempDir(), Now: time.Now, Log: io.Discard, Read: askdesk.StoreReads(map[string]askdesk.Serve{read: func(_ context.Context, args []string, out, _ io.Writer) error {
			if got := strings.Join(args, " "); got != wantArgs {
				return fmt.Errorf("the desk passed %q", got)
			}
			_, err := io.WriteString(out, answer)
			return err
		}})}.Follow(ctx)
		close(stopped)
	}()
	t.Cleanup(func() {
		cancel()
		<-stopped
	})
}

func TestASpriteGateAsksTheMacsStoreDeskByItsHostOrItsMarkerAndRefusesWithItsLines(t *testing.T) {
	for name, sprite := range map[string]struct{ host, marker string }{
		"host":   {host: drive.HostSprite},
		"marker": {marker: storeFile + drive.OnMac},
	} {
		t.Run(name, func(t *testing.T) {
			state := spriteState(t, sprite.host, sprite.marker)
			refusals := "#5 TAP-REQUIRED no approval on the owner's board at this head\n#4 is no open PR of this drive\n"
			serveStoreDesk(t, gateRead, "5 --partial", refusals)

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
			if _, err := os.Stat(filepath.Join(state, storeFile)); !errors.Is(err, fs.ErrNotExist) {
				t.Fatalf("the gate left a store on the Sprite: %v", err)
			}
		})
	}
}

func TestASpriteGoAsksTheMacsStoreDeskAndPrintsWhatTheMacDid(t *testing.T) {
	state := spriteState(t, drive.HostSprite, "")
	did := "GO #5 at 3f3acff: posted #12@ab: exempt go at 3f3acff\nREFUSED #6 at 9a8b7c6: no grade at this head\n"
	serveStoreDesk(t, goRead, "5 6 --lane pr-unblock", did)

	var errs, out bytes.Buffer
	a := app.New(&errs)
	a.ConfigPath = filepath.Join(state, "lr.json")
	if err := runGo(context.Background(), a, []int{5, 6}, goFlags{lane: "pr-unblock"}, &out); err != nil {
		t.Fatalf("got %v and stderr %q", err, errs.String())
	}
	if out.String() != did {
		t.Fatalf("printed %q, want the Mac's %q", out.String(), did)
	}
	if _, err := os.Stat(filepath.Join(state, storeFile)); !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("the go left a store on the Sprite: %v", err)
	}
}
