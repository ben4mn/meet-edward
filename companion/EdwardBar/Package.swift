// swift-tools-version:5.9
// EdwardBar — a menu-bar companion for Edward.
//
// Built with SwiftPM so it compiles with the Command Line Tools alone (no
// Xcode). `swift build -c release` produces .build/release/EdwardBar; copy it
// to any Mac you use and run it (see README.md for launch-at-login).
import PackageDescription

let package = Package(
    name: "EdwardBar",
    platforms: [.macOS(.v13)],
    targets: [
        // Pure logic: presence state from raw inputs, SSE line parsing, the
        // icon state machine. Tested without AppKit.
        .target(name: "EdwardBarCore"),
        .executableTarget(name: "EdwardBar", dependencies: ["EdwardBarCore"]),
        .testTarget(name: "EdwardBarCoreTests", dependencies: ["EdwardBarCore"]),
    ]
)
