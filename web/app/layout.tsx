import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Safety-Aware Route Planner",
  description:
    "Route planner that penalizes unprotected lefts and uncontrolled crossings of busy streets",
  // REQUIRED for installability. app/manifest.ts creates the
  // /manifest.webmanifest route but does not emit the <link> tag — that is
  // this field, and it defaults to null.
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    title: "Safe Routes",
    statusBarStyle: "black-translucent",
  },
  // Stops iOS turning coordinate pairs in the UI into tappable "phone numbers".
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  // Next already emits width=device-width, initial-scale=1 by default; these
  // are restated because declaring the export replaces the default.
  width: "device-width",
  initialScale: 1,
  // Lets the layout extend under the notch/home indicator, which is what makes
  // env(safe-area-inset-*) meaningful — the bottom sheet relies on it.
  viewportFit: "cover",
  // Deliberately NOT maximumScale/userScalable: locking zoom is an
  // accessibility failure, and the 16px input rule below is the right fix for
  // iOS zoom-on-focus.
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0f172a" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
