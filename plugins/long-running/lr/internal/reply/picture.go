package reply

import (
	"cmp"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/chromedp/chromedp"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

const (
	diagramKind  = "diagram"
	diagramLead  = "Diagram: "
	diagramAsset = "arch-kit/arch-doc.js"
	diagramDraw  = `(async () => {
		document.documentElement.innerHTML = "<head><style>html,body{margin:0;background:#fff}arch-doc{display:block}</style></head><body></body>";
		await import("/` + diagramAsset + `");
		const doc = document.createElement("arch-doc");
		const drawn = new Promise((done) => doc.addEventListener("arch-ready", done, { once: true }));
		doc.setAttribute("src", "data:application/json;base64,%s");
		document.body.append(doc);
		await drawn;
		return true;
	})()`
	pictureWidth   = 1400
	pictureHeight  = 900
	pictureScale   = 2
	pictureTimeout = 45 * time.Second
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
	var doc struct {
		Title string `json:"title"`
	}
	_ = json.Unmarshal(r.Diagram, &doc)
	return shortened(cmp.Or(doc.Title, r.Title), maxTitle)
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

func shootDiagram(ctx context.Context, dashboard string, diagram json.RawMessage, png string) error {
	ctx, cancel := context.WithTimeout(ctx, pictureTimeout)
	defer cancel()
	ctx, cancel = chromedp.NewExecAllocator(ctx, append(chromedp.DefaultExecAllocatorOptions[:], chromedp.WindowSize(pictureWidth, pictureHeight))...)
	defer cancel()
	ctx, cancel = chromedp.NewContext(ctx)
	defer cancel()
	err := chromedp.Do(ctx,
		chromedp.EmulateViewport(pictureWidth, pictureHeight, chromedp.EmulateScale(pictureScale)),
		chromedp.Navigate(dashboard+diagramAsset),
	)
	if err == nil {
		_, err = chromedp.Run(ctx, chromedp.Evaluate[bool](fmt.Sprintf(diagramDraw, base64.StdEncoding.EncodeToString(diagram)), chromedp.EvalAwaitPromise))
	}
	var shot []byte
	if err == nil {
		shot, err = chromedp.Run(ctx, chromedp.Screenshot(chromedp.CSS("arch-doc")))
	}
	if err != nil {
		return fmt.Errorf("drawing the diagram in Chrome from %s: %w", dashboard, err)
	}
	return os.WriteFile(png, shot, 0o600)
}
