package reply

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	testDashboard   = "http://mac.tail1.ts.net:61210/"
	testDiagramPage = testDashboard + "diagram/d1"
	testPNG         = "\x89PNG\r\n\x1a\n"
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
	if err := os.WriteFile(filepath.Join(f.Entry.StateDir, "dashboard.json"), []byte(`{"host":"mac.tail1.ts.net","port":61210}`), 0o600); err != nil {
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
	sent := f.Send(context.Background(), Reply{ID: "d1", Title: "How the relay runs", MD: "The relay in one picture.", Status: info, Diagram: json.RawMessage(drawnDiagram)})

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
	if _, err := os.Stat(got.Image); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("the picture %s outlived its post: %v", got.Image, err)
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
	for _, call := range fake.Calls() {
		if strings.Contains(strings.Join(call.Argv, " ")+call.Stdin, diagramNote) {
			t.Fatalf("%q still says only that the diagram is on the dashboard", call.Argv)
		}
	}
	if slack := surface(sent, "slack"); slack.Error != "" || slack.Warning != "" {
		t.Fatalf("slack outcome = %+v", slack)
	}
	stored, _ := stored(t, f, "d1")
	if stored.MD != "The relay in one picture." {
		t.Fatalf("the Chat copy of the words changed: %q", stored.MD)
	}
}

func TestADiagramLinksItsDashboardByTailnetNameWhileChromeDrawsFromTheDashboardAddress(t *testing.T) {
	fake := scripted()
	f, shots := onDashboard(t, fanout(t, fake), nil)
	if err := os.WriteFile(filepath.Join(f.Entry.StateDir, "dashboard.json"), []byte(`{"host":"100.64.0.1","port":61210}`), 0o600); err != nil {
		t.Fatal(err)
	}
	f.Send(context.Background(), Reply{ID: "d1", Title: "How the relay runs", MD: "The relay in one picture.", Status: info, Diagram: json.RawMessage(drawnDiagram)})

	if len(*shots) != 1 || (*shots)[0].dashboard != "http://100.64.0.1:61210/" {
		t.Fatalf("shots = %+v, want the diagram drawn from the dashboard's own address", *shots)
	}
	card := postedCard(t, calls(fake, "cc-slack")[0].Argv)
	if len(card.Artifacts) != 1 || card.Artifacts[0].URL != testDiagramPage || !strings.Contains(card.Body, testDiagramPage) {
		t.Fatalf("card = %+v, want its link on the tailnet name %s, which Slack can open", card, testDiagramPage)
	}
}

func TestADiagramWhosePictureDoesNotDrawStillPostsItsCardAndSaysWhy(t *testing.T) {
	fake := scripted()
	f, _ := onDashboard(t, fanout(t, fake), errors.New("no Chrome on this Mac"))
	sent := f.Send(context.Background(), Reply{ID: "d1", Title: "How the relay runs", MD: "The relay in one picture.", Status: info, Diagram: json.RawMessage(drawnDiagram)})

	card := postedCard(t, calls(fake, "cc-slack")[0].Argv)
	if len(card.Artifacts) != 1 || card.Artifacts[0].Image != "" || card.Artifacts[0].URL != testDiagramPage {
		t.Fatalf("artifacts = %+v, want the diagram's card with no picture", card.Artifacts)
	}
	if slack := surface(sent, "slack"); slack.Error != "" || !strings.Contains(slack.Warning, "no Chrome on this Mac") {
		t.Fatalf("slack outcome = %+v, want a posted card and the reason it has no picture", slack)
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
	sent := f.Send(context.Background(), Reply{ID: "d1", Title: "How the relay runs", MD: "The relay in one picture.", Diagram: json.RawMessage(drawnDiagram), Thread: "1793684147.131059"})

	posts := calls(fake, "cc-slack")
	if len(posts) != 1 || flag(t, posts[0].Argv, "--thread") != "1793684147.131059" || len(pictures) != 1 {
		t.Fatalf("cc-slack calls = %v, pictures read while posting %v; want one post in his thread carrying the drawn diagram", posts, pictures)
	}
	if text := posts[0].Argv[len(posts[0].Argv)-1]; !strings.Contains(text, "<"+testDiagramPage+"|"+diagramLead+"The relay>") {
		t.Fatalf("thread text %q lacks the dashboard link", text)
	}
	if _, err := os.Stat(pictures[0]); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("the picture %s outlived its post: %v", pictures[0], err)
	}
	if slack := surface(sent, "slack"); slack.Error != "" || slack.Warning != "" {
		t.Fatalf("slack outcome = %+v", slack)
	}
}

func TestADiagramWhosePictureDoesNotDrawStillPostsTheThreadReplyWithItsLink(t *testing.T) {
	fake := scripted()
	f, _ := onDashboard(t, fanout(t, fake), errors.New("no Chrome on this Mac"))
	sent := f.Send(context.Background(), Reply{ID: "d1", Title: "How the relay runs", MD: "The relay in one picture.", Diagram: json.RawMessage(drawnDiagram), Thread: "1793684147.131059"})

	posts := calls(fake, "cc-slack")
	if len(posts) != 1 || slices.Contains(posts[0].Argv, "--file") || !strings.Contains(posts[0].Argv[len(posts[0].Argv)-1], testDiagramPage) {
		t.Fatalf("cc-slack calls = %v, want the thread reply with the dashboard link and no picture", posts)
	}
	if slack := surface(sent, "slack"); slack.Error != "" || !strings.Contains(slack.Warning, "no Chrome on this Mac") {
		t.Fatalf("slack outcome = %+v, want a posted reply and the reason it has no picture", slack)
	}
}
