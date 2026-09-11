// v3 数据层：目标人物库 + 共同关注演示信号 + 排序/汇总逻辑。
// 演示标注：共同关注与互动记录为演示数据，用于展示产品形态；真实数据接入后替换 buildEdges()。
import { taxonomy } from './demo.js';
export { taxonomy };

export const DEMO_NOTE = '演示数据：共同关注与互动记录为演示标注，用于展示产品形态；报价档位来自 Mango 历史报价记录，以复核为准。';

export const groups = [
  { id: 'ai', label: 'AI / 科技', desc: 'Agent、基础模型、开发者工具、企业与消费 AI', domains: ['ai_agent', 'foundation_model', 'dev_tools', 'ai_coding', 'enterprise_ai', 'consumer_ai', 'creative_ai'] },
  { id: 'crypto', label: '加密 / Web3', desc: 'Crypto、DeFi、基础设施、链上生态', domains: ['crypto', 'defi', 'web3_infra'] },
  { id: 'finance', label: '金融科技', desc: 'FinTech、Trading、美股与投资', domains: ['fintech', 'trading', 'us_stocks'] },
];

export const circles = {
  ai: [
    { id: 'founders', label: '顶级科技创始人', value: '公共讨论 · 声量',
      who: 'AI 圈公共议题的第一层声量在这里形成。目标不是让他们接单，而是让你的内容出现在他们会顺手转发、会接一句话的位置。',
      pick: '优先挑他们共同关注的账号，以及历史上被他们回复过的人；内容偏短、有观点、有 demo。' },
    { id: 'researchers', label: 'AI 研究者', value: '行业背书 · 可信度',
      who: '一个说法在技术圈能不能立住，看这群人认不认。他们不带货，但他们的认可会被整个圈子反复引用。',
      pick: '优先挑做技术拆解、有真实工程经验的创作者，排除资讯搬运号与纯 prompt 内容。' },
    { id: 'vc', label: '风险投资人', value: '投资人关注 · BD 连接',
      who: '被他们看到之后，常见的结果不是一次转发，而是一次 warm intro 或一通电话。',
      pick: '优先挑投资人长期在看的播客、newsletter 与创始人型账号，而不是泛流量号。' },
    { id: 'devs', label: '开发者领袖', value: '产品采用',
      who: '工具选型的口碑在他们那里形成。一句"我自己用了"，胜过十次曝光。',
      pick: '优先挑会把产品真的跑一遍的创作者，有开源或实操内容的人排在前面。' },
    { id: 'media', label: '科技媒体与播客', value: '媒体路径 · 二跳',
      who: '长内容是进入投资人与从业者信息流最稳的通道，起效慢但留存久。',
      pick: '优先挑有公开赞助或选题入口的媒体与播客，商务路径清晰、可排期。' },
  ],
  crypto: [
    { id: 'builders', label: '协议建设者', value: '行业背书',
      who: '生态叙事由他们定调，被引用一次，胜过投十个号。',
      pick: '优先挑长期做机制解释、被协议方引用过的创作者。' },
    { id: 'cvc', label: '加密投资人', value: '融资与合作',
      who: '融资、上所与合作的判断在这一层形成，看见之后往往走私下渠道。',
      pick: '优先挑研究型账号与深度长文作者，排除喊单与短期热点号。' },
    { id: 'traders', label: '活跃交易者', value: '流动性注意力',
      who: '市场情绪与话题热度的起点，短期声量在这里放大最快。',
      pick: '优先挑观点鲜明、更新高频、评论区活跃的交易类账号。' },
    { id: 'cnzh', label: '中文加密圈', value: '中文市场',
      who: '中文市场的信息入口，扩散主要靠转载与社群，而不是单条曝光。',
      pick: '优先挑中文原生的媒体与研究账号，能承接专访与转载。' },
  ],
  finance: [
    { id: 'analysts', label: '研究与分析师', value: '专业可信度',
      who: '机构读者的第一手来源，一个议题从这里进入正式讨论。',
      pick: '优先挑做数据与结构化分析的账号，宁少勿滥。' },
    { id: 'fintechs', label: '金融科技创始人', value: '同行认可',
      who: '同行认不认，决定你的产品叙事能不能立住。',
      pick: '优先挑做产品与商业模式拆解的创作者，而不是行业资讯号。' },
    { id: 'ftraders', label: '活跃交易者', value: '交易社群',
      who: '散户社群的情绪与话题起点，转化最快也最短暂。',
      pick: '优先挑高频、贴近盘面、内容可操作的账号。' },
    { id: 'inst', label: '机构与交易所', value: 'BD 与合作',
      who: '渠道与合作的决策层。公开内容在这里只用于建立认知，推进在私下。',
      pick: '优先挑行业媒体与机构侧栏目，为后续 BD 铺垫。' },
  ],
};

const P = (id, name, handle, role, circle, group, why, markets, audiences) => ({ id, name, handle, role, circle, group, why, markets, audiences });

export const targets = [
  // ── AI · 顶级科技创始人
  P('t_elon', 'Elon Musk', '@elonmusk', 'xAI · Tesla · X', 'founders', 'ai', '极端放大节点，被卷入讨论能把话题带出 AI 圈层。', ['us', 'global'], ['founders', 'consumers']),
  P('t_sama', 'Sam Altman', '@sama', 'OpenAI CEO', 'founders', 'ai', 'frontier AI 叙事中心，决定行业注意力的二跳方向。', ['us', 'global'], ['founders', 'investors']),
  P('t_amasad', 'Amjad Masad', '@amasad', 'Replit CEO', 'founders', 'ai', 'AI-native 开发者叙事的活跃发声者，评论区常下场。', ['us'], ['developers', 'founders']),
  P('t_levelsio', 'Pieter Levels', '@levelsio', 'Indie founder', 'founders', 'ai', '独立开发者圈的高信任节点，产品叙事传播极快。', ['global'], ['founders', 'early_adopters']),
  P('t_alexandr', 'Alexandr Wang', '@alexandr_wang', 'Scale AI', 'founders', 'ai', '连接企业 AI 与政策圈的少数创始人。', ['us'], ['enterprise', 'investors']),
  // ── AI · 研究者
  P('t_karpathy', 'Andrej Karpathy', '@karpathy', 'AI researcher / educator', 'researchers', 'ai', '技术圈公认权威，可见面直接提升 builder 对机制的信任。', ['us', 'global'], ['developers', 'researchers']),
  P('t_ylecun', 'Yann LeCun', '@ylecun', 'Turing Award · AI debate', 'researchers', 'ai', '公开辩论型节点，带来学术侧关注。', ['us', 'eu'], ['researchers']),
  P('t_lilian', 'Lilian Weng', '@lilianweng', 'Agent research · AI safety', 'researchers', 'ai', 'agent 研究的高信任节点，低频但精选转发。', ['us'], ['researchers', 'developers']),
  P('t_jimfan', 'Jim Fan', '@DrJimFan', 'NVIDIA · embodied AI', 'researchers', 'ai', '研究与产品之间的翻译者，长文传播力强。', ['us'], ['researchers', 'developers']),
  P('t_sarahookr', 'Sara Hooker', '@sarahookr', 'AI research lead', 'researchers', 'ai', '研究社区组织者，覆盖非美研究者网络。', ['global', 'eu'], ['researchers']),
  // ── AI · VC
  P('t_saranormous', 'Sarah Guo', '@saranormous', 'Founder, Conviction', 'vc', 'ai', '活跃 AI 投资人，播客与 X 双通道，常回复创始人。', ['us'], ['investors', 'founders']),
  P('t_venturetwins', 'Justine Moore', '@venturetwins', 'Partner, a16z', 'vc', 'ai', '消费 AI 趋势的 VC 侧放大节点，高频引用新产品。', ['us'], ['investors', 'consumers']),
  P('t_eladgil', 'Elad Gil', '@eladgil', 'Investor / operator', 'vc', 'ai', '创业者广泛信任的投资人，长文观点影响面广。', ['us'], ['investors', 'founders']),
  P('t_pmarca', 'Marc Andreessen', '@pmarca', 'a16z co-founder', 'vc', 'ai', '同时覆盖 VC 圈与科技舆论场的超级节点。', ['us'], ['investors', 'founders']),
  P('t_natfriedman', 'Nat Friedman', '@natfriedman', 'Investor · ex-GitHub CEO', 'vc', 'ai', '开发者工具与 AI 投资的交点，口碑极高。', ['us'], ['developers', 'investors']),
  // ── AI · 开发者领袖
  P('t_simonw', 'Simon Willison', '@simonw', 'AI engineering · tools', 'devs', 'ai', '开发者信任度极高的工具评论者，亲自试用并长文记录。', ['us', 'global'], ['developers']),
  P('t_swyx', 'swyx', '@swyx', 'AI Engineer · Latent.Space', 'devs', 'ai', 'AI Engineer 社区核心，播客主，常发出邀约。', ['us'], ['developers', 'founders']),
  P('t_hwchase', 'Harrison Chase', '@hwchase17', 'LangChain founder', 'devs', 'ai', 'agent 框架生态中心，转发生态项目。', ['us'], ['developers']),
  P('t_jeremyphoward', 'Jeremy Howard', '@jeremyphoward', 'fast.ai', 'devs', 'ai', '教育与实践社区的长期权威。', ['global'], ['developers', 'researchers']),
  P('t_omarsar', 'Elvis Saravia', '@omarsar0', 'DAIR.AI', 'devs', 'ai', 'AI 工程内容的高频聚合者，覆盖学习者与从业者。', ['global'], ['developers', 'researchers']),
  // ── AI · 媒体与播客
  P('t_dwarkesh', 'Dwarkesh Patel', '@dwarkesh_sp', 'Dwarkesh Podcast', 'media', 'ai', '深度 AI 访谈，VC 与研究者高频收听。', ['us'], ['investors', 'researchers']),
  P('t_jackclark', 'Jack Clark', '@jackclarksf', 'Import AI · policy', 'media', 'ai', 'AI 政策与研究 newsletter 权威，精选引用。', ['us'], ['researchers', 'enterprise']),
  P('t_bhorowitz', 'Ben Thompson', '@benthompson', 'Stratechery', 'media', 'ai', '战略分析的行业标准读物，覆盖高管与投资人。', ['us', 'global'], ['enterprise', 'investors']),
  P('t_alexkantro', 'Alex Kantrowitz', '@kantrowitz', 'Big Technology', 'media', 'ai', '科技行业访谈与 newsletter，媒体二跳入口。', ['us'], ['tech_media', 'enterprise']),
  P('t_rowancheung', 'Rowan Cheung', '@rowancheung', 'The Rundown AI', 'media', 'ai', '覆盖面最广的 AI 日报之一，扩散速度快。', ['global'], ['early_adopters', 'consumers']),
  // ── Crypto · 建设者
  P('t_vitalik', 'Vitalik Buterin', '@VitalikButerin', 'Ethereum', 'builders', 'crypto', '生态最高信任节点，长文与回复都会被广泛引用。', ['global'], ['developers', 'community']),
  P('t_sreeram', 'Sreeram Kannan', '@sreeramkannan', 'EigenLayer', 'builders', 'crypto', '基础设施叙事的核心解释者。', ['us', 'global'], ['developers', 'investors']),
  P('t_dabit3', 'Nader Dabit', '@dabit3', 'Web3 developer relations', 'builders', 'crypto', '开发者教育与生态连接点。', ['global'], ['developers']),
  P('t_sassal', 'sassal.eth', '@sassal0x', 'The Daily Gwei', 'builders', 'crypto', '以太坊社区的日更叙事者。', ['global'], ['community', 'traders']),
  // ── Crypto · 投资人
  P('t_hasu', 'Hasu', '@hasufl', 'Flashbots · research', 'cvc', 'crypto', '研究型投资人，深度回复带来实质讨论。', ['global'], ['investors', 'researchers']),
  P('t_cdixon', 'Chris Dixon', '@cdixon', 'a16z crypto', 'cvc', 'crypto', '加密叙事的机构侧代表声音。', ['us'], ['investors', 'founders']),
  P('t_smyyguy', 'Mike Dudas', '@mdudas', '6th Man Ventures', 'cvc', 'crypto', '连接媒体与投资的活跃节点。', ['us'], ['investors', 'tech_media']),
  // ── Crypto · 交易者
  P('t_cobie', 'Cobie', '@cobie', 'Trader · UpOnly', 'traders', 'crypto', '交易圈意见形成点，讥讽式引用传播极广。', ['global'], ['traders']),
  P('t_hsaka', 'Hsaka', '@HsakaTrades', 'Trader', 'traders', 'crypto', '交易社群高信任度账号。', ['global'], ['traders']),
  P('t_gainzy', 'Gainzy', '@gainzy222', 'Trader', 'traders', 'crypto', '高活跃度交易叙事者。', ['global'], ['traders', 'community']),
  // ── Crypto · 中文圈
  P('t_cnbtc', '中文加密媒体主编（示例）', '@—', '中文媒体', 'cnzh', 'crypto', '中文加密市场的信息入口，专访与转载路径清晰。', ['cn', 'hk'], ['community', 'traders']),
  P('t_cnkol', '中文研究型 KOL（示例）', '@—', '中文研究', 'cnzh', 'crypto', '中文圈层的深度内容来源。', ['cn'], ['researchers', 'traders']),
  // ── Finance · 分析师
  P('t_lisaabramowicz', 'Lisa Abramowicz', '@lisaabramowicz1', 'Bloomberg', 'analysts', 'finance', '机构信息流的重要入口，宏观议题设定者。', ['us'], ['finance_pros', 'investors']),
  P('t_carlquintanilla', 'Carl Quintanilla', '@carlquintanilla', 'CNBC', 'analysts', 'finance', '市场叙事的广播节点。', ['us'], ['finance_pros', 'traders']),
  P('t_biancoresearch', 'Jim Bianco', '@biancoresearch', 'Bianco Research', 'analysts', 'finance', '深度宏观研究，专业读者密度高。', ['us'], ['finance_pros', 'investors']),
  // ── Finance · 金融科技创始人
  P('t_patrickc', 'Patrick Collison', '@patrickc', 'Stripe', 'fintechs', 'finance', '金融科技同行认可度最高的声音之一。', ['us', 'global'], ['founders', 'developers']),
  P('t_maxlevchin', 'Max Levchin', '@mlevchin', 'Affirm', 'fintechs', 'finance', '支付与信贷叙事的资深节点。', ['us'], ['founders', 'enterprise']),
  P('t_immad', 'Immad Akhund', '@immad', 'Mercury', 'fintechs', 'finance', '创业者社群中的高互动创始人。', ['us'], ['founders', 'early_adopters']),
  // ── Finance · 交易者与机构
  P('t_unusual_whales', 'unusual_whales', '@unusual_whales', 'Market data', 'ftraders', 'finance', '散户交易社群的高频聚合账号。', ['us'], ['traders', 'consumers']),
  P('t_zerohedge', 'zerohedge', '@zerohedge', 'Market commentary', 'ftraders', 'finance', '争议型放大节点，触达广但需控制角度。', ['global'], ['traders']),
  P('t_saylor', 'Michael Saylor', '@saylor', 'Strategy', 'inst', 'finance', '连接机构资金与公开叙事的少数人物。', ['us', 'global'], ['investors', 'enterprise']),
  P('t_cathiedwood', 'Cathie Wood', '@CathieDWood', 'ARK Invest', 'inst', 'finance', '成长股与创新叙事的机构侧代表。', ['us'], ['investors', 'consumers']),
];

export const targetsById = Object.fromEntries(targets.map(t => [t.id, t]));

// ── 共同关注与互动（演示）。真实数据接入后整体替换。
const EDGE_TYPES = [
  { id: 'cofollow', label: '共同关注', weight: 1 },
  { id: 'reply', label: '公开回复', weight: 1.6 },
  { id: 'quote', label: '引用转发', weight: 1.5 },
  { id: 'co_appear', label: '同场露出', weight: 1.3 },
];
const DATES = ['2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08'];
const hash = s => { let x = 2166136261; for (const c of String(s)) { x ^= c.charCodeAt(0); x = Math.imul(x, 16777619); } return x >>> 0; };

export function buildEdges(kols) {
  return kols.map(k => {
    const pool = targets.filter(t => t.group === k.group);
    const edges = [];
    if (!pool.length) return { ...k, edges };
    const byCircle = {};
    pool.forEach(t => { (byCircle[t.circle] = byCircle[t.circle] || []).push(t); });
    const cids = Object.keys(byCircle);
    const seed = hash(k.id);
    // 每个 KOL 有一个主圈层（约八成连接集中在此）+ 一个次圈层，偶尔一条跨圈层的边。
    const primary = cids[seed % cids.length];
    const secondary = cids[(seed >>> 5) % cids.length];
    const plan = [];
    const nPrim = 2 + (seed % 2);                       // 2–3 条主圈层
    for (let i = 0; i < nPrim; i++) plan.push(primary);
    if (secondary !== primary && seed % 3 !== 0) plan.push(secondary);
    if (seed % 7 === 0) plan.push(cids[(seed >>> 11) % cids.length]);
    const push = t => {
      if (!t || edges.some(e => e.targetId === t.id)) return;
      const s = hash(k.id + t.id);
      const audMatch = t.audiences.some(x => (k.audiences || []).includes(x));
      const typ = EDGE_TYPES[(s >>> 3) % (audMatch ? 4 : 2)];
      edges.push({ targetId: t.id, type: typ.id, label: typ.label, weight: typ.weight, date: DATES[(s >>> 7) % DATES.length], count: 1 + ((s >>> 11) % 3), verified: (s % 5) !== 0 });
    };
    plan.forEach((cid, i) => { const g = byCircle[cid] || []; push(g[hash(k.id + cid + i) % g.length]); });
    return { ...k, edges };
  });
}

// ── 目标人物筛选：领域 + 人群 + 市场
export function targetsFor(prefs) {
  const g = prefs.group;
  let list = targets.filter(t => t.group === g);
  if (prefs.circle) list = list.filter(t => t.circle === prefs.circle);
  const score = t => {
    let s = 0;
    if (prefs.audiences.length) s += t.audiences.filter(a => prefs.audiences.includes(a)).length * 2;
    if (prefs.markets.length) s += t.markets.filter(m => prefs.markets.includes(m)).length;
    return s;
  };
  return list.map(t => ({ ...t, _s: score(t) })).sort((a, b) => b._s - a._s);
}

// 客户跳过目标人物时：系统按圈层默认各取代表人物
export function defaultTargets(prefs) {
  const per = {};
  targetsFor({ ...prefs, circle: null }).forEach(t => { (per[t.circle] = per[t.circle] || []).push(t); });
  return Object.values(per).flatMap(a => a.slice(0, 2)).map(t => t.id);
}

export const FORMATS = [
  { id: 'post', label: '单条 Post', mult: 1 },
  { id: 'thread', label: 'Thread', mult: 1.6 },
  { id: 'spaces', label: 'Spaces', mult: 1.3 },
];
export const tierOf = id => taxonomy.tiers.find(t => t.id === id);

export function overlapOf(kol, targetIds) {
  const set = new Set(targetIds);
  return (kol.edges || []).filter(e => set.has(e.targetId));
}

// ── 排序：共同关注为第一依据，其后是内容/人群/市场契合与预算
// 保留名单的成员始终参与评分，即使当前目标人物选择下没有公开连接记录
export const circleOfTarget = id => (targetsById[id] || {}).circle;
export const groupTargetIds = group => targets.filter(t => t.group === group).map(t => t.id);

// 排序：候选池 = 与该领域任一目标人物有公开连接的创作者（不随客户操作收窄）。
// focus 是客户标记的重点圈层，只做加权重排，从不过滤。
function rankAll(kols, prefs, focus) {
  const ids = groupTargetIds(prefs.group);
  const F = new Set(focus || []);
  return kols.map(k => {
    const ov = overlapOf(k, ids);
    const w = e => e.weight * (e.verified ? 1 : 0.6) * (F.has(circleOfTarget(e.targetId)) ? 2.2 : 1);
    const reach = ov.reduce((s, e) => s + w(e), 0);
    const reachN = Math.min(1, reach / 6);
    const dom = prefs.domains.length ? (k.domains || []).filter(d => prefs.domains.includes(d)).length / prefs.domains.length : 0.55;
    const aud = prefs.audiences.length ? (k.audiences || []).filter(x => prefs.audiences.includes(x)).length / prefs.audiences.length : 0.55;
    const mk = prefs.markets.length ? ((k.markets || []).some(m => prefs.markets.includes(m)) || (k.markets || []).includes('global') ? 1 : 0.15) : 0.6;
    const bg = taxonomy.budgets.find(x => x.id === prefs.budget); const t = tierOf(k.tier) || {};
    const bud = !bg ? 0.6 : t.hi * 3 <= bg.hi ? 1 : t.lo > bg.hi ? 0.15 : 0.7;
    const circles = [...new Set(ov.map(e => circleOfTarget(e.targetId)))].filter(Boolean);
    const hit = circles.filter(c => F.has(c));
    const focusScore = ov.filter(e => F.has(circleOfTarget(e.targetId))).reduce((s, e) => s + e.weight * (e.verified ? 1 : 0.6), 0);
    const parts = [
      { id: 'reach', label: '进入目标圈层的机会', w: 40, v: reachN, note: ov.length ? `与 ${ov.length} 位目标人物有公开连接` : '暂无公开连接记录' },
      { id: 'domain', label: '内容方向契合', w: 22, v: Math.min(1, dom * 1.35), note: (k.domains || []).map(d => lbl('domains', d)).join('、') },
      { id: 'audience', label: '面向人群契合', w: 18, v: Math.min(1, aud * 1.45), note: (k.audiences || []).map(x => lbl('audiences', x)).join('、') },
      { id: 'market', label: '市场与语言', w: 10, v: mk, note: (k.markets || []).map(m => lbl('markets', m)).join('/') },
      { id: 'budget', label: '预算匹配', w: 10, v: bud, note: `${(t.mark || '')} 档 · 单条主内容` },
    ].filter(p => !p.skip);
    const fit = Math.round(parts.reduce((s, p) => s + p.w * p.v, 0) / parts.reduce((s, p) => s + p.w, 0) * 100);
    const focusNote = !F.size ? '' : hit.length ? `命中重点：${hit.map(c => circleLabel(prefs.group, c)).join('、')}` : '';
    return { kol: k, overlap: ov, circles, focusHit: hit.length, focusScore, focusNote, fit, parts, band: band(fit) };
  });
}
export const circleLabel = (group, id) => ((circles[group] || []).find(c => c.id === id) || {}).label || id;
export function rankKols(kols, prefs, focus) {
  return rankAll(kols, prefs, focus)
    .filter(m => m.overlap.length > 0)
    .sort((a, b) => b.focusScore - a.focusScore || b.fit - a.fit || b.overlap.length - a.overlap.length);
}
export const scoreOnly = (kols, prefs, focus) => rankAll(kols, prefs, focus);
const lbl = (list, id) => (taxonomy[list].find(x => x.id === id) || {}).label || id;
export const label = lbl;
export const band = fit => fit >= 78 ? { word: '强连接', level: 4 } : fit >= 64 ? { word: '路径清晰', level: 3 } : fit >= 50 ? { word: '可选择性补充', level: 2 } : { word: '下一步可拓展', level: 1 };

const HAN = '\\u4e00-\\u9fa5';
const NB = '\u00A0';
// 数字与量词之间用不断行空格（「8 位」不能拆）；字母与汉字之间用普通空格，保留断行机会。
const RE_HD = new RegExp(`([${HAN}]) ?([0-9])`, 'g');
const RE_DH = new RegExp(`([0-9]) ?([${HAN}])`, 'g');
const RE_HL = new RegExp(`([${HAN}]) ?([A-Za-z@$])`, 'g');
const RE_LH = new RegExp(`([A-Za-z%)\\]]) ?([${HAN}])`, 'g');
export const sp = s => String(s)
  .replace(RE_HD, `$1${NB}$2`).replace(RE_DH, `$1${NB}$2`)
  .replace(RE_HL, '$1 $2').replace(RE_LH, '$1 $2')
  .replace(new RegExp(`${NB} | ${NB}|${NB}{2,}`, 'g'), NB)
  .replace(/ {2,}/g, ' ');

const VERB = { cofollow: '关注', reply: '回复', quote: '引用', co_appear: '同场出现' };
export const nb = s => String(s).replace(/ /g, NB); // 人名内部不断行
const nameList = (ids, total) => {
  const ns = ids.slice(0, 2).map(id => nb((targetsById[id] || {}).name || '')).filter(Boolean);
  return ns.join('、') + (total > 2 ? `等 ${total} 位` : '');
};

// ── 推荐理由：随客户选择重写。主动语态、两句以内。
export function reasonFor(m, prefs) {
  const ov = m.overlap, k = m.kol, out = [];
  const trans = ov.filter(e => e.type === 'reply' || e.type === 'quote');
  const shared = ov.filter(e => e.type === 'co_appear');
  const follows = ov.filter(e => e.type === 'cofollow');
  if (trans.length) {
    const verbs = [...new Set(trans.map(e => VERB[e.type]))].join('或');
    out.push(`${nameList(trans.map(e => e.targetId), trans.length)}${verbs}过他的内容。`);
  } else if (shared.length) {
    out.push(`与 ${nameList(shared.map(e => e.targetId), shared.length)}同场出现过。`);
  } else if (follows.length) {
    out.push(`${nameList(follows.map(e => e.targetId), follows.length)}都在关注他。`);
  }
  const d = (k.domains || []).filter(x => prefs.domains.includes(x)).map(x => lbl('domains', x));
  const a = (k.audiences || []).filter(x => prefs.audiences.includes(x)).map(x => lbl('audiences', x));
  if (d.length && a.length) out.push(`写${d.join('与')}，读者以${a.join('、')}为主。`);
  else if (d.length) out.push(`内容集中在${d.join('与')}。`);
  else if (a.length) out.push(`读者以${a.join('、')}为主。`);
  else if (!out.length) out.push(`面向${lbl('markets', (k.markets || [])[0] || 'global')}读者的 X 创作者。`);
  return sp(out.join(''));
}

export function budgetOf(selected) {
  let lo = 0, hi = 0;
  selected.forEach(s => { const t = tierOf(s.kol.tier) || { lo: 0, hi: 0 }; const f = FORMATS.find(x => x.id === s.format) || FORMATS[0]; lo += t.lo * f.mult; hi += t.hi * f.mult; });
  return { lo: Math.round(lo), hi: Math.round(hi) };
}
export const fmt = n => n >= 1000 ? `$${Math.round(n / 1000)}k` : `$${Math.round(n)}`;
export const fmtRange = (lo, hi) => lo === hi ? fmt(lo) : `${fmt(lo)} – ${fmt(hi)}`;
export const compact = n => n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? Math.round(n / 1e3) + 'K' : String(n);
export const avatarUrl = handle => handle && handle !== '@—' ? `https://unavatar.io/x/${handle.replace('@', '')}` : '';

export function understandingText(prefs, focus, selected) {
  const g = groups.find(x => x.id === prefs.group);
  const cs = circles[prefs.group] || [];
  const mk = prefs.markets.map(m => lbl('markets', m)).join('、');
  const auRaw = prefs.audiences.map(a => lbl('audiences', a));
  const au = auRaw.slice(0, auRaw.some(x => x.includes('与')) ? 1 : 2).join('、');
  const dm = prefs.domains.slice(0, 2).map(d => lbl('domains', d)).join('、');
  const fl = (focus || []).map(id => (cs.find(c => c.id === id) || {}).label).filter(Boolean);
  const s1 = `你要在${g ? g.label : '所选领域'}方向被${au || '目标人群'}认真对待${mk ? `，主战场是${mk}市场` : ''}${dm ? `，叙事落在${dm}` : ''}。`;
  const s2 = fl.length
    ? `名单覆盖 ${cs.length} 个目标圈层，其中${fl.join('、')}标记为本轮重点，相关创作者已排在前面。`
    : `名单覆盖全部 ${cs.length} 个目标圈层，按整体连接强度排序。`;
  const s3 = selected.length ? `目前保留 ${selected.length} 位，预算区间 ${fmtRange(...Object.values(budgetOf(selected)))}。` : '';
  return sp(s1 + s2 + s3);
}

const KEY = 'msm-v3:';
export const save = (id, s) => { try { localStorage.setItem(KEY + id, JSON.stringify(s)); } catch (e) {} };
export const load = id => { try { return JSON.parse(localStorage.getItem(KEY + id) || 'null'); } catch (e) { return null; } };
export async function loadKols() {
  const res = await fetch(new URL('./kols-x.json', import.meta.url));
  const raw = await res.json();
  return buildEdges(raw.map(k => ({ ...k, ...k._demo })));
}
