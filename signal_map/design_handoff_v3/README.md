# Mango Signal Map v3 · 后端对接包

一条链接发给项目方：客户表达方向 → 看到自己会进入哪几类人的视野 → 拿到可投放创作者名单 → 保留 → 交回 Mango 复核与建联。

**核心方法：target-backed KOL discovery** —— 以目标人物（root）为起点，取其公开关注与互动网络，反推能把内容送进这些人视野的创作者。

> ⚠️ **最容易做错的一点**：候选池 **不等于** 已有报价库。池子里相当一部分人 Mango 从未联系过、没有报价——他们是通过共同关注关系**发现**出来的。这是产品最值钱的部分（"我们还会为你去找并建联新的人"），也是 Mango 资源库自我扩张的入口。前端已按此设计，后端必须两类都返回。

---

## 一、这一版的三个产品决定（会直接影响接口设计）

**1 · 客户不逐个勾选目标人物。**
早期版本让客户点具体人物，结果每点一个名单就收窄（26 → 4）——**客户越参与，结果越少**。现在候选池固定为"与该领域任一圈层人物有公开连接的创作者"，客户只能对**圈层**做「设为本轮重点」（最多 3 个），且**只重排不过滤**，数量永不下降。

后端含义：`GET /candidates` 不接受 `targetIds` 作为过滤条件。返回全量候选 + 每条候选的连接明细，重点加权在前端（或由后端接受 `focus[]` 只影响 `sort`）。

**2 · 圈层（circle）是第一等公民，目标人物是它的证据。**
每个圈层要带两段文案：`who`（这类人是谁、为什么值得影响）和 `pick`（**KOL 选择逻辑**——因为这群人是这样，我们据此如何为你挑人）。`pick` 是名单可解释性的来源，不是内容策略建议。

**3 · 排序主依据是连接，不是综合分。**
`fit` 只是可展开的解释，真正的排序键是"重点圈层的连接权重"。不要把所有维度揉成一个综合分再排——那会稀释"这个人能把内容送进你要影响的人的视野"这个唯一清晰的信号。

---

## 二、接口清单

| 用途 | 接口 | 说明 |
|---|---|---|
| 全库汇总 | `GET /api/meta` | `{kols, platforms, groups, markets}`，首页数字 |
| 分类体系 | `GET /api/taxonomy` | 市场/语言/领域/人群/形式/档位阈值/预算档 |
| 领域与圈层 | `GET /api/groups` | `Group[]`，每个含 `circles[]`（带 who / pick / value） |
| 圈层代表人物 | `GET /api/targets?group=ai` | `Target[]`，按 `circle` 分组 |
| 候选池 | `GET /api/candidates?group=&markets=&domains=&audiences=` | `Candidate[]`，**含 priced 与 discovered 两类** |
| 发现管线 | `POST /api/discover { group, circles? }` | 重算共同关注子集；返回新增 discovered 候选 |
| Brief | `GET /api/brief/:code` | 预填 prefs；无效返回 404 |
| 保存进度 | `PUT /api/sessions/:id` | `{prefs, focus, custom, plans, plan, listOpen}` |
| 提交名单 | `POST /api/sessions/:id/submit` | 回执 |
| 待核查目标 | `POST /api/sessions/:id/wanted` | 客户手填的库外人物，交 BD 核查 |

---

## 三、数据模型

```ts
Group {
  id: 'ai' | 'crypto' | 'finance'
  label; desc
  domains: string[]              // 该领域下的细分方向
  circles: Circle[]
}

Circle {
  id; label
  value: string      // 对客户的价值，如 '投资人关注 · BD 连接'
  who:   string      // 这类人是谁、为什么值得影响（1–2 句）
  pick:  string      // KOL 选择逻辑：据此如何挑人（1 句）
}

Target {                          // 圈层代表人物，仅展示，不可被客户勾选
  id; name; handle; role
  group; circle
  why: string                     // 为什么值得触达
  markets: string[]; audiences: string[]
  avatarUrl?                      // 缺失时前端退回首字母
}

Candidate {
  id; name; handle; url; platform; followers; type
  group
  source: 'priced' | 'discovered'          // ★ 必填，前端分组展示
  // ── priced 专有
  tier?: 1|2|3|4                           // 单条主内容 USD 档位；客户端只见 $ / $$ / $$$ / $$$$
  quoteDate?; quoteStatus?: 'confirmed' | 'historical_unverified'
  // ── discovered 专有
  discoveryPath?: string[]                 // 经由哪几位 target 的共同关注找到（target id）
  // ── 通用
  bizState: 'ready' | 'open_channel' | 'needs_bd'
  bizEvidence: string                      // 依据，如 '官网有 sponsor form 与商务邮箱'
  markets[]; languages[]; domains[]; audiences[]; formats[]
  contentLine?: string                     // 一句话内容方向（有则优先于前端生成）
  avatarUrl?; bio?; risk: string[]
  engagement: { medianViews; replyQuality }
  edges: Edge[]                            // 与本领域 target 的公开连接
}

Edge {
  targetId
  type: 'cofollow' | 'reply' | 'quote' | 'co_appear'
  count; lastSeen                          // ISO 或 YYYY-MM
  verified: boolean
  strength: 'strong' | 'medium' | 'weak'
  evidence?: string
}

Session {
  prefs: { group, domains[], markets[], languages[], audiences[], budget, brand[] }
  focus: string[]                           // 重点圈层 id，最多 3
  custom: { name, hint? }[]                 // 客户手填、待 BD 核查的目标
  plans: { A: Kept[], B: Kept[] }; plan: 'A'|'B'
  listOpen: boolean; submittedAt?
}
Kept { id, format: 'post'|'thread'|'spaces' }
```

### 关系强度三级（不要输出 H0–H3 这类内部代号）
| 级别 | 依据 |
|---|---|
| `strong` | 多次回复 / 引用 / 同场，且主题相关 |
| `medium` | 单次互动，或双向关注 |
| `weak` | 仅单向关注 |

### 商务状态三态
| 值 | 客户端文案 | 含义 |
|---|---|---|
| `ready` | 可立即确认报价与档期 | 已在报价库、报价有效 |
| `open_channel` | 有公开合作入口，待接洽 | 有 sponsor 页或商务邮箱 |
| `needs_bd` | 需 Mango 主动建联 | 未联系过，多为 discovered |

---

## 四、演示数据生成器要替换的部分

`data/v3.js` 里这两处是**演示用**，接真实数据后整体删除：

- `buildEdges(kols)` —— 按哈希给每个 KOL 分配一个主圈层（2–3 条连接）+ 一个次圈层，偶尔一条跨圈层边。
  **为什么这样生成**：早期版本把边均匀撒在全部 25 位目标人物上，结果每个 KOL 落在 4–5 个圈层里，圈层完全没有区分度，客户标记重点看不出任何变化。真实数据里也要保证**连接是集中的**——如果算出来每个人都连所有圈层，说明阈值太松，要提高 `verified` 与 `count` 门槛。
- `data/kols-x.json` 的 `_demo` 字段（`markets / languages / domains / audiences / formats / bio / risk / engagement`）—— 由数据库提供。
  `id / name / handle / url / platform / followers / type / quotes / tier / quoteDate / quoteStatus / commercial` 派生自客户真实报价导出，字段形状可直接对照。

演示数据规模：54 位创作者（AI 30 / 加密 13 / 金融 11，全部 X 平台）· 46 位目标人物 · 13 个圈层。

---

## 五、可直接搬到后端的前端逻辑（`data/v3.js`）

| 函数 | 作用 |
|---|---|
| `rankKols(kols, prefs, focus)` | 候选池 = 有连接的创作者；排序键 `focusScore` → `fit` → 连接数。**永不因 focus 过滤** |
| `band(fit)` | 四级：强连接 / 路径清晰 / 可选择性补充 / 下一步可拓展（同时决定色阶） |
| `reasonFor(m, prefs)` | 推荐理由，随客户选择重写。按动词价数分句：及物（回复/引用）→「X 回复或引用过他的内容」；`co_appear` →「与 X 同场出现过」；纯关注 →「X 都在关注他」 |
| `understandingText(prefs, focus, kept)` | 确认区的「Mango 这样理解你的需求」，按圈层口径 |
| `budgetOf(kept)` | 按实际保留的人 + 所选合作形式汇总。**未询价的人不能用估算值填补** |
| `sp(str)` / `nb(str)` | 中英排版：数字↔汉字用不断行空格（「8 位」不可拆），字母↔汉字用普通空格（保留断点）；`nb` 粘住人名内部 |

`fit` 权重：连接 40 / 内容方向 22 / 面向人群 18 / 市场语言 10 / 预算 10。重点圈层**不进分数**（避免与 `focusScore` 双重计算），只作为卡片上的「命中重点：…」标签。

---

## 六、严禁下发给客户端

联系方式原文 · 供应商底价与原始金额 · Mango 内部人员与介绍人 · 内部触达等级 · 谈判记录 · 任何未核验却被表述为已核实的关系。

价格只以档位 `$ / $$ / $$$ / $$$$` 呈现；预算汇总必须分两段返回，未知部分不得估算：
```json
{ "known": { "lo": 12000, "hi": 18000 }, "pendingCount": 3 }
```

---

## 七、用词约束（会直接出现在客户界面）

- 不用「缺口 / 待补充 / 无数据」→ 用「下一步可拓展」
- 不用「边界人选 / 匹配一般」→ 用「可选择性补充」
- 不用「审核 / 驳回 / 不合格」这类评判人的词
- 不用「AI 驱动 / 智能推荐」这类空词
- **不出现英文占位词**（曾有一版把第三档写成 `good fit`，与全中文界面不搭且不说明任何事）
- 演示数据必须显式标注，不得表述为已核实
- 任何地方都不承诺目标人物一定会看到、回复或转发

---

## 八、验收标准

- [ ] `GET /candidates` 同时返回 `priced` 与 `discovered`，比例约 2:1
- [ ] `discovered` 带 `discoveryPath`，能说清经由哪几位目标人物找到
- [ ] 标记 / 取消重点圈层时，候选**数量不变**，仅顺序变化，且前 5 名明显不同
- [ ] 单个圈层被标记时，命中率应在 25–40%（若超过 60%，说明连接过于分散，需收紧阈值）
- [ ] `focus` 最多 3 个；超出时挤掉最早的并告知客户
- [ ] 切换领域时，圈层重点、待核查目标、名单全部重置（圈层按领域分套，跨领域 id 会导致空结果）
- [ ] 预算返回两段，未知部分未被估算值填补
- [ ] 客户端响应里搜不到联系方式、底价、内部人名
- [ ] 无任何公开连接记录的候选不出现在名单里

---

## 九、包内文件

| 文件 | 说明 |
|---|---|
| `Mango Signal Map v3.dc.html` | 主原型：Hero → 01 偏好 → 02 目标圈层 → 03 名单 → 04 确认。渐进展开（选领域才出圈层，点「生成」才出名单） |
| `Design System.dc.html` | 视觉规范：色彩、字阶与句法、按钮/状态/档位、选择卡三态、信号带母题 |
| `data/v3.js` | 数据层 + 全部业务逻辑（唯一来源） |
| `data/kols-x.json` | 54 位演示候选 |
| `motion.js` | `<count-up>` / `<type-line>` / `<dot-grid>` 三个零依赖 web component |
| `gradient-waves.js` | 首屏波浪着色器（原生 WebGL2，不需要 ogl） |
| `PLAN-v3.md` | 单页改造计划与产品原则 |
| `PLAN-v3.2.md` | **不照搬 iLands 的八处判断** + 「这其实是发现+建联产品」的修正章节 |
| `AUDIT-v3.md` | 对照 iLands 的内容缺口审计 |
| `AUDIT-frontend.md` | 前端体验审计（客户视角 / 产品逻辑 / 高级感） |

---

## 十、后端之外仍待完成

1. Brief 码由后端签发与校验（现为前端演示校验）
2. `markets / languages / domains / audiences / formats / contentLine / avatarUrl` 接数据库，去掉演示标注
3. 头像：现走 `unavatar.io` 第三方代理，会间歇失败（已有首字母兜底），生产环境应自托管
4. 移动端（<768px）未做
5. 英文版：taxonomy、圈层文案、目标人物 `why` 均需 `_en`
6. 埋点：阶段进入 / 偏好变更 / 标记重点 / 保留移除 / 提交
