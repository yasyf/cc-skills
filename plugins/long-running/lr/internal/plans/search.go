package plans

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const sectionChars = 1500

var (
	markdownHeading = regexp.MustCompile(`^#{1,6} `)
	listItem        = regexp.MustCompile(`^(?:[-*] |\d+(?:\.\d+)* )`)
	searchWord      = regexp.MustCompile(`[\pL\pN_]{3,}`)
	unsearched      = map[string]bool{"the": true, "and": true, "for": true, "with": true, "that": true, "this": true, "what": true, "how": true, "does": true, "should": true, "from": true, "are": true, "when": true, "which": true, "into": true}
)

// Hit is one plan paragraph or table row that shares words with the question.
type Hit struct {
	Source  string
	Line    int
	Heading string
	Text    string
	shared  int
	length  int
}

func (h Hit) String() string {
	return fmt.Sprintf("%s:%d %s\n%s", h.Source, h.Line, h.Heading, h.Text)
}

// Search returns the paragraphs and table rows of the program's plans and of
// files that share the most words with question, best first, each under its
// heading.
func Search(ctx context.Context, ccn Ccn, program, question string, limit int, files ...string) ([]Hit, error) {
	words := map[string]bool{}
	for _, word := range searchWord.FindAllString(strings.ToLower(question), -1) {
		if !unsearched[word] {
			words[word] = true
		}
	}
	if len(words) == 0 {
		return nil, fmt.Errorf("the question %q has no word of three or more letters to search the plan for", question)
	}
	listed, err := listed[IdlePlan](ctx, ccn, "plan", "list", "--label", "program:"+program, "--json")
	if err != nil {
		return nil, err
	}
	var hits []Hit
	for _, plan := range listed {
		out, err := ccn(ctx, "plan", "show", plan.ID, "--json")
		if err != nil {
			return nil, err
		}
		var record planRecord
		if err := json.Unmarshal([]byte(out), &record); err != nil {
			return nil, fmt.Errorf("ccn plan show %s: %w", plan.ID, err)
		}
		hits = append(hits, sections("ccn plan "+plan.ID[:min(7, len(plan.ID))], record.Body, words)...)
	}
	for _, file := range files {
		body, err := os.ReadFile(file)
		if err != nil {
			return nil, err
		}
		hits = append(hits, sections(file, string(body), words)...)
	}
	slices.SortStableFunc(hits, func(a, b Hit) int { return cmp.Or(cmp.Compare(b.shared, a.shared), cmp.Compare(a.length, b.length)) })
	return hits[:min(limit, len(hits))], nil
}

func sections(source, body string, words map[string]bool) []Hit {
	var hits []Hit
	lines := strings.Split(body, "\n")
	heading, from := "", -1
	flush := func(end int) {
		if from < 0 {
			return
		}
		text := strings.TrimSpace(strings.Join(lines[from:end], "\n"))
		at := from
		from = -1
		seen := map[string]bool{}
		for _, word := range searchWord.FindAllString(strings.ToLower(text), -1) {
			if words[word] {
				seen[word] = true
			}
		}
		if len(seen) > 0 {
			hits = append(hits, Hit{Source: source, Line: at + 1, Heading: heading, Text: Clip(text, sectionChars), shared: len(seen), length: len(text)})
		}
	}
	for at, line := range lines {
		switch {
		case markdownHeading.MatchString(line):
			flush(at)
			heading = strings.TrimSpace(strings.TrimLeft(line, "#"))
		case strings.TrimSpace(line) == "":
			flush(at)
		case strings.HasPrefix(strings.TrimSpace(line), "|"):
			flush(at)
			from = at
			flush(at + 1)
		case listItem.MatchString(line):
			flush(at)
			from = at
		case from < 0:
			from = at
		}
	}
	flush(len(lines))
	return hits
}

func Command(a *app.App) *cobra.Command {
	var driveName string
	var files []string
	var limit int
	cmd := &cobra.Command{
		Use:   "search-plan <question>",
		Short: "The drive's plan sections that answer a question, from its ccn plans and any --file, each with its source and line; when none settles it, ask root with cci post --kind ask",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			ccn := func(ctx context.Context, argv ...string) (string, error) {
				result, err := a.Runner.Run(ctx, shell.Command{Argv: append([]string{"ccn", "-R", entry.Checkout}, argv...)})
				return result.Stdout, err
			}
			hits, err := Search(cmd.Context(), ccn, entry.Program, args[0], limit, files...)
			if err != nil {
				return err
			}
			if len(hits) == 0 {
				return fmt.Errorf("no plan section shares a word with %q; ask root with cci post --kind ask rather than guess", args[0])
			}
			for _, hit := range hits {
				fmt.Fprintf(cmd.OutOrStdout(), "%s\n\n", hit)
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&driveName, "drive", "", "the drive whose plan to search; the session's drive when empty")
	cmd.Flags().StringArrayVar(&files, "file", nil, "a plan file to search as well, such as /home/sprite/drive/plan.md")
	cmd.Flags().IntVar(&limit, "limit", 5, "the most sections to print")
	return cmd
}
