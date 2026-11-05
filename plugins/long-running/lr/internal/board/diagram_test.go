package board

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const relayDoc = `{"arch":1,"name":"relay","title":"The relay","nodes":[{"id":"a","kind":"service","name":"A","role":"sends"},{"id":"b","kind":"service","name":"B","role":"takes"}],"edges":[{"from":"a","to":"b"}]}`

func drawingLink(t *testing.T, failed error) (Link, *[]string) {
	t.Helper()
	state := t.TempDir()
	if err := os.WriteFile(filepath.Join(state, "dashboard.json"), []byte(`{"host":"100.64.0.1","port":61210}`), 0o600); err != nil {
		t.Fatal(err)
	}
	tailnet := &shelltest.Fake{}
	tailnet.Reply(`{"Self":{"DNSName":"mac.tail1.ts.net."}}`, "tailscale", "status")
	var drawn []string
	return Link{State: state, Runner: tailnet, Draw: func(_ context.Context, dashboard string, doc json.RawMessage, png string) error {
		drawn = append(drawn, dashboard+" "+string(doc))
		if failed != nil {
			return failed
		}
		return os.WriteFile(png, []byte("png"), 0o600)
	}}, &drawn
}

func diagramCard(doc string) json.RawMessage {
	return json.RawMessage(`{"id":"card-relay","type":"card","title":"Which relay?","children":[{"id":"relay-from","type":"markdown","md":"**From:** plan"},{"id":"relay-diagram","type":"lr.arch","doc":` + doc + `},{"id":"relay-choice","type":"choice","options":[]}]}`)
}

func TestADiagramOnACardStaysLiveAndGainsThePictureAndLinkItsSlackCardShows(t *testing.T) {
	l, drawn := drawingLink(t, nil)
	got, err := l.pictured(context.Background(), diagramCard(relayDoc))
	if err != nil {
		t.Fatal(err)
	}
	if len(*drawn) != 1 || (*drawn)[0] != "http://100.64.0.1:61210/ "+relayDoc {
		t.Fatalf("drawn = %q, want the one diagram drawn from the dashboard", *drawn)
	}
	var card struct {
		Children []struct {
			ID      string          `json:"id"`
			Type    string          `json:"type"`
			Doc     json.RawMessage `json:"doc"`
			Title   string          `json:"title"`
			Picture string          `json:"picture"`
			Link    string          `json:"link"`
		} `json:"children"`
	}
	if err := json.Unmarshal(got, &card); err != nil {
		t.Fatal(err)
	}
	if len(card.Children) != 3 || card.Children[0].ID != "relay-from" || card.Children[2].ID != "relay-choice" {
		t.Fatalf("children = %+v, want the diagram kept in place", card.Children)
	}
	diagram := card.Children[1]
	png := filepath.Join(l.State, diagramDir, "relay-diagram.png")
	if diagram.Type != diagramType || string(diagram.Doc) != relayDoc || diagram.Title != "The relay" || diagram.Picture != png || diagram.Link != "http://mac.tail1.ts.net:61210/#/question/card-relay" {
		t.Fatalf("diagram = %+v, want the live doc with its picture, title and question link", diagram)
	}
	if body, err := os.ReadFile(png); err != nil || string(body) != "png" {
		t.Fatalf("picture %s: %q, %v", png, body, err)
	}
}

func TestACardWithNoDiagramPassesThroughUntouched(t *testing.T) {
	l, drawn := drawingLink(t, nil)
	card := json.RawMessage(`{"id":"card-x","type":"card","children":[{"id":"x","type":"markdown","md":"m"}]}`)
	got, err := l.pictured(context.Background(), card)
	if err != nil || string(got) != string(card) || len(*drawn) != 0 {
		t.Fatalf("got %s, %v, drawn %q", got, err, *drawn)
	}
}

func TestADiagramThatCannotDrawRefusesTheCardWithItsReason(t *testing.T) {
	cases := map[string]struct {
		doc    string
		failed error
		said   string
	}{
		"broken doc":   {doc: `{"arch":1,"name":"r","title":"T","nodes":[{"id":"a","kind":"service","name":"A","role":"r"}],"edges":[{"from":"a","to":"ghost"}]}`, said: `"ghost"`},
		"chrome fails": {doc: relayDoc, failed: errors.New("no Chrome on this Mac"), said: "no Chrome on this Mac"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			l, _ := drawingLink(t, c.failed)
			_, err := l.pictured(context.Background(), diagramCard(c.doc))
			var refusal linkRefusal
			if !errors.As(err, &refusal) || !strings.Contains(refusal.said, c.said) {
				t.Fatalf("err = %v, want a refusal naming %s", err, c.said)
			}
		})
	}
}

func TestAskOwnerCarriesItsDiagramFileToTheLink(t *testing.T) {
	path := filepath.Join(t.TempDir(), "relay.json")
	if err := os.WriteFile(path, []byte(relayDoc), 0o600); err != nil {
		t.Fatal(err)
	}
	block, err := Ask{Title: "Which relay?", Background: "The relay drops one in ten.", Options: []string{"Fix it :: drops none", "Keep it :: drops one in ten"}, NothingToBuild: "a process pick", Diagram: path}.Block("acme/app", everyPR)
	if err != nil {
		t.Fatal(err)
	}
	var card struct {
		Children []struct {
			Type string          `json:"type"`
			Doc  json.RawMessage `json:"doc"`
		} `json:"children"`
	}
	if err := json.Unmarshal(block, &card); err != nil {
		t.Fatal(err)
	}
	for _, child := range card.Children {
		if child.Type == diagramType && string(child.Doc) == relayDoc {
			return
		}
	}
	t.Fatalf("card %s carries no diagram", block)
}
