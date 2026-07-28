// Fail the build if the MapLibre worker assets are missing, stale, or no
// longer where the source says they are.
//
// This guards the failure mode described at length in copy-maplibre-worker.mjs:
// a missing or wrong worker does NOT throw. MapLibre constructs a Worker that
// never answers, every tile stays in state "loading", and the map renders as a
// blank background colour. There is no error in the console, no failed request,
// nothing in the server log. It looks like a styling bug and it ships.
//
// Three distinct ways that can happen, one check each:
//
//   1. The copy never ran — `npm ci --ignore-scripts`, or a host that invokes
//      `next build` directly instead of `npm run build`, skipping `prebuild`.
//   2. The copy ran once and went stale — a `maplibre-gl` version bump changes
//      dist/, but public/maplibre/ still holds the old files. A mere existence
//      check passes here, which is exactly why sizes are compared. Dependabot
//      will eventually produce this PR.
//   3. Someone renamed the public path in MapView.tsx without moving the file,
//      or vice versa.
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const web = join(here, "..");
const WORKER_FILES = ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"];

const problems = [];

// 1 + 2: present, non-empty, and byte-identical in size to the installed copy.
for (const file of WORKER_FILES) {
  const dest = join(web, "public", "maplibre", file);
  const src = join(web, "node_modules", "maplibre-gl", "dist", file);

  if (!existsSync(dest)) {
    problems.push(
      `public/maplibre/${file} is missing. The prebuild hook did not run — ` +
        `invoke \`npm run copy:maplibre-worker\` explicitly in the build command.`,
    );
    continue;
  }
  const destSize = statSync(dest).size;
  if (destSize === 0) {
    problems.push(`public/maplibre/${file} is empty.`);
    continue;
  }
  if (!existsSync(src)) {
    problems.push(
      `node_modules/maplibre-gl/dist/${file} is missing — run npm ci.`,
    );
    continue;
  }
  const srcSize = statSync(src).size;
  if (destSize !== srcSize) {
    problems.push(
      `public/maplibre/${file} is stale: ${destSize} bytes vs ${srcSize} in ` +
        `node_modules. maplibre-gl was probably upgraded without re-running ` +
        `copy:maplibre-worker.`,
    );
  }
}

// 3: the path MapView actually asks for must be the path that exists.
const mapView = join(web, "components", "MapView.tsx");
if (existsSync(mapView)) {
  const source = readFileSync(mapView, "utf8");
  const match = source.match(/setWorkerUrl\(\s*["'`]([^"'`]+)["'`]\s*\)/);
  if (!match) {
    problems.push(
      `components/MapView.tsx no longer calls setWorkerUrl(). Without it ` +
        `MapLibre falls back to its bundler-derived URL, which is the blank-map bug.`,
    );
  } else {
    const referenced = join(web, "public", match[1].replace(/^\//, ""));
    if (!existsSync(referenced)) {
      problems.push(
        `MapView.tsx points setWorkerUrl at "${match[1]}", but no such file ` +
          `exists under public/.`,
      );
    }
  }
}

// The build itself produced output — catches this script being run against a
// tree that was never built.
if (!existsSync(join(web, ".next", "BUILD_ID"))) {
  problems.push(".next/BUILD_ID is missing — `next build` did not complete.");
}

if (problems.length > 0) {
  console.error("[assert-build-assets] FAILED:\n");
  for (const p of problems) console.error(`  - ${p}\n`);
  process.exit(1);
}
console.log(
  "[assert-build-assets] worker assets present, current, and referenced correctly",
);
