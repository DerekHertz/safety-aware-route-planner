// Copy MapLibre's worker (and the shared chunk it imports) into /public.
//
// Why this exists: MapLibre derives its worker URL from `import.meta.url`
// and bails to an empty string when that isn't an http(s) URL:
//
//     function () { let e = import.meta.url;
//       if (!/^https?:/.test(e)) return ``;   // <- Turbopack lands here
//       return new URL('./maplibre-gl-worker.mjs', e).href }
//
// It then calls `new Worker("")`, which resolves to the page's own HTML: a
// live Worker object that never answers and never errors, so every tile sits
// in state "loading" forever and the map renders only its background.
//
// Serving the real worker from /public and pointing setWorkerUrl at it side-
// steps the bundler entirely. Copying at pre-dev/pre-build time (rather than
// committing a vendored copy) keeps it in lockstep with the installed
// maplibre-gl version.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "node_modules", "maplibre-gl", "dist");
const dest = join(here, "..", "public", "maplibre");

mkdirSync(dest, { recursive: true });
for (const file of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
  copyFileSync(join(src, file), join(dest, file));
}
console.log(`[maplibre] worker assets copied to public/maplibre`);
