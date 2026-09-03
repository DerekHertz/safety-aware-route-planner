import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin Turbopack's workspace root to THIS directory. Otherwise Next infers the
  // root from lockfiles, and a stray `package-lock.json` in the repo root (npm
  // writes an empty stub there if `npm install` is ever run from the root
  // instead of `web/`) makes it walk UP and treat the parent as the root. On a
  // Windows/WSL checkout on the D: drive that inferred root resolves to a
  // `D:\...` string which, joined relative to the cwd, gets created as literal
  // junk directories under `web/` (e.g. `web/D:\...\web/.next/dev`). Pinning the
  // root silences the multi-lockfile warning and stops the junk dirs at source.
  turbopack: {
    root: __dirname,
  },

  // Proxy API calls through the Next server so the whole app is single-origin.
  //
  // This is what makes phone testing over a tunnel workable. NEXT_PUBLIC_* is
  // inlined at BUILD time, so pointing the browser directly at the API would
  // mean rebuilding the front-end every time a tunnel hands out a new random
  // hostname. Rewrites are evaluated per-request instead, so one tunnel to
  // :3000 serves both the app and the API — no rebuild, and no CORS at all.
  //
  // Unused in production: when NEXT_PUBLIC_API_URL is set, lib/api.ts uses that
  // absolute URL and never touches /api.
  async rewrites() {
    const target = process.env.API_PROXY_TARGET ?? "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${target}/:path*` }];
  },
};

export default nextConfig;
