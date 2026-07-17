# Swift Stack: Rationale and Knobs

Why each swift/swift-app choice is what it is, and what to adjust when a repo
outgrows the default.

## Stack at a Glance

| Concern | Choice | Why |
|---|---|---|
| Project shape | `swift` = SPM package/CLI; `swift-app` = Xcode iOS app | the two shapes real Swift repos take; pick by deliverable, not preference |
| CLI framework | swift-argument-parser (`AsyncParsableCommand`) | Apple's cobra/Click equivalent: subcommands, `--help`, `--version` for free |
| Tests | Swift Testing (`@Test` / `#expect`), never XCTest | free functions, parameterized cases, exact-value assertions; XCTest is legacy |
| Formatting | SwiftFormat (nicklockwood) | the de facto formatter; mechanical churn stays out of review |
| Lint | SwiftLint, minimal config | default rules + `force_unwrapping`; warnings only, nothing blocks |
| Build/test/run driver | `swift build`/`swift test` (package); XcodeBuildMCP or `xcodebuild` (app) | agents drive Xcode through the xcodebuildmcp CLI, not raw `xcrun`/`simctl` |
| Language mode | Swift 6, complete strict concurrency (app) | data-race safety proved at compile time from day one |

## Project Layout (swift)

```
Package.swift            # tools-version {{SWIFT_TOOLS_VERSION}}; three targets
Sources/<Module>/        # the library — ALL logic lives here
Sources/<name>/          # the executable — a thin ArgumentParser shell
Tests/<Module>Tests/     # Swift Testing against the library
```

**Logic in the library, not the executable.** The executable target holds the
command tree and nothing else; every behavior it invokes lives in the library
where `@testable import <Module>` can reach it. Tests never import an executable
target (SPM allows it only awkwardly, and `@main` complicates it). A command body
longer than argument parsing plus one library call is logic in the wrong target.

**Naming.** The executable product and the repo share a name (`swift run <name>`);
`MODULE_NAME` is the UpperCamelCase library module — the same split as python's
dist/package. They must differ: SPM target names are unique, and a collision fails
`swift build` with a confusing manifest error (the scaffolder rejects it upfront).
With feature `release`, the executable-equals-repo-name rule is also the calling
contract of the shared release workflow.

**`Package.resolved` is the lockfile.** `swift build` writes it; commit it like
`go.sum`/`uv.lock`.

## Project Layout (swift-app)

```
<name>.xcodeproj/
├── project.pbxproj                     # the ONE committed project file
└── xcshareddata/xcschemes/<name>.xcscheme  # shared scheme (CI needs it)
<name>/                                 # app sources — file-system-synchronized
│   ├── App/                            # @main App + views
│   └── Assets.xcassets/                # AppIcon + AccentColor
<name>Tests/                            # Swift Testing target — synchronized too
```

The project uses **file-system-synchronized root groups** (`objectVersion = 77`,
Xcode 16+): the two source folders sync automatically, so adding a `.swift` file
is just creating it — **no pbxproj edit, ever**. The committed pbxproj carries
fixed synthetic UUIDs (`0000…B1` for the app target, `0000…C1` for tests) that the
shared scheme's `BlueprintIdentifier`s reference — never regenerate the project or
accept an Xcode "upgrade project" prompt on the scaffolded file; both would churn
identifiers for zero benefit.

Build settings worth knowing (set in the pbxproj, from the room-scan lineage):
`SWIFT_VERSION = 6.0` + `SWIFT_STRICT_CONCURRENCY = complete` (Swift 6 language
mode, full data-race checking), `GENERATE_INFOPLIST_FILE = YES` (no Info.plist
file — Xcode synthesizes it; add `INFOPLIST_KEY_*` settings for usage
descriptions), `PRODUCT_MODULE_NAME = <Module>` (so tests `@testable import
<Module>` instead of a name-mangled module), `TARGETED_DEVICE_FAMILY = "1,2"`
(iPhone + iPad).

## Signing (swift-app)

The scaffold deliberately ships **no `DEVELOPMENT_TEAM`** — a committed team id is
a personal credential in a template. `CODE_SIGN_STYLE = Automatic` stays, so:
simulator builds and tests work unsigned out of the box (CI passes
`CODE_SIGNING_ALLOWED=NO` for belt-and-braces); the first **device** deploy needs
a one-click team pick in Xcode (Signing & Capabilities → Team), which writes the
team id locally. That's expected, not a scaffold bug.

## Starter Anatomy (swift)

`Root` is an `AsyncParsableCommand` with `CommandConfiguration(commandName:,
abstract:, version:, subcommands:)` — async-native from day one, and `--version`
comes free from the `version:` parameter (stamping it at release time is covered
in `reference/swift-ci-and-release.md`). The one `hello` subcommand calls
`helloMessage(name:)` in the library; replace both together when real commands
arrive.

## Logging

Diagnostics go through `os.Logger` with per-module categories on one subsystem —
never `print`:

```swift
import os

extension Logger {
    static let capture = Logger(subsystem: "com.<user>.<name>", category: "Capture")
}
```

Use `privacy: .public` interpolation for values that must appear in `log stream`
output, and stream a device/simulator's logs with
`log stream --predicate 'subsystem == "com.<user>.<name>"'`. This is the Swift
analogue of go's `internal/log` slog setup.

## Testing

Swift Testing only — zero XCTest. Free `@Test` functions (no class wrapper),
`#expect` for assertions, `#require` for unwrap-or-fail, `@Test(arguments:)` for
parameterized cases with per-case expected values. Package tests run with
`swift test`; app tests need a simulator destination:
`xcodebuild test -project <name>.xcodeproj -scheme <name> -destination
'platform=iOS Simulator,name=iPhone 17'` (any installed simulator works — list
them with `xcrun simctl list devices`, or drive it all through XcodeBuildMCP).

Sensor/hardware paths that return nothing in the Simulator (ARKit, RoomPlan,
CoreBluetooth…) get a `docs/DEVICE_TESTING.md` companion checklist instead of
untestable unit tests — keep the pure logic (geometry, encoding, persistence)
unit-tested and enumerate the on-device verification steps for the rest.

## Mechanical Linting

This stack uses **SwiftFormat by nicklockwood** — binary `swiftformat`, brew
formula `swiftformat`, config `.swiftformat`. Apple ships an unrelated
`swift-format` in recent toolchains (config `.swift-format`). Don't mix them up
and don't "fix" the config filename.

SwiftFormat owns formatting (including comma placement — SwiftLint's
`trailing_comma` rule is disabled because it fights SwiftFormat's default);
SwiftLint adds the judgment rules, minimally: defaults plus opt-in
`force_unwrapping` (the STYLEGUIDE ban made visible). Everything surfaces as
warnings — nothing style-related blocks a commit or fails a build; CI runs
`swiftformat --lint .` (that one does fail on unformatted code, which is the
point) and `swiftlint --quiet`.

The prek hooks are deliberately `repo: local` + `language: system` against the
brew-installed binaries: the upstream SwiftFormat/SwiftLint pre-commit hooks build
the tools from source via SPM on first run (minutes-long). `brew install
swiftformat swiftlint` once per machine, `uvx prek install` once per clone.

To exempt tests from the force-unwrap ban, drop a nested `Tests/.swiftlint.yml`
(or `<name>Tests/.swiftlint.yml`) with `opt_in_rules: []` — SwiftLint merges
nested configs directory-wise.

## XcodeBuildMCP

Both layers wire the `xcodebuildmcp` MCP server into `.mcp.json` via the
`cc-skills:mcp-swift` fragment in the `.claude/fragments/.mcp.json/` layout, and
vendor the `xcodebuildmcp-cli` project skill (`.claude/skills/xcodebuildmcp-cli/`). It's the
sanctioned driver for build/test/run/simulator/device/log/UI-automation work —
help-first discovery (`xcodebuildmcp tools`), instead of memorized `xcodebuild`
/`xcrun`/`simctl` incantations. AGENTS.md requires reading the skill before the
first XcodeBuildMCP call. Install: `brew tap getsentry/xcodebuildmcp && brew
install xcodebuildmcp` (or `npm i -g xcodebuildmcp`). Its local session state
(`.xcodebuildmcp/`) is gitignored — machine-specific device/simulator ids.

## App + Companion Package (the room-scan shape)

A repo that needs both — an iOS app plus a shared wire-format library and a
desktop CLI — starts as `swift-app`, then adds a local SPM package by hand:

1. Create `<PackageDir>/Package.swift` with the library (+ optional executables
   and their tests), following the swift layer's logic-in-library shape.
2. In Xcode, File → Add Package Dependencies → Add Local…, pick `<PackageDir>`,
   and link the library product into the app target. This writes an
   `XCLocalSwiftPackageReference` + `packageProductDependencies` into the pbxproj
   (one deliberate edit — the sync groups still cover all source files).
3. The app imports the library; the package's own tests run with
   `swift test --package-path <PackageDir>`.
