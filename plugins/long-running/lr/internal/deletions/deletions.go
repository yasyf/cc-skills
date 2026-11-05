package deletions

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	Label       = "owed-deletion"
	symbolLabel = "symbol:"
	dueLabel    = "due:"
	readTimeout = 2 * time.Minute
)

var (
	anchorSuffix = regexp.MustCompile(`(?::\d+(?:-\d+)?)?(?:\s+@\s+\S+)?$`)
	unanchored   = []string{"pending", "none", "all"}
	notRulings   = []string{"corpus/", "no/"}
)

type Owed struct {
	ID, Title, Owner     string
	Paths, Dirs, Symbols []string
	Due                  time.Time
}

func (o Owed) Overdue(now time.Time) bool {
	return !o.Due.IsZero() && now.After(o.Due.AddDate(0, 0, 1))
}

func (o Owed) String() string {
	targets := slices.Concat(o.Paths, o.Dirs)
	for _, symbol := range o.Symbols {
		targets = append(targets, "#"+symbol)
	}
	owner, due := o.Owner, "no due date"
	if owner == "" {
		owner = "nobody"
	}
	if !o.Due.IsZero() {
		due = "due " + o.Due.Format(time.DateOnly)
	}
	return fmt.Sprintf("task %s %q (%s; owner %s, %s)", o.ID[:min(7, len(o.ID))], o.Title, strings.Join(targets, ", "), owner, due)
}

type task struct {
	ID       string   `json:"id"`
	Title    string   `json:"title"`
	Assignee string   `json:"assignee"`
	Labels   []string `json:"labels"`
	Anchors  []struct {
		Kind  string `json:"kind"`
		Value string `json:"value"`
	} `json:"anchors"`
}

func owedOf(found task) (Owed, error) {
	owner, _, _ := strings.Cut(found.Assignee, " <")
	owed := Owed{ID: found.ID, Title: found.Title, Owner: owner}
	for _, label := range found.Labels {
		if symbol, ok := strings.CutPrefix(label, symbolLabel); ok {
			owed.Symbols = append(owed.Symbols, symbol)
		}
		if date, ok := strings.CutPrefix(label, dueLabel); ok {
			due, err := time.Parse(time.DateOnly, date)
			if err != nil {
				return Owed{}, fmt.Errorf("task %s: %w", found.ID, err)
			}
			owed.Due = due
		}
	}
	for _, anchor := range found.Anchors {
		switch anchor.Kind {
		case "path":
			owed.Paths = append(owed.Paths, anchor.Value)
		case "dir":
			owed.Dirs = append(owed.Dirs, anchor.Value)
		}
	}
	return owed, nil
}

type Trunk struct {
	Runner   shell.Runner
	Checkout string
	Branch   string
}

func Of(runner shell.Runner, cfg config.Config) Trunk {
	return Trunk{Runner: runner, Checkout: cfg.Checkout, Branch: cfg.Trunk}
}

func (t Trunk) List(ctx context.Context, labels ...string) ([]Owed, error) {
	args := make([]string, 0, 6+2*len(labels))
	args = append(args, "task", "list", "--all-branches", "--json", "--label", Label)
	for _, label := range labels {
		args = append(args, "--label", label)
	}
	out, err := t.run(ctx, "ccn", args...)
	if err != nil {
		return nil, err
	}
	var summaries []task
	if err := json.Unmarshal([]byte(out), &summaries); err != nil {
		return nil, err
	}
	owed := make([]Owed, 0, len(summaries))
	for _, summary := range summaries {
		out, err := t.run(ctx, "ccn", "task", "show", summary.ID, "--json")
		if err != nil {
			return nil, err
		}
		var found task
		if err := json.Unmarshal([]byte(out), &found); err != nil {
			return nil, err
		}
		row, err := owedOf(found)
		if err != nil {
			return nil, err
		}
		owed = append(owed, row)
	}
	return owed, nil
}

func (t Trunk) Present(ctx context.Context, rows []Owed) ([]Owed, error) {
	if len(rows) == 0 {
		return nil, nil
	}
	tip, err := t.fetchTip(ctx)
	if err != nil {
		return nil, err
	}
	var present []Owed
	for _, row := range rows {
		there, err := t.present(ctx, row, tip)
		if err != nil {
			return nil, err
		}
		if there {
			present = append(present, row)
		}
	}
	return present, nil
}

func (t Trunk) fetchTip(ctx context.Context) (string, error) {
	listed, err := t.run(ctx, "git", "ls-remote", "origin", "refs/heads/"+t.Branch)
	if err != nil {
		return "", err
	}
	tip, _, found := strings.Cut(listed, "\t")
	if !found {
		return "", fmt.Errorf("origin of %s advertises no %s", t.Checkout, t.Branch)
	}
	_, err = t.run(ctx, "git", "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--refmap=", "origin", tip)
	return tip, err
}

func (t Trunk) present(ctx context.Context, row Owed, tip string) (bool, error) {
	anchors := slices.Concat(row.Paths, row.Dirs)
	if len(anchors) == 0 {
		return true, nil
	}
	listing, err := t.run(ctx, "git", append([]string{"ls-tree", tip, "--"}, anchors...)...)
	if err != nil {
		return false, err
	}
	if listing == "" || len(row.Symbols) == 0 {
		return listing != "", nil
	}
	args := []string{"grep", "-q", "-w", "-F"}
	for _, symbol := range row.Symbols {
		args = append(args, "-e", identifier(symbol))
	}
	args = append(args, tip, "--")
	for line := range strings.Lines(listing) {
		meta, path, _ := strings.Cut(strings.TrimSuffix(line, "\n"), "\t")
		if strings.Fields(meta)[1] == "tree" {
			path = ":(glob)" + path + "/**/*.go"
		}
		args = append(args, path)
	}
	_, err = t.run(ctx, "git", append(args, ":(glob,exclude)**/*_test.go")...)
	var exit *shell.ExitError
	if errors.As(err, &exit) && exit.Code == 1 {
		return false, nil
	}
	return err == nil, err
}

func (t Trunk) run(ctx context.Context, tool string, args ...string) (string, error) {
	repo := []string{"-R", t.Checkout}
	if tool == "git" {
		repo = []string{"-C", t.Checkout}
	}
	result, err := t.Runner.Run(ctx, shell.Command{Argv: slices.Concat([]string{tool}, repo, args), Timeout: readTimeout})
	return result.Stdout, err
}

type Ruling struct {
	Key, Kind, Status      string
	Removes                bool
	Paths, Symbols, Guards []string
}

type Gap struct {
	Target      string
	Symbol      bool
	Keys, Paths []string
}

func (t Trunk) Gaps(ctx context.Context, rulings, drive string) ([]string, error) {
	result, err := t.Runner.Run(ctx, shell.Command{Argv: []string{rulings, "rows", "--drive", drive, "--repo", t.Checkout, "--json"}, Timeout: readTimeout})
	if err != nil {
		return nil, err
	}
	found, err := ParseRulings(result.Stdout)
	if err != nil {
		return nil, err
	}
	owed, err := t.List(ctx)
	if err != nil {
		return nil, err
	}
	var lines []string
	gaps := Gaps(found, owed)
	if len(gaps) == 0 {
		return lines, nil
	}
	tip, err := t.fetchTip(ctx)
	if err != nil {
		return nil, err
	}
	for _, gap := range gaps {
		standing, err := t.standing(ctx, gap, tip)
		if err != nil {
			return nil, err
		}
		if standing {
			lines = append(lines, fmt.Sprintf("%s, removed by %s", gap.Target, strings.Join(gap.Keys, ", ")))
		}
	}
	return lines, nil
}

func (t Trunk) standing(ctx context.Context, gap Gap, tip string) (bool, error) {
	if len(gap.Paths) == 0 {
		return true, nil
	}
	args := []string{"grep", "-q", "-e", "^"}
	if gap.Symbol {
		args = []string{"grep", "-q", "-w", "-F", "-e", identifier(gap.Target)}
	}
	args = slices.Concat(args, []string{tip, "--"}, gap.Paths, []string{":(glob,exclude)**/*_test.go"})
	_, err := t.run(ctx, "git", args...)
	var exit *shell.ExitError
	if errors.As(err, &exit) && exit.Code == 1 {
		return false, nil
	}
	return err == nil, err
}

func ParseRulings(raw string) ([]Ruling, error) {
	var payload struct {
		Rows map[string]map[string]string `json:"rows"`
	}
	if err := json.Unmarshal([]byte(raw), &payload); err != nil {
		return nil, err
	}
	var rulings []Ruling
	for _, key := range slices.Sorted(maps.Keys(payload.Rows)) {
		fields := payload.Rows[key]
		if slices.ContainsFunc(notRulings, func(prefix string) bool { return strings.HasPrefix(key, prefix) }) || fields["status"] == "superseded" {
			continue
		}
		var implications []struct{ Direction, State string }
		if fields["implications"] != "" {
			if err := json.Unmarshal([]byte(fields["implications"]), &implications); err != nil {
				return nil, fmt.Errorf("row %s implications: %w", key, err)
			}
		}
		paths, err := anchored(fields["paths"])
		if err != nil {
			return nil, fmt.Errorf("row %s paths: %w", key, err)
		}
		symbols, err := anchored(fields["symbols"])
		if err != nil {
			return nil, fmt.Errorf("row %s symbols: %w", key, err)
		}
		guards, err := anchored(fields["guards"])
		if err != nil {
			return nil, fmt.Errorf("row %s guards: %w", key, err)
		}
		for _, anchors := range [][]string{paths, guards} {
			for i, anchor := range anchors {
				anchors[i] = anchorSuffix.ReplaceAllString(anchor, "")
			}
		}
		rulings = append(rulings, Ruling{
			Key: key, Kind: fields["kind"], Status: fields["status"], Paths: paths, Symbols: symbols, Guards: guards,
			Removes: slices.ContainsFunc(implications, func(item struct{ Direction, State string }) bool {
				return item.Direction == "removes" && item.State != "struck"
			}),
		})
	}
	return rulings, nil
}

func anchored(value string) ([]string, error) {
	listed := []string{value}
	if strings.HasPrefix(value, "[") {
		if err := json.Unmarshal([]byte(value), &listed); err != nil {
			return nil, err
		}
	}
	return slices.DeleteFunc(listed, func(item string) bool { return item == "" || slices.Contains(unanchored, item) }), nil
}

func Gaps(rulings []Ruling, owed []Owed) []Gap {
	found := map[string]Gap{}
	for _, ruling := range rulings {
		if ruling.Status != "in-force" || !ruling.Removes {
			continue
		}
		symbolic := len(ruling.Symbols) > 0
		targets := ruling.Paths
		if symbolic {
			targets = ruling.Symbols
		}
		for _, target := range targets {
			names := func(paths, symbols []string) bool {
				if symbolic {
					return slices.ContainsFunc(symbols, func(symbol string) bool { return tail(symbol) == tail(target) })
				}
				return slices.ContainsFunc(paths, func(path string) bool { return coveredPath(target, path) })
			}
			listed := slices.ContainsFunc(owed, func(row Owed) bool {
				return names(slices.Concat(row.Paths, row.Dirs), row.Symbols)
			})
			excused := slices.ContainsFunc(rulings, func(other Ruling) bool {
				return other.Kind == "exception" && names(other.Paths, other.Symbols)
			})
			if listed || excused || names(ruling.Guards, ruling.Guards) {
				continue
			}
			gap := Gap{Target: target, Paths: []string{target}}
			if symbolic {
				gap = Gap{Target: tail(target), Symbol: true, Paths: ruling.Paths}
			}
			seen := found[gap.Target]
			gap.Keys, gap.Paths = seen.Keys, union(seen.Paths, gap.Paths)
			if !slices.Contains(gap.Keys, ruling.Key) {
				gap.Keys = append(gap.Keys, ruling.Key)
			}
			found[gap.Target] = gap
		}
	}
	gaps := make([]Gap, 0, len(found))
	for _, target := range slices.Sorted(maps.Keys(found)) {
		gaps = append(gaps, found[target])
	}
	return gaps
}

func union(a, b []string) []string {
	return slices.Compact(slices.Sorted(slices.Values(slices.Concat(a, b))))
}

func tail(symbol string) string {
	return symbol[strings.LastIndex(symbol, ".")+1:]
}

func identifier(symbol string) string {
	parts := strings.Split(tail(symbol), "::")
	return parts[len(parts)-1]
}

func coveredPath(target, path string) bool {
	glob := strings.NewReplacer(`\*`, ".*", `\?`, ".").Replace(regexp.QuoteMeta(target))
	return strings.HasPrefix(target, path+"/") || regexp.MustCompile("^"+glob+"$").MatchString(path)
}
