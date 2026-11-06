package lanes

import (
	"bytes"
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"maps"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/livetasks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	handoffWindow  = 12 * time.Hour
	handoffRecords = 20
	handoffChars   = 240
)

type handoffRegistry struct {
	Brief    string   `json:"brief"`
	PRs      []int    `json:"prs"`
	Waiting  int64    `json:"waiting"`
	Awaits   string   `json:"awaits"`
	Aliases  Aliases  `json:"aliases"`
	Standing bool     `json:"standing"`
	Orders   []string `json:"orders"`
	NoticeTo string   `json:"notice_to"`
}

type registryRead func(ctx context.Context, named []string) (handoffRegistry, error)

func handoffCommand(a *app.App) *cobra.Command {
	var cwd, transcript string
	deskWait := askdesk.AnswerWait
	cmd := &cobra.Command{
		Use:   "handoff LANE",
		Short: "Write a lane's handoff from its records, PRs and worktree with no model call, and post it; print the doc id, or the blocker it posted instead",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg, state, err := Loaded(a)
			if err != nil {
				return err
			}
			env, err := NewEnv(a.Runner, cfg, state, a.Now)
			if err != nil {
				return err
			}
			lane, err := Resolve(cmd.Context(), cfg.Drive, args[0])
			if err != nil {
				return err
			}
			if entry, onSprite := drive.SpriteEntry(); onSprite {
				_, err = env.WriteHandoff(cmd.Context(), cmd.OutOrStdout(), func(ctx context.Context, named []string) (handoffRegistry, error) {
					return askRegistry(ctx, askdesk.LiveCCI{}, entry.Bus(), lane, named, deskWait)
				}, lane, cwd, transcript)
				return err
			}
			s, err := store.Open(filepath.Join(state, StoreFile))
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			_, err = env.WriteHandoff(cmd.Context(), cmd.OutOrStdout(), env.storeRegistry(s, lane), lane, cwd, transcript)
			return err
		},
	}
	cmd.Flags().StringVar(&cwd, "cwd", "", "the lane's worktree")
	cmd.Flags().StringVar(&transcript, "transcript", "", "the lane's transcript, for its successor")
	cmd.Flags().DurationVar(&deskWait, "desk-wait", deskWait, "on a Sprite, how long to wait on the store desk's answer before posting the blocker")
	return cmd
}

func handoffRegistryCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:    "handoff-registry LANE [NAME...]",
		Short:  "Print as JSON what the drive's registry holds for a lane's handoff, with the alias of each NAME that is a registered numbered lane, which a Sprite lane's lr lanes handoff reads through the store desk",
		Hidden: true,
		Args:   cobra.MinimumNArgs(1),
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
			registry, err := env.storeRegistry(s, args[0])(cmd.Context(), args[1:])
			if err != nil {
				return err
			}
			return json.NewEncoder(cmd.OutOrStdout()).Encode(registry)
		},
	}
}

func askRegistry(ctx context.Context, desk askdesk.CCI, bus, lane string, named []string, wait time.Duration) (handoffRegistry, error) {
	ctx, stop := context.WithTimeout(ctx, wait)
	defer stop()
	var out, errs bytes.Buffer
	if err := askdesk.RunOnMac(ctx, desk, bus, askdesk.Asked{Verb: askdesk.HandoffRegistryVerb, Args: append([]string{"--", lane}, named...), Lane: lane}, &out, &errs); err != nil {
		return handoffRegistry{}, fmt.Errorf("lr %s on the Mac: %w %s", askdesk.HandoffRegistryVerb, err, errs.String())
	}
	var registry handoffRegistry
	if err := json.Unmarshal(out.Bytes(), &registry); err != nil {
		return handoffRegistry{}, fmt.Errorf("lr %s on the Mac answered %d bytes that are no registry: %w", askdesk.HandoffRegistryVerb, out.Len(), err)
	}
	return registry, nil
}

func (e Env) storeRegistry(s *store.Store, lane string) registryRead {
	return func(ctx context.Context, named []string) (registry handoffRegistry, err error) {
		defer recovered(&err)
		row, _ := laneOf(s, lane)
		registry = handoffRegistry{Brief: row.Brief, PRs: row.PRs, Waiting: row.Waiting, Awaits: row.Awaits, Aliases: AliasesOf(must(s.Registered(named))), Standing: lane == OwnerRelay || isStanding(row), NoticeTo: noticeTo(s, lane)}
		if !registry.Standing {
			return registry, nil
		}
		orders, err := OpenOrders(ctx, e.Bus, s, e.Config.Drive, e.Config.Repo, e.State)
		if err != nil {
			return registry, fmt.Errorf("lr owner audit: %w", err)
		}
		for _, order := range orders {
			registry.Orders = append(registry.Orders, order.Line())
		}
		return registry, nil
	}
}

func (e Env) WriteHandoff(ctx context.Context, out io.Writer, read registryRead, lane, cwd, transcript string) (string, error) {
	records, addressed, err := e.handoffRecords(ctx, lane)
	registry, readErr := read(ctx, numberedIn(lane, slices.Concat(records, addressed)))
	if err = cmp.Or(err, readErr); err == nil {
		var id string
		if id, err = e.writeHandoff(ctx, registry, records, addressed, lane, cwd, transcript); err == nil {
			fmt.Fprintln(out, id)
			return id, nil
		}
	}
	posted, postErr := e.Bus.Post(ctx, client.Draft{Drive: e.Config.Drive, Lane: lane, Kind: "blocker", To: []string{or(registry.NoticeTo, Root)}, Text: fmt.Sprintf("%s compacted with no handoff: %v. Its brief and records still hold the work.", lane, err)})
	if postErr != nil {
		return "", fmt.Errorf("%w; its blocker: %w", err, postErr)
	}
	fmt.Fprintln(out, "blocker", posted.Name())
	return "", err
}

func (e Env) writeHandoff(ctx context.Context, registry handoffRegistry, records, addressed []client.Record, lane, cwd, transcript string) (string, error) {
	body, err := e.handoffBody(ctx, registry, records, addressed, lane, cwd, transcript)
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

func (e Env) handoffRecords(ctx context.Context, lane string) (records, addressed []client.Record, err error) {
	records, err = e.Bus.Tail(ctx, client.Query{Drive: e.Config.Drive, Lanes: []string{lane}, Since: e.Now().Add(-handoffWindow)})
	if err != nil {
		return nil, nil, fmt.Errorf("cci tail: %w", err)
	}
	open, err := e.Bus.Open(ctx, e.Config.Drive, "ask", "blocker", "decide")
	if err != nil {
		return nil, nil, fmt.Errorf("cci open: %w", err)
	}
	return records[max(0, len(records)-handoffRecords):], slices.DeleteFunc(open, func(r client.Record) bool { return !Addresses(r.To, lane) }), nil
}

func numberedIn(lane string, records []client.Record) []string {
	named := map[string]bool{}
	for _, r := range records {
		for _, token := range laneToken.FindAllString(r.Lane+" "+truncated(r.Text, handoffChars), -1) {
			if word := strings.TrimRight(token, "."); word != lane && client.Stem(word) != word {
				named[word] = true
			}
		}
	}
	return slices.Sorted(maps.Keys(named))
}

func (e Env) handoffBody(ctx context.Context, registry handoffRegistry, records, addressed []client.Record, lane, cwd, transcript string) (string, error) {
	var out strings.Builder
	fmt.Fprintf(&out, "# %s handoff at %s\n\nThe PreCompact hook wrote this from the bus and the worktree, with no model call. Read it, then the brief.\n\n", lane, pacific(e.Now()))
	fmt.Fprintf(&out, "- Brief: %s\n- Transcript: %s\n- PRs it owns: %s\n", or(registry.Brief, "none on the registry"), or(transcript, "unknown"), or(numberList(registry.PRs), "none"))
	if registry.Waiting != 0 {
		fmt.Fprintf(&out, "- Parked on: record #%s %s\n", strconv.FormatInt(registry.Waiting, 10), registry.Awaits)
	}
	if registry.Standing {
		out.WriteString("\n## Open owner orders\n\nNo live lane, open PR or landed record delivers these yet (`lr owner audit`). Carry each until one does.\n\n")
		if len(registry.Orders) == 0 {
			out.WriteString("None.\n")
		}
		for _, order := range registry.Orders {
			fmt.Fprintf(&out, "- %s\n", order)
		}
	}
	out.WriteString("\n## Asks and blockers addressed to it\n\n")
	if len(addressed) == 0 {
		out.WriteString("None open.\n")
	}
	for _, r := range addressed {
		fmt.Fprintf(&out, "- %s %s from %s: %s\n", r.Name(), strings.ToUpper(string(r.Kind)), registry.Aliases.In(r.Lane), registry.Aliases.In(truncated(r.Text, handoffChars)))
	}
	out.WriteString("\n## Its last records, oldest first\n\n")
	if len(records) == 0 {
		out.WriteString("None in the last 12 hours.\n")
	}
	for _, r := range records {
		fmt.Fprintf(&out, "- %s %s %s: %s\n", r.Name(), strings.ToUpper(string(r.Kind)), pacific(r.At), registry.Aliases.In(truncated(r.Text, handoffChars)))
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
