package daemon

import "github.com/yasyf/daemonkit/version"

// buildVersion is the release version stamped at build time via
//
//	-ldflags "-X {{MODULE_PATH}}/internal/daemon.buildVersion=vX.Y.Z"
//
// (see Taskfile.yml and the release pipeline). It defaults to "dev"; an unstamped
// dev build reports version.DevString of the binary's mtime instead, so a
// dev-wins takeover still evicts an older dev binary.
var buildVersion = "dev"

// selfVersion resolves this daemon's build version exactly once and memoizes it:
// a stamped release passes through, an unstamped "dev" build becomes
// version.DevString(mtime). A rebuild on disk never changes a running daemon's
// reported version.
func selfVersion() string { return version.Running(buildVersion) }
