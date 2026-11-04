package board

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	onlyHim     = "Only your approval."
	fieldChars  = 700
	planChecked = "Checked against the plan by the fidelity review at this version."
)

var (
	heading  = regexp.MustCompile(`(?m)^#{1,3}\s+(.+?)\s*$`)
	planLine = regexp.MustCompile(`(?im)^.*\b(?:plan (?:step|row|\d)|rule/[0-9a-f]{6,}|appendix)\b.*$`)
)

func ReviewRows(snapshot []machine.Facts) []store.ReviewRow {
	byNumber := make(map[int]machine.Facts, len(snapshot))
	for _, f := range snapshot {
		byNumber[f.Number] = f
	}
	rows := make([]store.ReviewRow, 0, len(snapshot))
	for _, f := range snapshot {
		if f.Closed || f.Merged || !f.Ours() {
			continue
		}
		waits := withoutTap(gates.Missing(f))
		if below, stuck := stuckBelow(f, byNumber); stuck {
			waits = append(waits, fmt.Sprintf("the change below it, %s", titleOf(below)))
		}
		forUsers, plan, risk := section(f.Body, "Summary"), planOf(f), section(f.Body, "Risk profile")
		for _, field := range []struct{ said, name string }{{forUsers, "a Summary"}, {plan, "a plan row"}, {risk, "a Risk profile"}} {
			if field.said == "" {
				waits = append(waits, "its PR body has no "+field.name)
			}
		}
		said := onlyHim
		if len(waits) > 0 {
			said = strings.Join(waits, "; ")
		}
		rows = append(rows, store.ReviewRow{Number: f.Number, Head: f.Head, Lane: f.Lane, Title: titleOf(f), ForUsers: forUsers, Plan: plan, Risk: risk, Review: reviewOf(f), Waits: said, Ready: len(waits) == 0, Tapped: f.Tapped()})
	}
	return rows
}

func section(body, name string) string {
	found := heading.FindAllStringSubmatchIndex(body, -1)
	for at, match := range found {
		if !strings.EqualFold(body[match[2]:match[3]], name) {
			continue
		}
		end := len(body)
		if at+1 < len(found) {
			end = found[at+1][0]
		}
		return clipped(strings.TrimSpace(body[match[1]:end]))
	}
	return ""
}

func clipped(text string) string {
	runes := []rune(text)
	if len(runes) <= fieldChars {
		return text
	}
	return strings.TrimRight(string(runes[:fieldChars-1]), " \t\n") + "…"
}

func planOf(f machine.Facts) string {
	line := strings.TrimSpace(planLine.FindString(f.Body))
	switch {
	case line != "" && f.Fidelity == machine.FidelityPass:
		return clipped(line) + " " + planChecked
	case line != "":
		return clipped(line)
	case f.Fidelity == machine.FidelityPass:
		return planChecked
	}
	return ""
}

func reviewOf(f machine.Facts) string {
	ci := map[machine.CI]string{machine.CISuccess: "CI green", machine.CIFailure: "CI red", machine.CIPending: "CI running"}[f.CI]
	approved := "no approving review"
	if f.Approved {
		approved = "approved"
	}
	graded := "not graded"
	if f.Graded() {
		graded = "graded, a finding needs a verdict"
		if f.Cleared() {
			graded = "graded clear"
		}
	}
	return fmt.Sprintf("%s; %s; %s; mergeable: %s; queue: %s", ci, approved, graded, f.Mergeable, f.Queue)
}

func titleOf(f machine.Facts) string {
	if f.Title != "" {
		return f.Title
	}
	return fmt.Sprintf("#%d", f.Number)
}

func WriteReviewRows(s *store.Store, repo string, snapshot []machine.Facts, at time.Time) error {
	withdrawn, err := s.FactsOfKind(repo, FactWithdrawn)
	if err != nil {
		return err
	}
	return s.PutReviewRows(repo, ReviewRows(withoutWithdrawn(snapshot, withdrawn)), at)
}

func withoutWithdrawn(snapshot []machine.Facts, withdrawn map[store.FactKey]time.Time) []machine.Facts {
	return slices.DeleteFunc(slices.Clone(snapshot), func(f machine.Facts) bool {
		for key := range withdrawn {
			if key.Number == f.Number && namesHead(key.Head, f.Head) {
				return true
			}
		}
		return false
	})
}

func rowsCommand(opened func() (drive.Entry, string, error)) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "rows",
		Short: "Every open drive PR from the landing store, with what each waits on: the one list the review tab, status lines and asks read",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			entry, _, err := opened()
			if err != nil {
				return err
			}
			cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), c.ErrOrStderr())
			if err != nil {
				return err
			}
			s, err := store.Open(entry.Store())
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			rows, err := s.ReviewRows(cfg.Repo)
			if err != nil {
				return err
			}
			if asJSON {
				return json.NewEncoder(c.OutOrStdout()).Encode(rows)
			}
			for _, row := range rows {
				fmt.Fprintf(c.OutOrStdout(), "#%d at %s (%s): %s: %s\n", row.Number, abbreviate(row.Head), row.Lane, row.Title, row.Waits)
			}
			return nil
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "print the rows as one JSON array")
	return cmd
}
