// Mango KOL database -- vanilla JS frontend. No build step: this file is
// served as-is by FastAPI's StaticFiles mount. State lives in `state`;
// every mutation re-renders the relevant section explicitly (no framework).

const STRATEGIC_CLASSES = ["Top KOL", "Community Leader", "KOL"];
const DISTRIBUTION_CLASSES = ["KOC", "Marketing Account"]; // matches backend models.DISTRIBUTION_CLASSES
const MEDIA_CLASSES = ["Media / Community Account"];
const NEEDS_REVIEW_CLASSES = ["Unknown", "Non-creator / Irrelevant"]; // matches backend models.NEEDS_REVIEW_CLASSES
const ALL_CLASSES = STRATEGIC_CLASSES.concat(DISTRIBUTION_CLASSES, MEDIA_CLASSES, NEEDS_REVIEW_CLASSES);
const TAB_LABELS = { strategic: "核心 KOL", distribution: "KOC / 分发号", media: "媒体 / 社区", needs_review: "待复核" };
// creator_class is the literal API/DB value (must stay in English -- it's
// the actual stored enum), this is only the Chinese display label.
const CLASS_LABEL_ZH = {
  "Top KOL": "顶级 KOL",
  "Community Leader": "社区领袖",
  KOL: "KOL",
  "Media / Community Account": "媒体/社区账号",
  KOC: "KOC",
  "Marketing Account": "营销号",
  "Non-creator / Irrelevant": "非创作者/不相关",
  Unknown: "待复核",
};
const PROMO_LABEL_ZH = { Low: "低", Medium: "中", High: "高" };
const CONFIDENCE_LABEL_ZH = { High: "高", Medium: "中", Low: "低", none: "未知" };
const WORKFLOW_STATUS_ZH = {
  open: "待处理",
  in_progress: "进行中",
  done: "已完成",
  blocked: "受阻",
  follow_up_scheduled: "外联跟进中",
  relationship_rejected: "候选关系被否认",
  rejected: "本次路径或提案被拒绝",
};
const SPEND_CLASSIFICATION_ZH = {
  active_budget: "有活跃预算",
  repeated_paid: "多次付费",
  affiliate_only: "仅联盟分成",
  formal_program: "有正式合作项目",
};
const INTRO_STATUS_ZH = {
  human_intro_unvalidated: "尚未核实引荐意愿",
  not_evaluated: "尚未评估",
  not_applicable_no_graph_path: "无图谱路径，不适用",
  not_applicable_no_observed_graph_path: "未观察到图谱路径，不适用",
  not_evaluable_identity_unconfirmed: "身份未确认，无法评估",
};
const DISCLOSURE_LABEL_ZH = {
  paid_sponsorship: "付费赞助",
  affiliate: "联盟返佣",
  ambassador: "品牌大使",
  event: "活动合作",
  mention: "仅提及",
  unverified: "未核实",
  unknown: "未知",
};
function sponsorshipDisclosureLabel(evidence) {
  if (evidence.disclosure_type === "paid_sponsorship" && evidence.review_status !== "confirmed") {
    return "付费观察";
  }
  return DISCLOSURE_LABEL_ZH[evidence.disclosure_type] || "未知";
}

// Keep a stable callable for the canonical company workspace.  The legacy
// company and creator renderers still use the original helper directly.
const canonicalSponsorshipDisclosureLabel = sponsorshipDisclosureLabel;
const SPEND_EVIDENCE_LABEL_ZH = {
  L3: "明确现金、佣金或活跃投放",
  L2: "正式项目、实物支持或渠道合作",
  L1: "仅有能力或资金信号",
  legacy_unmapped: "历史信号，待重新核实",
};
const OPPORTUNITY_VALUE_ZH = {
  tier_a: "Tier A · 优先投入",
  tier_b: "Tier B · 值得准备",
  tier_c: "Tier C · 观察",
  archive: "Archive · 暂不投入",
};
const EXECUTION_READINESS_ZH = {
  contact_now: "现在联系",
  prepare_proposal: "准备方案",
  research_buyer_route: "补查买方/路径",
  watch: "观察",
  blocked: "阻塞",
};
const DECISION_BUCKET_ZH = {
  pursue_now: "立即推进",
  prepare: "下一批准备",
  watch: "观察/补查",
  archive: "归档",
};
const ASIA_STATUS_ZH = {
  confirmed_expansion: "已确认亚洲扩张",
  active_market: "亚洲市场已有实际动作",
  strong_signal: "亚洲强信号",
  lead_requiring_verification: "亚洲线索待核实",
  no_signal_found: "本轮未发现亚洲市场信号",
  not_reviewed: "尚未审查",
};
const ASIA_MARKET_CONTEXT_ZH = {
  asia_headquartered_global_first: "亚洲总部、全球优先",
  global_company_asia_origin_not_verified: "全球公司；亚洲渊源尚不足以证明区域市场意图",
  global_company_no_verified_asia_signal: "全球公司；本轮未发现可验证亚洲市场信号",
  global_company_with_asia_customer_evidence: "全球公司；已有亚洲客户证据",
  overseas_company_expanding_in_asia: "海外公司正在进入亚洲",
  overseas_company_with_active_asia_distribution: "海外公司在亚洲已有实际分发合作",
};
const ICP_LABEL_ZH = {
  "AI developer tools/API": "AI 开发者工具 / API",
  "AI productivity/SaaS": "AI 生产力 / SaaS",
  "Consumer AI products": "消费级 AI 产品",
  "Enterprise-only AI": "企业级 AI",
  "AI × crypto": "AI × Crypto",
};
const CHANNEL_LABEL_ZH = {
  "3D creator community": "3D 创作者社区", API: "API", Discord: "Discord", PR: "公关",
  "VC ecosystem": "VC 生态", "YouTube sponsorships": "YouTube 赞助", affiliate: "联盟返佣",
  agencies: "代理商", "agency partnerships": "代理商合作", "agency/team sales": "代理商 / 团队销售",
  ambassador: "品牌大使", ambassadors: "品牌大使", "brand activations": "品牌激活",
  "brand ambassadors": "品牌大使", "campus leads": "校园负责人", "case studies": "客户案例",
  "cash-reward campaigns": "现金奖励活动", "co-marketing": "联合营销", community: "社区",
  "community events": "社区活动", consultants: "顾问渠道", "creative grants": "创意资助",
  "creative partner program": "创意伙伴项目", "creator community": "创作者社区",
  "creator partners": "创作者伙伴", "creator partnerships": "创作者合作", "creator program": "创作者项目",
  "creator showcases": "创作者展示", "creator tutorials": "创作者教程", "customer stories": "客户故事",
  "developer content": "开发者内容", "developer ecosystem": "开发者生态", "developer/API": "开发者 / API",
  education: "教育渠道", enterprise: "企业渠道", "enterprise partnerships": "企业合作",
  "enterprise sales": "企业销售", events: "活动", "film competitions": "电影竞赛",
  "film festival": "电影节", "founder communities": "创始人社区", "founder events": "创始人活动",
  "game development": "游戏开发", hackathons: "黑客松", meetups: "线下聚会",
  "partner ecosystem": "伙伴生态", "performance marketing": "效果营销", "product marketing": "产品营销",
  "product-led growth": "产品驱动增长", "product-led sharing": "产品内分享", "productivity workflows": "生产力工作流",
  "referral codes": "推荐码", referrals: "推荐返佣", social: "社交渠道", "solution partners": "解决方案伙伴",
  "startup programs": "创业公司项目", "technical creators": "技术型创作者", "technology partners": "技术伙伴",
  "technology partnerships": "技术合作", templates: "模板", tutorials: "教程", "visual tutorials": "视觉教程",
  "workflow tutorials": "工作流教程",
};
const EVIDENCE_CLAIM_ZH = {
  "gtm:company:elevenlabs:3": "ElevenLabs 官方联盟页面向符合条件的付费订阅提供最长 12 个月、最高 22% 的佣金，并称已有数千名创作者、出版方、博主和影响者参与。",
  "gtm:company:elevenlabs:4": "官方品牌大使项目区分 Community Builder 与 Content Creator 两条路径，提供活动经费、额度、支持和执行手册；该项目本身不是付费职位，但可能出现付费机会。",
  "gtm:company:elevenlabs:5": "ElevenLabs 官方案例称，一位头部联盟伙伴每月带来数千名用户，主要通过常青型产品教程和显著的追踪链接获得持续联盟收入。",
  "v5:elevenlabs:affiliate-hire": "ElevenLabs 正在招聘 Affiliate Marketing Manager，职责包括招募并激活联盟伙伴、出版方和创作者伙伴。",
  "v5:elevenlabs:brand-budget": "ElevenLabs 的 Brand Marketing 职位负责创作者合作及数百万美元级合作预算。",
  "v5:elevenlabs:operator": "Dustin Blank 负责 Partnerships，公开职责包括创作者与媒体合作。",
  "gtm:company:gamma:3": "Gamma 的供应商案例称，双方合作覆盖超过 1,000 名创作者、国际创作者付款和 15 组受众测试，累计 3,100 万次观看、CPM 低于 13 美元，月度投放规模增长 20 倍。",
  "gtm:company:gamma:4": "Gamma 官方社区公告称，会为本地主办方提供场地和餐饮预算、活动手册、优惠券、周边、活动页与宣传支持，并围绕 9 月发布窗口组织活动。",
  "gtm:company:gamma:5": "Gamma 帮助中心记录了经审核的 PartnerStack 联盟项目，包含专属链接、推广素材以及推荐和收益追踪。",
  "gtm:company:gamma:6": "Gamma 的历史招聘镜像提到每月有 150 多位影响者上线；其中“数百万美元级”是候选人经验要求，不能作为 Gamma 自身预算的证明。",
  "v5:gamma:community": "Gamma 在官方社区页面公开提供赞助和联合主办活动合作。",
  "v5:gamma:growth-hire": "Gamma 正在招聘 Growth Manager，职责包括扩大联盟与创作者项目并管理付费获客。",
  "v5:gamma:operator": "Conor Irvine 负责 Gamma 的 partnerships 与战略分发渠道。",
  "v5:perplexity:operator": "Raman Malik 的公开职位为 VP, Product and Growth；联系前仍需用一手来源复核当前职位。",
  "v5:pixverse:cpp-aug": "PixVerse 当前 CPP 指南列出每周 5,250 美元现金分配、付费合作机会和直达创作者社区的渠道。",
  "v5:pixverse:pixlab": "PixLab 是邀请制路径，用于更深度的项目型和长期创作者合作。",
  "gtm:company:replit:0": "Replit 官方 Creator 页面介绍了经审核的合作项目，并公开最近内容的观看与受众门槛，包括最近 5 条内容平均 5,000 次观看。",
  "gtm:company:replit:1": "Replit 官方伙伴中心列出 4 类伙伴路径、联合营销、联合案例、结构化推荐与联合销售、25,000 美元 Race to Revenue 奖金，以及创作者和活动项目。",
  "gtm:company:replit:2": "Replit 明确为黑客松提供 credits，但该活动支持路径不包含现金赞助、奖金或直接资金支持。",
  "gtm:company:cursor:0": "Cursor 官方 Ambassador 页面列出 meetup、黑客松、workshop、免费 credits、活动成本支持、团队权限和申请审核流程。",
  "gtm:company:cursor:1": "Cursor Campus Leads 项目包含校园 workshop 与黑客松、全额承担的启动行程、一次黑客松差旅报销、活动经费和 6 个月 Cursor Ultra。",
  "gtm:company:cursor:2": "Cursor 官方职位说明定义了 startup-events 漏斗，要求负责人按 activation、usage、retention 和 advocacy 追踪投入，并与 Growth、PMM 和 Field Engineering 协作。",
  "v5:synthesia:operator": "Synthesia 官方资料将 Leander Sodji 列为 Product Marketing Manager。",
  "gtm:company:runway:2": "Runway 宣布 500 万美元电影基金，未来可能扩大到 1,000 万美元；单项资助从 5,000 美元到超过 100 万美元，并另提供 200 万美元 credits。",
  "gtm:company:runway:3": "Runway 官方资源页列出全球 meetup、Runway Studios 与 AI 电影项目、Creative Partners、Student Ambassadors、教育者支持和公开 partnerships 渠道。",
  "v5:runway:aiff": "Runway 2026 夏季 AI Film Festival 公布各赛道奖金合计超过 135,000 美元。",
  "v5:runway:resources": "Runway 官方资源页列出创作者项目、meetup、联盟项目、电影节和 partnerships@runwayml.com。",
  "v5:creatify:founder": "Creatify 官方 About 页面将 Yinan Na 列为联合创始人兼 CEO。",
  "v5:hedra:operator": "Hedra 官方团队页将 Erna Balayan 列为 Product Marketing Manager。",
  "v5:hedra:teams": "Hedra 于 2026 年 1 月发布面向品牌、代理商和创意团队的 Teams 方案。",
};
const CAMPAIGN_ROLE_ZH = {
  "Strategic KOL": "战略 KOL",
  "Technical educator": "技术型讲解者",
  "Regional/community distribution": "区域 / 社区分发",
  "Creator distribution": "创作者分发",
  KOC: "KOC",
  "Media/community channel": "媒体 / 社区渠道",
};
function confidenceLabelZh(value) {
  return ({ high: "高", medium: "中", low: "低" }[String(value || "").toLowerCase()] || value || "未记录");
}
const IDENTITY_MATCH_STATUS = "confirmed_official_role_and_rapid_x_exact_profile";

const state = {
  section: "home",
  opp: {
    filters: {
      decision_bucket: new Set(),
      opportunity_value: new Set(),
      execution_readiness: new Set(),
      signal: new Set(),
      icp_type: new Set(),
      category: new Set(),
      geography: new Set(),
    },
    search: "",
    sort: "canonical_decision",
    order: "desc",
    page: 1,
    pageSize: 25,
    meta: { categories: [], geographies: [], opportunity_values: {}, execution_readiness: {}, decision_buckets: {}, signal_filters: [], icp_types: [] },
    rows: [],
    total: 0,
  },
  tab: "strategic",
  search: "",
  filters: {
    creator_class: new Set(),
    platform: new Set(),
    region: new Set(),
    language: new Set(),
    promotion_level: new Set(),
    category: null,
    contact: null,
    followers_min: null,
    followers_max: null,
    avg_views_min: null,
    avg_views_max: null,
    engagement_min: null,
    engagement_max: null,
    quote_min: null,
    quote_max: null,
  },
  sort: "engagement_rate",
  order: "desc",
  page: 1,
  pageSize: 50,
  meta: { creator_classes: [], promotion_levels: [], platforms: [], regions: [], languages: [], categories: [], tab_counts: {} },
  rows: [],
  total: 0,
  shortlistId: localStorage.getItem("mango_kol_shortlist_id") ? Number(localStorage.getItem("mango_kol_shortlist_id")) : null,
  shortlistCreatorIds: new Set(),
  status: { rapid_x_configured: true, youtube_configured: true, scrapecreators_configured: true, openai_configured: true },
  selectedRowIds: new Set(), // bulk-selection checkboxes, independent of the shortlist
  writeAccess: true,
  hiddenColumns: new Set(JSON.parse(localStorage.getItem("mango_kol_hidden_cols") || "[]")),
};

const TOGGLEABLE_COLUMNS = [
  { key: "platform", label: "平台" },
  { key: "category", label: "类别" },
  { key: "region", label: "地区 / 语言" },
  { key: "avg_views", label: "平均播放量" },
  { key: "engagement", label: "互动率" },
  { key: "cpm", label: "预估 CPM" },
  { key: "contact", label: "联系方式" },
];

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch (e) {
      /* ignore */
    }
    const error = new Error(detail);
    error.status = res.status;
    throw error;
  }
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return res.json();
  return res.text();
}

async function loadAccessMode() {
  const badge = document.getElementById("accessModeBadge");
  try {
    const access = await api("/internal/access-status");
    state.writeAccess = Boolean(access.write_access);
    badge.textContent = state.writeAccess ? "内部编辑已授权" : "公开只读";
    badge.classList.toggle("access-write", state.writeAccess);
    badge.classList.toggle("access-read-only", !state.writeAccess);
    badge.classList.remove("hidden");
    document.body.classList.toggle("public-read-only", !state.writeAccess);
    badge.title = state.writeAccess
      ? "当前会话可以读取和修改数据"
      : "所有人可以浏览；新增、编辑和删除仍需内部授权";
  } catch (_error) {
    badge.classList.add("hidden");
  }
}

function toast(message, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  el.classList.toggle("error", isError);
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 3200);
}

function fmtNum(n) {
  if (n === null || n === undefined) return "-";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + "K";
  return String(Math.round(n));
}

function fmtUsd(n) {
  if (n === null || n === undefined) return "-";
  return "$" + n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function spendEvidenceLabel(level) {
  return SPEND_EVIDENCE_LABEL_ZH[level] || level || "未知";
}

function sponsorshipDateLabel(evidence) {
  if (evidence?.published_at) return evidence.published_at;
  const observed = evidence?.collected_at || evidence?.created_at;
  return observed
    ? `发布日期未知 · 收集于 ${String(observed).slice(0, 10)}`
    : "发布日期与收集日期均未记录";
}

function evidenceConfidenceLabel(value) {
  if (value === null || value === undefined || value === "") return "置信度未记录";
  if (typeof value === "number") return `提取置信度 ${Math.round(value <= 1 ? value * 100 : value)}%`;
  return `置信度 ${String(value)}`;
}

function currentWorkStatusLabel(record) {
  if (record?.current_work_kind === "outreach_terminal_outcome") {
    return WORKFLOW_STATUS_ZH[record.current_workflow_status]
      || record.current_workflow_status
      || "外联终态已记录";
  }
  if (!record?.has_scheduled_action) return "未创建行动项";
  return WORKFLOW_STATUS_ZH[record.current_workflow_status]
    || record.current_workflow_status
    || "状态待补齐";
}

function currentWorkKindLabel(record) {
  if (record?.current_work_kind === "outreach_follow_up") return "真实外联跟进";
  if (record?.current_work_kind === "outreach_terminal_outcome") return "最新外联终态";
  if (record?.current_work_kind === "release_internal_check") return "内部关系核实";
  if (record?.current_work_kind === "action_item") return "已排期行动项";
  return "未排期研究建议";
}

// API values and archived research prose keep their original vocabulary,
// while product-facing copy uses one plain-Chinese vocabulary throughout.
const CONTACT_ROUTE_TYPE_ZH = { Direct: "正式直达", Intermediary: "中间渠道", "Warm candidate": "暖关系候选", Cold: "冷启动渠道" };
const PRODUCT_COPY_EXACT_ZH = {
  "The remit matches user acquisition and product activation.": "职责范围直接涉及用户获取和产品激活。",
  "The public title is growth-adjacent but budget ownership is unconfirmed.": "公开职位与增长相关，但预算归属尚未确认。",
  "It is the company's explicit creator collaboration surface.": "这是公司公开指定的创作者合作入口。",
  "Identity is confirmed, but day-to-day ownership is unknown.": "身份已确认，但日常项目负责人仍未知。",
  "Product Marketing is the closest named public operator to the use case.": "Product Marketing 是与该方案最接近的公开具名职能。",
  "Founder identity is first-party confirmed.": "创始人身份已有首方来源确认。",
  "The maintained program has explicit creator economics.": "该持续运营项目公开了创作者收益机制。",
  "Early-stage founder routing is reasonable; budget authority is not claimed.": "早期团队可先由创始人路由，但不能据此声称其掌握该预算。",
  "The role directly covers product positioning and market activation.": "该职责直接覆盖产品定位和市场激活。",
  "The program is live and has recurring economics.": "项目仍在运行，并有持续性的商业激励机制。",
  "Teams is a current commercial expansion surface.": "Teams 是当前明确的商业化扩张入口。",
  "Growth is functionally relevant but budget ownership is unknown.": "Growth 职能相关，但预算负责人尚未确认。",
  "The named Growth remit matches the offer.": "具名 Growth 职责与本方案直接匹配。",
  "The role aligns with technical creator and developer adoption.": "该职责与技术创作者合作和开发者采用直接相关。",
  "His public remit directly includes creator and media deals.": "其公开职责明确包括创作者和媒体合作。",
  "His stated remit includes strategic distribution.": "其公开职责包括战略分发。",
  "The company explicitly invites sponsorships and co-hosted events.": "公司公开接受赞助和联合活动合作。",
  "It reaches the company without relying on an unverified warm path.": "该入口可直接触达公司，无需依赖未核实的暖关系。",
  "The business is enterprise-led and the contact surface is maintained.": "业务以 enterprise 为主，且该联系入口仍在维护。",
  "Terms and economics are public and measurable.": "条款与收益机制公开且可衡量。",
  "The company publicly defines both routes.": "公司已公开说明这两类合作路径。",
  "Make a routing ask, not a broad pitch.": "首次只请求路由到正确负责人，不发送宽泛提案。",
  "Make a routing ask, not an open-ended pitch.": "首次只请求路由到正确负责人，不发送开放式提案。",
  "Ask for process intelligence only.": "只询问 brief、采购与复购流程，不默认请求引荐。",
  "Ask for the current Creative Partnerships owner.": "询问当前负责 Creative Partnerships 的人员。",
  "Confirm current role through a first-party source, then send the cohort thesis.": "先用首方来源复核当前职位，再发送创作者组合假设。",
  "Validate the current role and make a routing request, not a full pitch.": "先复核当前职位，再请求路由，不直接发送完整提案。",
  "Validate current role, then send a routing request.": "先复核当前职位，再发送路由请求。",
  "Revalidate role, then send a concise regional cohort proposal.": "复核职位后，发送精简的区域创作者组合方案。",
  "Submit sample creators and three automation use cases.": "提交样例创作者和 3 个自动化使用场景。",
  "Send a short routing message with one concrete regional experiment.": "发送简短路由请求，并附 1 个具体区域实验。",
  "Send a one-page cohort with workflows, deliverables, and conversion plan.": "发送一页式创作者组合，写清工作流、交付物和转化方案。",
  "Submit a small set of creators with existing AI-video work.": "提交一组已有 AI video 作品的创作者。",
  "Frame Mango as an agency and creator activation partner.": "将 Mango 定位为 agency 与创作者激活合作方。",
  "Send three production-ready video-agent use cases and partner profiles.": "发送 3 个可投入生产的 video-agent 场景和合作方资料。",
  "Prepare sample creators and a 30-day activation design.": "准备样例创作者和 30 天激活方案。",
  "Hedra business route": "Hedra 商务入口",
  "Meshy team": "Meshy 团队入口",
  "Affiliate and partner intake": "联盟与合作伙伴申请入口",
  "Gamma community partner route": "Gamma 社区合作入口",
  "Perplexity business contact": "Perplexity 商务联系入口",
  "CPP rolling application": "CPP 常年开放申请",
  "Replit partner programs": "Replit 合作伙伴项目",
  "Observed Gumloop affiliate creator": "曾参与 Gumloop 联盟项目的创作者",
  "Wispr Flow creator partnership": "Wispr Flow 创作者合作入口",
  "It is the published route alongside the affiliate program.": "这是与 Affiliate Program 并列公开的团队联系入口。",
  "It is active and includes event funding and team access.": "项目仍在运行，并明确提供活动经费和团队支持。",
  "The channel is actively being rebuilt and has an explicit operator mandate.": "该渠道正在重建，且已有明确负责人。",
  "The official guide designates this address for applications and program questions.": "官方指南指定该邮箱接收申请和项目咨询。",
  "PixVerse discovers deeper partners through work already submitted and published.": "PixVerse 会从已提交并发布的作品中筛选深度合作方。",
  "Partner tracks include co-marketing and referral/co-sell mechanics.": "合作项目包含联合营销、转介和联合销售机制。",
  "It is the official route for community initiatives.": "这是社区合作项目的官方入口。",
  "The program is a maintained talent discovery surface.": "该项目持续用于发现和筛选创意人才。",
  "It is the clearest maintained public commercial route.": "这是当前最清晰且仍在维护的公开商业入口。",
  "They have used the program directly.": "该创作者曾直接参与此项目，可用于了解实际流程。",
  "Founder identity is confirmed; ownership beneath him is not.": "创始人身份已确认，但具体执行负责人仍未知。",
  "It is the closest maintained public route to creator collaboration.": "这是当前最接近创作者合作的公开入口。",
  "It is the explicit public collaboration surface.": "这是公司公开指定的合作入口。",
  "Submit Mango's best technical educators and a build-to-revenue format.": "提交 Mango 最合适的技术教育创作者，并附从搭建到变现的内容方案。",
  "Prepare three enterprise workflow concepts and a credible educator shortlist.": "准备 3 个企业工作流方案，并附可信的技术教育创作者名单。",
  "Prepare a one-page cohort with creator types, formats, market, budget bands, and attribution plan.": "准备一页式名单，写清创作者类型、内容形式、目标市场、预算区间和归因方案。",
  "Frame Mango as a distribution operator, not a software buyer.": "将 Mango 定位为企业内容分发合作方，不以软件采购方身份询价。",
  "Send 6-8 niche creators and three repeatable workflows.": "提交 6-8 位垂直创作者，并附 3 个可重复执行的 3D 工作流。",
  "Nominate builders who can teach and show retained usage.": "提名既能教学、又能展示持续使用结果的 builder。",
  "Submit Mango's creator coverage and proposed activation metrics.": "提交 Mango 的创作者覆盖范围和建议的激活指标。",
  "Send a one-page APAC experiment with creator cohorts and activation metrics.": "提交一页式 APAC 实验方案，写清创作者组合和激活指标。",
  "Propose one launch-timed workshop plus tracked creator content.": "提出一场配合发布期的工作坊，并搭配可追踪的创作者内容。",
  "Submit a concise brief with target users, creators, and conversion metrics.": "提交精简 brief，写清目标用户、创作者组合和转化指标。",
  "Send 8-12 sample creators, format mix, and a pilot operating model.": "提交 8-12 位样例创作者、内容形式组合和试点执行方式。",
  "Select creators with strong AI-video portfolios and disclose the agency relationship.": "选择 AI video 作品集扎实的创作者，并明确披露 agency 代理关系。",
  "Choose the correct solution or integration track and submit one specific APAC use case.": "先选择合适的解决方案或集成路径，再提交 1 个具体 APAC 场景。",
  "Send a scoped creative program with selection, milestones, budget, and distribution partner.": "提交边界清晰的创意项目，写明筛选标准、里程碑、预算和分发伙伴。",
  "Submit a strong portfolio and disclose representation.": "提交高质量作品集，并明确披露代理关系。",
  "Submit creator profiles, ad-workflow formats, and attribution plan.": "提交创作者资料、广告工作流内容形式和归因方案。",
  "Make a concise routing request with a clear 3D concept.": "用明确的 3D 项目构想发送简短路由请求。",
  "Submit a visual creator shortlist and sample project brief.": "提交视觉创作者名单和样例项目 brief。",
  "Enroll a small set of relevant creators with full disclosure.": "先让一小批相关创作者加入，并完整披露合作关系。",
  "Choose the technology or referral path and state the expected activation outcome.": "选择技术合作或转介路径，并写明预期激活结果。"
};

function productCopy(value) {
  const raw = String(value ?? "");
  if (PRODUCT_COPY_EXACT_ZH[raw]) return PRODUCT_COPY_EXACT_ZH[raw];
  return raw
    .replace(/Solomon\s*\(@Solomon_Nahhh\)/gi, "Mango 内部联系人")
    .replace(/@Solomon_Nahhh/gi, "Mango 内部联系人")
    .replace(/\bSolomon\b/gi, "Mango 内部联系人")
    .replace(/已验证\s*warm path/gi, "已验证关系路径")
    .replace(/cold outreach/gi, "直接联系")
    .replace(/warm path/gi, "待核实的引荐关系")
    .replace(/\bfallback\b/gi, "备选方案")
    .replace(/\boperator\b/gi, "负责人")
    .replace(/\bconnector\b/gi, "引荐候选人")
    .replace(/\bcampaign\b/gi, "合作活动")
    .replace(/\boffer\b/gi, "合作方案")
    .replace(/\bcreator\b/gi, "创作者")
    .replace(/\s+->\s+/g, " → ")
    .replace(/\u6280\u672f\u4e0a\u53ef\u89e6\u8fbe/g, "观察到公开 X 关注链");
}

function renderRetryState(container, message, retry, technicalDetail = "") {
  container.innerHTML = `
    <div class="retry-state" role="alert">
      <div class="empty-title">${escapeHtml(message)}</div>
      <div class="muted">请检查网络后重试。</div>
      ${technicalDetail ? `<details><summary>技术详情</summary><div class="provenance-note">${escapeHtml(technicalDetail)}</div></details>` : ""}
      <button class="btn btn-ghost btn-sm retry-btn" type="button">重试</button>
    </div>`;
  const button = container.querySelector(".retry-btn");
  if (button && retry) button.addEventListener("click", retry);
}

function makeActivatable(element, activate) {
  element.setAttribute("role", "button");
  element.setAttribute("tabindex", "0");
  element.addEventListener("click", activate);
  element.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    if (event.target.closest("button, a, input, select, textarea")) return;
    event.preventDefault();
    activate(event);
  });
}

function operatorHasDualSourceMatch(operator) {
  return Boolean(operator && (operator.identity_status === IDENTITY_MATCH_STATUS || operator.identity_confirmed));
}

function operatorIdentityLabel(operator) {
  return operatorHasDualSourceMatch(operator) ? "官网/公司来源 + X 资料已匹配" : "公开身份资料待补齐";
}

function operatorHumanReviewLabel(operator) {
  if (!operator) return "身份未核实 / 职位未核实";
  const identity = operator.identity_human_verified ? "身份已人工核实" : "身份未人工核实";
  const role = operator.role_human_verified ? "职位已人工核实" : "职位未人工核实";
  return `${identity} / ${role}`;
}

function verificationDateLabel(verified, verifiedAt) {
  return verified && verifiedAt ? `已于 ${new Date(verifiedAt).toLocaleDateString()} 核实` : "尚未单独核实";
}

function classTier(cls) {
  if (STRATEGIC_CLASSES.includes(cls)) return "tier-strategic";
  if (DISTRIBUTION_CLASSES.includes(cls)) return "tier-distribution";
  if (MEDIA_CLASSES.includes(cls)) return "tier-media";
  return "tier-review";
}

function classLabel(cls) {
  return CLASS_LABEL_ZH[cls] ?? cls;
}

function initials(name) {
  const parts = (name || "?").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

// Broken/expired avatar URLs (common for stale profile images) fall back to
// initials via a real DOM swap on error, not inline-HTML string concatenation
// -- avoids quote-collision bugs from nesting one HTML attribute inside another.
window.__onAvatarError = function (img, name, sizeClass) {
  const span = document.createElement("span");
  span.className = `avatar-fallback ${sizeClass}`;
  span.textContent = initials(name);
  img.replaceWith(span);
};

function avatarHtml(name, url, sizeClass = "") {
  // Instagram and TikTok return short-lived signed CDN URLs that frequently
  // reject browser hotlinks with 403/ORB after ingestion. They are not stable
  // product assets, so use the deterministic initials fallback instead.
  const unstableSignedCdn = /(?:cdninstagram\.com|tiktokcdn(?:-us)?\.com)/i.test(url || "");
  if (url && !unstableSignedCdn) {
    // JSON.stringify gives valid JS string literals (double-quoted); escapeAttr
    // then HTML-entity-encodes those quotes so they survive sitting inside the
    // onerror="..." HTML attribute without prematurely closing it.
    const nameArg = escapeAttr(JSON.stringify(name || ""));
    const sizeArg = escapeAttr(JSON.stringify(sizeClass));
    return `<img class="avatar ${sizeClass}" src="${escapeAttr(url)}" alt="" loading="lazy" onerror="window.__onAvatarError(this, ${nameArg}, ${sizeArg})" />`;
  }
  return avatarFallbackHtml(name, sizeClass);
}
function avatarFallbackHtml(name, sizeClass = "") {
  return `<span class="avatar-fallback ${sizeClass}">${escapeHtml(initials(name))}</span>`;
}

function platformBadgeHtml(platform, platformRaw) {
  if (!platform || platform === "Unknown") {
    return `<span class="platform-badge unresolved" title="${platformRaw ? escapeAttr("原始记录：" + platformRaw) : "源表格中未注明"}">未注明</span>`;
  }
  const title = platformRaw ? ` title="${escapeAttr("原始记录为：" + platformRaw)}"` : "";
  // The text is the accessible identifier. Avoid remote decorative icon
  // requests so normal navigation cannot create CDN aborts in the console.
  return `<span class="platform-badge"${title}>${escapeHtml(platform)}</span>`;
}

// ---------------------------------------------------------------------------
// Filters panel (compact collapsible checkbox groups)
// ---------------------------------------------------------------------------

async function loadMeta() {
  state.meta = await api("/api/meta/filters");
  renderTabCounts();
  renderFilters();
}

function renderTabCounts() {
  const counts = state.meta.tab_counts || {};
  document.getElementById("countStrategic").textContent = counts.strategic !== undefined ? ` (${counts.strategic})` : "";
  document.getElementById("countDistribution").textContent = counts.distribution !== undefined ? ` (${counts.distribution})` : "";
  document.getElementById("countMedia").textContent = counts.media !== undefined ? ` (${counts.media})` : "";
  document.getElementById("countNeedsReview").textContent = counts.needs_review !== undefined ? ` (${counts.needs_review})` : "";
}

function checkboxGroup(title, key, options, current, open = false, labelFn = (o) => o) {
  if (!options.length) {
    return `<details class="filter-group"><summary>${title}</summary><div class="filter-options"><div class="filter-empty">暂无数据</div></div></details>`;
  }
  const rows = options
    .map((opt) => {
      const checked = current.has(opt) ? "checked" : "";
      const activeCls = current.has(opt) ? "active-label" : "";
      return `<label class="filter-check ${activeCls}"><input type="checkbox" data-filter-key="${key}" data-filter-value="${escapeAttr(opt)}" ${checked} />${escapeHtml(labelFn(opt))}</label>`;
    })
    .join("");
  const hasActive = current.size > 0;
  return `<details class="filter-group" ${open || hasActive ? "open" : ""}><summary>${title}</summary><div class="filter-options">${rows}</div></details>`;
}

function radioGroup(title, key, options, current, open = false) {
  if (!options.length) return "";
  const rows = options
    .map((opt) => {
      const checked = current === opt ? "checked" : "";
      const activeCls = current === opt ? "active-label" : "";
      return `<label class="filter-check ${activeCls}"><input type="radio" name="single-${key}" data-single-key="${key}" data-single-value="${escapeAttr(opt)}" ${checked} />${escapeHtml(opt)}</label>`;
    })
    .join("");
  return `<details class="filter-group" ${open || current ? "open" : ""}><summary>${title}</summary><div class="filter-options">${rows}<label class="filter-check"><input type="radio" name="single-${key}" data-single-key="${key}" data-single-value="" ${!current ? "checked" : ""} />不限</label></div></details>`;
}

function rangeGroup(title, minKey, maxKey) {
  const hasActive = state.filters[minKey] !== null || state.filters[maxKey] !== null;
  return `
    <details class="filter-group" ${hasActive ? "open" : ""}>
      <summary>${title}</summary>
      <div class="range-row">
        <input type="number" placeholder="最小值" data-range-key="${minKey}" value="${state.filters[minKey] ?? ""}" />
        <span class="range-sep">至</span>
        <input type="number" placeholder="最大值" data-range-key="${maxKey}" value="${state.filters[maxKey] ?? ""}" />
      </div>
    </details>`;
}

function renderFilters() {
  const classesByTab = {
    strategic: STRATEGIC_CLASSES,
    distribution: DISTRIBUTION_CLASSES,
    media: MEDIA_CLASSES,
    needs_review: NEEDS_REVIEW_CLASSES,
  };
  const classOptions = (classesByTab[state.tab] || NEEDS_REVIEW_CLASSES).filter((c) => state.meta.creator_classes.includes(c));
  const body = document.getElementById("filterBody");
  const groups = [
    state.tab !== "needs_review" ? checkboxGroup("创作者分类", "creator_class", classOptions, state.filters.creator_class, true, classLabel) : "",
    checkboxGroup("平台", "platform", state.meta.platforms, state.filters.platform, true),
    checkboxGroup("推广等级", "promotion_level", state.meta.promotion_levels, state.filters.promotion_level),
    checkboxGroup("地区", "region", state.meta.regions, state.filters.region),
    checkboxGroup("语言", "language", state.meta.languages, state.filters.language),
    radioGroup("类别", "category", state.meta.categories, state.filters.category),
    rangeGroup("粉丝数", "followers_min", "followers_max"),
    rangeGroup("平均播放量", "avg_views_min", "avg_views_max"),
    rangeGroup("互动率 (%)", "engagement_min", "engagement_max"),
    rangeGroup("报价 (USD)", "quote_min", "quote_max"),
    radioGroup("已有联系方式", "contact", ["email", "telegram"], state.filters.contact),
  ];
  body.innerHTML = groups.join("");

  body.querySelectorAll("[data-filter-key]").forEach((input) => {
    input.addEventListener("change", () => {
      const key = input.dataset.filterKey;
      const value = input.dataset.filterValue;
      const set = state.filters[key];
      if (input.checked) set.add(value);
      else set.delete(value);
      state.page = 1;
      renderFilters();
      loadCreators();
    });
  });
  body.querySelectorAll("[data-single-key]").forEach((input) => {
    input.addEventListener("change", () => {
      const key = input.dataset.singleKey;
      const value = input.dataset.singleValue;
      state.filters[key] = value || null;
      state.page = 1;
      renderFilters();
      loadCreators();
    });
  });
  body.querySelectorAll("[data-range-key]").forEach((input) => {
    input.addEventListener("change", () => {
      const key = input.dataset.rangeKey;
      state.filters[key] = input.value === "" ? null : Number(input.value);
      state.page = 1;
      loadCreators();
    });
  });
}

document.getElementById("clearFilters").addEventListener("click", () => {
  state.filters = {
    creator_class: new Set(),
    platform: new Set(),
    region: new Set(),
    language: new Set(),
    promotion_level: new Set(),
    category: null,
    contact: null,
    followers_min: null,
    followers_max: null,
    avg_views_min: null,
    avg_views_max: null,
    engagement_min: null,
    engagement_max: null,
    quote_min: null,
    quote_max: null,
  };
  state.search = "";
  document.getElementById("search").value = "";
  state.page = 1;
  renderFilters();
  loadCreators();
});

// ---------------------------------------------------------------------------
// Directory: query building + fetch + render
// ---------------------------------------------------------------------------

function buildQuery() {
  const p = new URLSearchParams();
  p.set("tab", state.tab);
  p.set("sort", state.sort);
  p.set("order", state.order);
  p.set("page", state.page);
  p.set("page_size", state.pageSize);
  if (state.search) p.set("search", state.search);
  const f = state.filters;
  f.creator_class.forEach((v) => p.append("creator_class", v));
  f.platform.forEach((v) => p.append("platform", v));
  f.region.forEach((v) => p.append("region", v));
  f.language.forEach((v) => p.append("language", v));
  f.promotion_level.forEach((v) => p.append("promotion_level", v));
  if (f.category) p.set("category", f.category);
  if (f.contact) p.set("contact", f.contact);
  ["followers_min", "followers_max", "avg_views_min", "avg_views_max", "engagement_min", "engagement_max", "quote_min", "quote_max"].forEach(
    (k) => {
      if (f[k] !== null && f[k] !== undefined) p.set(k, f[k]);
    }
  );
  return p.toString();
}

function renderSkeletonRows(n = 8) {
  const tbody = document.getElementById("creatorRows");
  const cols = 14;
  tbody.innerHTML = Array.from({ length: n })
    .map(
      () =>
        `<tr class="skeleton-row">${Array.from({ length: cols })
          .map(() => `<td><div class="skeleton-bar" style="width:${40 + Math.random() * 50}%"></div></td>`)
          .join("")}</tr>`
    )
    .join("");
}

let loadSeq = 0;
async function loadCreators() {
  const seq = ++loadSeq;
  document.getElementById("emptyState").style.display = "none";
  document.getElementById("errorState").style.display = "none";
  renderSkeletonRows();
  try {
    const [data, metaFresh] = await Promise.all([api("/api/creators?" + buildQuery()), api("/api/meta/filters")]);
    if (seq !== loadSeq) return; // a newer request superseded this one
    state.meta = metaFresh;
    renderTabCounts();
    renderFilters();
    state.rows = data.results;
    state.total = data.total;
    renderTable();
    renderPagination();
    document.getElementById("resultCount").textContent = `共 ${data.total} 位创作者`;
  } catch (err) {
    if (seq !== loadSeq) return;
    document.getElementById("creatorRows").innerHTML = "";
    const errorState = document.getElementById("errorState");
    renderRetryState(errorState, "暂时无法加载创作者", loadCreators, err.message);
    errorState.style.display = "block";
  }
}

function renderTable() {
  const tbody = document.getElementById("creatorRows");
  if (!state.rows.length) {
    tbody.innerHTML = "";
    const emptyEl = document.getElementById("emptyState");
    const hasFilters =
      state.search || Object.values(state.filters).some((v) => (v instanceof Set ? v.size > 0 : v !== null));
    emptyEl.innerHTML = hasFilters
      ? `<div class="empty-title">没有符合条件的创作者</div>试试清除筛选条件或放宽范围。`
      : `<div class="empty-title">${TAB_LABELS[state.tab]}暂无数据</div>请导入数据，或将创作者重新分类到此视图。`;
    emptyEl.style.display = "block";
    return;
  }
  document.getElementById("emptyState").style.display = "none";
  tbody.innerHTML = state.rows.map(rowHtml).join("");

  tbody.querySelectorAll("tr[data-creator-id]").forEach((tr) => {
    makeActivatable(tr, (e) => {
      if (e.target.closest(".add-btn") || e.target.closest(".col-select")) return;
      openDrawer(Number(tr.dataset.creatorId));
    });
  });
  tbody.querySelectorAll(".add-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await toggleShortlist(Number(btn.dataset.creatorId));
    });
  });
  tbody.querySelectorAll(".row-select-checkbox").forEach((cb) => {
    cb.addEventListener("change", () => {
      const id = Number(cb.dataset.creatorId);
      if (cb.checked) state.selectedRowIds.add(id);
      else state.selectedRowIds.delete(id);
      cb.closest("tr").classList.toggle("row-selected", cb.checked);
      updateBulkBar();
    });
  });
  applyColumnVisibility();
  updateSelectAllCheckbox();
}

function updateSelectAllCheckbox() {
  const box = document.getElementById("selectAllCheckbox");
  if (!box) return;
  const idsOnPage = state.rows.map((r) => r.id);
  const allSelected = idsOnPage.length > 0 && idsOnPage.every((id) => state.selectedRowIds.has(id));
  box.checked = allSelected;
}

document.getElementById("selectAllCheckbox").addEventListener("change", (e) => {
  state.rows.forEach((r) => {
    if (e.target.checked) state.selectedRowIds.add(r.id);
    else state.selectedRowIds.delete(r.id);
  });
  renderTable();
  updateBulkBar();
});

// ---------------------------------------------------------------------------
// Bulk selection bar
// ---------------------------------------------------------------------------

function updateBulkBar() {
  const bar = document.getElementById("bulkBar");
  const count = state.selectedRowIds.size;
  if (count === 0) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  document.getElementById("bulkBarSummary").textContent = `已选 ${count} 项`;
}

document.getElementById("bulkClearBtn").addEventListener("click", () => {
  state.selectedRowIds.clear();
  renderTable();
  updateBulkBar();
});

document.getElementById("bulkAddBtn").addEventListener("click", async () => {
  const ids = [...state.selectedRowIds];
  if (!ids.length) return;
  const btn = document.getElementById("bulkAddBtn");
  btn.disabled = true;
  const originalLabel = btn.textContent;
  let added = 0;
  try {
    const shortlist = await ensureShortlist();
    if (!shortlist) return;
    const existingIds = new Set(shortlist.items.map((i) => i.creator_id));
    for (const id of ids) {
      if (existingIds.has(id)) continue;
      try {
        const detail = await api(`/api/shortlists/${shortlist.id}/items`, {
          method: "POST",
          body: JSON.stringify({ creator_id: id }),
        });
        state.shortlistCreatorIds = new Set(detail.items.map((i) => i.creator_id));
        updateStickyBar(detail);
        added++;
      } catch (e) {
        /* one creator failing (e.g. already added concurrently) shouldn't stop the rest */
      }
    }
    toast(`已将 ${ids.length} 项中的 ${added} 项加入候选名单。`);
    state.selectedRowIds.clear();
    renderTable();
    updateBulkBar();
  } catch (err) {
    toast("批量添加失败：" + err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
});

// ---------------------------------------------------------------------------
// Column visibility
// ---------------------------------------------------------------------------

function applyColumnVisibility() {
  document.querySelectorAll("[data-col]").forEach((el) => {
    el.style.display = state.hiddenColumns.has(el.dataset.col) ? "none" : "";
  });
}

function renderColumnsMenu() {
  const menu = document.getElementById("columnsMenu");
  menu.innerHTML = TOGGLEABLE_COLUMNS.map(
    (c) =>
      `<label class="filter-check"><input type="checkbox" data-col-toggle="${c.key}" ${state.hiddenColumns.has(c.key) ? "" : "checked"} />${escapeHtml(c.label)}</label>`
  ).join("");
  menu.querySelectorAll("[data-col-toggle]").forEach((cb) => {
    cb.addEventListener("change", () => {
      const key = cb.dataset.colToggle;
      if (cb.checked) state.hiddenColumns.delete(key);
      else state.hiddenColumns.add(key);
      localStorage.setItem("mango_kol_hidden_cols", JSON.stringify([...state.hiddenColumns]));
      applyColumnVisibility();
    });
  });
}

document.getElementById("columnsToggleBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = document.getElementById("columnsMenu");
  if (menu.classList.contains("hidden")) {
    renderColumnsMenu();
    menu.classList.remove("hidden");
  } else {
    menu.classList.add("hidden");
  }
});
document.addEventListener("click", (e) => {
  const wrap = document.querySelector(".columns-toggle-wrap");
  if (wrap && !wrap.contains(e.target)) document.getElementById("columnsMenu").classList.add("hidden");
});

document.getElementById("exportFilteredBtn").addEventListener("click", () => {
  window.location.href = "/api/creators/export.csv?" + buildQuery();
});

function deliverableCellHtml(r) {
  if (r.quote_min_usd === null) {
    return `<span class="muted">见原始报价</span>`;
  }
  const extra = r.deliverable_count > 1 ? `<span class="deliverable-more">另有 ${r.deliverable_count - 1} 项交付物</span>` : "";
  const priceText = r.quote_min_usd === r.quote_max_usd ? fmtUsd(r.quote_min_usd) : `起价 ${fmtUsd(r.quote_min_usd)}`;
  return `<div class="deliverable-cell"><span class="deliverable-price">${priceText}</span>${extra}</div>`;
}

function cleanText(v) {
  // Defensive against models emitting the literal word "null"/"n/a" instead
  // of a real empty value (see backend/classify.py normalization).
  if (!v) return null;
  const s = String(v).trim();
  return s && !["null", "none", "n/a", "unknown"].includes(s.toLowerCase()) ? s : null;
}

function categoriesCellHtml(categories) {
  const clean = (categories || []).map(cleanText).filter(Boolean);
  if (!clean.length) return '<span class="muted">-</span>';
  const shown = clean.slice(0, 2).join(", ");
  const extra = clean.length > 2 ? ` <span class="muted">+${clean.length - 2}</span>` : "";
  return escapeHtml(shown) + extra;
}

function rowHtml(r) {
  const cpm = r.cpm_min === null ? '<span class="muted">-</span>' : r.cpm_min === r.cpm_max ? `$${r.cpm_min}` : `$${r.cpm_min}-$${r.cpm_max}`;
  const contacts = [r.has_email ? "邮箱" : null, r.has_telegram ? "Telegram" : null].filter(Boolean).join("、") || '<span class="muted">-</span>';
  const inShortlist = state.shortlistCreatorIds.has(r.id);
  const isSelected = state.selectedRowIds.has(r.id);
  const confDot = r.classification_confidence ? `<span class="confidence-dot ${r.classification_confidence}" title="分类置信度：${escapeAttr(CONFIDENCE_LABEL_ZH[r.classification_confidence] || r.classification_confidence)}"></span>` : "";
  const regionLang = [cleanText(r.region), cleanText(r.language)].filter(Boolean).join(" / ");
  return `
    <tr data-creator-id="${r.id}" class="${isSelected ? "row-selected" : ""}">
      <td class="col-select" onclick="event.stopPropagation()"><input type="checkbox" class="row-checkbox row-select-checkbox" data-creator-id="${r.id}" ${isSelected ? "checked" : ""} /></td>
      <td class="name-cell">
        <div class="creator-cell">
          ${avatarHtml(r.display_name, r.avatar_url)}
          <div>
            <div class="creator-name">${escapeHtml(r.display_name)}</div>
            <div class="creator-handle">${r.handle ? "@" + escapeHtml(r.handle) : ""}</div>
          </div>
        </div>
      </td>
      <td><span class="class-badge ${classTier(r.creator_class)}">${confDot}${escapeHtml(classLabel(r.creator_class))}</span></td>
      <td data-col="platform">${platformBadgeHtml(r.platform, r.platform_raw)}</td>
      <td data-col="category">${categoriesCellHtml(r.categories)}</td>
      <td data-col="region">${regionLang ? escapeHtml(regionLang) : '<span class="muted">-</span>'}</td>
      <td class="num">${fmtNum(r.followers)}</td>
      <td class="num" data-col="avg_views">${fmtNum(r.avg_views)}</td>
      <td class="num" data-col="engagement">${r.engagement_rate === null ? '<span class="muted">-</span>' : r.engagement_rate + "%"}</td>
      <td>${deliverableCellHtml(r)}</td>
      <td class="num quote-text" data-col="cpm">${cpm}</td>
      <td data-col="contact">${contacts}</td>
      <td><button class="add-btn ${inShortlist ? "added" : ""}" data-creator-id="${r.id}">${inShortlist ? "已添加" : "添加"}</button></td>
    </tr>`;
}

function renderPagination() {
  const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
  const el = document.getElementById("pagination");
  if (totalPages <= 1) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = `
    <button class="btn btn-ghost btn-sm" id="prevPage" ${state.page <= 1 ? "disabled" : ""}>上一页</button>
    <span>第 ${state.page} / ${totalPages} 页</span>
    <button class="btn btn-ghost btn-sm" id="nextPage" ${state.page >= totalPages ? "disabled" : ""}>下一页</button>`;
  const prev = document.getElementById("prevPage");
  const next = document.getElementById("nextPage");
  if (prev) prev.addEventListener("click", () => { state.page--; loadCreators(); });
  if (next) next.addEventListener("click", () => { state.page++; loadCreators(); });
}

// ---------------------------------------------------------------------------
// Top controls: tabs, search, sort, batch enrich
// ---------------------------------------------------------------------------

document.getElementById("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab");
  if (!btn) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  btn.classList.add("active");
  state.tab = btn.dataset.tab;
  state.filters.creator_class = new Set();
  state.page = 1;
  state.selectedRowIds.clear();
  updateBulkBar();
  renderFilters();
  loadCreators();
});

let searchDebounce;
document.getElementById("search").addEventListener("input", (e) => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    state.search = e.target.value;
    state.page = 1;
    loadCreators();
  }, 250);
});

document.getElementById("sortField").addEventListener("change", (e) => {
  state.sort = e.target.value;
  loadCreators();
});
document.getElementById("sortOrderBtn").addEventListener("click", (e) => {
  state.order = state.order === "desc" ? "asc" : "desc";
  e.target.textContent = state.order === "desc" ? "从高到低" : "从低到高";
  loadCreators();
});

document.getElementById("batchEnrichBtn").addEventListener("click", async () => {
  const candidates = state.rows.filter((r) => ENRICHABLE_PLATFORMS.has((r.platform || "").toUpperCase()) && !r.last_enriched_at).map((r) => r.id);
  if (!candidates.length) {
    toast("本页没有需要刷新的行：符合条件的行都已有数据，或本页没有可补充数据的平台。");
    return;
  }
  const btn = document.getElementById("batchEnrichBtn");
  btn.disabled = true;
  const originalLabel = btn.textContent;
  btn.textContent = `正在刷新 ${candidates.length} 项...`;
  try {
    const result = await api("/api/enrich/batch", {
      method: "POST",
      body: JSON.stringify({ creator_ids: candidates, classify: true }),
    });
    const errors = result.results.flatMap((r) => (r.accounts || []).filter((a) => a.status === "error"));
    toast(`已刷新 ${candidates.length - errors.length}/${candidates.length} 项。${errors.length ? "部分失败，详情见控制台。" : ""}`, errors.length > 0);
    if (errors.length) console.warn("Enrichment errors:", errors);
    loadCreators();
  } catch (err) {
    toast("刷新失败：" + err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
});

// ---------------------------------------------------------------------------
// Creator detail drawer
// ---------------------------------------------------------------------------

let drawerReturnFocus = null;
function closeDrawer() {
  const drawer = document.getElementById("drawer");
  drawer.classList.add("hidden");
  drawer.setAttribute("aria-hidden", "true");
  document.getElementById("drawerOverlay").classList.add("hidden");
  if (drawerReturnFocus && document.contains(drawerReturnFocus)) drawerReturnFocus.focus();
  drawerReturnFocus = null;
}
document.getElementById("drawerClose").addEventListener("click", closeDrawer);
document.getElementById("drawerOverlay").addEventListener("click", closeDrawer);

function topOpenDialog() {
  return ["managerModal", "shortlistModal", "drawer"]
    .map((id) => document.getElementById(id))
    .find((element) => element && !element.classList.contains("hidden"));
}

function trapDialogFocus(dialog, event) {
  const focusable = [...dialog.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])')]
    .filter((element) => !element.closest(".hidden"));
  if (!focusable.length) {
    event.preventDefault();
    dialog.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    const dialog = topOpenDialog();
    if (dialog?.id === "managerModal") closeManager();
    else if (dialog?.id === "shortlistModal") closeShortlistModal();
    else if (dialog?.id === "drawer") closeDrawer();
  } else if (e.key === "Tab") {
    const dialog = topOpenDialog();
    if (dialog) trapDialogFocus(dialog, e);
  }
});

async function openDrawer(creatorId) {
  const drawer = document.getElementById("drawer");
  drawer.classList.remove("company-workspace");
  const overlay = document.getElementById("drawerOverlay");
  const body = document.getElementById("drawerBody");
  if (drawer.classList.contains("hidden")) drawerReturnFocus = document.activeElement;
  overlay.classList.remove("hidden");
  drawer.classList.remove("hidden");
  drawer.setAttribute("aria-hidden", "false");
  document.getElementById("drawerClose").focus();
  body.innerHTML = '<div class="empty-state">加载中</div>';
  try {
    const c = await api(`/api/creators/${creatorId}`);
    body.innerHTML = drawerHtml(c);
    wireDrawer(c);
  } catch (err) {
    renderRetryState(body, "暂时无法加载创作者详情", () => openDrawer(creatorId), err.message);
  }
}

const ENRICHABLE_PLATFORMS = new Set(["X", "YOUTUBE", "INSTAGRAM", "TIKTOK"]);

function drawerHtml(c) {
  const realXAccount = c.social_accounts.find((a) => a.platform.toUpperCase() === "X" && a.handle);
  const enrichableAccount = realXAccount || c.social_accounts.find((a) => ENRICHABLE_PLATFORMS.has(a.platform.toUpperCase()) && a.handle);
  const xAccount = enrichableAccount || c.social_accounts[0];

  const platformRow = c.social_accounts
    .map((a) => `<a href="${escapeAttr(a.profile_url || "#")}" target="_blank" rel="noopener">${platformBadgeHtml(a.platform, a.platform_raw)}</a>`)
    .join("");

  const rateRows = c.rate_cards
    .map(
      (rc) => `
      <div class="rate-row ${rc.is_confident ? "" : "low-confidence"}">
        <span class="deliverable">${escapeHtml(rc.deliverable)}${rc.platform && rc.platform !== xAccount?.platform ? ` (${escapeHtml(rc.platform)})` : ""}</span>
        <span class="amount">${
          rc.quote_amount_usd !== null
            ? fmtUsd(rc.quote_amount_usd) + (rc.quote_currency !== "USD" ? ` (${rc.quote_amount} ${rc.quote_currency})` : "") + (rc.cpm !== null ? `，CPM $${rc.cpm}` : "")
            : "见原始报价"
        }</span>
      </div>`
    )
    .join("") || '<div class="muted">暂无已解析的报价。</div>';

  const rawQuotes = [...new Set(c.rate_cards.map((rc) => rc.raw_quote_text).filter(Boolean))];

  const contactRows = c.contacts.map((ct) => `<div class="kv-grid"><span class="k">${escapeHtml(ct.method_type)}</span><span class="v">${escapeHtml(ct.value)}</span></div>`).join("") || '<div class="muted">暂无联系方式记录。</div>';

  const posts = (xAccount?.recent_content || [])
    .slice(0, 8)
    .map(
      (p) => `
      <div class="post-item">
        ${p.is_pinned ? "[置顶] " : ""}${p.is_repost ? "[转发] " : ""}${escapeHtml((p.text || "").slice(0, 220))}
        <div class="post-meta">${p.posted_at || ""}，${p.views !== null && p.views !== undefined ? fmtNum(p.views) + " 次播放" : "播放量未知"}，${p.likes !== null && p.likes !== undefined ? `${fmtNum(p.likes)} 赞` : "点赞未知"}，${p.replies !== null && p.replies !== undefined ? `${fmtNum(p.replies)} 回复` : "回复未知"}${p.reposts !== null && p.reposts !== undefined ? `，${fmtNum(p.reposts)} 转发` : "，转发未知"}</div>
      </div>`
    )
    .join("") || '<div class="muted">尚未拉取到最近动态。</div>';

  const sponsors = (xAccount?.sponsors || []).map((s) => `${escapeHtml(s.project_name)}（${s.mention_count} 次）`).join("、") || "暂未检测到";

  const sponsorshipHistory = c.sponsorship_history || [];
  const confirmedPaidCount = sponsorshipHistory.filter((s) => s.disclosure_type === "paid_sponsorship" && s.review_status === "confirmed").length;
  const observedPaidCount = sponsorshipHistory.filter((s) => s.disclosure_type === "paid_sponsorship" && s.review_status !== "confirmed").length;
  const sponsorCompanies = [...new Set(sponsorshipHistory.map((s) => s.company_name).filter(Boolean))];
  const hasPricedDeliverable = (c.rate_cards || []).some((rc) => rc.quote_amount_usd !== null && rc.quote_amount_usd !== undefined);
  const roleSummary = STRATEGIC_CLASSES.includes(c.creator_class)
    ? "战略影响力 / 核心创作者候选"
    : DISTRIBUTION_CLASSES.includes(c.creator_class)
    ? "KOC / 分发供给"
    : MEDIA_CLASSES.includes(c.creator_class)
    ? "媒体 / 社区渠道；不进入创作者推荐池"
    : "待复核；仅以低优先级、明确标注的方式进入候选区，加入前须人工确认分类";
  const creatorDecisionSummary = `
    <div class="section exec-summary creator-decision-summary">
      <div class="section-title">合作判断摘要</div>
      <div class="op-verify-grid">
        <div><strong>当前角色：</strong>${escapeHtml(roleSummary)}</div>
        <div><strong>Mango 建联：</strong>尚未单独建模；${c.contacts.length ? `已记录 ${c.contacts.length} 个联系渠道` : "暂无联系方式记录"}</div>
        <div><strong>真实报价：</strong>${hasPricedDeliverable ? `已有 ${c.rate_cards.filter((rc) => rc.quote_amount_usd !== null && rc.quote_amount_usd !== undefined).length} 个已解析报价` : "暂无已解析的金额报价"}</div>
        <div><strong>历史合作证据：</strong>${confirmedPaidCount ? `${confirmedPaidCount} 条付费证据已人工复核` : observedPaidCount ? `${observedPaidCount} 条付费观察待复核` : sponsorshipHistory.length ? `${sponsorshipHistory.length} 条非付费或类型未定记录` : "暂无记录"}</div>
        <div><strong>可能相关公司：</strong>${sponsorCompanies.length ? escapeHtml(sponsorCompanies.slice(0, 6).join("、")) : "暂无"}；历史内容不证明认识预算负责人或愿意引荐</div>
        <div><strong>用于具体合作活动前：</strong>仍需按目标受众、地区、平台、交付物与当前档期逐项确认</div>
      </div>
    </div>`;
  const commercialProfile = c.commercial_profile || {};
  const repeatObservationText = (c.repeat_paid_observations || [])
    .filter((row) => row.status !== "confirmed_repeat")
    .map((row) => `${row.company}（${row.observation_count} 条：${row.confirmed_count} 条已确认，${row.unreviewed_count} 条待复核）`)
    .join("、");
  const creatorCommercialProfile = `<div class="section creator-commercial-profile">
    <div class="section-title">商业可用性</div>
    <div class="op-verify-grid">
      <div><strong>报价鲜度：</strong>${escapeHtml(commercialProfile.quote_freshness || "未记录")} · ${escapeHtml(commercialProfile.quote_freshness_note || "发方案前需复价")}</div>
      <div><strong>受众匹配：</strong>${escapeHtml((commercialProfile.audience_fit || []).join("、") || "待核实")}</div>
      <div><strong>Mango 关系：</strong>${escapeHtml(commercialProfile.mango_relationship || "未记录")}</div>
      <div><strong>建议角色：</strong>${escapeHtml(commercialProfile.campaign_role || roleSummary)}</div>
      <div><strong>重复且已确认的付费合作：</strong>${escapeHtml((c.repeat_confirmed_paid_sponsor_companies || []).join("、") || "未发现")}</div>
      <div><strong>重复付费观察待复核：</strong>${escapeHtml(repeatObservationText || "未发现")}</div>
      <div><strong>竞争/排他风险：</strong>${escapeHtml((commercialProfile.competitive_conflicts || []).join("、") || "暂无历史 sponsor 记录；仍需人工问询")}</div>
      <div><strong>证据复核：</strong>${escapeHtml(commercialProfile.evidence_review_status || "待复核")}</div>
    </div>
  </div>`;

  const enrichLabel = enrichableAccount ? `从 ${enrichableAccount.platform} 刷新${state.status.openai_configured ? " + 分类" : ""}` : "刷新数据";
  const enrichSourceName = { X: "X", YOUTUBE: "YouTube", INSTAGRAM: "Instagram", TIKTOK: "TikTok" }[enrichableAccount?.platform.toUpperCase()];
  const enrichConfigured = enrichableAccount
    ? enrichableAccount.platform.toUpperCase() === "X"
      ? state.status.rapid_x_configured
      : enrichableAccount.platform.toUpperCase() === "YOUTUBE"
      ? state.status.youtube_configured
      : state.status.scrapecreators_configured
    : false;

  return `
    <div class="detail-head">
      ${avatarHtml(c.display_name, xAccount?.avatar_url, "lg")}
      <div>
        <div class="detail-name">${escapeHtml(c.display_name)}</div>
        <div class="detail-handle">${c.handle ? "@" + escapeHtml(c.handle) : ""}</div>
        <div class="detail-platform-row">${platformRow}</div>
      </div>
    </div>

    ${creatorDecisionSummary}
    ${creatorCommercialProfile}

    <div class="section">
      <div class="section-title">分类</div>
      <div class="tag-editable">
        <select class="inline-select" id="editClass">
          ${ALL_CLASSES.map((opt) => `<option value="${opt}" ${opt === c.creator_class ? "selected" : ""}>${classLabel(opt)}</option>`).join("")}
        </select>
        <select class="inline-select" id="editPromo">
          <option value="">推广等级：无</option>
          ${["Low", "Medium", "High"].map((opt) => `<option value="${opt}" ${opt === c.promotion_level ? "selected" : ""}>${PROMO_LABEL_ZH[opt]}</option>`).join("")}
        </select>
        <button class="btn btn-ghost btn-sm" id="saveClassBtn">保存</button>
      </div>
      ${c.creator_class_locked ? '<div class="lock-note">已人工设置，自动分类不会覆盖。</div>' : ""}
      ${c.creator_class_reason ? `<div class="reason-text">${escapeHtml(c.creator_class_reason)}</div>` : ""}
      ${c.classification_confidence ? `<div class="provenance-note">分类置信度：${escapeHtml(CONFIDENCE_LABEL_ZH[c.classification_confidence] || c.classification_confidence)}${c.creator_class_source ? "；依据已记录在系统中" : ""}</div>` : ""}
    </div>

    <div class="section">
      <div class="section-title">基本资料</div>
      <div class="kv-grid">
        <span class="k">粉丝数</span><span class="v">${fmtNum(c.followers)}</span>
        <span class="k">平均播放量</span><span class="v">${fmtNum(c.avg_views)}</span>
        <span class="k">互动率</span><span class="v">${c.engagement_rate === null ? "-" : c.engagement_rate + "%"}</span>
        <span class="k">发布频率</span><span class="v">${escapeHtml(xAccount?.posting_frequency || "-")}</span>
        <span class="k">原创/转发</span><span class="v">${xAccount?.original_repost_ratio !== null && xAccount?.original_repost_ratio !== undefined ? Math.round(xAccount.original_repost_ratio * 100) + "% 原创" : "-"}</span>
        <span class="k">推广关键词命中率（启发式）</span><span class="v">${xAccount?.promotional_content_ratio !== null && xAccount?.promotional_content_ratio !== undefined ? Math.round(xAccount.promotional_content_ratio * 100) + "%" : "-"}</span>
        <span class="k">已认证</span><span class="v">${xAccount?.verified ? "是" : "-"}</span>
        <span class="k">上次刷新</span><span class="v">${c.last_enriched_at ? new Date(c.last_enriched_at).toLocaleString() : "从未"}</span>
      </div>
      ${xAccount?.bio ? `<div class="bio-text">${escapeHtml(xAccount.bio)}</div>` : ""}
      ${xAccount?.content_summary ? `<div class="reason-text">${escapeHtml(xAccount.content_summary)}</div>` : ""}
      ${xAccount?.promotional_ratio_note ? `<div class="provenance-note">${escapeHtml(xAccount.promotional_ratio_note)}</div>` : ""}
      ${xAccount?.metric_coverage?.is_partial ? `<div class="provenance-note">互动指标覆盖不完整：样本 ${xAccount.metric_coverage.sample_size} 条；缺失指标显示为“未知”，不会当作 0。</div>` : ""}
      ${xAccount?.enrichment_error ? `<div class="provenance-note">${escapeHtml(xAccount.enrichment_error)}</div>` : ""}
      <div style="margin-top:10px">
        <button class="btn btn-ghost btn-sm" id="enrichBtn" ${enrichConfigured && enrichableAccount ? "" : "disabled"}>${enrichLabel}</button>
        ${!enrichableAccount ? '<span class="provenance-note">该创作者没有 X、YouTube、Instagram 或 TikTok 账号记录，暂无可用的实时数据源。</span>' : ""}
        ${enrichableAccount && !enrichConfigured && xAccount?.content_snapshot_available ? `<span class="provenance-note">${escapeHtml(enrichSourceName || "该平台")} 实时刷新暂不可用；以下仍显示 ${escapeHtml(String(xAccount.last_enriched_at || "").slice(0, 10))} 的缓存快照。</span>` : ""}
        ${enrichableAccount && !enrichConfigured && !xAccount?.content_snapshot_available ? `<span class="provenance-note">${escapeHtml(enrichSourceName || "该平台")} 数据源暂不可用，且当前没有可展示的缓存快照。</span>` : ""}
      </div>
    </div>

    <div class="section">
      <div class="section-title">地区 / 语言 / 类别</div>
      <div class="tag-editable">
        <input class="inline-input" id="editRegion" placeholder="地区" value="${escapeAttr(cleanText(c.region) || "")}" style="width:110px" />
        <input class="inline-input" id="editLanguage" placeholder="语言" value="${escapeAttr(cleanText(c.language) || "")}" style="width:110px" />
        <input class="inline-input" id="editCategories" placeholder="类别，用逗号分隔" value="${escapeAttr((c.categories || []).map(cleanText).filter(Boolean).join(", "))}" style="width:220px" />
        <button class="btn btn-ghost btn-sm" id="saveTagsBtn">保存</button>
      </div>
    </div>

    <div class="section">
      <div class="section-title">交付物与报价</div>
      ${rateRows}
      ${rawQuotes.length ? `<div class="raw-quote-block"><strong>原始报价文本：</strong>\n\n${rawQuotes.map(escapeHtml).join("\n---\n")}</div>` : ""}
    </div>

    <div class="section">
      <div class="section-title">联系方式</div>
      ${contactRows}
    </div>

    <div class="section">
      <div class="section-title">近期推广 / 提及</div>
      <div>${sponsors}</div>
    </div>

    ${sponsorshipHistoryHtml(c)}

    <div class="section">
      <div class="section-title">近期动态</div>
      ${posts}
    </div>

    <div class="section internal-only">
      <div class="section-title">内部备注</div>
      <textarea class="notes-area" id="editNotes">${escapeHtml(c.internal_notes || "")}</textarea>
      <button class="btn btn-ghost btn-sm" id="saveNotesBtn" style="margin-top:6px">保存备注</button>
    </div>

    <div class="section">
      <button class="btn btn-primary" id="drawerAddBtn">${state.shortlistCreatorIds.has(c.id) ? "已在候选名单中" : "加入候选名单"}</button>
    </div>
  `;
}

function sponsorshipHistoryHtml(c) {
  const history = c.sponsorship_history || [];
  if (!history.length) return "";
  const repeat = c.repeat_confirmed_paid_sponsor_companies || [];
  const rows = history
    .map((s) => {
      const disclosureLabel = sponsorshipDisclosureLabel(s);
      return `
      <div class="sponsorship-row">
        <div class="top">
          <span class="company-link" data-company-id="${escapeAttr(s.company_id)}">${escapeHtml(s.company_name || "未知公司")}</span>
          <span class="disclosure-tag disclosure-${escapeAttr(s.disclosure_type || "unknown")}">${escapeHtml(disclosureLabel)}</span>
          <span class="review-status-tag review-${escapeAttr(s.review_status || "unreviewed")}">${escapeHtml(REVIEW_STATUS_ZH[s.review_status || "unreviewed"] || "待审核")}</span>
        </div>
        <div class="muted">${s.platform ? escapeHtml(s.platform) + "，" : ""}${escapeHtml(sponsorshipDateLabel(s))} · ${escapeHtml(evidenceConfidenceLabel(s.confidence))} · ${s.verified_at ? `人工复核于 ${escapeHtml(String(s.verified_at).slice(0, 10))}` : "未人工复核"}${s.content_url ? ` -- <a href="${escapeAttr(s.content_url)}" target="_blank" rel="noopener">查看证据</a>` : ""}</div>
        ${s.evidence_text ? `<div class="reason-text">"${escapeHtml(s.evidence_text)}"</div>` : ""}
      </div>`;
    })
    .join("");
  return `
    <div class="section">
      <div class="section-title">合作 / 推广证据</div>
      <div class="provenance-note">“付费观察”是公开材料中的待审核分类，不等于 Mango 已确认的付费合作。</div>
      ${repeat.length ? `<div class="reason-text">多次已复核付费合作方：${escapeHtml(repeat.join("、"))}</div>` : ""}
      ${rows}
    </div>`;
}

function wireDrawer(c) {
  document.querySelectorAll("#drawerBody .company-link[data-company-id]").forEach((el) => {
    el.addEventListener("click", () => openCompanyDrawer(el.dataset.companyId));
  });

  document.getElementById("saveClassBtn").addEventListener("click", async () => {
    const creator_class = document.getElementById("editClass").value;
    const promotion_level = document.getElementById("editPromo").value || null;
    try {
      await api(`/api/creators/${c.id}`, { method: "PATCH", body: JSON.stringify({ creator_class, promotion_level }) });
      toast("分类已保存。");
      openDrawer(c.id);
      loadCreators();
    } catch (err) {
      toast("保存失败：" + err.message, true);
    }
  });

  document.getElementById("saveTagsBtn").addEventListener("click", async () => {
    const region = document.getElementById("editRegion").value || null;
    const language = document.getElementById("editLanguage").value || null;
    const categories = document
      .getElementById("editCategories")
      .value.split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      await api(`/api/creators/${c.id}`, { method: "PATCH", body: JSON.stringify({ region, language, categories }) });
      toast("已保存。");
      loadCreators();
    } catch (err) {
      toast("保存失败：" + err.message, true);
    }
  });

  document.getElementById("saveNotesBtn").addEventListener("click", async () => {
    const internal_notes = document.getElementById("editNotes").value;
    try {
      await api(`/api/creators/${c.id}`, { method: "PATCH", body: JSON.stringify({ internal_notes }) });
      toast("备注已保存。");
    } catch (err) {
      toast("保存失败：" + err.message, true);
    }
  });

  const enrichBtn = document.getElementById("enrichBtn");
  if (enrichBtn) {
    enrichBtn.addEventListener("click", async (e) => {
      const originalLabel = e.target.textContent;
      e.target.disabled = true;
      e.target.textContent = "刷新中...";
      try {
        const result = await api(`/api/creators/${c.id}/enrich`, { method: "POST" });
        const errored = (result.accounts || []).find((a) => a.status === "error");
        const classifyIssue = (result.accounts || []).find((a) => a.classify_error);
        if (errored) {
          toast("刷新失败：" + errored.error, true);
        } else if (classifyIssue) {
          toast(`已刷新。跳过分类：${classifyIssue.classify_error}`, true);
        } else {
          toast("已刷新。");
        }
        openDrawer(c.id);
        loadCreators();
      } catch (err) {
        toast("刷新失败：" + err.message, true);
        e.target.disabled = false;
        e.target.textContent = originalLabel;
      }
    });
  }

  document.getElementById("drawerAddBtn").addEventListener("click", async () => {
    const changed = await toggleShortlist(c.id);
    if (changed) openDrawer(c.id);
  });
}

// ---------------------------------------------------------------------------
// Shortlist
// ---------------------------------------------------------------------------

// Adding a creator is allowed to mutate an explicitly selected, named list
// only.  It must never manufacture a formal record as a side effect: users
// need to choose the commercial context before creator selection is saved.
async function ensureShortlist() {
  if (state.shortlistId) {
    try {
      return await api(`/api/shortlists/${state.shortlistId}`);
    } catch (e) {
      if (e.status !== 404) {
        toast("暂时无法确认当前候选名单：" + e.message, true);
        return null;
      }
      state.shortlistId = null;
      state.shortlistCreatorIds = new Set();
      localStorage.removeItem("mango_kol_shortlist_id");
      updateStickyBar(null);
    }
  }
  toast("请先创建或选择一个有名称的候选名单。", true);
  managerNeedsSelection = true;
  await openManager();
  return null;
}

async function refreshShortlistState() {
  if (!state.shortlistId) {
    state.shortlistCreatorIds = new Set();
    updateStickyBar(null);
    return;
  }
  try {
    const detail = await api(`/api/shortlists/${state.shortlistId}`);
    state.shortlistCreatorIds = new Set(detail.items.map((i) => i.creator_id));
    updateStickyBar(detail);
  } catch (e) {
    if (e.status !== 404) {
      toast("暂时无法刷新当前候选名单：" + e.message, true);
      return;
    }
    state.shortlistId = null;
    state.shortlistCreatorIds = new Set();
    localStorage.removeItem("mango_kol_shortlist_id");
    updateStickyBar(null);
  }
}

function updateStickyBar(detail) {
  const bar = document.getElementById("stickyBar");
  const summary = document.getElementById("stickySummary");
  const currentButton = document.getElementById("openCurrentShortlistBtn");
  currentButton.disabled = !state.shortlistId;
  currentButton.textContent = "打开当前名单";
  currentButton.title = detail?.name ? `当前：${detail.name}` : "尚未选择候选名单";
  if (!detail || detail.selected_count === 0) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  let text = `已选 ${detail.selected_count} 位创作者，共 ${fmtUsd(detail.total_spend_usd)}`;
  if (detail.budget_usd !== null && detail.budget_usd !== undefined) {
    text += `，剩余 ${fmtUsd(detail.remaining_budget_usd)}`;
  }
  summary.innerHTML = escapeHtml(text) + (detail.over_budget ? '<span class="over-flag">超出预算</span>' : "");
}

async function toggleShortlist(creatorId) {
  const shortlist = await ensureShortlist();
  if (!shortlist) return false;
  const already = state.shortlistCreatorIds.has(creatorId);
  try {
    if (already) {
      const item = shortlist.items.find((i) => i.creator_id === creatorId);
      const detail = item ? await api(`/api/shortlists/${shortlist.id}/items/${item.id}`, { method: "DELETE" }) : shortlist;
      state.shortlistCreatorIds = new Set(detail.items.map((i) => i.creator_id));
      updateStickyBar(detail);
      toast("已从候选名单移除。");
    } else {
      const detail = await api(`/api/shortlists/${shortlist.id}/items`, {
        method: "POST",
        body: JSON.stringify({ creator_id: creatorId }),
      });
      state.shortlistCreatorIds = new Set(detail.items.map((i) => i.creator_id));
      updateStickyBar(detail);
      toast("已加入候选名单。");
    }
    renderTable();
    return true;
  } catch (err) {
    toast("更新候选名单失败：" + err.message, true);
    return false;
  }
}

document.getElementById("openShortlistBtn").addEventListener("click", openShortlistModal);
document.getElementById("openCurrentShortlistBtn").addEventListener("click", openShortlistModal);

let shortlistReturnFocus = null;
function closeShortlistModal() {
  const modal = document.getElementById("shortlistModal");
  if (modal.classList.contains("hidden")) return;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  document.getElementById("shortlistOverlay").classList.add("hidden");
  if (shortlistReturnFocus && document.contains(shortlistReturnFocus)) shortlistReturnFocus.focus();
  shortlistReturnFocus = null;
}
document.getElementById("shortlistClose").addEventListener("click", closeShortlistModal);
document.getElementById("shortlistOverlay").addEventListener("click", closeShortlistModal);

async function openShortlistModal() {
  if (!state.shortlistId) {
    toast("候选名单为空，请先添加一位创作者。");
    return;
  }
  document.getElementById("shortlistOverlay").classList.remove("hidden");
  const modal = document.getElementById("shortlistModal");
  if (modal.classList.contains("hidden")) shortlistReturnFocus = document.activeElement;
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.getElementById("shortlistClose").focus();
  await renderShortlistModal();
}

async function renderShortlistModal() {
  const body = document.getElementById("shortlistBody");
  body.innerHTML = '<div class="empty-state">加载中</div>';
  try {
    const detail = await api(`/api/shortlists/${state.shortlistId}`);
    body.innerHTML = shortlistModalHtml(detail);
    wireShortlistModal(detail);
    updateStickyBar(detail);
  } catch (err) {
    renderRetryState(body, "暂时无法加载候选名单", renderShortlistModal, err.message);
  }
}

function shortlistModalHtml(detail) {
  const pricingAssumptions = detail.pricing_assumptions || null;
  const serviceFeeUsd = pricingAssumptions?.service_fee_pct != null
    ? detail.total_spend_usd * Number(pricingAssumptions.service_fee_pct) / 100
    : null;
  const contingencyUsd = pricingAssumptions?.contingency_pct != null
    ? detail.total_spend_usd * Number(pricingAssumptions.contingency_pct) / 100
    : null;
  const localizationFeeUsd = pricingAssumptions?.localization_fee_usd != null
    ? Number(pricingAssumptions.localization_fee_usd)
    : null;
  const currentClientTotal = pricingAssumptions
    ? detail.total_spend_usd + (serviceFeeUsd || 0) + (contingencyUsd || 0) + (localizationFeeUsd || 0)
    : null;
  const rows = detail.items
    .map((item) => {
      const selectedRateCard = (item.rate_cards || []).find((rc) => rc.id === item.rate_card_id);
      const sourceQuote = selectedRateCard?.quote_amount_usd;
      const options = item.rate_cards
        .map((rc) => `<option value="${rc.id}" ${rc.id === item.rate_card_id ? "selected" : ""}>${escapeHtml(rc.deliverable)}, ${rc.quote_amount_usd !== null ? fmtUsd(rc.quote_amount_usd) : "无"}</option>`)
        .join("");
      return `
      <tr data-item-id="${item.item_id}">
        <td>
          <div class="creator-cell">
            ${avatarHtml(item.display_name, item.avatar_url)}
            <div>
              <div class="creator-name">${escapeHtml(item.display_name)}</div>
              <div class="creator-handle">${item.handle ? "@" + escapeHtml(item.handle) : ""} <span class="class-badge ${classTier(item.creator_class)}">${escapeHtml(classLabel(item.creator_class))}</span></div>
              ${item.notes ? `<div class="reason-text"><strong>选择理由 / 备注：</strong>${escapeHtml(item.notes)}</div>` : ""}
            </div>
          </div>
        </td>
        <td>
          <select class="small-select item-deliverable" data-item-id="${item.item_id}">
            <option value="">未选择交付物</option>
            ${options}
          </select>
        </td>
        <td class="quote-editor-cell">
          <div class="item-quote-editor">
            <span aria-hidden="true">$</span>
            <label class="sr-only" for="itemQuoteOverride-${item.item_id}">${escapeHtml(item.display_name)} 的本次活动手工报价（美元）</label>
            <input id="itemQuoteOverride-${item.item_id}" class="item-quote-override" data-item-id="${item.item_id}" type="number" min="0" step="0.01" inputmode="decimal" value="${item.quote_is_override ? escapeAttr(item.quote_usd ?? "") : ""}" placeholder="${sourceQuote !== null && sourceQuote !== undefined ? escapeAttr(sourceQuote) : "未定"}" />
            <button class="btn btn-ghost btn-sm item-quote-save" type="button" data-item-id="${item.item_id}">应用</button>
          </div>
          <div class="quote-provenance">${
            item.quote_is_override
              ? `当前 ${fmtUsd(item.quote_usd)}：本次活动手工报价；源报价${sourceQuote !== null && sourceQuote !== undefined ? ` ${fmtUsd(sourceQuote)}` : "未记录"}。清空后应用可恢复源报价。`
              : `当前 ${fmtUsd(item.quote_usd)}：${sourceQuote !== null && sourceQuote !== undefined ? "所选交付物的源报价" : "尚未定价"}。手工报价只作用于本名单。`
          }</div>
        </td>
        <td><button class="btn btn-ghost btn-sm item-remove" data-item-id="${item.item_id}">移除</button></td>
      </tr>`;
    })
    .join("");

  return `
    <div class="shortlist-toolbar">
      <input id="shortlistName" aria-label="候选名单名称" placeholder="候选名单名称" value="${escapeAttr(detail.name)}" style="width:220px" />
      <input id="shortlistCampaign" aria-label="客户或合作活动" placeholder="客户 / 合作活动" value="${escapeAttr(detail.client_or_campaign || "")}" style="width:200px" />
      <input id="shortlistBudget" aria-label="预算（美元）" type="number" placeholder="预算 (USD)" value="${detail.budget_usd ?? ""}" style="width:130px" />
      <button class="btn btn-ghost btn-sm" id="saveShortlistMetaBtn">保存</button>
      <button class="btn btn-ghost btn-sm" id="copyTsvBtn">复制到 Google Sheets</button>
      <a class="btn btn-ghost btn-sm" href="/api/shortlists/${detail.id}/export.csv">导出 CSV</a>
      <a class="btn btn-ghost btn-sm" href="/api/shortlists/${detail.id}/export.xlsx">导出 XLSX</a>
    </div>

    ${
      detail.company_id
        ? `<div class="campaign-form">
            <label class="field-label">合作目标<input id="shortlistObjective" placeholder="例如：提升产品认知" value="${escapeAttr(detail.objective || "")}" /></label>
            <label class="field-label">目标受众<input id="shortlistAudience" placeholder="例如：AI 开发者" value="${escapeAttr(detail.target_audience || "")}" /></label>
            <label class="field-label">偏好地区<input id="shortlistRegion" placeholder="例如：北美" value="${escapeAttr(detail.region_pref || "")}" /></label>
            <label class="field-label">偏好语言<input id="shortlistLanguage" placeholder="例如：英语" value="${escapeAttr(detail.language_pref || "")}" /></label>
            <label class="field-label">目标平台<input id="shortlistPlatforms" placeholder="例如：YouTube、X" value="${escapeAttr(detail.platforms_pref || "")}" /></label>
            <label class="field-label">时间安排<input id="shortlistTiming" placeholder="例如：9 月第 2 周" value="${escapeAttr(detail.timing || "")}" /></label>
            <button class="btn btn-ghost btn-sm" id="saveCampaignFieldsBtn" style="grid-column:span 2">保存 ${escapeHtml(detail.company_name || "")} 的活动信息</button>
          </div>`
        : ""
    }

    <div class="campaign-loop-note">
      <strong>当前是可保存、可报价和可导出的合作候选名单。</strong>
      初筛理由显示在下方建议区，但加入后尚不保存逐人选择理由；导出前请人工确认受众匹配、档期和交付物。外联结果请回到公司详情记录。
    </div>

    ${pricingAssumptions ? `<div class="shortlist-commercial-breakdown"><strong>销售包商业模型</strong><span>创作者媒体费用 ${fmtUsd(detail.total_spend_usd)}</span><span>Mango 服务费 ${serviceFeeUsd == null ? "待配置" : `${fmtUsd(serviceFeeUsd)} (${pricingAssumptions.service_fee_pct}%)`}</span><span>风险预留 ${contingencyUsd == null ? "未配置" : `${fmtUsd(contingencyUsd)} (${pricingAssumptions.contingency_pct}%)`}</span><span>本地化 ${localizationFeeUsd == null ? "未配置" : fmtUsd(localizationFeeUsd)}</span><span>当前客户总价 ${fmtUsd(currentClientTotal)}</span></div>` : ""}

    <div class="budget-summary">
      <div class="budget-stat"><div class="label">已选</div><div class="value">${detail.selected_count}</div></div>
      <div class="budget-stat"><div class="label">Strategic Influence 花费</div><div class="value">${fmtUsd(detail.strategic_spend_usd ?? detail.kol_spend_usd)}</div></div>
      <div class="budget-stat"><div class="label">KOC / Distribution 花费</div><div class="value">${fmtUsd(detail.distribution_spend_usd ?? detail.koc_spend_usd)}</div></div>
      <div class="budget-stat"><div class="label">Media / Community 花费</div><div class="value">${fmtUsd(detail.media_spend_usd)}</div></div>
      <div class="budget-stat"><div class="label">Needs Review 花费</div><div class="value">${fmtUsd(detail.needs_review_spend_usd)}</div></div>
      <div class="budget-stat"><div class="label">总花费</div><div class="value">${fmtUsd(detail.total_spend_usd)}</div></div>
      <div class="budget-stat"><div class="label">剩余预算</div><div class="value ${detail.over_budget ? "over" : ""}">${detail.budget_usd !== null ? fmtUsd(detail.remaining_budget_usd) : "-"}</div></div>
      ${detail.unpriced_count ? `<div class="budget-stat"><div class="label">未定价</div><div class="value">${detail.unpriced_count}</div></div>` : ""}
    </div>

    <table class="shortlist-table">
      <thead><tr><th>创作者</th><th>交付物</th><th class="num">报价</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="4" class="muted">暂无创作者。</td></tr>'}</tbody>
    </table>

    ${detail.company_id ? '<div id="suggestedCreatorsPanel" class="suggested-creators-panel">正在加载初筛候选...</div>' : ""}
  `;
}

function _suggestedRowHtml(r, existingIds) {
  const hasPaidObservation = (r.prior_evidence_types || []).includes("paid_sponsorship");
  const tags = [
    r.prior_relationship
      ? `<span class="history-tag">${r.has_confirmed_paid_evidence
          ? "有人工复核的付费先例"
          : hasPaidObservation
          ? "有付费赞助观察（待复核）"
          : "有历史品牌记录（不代表已确认合作）"}</span>`
      : "",
    r.needs_review ? '<span class="needs-review-tag">待复核</span>' : "",
  ].join(" ");
  const priceText = r.quote_min_usd === null || r.quote_min_usd === undefined
    ? "暂无报价"
    : r.quote_min_usd === r.quote_max_usd
    ? fmtUsd(r.quote_min_usd)
    : `${fmtUsd(r.quote_min_usd)} - ${fmtUsd(r.quote_max_usd)}`;
  const metaParts = [r.platform, r.handle ? "@" + r.handle : "", r.followers ? fmtNum(r.followers) + " 粉丝" : "", priceText].filter(Boolean);
  return `
    <div class="suggested-row ${r.needs_review ? "needs-review-row" : ""}">
      <div class="info">
        <div class="name">${escapeHtml(r.display_name)} ${tags}</div>
        <div class="reasons suggested-meta">${escapeHtml(metaParts.join(" · "))}</div>
        <div class="reasons">${escapeHtml(r.reasons.join("；"))}</div>
      </div>
      <div class="suggested-actions">
        <button class="btn btn-ghost btn-sm suggested-add" data-creator-id="${r.id}" ${existingIds.has(r.id) ? "disabled" : ""}>${existingIds.has(r.id) ? "已添加" : "添加"}</button>
        <button class="link-btn suggested-unsuitable" data-creator-id="${r.id}" title="不再为该公司推荐这位创作者">不合适</button>
      </div>
    </div>`;
}

async function loadSuggestedCreators(detail) {
  const panel = document.getElementById("suggestedCreatorsPanel");
  if (!panel) return;
  try {
    const data = await api(`/api/companies/${encodeURIComponent(detail.company_id)}/suggested-creators`);
    const existingIds = new Set(detail.items.map((i) => i.creator_id));
    const recommendationById = new Map(
      [...(data.results || []), ...(data.media_channels || [])].map((row) => [Number(row.id), row])
    );
    const rows = data.results.map((r) => _suggestedRowHtml(r, existingIds)).join("");
    const mediaRows = (data.media_channels || []).map((r) => _suggestedRowHtml(r, existingIds)).join("");
    panel.innerHTML = `
      <div class="section-title">为 ${escapeHtml(detail.company_name || "此次活动")} 初筛的创作者 <span class="provenance-note inline-note">仍需按受众、地区和平台人工确认</span></div>
      ${rows || '<div class="muted">暂无初筛候选。当前没有已备案报价或历史品牌记录。</div>'}
      ${
        mediaRows
          ? `<div class="section-title" style="margin-top:14px">媒体 / 社区渠道 <span class="provenance-note" style="text-transform:none;letter-spacing:0;display:inline">分发渠道，非创作者人选</span></div>${mediaRows}`
          : ""
      }
    `;
    panel.querySelectorAll(".suggested-add").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          const creatorId = Number(btn.dataset.creatorId);
          const recommendation = recommendationById.get(creatorId);
          const reasonSnapshot = recommendation?.reasons?.length
            ? `推荐理由快照（加入时）：${recommendation.reasons.join("；")}`
            : "推荐理由快照（加入时）：当前仅有报价或历史品牌记录，仍需人工确认适配度";
          await api(`/api/shortlists/${detail.id}/items`, {
            method: "POST",
            body: JSON.stringify({ creator_id: creatorId, notes: reasonSnapshot }),
          });
          toast("已加入候选名单。");
          await refreshShortlistState();
          renderShortlistModal();
        } catch (err) {
          toast("添加失败：" + err.message, true);
        }
      });
    });
    panel.querySelectorAll(".suggested-unsuitable").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const reason = window.prompt("这位创作者为什么不适合该活动或类别？");
        if (!reason) return;
        try {
          await api(`/api/creators/${btn.dataset.creatorId}/exclusions`, {
            method: "POST",
            body: JSON.stringify({ company_id: detail.company_id, reason }),
          });
          toast("已设置为不再推荐该创作者。");
          loadSuggestedCreators(detail);
        } catch (err) {
          toast("操作失败：" + err.message, true);
        }
      });
    });
  } catch (err) {
    renderRetryState(panel, "暂时无法加载初筛候选", () => loadSuggestedCreators(detail), err.message);
  }
}

function wireShortlistModal(detail) {
  if (detail.company_id) loadSuggestedCreators(detail);

  const saveCampaignBtn = document.getElementById("saveCampaignFieldsBtn");
  if (saveCampaignBtn) {
    saveCampaignBtn.addEventListener("click", async () => {
      const objective = document.getElementById("shortlistObjective").value.trim() || null;
      const target_audience = document.getElementById("shortlistAudience").value.trim() || null;
      const region_pref = document.getElementById("shortlistRegion").value.trim() || null;
      const language_pref = document.getElementById("shortlistLanguage").value.trim() || null;
      const platforms_pref = document.getElementById("shortlistPlatforms").value.trim() || null;
      const timing = document.getElementById("shortlistTiming").value.trim() || null;
      try {
        await api(`/api/shortlists/${detail.id}`, {
          method: "PATCH",
          body: JSON.stringify({ objective, target_audience, region_pref, language_pref, platforms_pref, timing }),
        });
        toast("活动信息已保存。");
        renderShortlistModal();
      } catch (err) {
        toast("保存失败：" + err.message, true);
      }
    });
  }

  document.getElementById("saveShortlistMetaBtn").addEventListener("click", async () => {
    const nameInput = document.getElementById("shortlistName");
    const name = nameInput.value.trim();
    if (!name) {
      toast("候选名单名称不能为空。", true);
      nameInput.focus();
      return;
    }
    const client_or_campaign = document.getElementById("shortlistCampaign").value.trim() || null;
    const budgetRaw = document.getElementById("shortlistBudget").value;
    const budget_usd = budgetRaw === "" ? null : Number(budgetRaw);
    if (budgetRaw !== "" && (!Number.isFinite(budget_usd) || budget_usd < 0)) {
      toast("预算必须是 0 或更大的数字。", true);
      document.getElementById("shortlistBudget").focus();
      return;
    }
    try {
      await api(`/api/shortlists/${detail.id}`, { method: "PATCH", body: JSON.stringify({ name, client_or_campaign, budget_usd }) });
      toast("候选名单已保存。");
      renderShortlistModal();
    } catch (err) {
      toast("保存失败：" + err.message, true);
    }
  });

  document.getElementById("copyTsvBtn").addEventListener("click", async () => {
    try {
      const { tsv } = await api(`/api/shortlists/${detail.id}/export.tsv`);
      await navigator.clipboard.writeText(tsv);
      toast("已复制，可直接粘贴到 Google Sheets。");
    } catch (err) {
      toast("复制失败：" + err.message, true);
    }
  });

  document.querySelectorAll(".item-deliverable").forEach((sel) => {
    sel.addEventListener("change", async () => {
      const itemId = sel.dataset.itemId;
      const rate_card_id = sel.value ? Number(sel.value) : null;
      try {
        await api(`/api/shortlists/${detail.id}/items/${itemId}`, { method: "PATCH", body: JSON.stringify({ rate_card_id }) });
        renderShortlistModal();
      } catch (err) {
        toast("更新失败：" + err.message, true);
      }
    });
  });

  const saveQuoteOverride = async (input) => {
    const itemId = input.dataset.itemId;
    const raw = input.value.trim();
    const quote_usd_override = raw === "" ? null : Number(raw);
    if (raw !== "" && (!Number.isFinite(quote_usd_override) || quote_usd_override < 0)) {
      toast("本次活动报价必须是 0 或更大的数字；清空可恢复源报价。", true);
      input.focus();
      return;
    }
    const saveButton = document.querySelector(`.item-quote-save[data-item-id="${itemId}"]`);
    if (saveButton) saveButton.disabled = true;
    try {
      await api(`/api/shortlists/${detail.id}/items/${itemId}`, {
        method: "PATCH",
        body: JSON.stringify({ quote_usd_override }),
      });
      toast(raw === "" ? "已恢复所选交付物的源报价。" : "已应用本次活动手工报价；源报价未被修改。");
      await renderShortlistModal();
    } catch (err) {
      if (saveButton) saveButton.disabled = false;
      toast("更新报价失败：" + err.message, true);
    }
  };
  document.querySelectorAll(".item-quote-save").forEach((button) => {
    button.addEventListener("click", () => {
      const input = document.querySelector(`.item-quote-override[data-item-id="${button.dataset.itemId}"]`);
      if (input) saveQuoteOverride(input);
    });
  });
  document.querySelectorAll(".item-quote-override").forEach((input) => {
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      saveQuoteOverride(input);
    });
  });

  document.querySelectorAll(".item-remove").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api(`/api/shortlists/${detail.id}/items/${btn.dataset.itemId}`, { method: "DELETE" });
        toast("已移除。");
        await refreshShortlistState();
        renderShortlistModal();
        renderTable();
      } catch (err) {
        toast("移除失败：" + err.message, true);
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Shortlist manager (list / switch / create / delete)
// ---------------------------------------------------------------------------

document.getElementById("shortlistManagerBtn").addEventListener("click", () => {
  managerNeedsSelection = false;
  openManager();
});
document.getElementById("managerClose").addEventListener("click", closeManager);
document.getElementById("managerOverlay").addEventListener("click", closeManager);

let managerReturnFocus = null;
let managerNeedsSelection = false;
function closeManager() {
  const modal = document.getElementById("managerModal");
  if (modal.classList.contains("hidden")) return;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  document.getElementById("managerOverlay").classList.add("hidden");
  if (managerReturnFocus && document.contains(managerReturnFocus)) managerReturnFocus.focus();
  managerReturnFocus = null;
  managerNeedsSelection = false;
}

async function openManager() {
  document.getElementById("managerOverlay").classList.remove("hidden");
  const modal = document.getElementById("managerModal");
  if (modal.classList.contains("hidden")) managerReturnFocus = document.activeElement;
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.getElementById("managerClose").focus();
  await renderManager();
}

async function renderManager() {
  const body = document.getElementById("managerBody");
  body.innerHTML = '<div class="empty-state">加载中</div>';
  try {
    const shortlists = await api("/api/shortlists");
    body.innerHTML = `
      <div class="shortlist-toolbar">
        <input id="newShortlistName" aria-label="候选名单名称" placeholder="例如：Runway AI Creator Pilot" style="width:280px" />
        <button class="btn btn-primary btn-sm" id="createShortlistBtn">创建</button>
      </div>
      <table class="shortlist-table">
        <thead><tr><th>名称</th><th>合作活动</th><th class="num">已选</th><th class="num">预算</th><th></th></tr></thead>
        <tbody>
          ${
            shortlists.length
              ? shortlists
                  .map(
                    (s) => `
            <tr>
              <td>${escapeHtml(s.name)}${s.id === state.shortlistId ? ' <span class="muted">（当前）</span>' : ""}</td>
              <td>${escapeHtml(s.client_or_campaign || "-")}</td>
              <td class="num">${s.selected_count}</td>
              <td class="num">${s.budget_usd !== null ? fmtUsd(s.budget_usd) : "-"}</td>
              <td>
                <button class="link-btn open-shortlist" data-id="${s.id}">打开</button>
                <button class="link-btn switch-shortlist" data-id="${s.id}">切换到此</button>
                <button class="link-btn delete-shortlist" data-id="${s.id}">删除</button>
              </td>
            </tr>`
                  )
                  .join("")
              : '<tr><td colspan="5"><div class="empty-state"><strong>还没有候选名单</strong><div class="muted">完整合作活动请从公司详情填写合作目标、受众、平台与预算后创建；只需从创作者库组名单时，请先输入一个清晰名称。</div></div></td></tr>'
          }
        </tbody>
      </table>`;

    document.getElementById("createShortlistBtn").addEventListener("click", async (event) => {
      const name = document.getElementById("newShortlistName").value.trim();
      if (!name) {
        toast("请先填写候选名单名称。", true);
        document.getElementById("newShortlistName").focus();
        return;
      }
      event.currentTarget.disabled = true;
      try {
        const created = await api("/api/shortlists", { method: "POST", body: JSON.stringify({ name }) });
        state.shortlistId = created.id;
        localStorage.setItem("mango_kol_shortlist_id", created.id);
        await refreshShortlistState();
        if (managerNeedsSelection) {
          closeManager();
          toast("已创建并设为当前名单；请再次点击“加入候选名单”。");
        } else {
          renderManager();
        }
      } catch (err) {
        event.currentTarget.disabled = false;
        toast("创建候选名单失败：" + err.message, true);
      }
    });
    body.querySelectorAll(".switch-shortlist").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          state.shortlistId = Number(btn.dataset.id);
          localStorage.setItem("mango_kol_shortlist_id", btn.dataset.id);
          await refreshShortlistState();
          renderTable();
          if (managerNeedsSelection) {
            closeManager();
            toast("已切换当前名单；请再次点击“加入候选名单”。");
          } else {
            renderManager();
            toast("已切换当前候选名单。");
          }
        } catch (err) {
          btn.disabled = false;
          toast("切换候选名单失败：" + err.message, true);
        }
      });
    });
    body.querySelectorAll(".open-shortlist").forEach((btn) => {
      btn.addEventListener("click", async () => {
        state.shortlistId = Number(btn.dataset.id);
        localStorage.setItem("mango_kol_shortlist_id", btn.dataset.id);
        await refreshShortlistState();
        closeManager();
        await openShortlistModal();
      });
    });
    body.querySelectorAll(".delete-shortlist").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("删除此候选名单？该操作无法撤销。")) return;
        btn.disabled = true;
        try {
          await api(`/api/shortlists/${btn.dataset.id}`, { method: "DELETE" });
          if (Number(btn.dataset.id) === state.shortlistId) {
            state.shortlistId = null;
            localStorage.removeItem("mango_kol_shortlist_id");
            await refreshShortlistState();
            renderTable();
          }
          renderManager();
        } catch (err) {
          btn.disabled = false;
          toast("删除候选名单失败：" + err.message, true);
        }
      });
    });
  } catch (err) {
    renderRetryState(body, "暂时无法加载候选名单列表", renderManager, err.message);
  }
}

// ---------------------------------------------------------------------------
// Section switching
// ---------------------------------------------------------------------------

const SECTION_LOADERS = {
  home: () => loadHome(),
  opportunities: () => loadOpportunities(true),
  network: () => loadNetwork(),
  creators: () => {}, // already loaded at init; tab/filter state is preserved
};

function switchSection(section) {
  state.section = section;
  closeDrawer();
  document.querySelectorAll(".section-btn").forEach((b) => {
    const active = b.dataset.section === section;
    b.classList.toggle("active", active);
    if (active) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  document.querySelectorAll(".app-section").forEach((el) => el.classList.add("hidden"));
  document.getElementById(section + "Section").classList.remove("hidden");
  SECTION_LOADERS[section]();
}

document.getElementById("sectionNav").addEventListener("click", (e) => {
  const btn = e.target.closest(".section-btn");
  if (!btn) return;
  switchSection(btn.dataset.section);
});

// Short form used in filter chips/checkboxes, where the group is already
// titled "优先级"; the standalone badge next to a company name spells out
// "商业优先级" explicitly (see priorityBadgeHtml) so it's never mistaken
// for Relationship Stage or Execution Priority (section V, 2026-08-28) --
// this is pure business value, nothing else.
const PRIORITY_LABEL_ZH = { High: "高", Medium: "中", Low: "低" };
function priorityBadgeHtml(label) {
  return `<span class="priority-badge priority-${escapeAttr(label)}">商业优先级：${escapeHtml(PRIORITY_LABEL_ZH[label] ?? label)}</span>`;
}

const ROUTE_KIND_ZH = {
  connector_comparison: "待核实引荐候选（Warm candidate）+ 冷启动直接渠道（Cold）",
  connector_only: "待核实引荐候选（Warm candidate）",
  direct_only: "冷启动直接渠道（Cold）",
  cold_high_value: "暂无可执行联系渠道，先补路径/联系方式",
  research_only: "暂无可执行渠道，先补证据与负责人",
};

function mangoAskerLabel(handle) {
  if (handle === "Solomon_Nahhh") return "Mango 内部负责人";
  if (handle === "MangoLabs_") return "Mango 主账号负责人";
  return handle ? `@${handle}` : "Mango 内部负责人";
}

function internalPersonDisplay(value, fallback = "Mango 内部负责人") {
  const raw = String(value || "").trim();
  if (!raw) return fallback;
  if (/^@?Solomon(?:_Nahhh)?$/i.test(raw)) return "Mango 内部负责人";
  return raw;
}

function relationshipPathDisplay(value) {
  return String(value || "")
    .replace(/Solomon\s*\(@Solomon_Nahhh\)/gi, "Mango 内部联系人")
    .replace(/@Solomon_Nahhh/gi, "Mango 内部联系人")
    .replace(/\bSolomon\b/gi, "Mango 内部联系人");
}

function publicRouteTypeLabel(value) {
  const raw = String(value || "").trim();
  if (!raw) return "路线类型待补";
  if (raw.startsWith("Direct official program")) return "官方项目直达";
  if (raw.startsWith("Direct official channel")) return "官方渠道直达";
  if (raw.startsWith("Direct")) return "正式直达";
  if (raw.startsWith("Intermediary")) return "中间渠道";
  if (raw.startsWith("Warm")) return "暖关系候选";
  if (raw.startsWith("Cold")) return "冷启动渠道";
  return productCopy(raw);
}

function pathDirectionTruth(record) {
  const path = record?.best_person_path || record?.best_company_path;
  if (!path || path.direction_data_unavailable) return "";
  const directions = path.edge_directions || path.directional_edges || [];
  const arrowEdges = directions.filter((edge) => typeof edge === "string" && edge.includes(" -> "));
  if (arrowEdges.length) {
    const edgeSet = new Set(arrowEdges);
    const emitted = new Set();
    const parts = [];
    arrowEdges.forEach((edge) => {
      if (emitted.has(edge)) return;
      const [from, to] = edge.split(" -> ");
      const reverse = `${to} -> ${from}`;
      if (edgeSet.has(reverse)) {
        parts.push(`${from} ↔ ${to}（互关）`);
        emitted.add(reverse);
      } else {
        parts.push(`${from} → ${to}（单向关注）`);
      }
      emitted.add(edge);
    });
    return parts.join("；");
  }
  const pathLabels = String(path.path_labels || "").split(/\s+->\s+/).map((part) => part.trim()).filter(Boolean);
  if (pathLabels.length > 1 && directions.length) {
    return directions.slice(0, pathLabels.length - 1).map((direction, index) => {
      const connector = direction === "mutual_follow" ? "↔" : "→";
      const label = direction === "mutual_follow" ? "互关" : direction === "follows" ? "单向关注" : "方向待核实";
      return `${pathLabels[index]} ${connector} ${pathLabels[index + 1]}（${label}）`;
    }).join("；");
  }
  return "";
}

function relationshipTruthSummary(record) {
  const code = record?.relationship_stage_code || record?.code || "";
  const label = record?.reachability_label || record?.label || "";
  if (code === "E1") {
    const directionTruth = pathDirectionTruth(record);
    return directionTruth
      ? `E1 · ${directionTruth}；仅为公开 X 关注链，不证明认识或愿意引荐`
      : "E1 · 公开关注线索；方向与是否互关见详情";
  }
  return label || code || "尚未记录关系证据";
}

function whyNowSummary(record) {
  if (record?.why_now) return record.why_now;
  const reason = (record?.priority_reasons || []).find((item) => item && !item.startsWith("关系阶段"));
  return reason || "暂无已记录的近期触发信号；推进前需补证据";
}

function summaryBlockerText(record, operator) {
  if (!operator) return "尚未识别实际负责人";
  if (!operatorHasDualSourceMatch(operator)) return "负责人身份或当前职位仍需核实";
  if (["cold_high_value", "research_only"].includes(record?.best_route_kind)) return "尚无可执行联系渠道";
  if (!record?.next_action) return "尚未写明具体下一步";
  return "尚未记录真实执行结果";
}

function connectorFirstAsk(candidate, askerHandle = "") {
  const handle = candidate?.bridge_handle || candidate?.handle || "该候选人";
  const name = candidate?.bridge_name || candidate?.name || "";
  const target = `@${handle}${name ? `（${name}）` : ""}`;
  return `先由 ${mangoAskerLabel(askerHandle || candidate?.mango_side_handle || candidate?.asker)} 内部确认是否真实认识或近期联系过 ${target}。当前只核实关系，不请求引荐；确认关系有效后，再单独判断是否适合询问目标负责人。`;
}

function routeFallbackText(kind) {
  return {
    connector_comparison: "若引荐关系不成立，改走已记录的直接渠道",
    connector_only: "若内部核实不成立，改走公司官方渠道",
    direct_only: "若直接联系无回应，改走公司官方渠道",
    cold_high_value: "已有负责人则补联系方式；否则先定位负责人，再准备官方渠道触达",
    research_only: "证据不足时暂缓外联，先补齐可核验线索",
  }[kind] || "打开详情补全官方渠道";
}

const EXEC_PRIORITY_CLASS = {
  "需要跟进": "priority-High",
  "现在联系": "priority-High",
  "等待回复": "priority-Medium",
  "等待内部核实": "priority-Medium",
  "本周准备": "priority-Medium",
  "暂缓": "priority-Low",
  "放弃": "priority-Low",
};
function execPriorityBadgeHtml(label) {
  return `<span class="priority-badge ${EXEC_PRIORITY_CLASS[label] || "priority-Low"}">${escapeHtml(label || "暂缓")}</span>`;
}

// ---------------------------------------------------------------------------
// 首页 / 行动地图
// ---------------------------------------------------------------------------

const LAST_VISIT_KEY = "mango_kol_last_visit";

async function loadHome() {
  const body = document.getElementById("homeBody");
  body.innerHTML = '<div class="empty-state">加载中</div>';
  try {
    const [data, priority, creatorGraph, gtmCases] = await Promise.all([
      api("/api/home/summary"),
      api("/api/commercial/priority"),
      api("/api/commercial/creator-sponsor-graph"),
      api("/api/commercial/gtm-cases"),
    ]);
    // Read the previous visit's timestamp before overwriting it -- that's
    // the baseline "新" badges compare against. A first-ever visit has no
    // stored value: don't badge everything as new on someone's first look,
    // just start tracking from here.
    const lastVisit = localStorage.getItem(LAST_VISIT_KEY);
    body.innerHTML = homeHtml(data, lastVisit, { priority, creatorGraph, gtmCases });
    wireHome();
    localStorage.setItem(LAST_VISIT_KEY, new Date().toISOString());
  } catch (err) {
    renderRetryState(body, "暂时无法加载首页", loadHome, err.message);
  }
}

function _newBadge(createdAt, lastVisit) {
  if (!lastVisit || !createdAt) return "";
  return new Date(createdAt) > new Date(lastVisit) ? '<span class="new-tag">新</span>' : "";
}

function canonicalDecisionCard(company, compact = false) {
  const d = company.canonical_decision || {};
  const research = company.commercial_research || {};
  return `<article class="decision-card ${compact ? "decision-card-compact" : ""}" data-company-id="${escapeAttr(company.company_id)}">
    <div class="decision-card-head">
      <div><strong>${escapeHtml(company.name)}</strong><span class="icp-tag">${escapeHtml(ICP_LABEL_ZH[research.icp_type] || research.icp_type || "ICP 待分类")}</span>${company.sales_packet_available ? '<span class="sales-packet-tag">销售包已就绪</span>' : ""}</div>
      <div class="decision-badges"><span class="decision-badge bucket-${escapeAttr(d.decision_bucket || "watch")}">${escapeHtml(d.decision_bucket_label || DECISION_BUCKET_ZH[d.decision_bucket] || "观察")}</span><span class="decision-badge tier-${escapeAttr(d.opportunity_value || "tier_c")}">${escapeHtml(d.opportunity_value_label || OPPORTUNITY_VALUE_ZH[d.opportunity_value] || "待判断")}</span></div>
    </div>
    <div class="decision-card-why"><span>为什么现在</span>${escapeHtml(d.why_now || "时机证据待补")}</div>
    <div class="decision-card-grid">
      <div><span>Mango 可提供</span>${escapeHtml(d.what_to_sell || "切入点待补")}</div>
      <div><span>买方</span>${escapeHtml(d.buyer || "买方待补")}</div>
      <div><span>最佳路线</span>${escapeHtml(d.primary_route || "正式渠道待补")}</div>
      <div><span>执行准备度</span>${escapeHtml(d.execution_readiness_label || EXECUTION_READINESS_ZH[d.execution_readiness] || "待判断")}</div>
    </div>
    <div class="decision-card-action"><span>今天下一步</span>${escapeHtml(d.first_action || "补齐下一步")}</div>
  </article>`;
}

function canonicalHomeHtml(d, commercial = {}) {
  const portfolio = d.decision_portfolio || {};
  const pursue = (portfolio.today_top_three || portfolio.pursue_now || []).map((row) => canonicalDecisionCard(row)).join("") || '<div class="muted">当前没有达到“立即推进”门槛的公司。</div>';
  const pursueQueue = (portfolio.pursue_queue || []).map((row) => canonicalDecisionCard(row, true)).join("") || '<div class="muted">今日队列之后暂无候选。</div>';
  const prepare = (portfolio.prepare_next || []).map((row) => canonicalDecisionCard(row, true)).join("") || '<div class="muted">暂无下一批准备项。</div>';
  const watch = (portfolio.watch_investigate || []).map((row) => canonicalDecisionCard(row, true)).join("") || '<div class="muted">暂无观察项。</div>';
  const asia = (portfolio.asia_opportunities || []).map((row) => {
    const intel = row.asia_intelligence || {};
    return `<article class="asia-opportunity-card" data-company-id="${escapeAttr(row.company_id)}">
      <div><strong>${escapeHtml(row.name)}</strong><span class="asia-status asia-${escapeAttr(intel.status || "not_reviewed")}">${escapeHtml(intel.status_label || ASIA_STATUS_ZH[intel.status] || "尚未审查")}</span></div>
      <p>${escapeHtml(intel.signal_summary || "亚洲市场结论待补")}</p>
      <div class="muted">${escapeHtml((intel.target_markets || []).join(" · ") || "市场待确认")}</div>
    </article>`;
  }).join("") || '<div class="muted">当前没有可验证的亚洲市场强信号。</div>';
  return `
    <header class="decision-home-hero">
      <div><div class="eyebrow">AI 公司 BD 决策工作台</div><h2>今天先推进哪三家公司</h2><p>先看商业价值、时机、可售方案和正式进入路线；关系图谱只在路线需要时提供支持。</p></div>
      <button class="btn btn-primary" type="button" data-home-section="opportunities">查看完整 AI 公司库</button>
    </header>
    <div class="home-totals">
      <div class="home-total-stat"><div class="label">扩展目标库</div><div class="value">${d.totals.commercial_longlist_companies || d.totals.companies}</div></div>
      <div class="home-total-stat"><div class="label">重点公司档案</div><div class="value">${d.totals.commercial_priority_companies || 0}</div></div>
      <div class="home-total-stat"><div class="label">立即推进</div><div class="value">${(portfolio.pursue_now || []).length}</div></div>
      <div class="home-total-stat"><div class="label">亚洲强信号</div><div class="value">${(portfolio.asia_opportunities || []).length}</div></div>
    </div>
    <section class="decision-home-section pursue-section"><div class="home-card-title"><span>今日前三名</span><span class="hint">按执行准备度与商业价值排序；点击进入销售包</span></div><div class="decision-card-stack">${pursue}</div></section>
    <div class="decision-home-columns">
      <section class="decision-home-section"><div class="home-card-title">今日候补 <span class="hint">前三名完成后推进</span></div><div class="decision-card-stack">${pursueQueue}</div></section>
      <section class="decision-home-section"><div class="home-card-title">亚洲市场机会 <span class="hint">仅显示可验证强信号</span></div><div class="asia-opportunity-list">${asia}</div></section>
    </div>
    <details class="home-card research-archive"><summary>下一批准备（${(portfolio.prepare_next || []).length}）</summary><div class="decision-card-stack">${prepare}</div></details>
    <details class="home-card research-archive"><summary>观察 / 补查（${(portfolio.watch_investigate || []).length}）</summary><div class="decision-card-stack">${watch}</div></details>
    <details class="home-card research-archive"><summary>商业研究支持层</summary><div class="provenance-note">创作者与赞助品牌图谱、成熟获客案例仍保留在数据库；它们只为提案和方法判断提供依据，不取代公司级最终判断。</div><div class="home-card-body">${(commercial.gtmCases?.cases || []).slice(0, 5).map((item) => `<div class="commercial-support-row" data-company-id="${escapeAttr(item.company_id)}"><strong>${escapeHtml(item.company)}</strong><div>${escapeHtml(item.what_mango_should_copy || "待提炼")}</div></div>`).join("")}</div></details>`;
}

function homeHtml(d, lastVisit, commercial = {}) {
  if (d.decision_portfolio) return canonicalHomeHtml(d, commercial);
  const priorityRows = (commercial.priority?.companies || d.commercial_priority || [])
    .map((company) => {
      const decision = company.canonical_decision || {};
      const research = company.commercial_research || {};
      const route = research.best_route || {};
      const operator = research.operator || company.best_relevant_operator || company.identified_operator;
      const spendSignal = company.paid_sponsorship_count
        ? `${company.paid_sponsorship_count} 条付费合作观察`
        : spendEvidenceLabel(company.spend_evidence_level);
      return `
        <article class="commercial-priority-row" data-company-id="${escapeAttr(company.company_id)}">
          <div class="commercial-rank">${research.priority_rank || "?"}</div>
          <div class="commercial-main">
            <div class="commercial-title-line">
              <span class="name">${escapeHtml(company.name)}${_newBadge(company.created_at, lastVisit)}</span>
              <span class="icp-tag">${escapeHtml(ICP_LABEL_ZH[research.icp_type] || research.icp_type || "ICP 待分类")}</span>
              <span class="truth-tag truth-${escapeAttr(research.fact_status || "mixed")}">${escapeHtml(research.fact_status === "fact" ? "事实" : research.fact_status === "inference" ? "推断" : "事实与推断")}</span>
              <span class="commercial-score">${Math.round(research.commercial_priority_score || 0)}</span>
            </div>
            <div class="commercial-why"><strong>为什么现在</strong>${escapeHtml(decision.why_now || productCopy(research.why_now || "近期时机待补证据"))}</div>
            <div class="commercial-grid">
              <div><span>真实投放信号</span>${escapeHtml(spendSignal)}</div>
              <div><span>Mango 可提供</span>${escapeHtml(decision.what_to_sell || productCopy(research.mango_offer || "服务切入点待研究"))}</div>
              <div><span>相关负责人</span>${operator ? `${escapeHtml(operator.name)} · ${escapeHtml(operator.role || "职位待核实")}` : "负责人待补"}</div>
              <div><span>最佳进入方式</span>${escapeHtml(decision.primary_route || productCopy(route.label || route.route_detail || "路线待补"))}</div>
            </div>
            <div class="commercial-next"><strong>下一步</strong>${escapeHtml(decision.first_action || productCopy(route.required_first_step || "打开公司档案补齐第一步"))}</div>
          </div>
        </article>`;
    })
    .join("") || '<div class="muted">暂无 priority dossier。</div>';

  const creatorRows = (commercial.creatorGraph?.creators || [])
    .filter((item) => item.sponsor_count > 1 || item.repeated_cooperation || item.rates?.length)
    .slice(0, 12)
    .map((item) => {
      const sponsors = (item.sponsors || []).map((sponsor) => {
        const count = sponsor.observations?.length || 0;
        return `${sponsor.company}${count > 1 ? ` × ${count}` : ""}`;
      });
      return `<div class="commercial-support-row">
        <div><strong>${escapeHtml(item.creator)}</strong><span class="muted"> · ${escapeHtml(item.creator_class || "待分类")}</span></div>
        <div class="commercial-support-copy">${escapeHtml(sponsors.join("、") || "赞助方待补")}</div>
        <div class="commercial-support-meta">${item.repeated_cooperation ? "存在重复合作" : "跨品牌合作"} · ${item.mango_relationship === "rate_on_file" ? "Mango 有报价" : "Mango 关系未知"} · 不推断引荐意愿</div>
      </div>`;
    })
    .join("") || '<div class="muted">暂无可用的创作者与品牌商业关系。</div>';

  const gtmRows = (commercial.gtmCases?.cases || [])
    .map((item) => `<div class="commercial-support-row" data-company-id="${escapeAttr(item.company_id)}">
      <div><strong>${escapeHtml(item.company)}</strong><span class="icp-tag">${escapeHtml(item.category || "GTM case")}</span></div>
      <div class="commercial-support-copy">${escapeHtml(item.gtm_motion || "渠道组合待补")}</div>
      <div class="commercial-support-meta"><strong>Mango 可借鉴</strong> ${escapeHtml(item.what_mango_should_copy || "待提炼")}</div>
    </div>`)
    .join("") || '<div class="muted">暂无成熟 AI GTM 案例。</div>';

  const operationalRows = (d.top_priority_companies || [])
    .slice(0, 8)
    .map((c) => {
      const operator = c.identified_operator || c.confirmed_operator;
      const isInternalCheck = c.planned_work_type === "internal_relationship_check";
      const isTerminalOutcome = c.current_work_kind === "outreach_terminal_outcome";
      const occurredAt = c.current_source_occurred_at || c.current_work_created_at;
      const execution = isTerminalOutcome
        ? `记录人：${internalPersonDisplay(c.owner, "待补")} · 结果时间：${occurredAt ? String(occurredAt).slice(0, 10) : "未记录"} · ${currentWorkStatusLabel(c)}`
        : `${currentWorkKindLabel(c)} · ${internalPersonDisplay(c.owner, "待认领")} · ${currentWorkStatusLabel(c)}`;
      return `<div class="today-action-row" data-company-id="${escapeAttr(c.company_id)}">
        <div class="today-action-head"><div class="name">${escapeHtml(c.name)}</div>${execPriorityBadgeHtml(c.execution_priority)}</div>
        ${isInternalCheck ? '<div class="provenance-note">内部关系核实（非 warm path / 非引荐请求）；公司账号单向关注只作参考。</div>' : ""}
        <div class="today-action-why"><span>为什么现在</span>${escapeHtml(productCopy(whyNowSummary(c)))}</div>
        <div class="today-action-next"><span>具体下一步</span>${escapeHtml(productCopy(c.next_action || "尚未创建具体行动"))}</div>
        <div class="today-action-grid">
          <div><span>目标负责人</span>${escapeHtml(operator?.name || "待补")}</div>
          <div><span>路径类型</span>${escapeHtml(ROUTE_KIND_ZH[c.best_route_kind] || "待补")}</div>
          <div><span>关系事实</span>${escapeHtml(relationshipTruthSummary(c))}</div>
          <div><span>执行</span>${escapeHtml(execution)}</div>
          <div><span>主要阻碍</span>${escapeHtml(summaryBlockerText(c, operator))}</div>
          <div><span>失败备选</span>${escapeHtml(productCopy(c.current_fallback || routeFallbackText(c.best_route_kind)))}</div>
        </div>
      </div>`;
    })
    .join("") || '<div class="muted">暂无已排期行动。</div>';

  return `
    <div class="commercial-hero">
      <div>
        <div class="eyebrow">AI 公司商业优先级</div>
        <h2>现在最值得 Mango 接近的 15 家 AI 公司</h2>
        <p>排序只看商业价值、真实投放、时机、负责人和客户匹配度。人脉图谱只影响进入方式，不影响谁值得进入名单。</p>
      </div>
      <button class="btn btn-primary" type="button" data-home-section="opportunities">打开完整公司库</button>
    </div>
    <div class="home-totals">
      <div class="home-total-stat"><div class="label">清洗后公司库</div><div class="value">${d.totals.commercial_longlist_companies || d.totals.companies}</div></div>
      <div class="home-total-stat"><div class="label">重点公司档案</div><div class="value">${d.totals.commercial_priority_companies || commercial.priority?.count || 0}</div></div>
      <div class="home-total-stat"><div class="label">相关负责人</div><div class="value">${d.totals.identified_operators}</div></div>
      <div class="home-total-stat"><div class="label">付费赞助观察</div><div class="value">${d.totals.paid_sponsorships}</div></div>
    </div>
    <div class="home-card commercial-priority-card">
      <div class="home-card-title">优先研究的 15 家 <span class="hint">点击公司查看完整证据、获客方式、负责人、最佳路线与两个备选</span></div>
      <div class="home-card-body commercial-priority-body">${priorityRows}</div>
    </div>
    <div class="home-grid commercial-support-grid">
      <section class="home-card">
        <div class="home-card-title">高价值创作者与品牌图谱 <span class="hint">重复合作、跨品牌经验与 Mango 报价</span></div>
        <div class="home-card-body">${creatorRows}</div>
      </section>
      <section class="home-card">
        <div class="home-card-title">成熟 AI 公司获客模式 <span class="hint">8 个可销售的方法样本</span></div>
        <div class="home-card-body">${gtmRows}</div>
      </section>
    </div>
    <details class="home-card commercial-support-layer">
      <summary>执行与关系支持层 <span class="hint">已排期行动和人脉只用于执行，不参与商业排序</span></summary>
      <div class="home-card-body today-actions-body">${operationalRows}</div>
    </details>
  `;
}

function wireHome() {
  document.querySelectorAll("#homeBody [data-company-id]").forEach((el) => {
    if (!el.dataset.companyId) return;
    makeActivatable(el, () => {
      switchSection("opportunities");
      openCompanyDrawer(el.dataset.companyId);
    });
  });
  document.querySelectorAll("#homeBody [data-home-section]").forEach((button) => {
    button.addEventListener("click", () => switchSection(button.dataset.homeSection));
  });
}

// ---------------------------------------------------------------------------
// Opportunities
// ---------------------------------------------------------------------------

function oppBuildQuery() {
  const p = new URLSearchParams();
  p.set("sort", state.opp.sort);
  p.set("order", state.opp.order);
  p.set("page", state.opp.page);
  p.set("page_size", state.opp.pageSize);
  if (state.opp.search) p.set("search", state.opp.search);
  const f = state.opp.filters;
  f.decision_bucket.forEach((v) => p.append("decision_bucket", v));
  f.opportunity_value.forEach((v) => p.append("opportunity_value", v));
  f.execution_readiness.forEach((v) => p.append("execution_readiness", v));
  f.signal.forEach((v) => p.append("signal", v));
  f.icp_type.forEach((v) => p.append("icp_type", v));
  f.category.forEach((v) => p.append("category", v));
  f.geography.forEach((v) => p.append("geography", v));
  return p.toString();
}

let oppLoadSeq = 0;
async function loadOpportunities(refreshMeta = false) {
  const seq = ++oppLoadSeq;
  const emptyState = document.getElementById("oppEmptyState");
  const errorState = document.getElementById("oppErrorState");
  emptyState.style.display = "none";
  errorState.style.display = "none";
  document.getElementById("oppRows").innerHTML = '<tr><td colspan="8" class="muted">正在加载公司...</td></tr>';
  try {
    const calls = [api("/api/companies?" + oppBuildQuery())];
    if (refreshMeta || !state.opp.meta.total) calls.push(api("/api/companies/meta/filters"));
    const [data, meta] = await Promise.all(calls);
    if (seq !== oppLoadSeq) return;
    if (meta) state.opp.meta = meta;
    state.opp.rows = data.results;
    state.opp.total = data.total;
    renderOppFilters();
    renderOppTable();
    renderOppPagination();
    renderOppPriorityStrip();
    document.getElementById("oppResultCount").textContent = `共 ${data.total} 家公司`;
  } catch (err) {
    if (seq !== oppLoadSeq) return;
    document.getElementById("oppRows").innerHTML = "";
    renderRetryState(errorState, "暂时无法加载公司商机", () => loadOpportunities(refreshMeta), err.message);
    errorState.style.display = "block";
  }
}

function renderOppFilters() {
  const body = document.getElementById("oppFilterBody");
  body.innerHTML = [
    checkboxGroup("行动分组", "decision_bucket", Object.keys(state.opp.meta.decision_buckets || {}), state.opp.filters.decision_bucket, true, (value) => DECISION_BUCKET_ZH[value] || value),
    checkboxGroup("商机价值", "opportunity_value", Object.keys(state.opp.meta.opportunity_values || {}), state.opp.filters.opportunity_value, true, (value) => OPPORTUNITY_VALUE_ZH[value] || value),
    checkboxGroup("执行准备度", "execution_readiness", Object.keys(state.opp.meta.execution_readiness || {}), state.opp.filters.execution_readiness, true, (value) => EXECUTION_READINESS_ZH[value] || value),
    checkboxGroup("证据与缺口", "signal", (state.opp.meta.signal_filters || []).map((row) => row.value), state.opp.filters.signal, true, (value) => (state.opp.meta.signal_filters || []).find((row) => row.value === value)?.label || value),
    checkboxGroup("Mango ICP", "icp_type", state.opp.meta.icp_types || [], state.opp.filters.icp_type, true, (value) => ICP_LABEL_ZH[value] || value),
    checkboxGroup("类别", "category", state.opp.meta.categories || [], state.opp.filters.category),
    checkboxGroup("地区", "geography", state.opp.meta.geographies || [], state.opp.filters.geography),
  ].join("");
  body.querySelectorAll("[data-filter-key]").forEach((input) => {
    input.addEventListener("change", () => {
      const key = input.dataset.filterKey;
      const value = input.dataset.filterValue;
      const set = state.opp.filters[key];
      if (input.checked) set.add(value);
      else set.delete(value);
      state.opp.page = 1;
      renderOppFilters();
      loadOpportunities();
    });
  });
}

document.getElementById("oppClearFilters").addEventListener("click", () => {
  state.opp.filters = {
    decision_bucket: new Set(),
    opportunity_value: new Set(),
    execution_readiness: new Set(),
    signal: new Set(),
    icp_type: new Set(),
    category: new Set(),
    geography: new Set(),
  };
  state.opp.page = 1;
  renderOppFilters();
  loadOpportunities();
});

document.getElementById("oppSortField").addEventListener("change", (e) => {
  state.opp.sort = e.target.value;
  loadOpportunities();
});
document.getElementById("oppSortOrderBtn").addEventListener("click", (e) => {
  state.opp.order = state.opp.order === "desc" ? "asc" : "desc";
  e.currentTarget.dataset.order = state.opp.order;
  e.currentTarget.textContent = state.opp.order === "desc" ? "从高到低" : "从低到高";
  e.currentTarget.setAttribute("aria-label", `当前${e.currentTarget.textContent}，点击切换排序方向`);
  loadOpportunities();
});

document.querySelectorAll(".mobile-filter-toggle").forEach((button) => {
  button.addEventListener("click", () => {
    const panel = document.getElementById(button.dataset.filterPanel);
    if (!panel) return;
    const expanded = panel.classList.toggle("mobile-filters-open");
    button.setAttribute("aria-expanded", String(expanded));
    button.textContent = expanded ? "收起筛选" : "展开筛选";
  });
});

// Opportunity Priority (this strip) is pure business value, independent of
// Relationship Stage (the separate reach-level column in the table) -- see
// bd_compute.opportunity_priority's 2026-08-28 decoupling. This strip
// surfaces the shape of the portfolio at a glance and doubles as a filter.
function renderOppPriorityStrip() {
  const el = document.getElementById("oppPriorityStrip");
  const counts = state.opp.meta.decision_buckets || {};
  const order = ["pursue_now", "prepare", "watch", "archive"];
  el.innerHTML = order
    .filter((label) => counts[label] !== undefined)
    .map((label) => {
      const active = state.opp.filters.decision_bucket.has(label);
      return `<button class="priority-chip bucket-${escapeAttr(label)} ${active ? "active" : ""}" data-decision-bucket="${escapeAttr(label)}">${escapeHtml(DECISION_BUCKET_ZH[label] || label)} <span class="count">${counts[label]}</span></button>`;
    })
    .join("");
  el.querySelectorAll(".priority-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const label = btn.dataset.decisionBucket;
      const set = state.opp.filters.decision_bucket;
      if (set.has(label)) set.delete(label);
      else set.add(label);
      state.opp.page = 1;
      renderOppFilters();
      loadOpportunities();
    });
  });
}

function renderOppTable() {
  const tbody = document.getElementById("oppRows");
  if (!state.opp.rows.length) {
    tbody.innerHTML = "";
    document.getElementById("oppEmptyState").innerHTML = `<div class="empty-title">没有符合筛选条件的公司</div>试试清除筛选条件。`;
    document.getElementById("oppEmptyState").style.display = "block";
    return;
  }
  document.getElementById("oppEmptyState").style.display = "none";
  tbody.innerHTML = state.opp.rows
    .map((r) => {
      const commercial = r.commercial_research || {};
      const d = r.canonical_decision || {};
      const asia = r.asia_intelligence || {};
      return `
      <tr data-company-id="${escapeAttr(r.company_id)}">
        <td class="name-cell">
          <div class="creator-name">${escapeHtml(r.name)}</div>
          <div class="opp-commercial-tags">${commercial.priority_rank ? `<span class="icp-tag">重点档案 #${commercial.priority_rank}</span>` : ""}<span class="icp-tag">${escapeHtml(ICP_LABEL_ZH[commercial.icp_type] || commercial.icp_type || "ICP 待分类")}</span></div>
          <div class="opp-verified">${escapeHtml(r.category || "类别待补")}${r.geography ? ` · ${escapeHtml(r.geography)}` : ""}</div>
          <details class="opp-row-more"><summary>Mango 可提供</summary><div>${escapeHtml(d.what_to_sell || "切入点待补")}</div><div class="muted">最后核实：${escapeHtml(String(d.last_verified_at || "未记录").slice(0, 10))}</div></details>
        </td>
        <td><span class="decision-badge tier-${escapeAttr(d.opportunity_value || "tier_c")}">${escapeHtml(d.opportunity_value_label || OPPORTUNITY_VALUE_ZH[d.opportunity_value] || "待判断")}</span><div class="muted">${escapeHtml(d.opportunity_reason || "判断理由待补")}</div></td>
        <td><div class="opp-cell-copy">${escapeHtml(d.why_now || "时机待补")}</div><span class="asia-status asia-${escapeAttr(asia.status || "not_reviewed")}">${escapeHtml(asia.status_label || ASIA_STATUS_ZH[asia.status] || "尚未审查")}</span></td>
        <td><div class="opp-cell-copy">${escapeHtml(d.buyer || "买方待补")}</div></td>
        <td><div class="opp-cell-copy"><strong>${escapeHtml(d.primary_route || "正式渠道待补")}</strong></div></td>
        <td><span class="decision-badge readiness-${escapeAttr(d.execution_readiness || "watch")}">${escapeHtml(d.execution_readiness_label || EXECUTION_READINESS_ZH[d.execution_readiness] || "待判断")}</span></td>
        <td class="next-action-cell"><div class="next-action-copy">${escapeHtml(d.first_action || "下一步待补")}</div></td>
      </tr>`;
    })
    .join("");
  tbody.querySelectorAll("tr[data-company-id]").forEach((tr) => {
    makeActivatable(tr, () => openCompanyDrawer(tr.dataset.companyId));
  });
}

function renderOppPagination() {
  const totalPages = Math.max(1, Math.ceil(state.opp.total / state.opp.pageSize));
  const el = document.getElementById("oppPagination");
  if (totalPages <= 1) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = `
    <button class="btn btn-ghost btn-sm" id="oppPrevPage" ${state.opp.page <= 1 ? "disabled" : ""}>上一页</button>
    <span>第 ${state.opp.page} / ${totalPages} 页</span>
    <button class="btn btn-ghost btn-sm" id="oppNextPage" ${state.opp.page >= totalPages ? "disabled" : ""}>下一页</button>`;
  const prev = document.getElementById("oppPrevPage");
  const next = document.getElementById("oppNextPage");
  if (prev) prev.addEventListener("click", () => { state.opp.page--; loadOpportunities(); });
  if (next) next.addEventListener("click", () => { state.opp.page++; loadOpportunities(); });
}

// ---------------------------------------------------------------------------
// Company drawer (reuses the same #drawer chrome as the creator drawer)
// ---------------------------------------------------------------------------

async function openCompanyDrawer(companyId) {
  if (!companyId) return;
  const drawer = document.getElementById("drawer");
  const overlay = document.getElementById("drawerOverlay");
  const body = document.getElementById("drawerBody");
  drawer.classList.add("company-workspace");
  if (drawer.classList.contains("hidden")) drawerReturnFocus = document.activeElement;
  overlay.classList.remove("hidden");
  drawer.classList.remove("hidden");
  drawer.setAttribute("aria-hidden", "false");
  body.innerHTML = '<div class="empty-state">加载中</div>';
  try {
    const c = await api(`/api/companies/${encodeURIComponent(companyId)}`);
    body.innerHTML = companyDrawerHtml(c);
    wireCompanyDrawer(c);
    document.getElementById("drawerClose").focus();
  } catch (err) {
    renderRetryState(body, "暂时无法加载公司详情", () => openCompanyDrawer(companyId), err.message);
  }
}

function directionStepsHtml(path, compact = false) {
  if (!path) return "";
  const steps = path.direction_steps || [];
  if (!steps.length) {
    const reason = path.direction_data_unavailable_reason || "尚未记录逐边关注方向；不能从节点顺序推断。";
    return `<div class="direction-unavailable">方向未知：${escapeHtml(reason)}</div>`;
  }
  const rows = steps.map((step) => {
    const left = escapeHtml(relationshipPathDisplay(step.from_label || "未知账号"));
    const right = escapeHtml(relationshipPathDisplay(step.to_label || "未知账号"));
    let statement;
    if (step.relationship === "mutual_follow") {
      statement = `${left} 与 ${right} 双向互关`;
    } else if (step.relationship === "follows") {
      statement = `${left} 关注 ${right}`;
    } else if (step.relationship === "followed_by") {
      statement = `${right} 关注 ${left}`;
    } else {
      statement = `${left} 与 ${right}：关注方向未知`;
    }
    return `<li class="direction-hop direction-${escapeAttr(step.relationship || "unknown")}"><span class="direction-hop-index">第 ${step.hop || "?"} 跳</span>${statement}</li>`;
  }).join("");
  const availability = path.direction_data_unavailable
    ? `<div class="direction-unavailable">${escapeHtml(path.direction_data_unavailable_reason || "部分或全部逐边方向不可用。")}</div>`
    : "";
  return `<div class="direction-evidence ${compact ? "direction-evidence-compact" : ""}">
    <div class="direction-title">逐边 X 关注事实</div>
    <ol class="direction-steps">${rows}</ol>
    ${availability}
  </div>`;
}

function pathBlockHtml(title, path, strength) {
  if (!path) return `<div class="path-block">${escapeHtml(title)}：暂无路径记录。</div>`;
  const strengthTag = strength ? `<span class="reach-label reach-level-${strength.level}">${escapeHtml(strength.label)}</span>` : "";
  return `<div class="path-block">
    <span class="path-label">${escapeHtml(title)}：</span>
    <div class="path-node-order">路径节点顺序（不代表关注方向）：${path.path_labels ? escapeHtml(relationshipPathDisplay(path.path_labels)) : escapeHtml(relationshipPathDisplay(path.degree_label || "路径未知"))}</div>
    ${directionStepsHtml(path)}
    <div class="muted" style="margin-top:4px">${path.graph_reachable ? "已观察到公开 X 关注链；仅作研究线索" : "未观察到完整 X 关注链"} -- ${escapeHtml(INTRO_STATUS_ZH[path.human_intro_status] || "待核实")} ${strengthTag}</div>
  </div>`;
}

// path_labels is "A (@a) -> B (@b) -> C (@c) -> Target (@target)" -- the
// node right before the target (the second-to-last @handle) is the real
// bottleneck every row funnels through. connector_handle is NOT that: on
// 3+ hop rows it's the *first* hop from Solomon, which is different on
// every row even when they all end up at the same target-side bridge
// (e.g. Meshy's 20 recorded paths are 20 different Solomon-side contacts
// all routing through the one same person, @MaxForAI -- deduping on
// connector_handle missed this entirely and still showed 20 rows).
function _targetBridgeHandle(p) {
  const handles = [...(p.path_labels || "").matchAll(/@([A-Za-z0-9_]+)/g)].map((m) => m[1]);
  if (handles.length >= 3) return handles[handles.length - 2];
  return p.connector_handle ? p.connector_handle.replace(/^@/, "") : null;
}

// One path is a single point of failure -- if the connector behind it goes
// cold, the whole route disappears. This groups every other path on record
// by the actual target-side bridge (not the varying first hop) so several
// Mango-side entry points into the same one person show as one group, not
// as if there were that many independent routes to the company.
function altPathsHtml(c) {
  const shown = new Set([c.best_person_path?.id, c.best_company_path?.id].filter(Boolean));
  const others = (c.all_intro_paths || []).filter((p) => !shown.has(p.id));
  if (!others.length) return "";

  const groups = new Map();
  for (const p of others) {
    const key = _targetBridgeHandle(p) || `path-${p.id}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  }

  const rows = [...groups.entries()]
    .map(([bridgeHandle, paths]) => {
      const p = paths[0];
      const entryNote = paths.length > 1 ? `Mango 内部有 ${paths.length} 个不同入口可以联系到这个人 -- 只是同一个瓶颈，不是 ${paths.length} 条独立路径` : p.connector_handle ? "经由 " + escapeHtml(p.connector_handle) : "";
      return `
      <div class="alt-path-row">
        <span class="alt-path-root">${p.root === "solomon" ? "Mango 内部关系图" : "Mango 官方账号关系图"}</span>
        <span>目标方连接人：@${escapeHtml(bridgeHandle)}${directionStepsHtml(p, true)}</span>
        <span class="muted">${p.graph_reachable ? "有公开 X 关注链；方向以证据为准，真实关系未核实" : "未观察到完整关注链"}${entryNote ? " -- " + entryNote : ""}</span>
      </div>`;
    })
    .join("");
  return `
    <details class="alt-paths-toggle">
      <summary>另有 ${groups.size} 个待验证目标方连接人（共 ${others.length} 条原始记录）</summary>
      ${rows}
    </details>`;
}

function _lastVerifiedText(iso) {
  if (!iso) return '<span class="muted">尚未有人工核实</span>';
  return `已于 ${new Date(iso).toLocaleDateString()} 核实`;
}

function _canDmBadge(o) {
  if (o.can_dm === null || o.can_dm === undefined) return "";
  return o.can_dm
    ? '<span class="dm-tag dm-open">私信开放</span>'
    : '<span class="dm-tag dm-closed">私信关闭</span>';
}

// Full audit trail (section I, 2026-08-28): every check ran as two
// separate directional queries (never one OR'd query), each evidence row
// carries a clickable tweet URL -- never a truncated text blob standing
// in for "proof". A check with zero evidence must read as "not found
// within this query's coverage", never "these two don't know each other".
function _interactionChecksHtml(checks) {
  if (!checks || !checks.length) {
    return `<div class="muted">尚未查询公开互动记录</div>`;
  }
  const rows = checks
    .map((check) => {
      if (!check.evidence.length) {
        return `<div class="muted">${escapeHtml(check.query_string)} -- 本次查询范围内未发现（未发现≠不认识）</div>`;
      }
      const evidenceRows = check.evidence
        .map((e) => {
          const link = e.is_auditable && e.tweet_url ? `<a href="${escapeAttr(e.tweet_url)}" target="_blank" rel="noopener">查看原帖</a>` : '<span class="muted">原帖链接不可恢复，标记为不可审计</span>';
          return `<div class="muted">${e.posted_at ? new Date(e.posted_at).toLocaleDateString() : "日期未知"} · ${escapeHtml(e.interaction_type || "互动")} · ${link}${e.context_text ? ` -- "${escapeHtml(e.context_text.slice(0, 60))}"` : ""}</div>`;
        })
        .join("");
      return `<div class="muted">${escapeHtml(check.query_string)} -- 发现 ${check.evidence.length} 条</div>${evidenceRows}`;
    })
    .join("");
  const coverageNote = checks[0].coverage_note;
  return `${rows}${coverageNote ? `<div class="provenance-note" style="text-transform:none;letter-spacing:0;margin-top:4px">${escapeHtml(coverageNote)}</div>` : ""}`;
}

function _bridgeRowHtml(b) {
  return `
      <div class="bridge-row">
        <div><strong>@${escapeHtml(b.bridge_handle)}</strong>${b.bridge_name ? " -- " + escapeHtml(b.bridge_name) : ""} <span class="muted">${fmtNum(b.bridge_followers_count)} 粉丝</span></div>
        ${b.bridge_bio ? `<div class="muted">${escapeHtml(b.bridge_bio)}</div>` : ""}
        <details class="alt-paths-toggle"><summary>互动记录审计（${(b.interaction_checks || []).length} 次查询）</summary>${_interactionChecksHtml(b.interaction_checks)}</details>
      </div>`;
}

function _reachabilitySummaryBridgesHtml(c) {
  // Pulls every operator's verified bridges up into the top of the
  // reachability section -- this is the single most important fact about
  // whether a company is really reachable, and it used to be buried below
  // the raw one-way-follow path boxes where nobody would see it first.
  const all = (c.operators || []).flatMap((o) => o.bridges || []);
  if (!all.length) return "";
  const byHandle = new Map();
  all.forEach((bridge) => {
    const key = (bridge.bridge_handle || "").trim().toLowerCase();
    if (!key) return;
    if (!byHandle.has(key)) {
      byHandle.set(key, {
        ...bridge,
        interaction_checks: [...(bridge.interaction_checks || [])],
        mango_side_handles: bridge.mango_side_handle ? [bridge.mango_side_handle] : [],
      });
      return;
    }
    const current = byHandle.get(key);
    current.interaction_checks.push(...(bridge.interaction_checks || []));
    if (bridge.mango_side_handle && !current.mango_side_handles.includes(bridge.mango_side_handle)) {
      current.mango_side_handles.push(bridge.mango_side_handle);
    }
  });
  const unique = [...byHandle.values()];
  const checked = unique.filter((bridge) => bridge.interaction_checked_at || (bridge.interaction_checks || []).length).length;
  const found = unique.filter((bridge) =>
    (bridge.interaction_checks || []).some((check) => (check.evidence || []).length > 0)
  ).length;
  const rest = unique.slice(3);
  return `
    <div class="bridge-block">
      <div class="bridge-title">公开互关的中间联系人候选（去重 ${unique.length} 位）</div>
      <div class="provenance-note" style="text-transform:none;letter-spacing:0">
        公开互动已核查 ${checked}/${unique.length} 位 · 找到公开互动 ${found} 位 · 本次未找到 ${checked - found} 位 · 尚未核查 ${unique.length - checked} 位。公开互关或互动不等于真实认识，更不等于愿意引荐。
      </div>
      ${unique.slice(0, 3).map(_bridgeRowHtml).join("")}
      ${rest.length ? `<details class="alt-paths-toggle"><summary>还有 ${rest.length} 位中间联系人候选</summary>${rest.map(_bridgeRowHtml).join("")}</details>` : ""}
    </div>`;
}

function _bridgesHtml(o) {
  if (!o.bridges || !o.bridges.length) return "";
  // The same person can be mutual with more than one Mango-side account
  // (Solomon personally, and/or @MangoLabs_) -- that's real, but should
  // only ever show up once per drawer, not once per source it was found via.
  const seen = new Set();
  const unique = [];
  for (const b of o.bridges) {
    if (seen.has(b.bridge_handle)) continue;
    seen.add(b.bridge_handle);
    unique.push(b);
  }
  const shownCount = 6;
  const visible = unique.slice(0, shownCount);
  const rest = unique.slice(shownCount);
  const restHtml = rest.length
    ? `<details class="alt-paths-toggle"><summary>还有 ${rest.length} 位</summary>${rest.map(_bridgeRowHtml).join("")}</details>`
    : "";

  return `
    <div class="bridge-block">
      <div class="bridge-title">与 Mango 内部账号及目标负责人均互关的候选人（X 双向关注已验证，共 ${unique.length} 位；是否真的认识、是否愿意介绍尚未核实）</div>
      ${visible.map(_bridgeRowHtml).join("")}
      ${restHtml}
    </div>`;
}

// Section III (2026-08-28): never auto-picks a single connector. Renders
// up to 3 candidates side by side for human comparison (each with its own
// evidence and a specific internal-verification question), the direct/
// cold channel as a fully parallel track (never merged with or ranked
// above the warm candidates), and a fallback -- all three shown together
// when they exist, exactly matching the Sapien case that motivated this
// (roxinft: zero interaction found: calchulus: a real if old exchange --
// both must show, neither auto-picked).
function _connectorCandidateHtml(cand) {
  return `
    <div class="bridge-row">
      <div><strong>@${escapeHtml(cand.bridge_handle)}</strong>${cand.bridge_name ? " -- " + escapeHtml(cand.bridge_name) : ""} <span class="reach-label reach-level-${cand.level}">${escapeHtml(cand.code)}</span> <span class="muted">${fmtNum(cand.bridge_followers_count)} 粉丝</span></div>
      <div class="muted">${escapeHtml(productCopy(cand.evidence_note))}</div>
      ${cand.disproven ? `<div class="muted">⚠ ${escapeHtml(cand.disprove_note || "已记录为不成立")}</div>` : ""}
      <div class="muted">内部核实负责人：${escapeHtml(mangoAskerLabel(cand.mango_side_handle))}</div>
      <div class="reason-text">第一问：${escapeHtml(connectorFirstAsk(cand))}</div>
    </div>`;
}

// 2026-08-28, section IV fix: steps are numbered 1/2/3 based on what
// actually exists for this company -- never a fixed A/B/C where "B"
// shows without an "A" it was supposed to follow, and never "同时"
// (also/in parallel) language implying a warm path that isn't there.
// plan.steps is the single source of truth (backend builds plan.text
// from the same list), so this can't drift out of sync with the summary
// text shown elsewhere.
const STEP_KIND_ICON = {
  verify_warm: "🔶",
  direct_contact: "🟢",
  fallback: "⚪",
  find_operator: "🔍",
  prepare_offer: "📋",
  find_new_connector: "🔗",
};
const STEP_KIND_LABEL_ZH = {
  verify_warm: "核实引荐候选人",
  direct_contact: "直接联系",
  fallback: "执行备选方案",
  find_operator: "定位实际负责人",
  prepare_offer: "准备合作方案",
  find_new_connector: "寻找新的引荐候选人",
};

function _actionPlanHtml(plan) {
  if (!plan) return "";
  const stepRows = plan.steps
    .map(
      (step, i) => `
      <div class="bridge-block">
        <div class="bridge-title">${STEP_KIND_ICON[step.kind] || ""} ${i + 1}. ${escapeHtml(STEP_KIND_LABEL_ZH[step.kind] || productCopy(step.label))}</div>
        <div class="bridge-row">${escapeHtml(productCopy(step.detail))}</div>
        ${step.kind === "verify_warm" && plan.warm ? plan.warm.map(_connectorCandidateHtml).join("") : ""}
      </div>`
    )
    .join("");
  return stepRows;
}

const ACTION_STATUS_ZH = WORKFLOW_STATUS_ZH;

const OUTREACH_STAGES = [
  "contact_attempted",
  "connector_replied",
  "relationship_confirmed",
  "relationship_rejected",
  "intro_accepted",
  "intro_made",
  "target_replied",
  "meeting_booked",
  "proposal_requested",
  "rejected",
  "no_response",
];
const OUTREACH_STAGE_ZH = {
  contact_attempted: "已尝试联系",
  connector_replied: "连接人已回复",
  relationship_confirmed: "关系已确认真实",
  relationship_rejected: "候选关系被否认（对方表示不认识目标）",
  intro_accepted: "对方同意引荐",
  intro_made: "引荐已发出",
  target_replied: "目标人已回复（不自动等于 E6）",
  meeting_booked: "已约到会议",
  proposal_requested: "对方索要提案",
  rejected: "拒绝继续 / 不愿引荐（不等于不认识）",
  no_response: "暂无回应（不等于关系被证伪）",
};

const BRIDGE_REQUIRED_OUTREACH_STAGES = new Set([
  "connector_replied",
  "relationship_rejected",
  "intro_accepted",
  "intro_made",
]);

const CONTACT_CHANNELS = ["dm", "email", "form", "connector_ask", "in_person", "other"];
const CONTACT_CHANNEL_ZH = {
  dm: "私信",
  email: "邮箱",
  form: "联系表单",
  connector_ask: "经连接人询问",
  in_person: "线下/活动",
  other: "其他",
};
const REVIEW_STATUS_ZH = { unreviewed: "待审核", confirmed: "证据已确认", rejected: "证据已驳回" };

function actionBlockerText(c, bestOperator) {
  const firstStep = c.suggested_bridge_action?.steps?.[0];
  const continuationBlockers = {
    wait_reply: "首次联系已完成；当前等待对方回复",
    wait_follow_up_date: "当前等待已记录的跟进日期",
    follow_up: "首次联系已完成；当前需要按计划跟进",
    follow_up_no_response: "对方暂无回复；只做一次有新增价值的跟进并设置停止日期",
    qualify_connector: "引荐候选人已回复；需要确认真实关系和引荐意愿",
    request_intro_consent: "关系已确认；仍需取得明确引荐同意",
    enable_intro: "对方已同意引荐；需要提供可转发背景并促成发出",
    follow_up_intro: "引荐已发出；当前等待或跟进目标方回应",
    wait_target_reply: "引荐已发出；当前等待目标方回应",
    qualify_target: "目标方已回复；需要确认需求、预算和会议",
    prepare_meeting: "会议已预约；当前需要准备会议简报",
    send_proposal: "对方已索要提案；当前需要按约定时间发送",
    switch_connector: "当前候选路径未推进；需要切换其他候选人",
    switch_to_direct: "当前引荐路径未推进；需要切换独立直接渠道",
    close_outreach: "当前外联已关闭；只在出现明确新信号时重启",
  };
  if (firstStep && continuationBlockers[firstStep.kind]) return continuationBlockers[firstStep.kind];
  if (!bestOperator) return "尚未识别实际负责人";
  if (!operatorHasDualSourceMatch(bestOperator)) return "负责人身份仍需完成官网/公司来源与 X 资料匹配";
  if (!firstStep) return "尚未生成下一步";
  if (firstStep.kind === "verify_warm") return "需先确认引荐候选人是否认识目标人、是否愿意介绍";
  if (firstStep.kind === "direct_contact") return "尚未发起直接联系";
  if (firstStep.kind === "find_operator") return "还需定位更合适的实际负责人";
  return "尚未记录实际执行结果";
}

function recommendationSummary(c) {
  const decision = c.solomon_review?.decision;
  if (decision === "proceed") return "已决定推进";
  if (decision === "watch") return "已决定观察";
  if (decision === "reject") return "已决定放弃";
  if (c.execution_priority === "放弃") return "当前执行状态：放弃";
  if (c.execution_priority === "暂缓") return "等待更多证据";
  return "待内部核实后决定";
}

function commercialDossierHtml(c) {
  const dossier = c.commercial_dossier;
  if (!dossier) {
    const assessment = c.commercial_research;
    return `<div class="section commercial-dossier commercial-dossier-empty">
      <div class="section-title">商业研究状态</div>
      <div class="provenance-note">${assessment?.downgrade_reason ? escapeHtml(assessment.downgrade_reason) : "当前属于扩展公司库，尚未进入 15 家深度研究集。缺失内容保持未知，不用关系图谱补写商业结论。"}</div>
    </div>`;
  }
  const operator = dossier.operator;
  const bestRoute = (dossier.routes || []).find((route) => route.is_best) || dossier.routes?.[0];
  const evidenceRows = (dossier.evidence || []).map((evidence) => `
    <div class="commercial-evidence-row">
      <div><span class="truth-tag truth-${escapeAttr(factStatusLabel(evidence.fact_status).lane)}">${escapeHtml(factStatusLabel(evidence.fact_status).label)}</span> ${escapeHtml(evidence.claim || "未命名证据")}</div>
      <div class="muted">证据日期 ${escapeHtml(evidence.date || "未知")} · 最后核实 ${escapeHtml(evidence.last_verified_at ? String(evidence.last_verified_at).slice(0, 10) : "未知")} · ${escapeHtml(evidence.evidence_type || "类型未知")}${evidence.url ? ` · <a href="${escapeAttr(evidence.url)}" target="_blank" rel="noopener">公开来源 ↗</a>` : ""}</div>
    </div>`).join("") || '<div class="muted">尚无原子商业证据。</div>';
  const routeRows = (dossier.routes || []).map((route) => `
    <div class="commercial-route-row ${route.is_best ? "is-best" : ""}">
      <div class="commercial-route-head"><strong>${route.is_best ? "最佳路线" : `备选 ${route.fallback_order || ""}`}</strong><span class="route-type-tag">${escapeHtml(route.route_type || "类型未知")}</span><span class="truth-tag truth-${escapeAttr(route.confidence || "unverified")}">${escapeHtml(route.confidence || "置信度未知")}</span></div>
      <div><strong>${escapeHtml(route.label || "未命名路线")}</strong> · ${escapeHtml(route.route_detail || "路线说明待补")}</div>
      <div class="muted">为什么：${escapeHtml(route.why_this_route || "待验证")}</div>
      <div class="commercial-route-step">第一步：${escapeHtml(route.required_first_step || "待补")}</div>
      ${route.evidence_url ? `<a href="${escapeAttr(route.evidence_url)}" target="_blank" rel="noopener">路线证据 ↗</a>` : '<span class="muted">路线证据待补</span>'}
    </div>`).join("");
  const channels = (dossier.marketing_channels || []).map((value) => `<span class="icp-tag">${escapeHtml(value)}</span>`).join("") || '<span class="muted">渠道待补</span>';
  const campaigns = (dossier.historical_campaigns || []).map(structuredCampaignHtml).join("") || '<li class="muted">历史合作活动待补</li>';
  const creatorNames = (dossier.creators_media_communities || []).map(structuredCreatorFootprintHtml).join("") || '<li class="muted">待补</li>';
  const unknowns = (dossier.key_unknowns || []).map((value) => `<li>${escapeHtml(value)}</li>`).join("") || '<li>无额外未知项记录</li>';

  return `<section class="section commercial-dossier">
    <div class="commercial-dossier-head">
      <div><span class="commercial-rank">${dossier.priority_rank}</span><strong>重点公司档案</strong><span class="icp-tag">${escapeHtml(ICP_LABEL_ZH[dossier.icp_type] || dossier.icp_type || "ICP 待分类")}</span></div>
      <div><span class="truth-tag truth-${escapeAttr(dossier.fact_status || "mixed")}">${escapeHtml(dossier.fact_status === "fact" ? "事实" : dossier.fact_status === "inference" ? "推断" : "事实与推断")}</span><span class="commercial-score">${Math.round(dossier.commercial_priority_score || 0)}</span></div>
    </div>
    <div class="commercial-summary-grid">
      <div><span>为什么是这家公司</span>${escapeHtml(dossier.why_company)}</div>
      <div><span>为什么是现在</span>${escapeHtml(dossier.why_now)}</div>
      <div><span>商业化与预算</span>${escapeHtml(dossier.commercialization_evidence)} ${escapeHtml(dossier.budget_spend_signals)}</div>
      <div><span>相关负责人</span>${operator ? `<strong>${escapeHtml(operator.name)}</strong> · ${escapeHtml(operator.role || "职位待核实")}<div class="muted">${escapeHtml(dossier.operator_role_relevance || "角色相关性待补")}</div>` : "负责人待补"}</div>
      <div><span>Mango 可提供的方案</span><strong>${escapeHtml(dossier.mango_offer)}</strong><div class="muted">${escapeHtml(dossier.mango_service_fit)}</div></div>
      <div><span>建议开场</span>${escapeHtml(dossier.opening_angle)}</div>
    </div>
    <div class="commercial-route-primary">
      <span>最佳进入方式</span>
      <strong>${escapeHtml(bestRoute?.label || "路线待补")}</strong>
      <div>${escapeHtml(bestRoute?.required_first_step || "第一步待补")}</div>
    </div>
    <details class="alt-paths-toggle" open>
      <summary>完整获客方式、商业证据与 3 条进入路线</summary>
      <div class="commercial-gtm-grid">
        <div><strong>真实获客方式</strong><p>${escapeHtml(dossier.gtm_summary)}</p><div class="tag-row">${channels}</div></div>
        <div><strong>重复活动</strong><p>${dossier.repeated_activity ? "已观察到重复投放或持续渠道活动" : "尚未证明重复活动"}</p><strong>创作者 / 媒体 / 社区</strong><ul class="structured-list">${creatorNames}</ul></div>
      </div>
      <div class="commercial-subtitle">历史合作活动</div><ul class="reasons-list">${campaigns}</ul>
      <div class="commercial-subtitle">可回查商业证据</div>${evidenceRows}
      <div class="commercial-subtitle">联系路线</div><div class="commercial-routes">${routeRows}</div>
      <div class="commercial-subtitle">关键未知项</div><ul class="reasons-list">${unknowns}</ul>
    </details>
    <div class="provenance-note">结论置信度 ${escapeHtml(dossier.confidence || "未知")} · 最后核实 ${escapeHtml(dossier.last_verified_at ? String(dossier.last_verified_at).slice(0, 10) : "未知")} · 公开资料不足处明确保留为待验证。</div>
  </section>`;
}

function factStatusLabel(status) {
  const value = String(status || "").toLowerCase();
  if (["confirmed", "fact"].includes(value)) return { lane: "confirmed", label: "已确认事实" };
  if (["inference", "strong_inference", "mixed"].includes(value)) return { lane: "inference", label: "推断" };
  if (["rejected", "contradicted"].includes(value)) return { lane: "rejected", label: "已驳回/有冲突" };
  return { lane: "lead", label: "待验证线索" };
}

const EVIDENCE_TYPE_LABEL_ZH = {
  official: "官方资料", official_job: "官方招聘", creator_paid_sponsorship: "创作者付费合作观察",
  creator_affiliate: "创作者联盟观察", creator_mention: "创作者提及", partner: "伙伴资料",
  first_party: "一手资料", press_release: "官方新闻稿", case_study: "案例资料",
};

function isPrimarilyEnglish(value) {
  const text = String(value || "");
  const latin = (text.match(/[A-Za-z]/g) || []).length;
  const han = (text.match(/[\u3400-\u9fff]/g) || []).length;
  return latin > 24 && latin > han * 2;
}

function evidenceClaimForProduct(evidence, companyName) {
  const original = evidence.claim || evidence.summary || "未命名证据";
  if (EVIDENCE_CLAIM_ZH[evidence.evidence_id]) return EVIDENCE_CLAIM_ZH[evidence.evidence_id];
  if (!isPrimarilyEnglish(original)) return original;
  const typeLabel = EVIDENCE_TYPE_LABEL_ZH[evidence.evidence_type] || "公开资料";
  return `${companyName} 的${typeLabel}已作为${factStatusLabel(evidence.fact_status).label}收录；英文事实原文保留在研究归档，可通过公开来源逐项回查。`;
}

function evidenceLaneHtml(title, lane, rows, companyName) {
  const body = rows.map((evidence) => {
    const source = evidence.url || evidence.source_url;
    return `<article class="evidence-atom">
      <div>${escapeHtml(evidenceClaimForProduct(evidence, companyName))}</div>
      <div class="evidence-atom-meta">证据日期 ${escapeHtml(evidence.date || evidence.evidence_date || "未知")} · 最后核实 ${escapeHtml(String(evidence.last_verified_at || "未知").slice(0, 10))}${source ? ` · <a href="${escapeAttr(source)}" target="_blank" rel="noopener">公开来源 ↗</a>` : ""}</div>
    </article>`;
  }).join("") || '<div class="muted">本类暂无证据。</div>';
  return `<section class="evidence-lane lane-${escapeAttr(lane)}"><div class="evidence-lane-title">${escapeHtml(title)} <span>${rows.length}</span></div>${body}</section>`;
}

function structuredCampaignHtml(item) {
  if (typeof item === "string") return `<li><span>${escapeHtml(item)}</span></li>`;
  const name = item.name || item.title || item.format || "未命名活动";
  const meta = [item.date, item.platform, item.format, item.creator].filter(Boolean).join(" · ");
  return `<li><div><strong>${escapeHtml(name)}</strong>${meta ? `<span class="muted"> · ${escapeHtml(meta)}</span>` : ""}${item.source_url ? ` · <a href="${escapeAttr(item.source_url)}" target="_blank" rel="noopener">证据 ↗</a>` : ""}</div></li>`;
}

function structuredCreatorFootprintHtml(item) {
  if (typeof item === "string") return `<li>${escapeHtml(item)}</li>`;
  const rate = item.rate;
  const rateText = rate && (rate.amount || rate.amount_min || rate.amount_max)
    ? ` · ${escapeHtml(rate.currency || "USD")} ${escapeHtml(String(rate.amount || rate.amount_min || rate.amount_max))}${rate.deliverable ? ` / ${escapeHtml(rate.deliverable)}` : ""}`
    : "";
  return `<li><strong>${escapeHtml(item.name || "未命名 creator/media")}</strong> · ${escapeHtml(item.platform || "平台未知")} · ${escapeHtml(DISCLOSURE_LABEL_ZH[item.relationship_type] || item.relationship_type || "关系类型待核实")}${rateText}</li>`;
}

const APIFY_SIGNAL_LABEL_ZH = {
  commercialization: "商业化",
  creator_affiliate: "创作者 / 联盟",
  growth_marketing: "增长营销",
  partnership_ecosystem: "伙伴生态",
  regional_expansion: "区域扩张",
  community_events: "社区与活动",
};

function crossCompanyConnectorCards(bundle, { compact = false } = {}) {
  const creatorRows = bundle?.creator_connectors || [];
  const xRows = bundle?.x_research_connectors || [];
  const creatorHtml = creatorRows.map((row) => `<article class="route-portfolio-card cross-connector-card">
    <div class="route-portfolio-head"><strong>${row.profile_url ? `<a href="${escapeAttr(row.profile_url)}" target="_blank" rel="noopener">${escapeHtml(row.name)}</a>` : escapeHtml(row.name)}</strong><span>${Number(row.target_company_count || 0)} 家目标公司</span></div>
    <h4>创作者侧采购流程情报</h4>
    <p>${escapeHtml((row.target_companies || []).join(" · "))}</p>
    <div class="tag-row">${(row.commercial_types || []).map((value) => `<span class="icp-tag">${escapeHtml(DISCLOSURE_LABEL_ZH[value] || value)}</span>`).join("")}${row.rate_on_file ? '<span class="icp-tag">库内有报价</span>' : ""}</div>
    ${compact ? "" : `<div><strong>商业价值：</strong>${escapeHtml(row.commercial_value || "可询问多家公司采购流程。")}</div><div><strong>第一步：</strong>${escapeHtml(productCopy(row.required_first_step || "先核对证据，再询问流程情报。"))}</div>`}
    <div class="muted">${escapeHtml(row.relationship_truth || "历史合作不等于愿意引荐。")}</div>
  </article>`).join("");
  const xHtml = xRows.map((row) => `<article class="route-portfolio-card cross-connector-card">
    <div class="route-portfolio-head"><strong>${escapeHtml(row.name || row.handle)}</strong><span>${escapeHtml(row.handle || "")}</span></div>
    <h4>X 多目标研究路径 · ${Number(row.target_company_count || 0)} 家</h4>
    <p>${escapeHtml((row.target_companies || []).join(" · "))}</p>
    ${compact ? "" : `<div><strong>路径覆盖：</strong>${Number(row.path_count || 0)} 条；方向完整 ${Number(row.direction_complete_path_count || 0)} 条；含互关信号 ${Number(row.mutual_edge_path_count || 0)} 条</div><div><strong>第一步：</strong>${escapeHtml(productCopy(row.required_first_step || "先核实真实联系人和关系性质。"))}</div>`}
    <div class="muted">${escapeHtml(row.relationship_truth || "X 路径不证明认识或引荐意愿。")}</div>
  </article>`).join("");
  if (!creatorHtml && !xHtml) return '<div class="muted">当前没有覆盖两家以上重点公司的中间联系人候选。</div>';
  return `<div class="connector-intelligence-groups">${creatorHtml ? `<div><h4>跨公司的创作者 / 媒体节点</h4><div class="route-portfolio-grid">${creatorHtml}</div></div>` : ""}${xHtml ? `<div><h4>X 多目标研究节点</h4><div class="route-portfolio-grid">${xHtml}</div></div>` : ""}</div>`;
}

function apifyCompanyResearchHtml(research, connectorBundle) {
  if (!research) return "";
  const counts = research.decision_ready_counts || {};
  const firstParty = (research.website_signals || []).map((row) => `<article class="research-source-row">
    <div><span class="truth-tag truth-lead">官网观察 · 待复核</span><strong>${escapeHtml(row.source_title || "首方页面")}</strong></div>
    <div class="tag-row">${(row.signal_types || []).map((type) => `<span class="icp-tag">${escapeHtml(APIFY_SIGNAL_LABEL_ZH[type] || type)}</span>`).join("")}</div>
    <a href="${escapeAttr(row.source_url)}" target="_blank" rel="noopener">打开官网证据 ↗</a>
  </article>`).join("") || '<div class="muted">本轮没有可进入证据复核的官网页面。</div>';
  const whyNow = (research.why_now_observed || []).map((row) => `<li>${escapeHtml(row)}</li>`).join("");
  const operatorRows = (research.operator_candidates || []).slice(0, 10).map((row) => `<article class="research-operator-row">
    <div><strong>${row.public_profile_url ? `<a href="${escapeAttr(row.public_profile_url)}" target="_blank" rel="noopener">${escapeHtml(row.name || "未署名")}</a>` : escapeHtml(row.name || "未署名")}</strong><span>${escapeHtml(row.current_role || "职位待核实")}</span></div>
    <div class="muted">${row.role_relevance === "direct_buyer_function" ? "直接相关买方职能" : "相邻职能"} · 当前身份仍待官网或其他首方来源交叉确认</div>
  </article>`).join("") || '<div class="muted">本轮没有符合当前职位门槛的 operator 候选。</div>';
  const publicRoutes = (research.public_contact_routes || []).map((row) => `<article class="way-in-card ${String(row.route_type || "").startsWith("Direct") ? "primary" : "fallback"}">
    <div class="way-in-head"><strong>${escapeHtml(row.label || "公开路线")}</strong><span>${escapeHtml(publicRouteTypeLabel(row.route_type))}</span></div>
    <p>${escapeHtml(row.why_this_route || "路线理由待复核")}</p>
    <div><strong>第一步：</strong>${escapeHtml(row.required_first_step || "待补")}</div>
    <div class="muted">置信度：${escapeHtml(confidenceLabelZh(row.confidence))} · <a href="${escapeAttr(row.route_url)}" target="_blank" rel="noopener">打开路线 ↗</a>${row.evidence_url && row.evidence_url !== row.route_url ? ` · <a href="${escapeAttr(row.evidence_url)}" target="_blank" rel="noopener">官方说明 ↗</a>` : ""}</div>
  </article>`).join("") || '<div class="muted">本轮没有找到可执行的官方公开路线。</div>';
  const researchCohorts = research.campaign?.cohorts || [];
  const cohorts = researchCohorts.map((cohort) => {
    const creatorRows = (cohort.creator_candidates || []).map((row) => {
      const region = row.region_evidence === "unknown"
        ? "地域与受众地域待核实"
        : (row.region_evidence || "地域与受众地域待核实");
      const commercial = DISCLOSURE_LABEL_ZH[row.commercial_type_candidate]
        || (String(row.commercial_type_candidate || "").includes("no_gamma") ? "未发现 Gamma 商业披露" : "商业关系待复核");
      return `<article class="research-creator-row">
        <div class="research-creator-head"><strong>${row.youtube_channel_url ? `<a href="${escapeAttr(row.youtube_channel_url)}" target="_blank" rel="noopener">${escapeHtml(row.creator || "未署名 creator")}</a>` : escapeHtml(row.creator || "未署名 creator")}</strong><span>${row.rate_on_file ? "库内有报价" : "需询价"}</span></div>
        <p>${escapeHtml(row.why_fit || "公司特定适配理由待补")}</p>
        <div class="muted">${escapeHtml(commercial)} · ${escapeHtml(region)}${row.youtube_subscribers ? ` · ${fmtNum(row.youtube_subscribers)} 订阅` : ""}${row.observed_video_views ? ` · 样本 ${fmtNum(row.observed_video_views)} 播放` : ""}${row.evidence_url ? ` · <a href="${escapeAttr(row.evidence_url)}" target="_blank" rel="noopener">内容证据 ↗</a>` : ""}</div>
      </article>`;
    }).join("") || '<div class="muted">此层暂无合格候选。</div>';
    const pricingCopy = String(cohort.pricing_status || "").startsWith("partial_only")
      ? "报价不完整：不得生成虚假的组合总价。"
      : "报价、受众地域与商业合作意愿均待核实。";
    return `<details class="research-cohort" open><summary><strong>${escapeHtml(cohort.cohort || "未命名 cohort")}</strong><span>${(cohort.creator_candidates || []).length} 位候选</span></summary><p>${escapeHtml(cohort.objective || "目标待补")}</p><div class="research-creator-grid">${creatorRows}</div><div class="evidence-boundary">${escapeHtml(pricingCopy)}</div></details>`;
  }).join("");
  const campaignArchive = cohorts ? `<details class="research-archive"><summary>仅在进入提案阶段后使用的活动概念（${researchCohorts.length} 组候选）</summary><div class="evidence-boundary"><strong>当前状态：</strong>已有活动概念，但报价未完整。此处不构成报价，也不应代替首次 BD 接触。</div>${cohorts}</details>` : "";
  return `<section class="workspace-section apify-research-workspace">
    <div class="workspace-section-head"><div><div class="eyebrow">横向 BD 增量研究</div><h3>需求信号 → 负责人 → 进入路线 → 联系节点 → 下一步</h3></div><p>优先回答为什么现在联系、找谁和怎么进去；采集数量、采集费用与活动设计留在二级层。</p></div>
    <div class="apify-research-banner"><div><span>有效首方页</span><strong>${Number(counts.valid_first_party_pages || 0)}</strong></div><div><span>相关负责人</span><strong>${Number(counts.operator_candidates || 0)}</strong></div><div><span>官方路线</span><strong>${Number(counts.public_program_routes_found || 0)}</strong></div><div><span>活动状态</span><strong>${researchCohorts.length ? "已有概念" : "未进入"}</strong></div></div>
    <div class="research-decision-grid"><div><h4>为什么现在</h4><ul class="structured-list">${whyNow}</ul></div><div><h4>Mango 可测试的切入点</h4><p>${escapeHtml(research.mango_offer_hypothesis || "待提炼")}</p><h4>优先核验的买方路径</h4><p>${escapeHtml(research.best_route_hypothesis || "待提炼")}</p></div></div>
    <details class="research-archive"><summary>官网首方来源（${(research.website_signals || []).length}）</summary><div class="research-source-grid">${firstParty}</div></details>
    <details class="research-archive"><summary>LinkedIn 当前职位候选（${(research.operator_candidates || []).length}）</summary><div class="research-operator-grid">${operatorRows}</div></details>
    <div class="commercial-subtitle">公开进入路线</div><div class="ways-in-grid">${publicRoutes}</div>
    <div class="commercial-subtitle">可复用的跨公司联系节点</div>${crossCompanyConnectorCards(connectorBundle, { compact: true })}
    ${campaignArchive}
    <div class="evidence-boundary"><strong>公开联系方式边界：</strong>${escapeHtml(research.contact_gap || "未运行公开联系方式研究。")}</div>
    <div class="provenance-note">采集于 ${escapeHtml(String(research.generated_at || "未知").slice(0, 10))} · 状态：观察到但未人工复核 · 内容语言不等于 creator 所在地或受众地域。</div>
  </section>`;
}

function canonicalCompanyWorkspaceHtml(c) {
  const d = c.canonical_decision || {};
  const asia = c.asia_intelligence || {};
  const dossier = c.commercial_dossier || {};
  const commercialEvidence = dossier.evidence || [];
  const evidence = commercialEvidence.concat((asia.evidence || []).map((row) => ({ ...row, claim: row.summary, url: row.source_url, date: row.evidence_date })));
  const grouped = { confirmed: [], inference: [], lead: [], rejected: [] };
  evidence.forEach((row) => grouped[factStatusLabel(row.fact_status).lane].push(row));
  const routes = dossier.routes || [];
  const sortedRoutes = [...routes].sort((a, b) => Number(b.is_best) - Number(a.is_best) || (a.fallback_order || 99) - (b.fallback_order || 99));
  const routeTypeZh = { Direct: "正式直达", Intermediary: "中间人路径", "Warm candidate": "暖关系候选", Cold: "冷启动渠道" };
  const routeRows = sortedRoutes.map((route, index) => {
    const fallbackOrder = route.fallback_order || index;
    const routeSummary = route.is_best ? d.primary_route : (fallbackOrder === 1 ? d.fallback_1 : d.fallback_2);
    const routeStep = route.is_best ? d.first_action : routeSummary;
    return `<article class="way-in-card ${route.is_best ? "primary" : "fallback"}">
    <div class="way-in-head"><strong>${route.is_best ? "主路线" : `备选路线 ${fallbackOrder}`}</strong><span>${escapeHtml(routeTypeZh[route.route_type] || "路线类型待补")}</span></div>
    <h4>${escapeHtml(routeSummary || route.label || "未命名路线")}</h4>
    <p>${route.is_best ? "该路线优先使用已记录的正式身份或官方渠道；预算权限与响应意愿仍需通过首次沟通确认。" : "该路线作为主路线失败后的可验证替代方案，不将历史合作或公开关注自动表述为引荐意愿。"}</p>
    <div><strong>第一步：</strong>${escapeHtml(routeStep || "待补")}</div>
    <div class="muted">置信度：${escapeHtml(confidenceLabelZh(route.confidence))}${route.evidence_url ? ` · <a href="${escapeAttr(route.evidence_url)}" target="_blank" rel="noopener">路线证据 ↗</a>` : ""}</div>
  </article>`;
  }).join("") || '<div class="muted">尚未建立可验证的正式进入路线。</div>';
  const secondaryPath = c.best_person_path && (c.best_person_path.hop_count || 0) >= 2
    ? `<article class="way-in-card research-only"><div class="way-in-head"><strong>弱二级研究路径</strong><span>仅研究线索</span></div><h4>${escapeHtml(relationshipPathDisplay(c.best_person_path.path_labels || "二级 X 路径"))}</h4>${directionStepsHtml(c.best_person_path, true)}<p>单向关注或关注链不证明认识、引荐意愿或私信权限。</p></article>`
    : "";
  const atomicEvidenceUrls = new Set(
    evidence.map((row) => row.url || row.source_url).concat((c.sponsorships || []).map((row) => row.content_url)).filter(Boolean)
  );
  const campaigns = (dossier.historical_campaigns || []).map((item) => {
    if (typeof item === "object" && item?.source_url && atomicEvidenceUrls.has(item.source_url)) {
      return `<li><strong>${escapeHtml(item.name || item.title || "历史活动")}</strong><span class="muted"> · 原子证据见上方 Commercial evidence 或 Sponsorship evidence，不重复整段。</span></li>`;
    }
    return structuredCampaignHtml(item);
  }).join("") || '<li class="muted">历史合作活动待补。</li>';
  const creators = (dossier.creators_media_communities || []).map(structuredCreatorFootprintHtml).join("") || '<li class="muted">Creator / media / community 记录待补。</li>';
  const sponsorRows = (c.sponsorships || []).map((row) => `<li><strong>${escapeHtml(row.creator_name || row.creator?.display_name || "未知 creator")}</strong> · ${escapeHtml(canonicalSponsorshipDisclosureLabel(row))} · ${escapeHtml(sponsorshipDateLabel(row))}${row.content_url ? ` · <a href="${escapeAttr(row.content_url)}" target="_blank" rel="noopener">证据 ↗</a>` : ""}</li>`).join("") || '<li class="muted">暂无 sponsorship 观察。</li>';
  const channels = (dossier.marketing_channels || []).map((value) => `<span class="icp-tag">${escapeHtml(CHANNEL_LABEL_ZH[value] || value)}</span>`).join("") || '<span class="muted">渠道待补</span>';
  const unknowns = (d.key_unknowns || []).map((value) => `<li>${escapeHtml(value)}</li>`).join("") || '<li>暂无额外未知项记录</li>';
  const apifyResearch = apifyCompanyResearchHtml(c.apify_intelligence, c.cross_company_connectors);
  const actionHistory = (c.action_items || []).map((item) => `<li><strong>${escapeHtml(WORKFLOW_STATUS_ZH[item.status || "open"] || item.status || "待处理")}</strong> · ${escapeHtml(productCopy(item.primary_next_action || "未记录行动"))}${item.due_date ? ` · ${escapeHtml(item.due_date)}` : ""}</li>`).join("") || '<li class="muted">暂无历史行动。</li>';
  const originalEnglishEvidence = evidence.filter((row) => isPrimarilyEnglish(row.claim || row.summary)).map((row) => `<li><strong>${escapeHtml(row.evidence_id || "未命名证据")}</strong> · ${escapeHtml(row.claim || row.summary)}${row.url || row.source_url ? ` · <a href="${escapeAttr(row.url || row.source_url)}" target="_blank" rel="noopener">原始来源 ↗</a>` : ""}</li>`).join("");
  const asiaEvidence = (asia.evidence || []).map((row) => `<article class="asia-evidence-card"><div><strong>${escapeHtml(row.summary)}</strong></div><div class="muted">${escapeHtml((row.target_markets || []).join(" · "))} · ${escapeHtml(row.evidence_date || "日期未知")} · <a href="${escapeAttr(row.source_url)}" target="_blank" rel="noopener">官方来源 ↗</a></div></article>`).join("");
  return `<div id="canonicalCompanyWorkspace" class="canonical-company-workspace">
    <header class="company-workspace-head">
      <div><div class="eyebrow">公司决策工作台</div><h2>${escapeHtml(c.name)}</h2><div class="detail-handle">${escapeHtml(c.category || "类别待补")}${c.geography ? ` · ${escapeHtml(c.geography)}` : ""}${d.decision_source === "priority_15_review" ? " · 重点公司" : " · 扩展公司库"}</div></div>
      <div class="decision-strip">
        <span class="decision-badge bucket-${escapeAttr(d.decision_bucket || "watch")}">${escapeHtml(d.decision_bucket_label || "观察")}</span>
        <span class="decision-badge tier-${escapeAttr(d.opportunity_value || "tier_c")}">${escapeHtml(d.opportunity_value_label || "待判断")}</span>
        <span class="decision-badge readiness-${escapeAttr(d.execution_readiness || "watch")}">${escapeHtml(d.execution_readiness_label || "待判断")}</span>
        <span class="asia-status asia-${escapeAttr(asia.status || "not_reviewed")}">${escapeHtml(asia.status_label || "尚未审查")}</span>
        <span class="last-verified">最后核实 ${escapeHtml(String(d.last_verified_at || "未知").slice(0, 10))}</span>
      </div>
    </header>
    <section class="one-screen-brief">
      <div class="brief-cell brief-wide"><span>为什么是现在</span><strong>${escapeHtml(d.why_now || "时机待补")}</strong></div>
      <div class="brief-cell"><span>Mango 可提供</span>${escapeHtml(d.what_to_sell || "方案待补")}</div>
      <div class="brief-cell"><span>找谁</span>${escapeHtml(d.buyer || "买方待补")}</div>
      <div class="brief-cell"><span>最佳路线</span>${escapeHtml(d.primary_route || "正式渠道待补")}</div>
      <div class="brief-cell action"><span>第一动作</span>${escapeHtml(d.first_action || "下一步待补")}</div>
      <div class="brief-cell"><span>备选 1</span>${escapeHtml(d.fallback_1 || "待补")}</div>
      <div class="brief-cell"><span>备选 2</span>${escapeHtml(d.fallback_2 || "待补")}</div>
    </section>
    ${apifyResearch}
    ${c.sales_packet_available ? '<section class="workspace-section sales-packet-workspace"><div class="workspace-section-head"><div><div class="eyebrow">可执行销售包</div><h3>买方图谱、预算状态与发送文案</h3></div><p>创作者费用只使用现有真实报价；Mango 服务费需输入后才会进入客户总价。</p></div><button class="btn btn-primary" id="loadSalesPacketBtn">打开销售包</button><div id="salesPacketBody"></div></section>' : ""}
    <section class="workspace-section"><div class="workspace-section-head"><div><div class="eyebrow">证据分层</div><h3>商业证据</h3></div><p>事实、推断、待验证线索与冲突分开；同一原子证据只出现一次。</p></div><div class="evidence-lanes">${evidenceLaneHtml("已确认事实", "confirmed", grouped.confirmed, c.name)}${evidenceLaneHtml("强推断", "inference", grouped.inference, c.name)}${evidenceLaneHtml("待验证线索", "lead", grouped.lead, c.name)}${evidenceLaneHtml("已驳回 / 冲突", "rejected", grouped.rejected, c.name)}</div></section>
    <section class="workspace-section"><div class="workspace-section-head"><div><div class="eyebrow">获客方式与历史投放</div><h3>获客与历史合作</h3></div><p>${dossier.repeated_activity ? "已观察到重复或持续活动。" : "尚未证明重复投放；不要把一次自然提及当成持续预算。"}</p></div><div class="gtm-workspace-grid"><div><h4>主要渠道</h4><div class="tag-row">${channels}</div><h4>历史合作活动</h4><ul class="structured-list">${campaigns}</ul></div><div><h4>创作者 / 媒体 / 社区</h4><ul class="structured-list">${creators}</ul><h4>赞助证据</h4><ul class="structured-list">${sponsorRows}</ul></div></div></section>
    <section class="workspace-section"><div class="workspace-section-head"><div><div class="eyebrow">进入路径组合</div><h3>进入方式组合</h3></div><p>正式渠道优先；关系和创作者情报是支持层，不决定公司价值。</p></div><div class="ways-in-grid">${routeRows}${secondaryPath}</div></section>
    <section class="workspace-section asia-workspace"><div class="workspace-section-head"><div><div class="eyebrow">亚洲 / 中国市场情报</div><h3>${escapeHtml(asia.status_label || "尚未审查")}</h3></div><p>${escapeHtml((asia.target_markets || []).join(" · ") || "本轮没有确认目标市场")}</p></div><div class="asia-summary-grid"><div><span>市场结论</span>${escapeHtml(asia.signal_summary || "尚未完成结构化审查")}</div><div><span>Mango 亚洲适配</span>${escapeHtml(asia.mango_asia_fit || "待评估")}</div><div><span>推荐切入角度</span>${escapeHtml(asia.recommended_market_entry_angle || "没有证据时不默认销售 Asia")}</div><div><span>市场语境</span>${escapeHtml(ASIA_MARKET_CONTEXT_ZH[asia.market_context] || "待分类")}</div></div>${asiaEvidence}</section>
    ${c.sales_packet_available ? "" : '<section class="workspace-section proposal-workspace"><div class="workspace-section-head"><div><div class="eyebrow">客户方案</div><h3>基于真实报价的投放方案预览</h3></div><p>先预览组合、交付、预算和测量；只有内部授权后才可保存。</p></div><button class="btn btn-primary" id="previewCampaignBtn">生成只读方案预览</button><div id="campaignPreviewBody"></div></section>'}
    <section class="workspace-section key-unknowns"><h3>关键未知项</h3><ul>${unknowns}</ul></section>
    <details class="research-archive"><summary>研究归档 · 历史行动、关系线索与内部资料</summary><div class="archive-grid"><div><h4>历史行动</h4><ul class="structured-list">${actionHistory}</ul></div><div><h4>关系研究说明</h4><p>${escapeHtml(relationshipTruthSummary(c))}</p>${secondaryPath || '<p class="muted">没有可展示的真实二级研究路径。</p>'}</div>${originalEnglishEvidence ? `<div><h4>英文事实原文</h4><p class="muted">仅用于逐字回查；中文决策层不直接展示原始研究段落。</p><ul class="structured-list">${originalEnglishEvidence}</ul></div>` : ""}${c.internal_notes ? `<div class="internal-only"><h4>内部备注</h4><p>${escapeHtml(c.internal_notes)}</p></div>` : ""}</div></details>
  </div>`;
}

function companyDrawerHtml(c) {
  if (c.canonical_decision) return canonicalCompanyWorkspaceHtml(c);
  const operators = c.operators || [];
  const evidenceContract = c.evidence_contract || {};
  const operatorById = new Map(operators.map((o) => [String(o.id), o]));
  const allBridges = [];
  const seenBridgeIds = new Set();
  operators.forEach((operator) => {
    (operator.bridges || []).forEach((bridge) => {
      const key = String(bridge.id);
      if (!seenBridgeIds.has(key)) {
        seenBridgeIds.add(key);
        allBridges.push({ ...bridge, target_operator: operator });
      }
    });
  });
  const bridgeById = new Map(allBridges.map((b) => [String(b.id), b]));
  const introPaths = c.all_intro_paths || [];
  const pathById = new Map(introPaths.map((p) => [String(p.id), p]));
  const actionItems = c.action_items || [];
  const actionById = new Map(actionItems.map((a) => [String(a.id), a]));

  const operatorOptions = operators
    .map((o) => `<option value="${o.id}">${escapeHtml(o.name || "未署名")}${o.x_handle ? ` · @${escapeHtml(o.x_handle)}` : ""}</option>`)
    .join("");
  const bridgeOptions = allBridges
    .map(
      (b) => `<option value="${b.id}">${escapeHtml(b.bridge_name || `@${b.bridge_handle}`)} · 询问 ${escapeHtml(mangoAskerLabel(b.mango_side_handle))}${b.target_operator?.name ? ` · 目标 ${escapeHtml(b.target_operator.name)}` : ""}</option>`
    )
    .join("");
  const pathOptions = introPaths
    .map((p) => `<option value="${p.id}">${escapeHtml(p.root || "Mango")} · ${escapeHtml(p.target_type === "operator_person" ? "到联系人" : "到公司账号")}${p.connector_handle ? ` · 经 @${escapeHtml(p.connector_handle)}` : ""}</option>`)
    .join("");
  const actionOptions = actionItems
    .map((a) => `<option value="${a.id}">${escapeHtml((a.primary_next_action || `行动项 #${a.id}`).slice(0, 72))}</option>`)
    .join("");

  const operatorRows = operators
    .map(
      (o) => `
      <div class="op-list-row" data-operator-id="${o.id}">
        <div>
          <div>${escapeHtml(o.name || "未署名")}</div>
          <div class="role">${escapeHtml(o.role || "职位未知")}</div>
          <div class="op-verify-grid muted">
            <div>公开身份匹配：${escapeHtml(operatorIdentityLabel(o))}</div>
            <div>身份人工复核：${escapeHtml(verificationDateLabel(o.identity_human_verified, o.identity_human_verified_at))}</div>
            <div>当前职位人工复核：${escapeHtml(verificationDateLabel(o.role_human_verified, o.role_human_verified_at))}</div>
            <div>X 账号：${o.x_account_verified ? "已精确匹配 @" + escapeHtml(o.x_handle) : o.x_handle ? "候选账号 @" + escapeHtml(o.x_handle) + "，待核实" : "未匹配"}</div>
            <div>私信状态：${o.dm_status === "verified_open" ? "已验证开放" : o.dm_status === "checked_not_open" ? "已查询，未开放" : "未核实"}</div>
            <div>公开邮箱/表单：${o.public_contact_status === "verified" ? "已核实" : "未核实（暂未建模）"}</div>
            <div>Mango 关系：${o.mango_relationship_verified ? "已由真实执行记录核实" : (o.bridges || []).length ? `发现 ${o.bridges.length} 位互关候选人（真实关系未核实）` : "未记录已人工确认的关系"}</div>
            <div>联系人资料最后核实：${o.last_verified_at ? escapeHtml(new Date(o.last_verified_at).toLocaleDateString()) : "未记录"}</div>
          </div>
          ${_bridgesHtml(o)}
        </div>
        <div>
          ${o.budget_influence_status === "verified" ? '<span class="confirmed-tag">预算影响力：已人工确认</span>' : '<span class="muted">预算影响力：未核实</span>'}
          <button class="link-btn operator-edit-toggle" data-operator-id="${o.id}">编辑</button>
        </div>
      </div>
      <div class="edit-row hidden" id="operator-edit-${o.id}">
        <input class="inline-input" data-field="role" placeholder="职位" value="${escapeAttr(o.role || "")}" />
        <input class="inline-input" data-field="x_handle" placeholder="X 账号" value="${escapeAttr(o.x_handle || "")}" />
        <textarea class="notes-area" data-field="evidence_urls" placeholder="证据 URL，每行一条；人工核实身份/职位/预算时必填">${escapeHtml((o.evidence_urls || []).join("\n"))}</textarea>
        <label class="filter-check"><input type="checkbox" data-field="identity_human_verified" ${o.identity_human_verified ? "checked" : ""} />我已人工核对姓名、公开来源与 X 账号</label>
        <label class="filter-check"><input type="checkbox" data-field="role_human_verified" ${o.role_human_verified ? "checked" : ""} />我已人工核对当前职位</label>
        <label class="filter-check"><input type="checkbox" data-field="budget_authority_confirmed" ${o.budget_authority_confirmed ? "checked" : ""} />有预算决策权</label>
        <div class="provenance-note">公开双源匹配是只读研究结果，普通编辑不能把它改成“已匹配”。人工核实项分别记录时间；勾选前必须附可回查证据。</div>
        <button class="btn btn-ghost btn-sm operator-save" data-operator-id="${o.id}">保存</button>
      </div>`
    )
    .join("") || '<div class="muted">暂无联系人记录。</div>';

  const actionRows = [...(c.action_items || [])]
    .sort((a, b) => Number(b.id === c.action_id) - Number(a.id === c.action_id))
    .map(
      (a) => `
      <div class="action-item-block">
        <div><strong>第 ${a.execution_wave ?? "-"} 波</strong>${a.id === c.action_id && ["action_item", "release_internal_check"].includes(c.current_work_kind) ? ' <span class="confirmed-tag">当前已排期</span>' : ""}${a.owner ? " -- " + escapeHtml(internalPersonDisplay(a.owner)) : ""} <span class="status-tag status-${escapeAttr(a.status || "open")}">${escapeHtml(ACTION_STATUS_ZH[a.status || "open"] || a.status)}</span>${a.due_date ? ` <span class="muted">截止 ${escapeHtml(a.due_date)}</span>` : ""}</div>
        <div class="text">${escapeHtml(productCopy(a.primary_next_action))}</div>
        ${a.fallback ? `<div class="muted">备选方案：${escapeHtml(productCopy(a.fallback))}</div>` : ""}
        ${a.success_condition ? `<div class="muted">成功标准：${escapeHtml(productCopy(a.success_condition))}</div>` : ""}
        ${a.outcome_notes ? `<div class="reason-text">目前进展：${escapeHtml(a.outcome_notes)}</div>` : ""}
        <div class="action-item-controls">
          ${a.status !== "done" ? `<button class="btn btn-ghost btn-sm action-mark-done" data-action-id="${a.id}" data-owner="${escapeAttr(a.owner || "")}" data-due="${escapeAttr(a.due_date || "")}" data-notes="${escapeAttr(a.outcome_notes || "")}">标记为已完成</button>` : ""}
          <button class="link-btn action-edit-toggle" data-action-id="${a.id}">编辑</button>
        </div>
        <div class="edit-row hidden" id="action-edit-${a.id}">
          <label class="field-label">负责人<input class="inline-input" data-field="owner" value="${escapeAttr(a.owner || "")}" required /></label>
          <label class="field-label">状态<select class="inline-select" data-field="status">
            ${["open", "in_progress", "done", "blocked"].map((s) => `<option value="${s}" ${s === (a.status || "open") ? "selected" : ""}>${ACTION_STATUS_ZH[s]}</option>`).join("")}
          </select></label>
          <label class="field-label">截止日期<input class="inline-input" data-field="due_date" type="date" value="${escapeAttr(a.due_date || "")}" required /></label>
          <label class="field-label campaign-field-wide">具体下一步<textarea class="notes-area" data-field="primary_next_action" required>${escapeHtml(a.primary_next_action || "")}</textarea></label>
          <label class="field-label campaign-field-wide">失败备选<textarea class="notes-area" data-field="fallback" required>${escapeHtml(a.fallback || "")}</textarea></label>
          <label class="field-label campaign-field-wide">成功标准<textarea class="notes-area" data-field="success_condition">${escapeHtml(a.success_condition || "")}</textarea></label>
          <label class="field-label campaign-field-wide">目前进展 / 备注<textarea class="notes-area" data-field="outcome_notes">${escapeHtml(a.outcome_notes || "")}</textarea></label>
          <button class="btn btn-ghost btn-sm action-save" data-action-id="${a.id}">保存</button>
        </div>
      </div>`
    )
    .join("") || '<div class="muted">暂无行动项记录。</div>';

  const outreachLogRows =
    (c.outreach_logs || [])
      .map((l) => {
        const linkedOperator = l.operator_id == null ? null : operatorById.get(String(l.operator_id));
        const linkedBridge = l.bridge_id == null ? null : bridgeById.get(String(l.bridge_id));
        const linkedPath = l.intro_path_id == null ? null : pathById.get(String(l.intro_path_id));
        const linkedAction = l.linked_action_item_id == null ? null : actionById.get(String(l.linked_action_item_id));
        const contextParts = [];
        if (linkedOperator) contextParts.push(`目标联系人：${linkedOperator.name || "未署名"}${linkedOperator.x_handle ? ` (@${linkedOperator.x_handle})` : ""}`);
        else if (l.operator_id != null) contextParts.push(`目标联系人记录 #${l.operator_id}`);
        if (linkedBridge) contextParts.push(`引荐候选人：@${linkedBridge.bridge_handle} / 内部询问人：${mangoAskerLabel(linkedBridge.mango_side_handle)}`);
        else if (l.bridge_id != null) contextParts.push(`引荐候选记录 #${l.bridge_id}`);
        if (linkedPath) contextParts.push(`研究路径：${linkedPath.root || "Mango"} → ${linkedPath.target_type === "operator_person" ? "联系人" : "公司账号"}`);
        else if (l.intro_path_id != null) contextParts.push(`研究路径记录 #${l.intro_path_id}`);
        if (linkedAction) contextParts.push(`关联行动：${(linkedAction.primary_next_action || `#${linkedAction.id}`).slice(0, 72)}`);
        else if (l.linked_action_item_id != null) contextParts.push(`关联行动项 #${l.linked_action_item_id}`);
        const contextLine = contextParts.length
          ? contextParts.join("；")
          : "公司级/直接执行记录；未关联引荐候选人，不用于证明暖关系或引荐";
        return `
      <div class="action-item-block"${l.voided ? ' style="opacity:0.5"' : ""}>
        <div><strong>${escapeHtml(OUTREACH_STAGE_ZH[l.stage] || l.stage)}</strong> <span class="muted">${l.occurred_at ? new Date(l.occurred_at).toLocaleDateString() : ""}</span>${l.owner ? " -- " + escapeHtml(internalPersonDisplay(l.owner)) : ""}${l.voided ? ' <span class="status-tag status-blocked">已作废</span>' : ""}</div>
        <div class="muted">归属：${escapeHtml(contextLine)}</div>
        ${l.contacted_who ? `<div class="muted">联系对象：${escapeHtml(l.contacted_who)}${l.contact_channel ? "（" + escapeHtml(CONTACT_CHANNEL_ZH[l.contact_channel] || l.contact_channel) + "）" : ""}</div>` : ""}
        ${l.notes ? `<div class="text">${escapeHtml(l.notes)}</div>` : ""}
        ${l.evidence_url ? `<div class="muted">证据：<a href="${escapeAttr(l.evidence_url)}" target="_blank" rel="noopener">${escapeHtml(l.evidence_url)}</a></div>` : ""}
        ${l.next_follow_up_date ? `<div class="muted">下次跟进：${escapeHtml(l.next_follow_up_date)}</div>` : '<div class="muted">下次跟进：未设置</div>'}
        ${
          l.voided
            ? `<div class="muted">作废原因：${escapeHtml(l.voided_reason || "")}</div>`
            : `<div class="action-item-controls">
                <button class="link-btn outreach-log-edit-toggle" data-log-id="${l.id}">编辑执行备注 / 跟进</button>
                <button class="link-btn outreach-log-void" data-log-id="${l.id}">作废（记录错误时使用，不会删除）</button>
              </div>
              <div class="edit-row outreach-edit-form hidden" id="outreach-edit-${l.id}">
                <label class="field-label">Mango 执行人<input class="inline-input" data-field="owner" value="${escapeAttr(l.owner || "")}" required /></label>
                <label class="field-label">下次跟进日期<input class="inline-input" data-field="next_follow_up_date" type="date" value="${escapeAttr(l.next_follow_up_date || "")}" /></label>
                <label class="field-label campaign-field-wide">执行备注<textarea class="notes-area" data-field="notes" placeholder="记录回复、阻碍或下次跟进需要的上下文">${escapeHtml(l.notes || "")}</textarea></label>
                <div class="provenance-note campaign-field-wide">这里只能更正负责人、备注和跟进日期；阶段、对象、渠道、证据或归属有误时，请作废原记录并重新录入，避免改写历史。</div>
                <button class="btn btn-ghost btn-sm outreach-log-save" type="button" data-log-id="${l.id}">保存执行更新</button>
              </div>`
        }
      </div>`
      })
      .join("") || '<div class="muted">还没有记录任何真实结果。上面的建议目前都只是研究假设，未经验证。</div>';

  const sponsorshipRows = (c.sponsorships || [])
    .map(
      (s) => `
      <div class="sponsorship-row">
        <div class="top">
          <span class="creator-name" ${s.creator ? `data-creator-id="${s.creator.id}"` : ""}>${escapeHtml(s.creator_name || s.creator?.display_name || "未知创作者")}</span>
          <span class="disclosure-tag disclosure-${escapeAttr(s.disclosure_type || "unknown")}">${escapeHtml(sponsorshipDisclosureLabel(s))}</span>
          <span class="review-status-tag review-${escapeAttr(s.review_status || "unreviewed")}">${escapeHtml(REVIEW_STATUS_ZH[s.review_status || "unreviewed"] || "待审核")}</span>
        </div>
        <div class="muted">${s.platform ? escapeHtml(s.platform) + "，" : ""}${escapeHtml(sponsorshipDateLabel(s))} · ${escapeHtml(evidenceConfidenceLabel(s.confidence))} · ${s.verified_at ? `人工复核于 ${escapeHtml(String(s.verified_at).slice(0, 10))}` : "未人工复核"}${s.creator && !s.creator.has_quote ? " -- 暂无 Mango 报价" : ""}${s.content_url ? ` -- <a href="${escapeAttr(s.content_url)}" target="_blank" rel="noopener">查看证据</a>` : ""}</div>
        ${s.content_title ? `<div class="reason-text">${escapeHtml(s.content_title)}</div>` : ""}
        ${s.evidence_text ? `<div class="reason-text">"${escapeHtml(s.evidence_text)}"</div>` : ""}
        <div class="review-row">
          ${
            s.review_status === "unreviewed"
              ? `<button class="btn btn-ghost btn-sm evidence-confirm" data-evidence-id="${s.id}">确认</button><button class="btn btn-ghost btn-sm evidence-reject" data-evidence-id="${s.id}">驳回</button>`
              : `<span class="review-status-tag review-${escapeAttr(s.review_status)}">${escapeHtml(REVIEW_STATUS_ZH[s.review_status] || s.review_status)}</span>${s.reviewed_note ? ` <span class="muted">${escapeHtml(s.reviewed_note)}</span>` : ""}`
          }
        </div>
      </div>`
    )
    .join("") || '<div class="muted">暂无赞助观察记录。</div>';

  const bestOperator = c.identified_operator || c.confirmed_operator || (c.operators || [])[0] || null;
  const fallback = c.current_fallback || routeFallbackText(c.suggested_bridge_action?.kind);
  const isTerminalOutcome = c.current_work_kind === "outreach_terminal_outcome";
  const outcomeDate = c.current_source_occurred_at || c.current_work_created_at;
  const ownerDue = isTerminalOutcome
    ? `${internalPersonDisplay(c.owner, "记录人待补齐")} / 结果记录于 ${outcomeDate ? String(outcomeDate).slice(0, 10) : "日期未记录"}`
    : c.has_scheduled_action
    ? `${internalPersonDisplay(c.owner, "负责人待补齐")} / ${c.due_date || "跟进日期待补齐"}`
    : "未认领 / 未排期";
  const currentWorkStatus = currentWorkStatusLabel(c);
  const execSummary = `
    <div class="section exec-summary">
      <div class="section-title">决策摘要</div>
      <div class="op-verify-grid">
        <div><strong>建议：</strong>${escapeHtml(recommendationSummary(c))}</div>
        <div><strong>为什么现在：</strong>${escapeHtml(productCopy(whyNowSummary(c)))}</div>
        <div><strong>最强预算/投放证据：</strong>${c.paid_sponsorship_confirmed_count
          ? `人工复核的付费赞助证据 × ${c.paid_sponsorship_confirmed_count}`
          : c.paid_sponsorship_unreviewed_count
          ? `公开付费赞助观察 × ${c.paid_sponsorship_unreviewed_count}（待人工复核）`
          : escapeHtml(spendEvidenceLabel(c.spend_evidence_level))}</div>
        <div><strong>当前负责人候选：</strong>${bestOperator ? `${escapeHtml(bestOperator.name)} · ${escapeHtml(bestOperator.role || "职位未知")} · ${escapeHtml(operatorIdentityLabel(bestOperator))} · ${escapeHtml(operatorHumanReviewLabel(bestOperator))} · ${bestOperator.budget_influence_status === "verified" || bestOperator.budget_authority_confirmed ? "预算影响力已人工确认" : "预算影响力未核实"}` : "尚未识别"}</div>
        <div><strong>最佳当前路径：</strong>${c.suggested_bridge_action ? escapeHtml(ROUTE_KIND_ZH[c.suggested_bridge_action.kind] || "尚无已记录渠道") : "尚无已记录渠道"}</div>
        <div><strong>关系事实：</strong>${escapeHtml(relationshipTruthSummary(c))}</div>
        <div><strong>具体下一步：</strong>${c.next_action ? escapeHtml(productCopy(c.next_action)) : "暂无具体建议"}</div>
        <div><strong>当前工作类型 / 状态：</strong>${escapeHtml(currentWorkKindLabel(c))} · ${escapeHtml(currentWorkStatus)}</div>
        <div><strong>${isTerminalOutcome ? "记录人 / 结果时间" : "负责人 / 截止"}：</strong>${escapeHtml(ownerDue)}</div>
        <div><strong>主要阻碍：</strong>${escapeHtml(actionBlockerText(c, bestOperator))}</div>
        <div><strong>失败备选：</strong>${escapeHtml(productCopy(fallback))}</div>
      </div>
    </div>`;

  return `
    <div class="detail-head">
      <div>
        <div class="detail-name">${escapeHtml(c.name)}</div>
        <div class="detail-handle">${c.category ? escapeHtml(c.category) : ""}${c.geography ? " -- " + escapeHtml(c.geography) : ""}</div>
        <div style="margin-top:6px">${priorityBadgeHtml(c.priority)} ${execPriorityBadgeHtml(c.execution_priority)}</div>
      </div>
    </div>

    ${commercialDossierHtml(c)}

    <details class="alt-paths-toggle operational-support-toggle">
      <summary>执行状态与关系支持信息</summary>
      ${execSummary}
    </details>

    <details class="alt-paths-toggle">
      <summary>优先级依据（研究细节）</summary>
      <ul class="reasons-list">${(c.priority_reasons || []).map((r) => `<li>${escapeHtml(productCopy(r))}</li>`).join("")}</ul>
    </details>

    <div class="section"><div class="section-title">投放与时机证据</div><div class="bio-text"><strong>时机：</strong>${escapeHtml(productCopy(whyNowSummary(c)))}</div>${c.budget_evidence ? `<div class="bio-text"><strong>预算：</strong>${linkifyText(c.budget_evidence)}</div>` : '<div class="provenance-note">暂无单独的预算证据原文；请依据上方证据级别谨慎判断。</div>'}</div>
    <div class="section">
      <div class="section-title">目标负责人</div>
      ${operatorRows}
      <button class="link-btn" id="addOperatorToggle" style="margin-top:8px">+ 添加联系人</button>
      <div class="edit-row hidden" id="addOperatorForm">
        <input class="inline-input" id="newOpName" placeholder="姓名" />
        <input class="inline-input" id="newOpRole" placeholder="职位" />
        <input class="inline-input" id="newOpHandle" placeholder="X 账号" />
        <button class="btn btn-ghost btn-sm" id="addOperatorSubmit">添加</button>
      </div>
    </div>

    <div class="section">
      <div class="section-title">关系证据与候选路径 <span class="reach-label reach-level-${c.reachability_level}" style="text-transform:none;letter-spacing:0">${escapeHtml(relationshipTruthSummary(c))}</span></div>
      <div class="provenance-note" style="text-transform:none;letter-spacing:0">E1-E3 只代表公开 X 数据中的研究线索，不代表真实认识或愿意介绍；只有 E4 及以上才包含人工记录的关系事实。私信、邮箱等直接渠道单独展示，不计入此阶段。</div>
      ${_reachabilitySummaryBridgesHtml(c)}
      ${pathBlockHtml("到具体联系人的 X 关注链（研究线索）", c.best_person_path, c.person_relationship_strength)}
      ${pathBlockHtml("到公司官方账号的 X 关注链（研究线索）", c.best_company_path, c.company_relationship_strength)}
      ${altPathsHtml(c)}
    </div>

    <div class="section">
      <div class="section-title">创作者 / 赞助历史与复核（${c.sponsorship_count}）</div>
      ${sponsorshipRows}
    </div>

    <div class="section">
      <div class="section-title">建议合作活动</div>
      <div class="provenance-note">从公司商机创建可保存的合作候选名单；系统会给出可解释的初筛理由，仍需人工确认受众、档期与交付物。</div>
      <button class="btn btn-primary" id="buildCampaignBtn">为 ${escapeHtml(c.name)} 创建合作候选名单</button>
      <div id="buildCampaignForm" style="margin-top:12px"></div>
    </div>

    <div class="section">
      <div class="section-title">当前行动</div>
      ${
        (c.action_items || []).length
          ? `<div class="section-title" style="margin-top:4px">${["action_item", "release_internal_check"].includes(c.current_work_kind) ? "行动项记录（当前已排期项置顶；历史状态原样保留）" : "历史行动项（不等于当前工作）"}</div>${actionRows}`
          : '<div class="muted">尚未创建行动项。</div>'
      }
      ${
        c.suggested_bridge_action
          ? `<div class="section-title" style="margin-top:14px">${c.current_work_kind === "outreach_follow_up" ? "基于最新执行记录的外联跟进方案" : isTerminalOutcome ? "基于最新执行终态的停止 / 改路方案" : c.has_scheduled_action ? "实时研究建议（未自动改写行动项）" : "未排期研究建议（非行动项）"}</div><div class="provenance-note">${c.current_work_kind === "outreach_follow_up" ? "当前工作由最新非作废 OutreachLog 推进；关联的旧行动项状态仅作历史事实展示。" : isTerminalOutcome ? `最新非作废 OutreachLog #${escapeHtml(c.current_source_id ?? "-")} 在旧行动项之后记录了终态结果；旧行动项仍保留作审计历史，但不再作为当前工作。` : "这份建议根据当前证据即时计算；除非上方行动项原文与其一致，否则它没有沿用行动项的负责人、截止日期或状态。需要执行时，请新建或编辑正式行动项。"}</div>${_actionPlanHtml(c.suggested_bridge_action)}`
          : ""
      }
      <button class="link-btn" id="addActionToggle" style="margin-top:8px">+ 添加行动项</button>
      <div class="edit-row hidden" id="addActionForm">
        <label class="field-label">负责人<input class="inline-input" id="newActionOwner" required /></label>
        <label class="field-label">状态<select class="inline-select" id="newActionStatus">
          ${["open", "in_progress", "blocked"].map((s) => `<option value="${s}">${ACTION_STATUS_ZH[s]}</option>`).join("")}
        </select></label>
        <label class="field-label">截止日期<input class="inline-input" id="newActionDueDate" type="date" required /></label>
        <label class="field-label campaign-field-wide">具体下一步<textarea class="notes-area" id="newActionText" required></textarea></label>
        <label class="field-label campaign-field-wide">失败备选<textarea class="notes-area" id="newActionFallback" required></textarea></label>
        <label class="field-label campaign-field-wide">成功标准<textarea class="notes-area" id="newActionSuccessCondition"></textarea></label>
        <button class="btn btn-ghost btn-sm" id="addActionSubmit">添加</button>
      </div>
    </div>

    <div class="section">
      <div class="section-title">执行结果记录 <span class="provenance-note" style="text-transform:none;letter-spacing:0">真实结果，全部人工填写，不做任何推断</span></div>
      ${outreachLogRows}
      <button class="link-btn" id="addOutreachLogToggle" style="margin-top:8px">+ 记录一次真实结果</button>
      <div class="edit-row hidden" id="addOutreachLogForm">
        <select class="inline-select" id="newOutreachStage">
          ${OUTREACH_STAGES.map((s) => `<option value="${s}" ${BRIDGE_REQUIRED_OUTREACH_STAGES.has(s) && !allBridges.length ? "disabled" : ""}>${OUTREACH_STAGE_ZH[s] || s}${BRIDGE_REQUIRED_OUTREACH_STAGES.has(s) ? " · 需指定引荐候选人" : ""}</option>`).join("")}
        </select>
        <select class="inline-select" id="newOutreachOperator">
          <option value="">目标联系人（关系确认/目标回复时必选）</option>
          ${operatorOptions}
        </select>
        <select class="inline-select" id="newOutreachBridge">
          <option value="">引荐候选人（中间联系人 / 引荐阶段必选）</option>
          ${bridgeOptions}
        </select>
        <select class="inline-select" id="newOutreachPath">
          <option value="">关联研究路径（可留空）</option>
          ${pathOptions}
        </select>
        <select class="inline-select" id="newOutreachAction">
          <option value="">关联行动项（可留空）</option>
          ${actionOptions}
        </select>
        <select class="inline-select" id="newOutreachChannel">
          <option value="">联系渠道（必选）</option>
          ${CONTACT_CHANNELS.map((ch) => `<option value="${ch}">${CONTACT_CHANNEL_ZH[ch]}</option>`).join("")}
        </select>
        <input class="inline-input" id="newOutreachOwner" placeholder="Mango 执行人" />
        <input class="inline-input" id="newOutreachContactedWho" placeholder="实际联系对象（姓名/账号）" />
        <input class="inline-input" id="newOutreachFollowUp" placeholder="下次跟进日期（YYYY-MM-DD，可留空）" />
        <input class="inline-input" id="newOutreachEvidenceUrl" placeholder="证据链接（截图/对话记录，可留空）" style="width:100%" />
        <textarea class="notes-area" id="newOutreachNotes" placeholder="备注"></textarea>
        <div class="provenance-note">E4 需关联具体联系人或引荐候选人；E5 必须关联引荐候选人；E6 仅在同一候选人已有“引荐已发出”记录后成立。直接联系后的回复是执行结果，不自动升级为 E6。</div>
        <button class="btn btn-ghost btn-sm" id="addOutreachLogSubmit">保存</button>
      </div>
    </div>

    <div class="section">
      <div class="section-title">公司资料与历史 / 参考证据 <span class="provenance-note" style="text-transform:none;letter-spacing:0">${_lastVerifiedText(c.last_verified_at)}</span></div>
      <div class="op-verify-grid">
        <div><strong>记录鲜度：</strong>${evidenceContract.record_last_verified_at ? escapeHtml(String(evidenceContract.record_last_verified_at).slice(0, 10)) : "未记录"}</div>
        <div><strong>人工复核：</strong>${evidenceContract.human_review_status === "not_recorded" ? "未记录" : escapeHtml(evidenceContract.human_review_status || "未知")}</div>
        <div><strong>重新核实：</strong>${evidenceContract.revalidation_needed ? "需要" : "当前记录无强制标记"}</div>
        <div><strong>记录置信度 / 事实状态：</strong>${escapeHtml(evidenceContract.record_confidence || "未记录")} / ${escapeHtml(evidenceContract.fact_status || "未记录")}</div>
      </div>
      <div class="provenance-note">记录时间不等于预算、关系或负责人已人工核实；缺失值保持未知，不做推断。</div>
      ${(evidenceContract.evidence_records || []).length
        ? `<details class="alt-paths-toggle" style="margin-top:8px"><summary>原子证据记录（${evidenceContract.evidence_records.length}）</summary>${evidenceContract.evidence_records.map((record) => `<div class="reason-text"><strong>${escapeHtml(record.claim || record.evidence_id || "未命名证据")}</strong><div class="muted">日期 ${escapeHtml(record.date || "未记录")} · 最后核实 ${escapeHtml(record.last_verified_at || "未记录")} · ${escapeHtml(record.evidence_type || "类型未记录")} · ${escapeHtml(evidenceConfidenceLabel(record.confidence))} · ${escapeHtml(record.fact_status || "事实状态未记录")}${record.url ? ` · <a href="${escapeAttr(record.url)}" target="_blank" rel="noopener">来源 ↗</a>` : ""}</div></div>`).join("")}</details>`
        : '<div class="provenance-note" style="margin-top:8px">暂无可展开的原子证据记录；当前结论需回到下方来源并重新核实。</div>'}
      <div class="tag-editable">
        <input class="inline-input" id="editCompanyCategory" aria-label="公司类别" placeholder="类别" value="${escapeAttr(c.category || "")}" style="width:220px" />
        <input class="inline-input" id="editCompanyGeography" aria-label="公司地区" placeholder="地区" value="${escapeAttr(c.geography || "")}" style="width:180px" />
        <button class="btn btn-ghost btn-sm" id="saveCompanyFieldsBtn">保存基础资料</button>
        <button class="btn btn-ghost btn-sm" id="verifyCompanyBtn" title="仅记录公司基础资料复核日期；不自动验证预算、关系或联系人">确认已检查基础资料</button>
      </div>
      <div class="provenance-note">“已检查基础资料”不等于预算、关系路径或联系人已核实；这些证据需在各自区域单独判断。</div>
      ${c.buyer_or_route ? `<details class="alt-paths-toggle" style="margin-top:8px"><summary>历史触达思路（推断，不是当前行动）</summary><div class="bio-text">${linkifyText(c.buyer_or_route)}</div></details>` : ""}
      ${
        c.internal_notes || (c.aliases || []).length || (c.sources || []).length
          ? `<details class="alt-paths-toggle" style="margin-top:8px"><summary>研究备注与归档资料</summary>
              ${c.internal_notes ? `<div class="reason-text">${linkifyText(c.internal_notes)}</div>` : ""}
              ${(c.aliases || []).length ? `<div class="reason-text">曾用名/别名：${escapeHtml(c.aliases.join("、"))}</div>` : ""}
              ${(c.sources || []).length ? `<div class="reason-text">数据来源：${c.sources.map((u) => `<a href="${escapeAttr(u)}" target="_blank" rel="noopener">${escapeHtml(u)}</a>`).join("；")}</div>` : ""}
            </details>`
          : ""
      }
    </div>

    ${
      c.gtm_case
        ? `<div class="section"><div class="section-title">GTM 备注</div>
        ${c.gtm_case.spend_classification ? `<div class="reason-text"><strong class="gtm-label">预算类型</strong> ${escapeHtml(SPEND_CLASSIFICATION_ZH[c.gtm_case.spend_classification] || c.gtm_case.spend_classification)}</div>` : ""}
        ${c.gtm_case.gtm_motion ? `<div class="bio-text"><strong class="gtm-label">打法</strong> ${escapeHtml(c.gtm_case.gtm_motion)}</div>` : ""}
        ${c.gtm_case.what_mango_should_copy ? `<div class="reason-text"><strong class="gtm-label">可借鉴</strong> ${escapeHtml(c.gtm_case.what_mango_should_copy)}</div>` : ""}
        ${c.gtm_case.what_not_to_copy ? `<div class="reason-text"><strong class="gtm-label">不要照搬</strong> ${escapeHtml(c.gtm_case.what_not_to_copy)}</div>` : ""}
        </div>`
        : ""
    }

    ${solomonReviewSectionHtml(c)}

  `;
}

function solomonReviewSectionHtml(c) {
  const r = c.solomon_review;
  const pilotTag = r ? '<span class="pilot-tag">复盘试点</span>' : "";
  const reviewedNote = r?.reviewed_at ? ` <span class="provenance-note" style="text-transform:none;letter-spacing:0">最近复盘于 ${new Date(r.reviewed_at).toLocaleDateString()}</span>` : "";
  // The newest history row always mirrors current state (a snapshot is
  // written on every save) -- skip it here so "历史记录" only shows genuine
  // past decisions, not a duplicate of the form above.
  const pastHistory = (c.solomon_review_history || []).slice(1);
  const historyHtml = pastHistory.length
    ? `
      <details class="alt-paths-toggle" style="margin-top:10px">
        <summary>历史记录（${pastHistory.length}）</summary>
        ${pastHistory
          .map(
            (h) => `
          <div class="solomon-history-row">
            <div class="top">
              <span class="decision-tag decision-${escapeAttr(h.decision || "pending")}">${DECISION_LABEL_ZH[h.decision || "pending"]}</span>
              <span class="muted">${h.recorded_at ? new Date(h.recorded_at).toLocaleString() : ""}</span>
            </div>
            ${h.next_action ? `<div>下一步：${escapeHtml(h.next_action)}</div>` : ""}
            ${h.solomon_notes ? `<div class="muted">${escapeHtml(h.solomon_notes)}</div>` : ""}
          </div>`
          )
          .join("")}
      </details>`
    : "";
  return `
    <div class="section solomon-review-section">
      <div class="section-title">复盘决策 ${pilotTag}${reviewedNote}</div>
      <div class="solomon-form">
        <label>决策
          <select class="inline-select" id="srDecision">
            <option value="">-- 尚未决定 --</option>
            <option value="proceed" ${r?.decision === "proceed" ? "selected" : ""}>推进</option>
            <option value="watch" ${r?.decision === "watch" ? "selected" : ""}>观察</option>
            <option value="reject" ${r?.decision === "reject" ? "selected" : ""}>放弃</option>
          </select>
        </label>
        <div class="provenance-note">这里仅记录推进决策。关系是否成立、对方是否愿意引荐，请在“执行结果记录”中写入真实结果；创作者名单需在候选名单中逐人复核。</div>
        <textarea class="notes-area" id="srMissingInfo" placeholder="还缺什么信息阻碍了行动？">${escapeHtml(r?.missing_info || "")}</textarea>
        <textarea class="notes-area" id="srNotes" placeholder="复盘备注">${escapeHtml(r?.solomon_notes || "")}</textarea>
        <input class="inline-input" id="srNextAction" placeholder="下一步行动" value="${escapeAttr(r?.next_action || "")}" style="width:100%" />
        <button class="btn btn-primary btn-sm" id="srSaveBtn">保存复盘</button>
      </div>
      ${historyHtml}
  </div>`;
}

const BUYER_TYPE_ZH = {
  campaign_owner: "活动负责人",
  partnership_owner: "合作负责人",
  execution_owner: "执行负责人",
  economic_buyer: "经济买方",
  regional_owner: "区域负责人",
  procurement_route: "采购与付款流程",
  measurement_owner: "指标负责人",
  executive_escalation: "高层升级路径",
  operator_candidate: "待复核负责人",
  regional_route: "区域合作入口",
  fallback_program: "备选项目入口",
};

const BUYER_VERIFICATION_ZH = {
  official_named: "官网具名确认",
  official_team_channel: "官网团队入口",
  official_open_role: "官网招聘角色，尚未入职",
  secondary_identity_needs_first_party: "二级身份线索，发送前复核",
  unknown: "未知",
  no_signal_found: "未发现信号",
  not_recommended_without_route: "无内部路由时不建议",
};

function salesPacketHtml(packet) {
  const campaign = packet.campaign || {};
  const conceptOnly = campaign.pricing_display_mode === "concept_only_pricing_incomplete";
  const pricing = conceptOnly
    ? `<article class="pricing-tier pricing-incomplete"><div class="pricing-tier-head"><strong>已有活动概念</strong><span>报价未完整</span></div><p class="pricing-purpose">当前只有部分创作者历史报价，无法形成具有战略差异的三档客户方案。</p><div class="pricing-total"><span>已知创作者费用小计</span><strong>${fmtUsd((campaign.pricing_tiers || [])[1]?.creator_media_cost_usd)}</strong></div><div class="pricing-unpriced"><strong>发送前：</strong>补齐创作者逐项报价、Mango 服务费、本地化、制作与项目运营成本。</div></article>`
    : (campaign.pricing_tiers || []).map((tier) => `<article class="pricing-tier pricing-${escapeAttr(tier.key)}">
    <div class="pricing-tier-head"><strong>${escapeHtml(tier.label)}</strong><span>${tier.creator_count} 位候选 · ${tier.priced_creator_count} 位已有匹配报价</span></div>
    ${tier.purpose ? `<p class="pricing-purpose">${escapeHtml(tier.purpose)}</p>` : ""}
    <div class="pricing-line"><span>已知创作者媒体费用</span><strong>${fmtUsd(tier.creator_media_cost_usd)}</strong></div>
    <div class="pricing-line"><span>Mango 服务费</span><strong>${tier.mango_service_fee_usd == null ? "待输入" : fmtUsd(tier.mango_service_fee_usd)}</strong></div>
    <div class="pricing-line"><span>风险预留</span><strong>${tier.contingency_usd == null ? "未配置" : fmtUsd(tier.contingency_usd)}</strong></div>
    <div class="pricing-line"><span>本地化</span><strong>${tier.localization_fee_usd == null ? "未配置" : fmtUsd(tier.localization_fee_usd)}</strong></div>
    <div class="pricing-total"><span>${tier.program_budget_complete ? "完整项目预算" : "已知成本小计"}</span><strong>${fmtUsd(tier.total_client_budget_usd)}</strong></div>
    ${(tier.unpriced_scope || []).length ? `<div class="pricing-unpriced"><strong>未计价：</strong>${escapeHtml(tier.unpriced_scope.join("、"))}</div>` : ""}
  </article>`).join("");
  const buyers = (packet.buyer_map || []).map((row) => `<article class="buyer-map-row buyer-${escapeAttr(row.verification_status)}">
    <div class="buyer-map-head"><div><span class="buyer-type">${escapeHtml(BUYER_TYPE_ZH[row.buyer_type] || row.buyer_type)}</span><strong>${escapeHtml(row.name || "未确认具名人")}</strong></div><span class="truth-tag">${escapeHtml(BUYER_VERIFICATION_ZH[row.verification_status] || row.verification_status)}</span></div>
    <div class="buyer-role">${escapeHtml(row.role)}</div>
    <p>${escapeHtml(row.relevance)}</p>
    <div class="buyer-next"><span>下一步</span>${escapeHtml(row.next_step)}</div>
    <div class="muted">${escapeHtml(CONTACT_ROUTE_TYPE_ZH[row.route_type] || row.route_type)} · ${escapeHtml(row.contact_method || "公开联系方法未知")}${row.source_url ? ` · <a href="${escapeAttr(row.source_url)}" target="_blank" rel="noopener">官方证据 ↗</a>` : ""}</div>
  </article>`).join("");
  const recommendedCreators = ((campaign.creator_mix_by_tier || {}).recommended || campaign.creator_mix || []).map((row) => `<div class="sales-creator-row"><span>${escapeHtml(CAMPAIGN_ROLE_ZH[row.campaign_role] || row.campaign_role)}</span><strong>${row.profile_url ? `<a href="${escapeAttr(row.profile_url)}" target="_blank" rel="noopener">${escapeHtml(row.display_name)}</a>` : escapeHtml(row.display_name)}</strong><span>${escapeHtml(row.recommended_deliverable || "交付待确认")}</span><strong>${row.price_included_in_tier ? fmtUsd(row.quote?.representative) : "需按本方案询价"}</strong></div>`).join("") || '<div class="muted">当前没有能力匹配的创作者；系统没有用通用 AI 账号补位。</div>';
  const questions = (packet.qualification_questions || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const switches = (packet.switch_rules || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  return `<div class="sales-packet-result">
    <div class="sales-readiness"><div><span>发送准备度</span><strong>${escapeHtml(packet.send_readiness?.status === "person_and_route_ready" ? "具名人和官方路线已具备" : packet.send_readiness?.status === "official_team_route_ready" ? "官方团队路线已具备" : "仍需研究")}</strong></div><div><span>经济买方</span><strong>${escapeHtml(BUYER_VERIFICATION_ZH[packet.send_readiness?.economic_buyer_status] || "未知")}</strong></div><div><span>最后核实</span><strong>${escapeHtml(String(packet.last_verified_at || "未知").slice(0, 10))}</strong></div></div>
    <div class="sales-offer"><div class="eyebrow">建议产品</div><h4>${escapeHtml(packet.offer_name)}</h4><p>${escapeHtml(packet.commercial_thesis)}</p><div><strong>目标受众：</strong>${escapeHtml(packet.audience)}</div></div>
    ${conceptOnly ? '<div class="evidence-boundary"><strong>当前阶段：</strong>先确认合作意图、相关负责人和进入路线；进入正式提案阶段并补齐真实成本后，再恢复分档报价。</div>' : '<div class="commercial-inputs"><div><strong>客户价格假设</strong><p>以下输入只在浏览器中重算只读方案，不写数据库。创作者媒体价格来自现有报价。</p></div><label>Mango 服务费 %<input id="salesServiceFeePct" type="number" min="0" max="100" step="0.5" placeholder="发送前必填" /></label><label>风险预留 %<input id="salesContingencyPct" type="number" min="0" max="100" step="0.5" placeholder="可选" /></label><label>本地化 USD<input id="salesLocalizationFee" type="number" min="0" step="100" placeholder="可选" /></label><button class="btn btn-ghost btn-sm" id="repriceSalesPacketBtn">重算三档</button></div>'}
    <div class="pricing-tier-grid">${pricing}</div>
    <div class="pricing-boundary">总额只计入与建议交付物匹配的历史创作者报价及手动输入成本；未计价范围和“需重新询价”人选不在小计中，不可直接作为最终客户报价。</div>
    <div class="sales-packet-columns"><section><h4>${conceptOnly ? "进入提案阶段后复核的创作者候选" : "推荐创作者组合"}</h4><div class="sales-creator-list">${recommendedCreators}</div></section><section><h4>买方与采购路径</h4><div class="buyer-map-list">${buyers}</div></section></div>
    <div class="sales-packet-columns"><section><h4>资格确认问题</h4><ol class="structured-list">${questions}</ol></section><section><h4>无回复切换规则</h4><ol class="structured-list">${switches}</ol></section></div>
    <section class="outreach-copy"><div class="outreach-copy-head"><h4>Email</h4><button class="btn btn-ghost btn-sm" data-copy-sales="email">复制 Email</button></div><div class="outreach-subject"><strong>Subject:</strong> ${escapeHtml(packet.outreach?.email_subject || "")}</div><pre>${escapeHtml(packet.outreach?.email_body || "")}</pre></section>
    <section class="outreach-copy"><div class="outreach-copy-head"><h4>X DM</h4><button class="btn btn-ghost btn-sm" data-copy-sales="x">复制 X DM</button></div><pre>${escapeHtml(packet.outreach?.x_dm || "")}</pre></section>
    <div class="evidence-boundary"><strong>事实边界</strong><p>${escapeHtml(packet.evidence_note)}</p></div>
    <div class="proposal-actions"><button class="btn btn-ghost" id="exportSalesPacketBtn">导出完整 Markdown</button>${conceptOnly ? '<span class="read-only-note">价格不完整：暂不保存为三档方案。</span>' : state.writeAccess ? '<button class="btn btn-primary" id="saveCampaignFromSalesPacketBtn">保存推荐组合为内部候选名单</button>' : '<span class="read-only-note">公开只读：可查看、复制和导出，保存需内部授权。</span>'}</div>
  </div>`;
}

function campaignPreviewHtml(preview) {
  const conceptOnly = preview.pricing_display_mode === "concept_only_pricing_incomplete";
  const rows = (preview.creator_mix || []).map((row) => `<article class="creator-mix-row">
    <div><span class="campaign-role">${escapeHtml(CAMPAIGN_ROLE_ZH[row.campaign_role] || row.campaign_role)}</span><strong>${row.profile_url ? `<a href="${escapeAttr(row.profile_url)}" target="_blank" rel="noopener">${escapeHtml(row.display_name)}</a>` : escapeHtml(row.display_name)}</strong>${row.handle ? ` <span class="muted">@${escapeHtml(row.handle)}</span>` : ""}</div>
    <div class="creator-mix-grid"><div><span>受众</span>${escapeHtml(row.audience || "待核实")}</div><div><span>本方案交付</span>${escapeHtml(row.recommended_deliverable || "待确认")}</div><div><span>现有报价覆盖</span>${row.price_included_in_tier ? `${escapeHtml(row.priced_deliverable || "已匹配交付")}${row.quote?.representative != null ? ` · ${fmtUsd(row.quote.representative)}` : ""}` : `不覆盖 · ${escapeHtml(row.quote_scope?.reason || "需重新询价")}`}</div><div><span>公司特定依据</span>${escapeHtml((row.fit_evidence || []).join("；") || "尚无字段级匹配证据")}</div></div>
    <div class="muted">历史相关 sponsor：${escapeHtml((row.historical_relevant_sponsors || []).join("、") || "未记录")} · 风险：${escapeHtml(row.risk_or_unknown || "待核实")}</div>
  </article>`).join("") || '<div class="muted">现有报价库没有满足条件的 creator；系统没有生成 mock 人选或价格。</div>';
  const roster = (preview.capability_roster || []).map((row) => `<article class="creator-mix-row"><div><span class="campaign-role">${escapeHtml(row.campaign_role)}</span><strong>${row.profile_url ? `<a href="${escapeAttr(row.profile_url)}" target="_blank" rel="noopener">${escapeHtml(row.display_name)}</a>` : escapeHtml(row.display_name)}</strong></div><div class="muted">${escapeHtml((row.fit_evidence || []).join("；") || "公开字段命中")} · ${escapeHtml(row.quote_scope?.reason || "需确认交付与报价")}</div></article>`).join("");
  const missing = (preview.unfilled_roles || []).length ? `<div class="provenance-note">尚未填充的角色：${escapeHtml(preview.unfilled_roles.map((role) => CAMPAIGN_ROLE_ZH[role] || role).join("、"))}。不会用无报价或不合格账号强行补位。</div>` : "";
  const researchBoundary = preview.research_pricing_guardrail
    ? `<div class="evidence-boundary"><strong>公司特定研究边界：</strong>${escapeHtml(preview.research_pricing_guardrail)}</div>`
    : "";
  const tierCards = conceptOnly
    ? `<article class="pricing-tier pricing-incomplete"><div class="pricing-tier-head"><strong>已有活动概念</strong><span>报价未完整</span></div><p class="pricing-purpose">三档已知小计相同，当前不展示伪差异化档位。</p><div class="pricing-total"><span>已知创作者费用小计</span><strong>${fmtUsd((preview.pricing_tiers || [])[1]?.creator_media_cost_usd)}</strong></div><div class="pricing-unpriced"><strong>下一门槛：</strong>补齐逐人交付报价、Mango 服务费、本地化和项目成本。</div></article>`
    : (preview.pricing_tiers || []).map((tier) => {
    const pricedCount = Number(tier.priced_creator_count || 0);
    const unpricedCount = Math.max(0, Number(tier.creator_count || 0) - pricedCount);
    const knownCost = pricedCount ? fmtUsd(tier.creator_media_cost_usd) : "待逐一询价";
    return `<article class="pricing-tier pricing-${escapeAttr(tier.key)}"><div class="pricing-tier-head"><strong>${escapeHtml(tier.label)}</strong><span>${tier.creator_count} 位候选 · ${pricedCount} 位现有报价覆盖 · ${unpricedCount} 位需另询</span></div><p class="pricing-purpose">${escapeHtml(tier.purpose || "")}</p><div class="pricing-total"><span>已知且范围匹配的创作者媒体费用小计</span><strong>${knownCost}</strong></div><div class="pricing-unpriced"><strong>项目级未计价：</strong>${escapeHtml((tier.unpriced_scope || []).join("、") || "无")}</div></article>`;
  }).join("");
  return `<div class="campaign-preview-result">
    <div class="proposal-summary-grid"><div><span>投放方案核心命题</span>${escapeHtml(preview.campaign_thesis)}</div><div><span>亚洲 / 市场切入角度</span>${escapeHtml(preview.asia_market_angle)}</div><div><span>目标受众</span>${escapeHtml(preview.target_audience || "需与买方确认")}</div><div><span>联系路径</span>${escapeHtml(preview.contact_route || "待补")}</div></div>
    <div class="pricing-tier-grid">${tierCards}</div>
    <div class="pricing-boundary">${escapeHtml(preview.budget_allocation?.basis || "")}</div>
    ${researchBoundary}<div class="creator-mix-list">${rows}</div>${missing}
    ${roster ? `<details class="research-archive"><summary>能力匹配、但尚未进入推荐组合的待询价人选（${preview.capability_roster.length}）</summary><div class="creator-mix-list">${roster}</div></details>` : ""}
    <div class="proposal-actions"><button class="btn btn-ghost" id="exportProposalBtn">导出 Markdown 客户方案</button>${conceptOnly ? '<span class="read-only-note">价格不完整：此页只作为概念，不保存为三档方案。</span>' : state.writeAccess ? '<button class="btn btn-primary" id="saveCampaignFromPreviewBtn">保存为内部候选名单</button>' : '<span class="read-only-note">公开只读：可预览与导出，保存需内部授权。</span>'}</div>
  </div>`;
}

function wireCanonicalCompanyWorkspace(c) {
  const salesPacketButton = document.getElementById("loadSalesPacketBtn");
  const salesPacketBody = document.getElementById("salesPacketBody");
  const loadSalesPacket = async (assumptions = {}) => {
    if (!salesPacketButton || !salesPacketBody) return;
    salesPacketButton.disabled = true;
    salesPacketButton.textContent = "正在生成买方图谱与三档报价…";
    const params = new URLSearchParams();
    if (assumptions.serviceFeePct !== "" && assumptions.serviceFeePct != null) params.set("service_fee_pct", assumptions.serviceFeePct);
    if (assumptions.contingencyPct !== "" && assumptions.contingencyPct != null) params.set("contingency_pct", assumptions.contingencyPct);
    if (assumptions.localizationFee !== "" && assumptions.localizationFee != null) params.set("localization_fee_usd", assumptions.localizationFee);
    try {
      const packet = await api(`/api/companies/${encodeURIComponent(c.company_id)}/sales-packet${params.toString() ? `?${params}` : ""}`);
      salesPacketBody.innerHTML = salesPacketHtml(packet);
      const serviceInput = document.getElementById("salesServiceFeePct");
      const contingencyInput = document.getElementById("salesContingencyPct");
      const localizationInput = document.getElementById("salesLocalizationFee");
      if (serviceInput && assumptions.serviceFeePct != null) serviceInput.value = assumptions.serviceFeePct;
      if (contingencyInput && assumptions.contingencyPct != null) contingencyInput.value = assumptions.contingencyPct;
      if (localizationInput && assumptions.localizationFee != null) localizationInput.value = assumptions.localizationFee;
      document.getElementById("repriceSalesPacketBtn")?.addEventListener("click", () => loadSalesPacket({
        serviceFeePct: serviceInput?.value ?? "",
        contingencyPct: contingencyInput?.value ?? "",
        localizationFee: localizationInput?.value ?? "",
      }));
      salesPacketBody.querySelectorAll("[data-copy-sales]").forEach((button) => button.addEventListener("click", async () => {
        const value = button.dataset.copySales === "email"
          ? `Subject: ${packet.outreach?.email_subject || ""}\n\n${packet.outreach?.email_body || ""}`
          : packet.outreach?.x_dm || "";
        try {
          await navigator.clipboard.writeText(value);
          toast("已复制到剪贴板。");
        } catch (_err) {
          toast("浏览器未允许剪贴板访问，请从文案框手动复制。", true);
        }
      }));
      document.getElementById("exportSalesPacketBtn")?.addEventListener("click", () => {
        const blob = new Blob([packet.packet_markdown || ""], { type: "text/markdown;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `${c.name.replace(/[^a-z0-9_-]+/gi, "-").toLowerCase()}-mango-sales-packet.md`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
      });
      document.getElementById("saveCampaignFromSalesPacketBtn")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        if (packet.campaign?.commercial_cost_inputs?.service_fee_pct == null) {
          toast("请先输入 Mango 服务费并重算，再保存客户预算。", true);
          serviceInput?.focus();
          return;
        }
        button.disabled = true;
        try {
          const recommendedTier = (packet.campaign?.pricing_tiers || []).find((row) => row.key === "recommended");
          const recommendedCreators = packet.campaign?.creator_mix_by_tier?.recommended || [];
          const created = await api(`/api/companies/${encodeURIComponent(c.company_id)}/campaigns`, {
            method: "POST",
            body: JSON.stringify({
              name: `${c.name} Recommended sales packet`,
              objective: packet.commercial_thesis,
              target_audience: packet.audience,
              budget_usd: recommendedTier?.total_client_budget_usd ?? null,
              region_pref: packet.campaign?.asia_market_angle,
              platforms_pref: [...new Set(recommendedCreators.map((row) => row.platform).filter(Boolean))].join(", ") || null,
              timing: "与买方确认后排期",
              pricing_assumptions: {
                service_fee_pct: packet.campaign?.commercial_cost_inputs?.service_fee_pct ?? null,
                contingency_pct: packet.campaign?.commercial_cost_inputs?.contingency_pct ?? null,
                localization_fee_usd: packet.campaign?.commercial_cost_inputs?.localization_fee_usd ?? null,
              },
            }),
          });
          for (const creator of recommendedCreators) {
            await api(`/api/shortlists/${created.id}/items`, {
              method: "POST",
              body: JSON.stringify({
                creator_id: creator.id,
                rate_card_id: creator.rate_card_id,
                notes: `Sales packet v7 · ${CAMPAIGN_ROLE_ZH[creator.campaign_role] || creator.campaign_role}`,
              }),
            });
          }
          state.shortlistId = created.id;
          localStorage.setItem("mango_kol_shortlist_id", created.id);
          await refreshShortlistState();
          closeDrawer();
          toast(`已保存 ${recommendedCreators.length} 位 creator 与客户预算，可继续编辑。`);
          await openShortlistModal();
        } catch (err) {
          toast("保存销售包失败：" + err.message, true);
          button.disabled = false;
        }
      });
    } catch (err) {
      renderRetryState(salesPacketBody, "暂时无法加载销售包", () => loadSalesPacket(assumptions), err.message);
    } finally {
      salesPacketButton.disabled = false;
      salesPacketButton.textContent = "重新生成销售包";
    }
  };
  if (salesPacketButton) {
    salesPacketButton.addEventListener("click", () => loadSalesPacket());
    loadSalesPacket();
  }
  const previewButton = document.getElementById("previewCampaignBtn");
  const previewBody = document.getElementById("campaignPreviewBody");
  if (!previewButton || !previewBody) return;
  previewButton.addEventListener("click", async () => {
    previewButton.disabled = true;
    previewButton.textContent = "正在读取报价与历史合作…";
    try {
      const preview = await api(`/api/companies/${encodeURIComponent(c.company_id)}/campaign-preview`);
      previewBody.innerHTML = campaignPreviewHtml(preview);
      const exportButton = document.getElementById("exportProposalBtn");
      exportButton?.addEventListener("click", () => {
        const blob = new Blob([preview.proposal_markdown || ""], { type: "text/markdown;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `${c.name.replace(/[^a-z0-9_-]+/gi, "-").toLowerCase()}-mango-proposal.md`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
      });
      const saveButton = document.getElementById("saveCampaignFromPreviewBtn");
      saveButton?.addEventListener("click", async () => {
        saveButton.disabled = true;
        try {
          const created = await api(`/api/companies/${encodeURIComponent(c.company_id)}/campaigns`, {
            method: "POST",
            body: JSON.stringify({
              objective: preview.campaign_thesis,
              target_audience: preview.target_audience,
              budget_usd: preview.budget_allocation?.recommended_usd ?? null,
              region_pref: preview.asia_market_angle,
              platforms_pref: [...new Set((preview.creator_mix || []).map((row) => row.platform).filter(Boolean))].join(", ") || null,
              timing: "与买方确认后排期",
            }),
          });
          state.shortlistId = created.id;
          localStorage.setItem("mango_kol_shortlist_id", created.id);
          await refreshShortlistState();
          closeDrawer();
          toast(`已保存「${created.name}」，可继续选择 creator 与 deliverable。`);
          await openShortlistModal();
        } catch (err) {
          toast("保存候选名单失败：" + err.message, true);
          saveButton.disabled = false;
        }
      });
    } catch (err) {
      renderRetryState(previewBody, "暂时无法生成方案预览", () => previewButton.click(), err.message);
    } finally {
      previewButton.disabled = false;
      previewButton.textContent = "重新生成只读方案预览";
    }
  });
}

function wireCompanyDrawer(c) {
  if (document.getElementById("canonicalCompanyWorkspace")) {
    wireCanonicalCompanyWorkspace(c);
    return;
  }
  document.querySelectorAll("#drawerBody .creator-name[data-creator-id]").forEach((el) => {
    el.addEventListener("click", () => openDrawer(Number(el.dataset.creatorId)));
  });

  const refresh = () => openCompanyDrawer(c.company_id);

  document.getElementById("buildCampaignBtn").addEventListener("click", () => {
    document.getElementById("buildCampaignForm").innerHTML = `
      <div class="campaign-form">
        <label class="field-label campaign-field-wide">合作目标（必填）<input id="cfObjective" placeholder="例如：提升产品认知" /></label>
        <label class="field-label">预算（USD）<input id="cfBudget" type="number" min="0" placeholder="例如：10000" /></label>
        <label class="field-label">目标受众<input id="cfAudience" placeholder="例如：AI 开发者" /></label>
        <label class="field-label">目标地区<input id="cfRegion" placeholder="例如：北美" /></label>
        <label class="field-label">目标语言<input id="cfLanguage" placeholder="例如：英语" /></label>
        <label class="field-label">目标平台<input id="cfPlatforms" placeholder="例如：YouTube、X" /></label>
        <label class="field-label">时间安排<input id="cfTiming" placeholder="例如：9 月第 2 周" /></label>
      </div>
      <button class="btn btn-primary btn-sm" id="cfSubmit">创建合作候选名单</button>`;
    document.getElementById("cfSubmit").addEventListener("click", async () => {
      const objective = document.getElementById("cfObjective").value.trim() || null;
      const budgetRaw = document.getElementById("cfBudget").value;
      const target_audience = document.getElementById("cfAudience").value.trim() || null;
      const region_pref = document.getElementById("cfRegion").value.trim() || null;
      const language_pref = document.getElementById("cfLanguage").value.trim() || null;
      const platforms_pref = document.getElementById("cfPlatforms").value.trim() || null;
      const timing = document.getElementById("cfTiming").value.trim() || null;
      if (!objective) {
        toast("请先填写明确的合作活动目标，避免创建空名单。", true);
        document.getElementById("cfObjective").focus();
        return;
      }
      if (budgetRaw !== "" && (!Number.isFinite(Number(budgetRaw)) || Number(budgetRaw) < 0)) {
        toast("预算必须是 0 或更大的数字。", true);
        document.getElementById("cfBudget").focus();
        return;
      }
      try {
        const created = await api(`/api/companies/${encodeURIComponent(c.company_id)}/campaigns`, {
          method: "POST",
          body: JSON.stringify({
            objective,
            target_audience,
            budget_usd: budgetRaw === "" ? null : Number(budgetRaw),
            region_pref,
            language_pref,
            platforms_pref,
            timing,
          }),
        });
        state.shortlistId = created.id;
        localStorage.setItem("mango_kol_shortlist_id", created.id);
        await refreshShortlistState();
        closeDrawer();
        toast(`已创建合作候选名单「${created.name}」。`);
        await openShortlistModal();
      } catch (err) {
        toast("创建合作候选名单失败：" + err.message, true);
      }
    });
  });

  // -- Company category/geography edit + verify --
  document.getElementById("saveCompanyFieldsBtn").addEventListener("click", async () => {
    try {
      await api(`/api/companies/${encodeURIComponent(c.company_id)}`, {
        method: "PATCH",
        body: JSON.stringify({
          category: document.getElementById("editCompanyCategory").value || null,
          geography: document.getElementById("editCompanyGeography").value || null,
        }),
      });
      toast("公司资料已保存。");
      refresh();
    } catch (err) {
      toast("保存失败：" + err.message, true);
    }
  });
  document.getElementById("verifyCompanyBtn").addEventListener("click", async () => {
    try {
      await api(`/api/companies/${encodeURIComponent(c.company_id)}/verify`, { method: "POST" });
      toast("公司基础资料检查日期已更新；预算、关系和联系人未自动验证。");
      refresh();
    } catch (err) {
      toast("操作失败：" + err.message, true);
    }
  });

  // -- Operators: edit toggle + save, add new --
  document.querySelectorAll(".operator-edit-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById(`operator-edit-${btn.dataset.operatorId}`).classList.toggle("hidden");
    });
  });
  document.querySelectorAll(".operator-save").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const row = document.getElementById(`operator-edit-${btn.dataset.operatorId}`);
      const body = {
        role: row.querySelector('[data-field="role"]').value || null,
        x_handle: row.querySelector('[data-field="x_handle"]').value || null,
        evidence_urls: row.querySelector('[data-field="evidence_urls"]').value || null,
        identity_human_verified: row.querySelector('[data-field="identity_human_verified"]').checked,
        role_human_verified: row.querySelector('[data-field="role_human_verified"]').checked,
        budget_authority_confirmed: row.querySelector('[data-field="budget_authority_confirmed"]').checked,
      };
      try {
        await api(`/api/operators/${btn.dataset.operatorId}`, { method: "PATCH", body: JSON.stringify(body) });
        toast("联系人信息已保存。");
        refresh();
      } catch (err) {
        toast("保存失败：" + err.message, true);
      }
    });
  });
  document.getElementById("addOperatorToggle").addEventListener("click", () => {
    document.getElementById("addOperatorForm").classList.toggle("hidden");
  });
  document.getElementById("addOperatorSubmit").addEventListener("click", async () => {
    const name = document.getElementById("newOpName").value.trim();
    if (!name) {
      toast("请填写联系人姓名。", true);
      return;
    }
    try {
      await api(`/api/companies/${encodeURIComponent(c.company_id)}/operators`, {
        method: "POST",
        body: JSON.stringify({
          name,
          role: document.getElementById("newOpRole").value || null,
          x_handle: document.getElementById("newOpHandle").value || null,
        }),
      });
      toast("联系人已添加。");
      refresh();
    } catch (err) {
      toast("添加失败：" + err.message, true);
    }
  });

  // -- Action items: edit toggle + save, add new --
  document.querySelectorAll(".action-edit-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById(`action-edit-${btn.dataset.actionId}`).classList.toggle("hidden");
    });
  });
  document.querySelectorAll(".action-mark-done").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api(`/api/action-items/${btn.dataset.actionId}`, {
          method: "PATCH",
          body: JSON.stringify({
            status: "done",
            owner: btn.dataset.owner || null,
            due_date: btn.dataset.due || null,
            outcome_notes: btn.dataset.notes || null,
          }),
        });
        toast("行动项已标记为完成。");
        refresh();
      } catch (err) {
        toast("保存失败：" + err.message, true);
      }
    });
  });
  document.querySelectorAll(".action-save").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const row = document.getElementById(`action-edit-${btn.dataset.actionId}`);
      const owner = row.querySelector('[data-field="owner"]').value.trim();
      const dueDate = row.querySelector('[data-field="due_date"]').value;
      const primaryNextAction = row.querySelector('[data-field="primary_next_action"]').value.trim();
      const fallback = row.querySelector('[data-field="fallback"]').value.trim();
      if (!owner || !dueDate || !primaryNextAction || !fallback) {
        toast("负责人、截止日期、具体下一步和失败备选都必须填写。", true);
        return;
      }
      const body = {
        owner,
        status: row.querySelector('[data-field="status"]').value,
        due_date: dueDate,
        primary_next_action: primaryNextAction,
        fallback,
        success_condition: row.querySelector('[data-field="success_condition"]').value.trim() || null,
        outcome_notes: row.querySelector('[data-field="outcome_notes"]').value || null,
      };
      try {
        await api(`/api/action-items/${btn.dataset.actionId}`, { method: "PATCH", body: JSON.stringify(body) });
        toast("行动项已保存。");
        refresh();
      } catch (err) {
        toast("保存失败：" + err.message, true);
      }
    });
  });
  document.getElementById("addActionToggle").addEventListener("click", () => {
    document.getElementById("addActionForm").classList.toggle("hidden");
  });
  document.getElementById("addActionSubmit").addEventListener("click", async () => {
    const primary_next_action = document.getElementById("newActionText").value.trim();
    const owner = document.getElementById("newActionOwner").value.trim();
    const due_date = document.getElementById("newActionDueDate").value;
    const fallback = document.getElementById("newActionFallback").value.trim();
    if (!owner || !due_date || !primary_next_action || !fallback) {
      toast("负责人、截止日期、具体下一步和失败备选都必须填写。", true);
      return;
    }
    try {
      await api(`/api/companies/${encodeURIComponent(c.company_id)}/action-items`, {
        method: "POST",
        body: JSON.stringify({
          primary_next_action,
          owner,
          due_date,
          fallback,
          status: document.getElementById("newActionStatus").value,
          success_condition: document.getElementById("newActionSuccessCondition").value.trim() || null,
        }),
      });
      toast("行动项已添加。");
      refresh();
    } catch (err) {
      toast("添加失败：" + err.message, true);
    }
  });

  document.getElementById("addOutreachLogToggle").addEventListener("click", () => {
    document.getElementById("addOutreachLogForm").classList.toggle("hidden");
  });
  document.getElementById("newOutreachBridge").addEventListener("change", (event) => {
    const bridge = bridgeById.get(String(event.target.value));
    if (bridge?.target_operator?.id != null) {
      document.getElementById("newOutreachOperator").value = String(bridge.target_operator.id);
    }
  });
  document.getElementById("addOutreachLogSubmit").addEventListener("click", async () => {
    const optionalId = (elementId) => {
      const raw = document.getElementById(elementId).value;
      return raw === "" ? null : Number(raw);
    };
    const stage = document.getElementById("newOutreachStage").value;
    const operatorId = optionalId("newOutreachOperator");
    const bridgeId = optionalId("newOutreachBridge");
    const owner = document.getElementById("newOutreachOwner").value.trim();
    const contactedWho = document.getElementById("newOutreachContactedWho").value.trim();
    const contactChannel = document.getElementById("newOutreachChannel").value;
    if (!owner) {
      toast("请填写 Mango 执行人，避免产生无人负责的结果记录。", true);
      return;
    }
    if (!contactChannel) {
      toast("请选择本次真实使用的联系渠道。", true);
      return;
    }
    if (operatorId == null && bridgeId == null && !contactedWho) {
      toast("请选择联系人/引荐候选人，或填写实际联系的公司账号/对象。", true);
      return;
    }
    if (BRIDGE_REQUIRED_OUTREACH_STAGES.has(stage) && bridgeId == null) {
      toast("这个阶段必须选择具体引荐候选人，不能保存成公司级结论。", true);
      return;
    }
    if (["relationship_confirmed", "target_replied"].includes(stage) && operatorId == null && bridgeId == null) {
      toast("这个阶段必须关联具体目标联系人或引荐候选人。", true);
      return;
    }
    try {
      await api(`/api/companies/${encodeURIComponent(c.company_id)}/outreach-logs`, {
        method: "POST",
        body: JSON.stringify({
          stage,
          operator_id: operatorId,
          bridge_id: bridgeId,
          intro_path_id: optionalId("newOutreachPath"),
          linked_action_item_id: optionalId("newOutreachAction"),
          contact_channel: contactChannel,
          owner,
          contacted_who: contactedWho || null,
          evidence_url: document.getElementById("newOutreachEvidenceUrl").value || null,
          notes: document.getElementById("newOutreachNotes").value || null,
          next_follow_up_date: document.getElementById("newOutreachFollowUp").value || null,
        }),
      });
      toast("已记录。");
      refresh();
    } catch (err) {
      toast("记录失败：" + err.message, true);
    }
  });
  document.querySelectorAll(".outreach-log-void").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const reason = prompt("作废原因（会保留原记录，仅标记为作废）：");
      if (reason === null) return;
      try {
        await api(`/api/outreach-logs/${btn.dataset.logId}/void`, {
          method: "PATCH",
          body: JSON.stringify({ voided_reason: reason || "未填写原因" }),
        });
        toast("已作废。");
        refresh();
      } catch (err) {
        toast("作废失败：" + err.message, true);
      }
    });
  });
  document.querySelectorAll(".outreach-log-edit-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const form = document.getElementById(`outreach-edit-${btn.dataset.logId}`);
      form.classList.toggle("hidden");
      if (!form.classList.contains("hidden")) form.querySelector('[data-field="owner"]')?.focus();
    });
  });
  document.querySelectorAll(".outreach-log-save").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const form = document.getElementById(`outreach-edit-${btn.dataset.logId}`);
      const ownerInput = form.querySelector('[data-field="owner"]');
      const owner = ownerInput.value.trim();
      if (!owner) {
        toast("Mango 执行人不能为空。", true);
        ownerInput.focus();
        return;
      }
      const nextFollowUp = form.querySelector('[data-field="next_follow_up_date"]').value;
      btn.disabled = true;
      try {
        await api(`/api/outreach-logs/${btn.dataset.logId}`, {
          method: "PATCH",
          body: JSON.stringify({
            owner,
            notes: form.querySelector('[data-field="notes"]').value.trim() || null,
            next_follow_up_date: nextFollowUp || null,
          }),
        });
        toast("执行备注和跟进日期已更新；历史事件归属未改写。");
        refresh();
      } catch (err) {
        btn.disabled = false;
        toast("更新失败：" + err.message, true);
      }
    });
  });

  // -- Solomon review --
  document.getElementById("srSaveBtn").addEventListener("click", async () => {
    try {
      await api(`/api/solomon/reviews/${encodeURIComponent(c.company_id)}`, {
        method: "PUT",
        body: JSON.stringify({
          decision: document.getElementById("srDecision").value || null,
          missing_info: document.getElementById("srMissingInfo").value || null,
          solomon_notes: document.getElementById("srNotes").value || null,
          next_action: document.getElementById("srNextAction").value || null,
        }),
      });
      toast("复盘已保存。");
      refresh();
    } catch (err) {
      toast("保存失败：" + err.message, true);
    }
  });

  // -- Sponsorship evidence: confirm / reject --
  document.querySelectorAll(".evidence-confirm").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api(`/api/sponsorship-evidence/${btn.dataset.evidenceId}`, { method: "PATCH", body: JSON.stringify({ review_status: "confirmed" }) });
        toast("证据已确认。");
        refresh();
      } catch (err) {
        toast("操作失败：" + err.message, true);
      }
    });
  });
  document.querySelectorAll(".evidence-reject").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const reviewed_note = window.prompt("驳回原因是什么？（可选）") || null;
      try {
        await api(`/api/sponsorship-evidence/${btn.dataset.evidenceId}`, { method: "PATCH", body: JSON.stringify({ review_status: "rejected", reviewed_note }) });
        toast("证据已驳回。");
        refresh();
      } catch (err) {
        toast("操作失败：" + err.message, true);
      }
    });
  });
}

// ---------------------------------------------------------------------------
// 人脉网络
// ---------------------------------------------------------------------------

async function loadNetwork() {
  const body = document.getElementById("networkBody");
  body.innerHTML = '<div class="empty-state">加载中</div>';
  try {
    const [portfolio, sponsorIntelligence, queue] = await Promise.all([
      api("/api/network/route-portfolio"),
      api("/api/network/sponsor-intelligence-prospects"),
      api("/api/network/interview-queue"),
    ]);
    // queue.people stays in a collapsed archive only; the Route Portfolio is
    // the primary product and GAIB-style relationship verification no longer
    // drives company discovery or ranking.
    body.innerHTML = routePortfolioHtml(portfolio, sponsorIntelligence, queue.people, queue.interaction_audit);
    wireNetwork();
  } catch (err) {
    renderRetryState(body, "暂时无法加载人脉页", loadNetwork, err.message);
  }
}

function routeDirectionLabel(direction) {
  if (direction === "mutual_follow") return "互关";
  if (direction === "follows") return "前者关注后者";
  if (direction === "followed_by") return "后者关注前者";
  return direction || "方向待核实";
}

function routePortfolioCards(rows, emptyText, options = {}) {
  return (rows || []).map((row) => `<article class="route-portfolio-card" data-company-id="${escapeAttr(row.company_id)}">
    <div class="route-portfolio-head"><strong>${escapeHtml(row.company)}</strong><span class="decision-badge tier-${escapeAttr(row.opportunity_value || "tier_c")}">${escapeHtml(row.opportunity_value_label || "待判断")}</span></div>
    <h4>${escapeHtml(productCopy(row.route || "未命名路线"))}</h4>
    ${row.why && !options.hideWhy ? `<p>${escapeHtml(productCopy(row.why))}</p>` : ""}
    ${row.endpoint_name ? `<div><strong>终点：</strong>${escapeHtml(row.endpoint_name)}${row.endpoint_role ? ` · ${escapeHtml(row.endpoint_role)}` : ""}</div>` : ""}
    ${row.endpoint_buyer_status ? `<div><strong>买方相关性：</strong>${escapeHtml(row.endpoint_buyer_status)}</div>` : ""}
    ${row.relationship_basis ? `<div><strong>关系依据：</strong>${escapeHtml(productCopy(row.relationship_basis))}</div>` : ""}
    ${row.first_step ? `<div><strong>第一步：</strong>${escapeHtml(productCopy(row.first_step))}</div>` : ""}
    ${(row.edge_directions || []).length ? `<div class="direction-title">逐边方向：${escapeHtml(row.edge_directions.map(routeDirectionLabel).join("；"))}</div>` : ""}
    <div class="muted">${escapeHtml(row.execution_readiness || "")}${row.evidence_url ? ` · <a href="${escapeAttr(row.evidence_url)}" target="_blank" rel="noopener">路线证据 ↗</a>` : ""}</div>
  </article>`).join("") || `<div class="muted">${escapeHtml(emptyText)}</div>`;
}

function routePortfolioHtml(portfolio, sponsorIntelligence, people, interactionAudit) {
  const warm = portfolio.confirmed_warm_routes || [];
  const employees = portfolio.company_employee_routing_paths || [];
  const official = portfolio.official_commercial_channels || [];
  const programs = portfolio.partnership_community_programs || [];
  const creator = portfolio.creator_side_commercial_intelligence || [];
  const personPaths = portfolio.verified_person_to_person_paths || [];
  const secondary = portfolio.secondary_research_paths || [];
  const crossCompany = portfolio.cross_company_connectors || {};
  return `<header class="decision-home-hero"><div><div class="eyebrow">进入路线组合</div><h2>哪些路线能打开高价值公司</h2><p>正式商业渠道、项目和真实执行记录优先；X 关注链只保留为二级研究线索。</p></div></header>
    ${warm.length ? `<section class="route-group"><div class="home-card-title">已确认 / 暖路线 <span class="hint">必须有人工执行记录</span></div><div class="route-portfolio-grid">${routePortfolioCards(warm, "暂无人工确认的暖路线。")}</div></section>` : ""}
    ${employees.length ? `<section class="route-group"><div class="home-card-title">公司员工路由路径</div><div class="route-portfolio-grid">${routePortfolioCards(employees, "")}</div></section>` : ""}
    <section class="route-group"><div class="home-card-title">跨公司联系情报 <span class="hint">优先处理一次可覆盖多家目标的节点</span></div><div class="provenance-note">${escapeHtml(crossCompany.contract || "跨公司覆盖不等于愿意引荐；先核验真实关系或采购流程。")}</div>${crossCompanyConnectorCards(crossCompany)}</section>
    <section class="route-group"><div class="home-card-title">正式商业渠道 <span class="hint">优先用于发送提案和冷启动接触</span></div><div class="route-portfolio-grid">${routePortfolioCards(official, "暂无正式渠道记录。")}</div></section>
    <section class="route-group"><div class="home-card-title">合作伙伴 / 社区项目</div><div class="route-portfolio-grid">${routePortfolioCards(programs, "暂无正式项目记录。")}</div></section>
    <section class="route-group"><div class="home-card-title">创作者侧采购情报</div><div class="provenance-note">历史合作只用于了解采购流程或复盘合作活动，不代表对方愿意引荐。</div><div class="route-portfolio-grid">${routePortfolioCards(creator, "暂无历史创作者商业情报。", { hideWhy: true })}</div></section>
    ${personPaths.length ? `<section class="route-group"><div class="home-card-title">符合证据门槛的人对人路径 <span class="hint">终点必须是目标公司的真实员工</span></div><div class="provenance-note">每一跳保留方向，且至少一跳存在互关。此处只提供联系人研究线索，不代表已确认引荐。</div><div class="route-portfolio-grid">${routePortfolioCards(personPaths, "")}</div></section>` : ""}
    <details class="research-archive"><summary>二级研究路径（${secondary.length}）</summary><div class="provenance-note">包含 Runway 等逐边方向已保留的弱二级路径；单向关注不证明认识或可引荐。</div><div class="route-portfolio-grid">${routePortfolioCards(secondary, "暂无二级路径。")}</div></details>
    <details class="research-archive"><summary>创作者赞助情报详情</summary>${sponsorIntelligenceHtml(sponsorIntelligence)}</details>
    <details class="research-archive"><summary>待研究 / 历史归档</summary><p>${escapeHtml(productCopy(portfolio.archive?.note || "旧引荐候选队列保留在后端归档。"))}</p><div class="muted">历史引荐候选简报：${portfolio.archive?.connector_brief_count || 0} 条 · 去重后的引荐候选人：${(people || []).length} 位</div>${interactionAuditHtml(interactionAudit)}</details>`;
}

const DECISION_LABEL_ZH = { proceed: "推进", watch: "观察", reject: "放弃", pending: "待定" };

function solomonPilotHtml(pilot) {
  if (!pilot.length) return "";
  const rows = pilot
    .map(
      (r) => `
      <div class="pilot-list-row" data-company-id="${escapeAttr(r.company_id)}">
        <div>
          <div class="name">${escapeHtml(r.company_name)}</div>
          <div class="muted">${r.next_action ? escapeHtml(productCopy(r.next_action)) : "尚未记录下一步行动"}</div>
        </div>
        <span class="decision-tag decision-${escapeAttr(r.decision || "pending")}">${DECISION_LABEL_ZH[r.decision || "pending"]}</span>
      </div>`
    )
    .join("");
  return `
    <details class="alt-paths-toggle" style="margin:22px 0">
      <summary>复盘试点（${pilot.length} 家公司，均待内部真实复盘）</summary>
      <div class="home-card" style="margin-top:10px">${rows}</div>
    </details>
  `;
}

function _bridgeCardHtml(b) {
  return `
      <div class="connector-card verified">
        <div class="connector-head">
          <span class="connector-name">@${escapeHtml(b.bridge_handle)}${b.bridge_name ? " -- " + escapeHtml(b.bridge_name) : ""}</span>
          <span class="research-tag">已观察到 X 互关 · 关系待核实</span>
        </div>
        <div class="connector-companies">
          ${b.companies.map((c) => `<span class="company-pill" data-company-id="${escapeAttr(c.company_id)}">${escapeHtml(c.company_name)}${c.operator_name ? "（" + escapeHtml(c.operator_name) + "）" : ""}</span>`).join("")}
        </div>
        ${b.bridge_bio ? `<div class="connector-action">${escapeHtml(b.bridge_bio)}</div>` : ""}
      </div>`;
}

// Section V (2026-08-28): the network page's first question is "who
// should Mango ask, and how many companies does one conversation cover"
// -- not a bridge-person leaderboard. This is that queue, grouped by who
// has to do the asking.
function _interviewQueueHtml(queue) {
  if (!queue || !queue.length) return '<div class="muted">暂无待核实的互关候选人。</div>';
  return queue
    .map((group) => {
      const itemHtml = (item) => `
          <div class="bridge-row">
            <div><span class="company-link" data-company-id="${escapeAttr(item.company_id)}">${escapeHtml(item.company_name)}</span> <span class="muted">（此公司 ${candidateCountsByCompany.get(item.company_id) || 1} 位候选人）</span> -- @${escapeHtml(item.bridge_handle)}${item.bridge_name ? "（" + escapeHtml(item.bridge_name) + "）" : ""} <span class="reach-label reach-level-${escapeAttr(String(item.code || "E1").replace("E", ""))}">${escapeHtml(relationshipTruthSummary(item))}</span></div>
            <div class="muted">${escapeHtml(productCopy(item.evidence_note))}</div>
            <div class="reason-text"><strong>第一问：</strong>${escapeHtml(connectorFirstAsk(item, group.ask))}</div>
          </div>`;
      const allItems = group.items || [];
      const companyIds = new Set(allItems.map((item) => item.company_id).filter(Boolean));
      const candidateCountsByCompany = new Map();
      allItems.forEach((item) => candidateCountsByCompany.set(item.company_id, (candidateCountsByCompany.get(item.company_id) || 0) + 1));
      const firstPerCompany = [];
      const additionalCandidates = [];
      const shownCompanies = new Set();
      allItems.forEach((item) => {
        if (!shownCompanies.has(item.company_id)) {
          shownCompanies.add(item.company_id);
          firstPerCompany.push(item);
        } else {
          additionalCandidates.push(item);
        }
      });
      const visible = firstPerCompany.slice(0, 4);
      const rest = firstPerCompany.slice(4).concat(additionalCandidates);
      const candidateCount = Number.isFinite(group.count) ? group.count : allItems.length;
      return `
      <div class="bridge-block">
        <div class="bridge-title">请 ${escapeHtml(mangoAskerLabel(group.ask))} 做内部关系核实</div>
        <div class="network-count-line">本轮优先 ${visible.length} 家公司 · 全队列 ${companyIds.size} 家公司 / ${candidateCount} 位候选人</div>
        ${visible.map(itemHtml).join("")}
        ${rest.length ? `<details class="alt-paths-toggle"><summary>还有 ${rest.length} 位候选线索（同一家公司可能有多人）</summary>${rest.map(itemHtml).join("")}</details>` : ""}
      </div>`;
    })
    .join("");
}

const SPONSOR_ASKABILITY_ZH = {
  low_cost_information_ask: "可先低成本请教信息",
  relationship_building_first: "先建立关系，再请教信息",
};
const SOCIAL_COST_ZH = { low: "低", medium: "中", medium_to_high: "中高", high: "高", unknown: "未知" };

function _sponsorObservationLabel(company) {
  if (company.confirmed_paid_count) return `${company.confirmed_paid_count} 条付费证据已复核`;
  if (company.paid_observation_count) return `${company.paid_observation_count} 条付费观察待复核`;
  if ((company.evidence_types || []).includes("affiliate")) return "联盟合作观察";
  return `${company.observation_count || 0} 条商业类型观察`;
}

function _sponsorProspectCardHtml(prospect, isMedia = false) {
  const mango = prospect.mango_relationship || {};
  const contactText = mango.contact_available
    ? `已有 ${escapeHtml((mango.contact_types || []).join("、") || "联系渠道")}`
    : "暂无可用联系方式";
  const hasNumericQuote = Boolean(mango.numeric_quote_available);
  const pricingText = hasNumericQuote
    ? `已有可用数值报价记录 ${fmtUsd(mango.quote_min_usd)}${mango.quote_max_usd !== mango.quote_min_usd ? ` 至 ${fmtUsd(mango.quote_max_usd)}` : ""}`
    : mango.pricing_record_available
    ? "有报价历史记录，尚无可靠数值"
    : "暂无报价记录";
  const companies = (prospect.companies_potentially_unlocked || []).map((company) => {
    const evidenceUrl = (company.evidence_urls || [])[0];
    return `
      <div class="sponsor-company-row">
        <button class="company-pill company-link" type="button" data-company-id="${escapeAttr(company.company_id)}">${escapeHtml(company.company_name)}</button>
        <span>${escapeHtml(_sponsorObservationLabel(company))}${company.latest_evidence_date ? ` · 最近 ${escapeHtml(company.latest_evidence_date)}` : ""}</span>
        ${evidenceUrl ? `<a href="${escapeAttr(evidenceUrl)}" target="_blank" rel="noopener" aria-label="查看 ${escapeAttr(company.company_name)} 的公开合作证据">证据 ↗</a>` : ""}
      </div>`;
  }).join("");
  return `
    <article class="sponsor-intel-card ${isMedia ? "media-channel" : ""}">
      <div class="sponsor-intel-head">
        <button class="link-btn sponsor-creator-link" type="button" data-creator-id="${prospect.creator_id}">${escapeHtml(prospect.creator_name)}</button>
        <span class="research-tag">${isMedia ? "媒体 / 社区渠道" : "商业背景情报候选"}</span>
      </div>
      <div class="sponsor-intel-meta">
        <span>${escapeHtml(classLabel(prospect.creator_class))}</span>
        <span>${escapeHtml(SPONSOR_ASKABILITY_ZH[prospect.askability] || "先建立关系")}</span>
        <span>社交成本：${escapeHtml(SOCIAL_COST_ZH[prospect.social_cost] || "未知")}</span>
        <span>引荐准备度：未验证</span>
      </div>
      <div class="sponsor-intel-facts"><span>Mango 侧：${contactText}</span><span>${pricingText}</span></div>
      <div class="sponsor-company-list">${companies || '<div class="muted">暂无关联公司证据。</div>'}</div>
      <div class="reason-text"><strong>第一问：</strong>${escapeHtml(prospect.exact_first_ask || "先请教行业与采购背景，不请求引荐。")}</div>
      <div class="provenance-note">上方金额只表示历史报价文本已成功解析，不表示人工或当前报价确认。联系方式、公开合作观察、是否真实付费、是否认识现任预算负责人、关系是否仍有效、是否愿意帮助均需分开核实。</div>
      <div class="muted"><strong>何时才适合谈引荐：</strong>仅当本人确认当前仍认识 sponsor 侧负责人，并明确同意或主动提出后。</div>
      <div class="muted"><strong>备选：</strong>${isMedia ? "若不适合讨论 sponsor 侧联系人，仅将其作为媒体/社区分发渠道或请教品类反馈。" : "若不便讨论 sponsor 侧联系人，仅请教品类层面的合作偏好；也可只作为创作者供给评估。"}</div>
    </article>`;
}

function sponsorIntelligenceHtml(data) {
  if (!data) return "";
  const prospects = data.results || data.prospects || [];
  const mediaChannels = data.media_channels || [];
  const visible = prospects.slice(0, 3);
  const rest = prospects.slice(3);
  return `
    <section class="sponsor-intelligence-section" aria-labelledby="sponsorIntelTitle">
      <div class="home-card-title" id="sponsorIntelTitle">历史合作 → 商业背景情报候选<span class="hint">先问信息，不等于认识预算负责人或愿意引荐</span></div>
      <div class="provenance-note">公开赞助或联盟观察可支持低成本的信息询问；不能自动升级为付费事实、真实中间联系人或可引荐关系。</div>
      <div class="sponsor-intel-grid">${visible.map((row) => _sponsorProspectCardHtml(row)).join("") || '<div class="muted">暂无创作者型商业背景情报候选。</div>'}</div>
      ${rest.length ? `<details class="alt-paths-toggle"><summary>还有 ${rest.length} 位商业背景情报候选</summary><div class="sponsor-intel-grid">${rest.map((row) => _sponsorProspectCardHtml(row)).join("")}</div></details>` : ""}
      ${mediaChannels.length ? `<details class="alt-paths-toggle media-intel-toggle"><summary>媒体 / 社区商业渠道（${mediaChannels.length}）· 不进入创作者或中间联系人候选池</summary><div class="sponsor-intel-grid">${mediaChannels.map((row) => _sponsorProspectCardHtml(row, true)).join("")}</div></details>` : ""}
    </section>`;
}

const CONNECTOR_ASKABILITY_ZH = {
  ask_for_context_first: "可先向 Mango 负责人问背景",
  internal_relationship_check_only: "仅做内部关系核实",
  verify_evidence_before_asking: "先补互动证据，再决定是否内部询问",
};
const CONNECTOR_STATUS_ZH = {
  relationship_candidate: "关系候选 · 未人工确认",
  verification_queue: "待核查",
  relationship_confirmed: "真实关系已人工确认",
  intro_ready: "已明确同意引荐",
};

function interactionAuditHtml(audit) {
  if (!audit) return "";
  const checkedDates = (audit.checked_at || []).map((value) => new Date(value)).filter((date) => !Number.isNaN(date.valueOf()));
  const latestCheck = checkedDates.length ? new Date(Math.max(...checkedDates)).toLocaleString() : "未记录";
  const companyRows = (audit.by_company || []).map((company) => `
    <div class="audit-company-row">
      <button class="company-link link-btn" type="button" data-company-id="${escapeAttr(company.company_id)}">${escapeHtml(company.company_name)}</button>
      <span>候选 ${company.total_candidates}</span><span>已查 ${company.checked}</span><span>找到公开互动 ${company.interaction_found}</span><span>本次未找到 ${company.no_public_interaction_found}</span><span>未查 ${company.unchecked}</span>
    </div>`).join("");
  return `
    <section class="interaction-audit" aria-labelledby="interactionAuditTitle">
      <div class="home-card-title" id="interactionAuditTitle">公开互动核查覆盖</div>
      <div class="interaction-audit-stats">
        <div><span>原始候选行</span><strong>${audit.raw_bridge_rows}</strong></div>
        <div><span>去重公司-人组合</span><strong>${audit.unique_company_person_pairs}</strong></div>
        <div><span>已核查</span><strong>${audit.checked}</strong></div>
        <div><span>找到公开互动</span><strong>${audit.interaction_found}</strong></div>
        <div><span>本次未找到</span><strong>${audit.no_public_interaction_found}</strong></div>
        <div><span>尚未核查</span><strong>${audit.unchecked}</strong></div>
      </div>
      <div class="provenance-note">最近查询：${escapeHtml(latestCheck)}。“本次未找到”只表示所述搜索窗口内未命中，不代表两人不认识或关系不存在。</div>
      <div class="audit-company-list">${companyRows}</div>
      ${(audit.search_scope || []).length ? `<details class="alt-paths-toggle"><summary>查询范围与限制</summary>${audit.search_scope.map((note) => `<div class="provenance-note">${escapeHtml(note)}</div>`).join("")}</details>` : ""}
    </section>`;
}

function _connectorPersonCardHtml(person) {
  const companies = (person.companies || []);
  const companyPills = companies.map((company) => `<button class="company-pill company-link" type="button" data-company-id="${escapeAttr(company.company_id)}">${escapeHtml(company.company_name)}</button>`).join("");
  const evidenceRows = companies.slice(0, 3).map((company) => `
    <div class="connector-evidence-row"><span>${escapeHtml(company.company_name)}</span><span>${escapeHtml(relationshipTruthSummary(company))}</span><span>${escapeHtml(productCopy(company.evidence_note || "尚未记录公开互动核查"))}</span></div>`).join("");
  const asks = [...new Set(companies.map((company) => company.askability).filter(Boolean))];
  const costs = [...new Set(companies.map((company) => company.social_cost).filter(Boolean))];
  const checkedTruth = person.interaction_checked
    ? person.interaction_found
      ? "已在所述查询窗口找到公开互动；真实关系仍未确认"
      : "已查但本次未找到公开互动；不代表不认识"
    : "公开互动尚未核查";
  return `
    <article class="connector-person-card">
      <div class="connector-head">
        <div><a class="connector-name" href="https://x.com/${escapeAttr(person.bridge_handle)}" target="_blank" rel="noopener">@${escapeHtml(person.bridge_handle)} ↗</a>${person.bridge_name ? `<div class="connector-handle">${escapeHtml(person.bridge_name)}</div>` : ""}</div>
        <span class="research-tag">${escapeHtml(CONNECTOR_STATUS_ZH[person.current_status] || "候选关系 · 未验证")}</span>
      </div>
      <div class="connector-person-meta">
        <span>Mango 负责人：${escapeHtml((person.mango_owner_candidates || []).map(mangoAskerLabel).join("、") || "待认领")}</span>
        <span>${escapeHtml(asks.map((value) => CONNECTOR_ASKABILITY_ZH[value] || value).join(" / ") || "可询问性待评估")}</span>
        <span>社交成本：${escapeHtml(costs.map((value) => SOCIAL_COST_ZH[value] || value).join(" / ") || "未知")}</span>
      </div>
      <div class="connector-companies">${companyPills || '<span class="muted">暂无关联目标公司</span>'}</div>
      <div class="reason-text"><strong>已验证：</strong>${escapeHtml(checkedTruth)}</div>
      <div class="connector-evidence-list">${evidenceRows}</div>
      <div class="reason-text"><strong>第一问：</strong>${escapeHtml(person.exact_first_ask || connectorFirstAsk(person, (person.mango_owner_candidates || [])[0]))}</div>
      <div class="provenance-note">仍未验证：Mango 是否真实认识此人、此人与目标负责人的当前关系、对方是否愿意帮助或引荐。</div>
      <div class="muted"><strong>备选：</strong>${escapeHtml(productCopy(person.fallback || "若关系未确认，改查官方渠道或其他有合作背景的候选人。"))}</div>
    </article>`;
}

function connectorPeopleHtml(people) {
  const rows = people || [];
  if (!rows.length) return '<div class="muted">暂无按人去重的中间联系人候选。</div>';
  const visible = rows.slice(0, 6);
  const rest = rows.slice(6);
  return `
    <section class="connector-people-section" aria-labelledby="connectorPeopleTitle">
      <div class="home-card-title" id="connectorPeopleTitle">按人去重的中间联系人核实队列<span class="hint">${rows.length} 人；按可询问性与证据强度排序，不按粉丝量</span></div>
      <div class="connector-people-grid">${visible.map(_connectorPersonCardHtml).join("")}</div>
      ${rest.length ? `<details class="alt-paths-toggle"><summary>还有 ${rest.length} 位低优先级或未核查候选</summary><div class="connector-people-grid">${rest.map(_connectorPersonCardHtml).join("")}</div></details>` : ""}
    </section>`;
}

function networkHtml(connectors, bridges, operators, pilot, queue, sponsorIntelligence, people, interactionAudit) {
  const bridgeList = bridges || [];
  // Already sorted server-side by company_count desc then stable handle,
  // so the cross-company bridges (the whole point of this section) are
  // always in the visible slice, not buried among ~60 single-company ones.
  const shownBridges = bridgeList.slice(0, 8);
  const restBridges = bridgeList.slice(8);
  const bridgeCards =
    (shownBridges.map(_bridgeCardHtml).join("") || '<div class="muted">暂无互关引荐候选人。</div>') +
    (restBridges.length
      ? `<details class="alt-paths-toggle"><summary>还有 ${restBridges.length} 位（均为单家公司）</summary>${restBridges.map(_bridgeCardHtml).join("")}</details>`
      : "");

  const connectorList = connectors || [];
  const connectorCards = connectorList
    .map(
      (b) => `
      <div class="connector-card">
        <div class="connector-head">
          <span class="connector-name">${escapeHtml(b.connector_name)}</span>
          <span class="connector-handle">${escapeHtml(b.connector_x_handle || "")}</span>
        </div>
        <div class="connector-companies">
          ${b.companies.map((c) => `<span class="company-pill" data-company-id="${escapeAttr(c.company_id)}">${escapeHtml(c.name)}</span>`).join("")}
        </div>
        <div class="provenance-note">历史公司映射，仅供回查；旧行动措辞不在此页展示。</div>
      </div>`
    )
    .join("") || '<div class="muted">暂无连接人简报记录。</div>';

  const operatorRows = (operators || [])
    .map(
      (o) => `
      <div class="op-list-row">
        <div>
          <div>${escapeHtml(o.name)}</div>
          <div class="role">${escapeHtml(o.role || "职位未知")} -- <span class="company-link" data-company-id="${escapeAttr(o.company_id)}">${escapeHtml(o.company_name)}</span></div>
          <div class="op-verify-grid muted">
            <div>公开身份匹配：${o.identity_source_matched ? "官网/公司来源 + X 资料已匹配" : "公开身份资料待补齐"}</div>
            <div>身份人工复核：${verificationDateLabel(o.identity_human_verified, o.identity_human_verified_at)}</div>
            <div>当前职位人工复核：${verificationDateLabel(o.role_human_verified, o.role_human_verified_at)}</div>
            <div>X 账号：${o.x_account_verified ? "已精确匹配 @" + escapeHtml(o.x_handle) : o.x_handle ? "候选账号 @" + escapeHtml(o.x_handle) + "，待核实" : "未匹配"}</div>
            <div>私信状态：${o.dm_status === "verified_open" ? "已验证开放" : o.dm_status === "checked_not_open" ? "已查询，未开放" : "未核实"}</div>
            <div>${o.budget_authority_confirmed && o.budget_authority_verified_at ? "预算影响力：已人工确认" : "预算影响力：未核实"}</div>
          </div>
        </div>
      </div>`
    )
    .join("") || '<div class="muted">暂无已识别的目标联系人记录。</div>';

  return `
    ${interactionAuditHtml(interactionAudit)}
    ${connectorPeopleHtml(people)}
    <details class="alt-paths-toggle grouped-verification-queue" style="margin:22px 0">
      <summary>按 Mango 执行人查看完整核实队列</summary>
      <div class="provenance-note">这是执行视图，不是关系排行榜；同一家公司可能有多位候选。</div>
      ${_interviewQueueHtml(queue)}
    </details>
    ${sponsorIntelligenceHtml(sponsorIntelligence)}
    ${solomonPilotHtml(pilot || [])}
    <details class="alt-paths-toggle" style="margin:22px 0">
      <summary>互关研究线索（${bridgeList.length} 位）</summary>
      <div class="provenance-note">公开 X 互关不代表真实认识或愿意引荐，也不按粉丝量自动选择联系人。</div>
      ${bridgeCards}
    </details>
    <details class="alt-paths-toggle" style="margin:22px 0">
      <summary>历史公司映射（${connectorList.length} 条）</summary>
      <div class="provenance-note">早期研究材料可能已过期，不参与当前关系阶段与执行优先级计算；旧行动措辞已隐藏。</div>
      ${connectorCards}
    </details>
    <details class="alt-paths-toggle" style="margin:22px 0">
      <summary>已识别目标联系人（${(operators || []).length} 位）</summary>
      <div class="home-card">${operatorRows}</div>
    </details>
  `;
}

function wireNetwork() {
  document.querySelectorAll("#networkBody [data-company-id]").forEach((el) => {
    const open = () => openCompanyDrawer(el.dataset.companyId);
    if (el.matches("button, a")) el.addEventListener("click", open);
    else makeActivatable(el, open);
  });
  document.querySelectorAll("#networkBody [data-creator-id]").forEach((el) => {
    el.addEventListener("click", () => {
      switchSection("creators");
      openDrawer(Number(el.dataset.creatorId));
    });
  });
}

// ---------------------------------------------------------------------------
// Global search
// ---------------------------------------------------------------------------

let globalSearchDebounce;
document.getElementById("globalSearch").addEventListener("input", (e) => {
  clearTimeout(globalSearchDebounce);
  const input = e.currentTarget;
  const q = input.value.trim();
  const resultsEl = document.getElementById("globalSearchResults");
  if (q.length < 2) {
    resultsEl.classList.add("hidden");
    input.setAttribute("aria-expanded", "false");
    return;
  }
  globalSearchDebounce = setTimeout(async () => {
    try {
      const data = await api("/api/search?q=" + encodeURIComponent(q));
      renderGlobalSearchResults(data);
    } catch (err) {
      renderRetryState(resultsEl, "搜索暂时不可用", () => input.dispatchEvent(new Event("input")), err.message);
      resultsEl.classList.remove("hidden");
      input.setAttribute("aria-expanded", "true");
    }
  }, 220);
});

function renderGlobalSearchResults(data) {
  const resultsEl = document.getElementById("globalSearchResults");
  const groups = [
    { label: "公司", items: data.companies, kind: "company" },
    { label: "联系人", items: data.operators, kind: "operator" },
    { label: "创作者", items: data.creators, kind: "creator" },
  ].filter((g) => g.items && g.items.length);

  if (!groups.length) {
    resultsEl.innerHTML = '<div class="search-empty">没有匹配结果。</div>';
    resultsEl.classList.remove("hidden");
    document.getElementById("globalSearch").setAttribute("aria-expanded", "true");
    return;
  }

  resultsEl.innerHTML = groups
    .map(
      (g) => `
      <div class="search-group-label">${g.label}</div>
      ${g.items
        .map((item) => {
          if (g.kind === "company") return `<div class="search-result-row" data-kind="company" data-id="${escapeAttr(item.company_id)}"><span class="name">${escapeHtml(item.name)}</span></div>`;
          if (g.kind === "operator") return `<div class="search-result-row" data-kind="company" data-id="${escapeAttr(item.company_id)}"><span class="name">${escapeHtml(item.name)}</span><span class="meta">${escapeHtml(item.company_name)}</span></div>`;
          return `<div class="search-result-row" data-kind="creator" data-id="${item.id}"><span class="name">${escapeHtml(item.display_name)}</span></div>`;
        })
        .join("")}`
    )
    .join("");
  resultsEl.classList.remove("hidden");
  document.getElementById("globalSearch").setAttribute("aria-expanded", "true");

  resultsEl.querySelectorAll(".search-result-row").forEach((row) => {
    makeActivatable(row, () => {
      resultsEl.classList.add("hidden");
      document.getElementById("globalSearch").setAttribute("aria-expanded", "false");
      document.getElementById("globalSearch").value = "";
      if (row.dataset.kind === "company") {
        switchSection("opportunities");
        openCompanyDrawer(row.dataset.id);
      } else {
        openDrawer(Number(row.dataset.id));
      }
    });
  });
}

document.addEventListener("click", (e) => {
  const wrap = document.getElementById("globalSearchWrap");
  if (wrap && !wrap.contains(e.target)) {
    document.getElementById("globalSearchResults").classList.add("hidden");
    document.getElementById("globalSearch").setAttribute("aria-expanded", "false");
  }
});

// ---------------------------------------------------------------------------
// Utils
// ---------------------------------------------------------------------------

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeAttr(str) {
  return escapeHtml(str);
}

// Research/route notes routinely embed a bare source URL (an affiliate page,
// a press release, a contact form) -- escape first, then turn already-escaped
// URLs into real links so a person can click straight through instead of
// copy-pasting.
function linkifyText(str) {
  return escapeHtml(str).replace(/https?:\/\/[^\s<]+/g, (url) => `<a href="${url}" target="_blank" rel="noopener">${url}</a>`);
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

(async function init() {
  const initialParams = new URLSearchParams(window.location.search);
  const requestedSection = initialParams.get("section");
  const requestedCompany = initialParams.get("company");
  const requestedCreator = initialParams.get("creator");
  await loadAccessMode();
  try {
    state.status = await api("/api/meta/status");
  } catch (e) {
    /* keep optimistic default; enrich calls will surface the real error */
  }
  if (!state.status.rapid_x_configured && !state.status.youtube_configured) {
    document.getElementById("batchEnrichBtn").disabled = true;
    document.getElementById("batchEnrichBtn").title = "服务器尚未配置 X 或 YouTube 数据连接。";
  }
  await Promise.allSettled([loadHome(), refreshShortlistState(), loadCreators()]);
  // Read-only deep links make an internal UAT finding or account brief
  // shareable without adding accounts/password pages. They select existing
  // deterministic views only; they never write state.
  if (["home", "opportunities", "network", "creators"].includes(requestedSection)) {
    switchSection(requestedSection);
  }
  if (requestedCompany) {
    switchSection("opportunities");
    await openCompanyDrawer(requestedCompany);
  } else if (requestedCreator && /^\d+$/.test(requestedCreator)) {
    switchSection("creators");
    await openDrawer(Number(requestedCreator));
  }
})();
