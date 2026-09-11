import type { Metadata, Viewport } from "next";
import Script from "next/script";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mango Signal Map",
  description: "让重要的人，看见你。",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

/* The exact font request from the handoff prototype. Weight 300 is the body
 * default and 1,700 (italic bold) exists only for the title lead-in, so both
 * must be present or the brand 标题句法 silently falls back to a synthesised
 * oblique. */
const FONTS =
  "https://fonts.googleapis.com/css2?family=Inter:ital,wght@0,300;0,400;0,500;1,700" +
  "&family=Noto+Sans+SC:wght@300;400;500;700" +
  "&family=JetBrains+Mono:wght@400;500&display=swap";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link rel="stylesheet" href={FONTS} />
      </head>
      <body>
        {/* Reused verbatim from the handoff -- see public/vendor/README.
         * beforeInteractive so <gradient-waves> is defined by the time the hero
         * paints; a late upgrade shows one frame of flat gradient. */}
        <Script src="/vendor/motion.js" strategy="beforeInteractive" />
        <Script src="/vendor/gradient-waves.js" strategy="beforeInteractive" />
        {children}
      </body>
    </html>
  );
}
