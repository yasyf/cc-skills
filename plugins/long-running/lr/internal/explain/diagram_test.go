package explain

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

func TestExplainRefusesAMermaidBlockInOneLineAndHostsNothing(t *testing.T) {
	source := "# How a lane resumes\n\nA lane picks up.\n\n```mermaid\nflowchart LR\n  lane --> handoff\n```\n"
	dir := filepath.Join(t.TempDir(), "explainers")
	served, owner := &host{}, &told{}

	link, err := Explain(context.Background(), Checkout{Runner: &shelltest.Fake{}, Dir: dir}, served, owner, "Lane resume", []byte(source), time.Now())

	if err == nil || link != "" || !strings.Contains(err.Error(), "arch block") || strings.Contains(err.Error(), "\n") {
		t.Fatalf("link %q, err %v", link, err)
	}
	if len(served.dirs) != 0 || len(owner.notices) != 0 || len(owner.failures) != 0 {
		t.Fatalf("served %v, notices %v, failures %v", served.dirs, owner.notices, owner.failures)
	}
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Fatalf("a refused page touched the checkout: %v", err)
	}
}

func TestRedesignRefusesAMermaidBlockBeforeItTouchesAnyCardOrPR(t *testing.T) {
	source := []byte("# Landing queue\n\n```mermaid\nflowchart LR\n  a --> b\n```\n")

	err := Redesign{}.Run(context.Background(), "Landing queue", []string{"queue-batching"}, []string{"acme/repo#41"}, source, time.Now())

	if err == nil || !strings.Contains(err.Error(), "arch block") {
		t.Fatalf("err %v", err)
	}
}

func TestRefuseDiagramsNamesWhatKeepsAnArchBlockFromDrawing(t *testing.T) {
	for name, tc := range map[string]struct{ diagram, want string }{
		"not JSON":           {`flowchart LR`, "not JSON"},
		"wrong version":      {`{"arch":2,"name":"a","title":"b","nodes":[{"id":"x","kind":"app","name":"x","role":"y"}]}`, `"arch" must be 1`},
		"no nodes":           {`{"arch":1,"name":"a","title":"b","nodes":[]}`, `"nodes" needs`},
		"node without role":  {`{"arch":1,"name":"a","title":"b","nodes":[{"id":"x","kind":"app","name":"x"}]}`, `node "x" needs`},
		"store with no home": {`{"arch":1,"name":"a","title":"b","nodes":[{"id":"x","kind":"store","name":"x","role":"y"}]}`, `node "x" is a store`},
		"edge to nowhere":    {`{"arch":1,"name":"a","title":"b","nodes":[{"id":"x","kind":"app","name":"x","role":"y"}],"edges":[{"from":"x","to":"z"}]}`, `names "z"`},
		"story to nowhere":   {`{"arch":1,"name":"a","title":"b","nodes":[{"id":"x","kind":"app","name":"x","role":"y"}],"stories":[{"steps":[{"from":"q","to":"x"}]}]}`, `names "q"`},
		"note on nowhere":    {`{"arch":1,"name":"a","title":"b","nodes":[{"id":"x","kind":"app","name":"x","role":"y"}],"notes":[{"on":"z"}]}`, `sits on "z"`},
	} {
		err := refuseDiagrams([]byte("# Page\n\n```arch\n" + tc.diagram + "\n```\n"))
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Errorf("%s: err %v, want %q", name, err, tc.want)
		}
	}
}

func TestRefuseDiagramsPassesANestedNodeAndAPageWithNoDiagram(t *testing.T) {
	nested := `{"arch":1,"name":"a","title":"b","nodes":[{"id":"x","kind":"service","name":"x","role":"y","nodes":[{"id":"part","kind":"service","name":"p","role":"q"}]},{"id":"w","kind":"app","name":"w","role":"v"}],"edges":[{"from":"w","to":"part"}]}`
	for _, source := range []string{"# Page\n\n```arch\n" + nested + "\n```\n", "# Page\n\nJust words.\n\n```go\nfunc main() {}\n```\n"} {
		if err := refuseDiagrams([]byte(source)); err != nil {
			t.Errorf("source %q: %v", source, err)
		}
	}
}
