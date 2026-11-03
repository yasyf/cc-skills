package replay

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const nameBytes = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"

type Capture struct {
	Drive      string
	Class      string
	Defect     int64
	Fix        string
	Recurs     string
	RecursKind []string
	Check      string
	Lanes      []string
	From       int64
	Upto       int64
	Repo       string
	PRs        []int
	Expect     string
	Out        string
}

func namesLane(text, name string) bool {
	for at := 0; ; {
		found := strings.Index(text[at:], name)
		if found < 0 {
			return false
		}
		start, end := at+found, at+found+len(name)
		before := start == 0 || !strings.ContainsRune(nameBytes, rune(text[start-1]))
		after := end == len(text) || !strings.ContainsRune(nameBytes, rune(text[end]))
		if before && after {
			return true
		}
		at = start + 1
	}
}

func about(record map[string]any, wanted []string) bool {
	if name, _ := record["lane"].(string); slices.Contains(wanted, name) {
		return true
	}
	addressed, _ := record["to"].([]any)
	for _, to := range addressed {
		text, _ := to.(string)
		name, _, _ := strings.Cut(text, "@")
		if slices.ContainsFunc(wanted, func(lane string) bool { return lanes.Addresses([]string{name}, lane) }) {
			return true
		}
	}
	text, _ := record["text"].(string)
	return slices.ContainsFunc(wanted, func(name string) bool { return namesLane(text, name) })
}

func (d Deps) captureRecords(ctx context.Context, c Capture) ([]map[string]any, []map[string]any, error) {
	start := max(c.From-1, 0)
	records, err := d.paged(ctx, c.Drive, strconv.FormatInt(start, 10))
	if err != nil {
		return nil, nil, err
	}
	kept := make([]map[string]any, 0, len(records))
	for _, one := range records {
		if one.record.Seq > c.Upto {
			continue
		}
		var parsed map[string]any
		if err := json.Unmarshal(one.raw, &parsed); err != nil {
			return nil, nil, err
		}
		if about(parsed, c.Lanes) {
			kept = append(kept, scrubbed(parsed))
		}
	}
	rows, err := d.whoRows(ctx, c.Drive)
	if err != nil {
		return nil, nil, err
	}
	who := make([]map[string]any, 0, len(rows))
	for _, raw := range rows {
		var parsed map[string]any
		if err := json.Unmarshal(raw, &parsed); err != nil {
			return nil, nil, err
		}
		if name, _ := parsed["lane"].(string); !slices.Contains(c.Lanes, name) {
			continue
		}
		row := map[string]any{}
		for _, key := range keptRow {
			row[key] = parsed[key]
		}
		who = append(who, row)
	}
	return kept, who, nil
}

func (d Deps) capturePatches(ctx context.Context, c Capture) ([]map[string]any, error) {
	entries := make([]map[string]any, 0, len(c.PRs))
	for _, pr := range c.PRs {
		files, err := d.changedFiles(ctx, c.Repo, pr)
		if err != nil {
			return nil, err
		}
		patches := make(map[string]*string, len(files))
		for name, patch := range files {
			patches[name] = scrubbedPatch(name, patch)
		}
		entries = append(entries, map[string]any{"pr": pr, "patches": patches})
	}
	return entries, nil
}

func (d Deps) Capture(ctx context.Context, c Capture, out io.Writer) error {
	var expect any
	if err := json.Unmarshal([]byte(c.Expect), &expect); err != nil {
		return fmt.Errorf("--expect: %w", err)
	}
	given := map[string]any{}
	count, unit := 0, "PRs"
	if c.Check == "lanes" {
		records, who, err := d.captureRecords(ctx, c)
		if err != nil {
			return err
		}
		given["records"], given["who"] = records, who
		count, unit = len(records), "records"
	} else {
		entries, err := d.capturePatches(ctx, c)
		if err != nil {
			return err
		}
		given["prs"] = entries
		count = len(entries)
	}
	kinds := c.RecursKind
	if kinds == nil {
		kinds = []string{}
	}
	fixture := map[string]any{"class": c.Class, "defect": c.Defect, "fix": c.Fix, "recurs": map[string]any{"kinds": kinds, "text": c.Recurs}, "input": given, "expect": expect}
	var encoded bytes.Buffer
	encoder := json.NewEncoder(&encoded)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", " ")
	if err := encoder.Encode(fixture); err != nil {
		return err
	}
	if err := os.WriteFile(c.Out, encoded.Bytes(), 0o600); err != nil {
		return err
	}
	_, err := fmt.Fprintf(out, "replay: wrote %s with %d %s\n", c.Out, count, unit)
	return err
}

const noUpperBound = math.MaxInt64
