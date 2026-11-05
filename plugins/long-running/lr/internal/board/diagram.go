package board

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/arch"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

const (
	diagramType = "lr.diagram"
	diagramDir  = "diagrams"
	diagramLead = "Diagram: "
)

func (l Link) pictured(ctx context.Context, block json.RawMessage) (json.RawMessage, error) {
	var card map[string]json.RawMessage
	var children []json.RawMessage
	if json.Unmarshal(block, &card) != nil || json.Unmarshal(card["children"], &children) != nil {
		return block, nil
	}
	drew := false
	for i, raw := range children {
		var child struct {
			ID   string          `json:"id"`
			Type string          `json:"type"`
			Doc  json.RawMessage `json:"doc"`
		}
		if json.Unmarshal(raw, &child) != nil || child.Type != diagramType {
			continue
		}
		if problems := arch.Problems(child.Doc); len(problems) > 0 {
			return nil, linkRefusal{"refused: diagram " + child.ID + " does not draw: " + strings.Join(problems, "; ")}
		}
		dashboard, err := drive.DashboardURL(l.State)
		if err != nil {
			return nil, linkRefusal{"refused: diagram " + child.ID + " needs the drive's dashboard to draw it, and its address did not read: " + err.Error()}
		}
		png := filepath.Join(l.State, diagramDir, child.ID+".png")
		if err := os.MkdirAll(filepath.Dir(png), 0o750); err != nil {
			return nil, err
		}
		if err := l.Draw(ctx, dashboard, child.Doc, png); err != nil {
			return nil, linkRefusal{"refused: diagram " + child.ID + " did not draw, so the card was not drawn; send it again: " + err.Error()}
		}
		if children[i], err = json.Marshal(map[string]string{"id": child.ID, "type": "image", "src": png, "alt": diagramLead + arch.Title(child.Doc)}); err != nil {
			return nil, err
		}
		drew = true
	}
	if !drew {
		return block, nil
	}
	var err error
	if card["children"], err = json.Marshal(children); err != nil {
		return nil, err
	}
	return json.Marshal(card)
}
