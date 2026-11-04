package prrow

import (
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"
)

var (
	prListCall  = regexp.MustCompile(`"pr",\s*"list"|\bgh pr list\b|pulls\?state=open|search/issues`)
	mergeRowUse = regexp.MustCompile(`merge-prs|MergeBlock`)
	// One branch or one head at a time, never a list that decides which PRs are the drive's.
	namedLookups = []string{
		"lr/internal/landing/restack.go",
		"lr/internal/orca/git.go",
		"skills/long-running/scripts/label-watch.sh",
		"capt-hook/hooks/root_context.py",
	}
	// The block's name and its verdicts, never a row written onto it.
	mergeRowReaders = []string{
		"lr/internal/board/board.go",
		"lr/internal/landing/carry.go",
		"dashboard/app/server.py",
		"skills/long-running/scripts/board_watch.py",
	}
	skippedDirs = []string{"tests", "testdata", "fixtures", "node_modules", ".venv", "ios", "benchmarks"}
	sourceKinds = []string{".go", ".py", ".sh", ".js"}
)

func offenders(files map[string]string, call *regexp.Regexp, allowed []string) []string {
	var found []string
	for path, text := range files {
		if call.MatchString(text) && !slices.Contains(allowed, path) {
			found = append(found, path)
		}
	}
	slices.Sort(found)
	return found
}

func packSources(t *testing.T) map[string]string {
	t.Helper()
	root := filepath.Join("..", "..", "..")
	var paths []string
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() && slices.Contains(skippedDirs, entry.Name()) {
			return filepath.SkipDir
		}
		if !entry.IsDir() && slices.Contains(sourceKinds, filepath.Ext(path)) && !strings.HasSuffix(path, "_test.go") {
			paths = append(paths, path)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	files := make(map[string]string, len(paths))
	for _, path := range paths {
		text, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			t.Fatal(err)
		}
		files[filepath.ToSlash(relative)] = string(text)
	}
	return files
}

func TestNoPassListsOpenPRsOrWritesAMergeRowOutsideTheRow(t *testing.T) {
	files := packSources(t)
	if _, walked := files["lr/internal/prrow/fold.go"]; !walked {
		t.Fatalf("the walk missed the pack: %d files", len(files))
	}
	if found := offenders(files, prListCall, namedLookups); len(found) != 0 {
		t.Errorf("reads GitHub's PR list, and the rows in lr board rows are the only list: %v", found)
	}
	mergeSources := map[string]string{}
	for path, text := range files {
		if !strings.HasPrefix(path, "capt-hook/") {
			mergeSources[path] = text
		}
	}
	if found := offenders(mergeSources, mergeRowUse, mergeRowReaders); len(found) != 0 {
		t.Errorf("names the merge-prs block, and a PR's place on his tab comes from its row: %v", found)
	}
}

func TestTheGuardCatchesEachFormOfTheCall(t *testing.T) {
	for _, text := range []string{
		`e.Run(ctx, "gh", "pr", "list", "--state", "open")`,
		`subprocess.run(["gh", "pr", "list"])`,
		`gh pr list --state open`,
		`gh api "repos/o/r/pulls?state=open"`,
		`gh api search/issues?q=is:pr`,
	} {
		if found := offenders(map[string]string{"new.go": text}, prListCall, namedLookups); len(found) != 1 {
			t.Errorf("%q passed the guard", text)
		}
	}
	for _, text := range []string{`Block{ID: MergeBlock}`, `{"id": "merge-prs", "type": "pr-groups"}`} {
		if found := offenders(map[string]string{"new.py": text}, mergeRowUse, mergeRowReaders); len(found) != 1 {
			t.Errorf("%q passed the guard", text)
		}
	}
	if found := offenders(map[string]string{"lr/internal/landing/carry.go": `MergeBlock`}, mergeRowUse, mergeRowReaders); len(found) != 0 {
		t.Errorf("an allowed reader was refused: %v", found)
	}
}
