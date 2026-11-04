package lanes

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/livetasks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	handoffWindow  = 12 * time.Hour
	handoffRecords = 20
	handoffChars   = 240
)

func handoffCommand(a *app.App) *cobra.Command {
	var cwd, transcript string
	cmd := &cobra.Command{
		Use:   "handoff LANE",
		Short: "Write a lane's handoff from its records, PRs and worktree with no model call, and post it; print the doc id",
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
			id, err := env.WriteHandoff(cmd.Context(), s, lane, cwd, transcript)
			if err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), id)
			return nil
		},
	}
	cmd.Flags().StringVar(&cwd, "cwd", "", "the lane's worktree")
	cmd.Flags().StringVar(&transcript, "transcript", "", "the lane's transcript, for its successor")
	return cmd
}

func (e Env) WriteHandoff(ctx context.Context, s *store.Store, lane, cwd, transcript string) (string, error) {
	id, err := e.writeHandoff(ctx, s, lane, cwd, transcript)
	if err == nil {
		return id, nil
	}
	to, toErr := NoticeTo(s, lane)
	if toErr != nil {
		return "", errors.Join(err, toErr)
	}
	_, postErr := e.Bus.Post(ctx, client.Draft{Drive: e.Config.Drive, Lane: lane, Kind: "blocker", To: []string{to}, Text: fmt.Sprintf("%s compacted with no handoff: %v. Its brief and records still hold the work.", lane, err)})
	if postErr != nil {
		return "", fmt.Errorf("%w; its blocker: %w", err, postErr)
	}
	return "", err
}

func (e Env) writeHandoff(ctx context.Context, s *store.Store, lane, cwd, transcript string) (string, error) {
	body, err := e.handoffBody(ctx, s, lane, cwd, transcript)
	if err != nil {
		return "", err
	}
	stamp := e.Now().UTC().Format("2006-01-02T1504Z")
	add := shell.Command{
		Argv:    []string{"ccn", "-R", or(cwd, e.Config.Checkout), "doc", "add", fmt.Sprintf("%s: handoff %s", lane, stamp), "--label", "handoff:" + lane, "--when", fmt.Sprintf("Resuming the %s lane of drive %s after a compaction: read before anything else, then its brief", lane, e.Config.Drive), "--body", "-", "--json"},
		Stdin:   body,
		Timeout: readTimeout,
	}
	result, err := e.Runner.Run(ctx, add)
	if err != nil {
		result, err = e.Runner.Run(ctx, add)
	}
	if err != nil {
		return "", fmt.Errorf("ccn doc add: %w", err)
	}
	var added struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &added); err != nil {
		return "", fmt.Errorf("ccn doc add answered %q: %w", result.Stdout, err)
	}
	if _, err := e.Run(ctx, "cci", "post", "--drive", e.Config.Drive, "--lane", lane, "--kind", "handoff", "--ccn", added.ID, "--text", fmt.Sprintf("%s wrote its handoff before compacting: doc %s", lane, added.ID)); err != nil {
		return "", fmt.Errorf("cci post: %w", err)
	}
	return added.ID, nil
}

func (e Env) handoffBody(ctx context.Context, s *store.Store, lane, cwd, transcript string) (body string, err error) {
	defer recovered(&err)
	row, _ := laneOf(s, lane)
	records, err := e.Bus.Tail(ctx, client.Query{Drive: e.Config.Drive, Lanes: []string{lane}, Since: e.Now().Add(-handoffWindow)})
	if err != nil {
		return "", fmt.Errorf("cci tail: %w", err)
	}
	open, err := e.Bus.Open(ctx, e.Config.Drive, "ask", "blocker", "decide")
	if err != nil {
		return "", fmt.Errorf("cci open: %w", err)
	}
	var out strings.Builder
	fmt.Fprintf(&out, "# %s handoff at %s\n\nThe PreCompact hook wrote this from the bus and the worktree, with no model call. Read it, then the brief.\n\n", lane, pacific(e.Now()))
	fmt.Fprintf(&out, "- Brief: %s\n- Transcript: %s\n- PRs it owns: %s\n", or(row.Brief, "none on the registry"), or(transcript, "unknown"), or(numberList(row.PRs), "none"))
	if row.Waiting != 0 {
		fmt.Fprintf(&out, "- Parked on: record #%s %s\n", strconv.FormatInt(row.Waiting, 10), row.Awaits)
	}
	if lane == OwnerRelay || isStanding(row) {
		orders, err := OpenOrders(ctx, e.Bus, s, e.Config.Drive, e.Config.Repo, e.State)
		if err != nil {
			return "", fmt.Errorf("lr owner audit: %w", err)
		}
		out.WriteString("\n## Open owner orders\n\nNo live lane, open PR or landed record delivers these yet (`lr owner audit`). Carry each until one does.\n\n")
		if len(orders) == 0 {
			out.WriteString("None.\n")
		}
		for _, order := range orders {
			fmt.Fprintf(&out, "- %s\n", order.Line())
		}
	}
	out.WriteString("\n## Asks and blockers addressed to it\n\n")
	addressed := slices.DeleteFunc(open, func(r client.Record) bool { return !Addresses(r.To, lane) })
	if len(addressed) == 0 {
		out.WriteString("None open.\n")
	}
	for _, r := range addressed {
		fmt.Fprintf(&out, "- #%d %s from %s: %s\n", r.Seq, strings.ToUpper(string(r.Kind)), r.Lane, truncated(r.Text, handoffChars))
	}
	out.WriteString("\n## Its last records, oldest first\n\n")
	if len(records) == 0 {
		out.WriteString("None in the last 12 hours.\n")
	}
	for _, r := range records[max(0, len(records)-handoffRecords):] {
		fmt.Fprintf(&out, "- #%d %s %s: %s\n", r.Seq, strings.ToUpper(string(r.Kind)), pacific(r.At), truncated(r.Text, handoffChars))
	}
	if transcript != "" {
		out.WriteString(liveTasksSection(transcript, e.Now()))
	}
	if cwd != "" {
		status, err := e.Run(ctx, "git", "-C", cwd, "status", "--short", "--branch")
		if err != nil {
			return "", fmt.Errorf("git status: %w", err)
		}
		log, err := e.Run(ctx, "git", "-C", cwd, "log", "-5", "--oneline")
		if err != nil {
			return "", fmt.Errorf("git log: %w", err)
		}
		fmt.Fprintf(&out, "\n## Worktree %s\n\n```text\n%s\n%s```\n", cwd, status, log)
	}
	return out.String(), nil
}

func liveTasksSection(transcript string, now time.Time) string {
	tasks, err := livetasks.Read(transcript, now)
	if err != nil {
		return "\n## Background tasks\n\nCould not read them: " + err.Error() + "\n"
	}
	return "\n" + livetasks.Section("## Background tasks live now, each with the call that starts it again\n", tasks)
}
