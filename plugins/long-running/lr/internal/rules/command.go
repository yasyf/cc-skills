package rules

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/quotes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
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
	}, &cobra.Command{
		Use:   "settle",
		Short: "Read findings as JSON on stdin, print those no owner pick answers, and exit 1 when any remain",
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
			open, settled := Settle(findings, picks)
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
	})
	return rules
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
	pick := &cobra.Command{
		Use:   "pick ID",
		Short: "Register an owner pick and the finding key or words it answers",
		Args:  cobra.ExactArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			s, err := opened()
			if err != nil {
				return err
			}
			defer s.Close()
			return s.PutRule(args[0], words, store.Stamp(a.Now()), "predicate", answersPrefix+answers)
		},
	}
	pick.Flags().StringVar(&words, "words", "", "the owner's words, verbatim")
	pick.Flags().StringVar(&answers, "answers", "", "the finding key, or words its line contains, this pick answers")
	for _, name := range []string{"words", "answers"} {
		if err := pick.MarkFlagRequired(name); err != nil {
			panic(err)
		}
	}
	return pick
}
