package watch

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	metaLane    = "meta"
	metaKind    = "meta"
	dueClock    = "3:04 PM"
	doneSection = "done"
)

var (
	itemsFile = filepath.Join(metaLane, "items.md")
	divider   = regexp.MustCompile(`^:?-+:?$`)
	heading   = regexp.MustCompile(`^#+\s+(.*)$`)
)

type metaRow struct {
	item, state, next, due string
}

func ReadMeta(stateDir string) (string, time.Time, error) {
	path := filepath.Join(stateDir, itemsFile)
	info, err := os.Stat(path)
	if errors.Is(err, fs.ErrNotExist) {
		return "", time.Time{}, nil
	}
	if err != nil {
		return "", time.Time{}, err
	}
	data, err := os.ReadFile(path)
	return string(data), info.ModTime(), err
}

func Meta(s *store.Store, items string, written, now time.Time, post pass.Post) ([]pass.Escalated, error) {
	to := []string{lanes.OwnerRelay, metaLane}
	var escalated []pass.Escalated
	for _, row := range openMetaRows(items) {
		subject := metaKind + ":" + row.item
		due, dated := dueAt(row.due, written)
		cause := fmt.Sprintf("meta: %s, state %s, has no Due the watch reads (cell %q); next: %s; give the row a Due", row.item, orNone(row.state), row.due, orNone(row.next))
		late := ""
		if dated {
			if now.Before(due) {
				continue
			}
			cause = fmt.Sprintf("meta: %s, state %s, is past its Due; next: %s", row.item, orNone(row.state), orNone(row.next))
			late = fmt.Sprintf("; %d minutes late", int(now.Sub(due).Minutes()))
		}
		raised, err := s.Escalate(subject, cause+" "+row.due, lanes.OwnerRelay, now)
		if err != nil {
			return escalated, err
		}
		if !raised {
			continue
		}
		escalated = append(escalated, pass.Escalated{Subject: subject, Cause: cause + late, To: strings.Join(to, ",")})
		if err := post(to, bus.Escalation{Subject: subject, Cause: cause + late}); err != nil {
			return escalated, err
		}
	}
	return escalated, nil
}

func dueAt(cell string, written time.Time) (time.Time, bool) {
	day := written.In(pacific)
	if clock, err := time.ParseInLocation(dueClock, cell, pacific); err == nil {
		return time.Date(day.Year(), day.Month(), day.Day(), clock.Hour(), clock.Minute(), 0, 0, pacific), true
	}
	if dated, err := time.ParseInLocation(clockAndDay, cell, pacific); err == nil {
		return time.Date(day.Year(), dated.Month(), dated.Day(), dated.Hour(), dated.Minute(), 0, 0, pacific), true
	}
	return time.Time{}, false
}

func openMetaRows(items string) []metaRow {
	var rows []metaRow
	var columns map[string]int
	done := false
	for _, line := range strings.Split(items, "\n") {
		line = strings.TrimSpace(line)
		if found := heading.FindStringSubmatch(line); found != nil {
			done, columns = strings.EqualFold(strings.TrimSpace(found[1]), doneSection), nil
			continue
		}
		if !strings.HasPrefix(line, "|") {
			columns = nil
			continue
		}
		cells := tableCells(line)
		if columns == nil {
			columns = map[string]int{}
			for i, name := range cells {
				columns[strings.ToLower(name)] = i
			}
			continue
		}
		if done || divider.MatchString(cells[0]) {
			continue
		}
		cell := func(name string) string {
			if i, ok := columns[name]; ok && i < len(cells) {
				return cells[i]
			}
			return ""
		}
		rows = append(rows, metaRow{item: cell("item"), state: cell("state"), next: cell("blocks or next"), due: cell("due")})
	}
	return rows
}

func tableCells(line string) []string {
	line = strings.TrimSuffix(strings.TrimPrefix(line, "|"), "|")
	var cells []string
	var cell strings.Builder
	for i := 0; i < len(line); i++ {
		switch {
		case line[i] == '\\' && i+1 < len(line) && line[i+1] == '|':
			cell.WriteByte('|')
			i++
		case line[i] == '|':
			cells = append(cells, strings.TrimSpace(cell.String()))
			cell.Reset()
		default:
			cell.WriteByte(line[i])
		}
	}
	return append(cells, strings.TrimSpace(cell.String()))
}
