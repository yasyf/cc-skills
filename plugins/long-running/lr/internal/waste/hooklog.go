package waste

import (
	"bytes"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"time"
)

const (
	logChunk = 1 << 20
	logStamp = "2006-01-02 15:04:05,000"
)

var (
	skipMarks = [][]byte{[]byte("hook skipped:"), []byte("abandoning this hook's verdict")}
	skipHook  = regexp.MustCompile(`'hook': '([^']+)'`)
)

func hookSkips(cfg Config, files []transcript, since time.Time) ([]Finding, error) {
	var findings []Finding
	read := map[string]bool{}
	for _, file := range files {
		session := file.session()
		if read[session] {
			continue
		}
		read[session] = true
		hooks, err := sessionSkips(filepath.Join(cfg.HookLogs, session+".log"), since)
		if err != nil {
			return nil, err
		}
		var size int64
		for _, count := range hooks {
			size += count
		}
		if size >= cfg.Floors.Skips {
			findings = append(findings, Finding{signalSkips, signalSkips + "/" + session, size, "skips", file.owner, fmt.Sprintf("session %s, %s", session[:min(8, len(session))], ranked(hooks, 2)), cfg.Floors.Skips})
		}
	}
	return findings, nil
}

func sessionSkips(path string, since time.Time) (map[string]int64, error) {
	log, err := os.Open(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer func() { _ = log.Close() }()
	info, err := log.Stat()
	if err != nil {
		return nil, err
	}
	hooks := map[string]int64{}
	var carry []byte
	end := info.Size()
	for end > 0 {
		start := max(end-logChunk, 0)
		chunk := make([]byte, end-start, end-start+int64(len(carry)))
		if _, err := log.ReadAt(chunk, start); err != nil {
			return nil, err
		}
		chunk = append(chunk, carry...)
		carry = nil
		end = start
		if start > 0 {
			cut := bytes.IndexByte(chunk, '\n')
			carry = slices.Clone(chunk[:cut+1])
			chunk = chunk[cut+1:]
		}
		if tallyNewestFirst(chunk, since, hooks) {
			break
		}
	}
	return hooks, nil
}

func tallyNewestFirst(chunk []byte, since time.Time, hooks map[string]int64) (reachedOlder bool) {
	for len(chunk) > 0 {
		chunk = bytes.TrimRight(chunk, "\n")
		cut := bytes.LastIndexByte(chunk, '\n')
		line := chunk[cut+1:]
		chunk = chunk[:max(cut, 0)]
		at, err := time.ParseInLocation(logStamp, string(line[:min(len(line), len(logStamp))]), time.Local)
		if err != nil {
			continue
		}
		if at.Before(since) {
			return true
		}
		if !slices.ContainsFunc(skipMarks, func(mark []byte) bool { return bytes.Contains(line, mark) }) {
			continue
		}
		if hook := skipHook.FindSubmatch(line); hook != nil {
			hooks[string(hook[1])]++
		}
	}
	return false
}
