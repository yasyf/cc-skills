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
	diagramType = "lr.arch"
	diagramDir  = "diagrams"
)

func (l Link) pictured(ctx context.Context, block json.RawMessage) (json.RawMessage, error) {
	var card map[string]json.RawMessage
	var children []json.RawMessage
	if json.Unmarshal(block, &card) != nil || json.Unmarshal(card["children"], &children) != nil {
		return block, nil
	}
	drew := false
	var top struct {
		ID string `json:"id"`
	}
	_ = json.Unmarshal(block, &top)
	for i, raw := range children {
		var child map[string]json.RawMessage
		var id, kind string
		if json.Unmarshal(raw, &child) != nil || json.Unmarshal(child["type"], &kind) != nil || kind != diagramType {
			continue
		}
		_ = json.Unmarshal(child["id"], &id)
		doc := child["doc"]
		if problems := arch.Problems(doc); len(problems) > 0 {
			return nil, linkRefusal{"refused: diagram " + id + " does not draw: " + strings.Join(problems, "; ")}
		}
		dashboard, err := drive.DashboardURL(l.State)
		if err != nil {
			return nil, linkRefusal{"refused: diagram " + id + " needs the drive's dashboard to draw it, and its address did not read: " + err.Error()}
		}
		png := filepath.Join(l.State, diagramDir, id+".png")
		if err := os.MkdirAll(filepath.Dir(png), 0o750); err != nil {
			return nil, err
		}
		if err := l.Draw(ctx, dashboard, doc, png); err != nil {
			return nil, linkRefusal{"refused: diagram " + id + " did not draw, so the card was not drawn; send it again: " + err.Error()}
		}
		for field, value := range map[string]string{"picture": png, "link": dashboard + "#/question/" + top.ID, "title": arch.Title(doc)} {
			if child[field], err = json.Marshal(value); err != nil {
				return nil, err
			}
		}
		if children[i], err = json.Marshal(child); err != nil {
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
