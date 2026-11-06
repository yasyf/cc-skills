package rules

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/quotes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

func Command(a *app.App) *cobra.Command {
	var driveName string
	rules := &cobra.Command{Use: "rules", Short: "Owner rules as checks over a diff's hunks, and the picks that settle findings"}
	rules.PersistentFlags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	opened := func() (*store.Store, error) {
		entry, err := drive.Named(driveName)
		if err != nil {
			return nil, err
		}
		return store.Open(entry.Store())
	}
	rules.AddCommand(&cobra.Command{
		Use:   "check RULE PREDICATE[:ARGUMENT]",
		Short: "Grade the unified diff on stdin against one rule; exit 1 when it is not met",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			diff, err := io.ReadAll(cmd.InOrStdin())
			if err != nil {
				return err
			}
			check, err := Evaluate(args[0], args[1], Parse(string(diff)))
			if err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "%s: %s\n", check.Rule, check.How)
			if !check.Met {
				return fmt.Errorf("rule %s not met", check.Rule)
			}
			return nil
		},
	}, pickCommand(a, opened), answersCommand(a), &cobra.Command{
		Use:   "row KEY",
		Short: "Print an owner row whole, with every repeat and implication, or an answer as `answer <id>`; read this before quoting him",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			whole, err := quotes.NewReader(a.Runner, driveName).Whole(cmd.Context(), args[0])
			if err != nil {
				return err
			}
			fmt.Fprint(cmd.OutOrStdout(), whole)
			return nil
		},
	}, &cobra.Command{
		Use:   "quotes",
		Short: "Read an override on stdin, print each quote its cited row or answer does not hold whole, and exit 1 when any; the supervisor warns root the same way",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			text, err := io.ReadAll(cmd.InOrStdin())
			if err != nil {
				return err
			}
			warnings := quotes.NewReader(a.Runner, driveName).Check(cmd.Context(), string(text))
			for _, warning := range warnings {
				fmt.Fprintf(cmd.OutOrStdout(), "warned: %s\n", warning)
			}
			if len(warnings) > 0 {
				return fmt.Errorf("%d quotes to check against the owner's words", len(warnings))
			}
			return nil
		},
	}, settleCommand(opened), checkPRCommand(a))
	return rules
}

func checkPRCommand(a *app.App) *cobra.Command {
	var pull, head, base string
	var incident bool
	check := &cobra.Command{
		Use:   "check-pr",
		Short: "Run rulings.py check-pr on a PR head against the drive's register, print its lines and exit as it exits; a Sprite, which has no rulings.py, asks the Mac's store desk to run it",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			cfg, err := a.Config()
			if err != nil {
				return err
			}
			claude, err := drive.ConfigDir()
			if err != nil {
				return err
			}
			argv := []string{filepath.Join(claude, "long-running", "current", "bin", "rulings.py"), "check-pr", "--repo", cfg.Checkout, "--drive", cfg.Drive, "--pr", pull, "--head", head}
			if base != "" {
				argv = append(argv, "--base", base)
			}
			if incident {
				argv = append(argv, "--incident")
			}
			_, err = a.Runner.Run(cmd.Context(), shell.Command{Argv: argv, Stdout: cmd.OutOrStdout(), Stderr: cmd.ErrOrStderr()})
			var exit *shell.ExitError
			if errors.As(err, &exit) {
				return &wait.ExitError{Code: exit.Code}
			}
			return err
		},
	}
	check.Flags().StringVar(&pull, "pr", "", "OWNER/NAME#N: the PR whose head to check")
	check.Flags().StringVar(&head, "head", "", "the head SHA to check")
	check.Flags().StringVar(&base, "base", "", "the base ref to diff against; default the PR's base")
	check.Flags().BoolVar(&incident, "incident", false, "check as an incident fix, refusing only what an incident merge must clear")
	for _, name := range []string{"pr", "head"} {
		if err := check.MarkFlagRequired(name); err != nil {
			panic(err)
		}
	}
	return check
}

func settleCommand(opened func() (*store.Store, error)) *cobra.Command {
	var pull string
	settle := &cobra.Command{
		Use:   "settle",
		Short: "Read findings on one PR as JSON on stdin, print those no owner pick registered for that PR answers, and exit 1 when any remain",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			var findings []Finding
			if err := json.NewDecoder(cmd.InOrStdin()).Decode(&findings); err != nil {
				return fmt.Errorf("findings on stdin: %w", err)
			}
			s, err := opened()
			if err != nil {
				return err
			}
			defer s.Close()
			picks, err := s.CheckedRules()
			if err != nil {
				return err
			}
			open, settled := Settle(findings, picks, pull)
			for _, found := range settled {
				fmt.Fprintf(cmd.OutOrStdout(), "settled %s by %s: %s\n", found.Key, found.Rule, found.Words)
			}
			for _, finding := range open {
				fmt.Fprintf(cmd.OutOrStdout(), "open %s: %s\n", finding.Key, finding.Line)
			}
			if len(open) > 0 {
				return fmt.Errorf("%d findings no owner pick answers", len(open))
			}
			return nil
		},
	}
	settle.Flags().StringVar(&pull, "pr", "", "OWNER/NAME#N: the PR the findings are on")
	if err := settle.MarkFlagRequired("pr"); err != nil {
		panic(err)
	}
	return settle
}

func answersCommand(a *app.App) *cobra.Command {
	var repo, scope, pull, program string
	answers := &cobra.Command{
		Use:   "answers [ID...]",
		Short: "Print as JSON each answer named by id, as `row` finds one, and his top-ranked durable answers on a PR's scope; an id no answer holds is left out; --pr prints every answer a grade of that PR owes",
		RunE: func(cmd *cobra.Command, args []string) error {
			reader := &quotes.Reader{Runner: a.Runner, Entry: func() (drive.Entry, error) { return drive.Entry{Checkout: repo}, nil }}
			var found []quotes.Answer
			var err error
			if pull != "" {
				found, err = answersOwed(cmd.Context(), a.Runner, reader, program, pull)
			} else {
				found, err = reader.Answers(cmd.Context(), args, scope)
			}
			if err != nil {
				return err
			}
			return json.NewEncoder(cmd.OutOrStdout()).Encode(found)
		},
	}
	answers.Flags().StringVar(&repo, "repo", "", "the checkout whose cc-notes hold the answers")
	answers.Flags().StringVar(&scope, "scope", "", "the word of a PR title's scope to search his durable answers for")
	answers.Flags().StringVar(&pull, "pr", "", "OWNER/NAME#N: print every answer a grade of this PR owes, read as the coordinator reads them for a pass")
	answers.Flags().StringVar(&program, "program", "", "with --pr, the program whose topic ledger names his topics")
	if err := answers.MarkFlagRequired("repo"); err != nil {
		panic(err)
	}
	return answers
}

func answersOwed(ctx context.Context, runner shell.Runner, reader *quotes.Reader, program, pull string) ([]quotes.Answer, error) {
	repo, number, found := strings.Cut(pull, "#")
	if !found {
		return nil, fmt.Errorf("--pr %q is not OWNER/NAME#N", pull)
	}
	view, err := runner.Run(ctx, shell.Command{Argv: []string{"ccx", "vcs", "gh", "--max-age", "0s", "--", "pr", "view", number, "--repo", repo, "--json", quotes.PRFields}})
	if err != nil {
		return nil, err
	}
	var pr quotes.PR
	if err := json.Unmarshal([]byte(view.Stdout), &pr); err != nil {
		return nil, fmt.Errorf("%s: %w", pull, err)
	}
	return reader.ForPR(ctx, program, pr, "")
}

func pickCommand(a *app.App, opened func() (*store.Store, error)) *cobra.Command {
	var words, answers string
	var pulls []string
	pick := &cobra.Command{
		Use:   "pick ID",
		Short: "Register an owner pick, the finding key or words it answers, and the PRs whose change it is about; it settles findings on those PRs alone",
		Args:  cobra.ExactArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			for _, pull := range pulls {
				if repo, number, found := strings.Cut(pull, "#"); !found || !strings.Contains(repo, "/") || number == "" {
					return fmt.Errorf("--pr %q is not OWNER/NAME#N", pull)
				}
			}
			s, err := opened()
			if err != nil {
				return err
			}
			defer s.Close()
			return s.PutRule(args[0], words, store.Stamp(a.Now()), "predicate", answersPrefix+answers, pulls...)
		},
	}
	pick.Flags().StringVar(&words, "words", "", "the owner's words, verbatim")
	pick.Flags().StringVar(&answers, "answers", "", "the finding key, or words its line contains, this pick answers")
	pick.Flags().StringArrayVar(&pulls, "pr", nil, "OWNER/NAME#N whose change the pick is about, repeated for each PR of that change")
	for _, name := range []string{"words", "answers", "pr"} {
		if err := pick.MarkFlagRequired(name); err != nil {
			panic(err)
		}
	}
	return pick
}
