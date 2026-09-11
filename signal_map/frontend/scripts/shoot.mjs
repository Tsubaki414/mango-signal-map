/* Render the running app and save comparison shots.
 *
 * 924x540 is not an arbitrary size: the handoff's screenshots/ are natively
 * 924x540, so this is the only viewport at which a side-by-side comparison is
 * meaningful. (The handoff README calls them "1440 宽" -- they are not.)
 *
 *   node scripts/shoot.mjs [baseUrl]
 */
// playwright-core, not playwright: the browser binary is supplied below, so
// pulling a second ~150MB Chromium at install time buys nothing.
import { chromium } from "playwright-core";
import { mkdir } from "node:fs/promises";

const BASE = process.argv[2] || "http://localhost:3100";
const OUT = new URL("../shots/", import.meta.url).pathname;

await mkdir(OUT, { recursive: true });

const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({
  viewport: { width: 924, height: 540 },
  deviceScaleFactor: 1,
});

await page.goto(BASE, { waitUntil: "networkidle" });

// Webfonts decide the title's width, so a shot taken before they land compares
// a fallback face against the reference and every measurement is wrong.
await page.evaluate(() => document.fonts.ready);

// The ring self-draws over 2.4s and the entrance stagger ends at ~1.0s.
await page.waitForTimeout(3200);

await page.screenshot({ path: `${OUT}home-924x540.png` });
await page.screenshot({ path: `${OUT}home-full.png`, fullPage: true });

await browser.close();
console.log(`wrote ${OUT}home-924x540.png and home-full.png`);
