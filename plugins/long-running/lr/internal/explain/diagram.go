package explain

import (
	"bytes"
	"errors"
	"fmt"
	"strings"

	"github.com/yuin/goldmark/ast"
	"github.com/yuin/goldmark/text"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/arch"
)

const (
	archLanguage    = "arch"
	mermaidLanguage = "mermaid"
	mermaidRefusal  = "lr explain refuses this page: a mermaid block is not drawn; replace it with a fenced arch block holding an architecture doc (arch/v1), whose format is architecture/kit/vendor/arch-map.schema.json in the design-docs checkout"
)

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
			if problems := arch.Problems(fence(block, source)); len(problems) > 0 {
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
