package reply

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
	"github.com/yasyf/cc-interact/channel"
)

type arg struct {
	name, description string
}

type verb struct {
	tool  string
	path  []string
	args  []arg
	stdin *arg
}

var verbs = []verb{
	{tool: "owner_add", path: []string{"owner", "add"}, stdin: &arg{"text", "The owner's message, verbatim."}},
	{tool: "owner_mark", path: []string{"owner", "mark"}, args: []arg{{"row", "The ledger row, such as m47 or m47.2."}, {"state", "responded, addressed, no-order or due."}}},
	{tool: "owner_rule", path: []string{"owner", "rule"}, args: []arg{{"row", "The ledger row the ruling came in on, such as m47."}}},
	{tool: "explain", path: []string{"explain"}, args: []arg{{"topic", "The page's topic, a few words."}}},
	{tool: "ask_lane", path: []string{"ask-lane"}, args: []arg{{"question", "The question the search lane answers."}}},
	{tool: "asks", path: []string{"asks"}},
}

func VerbTools(commands func() *cobra.Command) []channel.Tool {
	tools := make([]channel.Tool, len(verbs))
	for i, v := range verbs {
		tools[i] = v.channelTool(commands)
	}
	return tools
}

func (v verb) channelTool(commands func() *cobra.Command) channel.Tool {
	cmd, _, err := commands().Find(v.path)
	if err != nil {
		panic(err)
	}
	properties := map[string]any{}
	var required []string
	for _, a := range v.inputs() {
		properties[a.name] = map[string]any{"type": "string", "description": a.description}
		required = append(required, a.name)
	}
	cmd.LocalNonPersistentFlags().VisitAll(func(f *pflag.Flag) {
		if f.Name == "help" {
			return
		}
		properties[f.Name] = property(f)
		if _, ok := f.Annotations[cobra.BashCompOneRequiredFlag]; ok {
			required = append(required, f.Name)
		}
	})
	schema := map[string]any{"type": "object", "properties": properties}
	if len(required) > 0 {
		schema["required"] = required
	}
	return channel.Tool{
		Name:        v.tool,
		Description: fmt.Sprintf("%s. The same code as `lr %s`, on the drive this session belongs to.", strings.TrimSuffix(cmd.Short, "."), strings.Join(v.path, " ")),
		InputSchema: schema,
		Handler: func(ctx context.Context, args json.RawMessage, _ func(string)) (string, bool) {
			argv, stdin, err := v.argv(args)
			if err != nil {
				return v.tool + ": " + err.Error(), true
			}
			root := commands()
			var out bytes.Buffer
			root.SetArgs(argv)
			root.SetIn(strings.NewReader(stdin))
			root.SetOut(&out)
			root.SetErr(&out)
			if err := root.ExecuteContext(ctx); err != nil {
				return strings.TrimSpace(out.String() + "\nlr " + strings.Join(v.path, " ") + ": " + err.Error()), true
			}
			return out.String(), false
		},
	}
}

func (v verb) inputs() []arg {
	if v.stdin == nil {
		return v.args
	}
	return append(slices.Clone(v.args), *v.stdin)
}

func property(f *pflag.Flag) map[string]any {
	switch f.Value.Type() {
	case "bool":
		return map[string]any{"type": "boolean", "description": f.Usage}
	case "int":
		return map[string]any{"type": "integer", "description": f.Usage}
	case "stringArray", "stringSlice":
		return map[string]any{"type": "array", "items": map[string]any{"type": "string"}, "description": f.Usage}
	}
	return map[string]any{"type": "string", "description": f.Usage}
}

func (v verb) argv(args json.RawMessage) ([]string, string, error) {
	decoder := json.NewDecoder(bytes.NewReader(args))
	decoder.UseNumber()
	in := map[string]any{}
	if err := decoder.Decode(&in); err != nil {
		return nil, "", err
	}
	var positional []string
	for _, a := range v.args {
		if value, ok := in[a.name].(string); ok {
			positional = append(positional, value)
		}
		delete(in, a.name)
	}
	stdin := ""
	if v.stdin != nil {
		stdin, _ = in[v.stdin.name].(string)
		delete(in, v.stdin.name)
	}
	argv := slices.Clone(v.path)
	for _, name := range slices.Sorted(maps.Keys(in)) {
		if items, ok := in[name].([]any); ok {
			for _, item := range items {
				argv = append(argv, fmt.Sprintf("--%s=%v", name, item))
			}
			continue
		}
		argv = append(argv, fmt.Sprintf("--%s=%v", name, in[name]))
	}
	return append(append(argv, "--"), positional...), stdin, nil
}
