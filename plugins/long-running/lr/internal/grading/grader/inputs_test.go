package grader

import (
	"context"
	"path/filepath"
	"testing"
)

func TestAPassWhoseInputDidNotReachItNamesItAndGradesNothing(t *testing.T) {
	missing := []string{"cc-notes doc 4baeb30: exit status 3", "the whole of cci #449562: open /Users/a/.cc-inbox/blobs/449562.txt: no such file or directory"}
	grades := map[string]func(*testing.T) (string, *fakeShell, error){
		"fidelity": func(t *testing.T) (string, *fakeShell, error) {
			directory := fidelityDir(t, map[string]any{"grades": []any{}, "unread": missing})
			shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(nil))
			return directory, shell, Fidelity(context.Background(), directory, shell.deps())
		},
		"finder": func(t *testing.T) (string, *fakeShell, error) {
			directory := finderJobDir(t, map[string]any{"unread": missing})
			shell, _ := newFinderFake(t, replyOf())
			return directory, shell, runFinderJob(t, directory, shell)
		},
	}
	for name, grade := range grades {
		t.Run(name, func(t *testing.T) {
			directory, shell, err := grade(t)
			if err == nil {
				t.Fatal("the pass settled as graded")
			}
			result := resultOf(t, directory)
			holdsAll(t, result["error"].(string), missing...)
			if _, read := result["verdict"]; read {
				t.Errorf("result %v carries a verdict", result)
			}
			equal(t, len(shell.asks()), 0)
			equal(t, len(shell.posted), 0)
			wantNoFile(t, directory, PostsFile)
		})
	}
}

func TestAPlanTheRegisterNamesButTheBoxLacksStopsThePassAndNamesIt(t *testing.T) {
	gone := filepath.Join(t.TempDir(), "gone", "plan", "plan.md")
	grades := map[string]func(*testing.T) (string, *fakeShell, error){
		"fidelity": func(t *testing.T) (string, *fakeShell, error) {
			directory := fidelityDir(t, map[string]any{"grades": []any{}})
			shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(nil))
			shell.plan = gone
			return directory, shell, Fidelity(context.Background(), directory, shell.deps())
		},
		"finder": func(t *testing.T) (string, *fakeShell, error) {
			directory := finderJobDir(t, nil)
			shell, _ := newFinderFake(t, replyOf())
			shell.plan = gone
			return directory, shell, runFinderJob(t, directory, shell)
		},
	}
	for name, grade := range grades {
		t.Run(name, func(t *testing.T) {
			directory, shell, err := grade(t)
			if err == nil {
				t.Fatal("the pass settled as graded")
			}
			holdsAll(t, resultOf(t, directory)["error"].(string), "not on this box", gone)
			equal(t, len(shell.asks()), 0)
			equal(t, len(shell.posted), 0)
		})
	}
}

func TestOpenOwedDeletionsReachTheFidelityPrompt(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "owed_deletions": []string{"- task c4015d7, owner lane-9: Owed deletion: the old reader"}})
	shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(nil))
	mustFidelity(t, directory, shell)
	holdsAll(t, readText(t, directory, "question-1.md"), "- task c4015d7, owner lane-9: Owed deletion: the old reader")
}
