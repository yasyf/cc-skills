package orca

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	deskLane        = "orca-desk"
	deskRunner      = "desk-runner"
	runnerTopic     = "orca"
	runnerAlive     = incident.RunnerAlive
	runnerLookback  = 24 * time.Hour
	verdictPoll     = 2 * time.Second
	verdictLaunched = "LAUNCHED"
	verdictPosted   = "POSTED"
	kindGo          = "go"
	kindState       = "state"
	nothingPosted   = "nothing was posted"
)

var (
	launchVerdict = regexp.MustCompile(`^(LAUNCHED|LAUNCH-FAILED|LAUNCH-DUPLICATE|LAUNCH-EXPIRED|LAUNCH-HELD|UNVERIFIABLE|SPRITE-UNVERIFIABLE) (?:desk-lane-[\w.-]+/)?(R\d+(?:@\w+)?) ([\w.-]+): `)
	pacificTime   = mustLocation("America/Los_Angeles")
)

func mustLocation(name string) *time.Location {
	location, err := time.LoadLocation(name)
	if err != nil {
		panic(err)
	}
	return location
}

func relayLine(args []string) (string, string) {
	names := strings.Split(args[0], ",")
	return names[0], "relay to " + strings.Join(names, ", ") + ": " + args[1]
}

func holdLine(args []string) (string, string) {
	return args[0], fmt.Sprintf("hold %s owner=%s :: %s", args[0], args[1], args[2])
}

func unholdLine(args []string) (string, string) {
	return args[0], "unhold " + args[0]
}

func withdrawLine(args []string) (string, string) {
	return args[0], "withdraw " + args[0]
}

type Poster struct {
	Bus    Bus
	Runner shell.Runner
	Env    func(string) string
	Now    func() time.Time
	Sleep  func(time.Duration)
	Out    io.Writer
	Drive  string
	Author string
}

func (p Poster) Entry(ctx context.Context) (*drive.Entry, error) {
	name := p.Drive
	if name == "" {
		if entry := currentDriveEntry(p.Env); entry != nil {
			return entry, nil
		}
		me, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"cci", "me"}})
		if err != nil {
			return nil, err
		}
		if name = drive.BoundInCCI(me.Stdout); name == "" {
			return nil, fmt.Errorf("no drive in the registry is bound to session %s and cci me names none, so its orca desk runner is unknown and nothing is posted", p.Env(sessionEnv))
		}
	}
	for _, entry := range allDrives(p.Env) {
		if entry.Bus() == name {
			return &entry, nil
		}
	}
	return nil, fmt.Errorf("no drive in the registry is cci drive %s, so its orca desk runner is unknown and nothing is posted", name)
}

func (p Poster) runnerFault(ctx context.Context, bus string) (string, error) {
	now := p.Now()
	records, err := p.Bus.Tail(ctx, client.Query{Drive: bus, Since: now.Add(-runnerLookback), Kinds: []string{kindState}, Lanes: []string{deskRunner}})
	if err != nil {
		return "", err
	}
	return incident.RunnerFault(records, bus, now), nil
}

func (p Poster) requireRunner(ctx context.Context, bus, fallback string) error {
	fault, err := p.runnerFault(ctx, bus)
	if err != nil || fault == "" {
		return err
	}
	return fmt.Errorf("refused: %s, so nothing reads %s lines; %s", fault, deskLane, fallback)
}

func (p Poster) post(ctx context.Context, bus, topic, text, attempt string) (client.Record, error) {
	stored, err := p.Bus.Post(ctx, client.Draft{Drive: bus, Lane: p.Author, Kind: kindGo, To: []string{deskLane}, Topic: topic, Text: deskLane + ": " + text, ID: attempt})
	if err != nil {
		return client.Record{}, err
	}
	fmt.Fprintf(p.Out, "posted cci %s: %s\n", stored.Name(), stored.Text)
	return stored, nil
}

func (p Poster) Line(ctx context.Context, topic, text string) error {
	entry, err := p.Entry(ctx)
	if err != nil {
		return err
	}
	if err := p.requireRunner(ctx, entry.Bus(), nothingPosted); err != nil {
		return err
	}
	_, err = p.post(ctx, entry.Bus(), topic, text, "")
	return err
}

func (p Poster) Faults(ctx context.Context, order Order) ([]string, error) {
	entry, err := p.Entry(ctx)
	if err != nil {
		return nil, err
	}
	faults, _, err := order.faults(ctx, p.Runner, p.Env("HOME"), entry.Checkout, filepath.Dir(entry.Config()))
	return faults, err
}

func (p Poster) Numbered(ctx context.Context, lane string) (string, error) {
	if client.Stem(lane) != lane {
		return lane, nil
	}
	entry, err := p.Entry(ctx)
	if err != nil {
		return "", err
	}
	taken, err := client.Generations(ctx, entry.Bus(), lane)
	if err != nil {
		return "", err
	}
	highest := 0
	for _, name := range taken {
		if n, err := strconv.Atoi(strings.TrimPrefix(name, lane+"-")); err == nil {
			highest = max(highest, n)
		}
	}
	if highest == 0 && slices.Contains(taken, lane) {
		return lane, nil
	}
	return fmt.Sprintf("%s-%d", lane, highest+1), nil
}

func (p Poster) Launch(ctx context.Context, order Order, timeout time.Duration) (string, error) {
	brief, err := filepath.Abs(order.Brief)
	if err != nil {
		return "", err
	}
	order.Brief = brief
	entry, err := p.Entry(ctx)
	if err != nil {
		return "", err
	}
	faults, left, err := order.faults(ctx, p.Runner, p.Env("HOME"), entry.Checkout, filepath.Dir(entry.Config()))
	if err != nil {
		return "", err
	}
	if len(faults) > 0 {
		return "", fmt.Errorf("refused, nothing was posted: %s", strings.Join(faults, "; "))
	}
	bus := entry.Bus()
	if err := p.requireRunner(ctx, bus, "spawn an Agent named `"+order.Lane+"` instead"); err != nil {
		return "", err
	}
	raw, err := os.ReadFile(order.Brief)
	if err != nil {
		return "", err
	}
	claims := briefClaims(string(raw))
	warnings := slices.Concat(left, p.claimedBeside(ctx, bus, order.Lane, claims))
	if grant := order.grantWarning(string(raw)); grant != "" {
		warnings = append(warnings, grant)
	}
	if doubled := doubledCCXWarning(order.Brief, string(raw)); doubled != "" {
		warnings = append(warnings, doubled)
	}
	if missing := order.sliceWarning(string(raw)); missing != "" {
		warnings = append(warnings, missing)
	}
	for _, warning := range warnings {
		fmt.Fprintln(p.Out, "WARN "+warning)
	}
	stored, err := p.post(ctx, bus, order.Lane, order.Line(), incident.LaunchAttempt(order.Lane, p.Now()))
	if err != nil {
		return "", err
	}
	key := "R" + strings.TrimPrefix(stored.Name(), "#")
	label, stage, err := p.settle(ctx, bus, stored, key, order.Lane, timeout)
	if label == verdictLaunched {
		p.claim(ctx, bus, order.Lane, claims)
	}
	if err != nil || label != "" {
		return label, err
	}
	fault, err := p.runnerFault(ctx, bus)
	if err != nil {
		return "", err
	}
	if fault == "" {
		fmt.Fprintf(p.Out, "%s %s posted with no verdict after %.0fs; the orca runner is alive, so its verdict arrives as a %s record\n", order.Lane, key, timeout.Seconds(), deskRunner)
		fmt.Fprintln(p.Out, incident.StageWait(order.Lane, key, bus, stage))
		return verdictPosted, nil
	}
	fmt.Fprintf(p.Out, "%s %s unsettled after %.0fs: %s; check it with `cci tail --drive %s --lane %s --kind state --topic %s -n 1`\n", order.Lane, key, timeout.Seconds(), fault, bus, deskRunner, runnerTopic)
	return "", nil
}

func (p Poster) settle(ctx context.Context, bus string, posted client.Record, key, lane string, timeout time.Duration) (string, *incident.LaunchStage, error) {
	deadline := p.Now().Add(timeout)
	since := posted.Seq - 1
	var last *incident.LaunchStage
	for p.Now().Before(deadline) {
		records, err := p.Bus.Tail(ctx, client.Query{Drive: bus, After: since, Lanes: []string{deskRunner}})
		if err != nil {
			return "", last, err
		}
		for _, record := range records {
			since = max(since, record.Seq)
			if stage, found := incident.StageOf(record, posted.At); found && stage.Key == key && stage.Lane == lane {
				fmt.Fprintln(p.Out, stage)
				last = &stage
				continue
			}
			match := launchVerdict.FindStringSubmatch(record.Text)
			if record.Lane == deskRunner && match != nil && match[2] == key && match[3] == lane {
				fmt.Fprintf(p.Out, "%s %s %s\n", lane, key, record.Text)
				return match[1], last, nil
			}
		}
		p.Sleep(verdictPoll)
	}
	return "", last, nil
}

func incidentOrders(name, fixBrief, evidenceBrief, commsBrief string) []Order {
	stem := strings.TrimSuffix(name, "-"+roleFix)
	orders := []Order{{Lane: stem + "-" + roleFix, Model: agentIncident, Effort: "xhigh", Role: roleFix, Brief: fixBrief, Now: true}}
	if evidenceBrief != "" {
		orders = append(orders, Order{Lane: stem + "-evidence", Model: agentIncident, Effort: "xhigh", Role: "evidence", Brief: evidenceBrief, Now: true})
	}
	if commsBrief != "" {
		orders = append(orders, Order{Lane: stem + "-comms", Model: modelIncidentOpus, Effort: "xhigh", Role: "comms", Brief: commsBrief, Now: true})
	}
	return orders
}

func wrongLaneFaults(orders []Order) ([]string, error) {
	var faults []string
	for _, order := range orders {
		brief, err := filepath.Abs(order.Brief)
		if err != nil {
			return nil, err
		}
		order.Brief = brief
		if fault := order.wrongLaneFault(); fault != "" {
			faults = append(faults, fault)
		}
	}
	return faults, nil
}

func doubledCCXWarning(brief, text string) string {
	lines := briefCCX.FindAllString(text, -1)
	if len(lines) < 2 {
		return ""
	}
	return fmt.Sprintf("brief %s has %d `ccx:` lines, and each field reads the first line naming it, so keep one: %s", brief, len(lines), strings.Join(lines, " | "))
}
