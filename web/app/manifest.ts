import type { MetadataRoute } from "next";

// Served at /manifest.webmanifest by Next's metadata file convention.
//
// NOTE: this file only creates the route — it does NOT inject the
// <link rel="manifest"> tag. That comes from `metadata.manifest` in layout.tsx,
// which defaults to null. Without both, the app is not installable and nothing
// warns you.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Safety-Aware Route Planner",
    short_name: "Safe Routes",
    description:
      "Driving routes that avoid unprotected left turns and uncontrolled crossings of busy streets",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#ffffff",
    theme_color: "#0f172a",
    icons: [
      // Deliberately pointing at files in public/ rather than at Next's
      // app/icon.png convention: that one is served from a hashed URL
      // (/icon?<generated>), which is fine inside a <link> tag Next writes
      // itself but fragile to hardcode in a hand-written manifest.
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      {
        // Android may crop to a circle, squircle or other shape; the artwork
        // in this one sits inside the 80% safe zone so nothing important is
        // lost.
        src: "/icon-512-maskable.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
