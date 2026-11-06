package board

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

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	lrfacts "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

func TestTheHooksPortAndOwnerReadsOnASpriteAskTheMacsStoreDeskAndPrintItsAnswer(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	t.Setenv("CLAUDE_CONFIG_DIR", t.TempDir())
	t.Setenv(lanes.LaneEnv, "old-tree-port-desk")
	state := t.TempDir()
	entry := drive.Entry{Drive: "d", CCIDrive: "d", StateDir: state, Sessions: []string{"s"}, Host: drive.HostSprite}
	registry := filepath.Join(os.Getenv("CLAUDE_CONFIG_DIR"), "long-running", "drives")
	if err := os.MkdirAll(registry, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(registry, "d.json"), []byte(`{"drive":"d","cci_drive":"d","state_dir":"`+state+`","sessions":["s"],"host":"`+drive.HostSprite+`"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	answers := map[string]string{
		portsRead:     "{\"50254\":49412}\n",
		"facts whose": "{\"number\":46018,\"author\":\"andrew\",\"foreign\":true}\n",
	}
	asked := map[string]string{portsRead: "50254 50255", "facts whose": "46018"}
	reads := map[string]askdesk.Serve{}
	for read, answer := range answers {
		reads[read] = func(_ context.Context, args []string, out, _ io.Writer) error {
			if got := strings.Join(args, " "); got != asked[read] {
				return fmt.Errorf("the desk passed %q to %s", got, read)
			}
			_, err := io.WriteString(out, answer)
			return err
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	stopped := make(chan struct{})
	go func() {
		askdesk.Desk{Lane: askdesk.StoreDesk, Drive: "d", Bodies: t.TempDir(), Now: time.Now, Log: io.Discard, Read: askdesk.StoreReads(reads)}.Follow(ctx)
		close(stopped)
	}()
	t.Cleanup(func() {
		cancel()
		<-stopped
	})

	for read, cmd := range map[string]*cobra.Command{
		portsRead:     portsCommand(func() (drive.Entry, string, error) { return entry, "", nil }),
		"facts whose": lrfacts.Command(app.New(io.Discard)),
	} {
		args := strings.Fields(asked[read])
		if read == "facts whose" {
			args = append([]string{"whose"}, append(args, "--drive", "d")...)
		}
		var out bytes.Buffer
		cmd.SetArgs(args)
		cmd.SetOut(&out)
		if err := cmd.ExecuteContext(context.Background()); err != nil {
			t.Fatalf("%s: %v", read, err)
		}
		if out.String() != answers[read] {
			t.Fatalf("%s printed %q, want the Mac's %q", read, out.String(), answers[read])
		}
	}
	if _, err := os.Stat(entry.Store()); !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("a read left a store on the Sprite: %v", err)
	}
}
