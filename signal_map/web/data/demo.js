// 演示数据（DEMO）。所有 _demo 字段为前端演示补充，非 Mango 真实数据；报价档位由真实导出的历史报价推导。
export const DEMO_NOTICE = '演示数据：报价档位来自 Mango 历史报价记录（有效性未核验）；市场/语言/领域/受众/关系 Signal 为演示标注。';

export const taxonomy = {
  markets: [
    { id: 'us', label: '美国', en: 'United States' },
    { id: 'cn', label: '中国内地', en: 'Mainland China' },
    { id: 'hk', label: '香港及大中华', en: 'HK & Greater China' },
    { id: 'eu', label: '欧洲', en: 'Europe' },
    { id: 'global', label: '全球英文', en: 'Global (EN)' },
    { id: 'cross', label: '中英跨境', en: 'Cross-border ZH/EN' },
  ],
  languages: [
    { id: 'en', label: '英文' }, { id: 'zh', label: '中文' }, { id: 'bi', label: '双语' },
  ],
  domains: [
    { id: 'ai_agent', label: 'AI Agent', group: 'ai' }, { id: 'foundation_model', label: '基础模型', group: 'ai' },
    { id: 'dev_tools', label: '开发者工具', group: 'ai' }, { id: 'ai_coding', label: 'AI 编程', group: 'ai' },
    { id: 'enterprise_ai', label: '企业 AI', group: 'ai' }, { id: 'consumer_ai', label: '消费 AI', group: 'ai' },
    { id: 'creative_ai', label: '创意 AI', group: 'ai' },
    { id: 'fintech', label: '金融科技', group: 'finance' }, { id: 'trading', label: 'Trading', group: 'finance' },
    { id: 'us_stocks', label: '美股与投资', group: 'finance' },
    { id: 'crypto', label: 'Crypto', group: 'crypto' }, { id: 'defi', label: 'DeFi', group: 'crypto' },
    { id: 'web3_infra', label: 'Web3 基础设施', group: 'crypto' },
  ],
  audiences: [
    { id: 'founders', label: '创始人与产品建造者', sub: '正在构建或使用同类产品的创始人和产品负责人', why: '高度相关的同类影响，加速产品可信度在创业圈内的传播' },
    { id: 'investors', label: '投资人与行业研究员', sub: 'VC、LP、行业分析师，持续关注新兴技术动向', why: '进入投资人信息流，可能带来 warm intro 与融资关注' },
    { id: 'developers', label: '开发者与技术意见领袖', sub: '工程师、技术博主、开源社区核心参与者', why: '驱动产品试用，开发者认可能触发技术社区扩散' },
    { id: 'researchers', label: '研究者与学术圈', sub: '高校与实验室研究员、论文作者', why: '让讨论具体、可信，带来学术侧背书' },
    { id: 'enterprise', label: '企业采购与决策者', sub: 'CTO、IT 负责人、企业数字化管理层', why: '直接影响 B2B 采购决策链，适合希望进入企业客户视野的项目' },
    { id: 'tech_media', label: '科技媒体与播客主持人', sub: '科技垂类媒体编辑、播客主持、深度内容创作者', why: '进入长内容信息流，形成二跳传播路径' },
    { id: 'finance_pros', label: '金融从业者', sub: '买方研究、资管、金融科技从业者', why: '适合有金融属性的产品，建立专业可信度' },
    { id: 'traders', label: '金融投资者与交易者', sub: '加密投资者、量化交易者、金融科技爱好者', why: '适合有代币或金融属性的产品，扩大在全球投资社区的认知' },
    { id: 'consumers', label: '普通消费者', sub: '对新产品好奇的大众用户', why: '拉动下载与注册，适合消费级产品' },
    { id: 'early_adopters', label: '早期使用者', sub: 'Product Hunt / HN 社区、乐于尝鲜的技术用户', why: '形成首批真实使用反馈与口碑' },
    { id: 'community', label: '社区参与者', sub: 'Discord / Telegram / 微信群活跃成员', why: '维持热度与讨论密度' },
  ],
  platforms: [
    { id: 'X', label: 'X' }, { id: 'YouTube', label: 'YouTube' }, { id: 'Instagram', label: 'Instagram' },
    { id: 'TikTok', label: 'TikTok' }, { id: 'Podcast', label: '播客' }, { id: 'Newsletter', label: 'Newsletter' },
    { id: 'Bilibili', label: 'Bilibili' }, { id: 'WeChat', label: '微信公众号' },
  ],
  formats: [
    { id: 'post', label: '单条帖子' }, { id: 'thread', label: 'Thread' }, { id: 'quote', label: '引用转发' },
    { id: 'video', label: '视频' }, { id: 'short', label: '短视频' }, { id: 'article', label: '长文' },
    { id: 'spaces', label: 'Spaces' }, { id: 'podcast', label: '播客' }, { id: 'newsletter', label: 'Newsletter' },
  ],
  goals: [
    { id: 'credibility', label: '行业可信度', desc: '让专业圈层认真讨论你的产品。' },
    { id: 'reach', label: '扩大声量', desc: '让更多目标人群第一次听说你。' },
    { id: 'adoption', label: '产品试用', desc: '把人带到产品里，形成早期使用。' },
    { id: 'investors', label: '投资人关注', desc: '进入投资人和研究员的信息流。' },
  ],
  // 价格档位：按单条主内容 USD 价分档；客户端只显示档位与隐藏区间
  tiers: [
    { id: 1, mark: '$', lo: 150, hi: 500, label: '入门' },
    { id: 2, mark: '$$', lo: 500, hi: 2000, label: '中腰部' },
    { id: 3, mark: '$$$', lo: 2000, hi: 8000, label: '头部' },
    { id: 4, mark: '$$$$', lo: 8000, hi: 20000, label: '顶级' },
  ],
  budgets: [
    { id: 'b1', label: '$10k 以下', lo: 3000, hi: 10000 },
    { id: 'b2', label: '$10k – 30k', lo: 10000, hi: 30000 },
    { id: 'b3', label: '$30k – 80k', lo: 30000, hi: 80000 },
    { id: 'b4', label: '$80k 以上', lo: 80000, hi: 200000 },
  ],
  kolTypes: { 'Top KOL': '头部 KOL', KOL: 'KOL', KOC: 'KOC', 'Marketing Account': '营销账号', 'Media / Community Account': '媒体/社区', 'Community Leader': '社区领袖', Unknown: '待标注' },
  brandPrefs: [
    { id: 'credible', label: '专业可信' }, { id: 'technical', label: '技术深入' }, { id: 'accessible', label: '易于理解' },
    { id: 'opinionated', label: '观点鲜明' }, { id: 'review', label: '产品测评' }, { id: 'no_hard_sell', label: '避免过度营销' },
    { id: 'no_risk', label: '避免高风险账号' },
  ],
};

// Root 库：按领域分套。项目 domainGroup 决定加载哪套。
export const rootLibrary = {
  ai: [
    { id: 'rt_founders', label: '顶级科技创始人', why: '决定 AI 圈公共议题的第一层声量。目标不是让他们接单，而是让内容进入他们会看到、会回复的语境。', value: '公共讨论 · 声量', people: [
      { id: 'r_elon', name: 'Elon Musk', handle: '@elonmusk', role: 'xAI / Tesla / X', why: '极端放大节点；被卷入讨论能把话题带出 AI 小圈层。', behavior: '回复 + 转发放大型；被短句、反问、现实影响和平台生态话题触发。' },
      { id: 'r_sama', name: 'Sam Altman', handle: '@sama', role: 'OpenAI CEO', why: 'frontier AI 叙事中心；决定行业注意力和二跳讨论方向。', behavior: '偏 quote / 广播；被重大行业节点、创业者叙事触发。' },
      { id: 'r_pmarca', name: 'Marc Andreessen', handle: '@pmarca', role: 'a16z co-founder', why: 'AI techno-optimism 超级节点，同时覆盖 VC 圈与科技舆论场。', behavior: '偏观点放大；吃鲜明 thesis 与反共识。' },
      { id: 'r_amasad', name: 'Amjad Masad', handle: '@amasad', role: 'Replit CEO', why: 'AI-native 开发者叙事的活跃发声者。', behavior: '评论区活跃，会回复 builder。' },
    ]},
    { id: 'rt_researchers', label: 'AI 研究者', why: '让讨论变得可信、具体、有技术含量。', value: '行业背书 · 可信度', people: [
      { id: 'r_karpathy', name: 'Andrej Karpathy', handle: '@karpathy', role: 'AI researcher / educator', why: '技术圈公认权威；可见面提升 builder 对产品机制的信任。', behavior: '评论区下场型；吃高质量 technical observation 与 demo。' },
      { id: 'r_ylecun', name: 'Yann LeCun', handle: '@ylecun', role: 'Turing Award / AI debate', why: '公开辩论型；带来学术侧关注。', behavior: '争论驱动，quote 频繁。' },
      { id: 'r_lilian', name: 'Lilian Weng', handle: '@lilianweng', role: 'agent research / AI safety', why: 'agent 研究的高信任节点。', behavior: '低频、精选转发。' },
    ]},
    { id: 'rt_vc', label: '风险投资人', why: '看见之后可能发生 warm intro、投资兴趣或 BD 联系。', value: '投资人关注', people: [
      { id: 'r_sarahguo', name: 'Sarah Guo', handle: '@saranormous', role: 'Founder, Conviction', why: '活跃 AI 投资人，播客 + X 双通道。', behavior: '回复创始人；关注 agent / infra。' },
      { id: 'r_venturetwins', name: 'Justine Moore', handle: '@venturetwins', role: 'Partner, a16z', why: '消费 AI 趋势的 VC 侧放大节点。', behavior: '高频 quote 新产品。' },
      { id: 'r_eladgil', name: 'Elad Gil', handle: '@eladgil', role: 'Investor / operator', why: '创业者广泛信任的投资人。', behavior: '长文观点，偶尔回复。' },
    ]},
    { id: 'rt_devs', label: '开发者领袖', why: '开发者圈的意见形成点；影响工具选型与口碑。', value: '产品采用 · 开发者', people: [
      { id: 'r_simonw', name: 'Simon Willison', handle: '@simonw', role: 'AI engineering / tools', why: '开发者信任度极高的工具评论者。', behavior: '亲自试用、写长博客。' },
      { id: 'r_swyx', name: 'swyx', handle: '@swyx', role: 'AI Engineer / Latent.Space', why: 'AI Engineer 社区核心，播客主。', behavior: '回复 + 邀约播客。' },
      { id: 'r_hwchase', name: 'Harrison Chase', handle: '@hwchase17', role: 'LangChain founder', why: 'agent 框架生态中心。', behavior: '转发生态项目。' },
    ]},
    { id: 'rt_media', label: '科技媒体 / 播客', why: '把项目送进投资人和从业者的长内容信息流。', value: '媒体与长内容', people: [
      { id: 'r_dwarkesh', name: 'Dwarkesh Patel', handle: '@dwarkesh_sp', role: 'Dwarkesh Podcast', why: '深度 AI 访谈，VC 与研究者高频收听。', behavior: '有赞助入口。' },
      { id: 'r_jackclark', name: 'Jack Clark', handle: '@jackclarksf', role: 'Import AI / Anthropic', why: 'AI 政策与研究 newsletter 权威。', behavior: '精选引用。' },
    ]},
  ],
  crypto: [
    { id: 'rt_builders', label: '加密建设者', why: '协议与基础设施的叙事中心。', value: '行业背书', people: [
      { id: 'r_vitalik', name: 'Vitalik Buterin', handle: '@VitalikButerin', role: 'Ethereum', why: '生态最高信任节点。', behavior: '长文与回复。' },
      { id: 'r_cz', name: 'CZ', handle: '@cz_binance', role: 'Binance founder', why: '交易与市场情绪放大节点。', behavior: '短句广播。' },
    ]},
    { id: 'rt_traders', label: '活跃交易者', why: '决定流动性侧的注意力。', value: '声量 · 转化', people: [
      { id: 'r_cobie', name: 'Cobie', handle: '@cobie', role: 'Trader / UpOnly', why: '交易圈意见形成点。', behavior: '讥讽式 quote。' },
    ]},
    { id: 'rt_cvc', label: '加密投资人', why: '融资与上所关注。', value: '投资人关注', people: [
      { id: 'r_hasu', name: 'Hasu', handle: '@hasufl', role: 'Flashbots / Paradigm', why: '研究型投资人。', behavior: '深度回复。' },
    ]},
    { id: 'rt_cn_kol', label: '中文科技圈 KOL', why: '中文市场的信息入口。', value: '中文市场', people: [
      { id: 'r_cn1', name: '中文加密媒体主编（示例）', handle: '@—', role: '中文媒体', why: '中文圈层入口。', behavior: '转发与专访。' },
    ]},
  ],
  finance: [
    { id: 'rt_analysts', label: '金融分析师', why: '专业可信度来源。', value: '行业背书', people: [
      { id: 'r_fa1', name: '宏观分析师（示例）', handle: '@—', role: '买方研究', why: '机构信息流入口。', behavior: '长文。' },
    ]},
    { id: 'rt_ftraders', label: '活跃交易者', why: '交易社群注意力。', value: '声量', people: [] },
    { id: 'rt_fintech_founders', label: '金融科技创始人', why: '产品叙事同行认可。', value: '公共讨论', people: [] },
    { id: 'rt_inst', label: '交所 / 机构决策者', why: 'BD 与合作机会。', value: '企业采购', people: [] },
  ],
};

// 项目配置：Mango 建项目时定义。换项目只换这份配置。
export const project = {
  id: 'p_ilands',
  name: 'iLands',
  tagline: 'AI agent 原生的角色社交网络',
  domainGroup: 'ai',
  domains: ['ai_agent', 'consumer_ai'],
  stage: '产品公测前',
  markets: ['us'],
  goal: 'credibility',
  signalState: 'full', // 'full' | 'partial' | 'none'
  createdBy: 'Mango Labs',
  updated: '2026-09-07',
  token: 'demo',
  contactName: 'iLands 团队',
  lastNotes: ['希望被 AI 研究者与美国 VC 认真讨论，而非泛 AI 工具号曝光', '避免硬广式测评；接受播客与 newsletter 作为二跳路径', '预算区间 $10k–30k，首波以英文市场为主'],
};

// 关系 Signal（演示）。type: follow | reply | quote | co_appear | media
export const signals = [
  { kolId: 'k24', rootId: 'r_karpathy', type: 'follow', date: '2026-08', count: 1, verified: true, evidence: '当前关注关系（公开列表）' },
  { kolId: 'k24', rootId: 'r_sama', type: 'quote', date: '2026-07', count: 2, verified: true, evidence: '两次被 quote 的公开帖' },
  { kolId: 'k24', rootId: 'r_venturetwins', type: 'follow', date: '2026-08', count: 1, verified: true, evidence: '当前关注关系' },
  { kolId: 'k64', rootId: 'r_elon', type: 'reply', date: '2026-06', count: 3, verified: true, evidence: '三次公开回复线程' },
  { kolId: 'k64', rootId: 'r_amasad', type: 'follow', date: '2026-08', count: 1, verified: true, evidence: '当前关注关系' },
  { kolId: 'k61', rootId: 'r_pmarca', type: 'quote', date: '2026-05', count: 1, verified: false, evidence: '一次 quote，待核验账号归属' },
  { kolId: 'k61', rootId: 'r_eladgil', type: 'follow', date: '2026-08', count: 1, verified: true, evidence: '当前关注关系' },
  { kolId: 'k62', rootId: 'r_simonw', type: 'reply', date: '2026-07', count: 2, verified: true, evidence: '两次评论区互动' },
  { kolId: 'k62', rootId: 'r_swyx', type: 'co_appear', date: '2026-04', count: 1, verified: true, evidence: '同场 Spaces' },
  { kolId: 'k60', rootId: 'r_dwarkesh', type: 'media', date: '2026-03', count: 1, verified: true, evidence: '同一期播客提及' },
  { kolId: 'k60', rootId: 'r_sarahguo', type: 'follow', date: '2026-08', count: 1, verified: false, evidence: '关注关系待复核' },
  { kolId: 'k63', rootId: 'r_hwchase', type: 'follow', date: '2026-08', count: 1, verified: true, evidence: '当前关注关系' },
  { kolId: 'k63', rootId: 'r_karpathy', type: 'reply', date: '2026-06', count: 1, verified: true, evidence: '一次评论区回复' },
  { kolId: 'k65', rootId: 'r_jackclark', type: 'media', date: '2026-02', count: 1, verified: true, evidence: 'Import AI 引用' },
  { kolId: 'k69', rootId: 'r_ylecun', type: 'quote', date: '2026-07', count: 1, verified: true, evidence: '被 quote 一次' },
];
