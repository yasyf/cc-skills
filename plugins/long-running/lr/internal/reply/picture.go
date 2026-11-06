package reply

import (
	"cmp"
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/arch"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

const (
	diagramKind = "diagram"
	diagramLead = "Diagram: "
)

func (f Fanout) pictured(ctx context.Context, r Reply) (Reply, error) {
	if len(r.Diagram) == 0 {
		return r, nil
	}
	if problems := arch.Problems(r.Diagram); len(problems) > 0 {
		return r, fmt.Errorf("the diagram does not draw, so nothing posted; fix it and send again: %s", strings.Join(problems, "; "))
	}
	dashboard, err := drive.DashboardURL(f.Entry.StateDir)
	if err != nil {
		return r, fmt.Errorf("the diagram needs the drive's dashboard to draw it, and its address did not read, so nothing posted: %w", err)
	}
	named, err := arch.DashboardLink(ctx, f.Runner, f.Entry.StateDir)
	if err != nil {
		return r, fmt.Errorf("the diagram needs the dashboard's https address for its link, and it did not read, so nothing posted: %w", err)
	}
	png := arch.Picture(f.Entry.StateDir, r.ID)
	if err := os.MkdirAll(filepath.Dir(png), 0o750); err != nil {
		return r, err
	}
	if err := f.Shoot(ctx, dashboard, r.Diagram, png); err != nil {
		return r, fmt.Errorf("the diagram's picture did not draw, so nothing posted; fix it and send again: %w", err)
	}
	r.diagram = Artifact{Title: shortened(cmp.Or(arch.Title(r.Diagram), r.Title), maxTitle), Kind: diagramKind, URL: named + diagramKind + "/" + r.ID, Image: png}
	return r, nil
}

func (r Reply) cardArtifacts() []Artifact {
	if r.diagram.URL == "" {
		return r.Artifacts
	}
	return append([]Artifact{r.diagram}, r.Artifacts...)
}

func (r Reply) pictureFlags() []string {
	if r.diagram.Image == "" {
		return nil
	}
	return []string{"--file", r.diagram.Image}
}
