package gates

import (
	"path"
	"regexp"
	"slices"
	"strings"
)

var (
	testFile = regexp.MustCompile(`(?:^|/)(?:tests?|testing|testdata|fixtures|__snapshots__|__mocks__|e2e|evals|fakes?|mocks?|testkit|testutils?|test-scripts|vcr)/|(?:^|/)[a-z0-9_-]+(?:test|testkit|testutils?|harness)/|(?:^|/)[a-z0-9]+ForTests?/` +
		`|(?:^|/)tests?\.\w+$|(?:^|/)test_[^/]*$|_test\.\w+$|\.(?:test|spec|e2e)\.\w+$|(?:^|/)(?:fake|mock)s?_[^/]*$|_(?:fake|mock)s?\.\w+$|(?:^|/)[^/]*test-timings\.json$`)
	latestDir    = regexp.MustCompile(`[a-z0-9_-]*latest/`)
	toolingGlobs = regexp.MustCompile(`^(?:\.agents/skills/.*/scripts/.*|\.agents/skills/.*/tests/.*|\.agents/skills/.*\.md|\.config/nextest.*|.*/\.config/nextest.*)$`)
	devTooling   = regexp.MustCompile(`^(?:go/remote-builder/|tools/remote-builder/|tools/cc-remote/)`)
	tomlSection  = regexp.MustCompile(`^\s*\[+([^\]]+)\]+\s*$`)
	jsonSection  = regexp.MustCompile(`^\s*"([^"]+)":\s*\{\s*$`)
	jsonClose    = regexp.MustCompile(`^\s*\},?\s*$`)

	testDirs  = []string{"__tests__", "testdata", "fixtures", "docs"}
	ciTrees   = []string{".buildkite/", ".github/", "tools/ci/", "go/ci/", "infra/ci/"}
	ciTooling = []string{
		"tools/ci/", ".github/codeql/",
		".buildkite/scripts/check-iris-windows.py", ".buildkite/scripts/verify-desktop-packaging-commands.sh",
		"infra/ci/src/cache/", "infra/ci/src/pipelines/ci-go.ts",
		"go/ci/bkverb.go", "go/ci/cached.go", "go/ci/citiming.go", "go/ci/coverage.go", "go/ci/jobtime.go", "go/ci/pipelinecoverage.go",
		"go/ci/prune.go", "go/ci/shardtimings.go", "go/ci/timings.go", "go/ci/trunkdynamicci.go", "go/ci/trunkimpacted.go", "go/ci/trunktests",
		"go/ci/internal/cache/", "go/ci/internal/jobtime/", "go/ci/internal/prune/", "go/ci/internal/shardtimings/", "go/ci/internal/timing/", "go/ci/internal/timings/",
	}
	gateScripts    = []string{".agents/skills/submit-pr/scripts/", ".agents/skills/escape-hatch-new-tenant/scripts/"}
	ciScript       = regexp.MustCompile(`^\.buildkite/v2/scripts/[a-z0-9-]*(?:test|shard)[a-z0-9.-]*$`)
	lintConfigs    = []string{"clippy.toml", ".clippy.toml", "deny.toml", "rustfmt.toml", ".rustfmt.toml", ".golangci.yml", ".golangci.yaml", "ruff.toml", ".ruff.toml", "biome.json", ".eslintrc.json", "eslint.config.js", "eslint.config.mjs"}
	manifests      = []string{"Cargo.toml", "package.json"}
	lockfiles      = []string{"Cargo.lock", "yarn.lock", "package-lock.json", "pnpm-lock.yaml"}
	sourceSuffixes = []string{".go", ".rs", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".swift", ".kt", ".java", ".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".mm", ".sql", ".proto", ".sand", ".sh", ".css", ".scss", ".html"}

	pathClasses = map[string]func(string) bool{
		ClassOf(TestOnlyGrade): testOrCIFile,
		"test-only":            testOrCIFile,
		"ci-only":              testOrCIFile,
		"ci-tooling":           func(name string) bool { return testOrCIFile(name) || devTooling.MatchString(name) },
	}
)

func PathClass(files, grades []string) string {
	for _, phrase := range grades {
		if covers, found := pathClasses[ClassOf(phrase)]; found && len(files) > 0 && !slices.ContainsFunc(files, func(name string) bool { return !covers(name) }) {
			return phrase
		}
	}
	return ""
}

func PathDecided(phrase string) bool {
	_, found := pathClasses[ClassOf(phrase)]
	return found
}

func TestOnlyClass(phrase string) bool {
	return slices.Contains([]string{ClassOf(TestOnlyGrade), "test-only"}, ClassOf(phrase))
}

func Manifest(name string) bool {
	return slices.Contains(manifests, path.Base(name))
}

func Lockfile(name string) bool {
	return slices.Contains(lockfiles, path.Base(name))
}

func SectionLine(body string) bool {
	return tomlSection.MatchString(body) || jsonSection.MatchString(body) || jsonClose.MatchString(body)
}

func testCode(name string) bool {
	return testPath(name) || strings.Contains("/"+name, "/__tests__/")
}

func testPath(name string) bool {
	return testFile.MatchString(latestDir.ReplaceAllString(name, ""))
}

func prefixedBy(prefixes []string, name string) bool {
	return slices.ContainsFunc(prefixes, func(prefix string) bool { return strings.HasPrefix(name, prefix) })
}

func testOrCIFile(name string) bool {
	return isTestOrCI(name) || slices.Contains(lintConfigs, path.Base(name))
}

func isTestOrCI(name string) bool {
	if prefixedBy(ciTooling, name) || ciScript.MatchString(name) {
		return true
	}
	if testPath(name) || toolingGlobs.MatchString(name) && !prefixedBy(gateScripts, name) {
		return true
	}
	parts := strings.Split(name, "/")
	return slices.ContainsFunc(parts[:len(parts)-1], func(part string) bool { return slices.Contains(testDirs, part) })
}

func suffixOf(name string) string {
	base := path.Base(name)
	if ext := path.Ext(base); ext != base {
		return ext
	}
	return ""
}

func devOnly(name string, patch *string) bool {
	if patch == nil {
		return false
	}
	toml := strings.HasSuffix(name, ".toml")
	section := ""
	for line := range strings.SplitSeq(*patch, "\n") {
		if strings.HasPrefix(line, "@@") {
			section = ""
			continue
		}
		body := line[min(1, len(line)):]
		header := jsonSection
		if toml {
			header = tomlSection
		}
		if found := header.FindStringSubmatch(body); found != nil {
			section = strings.TrimSpace(found[1])
		}
		if (strings.HasPrefix(line, "+") || strings.HasPrefix(line, "-")) && !devSection(section) {
			return false
		}
		if !toml && jsonClose.MatchString(body) {
			section = ""
		}
	}
	return true
}

func devSection(section string) bool {
	if strings.HasSuffix(section, "dev-dependencies") || section == "devDependencies" {
		return true
	}
	parts := strings.Split(section, ".")
	return slices.Contains(parts[:min(2, len(parts))], "lints")
}

func removesNothing(patch *string) bool {
	if patch == nil {
		return false
	}
	return !slices.ContainsFunc(strings.Split(*patch, "\n"), func(line string) bool { return strings.HasPrefix(line, "-") })
}

func TestOnlyOffender(patches map[string]*string) string {
	names := make([]string, 0, len(patches))
	for name := range patches {
		names = append(names, name)
	}
	slices.Sort(names)
	manifested := make([]string, 0, len(names))
	for _, name := range names {
		if slices.Contains(manifests, path.Base(name)) && !isTestOrCI(name) {
			manifested = append(manifested, name)
		}
	}
	devManifests := len(manifested) > 0 && !slices.ContainsFunc(manifested, func(name string) bool { return !devOnly(name, patches[name]) })
	for _, name := range names {
		base := path.Base(name)
		switch {
		case isTestOrCI(name), slices.Contains(lintConfigs, base), slices.Contains(manifested, name) && devOnly(name, patches[name]):
		case slices.Contains(lockfiles, base) && devManifests && removesNothing(patches[name]):
		case slices.Contains(manifests, base), slices.Contains(lockfiles, base), slices.Contains(sourceSuffixes, suffixOf(name)):
			return name
		}
	}
	return ""
}
