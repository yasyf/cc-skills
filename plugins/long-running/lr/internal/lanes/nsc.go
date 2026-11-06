package lanes

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	nscTopic   = "nsc:"
	nscKind    = "evidence"
	nscWarning = "urgent"
	nscRenewed = "report"
	nscLabel   = "lane"
	nscWarnAt  = time.Hour
	nscMake    = 15 * time.Minute
)

var nscExtended = regexp.MustCompile(`(?m)^New deadline: (\S+)`)

type nscShape struct {
	VCPU int    `json:"virtual_cpu"`
	OS   string `json:"os"`
}

type nscBox struct {
	ID      string            `json:"cluster_id"`
	Created time.Time         `json:"created_at"`
	Labels  map[string]string `json:"labels"`
	Shape   nscShape          `json:"shape"`
}

type NscDeadlines struct {
	Env   Env
	Store *store.Store
	Log   *slog.Logger
	read  time.Time
	after int64
	due   map[string]time.Time
	lasts map[string]time.Duration
}

func (n *NscDeadlines) Check(ctx context.Context, now time.Time) error {
	if !n.read.IsZero() && now.Sub(n.read) < gcEvery {
		return nil
	}
	n.read = now
	stdout, err := n.Env.Run(ctx, "nsc", "list", "-o", "json")
	if err != nil {
		n.Log.Warn("nsc deadlines did not list the boxes", "err", err)
		return nil
	}
	var boxes []nscBox
	if err := json.Unmarshal([]byte(stdout), &boxes); err != nil {
		n.Log.Warn("nsc deadlines did not parse the boxes", "err", err)
		return nil
	}
	if err := n.recorded(ctx, now); err != nil {
		n.Log.Warn("nsc deadlines did not read the recorded deadlines", "err", err)
		return nil
	}
	for _, box := range boxes {
		deadline, known := n.due[box.ID]
		if left := deadline.Sub(now); !known || left <= 0 || left > nscWarnAt {
			continue
		}
		delete(n.due, box.ID)
		if err := n.renew(ctx, box, deadline, now); err != nil {
			return fmt.Errorf("%w: nsc renew %s: %w", ErrUnread, box.ID, err)
		}
	}
	return nil
}

func (n *NscDeadlines) renew(ctx context.Context, box nscBox, deadline, now time.Time) error {
	lasts, known := n.lasts[box.ID]
	if !known {
		_, err := n.Env.Bus.Post(ctx, n.warning(box, deadline, now, ""))
		return err
	}
	lane, found, err := n.Store.Lane(box.Labels[nscLabel])
	if err != nil {
		return err
	}
	if !found || lane.State == store.LaneRetired {
		_, err := n.Env.Bus.Post(ctx, client.Draft{
			Drive: n.Env.Config.Drive, Lane: Self, Kind: nscWarning, To: []string{Root}, ID: fmt.Sprintf("nsc-deadline:%s:%d", box.ID, deadline.Unix()),
			Text: fmt.Sprintf("NSC-DEADLINE %s: this Namespace box dies by %s, in %d minutes, and its lane %s has no live registry row, so the supervisor leaves it to expire. Destroy it now with `nsc destroy %s`, or keep it with `lr nsc extend %s --drive %s --lane %s -- --duration <how much longer>`.", box.ID, pacific(deadline), int(deadline.Sub(now).Minutes()), box.Labels[nscLabel], box.ID, box.ID, n.Env.Config.Drive, Root),
		})
		return err
	}
	_, renewed, err := extendNsc(ctx, n.Env.Runner, box.ID, []string{"--duration", lasts.String()})
	if err != nil {
		n.Log.Warn("nsc refused to extend a box", "box", box.ID, "err", err)
		_, err := n.Env.Bus.Post(ctx, n.warning(box, deadline, now, truncated(err.Error(), 300)))
		return err
	}
	if err := recordNscDeadline(ctx, n.Env.Bus, n.Env.Config.Drive, Self, box.ID, renewed, lasts); err != nil {
		return err
	}
	n.due[box.ID] = renewed
	_, err = n.Env.Bus.Post(ctx, client.Draft{
		Drive: n.Env.Config.Drive, Lane: Self, Kind: nscRenewed, To: []string{lane.Name}, ID: fmt.Sprintf("nsc-renewed:%s:%d", box.ID, renewed.Unix()), Force: true,
		Text: fmt.Sprintf("NSC-EXTENDED %s: this Namespace box was to die by %s, so the supervisor extended it by its create duration, %s; it now dies by %s. Destroy it with `nsc destroy %s` once its work is done.", box.ID, pacific(deadline), lasts, pacific(renewed), box.ID),
	})
	return err
}

func (n *NscDeadlines) recorded(ctx context.Context, now time.Time) error {
	query := client.Query{Drive: n.Env.Config.Drive, Kinds: []string{nscKind}, After: n.after}
	if n.after == 0 {
		query.Since = now.Add(-adoptWindow)
	}
	records, err := n.Env.Bus.Tail(ctx, query)
	if err != nil {
		return err
	}
	if n.due == nil {
		n.due, n.lasts = map[string]time.Time{}, map[string]time.Duration{}
	}
	for _, record := range records {
		n.after = max(n.after, record.Seq)
		box, ours := strings.CutPrefix(record.Topic, nscTopic)
		if !ours {
			continue
		}
		deadline, err := time.Parse(time.RFC3339, record.Fields.Attrs["deadline"])
		if err != nil {
			return fmt.Errorf("record %s: %w", record.Name(), err)
		}
		n.due[box] = deadline
		if lasts, given := record.Fields.Attrs["duration"]; given {
			if n.lasts[box], err = time.ParseDuration(lasts); err != nil {
				return fmt.Errorf("record %s: %w", record.Name(), err)
			}
		}
	}
	return nil
}

func (n *NscDeadlines) warning(box nscBox, deadline, now time.Time, refused string) client.Draft {
	lane, unlabeled := box.Labels[nscLabel], ""
	if lane == "" {
		lane, unlabeled = Root, " It carries no lane label, so the warning goes to the coordinator."
	}
	next := fmt.Sprintf("Extend it with `lr nsc extend %s --drive %s --lane %s -- --duration <how much longer>`, or finish its work and destroy it.", box.ID, n.Env.Config.Drive, lane)
	if refused != "" {
		next = fmt.Sprintf("nsc refused to extend it: %s. Recreate it with `lr nsc create --drive %s --lane %s -- <nsc create flags>` and move its work there.", refused, n.Env.Config.Drive, lane)
	}
	return client.Draft{
		Drive: n.Env.Config.Drive, Lane: Self, Kind: nscWarning, To: []string{lane}, ID: fmt.Sprintf("nsc-deadline:%s:%d", box.ID, deadline.Unix()), Force: true,
		Text: fmt.Sprintf("NSC-DEADLINE %s: this Namespace box dies by %s, in %d minutes.%s %s", box.ID, pacific(deadline), int(deadline.Sub(now).Minutes()), unlabeled, next),
	}
}

func MakeNscBox(ctx context.Context, runner shell.Runner, bus Bus, drive, lane string, flags []string) (string, error) {
	made, err := os.CreateTemp("", "nsc-create-*.json")
	if err != nil {
		return "", err
	}
	_ = made.Close()
	defer func() { _ = os.Remove(made.Name()) }()
	argv := append(append([]string{"nsc", "create"}, flags...), "--label", nscLabel+"="+lane, "--output_json_to", made.Name())
	result, err := runner.Run(ctx, shell.Command{Argv: argv, Timeout: nscMake})
	if err != nil {
		return "", err
	}
	data, err := os.ReadFile(made.Name())
	if err != nil {
		return "", err
	}
	var box struct {
		ID       string    `json:"cluster_id"`
		Created  time.Time `json:"created"`
		Deadline time.Time `json:"deadline"`
	}
	if err := json.Unmarshal(data, &box); err != nil {
		return "", fmt.Errorf("nsc create --output_json_to: %w", err)
	}
	return result.Stdout, recordNscDeadline(ctx, bus, drive, lane, box.ID, box.Deadline, box.Deadline.Sub(box.Created).Round(time.Minute))
}

func ExtendNscBox(ctx context.Context, runner shell.Runner, bus Bus, drive, lane, id string, flags []string) (string, error) {
	printed, deadline, err := extendNsc(ctx, runner, id, flags)
	if err != nil {
		return "", err
	}
	return printed, recordNscDeadline(ctx, bus, drive, lane, id, deadline, 0)
}

func extendNsc(ctx context.Context, runner shell.Runner, id string, flags []string) (string, time.Time, error) {
	result, err := runner.Run(ctx, shell.Command{Argv: append([]string{"nsc", "extend", id}, flags...), Timeout: readTimeout})
	if err != nil {
		return "", time.Time{}, err
	}
	extended := nscExtended.FindStringSubmatch(result.Stdout)
	if extended == nil {
		return "", time.Time{}, fmt.Errorf("nsc extend %s printed no new deadline: %q", id, result.Stdout)
	}
	deadline, err := time.Parse(time.RFC3339, extended[1])
	if err != nil {
		return "", time.Time{}, fmt.Errorf("nsc extend %s: %w", id, err)
	}
	return result.Stdout, deadline, nil
}

func recordNscDeadline(ctx context.Context, bus Bus, drive, lane, id string, deadline time.Time, lasts time.Duration) error {
	attrs := map[string]string{"deadline": deadline.UTC().Format(time.RFC3339)}
	if lasts > 0 {
		attrs["duration"] = lasts.String()
	}
	_, err := bus.Post(ctx, client.Draft{
		Drive: drive, Lane: lane, Kind: nscKind, Topic: nscTopic + id, ID: fmt.Sprintf("%s%s:%d", nscTopic, id, deadline.Unix()), Attrs: attrs,
		Text: fmt.Sprintf("NSC %s: the Namespace box %s holds dies by %s", id, lane, pacific(deadline)),
	})
	return err
}

func NscCommand(a *app.App) *cobra.Command {
	var drive, lane string
	cmd := &cobra.Command{Use: "nsc", Short: "Make and extend Namespace boxes labeled with their lane, recording each deadline so the supervisor warns that lane an hour before it"}
	cmd.PersistentFlags().StringVar(&drive, "drive", "", "the drive whose bus records the deadline")
	cmd.PersistentFlags().StringVar(&lane, "lane", "", "the lane that holds the box")
	for _, name := range []string{"drive", "lane"} {
		if err := cmd.MarkPersistentFlagRequired(name); err != nil {
			panic(err)
		}
	}
	cmd.AddCommand(
		&cobra.Command{Use: "create [-- NSC_CREATE_FLAGS...]", Short: "Run nsc create with the label lane=<lane> and record the box's deadline; a macOS box also prints how many macOS boxes lanes hold against the workspace limit", RunE: func(cmd *cobra.Command, flags []string) error {
			if warning := macCreateWarning(cmd.Context(), a.Runner, drive, flags); warning != "" {
				fmt.Fprintln(cmd.ErrOrStderr(), warning)
			}
			printed, err := MakeNscBox(cmd.Context(), a.Runner, CCI{}, drive, lane, flags)
			fmt.Fprint(cmd.OutOrStdout(), printed)
			return err
		}},
		&cobra.Command{Use: "extend ID [-- NSC_EXTEND_FLAGS...]", Short: "Run nsc extend and record the box's new deadline", Args: cobra.MinimumNArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
			printed, err := ExtendNscBox(cmd.Context(), a.Runner, CCI{}, drive, lane, args[0], args[1:])
			fmt.Fprint(cmd.OutOrStdout(), printed)
			return err
		}},
	)
	return cmd
}
