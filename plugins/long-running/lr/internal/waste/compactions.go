package waste

import (
	"bufio"
	"bytes"
	"cmp"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

const (
	codexDayLayout     = "2006/01/02"
	codexStartLookback = 48 * time.Hour
	codexLinePrefix    = 4096
	compactedReach     = 200
	stampOpen          = `{"timestamp":"`
	rolloutSuffix      = ".jsonl"
	sessionIDLength    = 36
)

var compactedMark = []byte(`"type":"compacted"`)

func codexCompactions(cfg Config, peers []client.Peer, since, now time.Time) ([]Finding, error) {
	laneOf := make(map[string]string, len(peers))
	for _, peer := range peers {
		if peer.Agent == "" {
			laneOf[peer.Session] = peer.Lane
		}
	}
	counts := map[string]int64{}
	var total int64
	var active time.Duration
	local := since.Add(-codexStartLookback).In(time.Local)
	for day := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, time.Local); !day.After(now); day = day.AddDate(0, 0, 1) {
		dir := filepath.Join(cfg.CodexSessions, day.Format(codexDayLayout))
		entries, err := os.ReadDir(dir)
		if errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err != nil {
			return nil, err
		}
		for _, entry := range entries {
			name, isRollout := strings.CutSuffix(entry.Name(), rolloutSuffix)
			if !isRollout {
				continue
			}
			lane := laneOf[name[max(len(name)-sessionIDLength, 0):]]
			if lane == "" {
				continue
			}
			info, err := entry.Info()
			if err != nil {
				return nil, err
			}
			if info.ModTime().Before(since) {
				continue
			}
			began, compactions, err := scanRollout(filepath.Join(dir, entry.Name()), since)
			if err != nil {
				return nil, err
			}
			counts[lane] += compactions
			total += compactions
			active += overlap(began, info.ModTime(), since, now)
		}
	}
	if active <= 0 {
		return nil, nil
	}
	size := int64(float64(total) / active.Hours())
	if size < cfg.Floors.Compactions {
		return nil, nil
	}
	worst := slices.MaxFunc(slices.Sorted(maps.Keys(counts)), func(a, b string) int { return cmp.Compare(counts[a], counts[b]) })
	detail := fmt.Sprintf("%d compactions in %.1f lane hours: %s", total, active.Hours(), ranked(counts, 3))
	return []Finding{{signalCompactions, signalCompactions, size, "compactions/h", worst, detail, cfg.Floors.Compactions}}, nil
}

func scanRollout(path string, since time.Time) (began time.Time, compactions int64, err error) {
	file, err := os.Open(path)
	if err != nil {
		return time.Time{}, 0, err
	}
	defer func() { _ = file.Close() }()
	reader := bufio.NewReaderSize(file, codexLinePrefix)
	for {
		line, err := reader.ReadSlice('\n')
		if len(line) > 0 {
			compacted := bytes.Contains(line[:min(len(line), compactedReach)], compactedMark)
			if at, ok := stampOf(line); ok && (compacted || began.IsZero()) {
				if began.IsZero() {
					began = at
				}
				if compacted && !at.Before(since) {
					compactions++
				}
			}
		}
		for errors.Is(err, bufio.ErrBufferFull) {
			_, err = reader.ReadSlice('\n')
		}
		if errors.Is(err, io.EOF) {
			return began, compactions, nil
		}
		if err != nil {
			return time.Time{}, 0, err
		}
	}
}

func overlap(from, to, since, now time.Time) time.Duration {
	if from.Before(since) {
		from = since
	}
	if to.After(now) {
		to = now
	}
	return max(to.Sub(from), 0)
}

func stampOf(line []byte) (time.Time, bool) {
	rest, ok := bytes.CutPrefix(line, []byte(stampOpen))
	end := bytes.IndexByte(rest, '"')
	if !ok || end < 0 {
		return time.Time{}, false
	}
	at, err := time.Parse(time.RFC3339Nano, string(rest[:end]))
	return at, err == nil
}
