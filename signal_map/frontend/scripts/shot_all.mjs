import { chromium } from 'playwright-core';
const b = await chromium.launch({ channel: 'chrome' });
const p = await b.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = []; p.on('pageerror', e => errs.push(String(e)));
await p.goto('http://127.0.0.1:3100/', { waitUntil: 'networkidle' });
await p.evaluate(() => localStorage.clear());
await p.reload({ waitUntil: 'networkidle' });
await p.waitForTimeout(2500);

await p.locator('#prefs button').first().click();     // 选 AI
await p.waitForTimeout(1200);
// 揭示动画
const reveal = await p.locator('div:has-text("正在生成你的名单")').count();
console.log('揭示动画出现:', reveal > 0);
await p.screenshot({ path: 'shots/all-reveal.png' });
await p.mouse.click(800, 500);                        // 跳过
await p.waitForTimeout(1500);

console.log('六组偏好:', (await p.locator('#prefs [class*="PrefGroups"] [class*="k_"], #prefs div[class*="row"] > div[class*="k"]').allTextContents()).slice(0,8));
await p.locator('#prefs').scrollIntoViewIfNeeded(); await p.waitForTimeout(600);
await p.screenshot({ path: 'shots/all-prefs.png' });

console.log('右栏存在:', await p.locator('aside[data-screen-label="实时反馈"]').count() > 0);
console.log('右栏数字:', await p.locator('aside[data-screen-label="实时反馈"] div').nth(1).innerText().catch(()=>'-'));
console.log('待核查入口:', await p.locator('input[aria-label="待核查的目标人物"]').count() > 0);

// Mango 建议起点
await p.locator('#list').scrollIntoViewIfNeeded(); await p.waitForTimeout(1200);
const sugg = await p.locator('button:has-text("采用这个起点")').count();
console.log('建议起点:', sugg > 0);
if (sugg) { await p.locator('button:has-text("采用这个起点")').click(); await p.waitForTimeout(2500); }
console.log('已保留:', await p.locator('#list article[class*="kept"]').count());
console.log('路径图:', await p.locator('#list svg + button, #list [class*="PathGraph"]').count() > 0 ? 'yes' : (await p.locator('text=注意力路径').count() > 0 ? 'yes' : 'no'));
await p.locator('text=注意力路径').scrollIntoViewIfNeeded().catch(()=>{});
await p.waitForTimeout(900);
await p.screenshot({ path: 'shots/all-graph.png' });
console.log('换一个按钮:', await p.locator('button:has-text("换一个")').count());
console.log('页面错误:', errs.length ? errs.slice(0,2) : 'none');
await b.close();
