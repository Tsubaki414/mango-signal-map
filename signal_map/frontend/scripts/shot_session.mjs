import { chromium } from 'playwright-core';
const b = await chromium.launch({ channel: 'chrome' });
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
const p = await ctx.newPage();
const errs = []; p.on('pageerror', e => errs.push(String(e)));

await p.goto('http://127.0.0.1:3100/', { waitUntil: 'networkidle' });
await p.locator('#prefs button').first().waitFor({ timeout: 20000 });
await p.locator('#prefs button').first().click();          // 选 AI
await p.locator('#list').waitFor({ timeout: 25000 });
await p.waitForTimeout(2000);
const sid = await p.evaluate(() => localStorage.getItem('mango-signal-map:session'));
const before = await p.locator('#list [class*="countV"]').allTextContents();
console.log('会话 id (服务端签发):', sid);
console.log('选择后名单:', before);

// —— 关掉页面，重新打开同一条链接 ——
await p.close();
const p2 = await ctx.newPage();
p2.on('pageerror', e => errs.push(String(e)));
await p2.goto('http://127.0.0.1:3100/', { waitUntil: 'networkidle' });
await p2.waitForTimeout(4000);
const sid2 = await p2.evaluate(() => localStorage.getItem('mango-signal-map:session'));
const restored = await p2.locator('#prefs [class*="restored"]').allTextContents();
const after = await p2.locator('#list [class*="countV"]').allTextContents();
console.log('重开后 id 相同:', sid === sid2);
console.log('恢复提示:', restored);
console.log('重开后名单:', after);
await p2.screenshot({ path: 'shots/v3-restored.png' });
console.log('页面错误:', errs.length ? errs.slice(0,2) : 'none');
await b.close();
