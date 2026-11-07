package reply

import (
	"cmp"
	"context"
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/arch"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

const (
	diagramKind = "diagram"
	diagramLead = "Diagram: "
)

var claimsPicture = regexp.MustCompile(`(?i)\bscreen ?(?:shots?|grabs?)\b|\b(?:attached|below|above|here(?:'s| is| are)|see)\s+(?:the\s+|a\s+|an\s+|this\s+|these\s+|\d+\s+)?(?:images?|pictures?|photos?)\b|\b(?:images?|pictures?|photos?)\s+(?:attached|below|above)\b`)

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
	var flags []string
	for _, a := range r.cardArtifacts() {
		if a.Image != "" {
			flags = append(flags, "--file", a.Image)
		}
	}
	return flags
}

func (r Reply) unshownPicture() error {
	imaged := slices.ContainsFunc(r.Artifacts, func(a Artifact) bool { return a.Image != "" })
	if imaged && len(r.Picks) > 0 {
		return errors.New("NO PICTURE: a pick card carries no image file, so nothing posted; send the image in its own reply with no picks")
	}
	said := strings.Join(slices.Concat([]string{r.Title, r.MD}, slices.Collect(maps.Values(r.Words))), "\n")
	claim := claimsPicture.FindString(said)
	if claim == "" || imaged || r.diagram.Image != "" {
		return nil
	}
	return fmt.Errorf("NO PICTURE: the reply says %q, but no image file goes with this post, so nothing posted; attach the image as an artifact of kind image, or take the words out", claim)
}
