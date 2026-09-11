import { chromium } from 'playwright-core';
const b = await chromium.launch({ channel: 'chrome' });
const p = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const errs = [];
p.on('pageerror', e => errs.push(String(e)));
p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });

await p.goto('http://127.0.0.1:3100/', { waitUntil: 'networkidle' });
await p.evaluate(() => document.fonts.ready);
await p.waitForTimeout(3000);
await p.screenshot({ path: 'shots/v3-hero.png' });

// 领域卡片来自 GET /api/groups —— 能点到就说明活数据渲染出来了
await p.locator('#prefs button').first().waitFor({ timeout: 15000 });
const cards = await p.locator('#prefs button').allTextContents();
console.log('领域卡片:', cards.map(t => t.replace(/\s+/g, ' ').trim()));

await p.locator('#prefs button').first().click();
await p.locator('#list').waitFor({ timeout: 20000 });
await p.waitForTimeout(2500);
await p.locator('#list').scrollIntoViewIfNeeded();
await p.waitForTimeout(600);
await p.screenshot({ path: 'shots/v3-list.png' });

const counts = await p.locator('#list [class*="countV"]').allTextContents();
console.log('名单统计:', counts);
console.log('页面错误:', errs.length ? errs.slice(0,3) : 'none');
await b.close();
