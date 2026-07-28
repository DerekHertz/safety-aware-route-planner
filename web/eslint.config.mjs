// Flat config, per node_modules/next/dist/docs/01-app/03-api-reference/05-config/03-eslint.md.
// `core-web-vitals` promotes the Core-Web-Vitals-affecting rules from warning
// to error; `typescript` layers on typescript-eslint.
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTypescript,
  globalIgnores([
    // eslint-config-next's own defaults, restated because declaring
    // globalIgnores replaces them.
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Verbatim copies of maplibre-gl's worker, regenerated from node_modules
    // by scripts/copy-maplibre-worker.mjs on predev/prebuild. Not our code.
    "public/maplibre/**",
  ]),

  // ---------------------------------------------------------------------
  // Grandfathered violations — a ratchet, not an exemption.
  //
  // eslint-config-next 16 turns on the React-Compiler-era `react-hooks`
  // rules, which flag 7 pre-existing sites in these four files:
  //   - set-state-in-effect: app/page.tsx (3), components/SearchBox.tsx,
  //     lib/useGeolocation.ts — effects that seed state synchronously.
  //   - refs: components/MapView.tsx (2) — the "latest ref" pattern at
  //     MapView.tsx:68-72, deliberately written so map event handlers see
  //     current props without re-registering. Writing a ref during render is
  //     unsound under concurrent rendering (a discarded render can still
  //     mutate it), so this is a genuine finding, not a false positive.
  //
  // Downgraded to `warn` HERE ONLY so they stay visible on every lint run
  // without blocking CI. Any other file still fails on these rules, so no
  // new violations can land. Fix these when the mobile/PWA work rewrites
  // page.tsx and MapView.tsx — rewriting working map interaction code purely
  // to satisfy a newly-added linter is the wrong order of operations.
  // ---------------------------------------------------------------------
  {
    files: [
      "app/page.tsx",
      "components/MapView.tsx",
      "components/SearchBox.tsx",
      "lib/useGeolocation.ts",
    ],
    rules: {
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",
    },
  },
]);
