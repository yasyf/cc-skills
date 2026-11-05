package arch

import (
	"encoding/json"
	"fmt"
	"slices"
)

type node struct {
	ID     string `json:"id"`
	Kind   string `json:"kind"`
	Name   string `json:"name"`
	Role   string `json:"role"`
	Medium string `json:"medium"`
	Nodes  []node `json:"nodes"`
}

type link struct {
	From string `json:"from"`
	To   string `json:"to"`
}

type steps struct {
	Steps []link `json:"steps"`
}

type doc struct {
	Arch    int     `json:"arch"`
	Name    string  `json:"name"`
	Title   string  `json:"title"`
	Nodes   []node  `json:"nodes"`
	Edges   []link  `json:"edges"`
	Path    steps   `json:"path"`
	Stories []steps `json:"stories"`
	Notes   []struct {
		On string `json:"on"`
	} `json:"notes"`
}

func Title(raw []byte) string {
	var drawn doc
	_ = json.Unmarshal(raw, &drawn)
	return drawn.Title
}

func Problems(raw []byte) []string {
	var drawn doc
	if err := json.Unmarshal(raw, &drawn); err != nil {
		return []string{"it is not JSON: " + err.Error()}
	}
	var problems []string
	if drawn.Arch != 1 {
		problems = append(problems, `"arch" must be 1`)
	}
	if drawn.Name == "" || drawn.Title == "" {
		problems = append(problems, `"name" and "title" are required`)
	}
	if len(drawn.Nodes) == 0 {
		return append(problems, `"nodes" needs at least one node`)
	}
	ids, nodeProblems := walkNodes(drawn.Nodes)
	problems = append(problems, nodeProblems...)
	links := append(slices.Clone(drawn.Edges), drawn.Path.Steps...)
	for _, story := range drawn.Stories {
		links = append(links, story.Steps...)
	}
	for _, line := range links {
		for _, id := range []string{line.From, line.To} {
			if !slices.Contains(ids, id) {
				problems = append(problems, fmt.Sprintf("a line names %q, which is not a node", id))
			}
		}
	}
	for _, note := range drawn.Notes {
		if note.On != "" && !slices.Contains(ids, note.On) {
			problems = append(problems, fmt.Sprintf("a note sits on %q, which is not a node", note.On))
		}
	}
	return problems
}

func walkNodes(nodes []node) ([]string, []string) {
	var ids, problems []string
	for _, n := range nodes {
		ids = append(ids, n.ID)
		if n.ID == "" || n.Kind == "" || n.Name == "" || n.Role == "" {
			problems = append(problems, fmt.Sprintf(`node %q needs "id", "kind", "name" and "role"`, n.ID))
		}
		if (n.Kind == "store" || n.Kind == "log") && n.Medium == "" {
			problems = append(problems, fmt.Sprintf(`node %q is a %s and needs "medium"`, n.ID, n.Kind))
		}
		childIDs, childProblems := walkNodes(n.Nodes)
		ids = append(ids, childIDs...)
		problems = append(problems, childProblems...)
	}
	return ids, problems
}
