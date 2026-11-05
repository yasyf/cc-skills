package reply

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"slices"
	"strings"
	"testing"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
	"github.com/yasyf/cc-interact/channel"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

func verbTree(ran *[]string) func() *cobra.Command {
	return func() *cobra.Command {
		root := &cobra.Command{Use: "lr", SilenceUsage: true, SilenceErrors: true}
		root.PersistentFlags().String("config", "", "")
		run := func(cmd *cobra.Command, args []string) error {
			stdin, err := io.ReadAll(cmd.InOrStdin())
			if err != nil {
				return err
			}
			var flags []string
			cmd.Flags().Visit(func(f *pflag.Flag) { flags = append(flags, f.Name+"="+f.Value.String()) })
			*ran = append(*ran, fmt.Sprintf("%s %q %v stdin=%q", cmd.CommandPath(), args, flags, stdin))
			if slices.Contains(args, "refused") {
				return fmt.Errorf("mark %s refused", args[0])
			}
			fmt.Fprintln(cmd.OutOrStdout(), "ok")
			return nil
		}
		owner := &cobra.Command{Use: "owner"}
		mark := &cobra.Command{Use: "mark", Short: "Mark one ledger row.", Args: cobra.ExactArgs(2), RunE: run}
		mark.Flags().Int("routed", 0, "the routing seq")
		mark.Flags().String("by", "", "the clock")
		add := &cobra.Command{Use: "add", Short: "Add the owner message", Args: cobra.NoArgs, RunE: run}
		add.Flags().String("state", "open", "open or addressed")
		rule := &cobra.Command{Use: "rule", Short: "Record an owner ruling", Args: cobra.ExactArgs(1), RunE: run}
		rule.Flags().String("reply", "", "the reply")
		owner.AddCommand(mark, add, rule)
		explain := &cobra.Command{Use: "explain", Short: "Host a page", Args: cobra.ExactArgs(1), RunE: run}
		explain.Flags().String("from", "", "a markdown file")
		_ = explain.MarkFlagRequired("from")
		ask := &cobra.Command{Use: "ask-lane", Short: "Launch a search lane", Args: cobra.ExactArgs(1), RunE: run}
		ask.Flags().Bool("mac", false, "on the Mac")
		root.AddCommand(owner, explain, ask, &cobra.Command{Use: "asks", Short: "Every open ask", Args: cobra.NoArgs, RunE: run})
		return root
	}
}

func call(t *testing.T, tools []channel.Tool, name string, args map[string]any) (string, bool) {
	t.Helper()
	i := slices.IndexFunc(tools, func(tool channel.Tool) bool { return tool.Name == name })
	if i < 0 {
		t.Fatalf("no tool %s", name)
	}
	raw, err := json.Marshal(args)
	if err != nil {
		t.Fatal(err)
	}
	return tools[i].Handler(context.Background(), raw, nil)
}

func TestEachVerbToolRunsItsCLIVerbWithTheSameArgsFlagsAndStdin(t *testing.T) {
	var ran []string
	tools := VerbTools(verbTree(&ran))
	if text, failed := call(t, tools, "owner_mark", map[string]any{"row": "m47", "state": "due", "by": "6:40 PM", "routed": 398779}); failed || text != "ok\n" {
		t.Fatalf("owner_mark = %q, %v", text, failed)
	}
	if _, failed := call(t, tools, "owner_add", map[string]any{"text": "why not the mcp?", "state": "addressed"}); failed {
		t.Fatal("owner_add failed")
	}
	if _, failed := call(t, tools, "ask_lane", map[string]any{"question": "-which PR moved it?", "mac": true}); failed {
		t.Fatal("ask_lane failed")
	}
	want := []string{
		`lr owner mark ["m47" "due"] [by=6:40 PM routed=398779] stdin=""`,
		`lr owner add [] [state=addressed] stdin="why not the mcp?"`,
		`lr ask-lane ["-which PR moved it?"] [mac=true] stdin=""`,
	}
	if !slices.Equal(ran, want) {
		t.Fatalf("ran\n%s\nwant\n%s", strings.Join(ran, "\n"), strings.Join(want, "\n"))
	}
}

func TestAVerbToolCarriesTheVerbsRefusalAndItsRequiredInputs(t *testing.T) {
	var ran []string
	tools := VerbTools(verbTree(&ran))
	text, failed := call(t, tools, "owner_mark", map[string]any{"row": "m3", "state": "refused"})
	if !failed || text != "lr owner mark: mark m3 refused" {
		t.Fatalf("owner_mark = %q, %v", text, failed)
	}
	if text, failed := call(t, tools, "explain", map[string]any{"topic": "relay"}); !failed || !strings.Contains(text, `"from" not set`) {
		t.Fatalf("explain with no from = %q, %v", text, failed)
	}
	i := slices.IndexFunc(tools, func(tool channel.Tool) bool { return tool.Name == "explain" })
	if required := tools[i].InputSchema["required"]; !slices.Equal(required.([]string), []string{"topic", "from"}) {
		t.Fatalf("explain requires %v", required)
	}
}

func listed(t *testing.T) []string {
	t.Helper()
	var out bytes.Buffer
	server := Command(&app.App{}, verbTree(nil))
	server.SetArgs([]string{"serve"})
	server.SetIn(strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}` + "\n"))
	server.SetOut(&out)
	if err := server.ExecuteContext(context.Background()); err != nil {
		t.Fatal(err)
	}
	var response struct {
		Result struct {
			Tools []struct {
				Name string `json:"name"`
			} `json:"tools"`
		} `json:"result"`
	}
	if err := json.Unmarshal(out.Bytes(), &response); err != nil {
		t.Fatalf("%v: %s", err, out.String())
	}
	names := make([]string, 0, len(response.Result.Tools))
	for _, tool := range response.Result.Tools {
		names = append(names, tool.Name)
	}
	return names
}

func TestASessionInNoDriveYetListsEveryToolInItsFirstListing(t *testing.T) {
	t.Setenv("CLAUDE_CONFIG_DIR", t.TempDir())
	t.Setenv(drive.Env, "")
	want := []string{"reply", "owner_add", "owner_mark", "owner_rule", "explain", "ask_lane", "asks"}
	if names := listed(t); !slices.Equal(names, want) {
		t.Fatalf("a session in no drive lists %v, want %v", names, want)
	}
}
