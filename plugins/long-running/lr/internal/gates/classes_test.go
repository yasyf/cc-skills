package gates

import "testing"

func patchOf(text string) *string { return &text }

func TestTheClassCheckNamesWhatMakesAChangeCode(t *testing.T) {
	devManifest := "@@ -1,1 +1,2 @@\n [dev-dependencies]\n+a = \"1\"\n"
	production := "@@ -1,1 +1,2 @@\n [dependencies]\n+a = \"1\"\n"
	cases := []struct {
		name    string
		patches map[string]*string
		want    string
	}{
		{"tests and CI", map[string]*string{"go/a_test.go": nil, ".github/workflows/x.yml": nil, "docs/a.go": nil}, ""},
		{"source", map[string]*string{"go/a_test.go": nil, "go/a.go": nil}, "go/a.go"},
		{"an unread file stands", map[string]*string{"README.md": nil}, ""},
		{"a dev manifest and its lockfile", map[string]*string{"go/Cargo.toml": patchOf(devManifest), "Cargo.lock": patchOf("@@\n+x\n")}, ""},
		{"a production manifest", map[string]*string{"go/Cargo.toml": patchOf(production)}, "go/Cargo.toml"},
		{"a lockfile that removes", map[string]*string{"go/Cargo.toml": patchOf(devManifest), "Cargo.lock": patchOf("@@\n-x\n")}, "Cargo.lock"},
		{"a lint config", map[string]*string{".golangci.yml": nil}, ""},
		{"tooling", map[string]*string{".agents/skills/x/scripts/a.py": nil}, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := TestOnlyOffender(tc.patches); got != tc.want {
				t.Fatalf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestAClassThePathsAloneDecideIsNamedOnlyWhenEveryChangedFileFitsIt(t *testing.T) {
	grades := Grades(nil, []string{"class regression", "class ci-tooling"}, "")
	cases := []struct {
		name  string
		files []string
		want  string
	}{
		{"#49101 remote-builder", []string{"go/remote-builder/AGENTS.md", "go/remote-builder/cli/rb/exec.go", "go/remote-builder/cli/rb/exec_test.go", "tools/remote-builder/README.md"}, "class ci-tooling"},
		{"#49240 cc-remote pin", []string{"mise.toml", "tools/cc-remote/agents-payload.json", "tools/cc-remote/config.yaml", "tools/versions.toml"}, "class ci-tooling"},
		{"#49370 remote-builder routes", []string{"go/remote-builder/AGENTS.md", "go/remote-builder/cli/rb/exec.go", "go/remote-builder/cli/rb/root.go", "go/remote-builder/cli/rb/routes.go", "tools/remote-builder/shims/remote-builder"}, "class ci-tooling"},
		{"tests and CI only", []string{"go/sand/internal/vm/run_test.go", ".buildkite/pipeline.yml"}, TestOnlyGrade},
		{"#49353 sand install pins", []string{"go/sand/internal/sandsql/local/pin_test.go", "go/sand/internal/sandsql/local/pin_unix.go", "go/sand/next/internal/sandsql/local/pin_test.go", "go/sand/next/internal/sandsql/local/pin_unix.go"}, ""},
		{"#49351 iris chat load", []string{"dashboard/src/iris/lib/events.test.ts", "dashboard/src/iris/lib/events.ts"}, ""},
		{"#49341 sandsql presigned uploads", []string{"go/sand/next/internal/sandsql/data/blob.go", "go/sand/next/internal/sandsql/data/put_test.go"}, ""},
		{"#49329 iris download CA", []string{"go/sand/next/internal/cloudrunner/substrate.go", "go/sand/next/internal/cloudrunner/substrate_test.go", "go/sand/next/internal/iris/browser_download_guard.go", "go/sand/next/internal/iris/browser_download_test.go", "go/sand/next/internal/iris/browser_driver.go", "go/sand/next/internal/iris/cdp_driver.go"}, ""},
		{"#49327 api run grant", []string{"api/src/routes/apiRoutes/agentRoutes.irisRunGrant.test.ts", "api/src/routes/apiRoutes/agentRoutes.ts"}, ""},
		{"tooling beside product code", []string{"tools/remote-builder/README.md", "go/sand/internal/vm/run.go"}, ""},
		{"a test beside product code", []string{"api/src/a.test.ts", "api/src/a.ts"}, ""},
		{"a product config", []string{"infra/k8s/values.yaml"}, ""},
		{"a production manifest", []string{"go/remote-builder/x_test.go", "package.json"}, ""},
		{"no files", nil, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := PathClass(tc.files, grades); got != tc.want {
				t.Fatalf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestAPathClassTheDriveDoesNotListIsNeverNamed(t *testing.T) {
	if got := PathClass([]string{"tools/cc-remote/config.yaml"}, Grades(nil, []string{"class regression"}, "")); got != "" {
		t.Fatalf("got %q, want no class", got)
	}
}
