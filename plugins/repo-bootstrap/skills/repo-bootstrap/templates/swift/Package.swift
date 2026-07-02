// swift-tools-version: {{SWIFT_TOOLS_VERSION}}
import PackageDescription

/// Logic lives in the {{MODULE_NAME}} library; the executable target is a thin
/// ArgumentParser shell. Tests import the library, never the executable.
let package = Package(
    name: "{{PROJECT_NAME}}",
    platforms: [.macOS(.v15)],
    products: [
        .library(name: "{{MODULE_NAME}}", targets: ["{{MODULE_NAME}}"]),
        .executable(name: "{{PROJECT_NAME}}", targets: ["{{PROJECT_NAME}}"]),
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-argument-parser", from: "1.5.0"),
    ],
    targets: [
        .target(name: "{{MODULE_NAME}}"),
        .executableTarget(
            name: "{{PROJECT_NAME}}",
            dependencies: [
                "{{MODULE_NAME}}",
                .product(name: "ArgumentParser", package: "swift-argument-parser"),
            ]
        ),
        .testTarget(name: "{{MODULE_NAME}}Tests", dependencies: ["{{MODULE_NAME}}"]),
    ]
)
