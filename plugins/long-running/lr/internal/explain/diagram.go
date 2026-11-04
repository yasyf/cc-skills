package explain

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/yuin/goldmark/ast"
	"github.com/yuin/goldmark/text"
)

const (
	archLanguage    = "arch"
	mermaidLanguage = "mermaid"
	mermaidRefusal  = "lr explain refuses this page: a mermaid block is not drawn; replace it with a fenced arch block holding an architecture doc (arch/v1), whose format is architecture/kit/vendor/arch-map.schema.json in the design-docs checkout"
)

type archNode struct {
	ID     string     `json:"id"`
	Kind   string     `json:"kind"`
	Name   string     `json:"name"`
	Role   string     `json:"role"`
	Medium string     `json:"medium"`
	Nodes  []archNode `json:"nodes"`
}

type archLink struct {
	From string `json:"from"`
	To   string `json:"to"`
}

type archSteps struct {
	Steps []archLink `json:"steps"`
}

type archDoc struct {
	Arch    int         `json:"arch"`
	Name    string      `json:"name"`
	Title   string      `json:"title"`
	Nodes   []archNode  `json:"nodes"`
	Edges   []archLink  `json:"edges"`
	Path    archSteps   `json:"path"`
	Stories []archSteps `json:"stories"`
	Notes   []struct {
		On string `json:"on"`
	} `json:"notes"`
}

func refuseDiagrams(source []byte) error {
	var refusal error
	err := ast.Walk(markdown.Parser().Parse(text.NewReader(source)), func(n ast.Node, entering bool) (ast.WalkStatus, error) {
		block, ok := n.(*ast.FencedCodeBlock)
		if !ok || !entering {
			return ast.WalkContinue, nil
		}
		switch string(block.Language(source)) {
		case mermaidLanguage:
			refusal = errors.New(mermaidRefusal)
			return ast.WalkStop, nil
		case archLanguage:
			if problems := archProblems(fence(block, source)); len(problems) > 0 {
				refusal = fmt.Errorf("lr explain refuses this page: its arch block does not draw: %s", strings.Join(problems, "; "))
				return ast.WalkStop, nil
			}
		}
		return ast.WalkContinue, nil
	})
	return errors.Join(err, refusal)
}

func fence(block *ast.FencedCodeBlock, source []byte) []byte {
	var out bytes.Buffer
	for i := range block.Lines().Len() {
		line := block.Lines().At(i)
		out.Write(line.Value(source))
	}
	return out.Bytes()
}

func archProblems(raw []byte) []string {
	var doc archDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		return []string{"it is not JSON: " + err.Error()}
	}
	var problems []string
	if doc.Arch != 1 {
		problems = append(problems, `"arch" must be 1`)
	}
	if doc.Name == "" || doc.Title == "" {
		problems = append(problems, `"name" and "title" are required`)
	}
	if len(doc.Nodes) == 0 {
		return append(problems, `"nodes" needs at least one node`)
	}
	ids, nodeProblems := walkNodes(doc.Nodes)
	problems = append(problems, nodeProblems...)
	links := append(slices.Clone(doc.Edges), doc.Path.Steps...)
	for _, story := range doc.Stories {
		links = append(links, story.Steps...)
	}
	for _, link := range links {
		for _, id := range []string{link.From, link.To} {
			if !slices.Contains(ids, id) {
				problems = append(problems, fmt.Sprintf("a line names %q, which is not a node", id))
			}
		}
	}
	for _, note := range doc.Notes {
		if note.On != "" && !slices.Contains(ids, note.On) {
			problems = append(problems, fmt.Sprintf("a note sits on %q, which is not a node", note.On))
		}
	}
	return problems
}

func walkNodes(nodes []archNode) ([]string, []string) {
	var ids, problems []string
	for _, node := range nodes {
		ids = append(ids, node.ID)
		if node.ID == "" || node.Kind == "" || node.Name == "" || node.Role == "" {
			problems = append(problems, fmt.Sprintf(`node %q needs "id", "kind", "name" and "role"`, node.ID))
		}
		if (node.Kind == "store" || node.Kind == "log") && node.Medium == "" {
			problems = append(problems, fmt.Sprintf(`node %q is a %s and needs "medium"`, node.ID, node.Kind))
		}
		childIDs, childProblems := walkNodes(node.Nodes)
		ids = append(ids, childIDs...)
		problems = append(problems, childProblems...)
	}
	return ids, problems
}
