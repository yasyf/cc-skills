package reply

import (
	"context"
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	artifactURL = "http://mac.tail1.ts.net:61211/artifact/r9/0"
	transcript  = `{"i":1,"kind":"user","text":"why did it stop?","blocks":[]}
{"i":2,"kind":"attachment","attachment_type":"hook_success"}
{"i":3,"kind":"assistant","text":"The load gate.","blocks":[{"type":"thinking","thinking":"read the log"},{"type":"text","text":"The load gate."}]}
`
)

func withDashboard(t *testing.T, f Fanout) Fanout {
	t.Helper()
	if err := os.WriteFile(filepath.Join(f.Entry.StateDir, "dashboard.json"), []byte(`{"host":"127.0.0.1","port":61211}`), 0o600); err != nil {
		t.Fatal(err)
	}
	return f
}

func sourceFile(t *testing.T, name, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func attached(t *testing.T, f Fanout, id string, artifacts ...Artifact) (Reply, string) {
	t.Helper()
	return f.withArtifacts(context.Background(), Reply{ID: id, Title: "Why it stopped", MD: "The load gate.", Artifacts: artifacts})
}

func TestCodeIsCopiedByteForByteSoAWipedSourceBreaksNoLink(t *testing.T) {
	fake := scripted()
	f := withDashboard(t, fanout(t, fake))
	body := "func main() {\n\tprintln(\"<b>\")\n}\n"
	source := sourceFile(t, "main.go", body)
	r, warning := attached(t, f, "r9", Artifact{Title: "  The   entry  point ", Kind: kindCode, File: source})
	if warning != "" {
		t.Fatalf("warning = %q", warning)
	}
	if err := os.Remove(source); err != nil {
		t.Fatal(err)
	}
	copied, err := os.ReadFile(filepath.Join(f.Entry.StateDir, storeDir, "r9", "0"))
	if err != nil {
		t.Fatal(err)
	}
	if string(copied) != body {
		t.Fatalf("copy = %q, want the source's bytes", copied)
	}
	want := Artifact{Title: "The entry point", Kind: kindCode, Language: "go", URL: artifactURL, Size: "3 lines"}
	if len(r.Artifacts) != 1 || r.Artifacts[0] != want {
		t.Fatalf("artifacts = %+v, want %+v", r.Artifacts, want)
	}
}

func TestATranscriptCountsItsTurnsAndKeepsEveryLine(t *testing.T) {
	fake := scripted()
	f := withDashboard(t, fanout(t, fake))
	r, warning := attached(t, f, "r9", Artifact{Title: "The session", Kind: kindTranscript, File: sourceFile(t, "s.jsonl", transcript)})
	if warning != "" {
		t.Fatalf("warning = %q", warning)
	}
	if len(r.Artifacts) != 1 || r.Artifacts[0].Size != "2 turns" {
		t.Fatalf("artifacts = %+v, want one of 2 turns", r.Artifacts)
	}
	copied, err := os.ReadFile(filepath.Join(f.Entry.StateDir, storeDir, "r9", "0"))
	if err != nil {
		t.Fatal(err)
	}
	if string(copied) != transcript {
		t.Fatalf("copy = %q, want every line of the source", copied)
	}
}

func TestAnArtifactThatDoesNotReadIsLeftOffAndTheRestStillAttach(t *testing.T) {
	cases := map[string]Artifact{
		"a file that is gone":      {Title: "Gone", Kind: kindCode, File: "/no/such/file.go"},
		"a transcript of prose":    {Title: "Prose", Kind: kindTranscript, File: sourceFile(t, "prose.txt", "not json\n")},
		"a transcript of no turns": {Title: "Hooks", Kind: kindTranscript, File: sourceFile(t, "hooks.jsonl", `{"kind":"attachment"}`+"\n")},
		"an empty file":            {Title: "Empty", Kind: kindCode, File: sourceFile(t, "empty.go", "\n")},
	}
	for name, bad := range cases {
		t.Run(name, func(t *testing.T) {
			fake := scripted()
			f := withDashboard(t, fanout(t, fake))
			good := Artifact{Title: "Good", Kind: kindCode, File: sourceFile(t, "good.go", "package good\n")}
			r, warning := attached(t, f, "r9", bad, good)
			if !strings.Contains(warning, bad.Title) {
				t.Fatalf("warning = %q, want it to name %q", warning, bad.Title)
			}
			if len(r.Artifacts) != 1 || r.Artifacts[0].Title != "Good" || r.Artifacts[0].URL != artifactURL {
				t.Fatalf("artifacts = %+v, want Good alone at the first slot", r.Artifacts)
			}
		})
	}
}

func TestAnEditThatLeavesTheArtifactOffDeletesItsCopy(t *testing.T) {
	fake := scripted()
	f := withDashboard(t, fanout(t, fake))
	attached(t, f, "r9", Artifact{Title: "Secret", Kind: kindCode, File: sourceFile(t, "s.go", "token\n")})
	r, warning := attached(t, f, "r9")
	if warning != "" || len(r.Artifacts) != 0 {
		t.Fatalf("artifacts = %+v, warning = %q", r.Artifacts, warning)
	}
	if _, err := os.Stat(filepath.Join(f.Entry.StateDir, storeDir, "r9")); !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("stat = %v, want the copy gone", err)
	}
}

func TestADashboardWithNoTailnetAddressLeavesTheArtifactsOffWithAWarning(t *testing.T) {
	cases := map[string]func(*shelltest.Fake){
		"no tailscale": func(fake *shelltest.Fake) { fake.Fail(1, "not running", "tailscale") },
		"no dns name":  func(fake *shelltest.Fake) { fake.Reply(`{"Self":{}}`, "tailscale") },
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			fake := &shelltest.Fake{}
			c(fake)
			scriptedOver(fake)
			f := withDashboard(t, fanout(t, fake))
			r, warning := attached(t, f, "r9", Artifact{Title: "Code", Kind: kindCode, File: sourceFile(t, "c.go", "package c\n")})
			if len(r.Artifacts) != 0 || !strings.Contains(warning, "no artifact was attached") {
				t.Fatalf("artifacts = %+v, warning = %q", r.Artifacts, warning)
			}
		})
	}
}

func TestAMalformedArtifactIsLeftOffBeforeAnythingReadsIt(t *testing.T) {
	r, warned := Reply{ID: "r9", Title: "t", MD: "m", Artifacts: []Artifact{
		{Kind: kindCode, File: "/a.go"},
		{Title: "Kind", Kind: "video", File: "/a.go"},
		{Title: "Relative", Kind: kindCode, File: "a.go"},
		{Title: "Good", Kind: kindCode, File: "/a.go"},
	}}.mended()
	if len(r.Artifacts) != 1 || r.Artifacts[0].Title != "Good" {
		t.Fatalf("artifacts = %+v", r.Artifacts)
	}
	if !strings.Contains(strings.Join(warned, ";"), "3 of the artifacts are malformed") {
		t.Fatalf("warned = %q", warned)
	}
}

func TestAnArtifactReachesEverySurfaceAsItsLinkAndNoBodyLeavesTheMac(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	artifact := Artifact{Title: "The session", Kind: kindTranscript, URL: artifactURL, Size: "2 turns"}
	f.Send(context.Background(), Reply{ID: "r9", Title: "Why it stopped", MD: "The load gate.", Artifacts: []Artifact{artifact}})
	link := "[The session](" + artifactURL + ")"

	posted := calls(fake, "cc-slack")
	if len(posted) != 1 {
		t.Fatalf("cc-slack calls = %+v, want one card though the reply has no status", posted)
	}
	card := flag(t, posted[0].Argv, "--card")
	var drawn struct {
		Body      string     `json:"body"`
		Artifacts []Artifact `json:"artifacts"`
	}
	if err := json.Unmarshal([]byte(card), &drawn); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(drawn.Body, link) || len(drawn.Artifacts) != 1 || drawn.Artifacts[0] != artifact {
		t.Fatalf("slack card = %+v, want the link in its text and the artifact beside it", drawn)
	}
	records := calls(fake, "cci")
	if !slices.ContainsFunc(records, func(c shell.Command) bool { return strings.Contains(c.Stdin, link) }) {
		t.Fatalf("cci calls = %+v, want the record to carry the link", records)
	}
	saved, _ := stored(t, f, "r9")
	if saved.MD != "The load gate." || len(saved.Artifacts) != 1 || saved.Artifacts[0] != artifact {
		t.Fatalf("stored reply = %+v, want clean text and the artifact for Chat to draw", saved)
	}
}

func TestAPickCardAndAThreadReplyCarryTheArtifactAsALink(t *testing.T) {
	artifact := Artifact{Title: "The session", Kind: kindTranscript, URL: artifactURL, Size: "2 turns"}
	link := "[The session](" + artifactURL + ")"

	fake := scripted()
	fanout(t, fake).Send(context.Background(), Reply{ID: "r9", Title: "Land it?", MD: "The load gate.", Picks: []string{"Land", "Hold"}, Recommended: "Land", Artifacts: []Artifact{artifact}})
	if !slices.ContainsFunc(calls(fake, "cc-present"), func(c shell.Command) bool {
		return strings.Contains(c.Stdin, `"card-r9"`) && strings.Contains(c.Stdin, link)
	}) {
		t.Fatalf("cc-present calls = %+v, want the board card to carry the link", calls(fake, "cc-present"))
	}

	fake = scripted()
	fanout(t, fake).Send(context.Background(), Reply{ID: "r9", Title: "Why", MD: "The load gate.", Thread: "1793635190.051179", Artifacts: []Artifact{artifact}})
	posts := calls(fake, "cc-slack")
	if len(posts) != 1 || !strings.Contains(posts[0].Argv[len(posts[0].Argv)-1], "<"+artifactURL+"|The session>") {
		t.Fatalf("cc-slack calls = %+v, want the link in Slack's form", posts)
	}
}

func TestADiagramTakesOneOfTheCardsFiveArtifactSlots(t *testing.T) {
	five := make([]Artifact, 5)
	for n := range five {
		five[n] = Artifact{Title: "Code", Kind: kindCode, File: "/a.go"}
	}
	r, warned := Reply{ID: "r9", Title: "t", MD: "m", Artifacts: five}.mended()
	if len(r.Artifacts) != 5 || len(warned) != 0 {
		t.Fatalf("with no diagram: artifacts = %+v, warned = %q", r.Artifacts, warned)
	}
	r, warned = Reply{ID: "r9", Title: "t", MD: "m", Artifacts: five, Diagram: json.RawMessage(drawnDiagram)}.mended()
	if len(r.Artifacts) != 4 || len(warned) != 1 {
		t.Fatalf("with a diagram: artifacts = %+v, warned = %q", r.Artifacts, warned)
	}
}

func TestACardCarriesTheDiagramsPictureAheadOfTheArtifactsAndStoresNoImage(t *testing.T) {
	fake := scripted()
	f, _ := onDashboard(t, fanout(t, fake), nil)
	artifact := Artifact{Title: "The session", Kind: kindTranscript, URL: artifactURL, Size: "2 turns"}
	f.Send(context.Background(), Reply{ID: "d1", Title: "How the relay runs", MD: "The relay.", Status: info, Diagram: json.RawMessage(drawnDiagram), Artifacts: []Artifact{artifact}})

	posts := calls(fake, "cc-slack")
	if len(posts) != 1 {
		t.Fatalf("cc-slack calls = %v", posts)
	}
	card := postedCard(t, posts[0].Argv)
	if len(card.Artifacts) != 2 || card.Artifacts[0].Kind != diagramKind || card.Artifacts[0].Image == "" || card.Artifacts[1] != artifact {
		t.Fatalf("artifacts = %+v, want the pictured diagram and then the artifact", card.Artifacts)
	}
	saved, _ := stored(t, f, "d1")
	if len(saved.Artifacts) != 1 || saved.Artifacts[0] != artifact {
		t.Fatalf("stored artifacts = %+v, want the artifact alone, for Chat's window", saved.Artifacts)
	}
}
