package owner

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
)

const (
	AtRoot      = "root"
	handWritten = "Slack "
)

var (
	dmThreadLink = regexp.MustCompile(`https://\S+/archives/D\w+/p\d+\?(?:[^\s|>]*&)?thread_ts=(\d+\.\d+)[^\s|>]*`)
	handThread   = regexp.MustCompile(`^Slack thread (\d+\.\d+)\b`)
)

func (w wordRows) names(row string) bool {
	return slices.ContainsFunc(w.Rows, func(listed string) bool { return sameRow(listed, row) })
}

func sameRow(listed, row string) bool {
	return listed == row || strings.HasPrefix(listed, row+".")
}

func PlaceOf(stateDir, row string) (string, error) {
	written, err := allWordRows(Ledger(stateDir))
	if err != nil {
		return "", err
	}
	for _, word := range slices.Backward(written) {
		if word.names(row) {
			return word.Thread, nil
		}
	}
	ledger, err := os.ReadFile(Ledger(stateDir))
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return "", err
	}
	rows := Parse(ledger)
	at := slices.IndexFunc(rows, func(r Row) bool { return sameRow(r.ID, row) })
	if at < 0 {
		return "", nil
	}
	return placeInWords(strings.TrimSpace(rows[at].Text)), nil
}

func placeInWords(text string) string {
	if strings.HasPrefix(text, boardTap) {
		return AtRoot
	}
	var named []string
	for _, found := range dmThreadLink.FindAllStringSubmatch(text, -1) {
		named = append(named, found[1])
	}
	if found := handThread.FindStringSubmatch(text); found != nil {
		named = append(named, found[1])
	}
	slices.Sort(named)
	named = slices.Compact(named)
	hand := strings.HasPrefix(text, handWritten)
	switch {
	case len(named) == 1 && (hand || dmThreadLink.FindString(text) == text):
		return named[0]
	case len(named) > 0 || hand:
		return ""
	}
	return AtRoot
}

func RowIn(stateDir, thread string) (string, error) {
	written, err := allWordRows(Ledger(stateDir))
	if err != nil {
		return "", err
	}
	for _, word := range slices.Backward(written) {
		if word.Thread == thread && len(word.Rows) > 0 {
			return word.Rows[len(word.Rows)-1], nil
		}
	}
	return "", nil
}

func allWordRows(path string) ([]wordRows, error) {
	file, err := os.Open(filepath.Join(filepath.Dir(path), wordsFile))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer file.Close()
	var written []wordRows
	lines := bufio.NewScanner(file)
	for lines.Scan() {
		var word wordRows
		if err := json.Unmarshal(lines.Bytes(), &word); err != nil {
			return nil, fmt.Errorf("%s: %w", wordsFile, err)
		}
		written = append(written, word)
	}
	return written, lines.Err()
}
