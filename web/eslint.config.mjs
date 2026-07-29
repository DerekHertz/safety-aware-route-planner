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
  // eslint-config-next 16 enables the React-Compiler-era `react-hooks` rules.
  // They originally flagged 7 sites; 2 are now fixed and the rest are held
  // here at `warn` so they stay visible without blocking CI. Every other file
  // still ERRORS on these rules, so no new violations can land.
  //
  // FIXED, and deliberately no longer listed:
  //   - react-hooks/refs in components/MapView.tsx. The "latest ref" pattern
  //     wrote refs during render, which is genuinely unsound under concurrent
  //     rendering: a discarded or replayed render would publish state that was
  //     never committed. Those writes moved into an effect. The rule is now
  //     enforced repo-wide.
  //
  // REMAINING, reviewed and intentional:
  //   - app/page.tsx x3, components/SearchBox.tsx, lib/useGeolocation.ts —
  //     all `set-state-in-effect`. Each is an effect synchronising with an
  //     external system that has no React-native equivalent: the Geolocation
  //     watch, localStorage read-after-hydration, and clearing stale geocoder
  //     results. Restructuring them is possible (useSyncExternalStore, as
  //     lib/useMediaQuery.ts now does) but it means rewriting the live GPS
  //     path, which is exactly the code being field-tested. Not worth the risk
  //     for a lint warning; revisit when that path changes for its own reasons.
  // ---------------------------------------------------------------------
  {
    files: [
      "app/page.tsx",
      "components/SearchBox.tsx",
      "lib/useGeolocation.ts",
    ],
    rules: {
      "react-hooks/set-state-in-effect": "warn",
    },
  },
]);
