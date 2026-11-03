package replay

import (
	"path"
	"regexp"
	"slices"
	"strings"
)

var (
	testFile = regexp.MustCompile(`(?:^|/)(?:tests?|testing|testdata|fixtures|__snapshots__|__mocks__|e2e|evals|fakes?|mocks?|testkit|testutils?|test-scripts|vcr)/|(?:^|/)[a-z0-9_-]+(?:test|testkit|testutils?|harness)/|(?:^|/)[a-z0-9]+ForTests?/` +
		`|(?:^|/)tests?\.\w+$|(?:^|/)test_[^/]*$|_test\.\w+$|\.(?:test|spec|e2e)\.\w+$|(?:^|/)(?:fake|mock)s?_[^/]*$|_(?:fake|mock)s?\.\w+$|(?:^|/)[^/]*test-timings\.json$`)
	toolingGlobs = regexp.MustCompile(`^(?:\.agents/skills/.*/scripts/.*|\.agents/skills/.*/tests/.*|\.agents/skills/.*\.md|\.config/nextest.*|.*/\.config/nextest.*)$`)
	tomlSection  = regexp.MustCompile(`^\s*\[+([^\]]+)\]+\s*$`)
	jsonSection  = regexp.MustCompile(`^\s*"([^"]+)":\s*\{\s*$`)
	jsonClose    = regexp.MustCompile(`^\s*\},?\s*$`)

	testDirs       = []string{"__tests__", "testdata", "fixtures", "docs"}
	ciPrefixes     = []string{".buildkite/", ".github/", "tools/ci/", "go/ci/", "infra/ci/"}
	lintConfigs    = []string{"clippy.toml", ".clippy.toml", "deny.toml", "rustfmt.toml", ".rustfmt.toml", ".golangci.yml", ".golangci.yaml", "ruff.toml", ".ruff.toml", "biome.json", ".eslintrc.json", "eslint.config.js", "eslint.config.mjs"}
	manifests      = []string{"Cargo.toml", "package.json"}
	lockfiles      = []string{"Cargo.lock", "yarn.lock", "package-lock.json", "pnpm-lock.yaml"}
	sourceSuffixes = []string{".go", ".rs", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".swift", ".kt", ".java", ".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".mm", ".sql", ".proto", ".sand", ".sh", ".css", ".scss", ".html"}
)

func isTestOrCI(name string) bool {
	if slices.ContainsFunc(ciPrefixes, func(prefix string) bool { return strings.HasPrefix(name, prefix) }) {
		return true
	}
	if testFile.MatchString(name) || toolingGlobs.MatchString(name) {
		return true
	}
	parts := strings.Split(name, "/")
	if slices.ContainsFunc(parts[:len(parts)-1], func(part string) bool { return slices.Contains(testDirs, part) }) {
		return true
	}
	suffix := suffixOf(name)
	return (suffix == ".yml" || suffix == ".yaml") && strings.Contains(path.Base(name), "buildkite")
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

func testOnlyOffender(patches map[string]*string) string {
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
