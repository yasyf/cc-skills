package landing

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"path/filepath"
	"slices"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	kindGo   = "go"
	openPR   = "OPEN"
	filesJQ  = ".[].filename"
	goReader = "desk-runner"
)

type goFlags struct {
	lane, head string
	before     int64
}

func goCommand(a *app.App) *cobra.Command {
	var flags goFlags
	cmd := &cobra.Command{
		Use:   "go PR...",
		Short: "Gate an exempt go on each PR at its exact head and, with --lane, post the ones the gate admits; a refused PR prints every reason and posts nothing",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			numbers := make([]int, len(args))
			for i, arg := range args {
				number, err := strconv.Atoi(strings.TrimPrefix(arg, "#"))
				if err != nil {
					return err
				}
				numbers[i] = number
			}
			if (flags.before != 0) != (flags.head != "") || flags.before != 0 && (flags.lane != "" || len(numbers) != 1) {
				return errors.New("--before replays one PR at the --head it names and posts nothing, so it takes one PR, --head and no --lane")
			}
			return runGo(cmd.Context(), a, numbers, flags, cmd.OutOrStdout())
		},
	}
	cmd.Flags().StringVar(&flags.lane, "lane", "", "post each admitted go as this lane; without it the pass is dry")
	cmd.Flags().StringVar(&flags.head, "head", "", "the head a replayed go named, with --before")
	cmd.Flags().Int64Var(&flags.before, "before", 0, "replay one PR's past go: read only the records before this seq, at --head, posting nothing")
	return cmd
}

func runGo(ctx context.Context, a *app.App, numbers []int, flags goFlags, stdout io.Writer) error {
	cfg, err := a.Config()
	if err != nil {
		return err
	}
	inputs, err := readGateInputs(ctx, cfg.Drive, a.Now())
	if err != nil {
		return err
	}
	heads, err := currentHeads(ctx, a.CCX, cfg.Repo, numbers, flags.head)
	if err != nil {
		return err
	}
	s, err := store.Open(filepath.Join(filepath.Dir(a.ConfigPath), storeFile))
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	for _, number := range numbers {
		head, open := heads[number]
		if !open {
			fmt.Fprintf(stdout, "REFUSED #%d: it is not an open pull request\n", number)
			continue
		}
		records, err := client.Tail(ctx, client.Query{Drive: cfg.Drive, PR: fmt.Sprintf("%s#%d", cfg.Repo, number)})
		if err != nil {
			return err
		}
		if flags.before != 0 {
			records = slices.DeleteFunc(records, func(record client.Record) bool { return record.Seq >= flags.before })
		}
		files, err := a.CCX.GH(ctx, ccx.MaxAgeFresh, "api", "--paginate", fmt.Sprintf("repos/%s/pulls/%d/files", cfg.Repo, number), "--jq", filesJQ)
		if err != nil {
			return err
		}
		body, err := a.CCX.GH(ctx, ccx.MaxAgeFresh, "pr", "view", strconv.Itoa(number), "--repo", cfg.Repo, "--json", "body", "--jq", ".body")
		if err != nil {
			fmt.Fprintf(stdout, "WARNING #%d: could not read its body, so no wait it names refuses it: %v\n", number, err)
		}
		lifts, err := bus.HoldLifts(records, func(seqs []int64) ([]client.Record, error) { return client.Among(ctx, cfg.Drive, seqs) })
		if err != nil {
			fmt.Fprintf(stdout, "WARNING #%d: could not read what its holds resolve, so each counts as a lift: %v\n", number, err)
		}
		claim := gates.Claim{Repo: cfg.Repo, PR: number, Head: head}
		text, refusals := gates.ExemptGo(claim, gates.Read{Lifts: lifts, Records: records, GoLanes: inputs.GoLanes, Exempt: inputs.Exempt, TapRules: inputs.TapRules, Files: strings.Fields(files), Cards: inputs.Cards, TapRows: inputs.Taps, Body: body})
		if flags.before == 0 {
			row, found, err := s.ReviewRow(cfg.Repo, number)
			if err != nil {
				return err
			}
			if unread := gates.RowRead(row, found, head); unread != "" {
				refusals = append(refusals, unread)
			}
		}
		if len(refusals) > 0 {
			fmt.Fprintf(stdout, "REFUSED #%d at %s: %s\n", number, head[:7], strings.Join(refusals, "; "))
			continue
		}
		if flags.lane == "" {
			fmt.Fprintf(stdout, "GO #%d at %s (dry): %s\n", number, head[:7], text)
			continue
		}
		posted, err := client.Post(ctx, client.Draft{Drive: cfg.Drive, Lane: flags.lane, Kind: kindGo, To: []string{goReader}, PRs: []string{strconv.Itoa(number)}, Text: text})
		if err != nil {
			return err
		}
		fmt.Fprintf(stdout, "GO #%d at %s: posted %s: %s\n", number, head[:7], posted.Name(), text)
	}
	return nil
}

type landingConfig struct {
	ExemptGrades []string            `json:"exempt_grades"`
	GoClasses    []string            `json:"go_classes"`
	PortPhrase   string              `json:"port_phrase"`
	TapRules     map[string][]string `json:"tap_rules"`
}

func landingSection(path string) (landingConfig, error) {
	runner, err := config.LoadRunner(path)
	if err != nil {
		return landingConfig{}, err
	}
	var section landingConfig
	if err := json.Unmarshal(runner.Sections["landing"], &section); err != nil {
		return landingConfig{}, fmt.Errorf("%s: landing: %w", path, err)
	}
	section.ExemptGrades = gates.Grades(section.ExemptGrades, section.GoClasses, section.PortPhrase)
	return section, nil
}

func ownerWaits(live board.Live) ([]gates.Card, []gates.TapRow) {
	cards := make([]gates.Card, 0, len(live.Picks))
	for _, pick := range live.Picks {
		cards = append(cards, gates.Card{ID: pick.Block, Title: pick.Title})
	}
	var taps []gates.TapRow
	for _, wait := range live.OpenTaps() {
		if number, err := strconv.Atoi(wait.PR); err == nil {
			taps = append(taps, gates.TapRow{PR: number, Head: wait.Head})
		}
	}
	return cards, taps
}

func currentHeads(ctx context.Context, c ccx.Client, repo string, numbers []int, head string) (map[int]string, error) {
	if head != "" {
		return map[int]string{numbers[0]: strings.ToLower(head)}, nil
	}
	rows, err := c.PRStatus(ctx, repo, numbers)
	if err != nil {
		return nil, err
	}
	heads := make(map[int]string, len(rows))
	for _, row := range rows {
		if row.State == openPR && row.Head != "" {
			heads[row.Number] = row.Head
		}
	}
	return heads, nil
}
