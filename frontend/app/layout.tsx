import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Newsletter Digest",
  description: "Filter newsletters and turn them into a digest, story, and LinkedIn post.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
