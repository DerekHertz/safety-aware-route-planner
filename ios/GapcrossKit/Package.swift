// swift-tools-version: 6.0
// The app's logic, kept out of the Xcode project so `swift test` runs it on macOS without a simulator.
import PackageDescription

let package = Package(
    name: "GapcrossKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "GapcrossAPI", targets: ["GapcrossAPI"]),
        .library(name: "GapcrossCore", targets: ["GapcrossCore"]),
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-openapi-generator", from: "1.0.0"),
        .package(url: "https://github.com/apple/swift-openapi-runtime", from: "1.0.0"),
        .package(url: "https://github.com/apple/swift-openapi-urlsession", from: "1.0.0"),
    ],
    targets: [
        // Generated at build time from api/openapi.json (symlinked here): the only file the app
        // reads from the service. Request and response types are never hand-written.
        .target(
            name: "GapcrossAPI",
            dependencies: [
                .product(name: "OpenAPIRuntime", package: "swift-openapi-runtime"),
                .product(name: "OpenAPIURLSession", package: "swift-openapi-urlsession"),
            ],
            plugins: [.plugin(name: "OpenAPIGenerator", package: "swift-openapi-generator")]
        ),
        .target(name: "GapcrossCore", dependencies: ["GapcrossAPI"]),
        .testTarget(name: "GapcrossCoreTests", dependencies: ["GapcrossCore"], resources: [.copy("Fixtures")]),
    ]
)
