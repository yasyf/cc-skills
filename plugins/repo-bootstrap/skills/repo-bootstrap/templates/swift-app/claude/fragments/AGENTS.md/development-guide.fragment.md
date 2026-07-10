# {{PROJECT_NAME}} Development Guide

{{DESCRIPTION}}

## Repository Structure

```
{{PROJECT_NAME}}/
├── {{PROJECT_NAME}}.xcodeproj/  # Synced-folder project — one committed project.pbxproj
├── {{PROJECT_NAME}}/            # App sources (file-system-synchronized group)
│   ├── App/                     # Entry point + SwiftUI views
│   └── Assets.xcassets/         # App icon + accent color
├── {{PROJECT_NAME}}Tests/       # Swift Testing target (synchronized group)
├── AGENTS.md                    # This file — shared conventions
├── README.md                    # Project overview
└── STYLEGUIDE.md                # Swift style rules for this repo
```

Sources live in file-system-synchronized folders: add a `.swift` file by creating
it under `{{PROJECT_NAME}}/` (app) or `{{PROJECT_NAME}}Tests/` (tests) — no
`.pbxproj` edit. The app targets iOS {{IOS_DEPLOYMENT_TARGET}} (Swift 6 language
mode, complete strict concurrency). The project file uses fixed synthetic UUIDs —
never regenerate it or accept an Xcode "upgrade" of it.
