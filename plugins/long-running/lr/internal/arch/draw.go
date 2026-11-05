package arch

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/chromedp/chromedp"
)

const (
	asset      = "arch-kit/arch-doc.js"
	drawScript = `(async () => {
		document.documentElement.innerHTML = "<head><style>html,body{margin:0;background:#fff}arch-doc{display:block}</style></head><body></body>";
		await import("/` + asset + `");
		const doc = document.createElement("arch-doc");
		const drawn = new Promise((done) => doc.addEventListener("arch-ready", done, { once: true }));
		doc.setAttribute("src", "data:application/json;base64,%s");
		document.body.append(doc);
		await drawn;
		return true;
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
	if err == nil {
		_, err = chromedp.Run(ctx, chromedp.Evaluate[bool](fmt.Sprintf(drawScript, base64.StdEncoding.EncodeToString(diagram)), chromedp.EvalAwaitPromise))
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
