package main

import (
	"errors"
	"fmt"
	"os"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const macUnread = 3

var (
	appVersion = "dev"
	verbs      []func(*app.App) *cobra.Command
)

func register(verb func(*app.App) *cobra.Command) {
	verbs = append(verbs, verb)
}

func root(a *app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:           "lr",
		Short:         "The long-running drive's one command line",
		Version:       appVersion,
		SilenceUsage:  true,
		SilenceErrors: true,
		Args:          cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return cmd.Help()
		},
	}
	cmd.PersistentFlags().StringVar(&a.ConfigPath, "config", os.Getenv("LR_CONFIG"), "the drive's lr.json; default LR_CONFIG, for a Sprite desk with no drive registry, then the lr.json of the drive this session belongs to")
	for _, verb := range verbs {
		cmd.AddCommand(verb(a))
	}
	runOnMac(cmd)
	return cmd
}

func runOnMac(cmd *cobra.Command) {
	for _, sub := range cmd.Commands() {
		runOnMac(sub)
	}
	verb := strings.TrimPrefix(cmd.CommandPath(), cmd.Root().Name()+" ")
	local := cmd.RunE
	if local == nil || !slices.Contains(askdesk.StoreVerbs, verb) {
		return
	}
	cmd.RunE = func(c *cobra.Command, args []string) error {
		entry, onSprite := drive.SpriteEntry()
		if !onSprite {
			return local(c, args)
		}
		err := askdesk.RunOnMac(c.Context(), askdesk.LiveCCI{}, entry.Bus(), askdesk.Asked{Verb: verb, Args: forwarded(c, args), Lane: os.Getenv(lanes.LaneEnv)}, c.OutOrStdout(), c.ErrOrStderr())
		var exit *askdesk.ExitError
		if errors.As(err, &exit) {
			return &wait.ExitError{Code: exit.Code}
		}
		if err != nil {
			fmt.Fprintln(c.ErrOrStderr(), "lr:", err)
			return &wait.ExitError{Code: macUnread}
		}
		return nil
	}
}

func forwarded(c *cobra.Command, args []string) []string {
	var words []string
	c.Flags().Visit(func(f *pflag.Flag) {
		if f.Name == "config" {
			return
		}
		if values, ok := f.Value.(pflag.SliceValue); ok {
			for _, value := range values.GetSlice() {
				words = append(words, "--"+f.Name+"="+value)
			}
			return
		}
		words = append(words, "--"+f.Name+"="+f.Value.String())
	})
	return append(append(words, "--"), args...)
}

func execute(cmd *cobra.Command, args []string) error {
	verb, rest, err := cmd.Find(args)
	if err == nil {
		ccx.Caller = strings.ReplaceAll(verb.CommandPath(), " ", "-")
	}
	if err == nil && verb.HasSubCommands() && !verb.Runnable() && len(rest) > 0 && !strings.HasPrefix(rest[0], "-") {
		return fmt.Errorf("%s has no verb %s; see %s --help", verb.CommandPath(), rest[0], verb.CommandPath())
	}
	cmd.SetArgs(args)
	return cmd.Execute()
}

func main() {
	if err := execute(root(app.New(os.Stderr)), os.Args[1:]); err != nil {
		var exit *wait.ExitError
		if errors.As(err, &exit) {
			os.Exit(exit.Code)
		}
		fmt.Fprintln(os.Stderr, "lr:", err)
		os.Exit(1)
	}
}
