// Package version exposes the build version. The release pipeline stamps it in
// with -ldflags; a `go install`ed binary falls back to module build info.
package version

import "runtime/debug"

// Set at build time via -ldflags (see Taskfile.yml and .goreleaser.yaml), e.g.
//
//	-X {{MODULE_PATH}}/internal/version.Version=v1.2.3
var (
	Version = "dev"
	Commit  = ""
	Date    = ""
)

// String returns the build version, preferring the ldflags-injected value and
// falling back to the module version recorded by `go install`.
func String() string {
	if Version != "dev" {
		return Version
	}
	if info, ok := debug.ReadBuildInfo(); ok {
		if v := info.Main.Version; v != "" && v != "(devel)" {
			return v
		}
	}
	return Version
}
