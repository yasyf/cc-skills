package waste

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	transcriptTool = "cc-transcript"
	readLimit      = 5 * time.Minute
	choreWords     = 3
	hookKeyWords   = 6
)

var envPrefix = regexp.MustCompile(`^(cd \S+ *(&&|;) *|\w+=\S+ +)+`)

type transcript struct {
	Path     string  `json:"path"`
	Modified float64 `json:"mtime"`
	Size     int64   `json:"size"`
	lane     string
	owner    string
}

type binding struct{ session, agent string }

func (t transcript) session() string {
	if dir := filepath.Dir(t.Path); filepath.Base(dir) == "subagents" {
		return filepath.Base(filepath.Dir(dir))
	}
	return strings.TrimSuffix(filepath.Base(t.Path), ".jsonl")
}

func (t transcript) agent() string {
	if filepath.Base(filepath.Dir(t.Path)) != "subagents" {
		return ""
	}
	return strings.TrimPrefix(strings.TrimSuffix(filepath.Base(t.Path), ".jsonl"), "agent-")
}

func changedTranscripts(ctx context.Context, runner shell.Runner, cfg Config, peers []client.Peer, since time.Time) ([]transcript, int, error) {
	listed, err := runner.Run(ctx, shell.Command{Argv: []string{transcriptTool, "list", "--all", "--json"}, Timeout: readLimit})
	if err != nil {
		return nil, 0, err
	}
	laneOf := make(map[binding]string, len(peers))
	for _, peer := range peers {
		laneOf[binding{peer.Session, peer.Agent}] = peer.Lane
	}
	var files []transcript
	decoder := json.NewDecoder(strings.NewReader(listed.Stdout))
	for decoder.More() {
		var file transcript
		if err := decoder.Decode(&file); err != nil {
			return nil, 0, fmt.Errorf("%s list printed a row that does not parse: %w", transcriptTool, err)
		}
		owner, owned := laneOf[binding{file.session(), ""}]
		lane, bound := laneOf[binding{file.session(), file.agent()}]
		if !bound {
			lane, bound = owner, owned
		}
		if bound && file.Modified >= float64(since.Unix()) {
			file.lane, file.owner = lane, cmp.Or(owner, lane)
			files = append(files, file)
		}
	}
	slices.SortFunc(files, func(a, b transcript) int {
		return cmp.Or(cmp.Compare(b.Size, a.Size), cmp.Compare(a.Path, b.Path))
	})
	return files[:min(cfg.Transcripts, len(files))], len(files), nil
}

type usage struct {
	Input         int64 `json:"input_tokens"`
	Output        int64 `json:"output_tokens"`
	CacheRead     int64 `json:"cache_read_input_tokens"`
	CacheCreation int64 `json:"cache_creation_input_tokens"`
}

func (u usage) total() int64 {
	return u.Input + u.Output + u.CacheRead + u.CacheCreation
}

type block struct {
	Type  string          `json:"type"`
	Name  string          `json:"name"`
	Input json.RawMessage `json:"input"`
}

type event struct {
	Kind string `json:"kind"`
	Meta struct {
		Timestamp time.Time `json:"timestamp"`
	} `json:"meta"`
	RequestID      string  `json:"request_id"`
	Usage          *usage  `json:"usage"`
	Blocks         []block `json:"blocks"`
	AttachmentType string  `json:"attachment_type"`
	Detail         struct {
		HookName string          `json:"hook_name"`
		Content  json.RawMessage `json:"content"`
		Raw      struct {
			Rendered json.RawMessage `json:"rendered"`
		} `json:"raw"`
	} `json:"detail"`
}

func (e event) injected() (string, int64) {
	if e.AttachmentType == "hook_additional_context" {
		return e.Detail.HookName + " " + shape(strings.TrimLeft(string(e.Detail.Content), `["`), hookKeyWords), int64(len(e.Detail.Content))
	}
	return e.AttachmentType, int64(len(e.Detail.Raw.Rendered))
}

type injection struct {
	chars int64
	count int64
	lanes map[string]int64
}

type chore struct{ lane, shape string }

type transcriptUse struct {
	window, prior map[string]int64
	sessions      map[string]map[string]bool
	injections    map[string]*injection
	chores        map[chore]int64
	repeats       map[string]map[string]int64
}

func readTranscripts(ctx context.Context, runner shell.Runner, files []transcript, since time.Time, window time.Duration) (transcriptUse, error) {
	used := transcriptUse{
		window:     map[string]int64{},
		prior:      map[string]int64{},
		sessions:   map[string]map[string]bool{},
		injections: map[string]*injection{},
		chores:     map[chore]int64{},
		repeats:    map[string]map[string]int64{},
	}
	for _, file := range files {
		shown, err := runner.Run(ctx, shell.Command{Argv: []string{transcriptTool, "show", file.Path, "--json", "--all", "--kind", "assistant", "--kind", "attachment"}, Timeout: readLimit})
		if err != nil {
			return used, err
		}
		if used.sessions[file.lane] == nil {
			used.sessions[file.lane] = map[string]bool{}
		}
		used.sessions[file.lane][file.session()] = true
		if err := used.add(file.lane, shown.Stdout, since, since.Add(-window)); err != nil {
			return used, fmt.Errorf("%s: %w", file.Path, err)
		}
	}
	return used, nil
}

func (u transcriptUse) add(lane, stdout string, since, priorSince time.Time) error {
	type request struct {
		at     time.Time
		tokens int64
	}
	requests := map[string]request{}
	seen := map[string]map[string]bool{}
	decoder := json.NewDecoder(strings.NewReader(stdout))
	for decoder.More() {
		var e event
		if err := decoder.Decode(&e); err != nil {
			return err
		}
		inWindow := !e.Meta.Timestamp.Before(since)
		if e.Kind == "attachment" {
			if key, chars := e.injected(); inWindow && chars > 0 {
				u.inject(key, lane, chars)
			}
			continue
		}
		if e.Usage != nil {
			requests[e.RequestID] = request{e.Meta.Timestamp, e.Usage.total()}
		}
		for _, call := range e.Blocks {
			if call.Type != "tool_use" {
				continue
			}
			if err := u.tool(lane, call, seen, inWindow); err != nil {
				return err
			}
		}
	}
	for _, spent := range requests {
		switch {
		case !spent.at.Before(since):
			u.window[lane] += spent.tokens
		case !spent.at.Before(priorSince):
			u.prior[lane] += spent.tokens
		}
	}
	return nil
}

func (u transcriptUse) inject(key, lane string, chars int64) {
	entry := u.injections[key]
	if entry == nil {
		entry = &injection{lanes: map[string]int64{}}
		u.injections[key] = entry
	}
	entry.chars += chars
	entry.count++
	entry.lanes[lane] += chars
}

func (u transcriptUse) tool(lane string, call block, seen map[string]map[string]bool, inWindow bool) error {
	switch call.Name {
	case "Bash":
		var input struct {
			Command string `json:"command"`
		}
		if err := json.Unmarshal(call.Input, &input); err != nil {
			return err
		}
		if inWindow {
			u.chores[chore{lane, commandShape(input.Command)}]++
		}
	case "Read":
		var input struct {
			FilePath string          `json:"file_path"`
			Offset   json.RawMessage `json:"offset"`
			Limit    json.RawMessage `json:"limit"`
		}
		if err := json.Unmarshal(call.Input, &input); err != nil {
			return err
		}
		span := string(input.Offset) + ":" + string(input.Limit)
		if seen[input.FilePath] == nil {
			seen[input.FilePath] = map[string]bool{}
		}
		if seen[input.FilePath][span] && inWindow {
			if u.repeats[lane] == nil {
				u.repeats[lane] = map[string]int64{}
			}
			u.repeats[lane][filepath.Base(input.FilePath)]++
		}
		seen[input.FilePath][span] = true
	case "Edit", "Write", "NotebookEdit":
		var input struct {
			FilePath     string `json:"file_path"`
			NotebookPath string `json:"notebook_path"`
		}
		if err := json.Unmarshal(call.Input, &input); err != nil {
			return err
		}
		delete(seen, cmp.Or(input.FilePath, input.NotebookPath))
	}
	return nil
}

func commandShape(command string) string {
	line, _, _ := strings.Cut(strings.TrimSpace(command), "\n")
	var words []string
	for _, word := range strings.Fields(envPrefix.ReplaceAllString(line, "")) {
		if strings.Contains(word, "/") {
			word = "<path>"
		}
		words = append(words, digits.ReplaceAllString(word, "N"))
		if len(words) == choreWords {
			break
		}
	}
	return strings.Join(words, " ")
}

func (u transcriptUse) findings(cfg Config, records []client.Record, window time.Duration) []Finding {
	posts := map[string]int64{}
	for _, record := range records {
		posts[record.Lane]++
	}
	var findings []Finding
	for lane, tokens := range u.window {
		size := perHour(tokens, window)
		if size < cfg.Floors.Cost {
			continue
		}
		perRecord := "no records"
		if posts[lane] > 0 {
			perRecord = fmt.Sprintf("%d per record", tokens/posts[lane])
		}
		findings = append(findings, Finding{signalCost, signalCost + "/" + lane, size, "tokens/h", lane, fmt.Sprintf("previous window %d/h, %s, %d sessions", perHour(u.prior[lane], window), perRecord, len(u.sessions[lane])), cfg.Floors.Cost})
	}
	for key, entry := range u.injections {
		if entry.chars >= cfg.Floors.Injected {
			findings = append(findings, Finding{signalInjected, signalInjected + "/" + key, entry.chars, "chars", key, fmt.Sprintf("%d injections, most in %s", entry.count, ranked(entry.lanes, 1)), cfg.Floors.Injected})
		}
	}
	for shaped, runs := range u.chores {
		if runs >= cfg.Floors.Chores {
			findings = append(findings, Finding{signalChore, signalChore + "/" + shaped.lane + "/" + shaped.shape, runs, "runs", shaped.lane, fmt.Sprintf("%q", shaped.shape), cfg.Floors.Chores})
		}
	}
	for _, lane := range slices.Sorted(maps.Keys(u.repeats)) {
		var size int64
		for _, count := range u.repeats[lane] {
			size += count
		}
		if size >= cfg.Floors.Reads {
			findings = append(findings, Finding{signalRepeatRead, signalRepeatRead + "/" + lane, size, "reads", lane, "unchanged: " + ranked(u.repeats[lane], 2), cfg.Floors.Reads})
		}
	}
	return findings
}
