package lanes

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const StoreFile = "lr.sqlite"

func Loaded(a *app.App) (config.Config, string, error) {
	path, err := a.ConfigFile()
	if err != nil {
		return config.Config{}, "", err
	}
	path, err = filepath.Abs(path)
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

func sessionScope(a *app.App) (config.Config, string, error) {
	named := a.ConfigPath != ""
	cfg, state, err := Loaded(a)
	if named || !errors.Is(err, fs.ErrNotExist) {
		return cfg, state, err
	}
	entry, err := drive.Session()
	return config.Config{Drive: entry.Drive, Repo: entry.Repo}, "", err
}

func requestScope(a *app.App) (*store.Store, config.Config, error) {
	cfg, state, err := sessionScope(a)
	if err != nil {
		return nil, cfg, err
	}
	if state == "" {
		s, err := store.Memory()
		return s, cfg, err
	}
	s, err := store.Open(filepath.Join(state, StoreFile))
	return s, cfg, err
}

func Command(a *app.App) *cobra.Command {
	var asJSON, idle bool
	cmd := &cobra.Command{
		Use:   "lanes [NAME...]",
		Short: "The drive's one liveness answer: one line per lane in the registry",
		Args:  cobra.ArbitraryArgs,
		RunE: func(cmd *cobra.Command, names []string) error {
			if idle {
				return idleCensus(cmd, a)
			}
			s, cfg, err := opened(a)
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			for i := range names {
				if names[i], err = Resolve(cmd.Context(), cfg.Drive, names[i]); err != nil {
					return err
				}
			}
			if !asJSON {
				lines, err := RegistryLines(s, names, a.Now())
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
	cmd.Flags().BoolVar(&idle, "idle", false, "the idle-session census: claude and codex processes, Orca worktrees and terminals, and what a whole-Run gc would close, remove, destroy and keep; changes nothing")
	cmd.AddCommand(handoffCommand(a), handoffRegistryCommand(a), adoptMetaCommand(a), sameCommand())
	return cmd
}

func sameCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "same LANE NAME...",
		Short: "Print each NAME that means LANE: the lane itself, the alias it answers under, or a numbered lane answering under it",
		Args:  cobra.MinimumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			lane := args[0]
			for _, name := range args[1:] {
				if name == lane || client.Stem(name) == lane || client.Stem(lane) == name {
					fmt.Fprintln(cmd.OutOrStdout(), name)
				}
			}
			return nil
		},
	}
}

func adoptMetaCommand(a *app.App) *cobra.Command {
	var from string
	cmd := &cobra.Command{
		Use:   "adopt-meta DESK --from FILE",
		Short: "Make DESK the drive's meta lane and record it as the launcher of the lanes FILE names, for a desk that did not start through lr launch meta",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			listed, err := os.ReadFile(from)
			if err != nil {
				return err
			}
			s, cfg, err := opened(a)
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			desk, err := Resolve(cmd.Context(), cfg.Drive, args[0])
			if err != nil {
				return err
			}
			adopted, unknown, err := AdoptMeta(s, desk, strings.Fields(string(listed)))
			if err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "%s is the meta lane; %d lanes adopted; %d have no row: %s\n", desk, adopted, len(unknown), strings.Join(unknown, " "))
			return err
		},
	}
	cmd.Flags().StringVar(&from, "from", "", "a file of lane names the desk launched")
	if err := cmd.MarkFlagRequired("from"); err != nil {
		panic(err)
	}
	return cmd
}

func StopSpritesCommand(a *app.App) *cobra.Command {
	var leave string
	cmd := &cobra.Command{
		Use:   "stop-sprites",
		Short: "Stop the Sprite of each lane that ended with cc-remote orca stop; a 5xx or a timeout is tried every pass until it stops, is gone or --leave names it, under one blocker and one closing line",
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
			stops := &SpriteStops{Env: env, Store: s, CcRemote: *remote, Log: a.Log}
			if leave != "" {
				return stops.Leave(cmd.Context(), leave, a.Now())
			}
			return stops.Check(cmd.Context(), a.Now())
		},
	}
	cmd.Flags().StringVar(&leave, "leave", "", "stop retrying the failed stop of this lane's Sprite, as a person's decision to leave it")
	return cmd
}

func DoneCheckCommand(a *app.App) *cobra.Command {
	var text string
	var attrs []string
	cmd := &cobra.Command{
		Use:   "done-check LANE",
		Short: "Print why a lane may not post done: it owns an open PR, or it runs on a Sprite and the done's text or attributes name a file on that Sprite's disk; print nothing when it may",
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
			lane, err := Resolve(cmd.Context(), cfg.Drive, args[0])
			if err != nil {
				return err
			}
			home, err := os.UserHomeDir()
			if err != nil {
				return err
			}
			refusal, err := DoneRefusal(s, lane, append([]string{text}, attrs...), home, (&prReader{env: env, ctx: cmd.Context(), read: map[int]prRead{}}).states)
			if err != nil || refusal == "" {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), refusal)
			return nil
		},
	}
	cmd.Flags().StringVar(&text, "text", "", "the done record's text")
	cmd.Flags().StringArrayVar(&attrs, "attr", nil, "each key=value attribute the done record carries")
	return cmd
}

func RouteCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "route LANE",
		Short: "Where mail for a lane goes: the live end of its successor chain, or the coordinator",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			s, cfg, err := opened(a)
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			lane, err := resolveMember(cmd.Context(), s, cfg.Drive, args[0], true)
			if err != nil {
				return err
			}
			answer, err := AnswerRoute(s, lane)
			if err != nil {
				return err
			}
			answer.Lane = Canonical(cfg.Drive, args[0])
			return json.NewEncoder(cmd.OutOrStdout()).Encode(answer)
		},
	}
}

func RequestCommand(a *app.App) *cobra.Command {
	var request Request
	cmd := &cobra.Command{
		Use:   fmt.Sprintf("request {%s} LANE", strings.Join(Verbs, "|")),
		Short: "Post the one typed record that asks the supervisor to change a registry row",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			request.Verb, request.Lane = args[0], args[1]
			if !slices.Contains(Verbs, request.Verb) {
				return fmt.Errorf("unknown request %q, want one of %s", request.Verb, strings.Join(Verbs, ", "))
			}
			if request.Verb == "step" && request.Step == "" {
				return errors.New("a step request names the plan step the lane owns: lr request step LANE --step <n>")
			}
			if request.Verb == "release" && len(request.PRs) == 0 {
				return errors.New("a release request names each PR it drops from the lane's row: lr request release LANE --pr <n>")
			}
			s, cfg, err := requestScope(a)
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			if request.By == "" {
				if request.By, err = BoundLane(cmd.Context(), a.Runner); err != nil {
					return err
				}
			}
			if request.By == "" {
				return errors.New("no lane is bound to this session, so the request would post under no name; pass --by <your lane>")
			}
			if request.Lane, err = resolveMember(cmd.Context(), s, cfg.Drive, request.Lane, false); err != nil {
				return err
			}
			request.Successor, request.By = Canonical(cfg.Drive, request.Successor), Canonical(cfg.Drive, request.By)
			refusal, err := StandingRefusal(s, request)
			if err == nil && refusal == "" {
				refusal, err = UnreadRefusal(cmd.Context(), s, cfg.Drive, request)
			}
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
	cmd.Flags().StringVar(&request.Open, "open", "", "a desk's open items its stand-down hands on, or none")
	cmd.Flags().StringVar(&request.Step, "step", "", "the plan step the lane owns from now on, the field lr launch lane --step sets at launch")
	cmd.Flags().IntSliceVar(&request.PRs, "pr", nil, "a PR the request moves or drops; repeatable")
	cmd.Flags().StringVar(&request.By, "by", "", "the lane asking; defaults to the lane bound to this session")
	return cmd
}
