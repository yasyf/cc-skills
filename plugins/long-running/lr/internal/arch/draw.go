package arch

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/chromedp/chromedp"
)

const (
	asset      = "arch-kit/arch-doc.js"
	validator  = "arch-kit/vendor/arch-map.js"
	drawScript = `(async () => {
		document.documentElement.innerHTML = "<head><style>html,body{margin:0;background:#fff}arch-doc{display:block}.arch-key,.smap-readout{display:none!important}</style></head><body></body>";
		document.documentElement.setAttribute("data-print", "");
		const src = "data:application/json;base64,%s";
		const problems = (await import("/` + validator + `")).validate(await (await fetch(src)).json());
		if (problems.length) return problems;
		await import("/` + asset + `");
		const doc = document.createElement("arch-doc");
		const drawn = new Promise((done) => doc.addEventListener("arch-ready", done, { once: true }));
		doc.setAttribute("src", src);
		document.body.append(doc);
		await drawn;
		return [];
	})()`
	width   = 1400
	height  = 900
	scale   = 2
	timeout = 45 * time.Second
)

func Draw(ctx context.Context, dashboard string, diagram json.RawMessage, png string) error {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	ctx, cancel = chromedp.NewExecAllocator(ctx, append(chromedp.DefaultExecAllocatorOptions[:], chromedp.WindowSize(width, height))...)
	defer cancel()
	ctx, cancel = chromedp.NewContext(ctx)
	defer cancel()
	err := chromedp.Do(ctx,
		chromedp.EmulateViewport(width, height, chromedp.EmulateScale(scale)),
		chromedp.Navigate(dashboard+asset),
	)
	var problems []string
	if err == nil {
		problems, err = chromedp.Run(ctx, chromedp.Evaluate[[]string](fmt.Sprintf(drawScript, base64.StdEncoding.EncodeToString(diagram)), chromedp.EvalAwaitPromise))
	}
	if err == nil && len(problems) > 0 {
		return fmt.Errorf("the diagram does not draw: %s", strings.Join(problems, "; "))
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
