// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "NavigationProtocol",
    platforms: [.macOS(.v13)],
    products: [.library(name: "NavigationProtocol", targets: ["NavigationProtocol"]),
               .executable(name: "ProtocolChecks", targets: ["ProtocolChecks"])],
    targets: [
        .target(name: "NavigationProtocol", path: "NavigationCompanion/Protocol"),
        .executableTarget(name: "ProtocolChecks", dependencies: ["NavigationProtocol"], path: "HostChecks"),
        .testTarget(name: "NavigationProtocolTests", dependencies: ["NavigationProtocol"], path: "Tests")
    ]
)
