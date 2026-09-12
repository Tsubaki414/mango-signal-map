import { chromium } from 'playwright-core';
const b = await chromium.launch({ channel: 'chrome' });
const p = await b.newPage({ viewport: { width: 1440, height: 950 } });
const errs = []; p.on('pageerror', e => errs.push(String(e)));
await p.goto('http://127.0.0.1:3100/', { waitUntil: 'networkidle' });
await p.evaluate(() => localStorage.clear());
await p.reload({ waitUntil: 'networkidle' });
await p.waitForTimeout(2500);

await p.locator('#prefs button').first().click();               // 选 AI
await p.locator('#targets').waitFor({ timeout: 25000 });
await p.waitForTimeout(2500);
console.log('02 圈层卡数量:', await p.locator('#targets article').count());
console.log('圈层标题:', (await p.locator('#targets h3').allTextContents()).join(' / '));
console.log('代表人物(第一张卡):', (await p.locator('#targets article').first().locator('button[class*="person"]').allTextContents()).slice(0,3).map(t=>t.replace(/\s+/g,' ').trim()));
await p.locator('#targets').scrollIntoViewIfNeeded(); await p.waitForTimeout(4500);
console.log('头像已加载:', await p.locator('#targets img').count(), '首字母兜底:', await p.locator('#targets span[aria-hidden]').count());
await p.screenshot({ path: 'shots/full-targets.png' });

// 设为重点
await p.locator('#targets button[class*="focusBtn"]').nth(2).click();
await p.waitForTimeout(3000);

await p.locator('#list').scrollIntoViewIfNeeded(); await p.waitForTimeout(1500);
console.log('名单卡数量:', await p.locator('#list article').count());
console.log('第一张卡:', (await p.locator('#list article').first().innerText()).replace(/\n+/g,' | ').slice(0,190));
await p.screenshot({ path: 'shots/full-list.png' });

// 保留两位 -> 出现 04 确认
await p.locator('#list article button:has-text("保留")').first().click();
await p.waitForTimeout(900);
await p.locator('#list article button:has-text("保留")').first().click();
await p.waitForTimeout(2000);
const hasConfirm = await p.locator('#confirm').count();
console.log('04 确认区出现:', hasConfirm > 0);
if (hasConfirm) { await p.locator('#confirm').scrollIntoViewIfNeeded(); await p.waitForTimeout(900); await p.screenshot({ path: 'shots/full-confirm.png' }); }

// 点开目标人物
await p.locator('#targets').scrollIntoViewIfNeeded(); await p.waitForTimeout(600);
await p.locator('#targets button[class*="person"]').first().click();
await p.waitForTimeout(1500);
console.log('人物面板打开:', await p.locator('[role="dialog"]').count() > 0);
await p.screenshot({ path: 'shots/full-person.png' });
console.log('页面错误:', errs.length ? errs.slice(0,2) : 'none');
await b.close();
