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
		{"an unlisted CI package", map[string]*string{"go/ci/internal/buildkite/buildkite.go": nil, "go/ci/prune.go": nil}, "go/ci/internal/buildkite/buildkite.go"},
		{"listed CI tooling", map[string]*string{"go/ci/prune.go": nil, "infra/ci/src/cache/units.ts": nil}, ""},
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
		{"#49101 remote-builder", []string{"go/remote-builder/AGENTS.md", "go/remote-builder/cli/rb/exec.go", "go/remote-builder/cli/rb/exec_test.go", "tools/remote-builder/README.md"}, ""},
		{"#49240 cc-remote pin, whose shared pin files hold the pulumi and kubectl pins a release uses", []string{"mise.toml", "tools/cc-remote/agents-payload.json", "tools/cc-remote/config.yaml", "tools/versions.toml"}, ""},
		{"cc-remote alone", []string{"tools/cc-remote/agents-payload.json", "tools/cc-remote/config.yaml"}, ""},
		{"the toolchain pins alone", []string{"tools/versions.toml"}, ""},
		{"the landing enqueue script", []string{".agents/skills/submit-pr/scripts/stack-enqueue"}, ""},
		{"the landing check list", []string{".agents/skills/submit-pr/scripts/merge-check.json"}, ""},
		{"the queue watch wrapper", []string{".agents/skills/submit-pr/scripts/queue-watch.sh"}, ""},
		{"a tenant script that writes to a bucket", []string{".agents/skills/escape-hatch-new-tenant/scripts/verify-chain.sh"}, ""},
		{"the landing scripts' tests", []string{".agents/skills/submit-pr/scripts/stack_enqueue_test.py"}, TestOnlyGrade},
		{"a skill script that decides no gate", []string{".agents/skills/iris-desktop-cdp-preview/scripts/iris-stack.sh"}, TestOnlyGrade},
		{"#49370 remote-builder routes", []string{"go/remote-builder/AGENTS.md", "go/remote-builder/cli/rb/exec.go", "go/remote-builder/cli/rb/root.go", "go/remote-builder/cli/rb/routes.go", "tools/remote-builder/shims/remote-builder"}, "class ci-tooling"},
		{"tests and CI only", []string{"go/sand/internal/vm/run_test.go", "infra/ci/src/pipelines/ci-go.ts"}, TestOnlyGrade},
		{"#51295 api jest test", []string{"api/src/modules/llm/apiContextToLLMContext.jest.ts"}, TestOnlyGrade},
		{"a jest test beside product code", []string{"api/src/modules/llm/apiContextToLLMContext.jest.ts", "api/src/modules/llm/apiContextToLLMContext.ts"}, ""},
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

func TestProductionCodeUnderTheCITreesGetsNoPathClassWhileItsTestsAndRealCIToolingKeepIt(t *testing.T) {
	grades := Grades(nil, []string{"class ci-tooling"}, "")
	cases := []struct {
		name  string
		files []string
		want  string
	}{
		{"#50659 a release Watch", []string{"go/ci/internal/release/pipeline/cancel.go"}, ""},
		{"the release verbs", []string{"go/ci/releasepromote.go"}, ""},
		{"the release starts", []string{"go/ci/releasestarts.go"}, ""},
		{"a release package whose name ends in test", []string{"go/ci/internal/release/sandlatest/sandlatest.go"}, ""},
		{"a deploy", []string{"go/ci/internal/deploy/ship.go"}, ""},
		{"a promotion", []string{"go/ci/internal/rollout/promote.go"}, ""},
		{"break-glass", []string{"go/ci/breakglass.go"}, ""},
		{"a production write", []string{"go/ci/internal/bucketrestore/restore.go"}, ""},
		{"the release pipeline", []string{"infra/ci/src/pipelines/release.ts"}, ""},
		{"a release file beside its test", []string{"go/ci/releasestarts.go", "go/ci/releasestarts_test.go"}, ""},
		{"release tests alone", []string{"go/ci/releasestarts_test.go", "go/ci/internal/release/pipeline/cancel_test.go", "go/ci/internal/release/sandlatest/sandlatest_test.go"}, TestOnlyGrade},
		{"release fixtures", []string{"go/ci/internal/release/plandoc/testdata/plan.json"}, TestOnlyGrade},
		{"a release test helper", []string{"go/ci/internal/release/facts/factstest/factstest.go"}, TestOnlyGrade},
		{"the ci wrapper", []string{"tools/ci/ci"}, TestOnlyGrade},
		{"#47254 ci verbs and the build cache", []string{"go/ci/bkverb.go", "go/ci/internal/cache/cold.go", "go/ci/prune.go"}, TestOnlyGrade},
		{"a CI pipeline", []string{"infra/ci/src/pipelines/ci-go.ts"}, TestOnlyGrade},
		{"#49298 the Buildkite client a release start retries through", []string{"go/ci/internal/buildkite/budget.go", "go/ci/internal/buildkite/buildkite.go"}, ""},
		{"#48724 the checks that say which dev commit is green", []string{"go/ci/internal/github/checks.go"}, ""},
		{"#50959 a manual release start", []string{"go/ci/releasemanual.go", "go/ci/releasestarts.go"}, ""},
		{"#49595 the CI cache units", []string{"infra/ci/src/cache/units.ts"}, TestOnlyGrade},
		{"a CI package nobody listed", []string{"go/ci/internal/newthing/run.go"}, ""},
		{"a verb nobody listed", []string{"go/ci/newverb.go"}, ""},
		{"the release shared client libraries", []string{"go/ci/internal/contract/components.go", "go/ci/internal/pipelines/pipelines.go", "go/ci/internal/registry/registry.go"}, ""},
		{"a production data read", []string{"infra/ci/src/pipelines/db-read/index.ts"}, ""},
		{"the emergency approval pipeline", []string{"infra/ci/src/pipelines/emergency-approve.ts"}, ""},
		{"an identity pipeline", []string{"infra/ci/src/pipelines/identity.ts"}, ""},
		{"the release check pipeline", []string{"infra/ci/src/pipelines/release-check.ts"}, ""},
		{"a build step hook every job runs", []string{".buildkite/hooks/pre-command"}, ""},
		{"a signing script", []string{".buildkite/scripts/sign-windows-pe.sh"}, ""},
		{"a pipeline upload script", []string{".buildkite/v2/scripts/pipeline-upload.sh"}, ""},
		{"a publishing script", []string{".buildkite/v2/scripts/ci-go-publish.sh"}, ""},
		{"a test shard script", []string{".buildkite/v2/scripts/go-test-shard.sh", ".buildkite/v2/scripts/node-test.sh"}, TestOnlyGrade},
		{"the owner rules", []string{".github/CODEOWNERS"}, ""},
		{"a workflow that approves", []string{".github/workflows/pr-events.yaml"}, ""},
		{"a CodeQL config", []string{".github/codeql/codeql-config.yml"}, TestOnlyGrade},
		{"an agent manifest named for Buildkite", []string{"infra/k8s/buildkite-agent.yaml"}, ""},
		{"tooling beside an unlisted CI file", []string{"go/ci/prune.go", "go/ci/internal/buildkite/buildkite.go"}, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := PathClass(tc.files, grades); got != tc.want {
				t.Fatalf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestTheClassCheckNamesProductionCodeUnderTheCITrees(t *testing.T) {
	cancel := "go/ci/internal/release/pipeline/cancel.go"
	if got := TestOnlyOffender(map[string]*string{cancel: nil, "tools/ci/ci": nil}); got != cancel {
		t.Fatalf("got %q, want %q", got, cancel)
	}
}

func TestAPathClassTheDriveDoesNotListIsNeverNamed(t *testing.T) {
	if got := PathClass([]string{"tools/cc-remote/config.yaml"}, Grades(nil, []string{"class regression"}, "")); got != "" {
		t.Fatalf("got %q, want no class", got)
	}
}
