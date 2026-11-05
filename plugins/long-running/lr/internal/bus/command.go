package bus

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

var digits = regexp.MustCompile(`^\d+$`)

func Command(*app.App) *cobra.Command {
	return &cobra.Command{
		Use:                "post <kind> [--drive D] [--as LANE] --<field> <value>... [--to <lane>]...",
		Short:              "Write one typed record, or print the corrected command when the bus refuses it",
		DisableFlagParsing: true,
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) == 0 || slices.Contains(args, "--help") || slices.Contains(args, "-h") {
				return cmd.Help()
			}
			post, err := parsePost(args)
			if err != nil {
				return err
			}
			if post.kind == "verdict" {
				return &Refused{"a verdict cannot name the grader it answers, so it clears nothing; root posts a decide record reading finder-override #<pr> at <sha>: <cite>, or fidelity-override"}
			}
			record, err := Bus{Drive: post.drive, Lane: post.lane}.PostFields(cmd.Context(), post.kind, post.fields, post.to)
			if err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "#%d %s\n", record.Seq, record.Kind())
			return err
		},
	}
}

type postArgs struct {
	kind, drive, lane string
	fields            map[string]any
	to                []string
}

func parsePost(args []string) (postArgs, error) {
	post := postArgs{kind: args[0], drive: os.Getenv("LR_DRIVE"), lane: os.Getenv("LR_LANE"), fields: map[string]any{}}
	words := args[1:]
	for len(words) > 0 {
		key, inline, hasInline := strings.Cut(strings.TrimPrefix(words[0], "--"), "=")
		if !strings.HasPrefix(words[0], "--") {
			return postArgs{}, fmt.Errorf("lr post: expected --<field> before '%s'", words[0])
		}
		value := inline
		words = words[1:]
		if !hasInline {
			if len(words) == 0 {
				return postArgs{}, fmt.Errorf("lr post: --%s needs a value", key)
			}
			value, words = words[0], words[1:]
		}
		switch key {
		case "drive":
			post.drive = value
		case "as":
			post.lane = value
		case "to":
			post.to = append(post.to, value)
		default:
			post.set(key, value)
		}
	}
	return post, nil
}

func (p postArgs) set(key, value string) {
	var typed any = value
	if digits.MatchString(value) {
		typed = json.Number(value)
	}
	switch seen := p.fields[key].(type) {
	case nil:
		p.fields[key] = typed
	case []any:
		p.fields[key] = append(seen, typed)
	default:
		p.fields[key] = []any{seen, typed}
	}
}
