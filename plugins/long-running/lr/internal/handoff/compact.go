package handoff

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	pollInterval    = 30 * time.Second
	compactDeadline = 30 * time.Minute
	waitTimeoutMS   = 120_000
	readChunk       = 64 * 1024
	promptMark      = "❯"
	modeIndent      = 2
	noticeMargin    = 2
)

var (
	compactBoundary = []byte(`"subtype":"compact_boundary"`)
	ruled           = regexp.MustCompile(`^─{2,}(?: [^─]+ ─+)?$`)
)

type compactionCursor struct {
	path     string
	start    int64
	offset   int64
	identity os.FileInfo
	overlap  []byte
	found    bool
}

func newCompactionCursor(path string, offset int64) *compactionCursor {
	return &compactionCursor{path: path, start: offset, offset: offset}
}

func (c *compactionCursor) compacted() (bool, error) {
	if c.found {
		return true, nil
	}
	file, err := os.Open(c.path)
	if err != nil {
		return false, err
	}
	defer func() { _ = file.Close() }()
	info, err := file.Stat()
	if err != nil {
		return false, err
	}
	if c.identity == nil || !os.SameFile(c.identity, info) || info.Size() < c.offset {
		c.offset, c.overlap = c.start, nil
	}
	c.identity = info
	if _, err := file.Seek(c.offset, io.SeekStart); err != nil {
		return false, err
	}
	chunk := make([]byte, readChunk)
	for {
		size, err := file.Read(chunk)
		if size > 0 {
			data := append(c.overlap, chunk[:size]...)
			c.offset += int64(size)
			c.overlap = bytes.Clone(data[max(0, len(data)-len(compactBoundary)+1):])
			if bytes.Contains(data, compactBoundary) {
				c.found = true
				return true, nil
			}
		}
		if errors.Is(err, io.EOF) {
			return false, nil
		}
		if err != nil {
			return false, err
		}
	}
}

type terminalScreen struct {
	Draft  json.RawMessage `json:"draft"`
	Source string          `json:"source"`
	Tail   []string        `json:"tail"`
}

func (t terminalScreen) hasDraft() bool {
	switch string(t.Draft) {
	case "", "null", "false", `""`:
		return false
	}
	return true
}

func isRule(line string) bool {
	return ruled.MatchString(strings.TrimSpace(line))
}

func idlePrompt(terminal terminalScreen) bool {
	if terminal.hasDraft() || terminal.Source != "screen" {
		return false
	}
	tail := make([]string, 0, len(terminal.Tail))
	rules := []int{}
	for at, line := range terminal.Tail {
		trimmed := strings.TrimRightFunc(line, unicode.IsSpace)
		tail = append(tail, trimmed)
		if isRule(trimmed) {
			rules = append(rules, at)
		}
	}
	if len(rules) < 2 {
		return false
	}
	upper, lower := rules[len(rules)-2], rules[len(rules)-1]
	if lower-upper != 2 || strings.TrimSpace(tail[upper+1]) != promptMark {
		return false
	}
	width := utf8.RuneCountInString(tail[lower])
	for row, line := range tail[lower+1:] {
		indent := indentOf(line)
		mode := row == 0 && indent == modeIndent
		notice := indent > modeIndent && utf8.RuneCountInString(line) == width-noticeMargin
		if line != "" && !mode && !notice {
			return false
		}
	}
	return true
}

type compactJob struct {
	runner shell.Runner
	now    func() time.Time
	sleep  func(time.Duration)
}

func (j compactJob) orca(ctx context.Context, args ...string) (string, bool) {
	result, err := j.runner.Run(ctx, shell.Command{Argv: append([]string{"orca", "terminal"}, args...)})
	return result.Stdout, err == nil
}

func (j compactJob) atIdlePrompt(ctx context.Context, handle string) (bool, error) {
	if _, ok := j.orca(ctx, "wait", "--terminal", handle, "--for", "tui-idle", "--timeout-ms", strconv.Itoa(waitTimeoutMS)); !ok {
		return false, nil
	}
	out, ok := j.orca(ctx, "read", "--terminal", handle, "--screen", "--json")
	if !ok {
		return false, nil
	}
	var read struct {
		Result struct {
			Terminal terminalScreen `json:"terminal"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(out), &read); err != nil {
		return false, err
	}
	return idlePrompt(read.Result.Terminal), nil
}

func (j compactJob) run(ctx context.Context, handle, text, transcript string) error {
	info, err := os.Stat(transcript)
	if err != nil {
		return err
	}
	cursor := newCompactionCursor(transcript, info.Size())
	for deadline := j.now().Add(compactDeadline); j.now().Before(deadline); j.sleep(pollInterval) {
		if done, err := cursor.compacted(); done || err != nil {
			return err
		}
		idle, err := j.atIdlePrompt(ctx, handle)
		if err != nil {
			return err
		}
		if !idle {
			continue
		}
		done, err := cursor.compacted()
		if err != nil {
			return err
		}
		if !done {
			j.orca(ctx, "send", "--terminal", handle, "--text", text, "--enter")
			return nil
		}
	}
	return nil
}
