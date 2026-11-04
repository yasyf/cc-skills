package owner

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	AwayAfter = 30 * time.Minute

	ownerKind    = "owner"
	wordKind     = "lr.presence"
	wordAway     = "away"
	wordHere     = "here"
	wordHolds    = overnight
	attrWord     = "word"
	attrRow      = "row"
	attrIncident = "incident"

	backfilled  = "Backfilled from board"
	noCommander = "has no running incident commander"
	ciRead      = 2 * time.Minute
	connectWait = 20 * time.Second
	probeLabel  = "lr-owner-away"
)

var hisLanes = []string{communicator, "board-watch", "dashboard"}

type incidentHold struct {
	Incident string `json:"incident"`
	Phase    string `json:"phase"`
	Owner    *struct {
		Name string `json:"name"`
	} `json:"owner"`
	TookOver  bool            `json:"tookOver"`
	StoodDown json.RawMessage `json:"stoodDown"`
}

type awayCheck struct {
	app      *app.App
	entry    drive.Entry
	incident string
	now      time.Time
	read     []string
}

func awayCommand(a *app.App, driveName *string) *cobra.Command {
	return &cobra.Command{
		Use:   "away INCIDENT",
		Short: "Print present or away with what it read, and exit non-zero when present: a fix lane takes INCIDENT over from ai-oncall only on away",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			entry, err := drive.Named(*driveName)
			if err != nil {
				fmt.Fprintf(cmd.OutOrStdout(), "present: no drive read: %v\n", err)
				return errors.New("present")
			}
			check := awayCheck{app: a, entry: entry, incident: args[0], now: a.Now()}
			away, verdict := check.run(cmd.Context())
			word := "present"
			if away {
				word = "away"
			}
			fmt.Fprintf(cmd.OutOrStdout(), "%s: %s\n", word, verdict)
			for _, line := range check.read {
				fmt.Fprintf(cmd.OutOrStdout(), "read: %s\n", line)
			}
			if !away {
				return errors.New("present")
			}
			return nil
		},
	}
}

func wordCommand(driveName *string) *cobra.Command {
	var row, incident string
	cmd := &cobra.Command{
		Use:   "word away|here",
		Short: "Record his explicit word on his presence from one ledger row, which wins over lr owner away's clock; with --incident it speaks for that incident alone",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			word := args[0]
			if word != wordAway && word != wordHere {
				return fmt.Errorf("his word is away or here, not %s", word)
			}
			entry, err := drive.Named(*driveName)
			if err != nil {
				return err
			}
			path := Ledger(entry.StateDir)
			ledger, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			rows := Parse(ledger)
			i := slices.IndexFunc(rows, func(r Row) bool { return r.ID == row })
			if i < 0 {
				return fmt.Errorf("no row %q in %s", row, path)
			}
			attrs := map[string]string{attrWord: word, attrRow: row}
			if incident != "" {
				attrs[attrIncident] = incident
			}
			record, err := client.Post(cmd.Context(), client.Draft{
				Drive: entry.Bus(), Lane: communicator, Kind: wordKind, Attrs: attrs,
				Text: fmt.Sprintf("Owner %s %s: %s", row, word, rows[i].Text),
			})
			if err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), record.Name())
			return nil
		},
	}
	cmd.Flags().StringVar(&row, "row", "", "the ledger row holding his words")
	cmd.Flags().StringVar(&incident, "incident", "", "the one incident his word speaks for")
	if err := cmd.MarkFlagRequired("row"); err != nil {
		panic(err)
	}
	return cmd
}

func (c *awayCheck) run(ctx context.Context) (bool, string) {
	records, err := client.Tail(ctx, client.Query{Drive: c.entry.Bus(), Kinds: []string{ownerKind, wordKind}, Since: c.now.Add(-wordHolds)})
	if err != nil {
		return false, "the drive's records did not read: " + err.Error()
	}
	if word, ok := newestWord(records, c.incident); ok {
		c.read = append(c.read, fmt.Sprintf("word %s %s for %s, record %s", word.Fields.Attrs[attrRow], word.Fields.Attrs[attrWord], c.incident, word.Name()))
		if word.Fields.Attrs[attrWord] == wordHere {
			return false, "his word says he holds this incident"
		}
		return true, "his word orders this takeover"
	}
	seen, source, err := c.lastSeen(records)
	if err != nil {
		return false, "his ledger did not read: " + err.Error()
	}
	silent := c.now.Sub(seen).Round(time.Minute)
	c.read = append(c.read, fmt.Sprintf("newest from him: %s, %s ago", source, silent))
	switch word, ok := newestWord(records, ""); {
	case ok && word.Fields.Attrs[attrWord] == wordHere:
		c.read = append(c.read, "word "+word.Fields.Attrs[attrRow]+" here, record "+word.Name())
		return false, "his word says he is here"
	case ok && !seen.After(word.At):
		c.read = append(c.read, "word "+word.Fields.Attrs[attrRow]+" away, record "+word.Name())
	case c.now.Sub(seen) < AwayAfter:
		return false, fmt.Sprintf("he was seen %s ago, inside %s", silent, AwayAfter)
	}
	holder, err := c.holder(ctx)
	if err != nil {
		return false, "who holds the incident did not read: " + err.Error()
	}
	if holder != "" {
		return false, holder + " holds " + c.incident
	}
	return true, fmt.Sprintf("nothing from him for %s, and no person or commander holds %s", silent, c.incident)
}

func newestWord(records []client.Record, incident string) (client.Record, bool) {
	for _, record := range slices.Backward(records) {
		if string(record.Kind) == wordKind && record.Fields.Attrs[attrIncident] == incident {
			return record, true
		}
	}
	return client.Record{}, false
}

func (c *awayCheck) lastSeen(records []client.Record) (time.Time, string, error) {
	ledger, err := os.ReadFile(Ledger(c.entry.StateDir))
	if err != nil {
		return time.Time{}, "", err
	}
	rows := Parse(ledger)
	if len(rows) == 0 {
		return time.Time{}, "", errors.New("it holds no row")
	}
	newest := rows[len(rows)-1]
	seen, err := rowAt(newest.At, c.now)
	if err != nil {
		return time.Time{}, "", fmt.Errorf("%s: %w", newest.ID, err)
	}
	source := "ledger " + newest.ID
	for _, record := range records {
		if fromHim(record) && record.At.After(seen) {
			seen, source = record.At, fmt.Sprintf("record %s from %s", record.Name(), client.Stem(record.Lane))
		}
	}
	return seen, source, nil
}

func fromHim(record client.Record) bool {
	return string(record.Kind) == ownerKind && slices.Contains(hisLanes, client.Stem(record.Lane)) && !strings.HasPrefix(record.Text, backfilled)
}

func rowAt(clock string, now time.Time) (time.Time, error) {
	at, err := time.ParseInLocation(rowTime, clock, pacific)
	if err != nil {
		return time.Time{}, err
	}
	at = time.Date(now.In(pacific).Year(), at.Month(), at.Day(), at.Hour(), at.Minute(), 0, 0, pacific)
	if at.After(now.AddDate(0, 0, 1)) {
		return at.AddDate(-1, 0, 0), nil
	}
	return at, nil
}

func (c *awayCheck) holder(ctx context.Context) (string, error) {
	ci := filepath.Join(c.entry.Checkout, "tools/ci")
	result, err := c.app.Runner.Run(ctx, shell.Command{Argv: []string{ci, "oncall", "incidents", "--json"}, Dir: c.entry.Checkout, Timeout: ciRead})
	if err != nil {
		return "", err
	}
	var open []incidentHold
	if err := json.Unmarshal([]byte(result.Stdout), &open); err != nil {
		return "", err
	}
	i := slices.IndexFunc(open, func(hold incidentHold) bool { return hold.Incident == c.incident })
	if i < 0 {
		return "", errors.New("ci oncall incidents lists no open " + c.incident)
	}
	hold := open[i]
	c.read = append(c.read, fmt.Sprintf("ci oncall incidents: phase %s, took over %t, owner %t, stood down %t", hold.Phase, hold.TookOver, hold.Owner != nil, len(hold.StoodDown) > 0 && string(hold.StoodDown) != "null"))
	if hold.Owner != nil {
		return hold.Owner.Name, nil
	}
	return c.commander(ctx, ci)
}

func (c *awayCheck) commander(ctx context.Context, ci string) (string, error) {
	dir, err := os.MkdirTemp(c.entry.StateDir, "away-probe-")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(dir)
	state := filepath.Join(dir, "connect.json")
	result, err := c.app.Runner.Run(ctx, shell.Command{Argv: []string{ci, "oncall", "connect", c.incident, "--state", state, "--label", probeLabel}, Dir: c.entry.Checkout, Timeout: connectWait})
	said := strings.TrimSpace(result.Stdout + result.Stderr)
	c.read = append(c.read, fmt.Sprintf("ci oncall connect: %s (%v)", said, err))
	if strings.Contains(said, noCommander) {
		return "", nil
	}
	if _, statErr := os.Stat(state); statErr == nil {
		if _, err := c.app.Runner.Run(ctx, shell.Command{Argv: []string{ci, "oncall", "disconnect", c.incident, "--state", state}, Dir: c.entry.Checkout, Timeout: ciRead}); err != nil {
			c.read = append(c.read, "ci oncall disconnect failed, so the probe's connection lapses by itself: "+err.Error())
		}
	}
	return "a commander, since connect did not say none runs,", nil
}
