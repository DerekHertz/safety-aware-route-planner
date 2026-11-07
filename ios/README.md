# iOS app (S8)

A thin SwiftUI and MapKit client over the service. It reads only `api/openapi.json`.

- `GapcrossKit/` is a Swift package. `GapcrossAPI` is generated at build time from the contract
  (symlinked in as `Sources/GapcrossAPI/openapi.json`) by `swift-openapi-generator`.
  `GapcrossCore` holds the app's logic: crossing markers and counts, units, `Follower`,
  `SimulatedFeed` and `DriveSession`. Its tests run on macOS without a simulator.
- `Gapcross.xcodeproj` is the app target. Its sources live in `Gapcross/`, which Xcode syncs as a
  folder, so adding a file needs no project edit.

## Run

Start the service on recorded data from the repo root (`docker compose up --build`), then:

```bash
cd ios/GapcrossKit && swift test
```

```bash
cd ios && xcodebuild -project Gapcross.xcodeproj -scheme Gapcross \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' -skipPackagePluginValidation build
```

In Xcode, open `Gapcross.xcodeproj`, trust the OpenAPIGenerator plugin when asked, and run on a
simulator. The simulator reaches the Mac's service as `localhost:8000`; pass the launch argument
`-GapcrossServiceURL http://host:port` to point elsewhere.

After a contract change, re-record `GapcrossKit/Tests/GapcrossCoreTests/Fixtures/` (see its README).
