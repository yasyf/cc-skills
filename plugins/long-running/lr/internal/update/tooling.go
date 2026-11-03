package update

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"slices"
	"strings"
)

const (
	toolingTitle = "Drive tooling"
	toolingList  = "meta/programs.md"
	noBlocker    = ": no blocker line"
	toolingHead  = "| Program | State | What blocks it | Next |"
)

var programColumns = []string{"Program", "State", "Blocker", "Next"}

type Program struct {
	Name    string
	State   string
	Blocker string
	Next    string
}

func ReadPrograms(path string) ([]Program, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var out []Program
	headed := false
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "|") {
			continue
		}
		cells := strings.Split(strings.Trim(line, "|"), "|")
		for i, text := range cells {
			cells[i] = strings.TrimSpace(text)
		}
		if len(cells) != len(programColumns) {
			return nil, fmt.Errorf("%s: %q has %d cells, want | %s |", path, line, len(cells), strings.Join(programColumns, " | "))
		}
		if !headed {
			if !slices.Equal(cells, programColumns) {
				return nil, fmt.Errorf("%s: header %q, want | %s |", path, line, strings.Join(programColumns, " | "))
			}
			headed = true
			continue
		}
		if strings.Trim(strings.Join(cells, ""), "-: ") == "" {
			continue
		}
		out = append(out, Program{cells[0], cells[1], cells[2], cells[3]})
	}
	return out, nil
}

func toolingLines(programs []Program) []string {
	if len(programs) == 0 {
		return nil
	}
	lines := []string{toolingHead, "|---|---|---|---|"}
	for _, program := range programs {
		lines = append(lines, fmt.Sprintf("| %s | %s | %s | %s |", cell(program.Name), cell(program.State), cell(program.Blocker), cell(program.Next)))
	}
	return lines
}

func ToolingLeft(text string, programs []Program) []string {
	if len(programs) == 0 {
		return []string{fmt.Sprintf("%s has no programs: meta-desk keeps one row per program in <state dir>/%s", toolingTitle, toolingList)}
	}
	lines := strings.Split(strings.ToLower(text), "\n")
	var left []string
	for _, program := range programs {
		name := strings.ToLower(cell(program.Name))
		naming := slices.DeleteFunc(slices.Clone(lines), func(line string) bool { return !namesWhole(line, name) })
		if len(naming) == 0 {
			left = append(left, program.Name)
			continue
		}
		if !slices.ContainsFunc(naming, func(line string) bool { return carriesBlocker(line, program.Blocker) }) {
			left = append(left, program.Name+noBlocker)
		}
	}
	return left
}

func carriesBlocker(line, blocker string) bool {
	blocker = strings.ToLower(cell(blocker))
	if blocker == "" {
		return false
	}
	words := map[string]bool{}
	for _, word := range subjectWords.FindAllString(line, -1) {
		words[word] = true
	}
	return strings.Contains(line, blocker) || coversSubject(words, blocker)
}
