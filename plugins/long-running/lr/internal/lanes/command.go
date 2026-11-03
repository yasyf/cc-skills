package lanes

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const StoreFile = "lr.sqlite"

func Loaded(a *app.App) (config.Config, string, error) {
	if a.ConfigPath == "" {
		return config.Config{}, "", errors.New("name the drive's lr.json with --config or LR_CONFIG")
	}
	path, err := filepath.Abs(a.ConfigPath)
	if err != nil {
		return config.Config{}, "", err
	}
	if resolved, err := filepath.EvalSymlinks(path); err == nil {
		path = resolved
	}
	cfg, err := config.Load(path, os.Stderr)
	if err != nil {
		return config.Config{}, "", err
	}
	return cfg, filepath.Dir(path), nil
}

func opened(a *app.App) (*store.Store, config.Config, error) {
	cfg, state, err := Loaded(a)
	if err != nil {
		return nil, cfg, err
	}
	s, err := store.Open(filepath.Join(state, StoreFile))
	return s, cfg, err
}

func Command(a *app.App) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "lanes [NAME...]",
		Short: "The drive's one liveness answer: one line per lane in the registry",
		Args:  cobra.ArbitraryArgs,
		RunE: func(cmd *cobra.Command, names []string) error {
			s, _, err := opened(a)
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			if !asJSON {
				lines, err := RegistryLines(s, names)
				if err != nil {
					return err
				}
				fmt.Fprintln(cmd.OutOrStdout(), strings.Join(lines, "\n"))
				return nil
			}
			rows, err := LivenessRows(s)
			if err != nil {
				return err
			}
			encoder := json.NewEncoder(cmd.OutOrStdout())
			encoder.SetEscapeHTML(false)
			for _, row := range rows {
				if len(names) > 0 && !slices.Contains(names, row.Lane) {
					continue
				}
				if err := encoder.Encode(row); err != nil {
					return err
				}
			}
			return nil
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "one JSON object per lane")
	cmd.AddCommand(handoffCommand(a))
	return cmd
}

func StopSpritesCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "stop-sprites",
		Short: "Stop the Sprite of each lane that ended, once, with cc-remote orca stop; a stop that fails posts one blocker to root",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			cfg, state, err := Loaded(a)
			if err != nil {
				return err
			}
			remote, err := CcRemoteOf(state)
			if err != nil || remote == nil || cfg.OrcaRun == "" {
				return err
			}
			s, err := store.Open(filepath.Join(state, StoreFile))
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			env, err := NewEnv(a.Runner, cfg, state, a.Now)
			if err != nil {
				return err
			}
			return (&SpriteStops{Env: env, Store: s, CcRemote: *remote, Log: a.Log}).Check(cmd.Context(), a.Now())
		},
	}
}

func DoneCheckCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "done-check LANE",
		Short: "Print why a lane may not post done while it owns an open PR; print nothing when it may",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg, state, err := Loaded(a)
			if err != nil {
				return err
			}
			s, err := store.Open(filepath.Join(state, StoreFile))
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			env, err := NewEnv(a.Runner, cfg, state, a.Now)
			if err != nil {
				return err
			}
			refusal, err := DoneRefusal(s, args[0], (&prReader{env: env, ctx: cmd.Context(), read: map[int]prRead{}}).states)
			if err != nil || refusal == "" {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), refusal)
			return nil
		},
	}
}

func RouteCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "route LANE",
		Short: "Where mail for a lane goes: the live end of its successor chain, or root",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			s, _, err := opened(a)
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			answer, err := AnswerRoute(s, args[0])
			if err != nil {
				return err
			}
			return json.NewEncoder(cmd.OutOrStdout()).Encode(answer)
		},
	}
}

func RequestCommand(a *app.App) *cobra.Command {
	request := Request{By: Root}
	cmd := &cobra.Command{
		Use:   fmt.Sprintf("request {%s} LANE", strings.Join(Verbs, "|")),
		Short: "Post the one typed record that asks the supervisor to change a registry row",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			request.Verb, request.Lane = args[0], args[1]
			if !slices.Contains(Verbs, request.Verb) {
				return fmt.Errorf("unknown request %q, want one of %s", request.Verb, strings.Join(Verbs, ", "))
			}
			s, cfg, err := opened(a)
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			refusal, err := StandingRefusal(s, request)
			if err != nil {
				return err
			}
			if refusal != "" {
				return errors.New(refusal)
			}
			env, err := NewEnv(a.Runner, cfg, "", a.Now)
			if err != nil {
				return err
			}
			posted, err := env.Bus.Post(cmd.Context(), request.Draft(cfg.Drive))
			if err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), posted.Name())
			return nil
		},
	}
	cmd.Flags().StringVar(&request.Successor, "successor", "", "the lane that takes over")
	cmd.Flags().StringVar(&request.LastRead, "last-read", "", "the lane's newest record you have read")
	cmd.Flags().IntSliceVar(&request.PRs, "pr", nil, "a PR the request moves; repeatable")
	cmd.Flags().StringVar(&request.By, "by", Root, "the lane asking")
	return cmd
}
