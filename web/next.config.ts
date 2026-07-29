import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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
