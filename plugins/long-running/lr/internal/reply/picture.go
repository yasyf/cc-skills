package reply

import (
	"cmp"
	"context"
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

type cardArtifact struct {
	Title string `json:"title"`
	Kind  string `json:"kind"`
	URL   string `json:"url"`
	Image string `json:"image,omitempty"`
}

type picture struct {
	dir     string
	warning string
}

func (p picture) remove() {
	if p.dir != "" {
		_ = os.RemoveAll(p.dir)
	}
}

func (p picture) told(o Outcome) Outcome {
	if p.warning != "" {
		o.Warning = strings.TrimSpace(o.Warning + " " + p.warning)
	}
	return o
}

func (f Fanout) diagramPage(r Reply) (string, string) {
	if len(r.Diagram) == 0 {
		return "", ""
	}
	dashboard, err := drive.DashboardURL(f.Entry.StateDir)
	if err != nil {
		return "", ""
	}
	return dashboard, dashboard + diagramKind + "/" + r.ID
}

func (r Reply) diagramTitle() string {
	return shortened(cmp.Or(arch.Title(r.Diagram), r.Title), maxTitle)
}

func (r Reply) pictureFlags() []string {
	var flags []string
	for _, a := range r.artifacts {
		if a.Image != "" {
			flags = append(flags, "--file", a.Image)
		}
	}
	return flags
}

func (f Fanout) pictured(ctx context.Context, r Reply) (Reply, picture) {
	dashboard, link := f.diagramPage(r)
	if link == "" {
		return r, picture{}
	}
	shown := cardArtifact{Title: r.diagramTitle(), Kind: diagramKind, URL: link}
	r.artifacts = []cardArtifact{shown}
	dir, err := os.MkdirTemp("", "lr-diagram-")
	if err != nil {
		return r, picture{warning: "the diagram's picture was not drawn, so its Slack card has none: " + err.Error()}
	}
	png := filepath.Join(dir, r.ID+".png")
	if err := f.Shoot(ctx, dashboard, r.Diagram, png); err != nil {
		return r, picture{dir: dir, warning: "the diagram's picture was not drawn, so its Slack card has none: " + err.Error()}
	}
	shown.Image = png
	r.artifacts = []cardArtifact{shown}
	return r, picture{dir: dir}
}
