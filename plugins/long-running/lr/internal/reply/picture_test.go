package reply

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/arch"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	testDashboard   = "http://mac.tail1.ts.net:61210/"
	testDiagramPage = "https://mac.tail1.ts.net:61211/diagram/d1"
	testPNG         = "\x89PNG\r\n\x1a\n"
	s3Diagram       = `{"arch":1,"name":"share","title":"Opening a shared chat","nodes":[{"id":"page","kind":"app","name":"Page","role":"asks"},{"id":"copy","kind":"store","name":"Copy","role":"holds the chat","medium":"s3"}],"edges":[{"from":"page","to":"copy"}]}`
)

type readingRunner struct {
	shell.Runner
	read func(shell.Command)
}

func (r readingRunner) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	r.read(c)
	return r.Runner.Run(ctx, c)
}

type shot struct {
	dashboard string
	diagram   string
}

func onDashboard(t *testing.T, f Fanout, failed error) (Fanout, *[]shot) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(f.Entry.StateDir, "dashboard.json"), []byte(`{"host":"mac.tail1.ts.net","port":61210,"tls_port":61211}`), 0o600); err != nil {
		t.Fatal(err)
	}
	var shots []shot
	f.Shoot = func(_ context.Context, dashboard string, diagram json.RawMessage, png string) error {
		shots = append(shots, shot{dashboard: dashboard, diagram: string(diagram)})
		if failed != nil {
			return failed
		}
		return os.WriteFile(png, []byte(testPNG), 0o600)
	}
	return f, &shots
}

func sendPictured(t *testing.T, f Fanout, r Reply) []Outcome {
	t.Helper()
	r, err := f.pictured(context.Background(), r)
	if err != nil {
		t.Fatal(err)
	}
	return f.Send(context.Background(), r)
}

func postedCard(t *testing.T, argv []string) struct {
	Body      string     `json:"body"`
	Artifacts []Artifact `json:"artifacts"`
} {
	t.Helper()
	var card struct {
		Body      string     `json:"body"`
		Artifacts []Artifact `json:"artifacts"`
	}
	if err := json.Unmarshal([]byte(flag(t, argv, "--card")), &card); err != nil {
		t.Fatal(err)
	}
	return card
}

func TestADiagramPostsToSlackAsItsPictureAndEverySurfaceLinksItsDashboardPage(t *testing.T) {
	fake := scripted()
	f, shots := onDashboard(t, fanout(t, fake), nil)
	var images []string
	f.Runner = readingRunner{fake, func(c shell.Command) {
		if c.Argv[0] != "cc-slack" || c.Argv[1] != "dm-status" {
			return
		}
		for _, a := range postedCard(t, c.Argv).Artifacts {
			if body, err := os.ReadFile(a.Image); err == nil && string(body) == testPNG {
				images = append(images, a.Image)
			}
		}
	}}
	sent := sendPictured(t, f, Reply{ID: "d1", Title: "How the relay runs", MD: "The relay in one picture.", Status: info, Diagram: json.RawMessage(drawnDiagram)})

	if len(*shots) != 1 || (*shots)[0].dashboard != testDashboard || (*shots)[0].diagram != drawnDiagram {
		t.Fatalf("shots = %+v, want the one diagram drawn from the dashboard", *shots)
	}
	posts := calls(fake, "cc-slack")
	if len(posts) != 1 {
		t.Fatalf("cc-slack calls = %v", posts)
	}
	card := postedCard(t, posts[0].Argv)
	if len(card.Artifacts) != 1 {
		t.Fatalf("artifacts = %+v, want the diagram", card.Artifacts)
	}
	got := card.Artifacts[0]
	if got.Title != "The relay" || got.Kind != diagramKind || got.URL != testDiagramPage || len(images) != 1 || images[0] != got.Image {
		t.Fatalf("artifact = %+v, images read while posting %v", got, images)
	}
	link := "[" + diagramLead + "The relay](" + testDiagramPage + ")"
	if !strings.Contains(card.Body, link) {
		t.Fatalf("Slack body %q lacks %s", card.Body, link)
	}
	for _, write := range threadWrites(fake) {
		if !strings.Contains(strings.Join(write.Argv, " ")+write.Stdin, testDiagramPage) {
			t.Fatalf("the terminal thread write %q lacks the dashboard link", write.Argv)
		}
	}
	if slack := surface(sent, "slack"); slack.Error != "" || slack.Warning != "" {
		t.Fatalf("slack outcome = %+v", slack)
	}
	stored, _ := stored(t, f, "d1")
	var kept bytes.Buffer
	if err := json.Compact(&kept, stored.Diagram); err != nil || kept.String() != drawnDiagram || stored.MD != "The relay in one picture." {
		t.Fatalf("Chat stored diagram %s (%v) and words %q, want the diagram and the words unchanged", stored.Diagram, err, stored.MD)
	}
}

func TestADiagramOnAPickCardPostsAsThatCardsPicture(t *testing.T) {
	fake := scripted()
	f, _ := onDashboard(t, fanout(t, fake), nil)
	sendPictured(t, f, Reply{ID: "d1", Title: "Which fix?", MD: "Two fixes.\n\nBoth shown in the diagram.", Picks: []string{"Fix a", "Fix b"}, Recommended: "Fix a", Diagram: json.RawMessage(drawnDiagram)})

	posts := calls(fake, "cc-present")
	if len(posts) != 1 || posts[0].Argv[1] != "update-block" {
		t.Fatalf("cc-present calls = %v, want the one card", posts)
	}
	var card struct {
		Children []struct {
			Type    string          `json:"type"`
			MD      string          `json:"md"`
			Doc     json.RawMessage `json:"doc"`
			Picture string          `json:"picture"`
			Link    string          `json:"link"`
			Title   string          `json:"title"`
		} `json:"children"`
	}
	if err := json.Unmarshal([]byte(posts[0].Stdin), &card); err != nil {
		t.Fatal(err)
	}
	types := make([]string, len(card.Children))
	for n, child := range card.Children {
		types[n] = child.Type
	}
	if !slices.Equal(types, []string{"markdown", arch.Block, "choice"}) {
		t.Fatalf("children = %v, want the words, then the diagram, then the picks", types)
	}
	diagram := card.Children[1]
	var doc bytes.Buffer
	if err := json.Compact(&doc, diagram.Doc); err != nil || doc.String() != drawnDiagram || diagram.Link != testDiagramPage || diagram.Title != "The relay" {
		t.Fatalf("diagram child = %+v, want the doc, the https dashboard page and its title", diagram)
	}
	if body, err := os.ReadFile(diagram.Picture); err != nil || string(body) != testPNG || diagram.Picture != arch.Picture(f.Entry.StateDir, "d1") {
		t.Fatalf("picture %s read %q (%v), want the drawn PNG kept in the drive's diagrams for the card's later redraws", diagram.Picture, body, err)
	}
	if !strings.Contains(card.Children[0].MD, "["+diagramLead+"The relay]("+testDiagramPage+")") {
		t.Fatalf("card words %q lack the diagram's link", card.Children[0].MD)
	}
}

func TestADiagramLinksItsHTTPSDashboardWhileChromeDrawsFromTheDashboardAddress(t *testing.T) {
	fake := scripted()
	f, shots := onDashboard(t, fanout(t, fake), nil)
	if err := os.WriteFile(filepath.Join(f.Entry.StateDir, "dashboard.json"), []byte(`{"host":"100.64.0.1","port":61210,"tls_port":61211}`), 0o600); err != nil {
		t.Fatal(err)
	}
	sendPictured(t, f, Reply{ID: "d1", Title: "How the relay runs", MD: "The relay in one picture.", Status: info, Diagram: json.RawMessage(drawnDiagram)})

	if len(*shots) != 1 || (*shots)[0].dashboard != "http://100.64.0.1:61210/" {
		t.Fatalf("shots = %+v, want the diagram drawn from the dashboard's own address", *shots)
	}
	card := postedCard(t, calls(fake, "cc-slack")[0].Argv)
	if len(card.Artifacts) != 1 || card.Artifacts[0].URL != testDiagramPage || !strings.Contains(card.Body, testDiagramPage) {
		t.Fatalf("card = %+v, want its link at the tailnet name's https port, %s, which Slack opens", card, testDiagramPage)
	}
}

func TestADiagramThatCannotPostItsPictureRefusesTheReplyBeforeAnythingPosts(t *testing.T) {
	cases := map[string]struct {
		dashboard string
		failed    error
		diagram   string
		want      []string
		drawn     bool
	}{
		"a medium outside the list": {`{"host":"mac.tail1.ts.net","port":61210,"tls_port":61211}`, nil, s3Diagram, []string{`"copy"`, `"s3"`, "memory, disk, bucket, keys, pages"}, false},
		"no https port":             {`{"host":"mac.tail1.ts.net","port":61210}`, nil, drawnDiagram, []string{"no tls_port", "https"}, false},
		"no Chrome":                 {`{"host":"mac.tail1.ts.net","port":61210,"tls_port":61211}`, errors.New("no Chrome on this Mac"), drawnDiagram, []string{"no Chrome on this Mac"}, true},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			fake := scripted()
			f, shots := onDashboard(t, fanout(t, fake), c.failed)
			if err := os.WriteFile(filepath.Join(f.Entry.StateDir, "dashboard.json"), []byte(c.dashboard), 0o600); err != nil {
				t.Fatal(err)
			}
			_, err := f.pictured(context.Background(), Reply{ID: "d1", Title: "How it opens", MD: "One picture.", Diagram: json.RawMessage(c.diagram), Thread: "1793684147.131059"})

			if err == nil || !strings.Contains(err.Error(), "nothing posted") {
				t.Fatalf("err = %v, want a refusal that says nothing posted", err)
			}
			for _, want := range c.want {
				if !strings.Contains(err.Error(), want) {
					t.Fatalf("refusal %q does not name %s", err, want)
				}
			}
			if drawn := len(*shots) > 0; drawn != c.drawn {
				t.Fatalf("Chrome drew %d times, want drawn = %v", len(*shots), c.drawn)
			}
			if posts := slices.Concat(calls(fake, "cc-slack"), calls(fake, "cc-present")); len(posts) > 0 {
				t.Fatalf("posted %v before the refusal", posts)
			}
		})
	}
}

func TestADiagramOnAThreadReplyPostsItsPictureInThatThread(t *testing.T) {
	fake := scripted()
	f, _ := onDashboard(t, fanout(t, fake), nil)
	var pictures []string
	f.Runner = readingRunner{fake, func(c shell.Command) {
		if c.Argv[0] != "cc-slack" || c.Argv[1] != "dm-status" || !slices.Contains(c.Argv, "--file") {
			return
		}
		if body, err := os.ReadFile(flag(t, c.Argv, "--file")); err == nil && string(body) == testPNG {
			pictures = append(pictures, flag(t, c.Argv, "--file"))
		}
	}}
	sent := sendPictured(t, f, Reply{ID: "d1", Title: "How the relay runs", MD: "The relay in one picture.", Diagram: json.RawMessage(drawnDiagram), Thread: "1793684147.131059"})

	posts := calls(fake, "cc-slack")
	if len(posts) != 1 || flag(t, posts[0].Argv, "--thread") != "1793684147.131059" || len(pictures) != 1 {
		t.Fatalf("cc-slack calls = %v, pictures read while posting %v; want one post in his thread carrying the drawn diagram", posts, pictures)
	}
	if text := posts[0].Argv[len(posts[0].Argv)-1]; !strings.Contains(text, "<"+testDiagramPage+"|"+diagramLead+"The relay>") {
		t.Fatalf("thread text %q lacks the dashboard link", text)
	}
	if slack := surface(sent, "slack"); slack.Error != "" || slack.Warning != "" {
		t.Fatalf("slack outcome = %+v", slack)
	}
}
