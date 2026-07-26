import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Safety-Aware Route Planner",
  description:
    "Route planner that penalizes unprotected lefts and uncontrolled crossings of busy streets",
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
