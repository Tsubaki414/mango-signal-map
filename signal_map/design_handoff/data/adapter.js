// 数据适配层。页面只经此取数；接真实 API 时替换 load*() 实现，其余保持不变。
import { taxonomy, rootLibrary, project as demoProject, signals as demoSignals, DEMO_NOTICE } from './demo.js';

export async function loadProject(/* projectId */) {
  // API: GET /api/projects/:id
  return demoProject;
}
export async function loadKols(/* projectId */) {
  // API: GET /api/projects/:id/candidates  → KOL[] (含报价明细、档位、商务状态、标签)
  const res = await fetch(new URL('./demo-kols.json', import.meta.url));
  const raw = await res.json();
  return raw.map(k => ({ ...k, ...k._demo, demoFields: Object.keys(k._demo) }));
}
export async function loadSignals(projectId, state) {
  // API: GET /api/projects/:id/signals  → Signal[]；无数据返回 []
  if (state === 'none') return [];
  if (state === 'partial') return demoSignals.filter(s => s.verified).slice(0, 6);
  return demoSignals;
}
export function rootsFor(project) { return rootLibrary[project.domainGroup] || []; }
export { taxonomy, DEMO_NOTICE };

export const tierOf = id => taxonomy.tiers.find(t => t.id === id);
export const label = (list, id) => (taxonomy[list].find(x => x.id === id) || {}).label || id;

export function defaultPrefs(project) {
  return {
    markets: [...project.markets], languages: [], domains: [...project.domains], audiences: [],
    rootTypes: {}, // rootTypeId -> 'primary' | 'secondary' | 'skip'
    platforms: [], formats: [], budget: 'b2', tiers: [], goal: project.goal, brand: [], acceptUnverified: true,
    custom: { audiences: '', domains: '', roots: '' }, excluded: [],
  };
}

const inter = (a, b) => a.filter(x => b.includes(x));

// 项目适配度：相对当前偏好的整数分。每一项都可解释。
export function scoreKol(k, prefs, roots, signals) {
  const parts = [];
  const dom = prefs.domains.length ? inter(k.domains, prefs.domains).length / prefs.domains.length : 0.6;
  parts.push({ id: 'domain', label: '内容领域', w: 30, v: Math.min(1, dom * 1.4), note: inter(k.domains, prefs.domains).map(d => label('domains', d)).join('、') || '与所选领域无直接重合' });
  const aud = prefs.audiences.length ? inter(k.audiences, prefs.audiences).length / prefs.audiences.length : 0.6;
  parts.push({ id: 'audience', label: '目标人群', w: 20, v: Math.min(1, aud * 1.5), note: inter(k.audiences, prefs.audiences).map(a => label('audiences', a)).join('、') || '人群重合待确认' });
  let mk = 0.5;
  if (prefs.markets.length) { const m = inter(k.markets, prefs.markets).length > 0 || (k.markets.includes('global') && prefs.markets.some(x => x !== 'cn')); mk = m ? 1 : 0.1; }
  if (prefs.languages.length) { const l = inter(k.languages, prefs.languages.map(x => x === 'bi' ? 'zh' : x)).length > 0; mk = (mk + (l ? 1 : 0.2)) / 2; }
  parts.push({ id: 'market', label: '市场与语言', w: 15, v: mk, note: k.markets.map(m => label('markets', m)).join('/') + ' · ' + k.languages.map(l => label('languages', l)).join('/') });
  let pf = 0.7;
  if (prefs.platforms.length) pf = prefs.platforms.includes(k.platform) ? 1 : 0.15;
  if (prefs.formats.length) pf = (pf + (inter(k.formats, prefs.formats).length ? 1 : 0.3)) / 2;
  parts.push({ id: 'platform', label: '平台与形式', w: 15, v: pf, note: k.platform + ' · ' + k.formats.map(f => label('formats', f)).join('/') });
  const primary = Object.entries(prefs.rootTypes).filter(([, v]) => v === 'primary').map(([id]) => id);
  const secondary = Object.entries(prefs.rootTypes).filter(([, v]) => v === 'secondary').map(([id]) => id);
  const ks = signals.filter(s => s.kolId === k.id);
  const rootType = rid => (roots.find(rt => rt.people.some(p => p.id === rid)) || {}).id;
  const hitP = ks.filter(s => primary.includes(rootType(s.rootId))).length;
  const hitS = ks.filter(s => secondary.includes(rootType(s.rootId))).length;
  const rv = signals.length === 0 ? null : Math.min(1, hitP * 0.5 + hitS * 0.25 + (ks.length ? 0.15 : 0));
  parts.push({ id: 'root', label: 'Root 圈层 Signal', w: 10, v: rv == null ? 0.5 : rv, note: signals.length === 0 ? '关系数据待补充，不计入判断' : ks.length ? `${ks.length} 条已观察 Signal` : '暂无与所选 Root 的 Signal', pending: signals.length === 0 });
  const bp = budgetFit(k, prefs);
  parts.push({ id: 'budget', label: '预算匹配', w: 10, v: bp, note: (tierOf(k.tier) || {}).mark + ' 档 · 单条主内容' });
  const fit = Math.round(parts.reduce((s, p) => s + p.w * p.v, 0));
  const strengths = parts.filter(p => p.v >= 0.8 && !p.pending).map(p => p.label);
  const limits = parts.filter(p => p.v < 0.4 && !p.pending).map(p => p.label);
  // 双轴（仅当有 Signal 数据）
  const seen = signals.length ? clamp(Math.round(30 + ks.filter(s => s.type === 'follow').length * 20 + ks.length * 8 + (k.followers > 300000 ? 10 : 0))) : null;
  const engaged = signals.length ? clamp(Math.round(20 + ks.filter(s => ['reply', 'quote', 'co_appear'].includes(s.type)).reduce((a, s) => a + s.count * 12, 0) + (k.engagement.replyQuality > 50 ? 10 : 0))) : null;
  return { fit, parts, strengths, limits, seen, engaged, signals: ks };
}
const clamp = n => Math.max(5, Math.min(95, n));
function budgetFit(k, prefs) {
  if (prefs.tiers.length) return prefs.tiers.includes(k.tier) ? 1 : 0.3;
  const b = taxonomy.budgets.find(x => x.id === prefs.budget); const t = tierOf(k.tier);
  if (!b || !t) return 0.5;
  if (t.hi * 3 <= b.hi) return 1; if (t.lo > b.hi) return 0.1; return 0.6;
}

export function roleOf(k, score, prefs) {
  if (k.type === 'Media / Community Account' || k.formats.includes('podcast') || k.formats.includes('newsletter')) return { id: 'media', label: '媒体与长内容' };
  if (score.signals.length >= 2) return { id: 'bridge', label: '圈层桥梁' };
  if (k.audiences.includes('developers') || k.audiences.includes('researchers')) return { id: 'explain', label: '专业解释' };
  if (k.followers > 300000) return { id: 'amplify', label: '声量节点' };
  if (k.audiences.includes('consumers') || k.audiences.includes('early_adopters')) return { id: 'convert', label: '产品转化' };
  return { id: 'extend', label: '圈层扩展' };
}

export function matchKols(kols, prefs, roots, signals) {
  const approvedAvoid = (prefs.criteria || []).filter(c => c.status === 'approved' && (c.kind === 'avoid' || (c.kind === 'custom' && c.group === 'avoid'))).map(c => c.text);
  const approvedWant = (prefs.criteria || []).filter(c => c.status === 'approved' && (c.kind === 'want' || (c.kind === 'custom' && c.group === 'want'))).map(c => c.text);
  const has = (arr, kw) => arr.some(t => t.includes(kw));
  return kols.map(k => {
    const score = scoreKol(k, prefs, roots, signals);
    // 判别标准接入：认可的「不想要」排除或扣分，认可的「更想要」加分
    let rules = [];
    if (has(approvedAvoid, '官方公司号') && k.type === 'Marketing Account') rules.push({ text: '官方/营销账号 → 排除', excl: true });
    if (has(approvedAvoid, '泛工具号') && k.type === 'Marketing Account') rules.push({ text: '泛工具/流量型账号 → 排除', excl: true });
    if (has(approvedAvoid, '硬广') && k.risk.includes('promo_heavy')) rules.push({ text: '硬广痕迹重 → -15', d: -15 });
    if (has(approvedAvoid, '风险') && k.risk.length) rules.push({ text: '风险标签 → 排除', excl: true });
    if (has(approvedWant, '亲自试用') && k.formats.some(f => ['video', 'article', 'thread'].includes(f))) rules.push({ text: '长内容形式适合真实试用 → +5', d: 5 });
    if (has(approvedWant, '真实个人账号') && (k.type === 'KOL' || k.type === 'Top KOL' || k.type === 'KOC')) rules.push({ text: '真实个人账号 → +3', d: 3 });
    if (has(approvedWant, '可信权威') && score.signals.length) rules.push({ text: '与目标 Root 存在关系 → +5', d: 5 });
    if (has(approvedWant, '跨圈层放大') && k.followers > 300000) rules.push({ text: '大体量账号 → +5', d: 5 });
    const delta = rules.reduce((a, r) => a + (r.d || 0), 0); const excluded = rules.some(r => r.excl);
    score.fit = Math.max(0, Math.min(99, score.fit + delta)); score.rules = rules;
    const hardOk = !excluded && (!prefs.platforms.length || prefs.platforms.includes(k.platform)) && (prefs.acceptUnverified || k.quoteStatus === 'confirmed') && (!prefs.brand.includes('no_risk') || !k.risk.length) && !(prefs.excluded || []).includes(k.id);
    return { kol: k, score, role: roleOf(k, score, prefs), eligible: hardOk && score.fit >= 35 };
  }).sort((a, b) => b.score.fit - a.score.fit);
}

export function groupMatches(matches) {
  const el = matches.filter(m => m.eligible);
  const groups = [
    { id: 'top', label: '高度符合当前选择', items: el.filter(m => m.score.fit >= 70) },
    { id: 'bridge', label: '具有独特圈层价值', items: el.filter(m => m.score.fit < 70 && m.score.signals.length) },
    { id: 'value', label: '预算效率较高', items: el.filter(m => m.score.fit >= 55 && m.score.fit < 70 && m.kol.tier <= 2 && !m.score.signals.length) },
    { id: 'media', label: '媒体与播客', items: el.filter(m => m.role.id === 'media' && m.score.fit < 70 && m.score.fit >= 35 && !(m.score.fit >= 55 && m.kol.tier <= 2)) },
  ];
  const used = new Set(groups.flatMap(g => g.items.map(m => m.kol.id)));
  groups.push({ id: 'explore', label: '值得探索', items: el.filter(m => !used.has(m.kol.id)) });
  return groups.filter(g => g.items.length);
}

export function lineupSummary(selected, prefs, roots, signals) {
  const lo = selected.reduce((s, m) => s + (tierOf(m.kol.tier) || {}).lo || 0, 0);
  const hi = selected.reduce((s, m) => s + (tierOf(m.kol.tier) || {}).hi || 0, 0);
  const set = f => [...new Set(selected.flatMap(f))];
  const roleCount = {}; selected.forEach(m => { roleCount[m.role.label] = (roleCount[m.role.label] || 0) + 1; });
  const primary = Object.entries(prefs.rootTypes).filter(([, v]) => v !== 'skip' && v).map(([id]) => id);
  const rootType = rid => (roots.find(rt => rt.people.some(p => p.id === rid)) || {}).id;
  const sig = selected.flatMap(m => m.score.signals);
  const coveredTypes = new Set(sig.map(s => rootType(s.rootId)));
  const gaps = primary.filter(t => !coveredTypes.has(t));
  const byType = { follow: '当前关注', reply: '直接回复', quote: '引用转发', co_appear: '共同露出', media: '媒体连接' };
  const sigByType = {}; sig.forEach(s => { sigByType[byType[s.type]] = (sigByType[byType[s.type]] || 0) + 1; });
  return {
    count: selected.length, lo, hi, platforms: set(m => [m.kol.platform]), markets: set(m => m.kol.markets), languages: set(m => m.kol.languages),
    domains: set(m => m.kol.domains), audiences: set(m => m.kol.audiences), roles: roleCount, tiers: selected.map(m => m.kol.tier),
    contactable: selected.filter(m => m.commercial === 'contactable' || m.kol.commercial === 'contactable').length,
    unverified: selected.filter(m => m.kol.quoteStatus !== 'confirmed').length,
    signals: sig.length, verifiedSignals: sig.filter(s => s.verified).length, sigByType, rootsReached: [...new Set(sig.map(s => s.rootId))], coveredTypes: [...coveredTypes], gaps,
    audienceGaps: prefs.audiences.filter(a => !selected.some(m => m.kol.audiences.includes(a))),
  };
}

export function diffSummary(before, after) {
  const notes = [];
  const d = after.hi - before.hi;
  if (d) notes.push(d < 0 ? `预算上限减少 ${fmt(-d)}` : `预算上限增加 ${fmt(d)}`);
  const lostP = before.platforms.filter(p => !after.platforms.includes(p)); if (lostP.length) notes.push(`失去平台 ${lostP.join('、')}`);
  const gainP = after.platforms.filter(p => !before.platforms.includes(p)); if (gainP.length) notes.push(`新增平台 ${gainP.join('、')}`);
  const lostT = before.coveredTypes.filter(t => !after.coveredTypes.includes(t)); if (lostT.length) notes.push(`失去通往 ${lostT.length} 个目标圈层的唯一 Signal 路径`);
  const gainT = after.coveredTypes.filter(t => !before.coveredTypes.includes(t)); if (gainT.length) notes.push(`新增 ${gainT.length} 个圈层的 Signal 路径`);
  Object.keys(before.roles).forEach(r => { if (!after.roles[r]) notes.push(`「${r}」角色出现空缺`); });
  const ds = after.signals - before.signals; if (ds) notes.push(`${ds > 0 ? '+' : ''}${ds} 条关系 Signal`);
  return notes;
}

// 一句话说明这位创作者的内容是做什么的（演示：由标签生成；正式版由 Mango 数据库提供 contentLine 字段）
export function describeKol(k) {
  if (k.contentLine) return k.contentLine;
  const d = k.domains.map(x => label('domains', x));
  const f = k.formats.map(x => label('formats', x));
  const a = k.audiences.map(x => (taxonomy.audiences.find(o => o.id === x) || {}).label || x).map(x => x.split('与')[0]);
  const lang = k.languages.includes('zh') && k.languages.includes('en') ? '中英双语' : k.languages.includes('zh') ? '中文' : '英文';
  const kind = { 'Top KOL': '头部创作者', KOL: '创作者', KOC: '小体量创作者', 'Marketing Account': '营销账号', 'Media / Community Account': '媒体/社区账号', 'Community Leader': '社区领袖' }[k.type] || '创作者';
  return `${k.platform} 上的${lang}${kind}，以${f.slice(0, 2).join('与')}讲${d.slice(0, 2).join('、')}，读者主要是${a.slice(0, 2).join('与')}。`;
}

// 根据目的与预算生成「Mango 建议起点」阵容：广撒网 → 多人中低档；精准 → 少人高档 + 圈层桥梁
export function suggestLineup(matches, prefs, project) {
  const el = matches.filter(m => m.eligible); if (!el.length) return { ids: [], strategy: '', count: 0, lo: 0, hi: 0, note: '当前没有符合条件的候选。' };
  const goals = project.goals && project.goals.length ? project.goals : project.goal ? [project.goal] : [];
  const b = taxonomy.budgets.find(x => x.id === prefs.budget) || taxonomy.budgets[1];
  const broad = goals.includes('reach') || goals.includes('adoption');
  const deep = goals.includes('credibility') || goals.includes('investors');
  const strategy = broad && !deep ? 'broad' : deep && !broad ? 'deep' : 'mixed';
  const cfg = { broad: { target: 10, prefer: [1, 2, 2, 1, 2, 1, 2, 3, 1, 2], roles: ['amplify', 'convert', 'extend', 'explain'] },
                deep: { target: 6, prefer: [4, 3, 3, 2, 3, 2], roles: ['bridge', 'explain', 'media', 'amplify'] },
                mixed: { target: 8, prefer: [3, 2, 3, 2, 1, 2, 3, 1], roles: ['bridge', 'explain', 'amplify', 'media', 'convert'] } }[strategy];
  const cap = b.hi; const cost = m => (tierOf(m.kol.tier) || { lo: 0, hi: 0 });
  const picked = []; let lo = 0, hi = 0;
  const take = m => { const c = cost(m); picked.push(m); lo += c.lo; hi += c.hi; };
  cfg.roles.forEach(rid => { if (picked.length >= cfg.target) return; const m = el.find(x => x.role.id === rid && !picked.includes(x)); if (m && hi + cost(m).hi <= cap * 1.1) take(m); });
  cfg.prefer.forEach(tier => { if (picked.length >= cfg.target) return; const m = el.find(x => !picked.includes(x) && x.kol.tier === tier); if (m && hi + cost(m).hi <= cap * 1.1) take(m); });
  el.forEach(m => { if (picked.length >= cfg.target || picked.includes(m)) return; if (hi + cost(m).hi <= cap) take(m); });
  const roleStr = Object.entries(picked.reduce((a, m) => (a[m.role.label] = (a[m.role.label] || 0) + 1, a), {})).map(([k, v]) => `${v} 位${k}`).join('、');
  const why = strategy === 'broad' ? '你的目的偏向扩大声量与产品试用，所以建议人数多、以中低档为主，覆盖更多信息流。' : strategy === 'deep' ? '你的目的偏向行业可信度与投资人关注，所以建议人数精简、以头部与圈层桥梁为主。' : '你的目的兼顾可信度与声量，所以建议中高档搭配，兼顾深度与覆盖。';
  return { ids: picked.map(m => m.kol.id), strategy, count: picked.length, lo, hi, note: `${why}共 ${picked.length} 位（${roleStr}），预算 ${fmtRange(lo, hi)}，不超过你选的 ${b.label} 上限。` };
}

export const fitBand = fit => fit >= 78 ? { word: '高度符合', level: 4 } : fit >= 65 ? { word: '符合', level: 3 } : fit >= 50 ? { word: '部分符合', level: 2 } : { word: '边界人选', level: 1 };

export const fmt = n => n >= 1000 ? `$${Math.round(n / 1000)}k` : `$${n}`;
export const fmtRange = (lo, hi) => lo === hi ? fmt(lo) : `${fmt(lo)} – ${fmt(hi)}`;
export const compact = n => n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? Math.round(n / 1e3) + 'K' : String(n);

const KEY = 'mango-signal-map:';
export function saveState(pid, s) { try { localStorage.setItem(KEY + pid, JSON.stringify(s)); } catch (e) {} }
export function loadState(pid) { try { return JSON.parse(localStorage.getItem(KEY + pid) || 'null'); } catch (e) { return null; } }
export function listSaved() {
  const out = [];
  try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (!k || !k.startsWith(KEY)) continue; const v = JSON.parse(localStorage.getItem(k) || 'null'); if (v && v.project) out.push({ id: k.slice(KEY.length), token: v.project.token || null, name: v.project.name, view: v.view || 'configure', selected: ((v.plans && v.plans[v.plan || 'A'] && v.plans[v.plan || 'A'].selected) || []).length, at: v.at || 0 }); } } catch (e) {}
  return out.sort((a, b) => b.at - a.at);
}
