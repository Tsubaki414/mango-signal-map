/* Home copy, verbatim from the handoff prototype.
 *
 * Kept in one module because product-language.md makes wording binding: one
 * concept, one name, product-wide. Copy scattered through JSX is copy that
 * drifts. Three lines here are load-bearing product claims, not decoration:
 *   - "确认不是购买"        -- the client is not transacting at this step
 *   - "价格只显示档位"       -- tiers only; never an exact figure
 *   - "已核实报价"          -- says the quote was collected, not that it is current
 */

export const EYEBROW = "01 /// KOL 阵容配置";

export const TITLE_EM = "Mango Signal Map，";
export const TITLE_REST = "让重要的人，看见你。";

export const LEDE =
  "你表达想被谁注意到、进入哪些圈层、预算多少；Mango 已核实报价的创作者随之重新排列，形成一份属于你项目、每个人都有解释的传播阵容。";

export const CTA_BRIEF = "MANGO 已为我准备了 BRIEF";
export const CTA_NEW = "第一次接触 MANGO";

export const TOKEN_PLACEHOLDER = "输入 Mango 顾问发给你的 Brief 码 · 演示输入 demo";
export const TOKEN_SUBMIT = "打开 BRIEF";
export const TOKEN_ERROR = "未找到该 Brief，请核对或联系你的 Mango 顾问。";

export const SLASH_FLOW =
  "AI / CRYPTO / FINTECH · 中文 / EN / 跨境 · X / YOUTUBE / PODCAST / NEWSLETTER";

export const FLOW = [
  {
    n: "01",
    title: "你表达偏好",
    body: "想被谁注意到、进入哪些圈层、预算多少。每个选择即时改变候选与预算，不需要研究几百个账号。",
  },
  {
    n: "02",
    title: "名单随你成形",
    body: "每位创作者有本项目适配度与解释；整套阵容说明覆盖哪些圈层、预算花在哪里、替换一个人会发生什么。",
  },
  {
    n: "03",
    title: "Mango 复核并执行",
    body: "确认初步阵容后，Mango 复核最新报价、档期与合作条件，返回最终执行方案。确认不是购买。",
  },
] as const;

export const OUTCOME_EYEBROW = "你最终会拿到";
export const OUTCOME_TITLE = "一份能在会议上直接讨论的初步阵容。";
export const OUTCOME_BODY = "不是几百个账号的数据库，也不是只能签字的静态提案。";

export const OUTCOME_ROWS = [
  {
    n: "01",
    title: "名单里的每个人，都能解释为什么是他",
    body: "本项目适配度、传播角色、面向的人群与市场、价格档位、报价与商务状态。",
  },
  {
    n: "02",
    title: "整套阵容覆盖了哪些圈层，缺口在哪",
    body: "可能进入谁的信息流；已观察到的关系 Signal；替换一个人会发生什么。",
  },
  {
    n: "03",
    title: "预算区间与哪些事项仍需 Mango 核验",
    body: "价格只显示档位；确认后 Mango 复核最新报价、档期与合作条件并返回执行方案。",
  },
] as const;

export const COPYRIGHT = "© 2026 MANGO LABS GLOBAL HOLDING LTD.";

/* From design_handoff/data/demo.js. Shown while the surface runs on demo data;
 * it must disappear the moment real project data is bound, or it becomes a lie
 * in the other direction. */
export const DEMO_NOTICE =
  "演示数据：报价档位来自 Mango 历史报价记录（有效性未核验）；市场/语言/领域/受众/关系 Signal 为演示标注。";
