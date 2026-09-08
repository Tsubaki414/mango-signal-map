# Handoff: Mango Signal Map（KOL 阵容配置器）

## Overview
Mango Labs 发给客户（项目方）的一条链接。客户在其中表达"想被谁看见"的偏好，从 Mango 已有报价的创作者库中生成一份可解释的传播阵容，确认后交回 Mango 复核报价与档期。
一条链接服务所有项目方：项目差异全部来自配置（`domainGroup` 决定 Root 库与文案），不来自代码分支。

## About the Design Files
本包内的 HTML 文件是**设计参考**（用 HTML 写的高保真原型），不是要直接上线的生产代码。任务是**在目标代码库既有环境中复刻这些设计**（React / Vue / Next 等），沿用其组件、路由与状态方案；如果还没有前端工程，建议 **Next.js + TypeScript**（App Router）+ 无 CSS 框架（本设计只用内联样式与 1px 网格，Tailwind 反而会引入不需要的圆角/阴影默认值）。

`.dc.html` 是原型运行格式：一个模板（HTML + `{{ }}` 插值 + `<sc-for>` / `<sc-if>`）加一个逻辑类（`renderVals()` 返回模板输入）。复刻时把模板当 JSX 结构、把 `renderVals()` 当 `useMemo` 派生值即可，**不要**尝试移植这套运行时。

## Fidelity
**高保真（hifi）**。颜色、字号、字重、间距、状态色、动效时长均为最终值，请按本文件与 `Design System.dc.html` 像素级复刻。唯一例外：候选人数据是演示数据（见 Assets）。

## 目标环境建议
```
Next.js 14+ (App Router) + TypeScript
状态：Zustand 或 useReducer（单一 store，见 State Management）
样式：CSS Modules 或 vanilla-extract；禁止引入带默认圆角/阴影的 UI 库
动效：本包 motion.js / gradient-waves.js 两个原生 web component 可直接复用（零依赖），
      也可改写为 React 组件（reactbits.dev 的 CountUp / TextType / DotGrid / Waves 对应实现）
数据：Server Component 取数 + 客户端 store 缓存；所有取数经一层 adapter（见 API 契约）
```

---

## Design Tokens

### 颜色（全部实色，禁止渐变按钮、光晕、紫色）
| 用途 | 值 |
|---|---|
| 顶栏 / 转场黑幕 | `#0E0E0E` |
| 主场背景（深青） | `#1F4144` |
| 面板 / 卡片（更深） | `#152F31` |
| Root 卡选中态背景 | `#1B3A3C` |
| 骨白阅读区背景 | `#F3F1EA` |
| 骨白页面板 | `#FAF9F5` |
| 骨白页更深面板 | `#E3E0D6` |
| 强调色（暗底） | `#5FC4BB` |
| 强调色（骨白页） | `#0E6E6A` |
| 警示 / 待复核 | `#C9A227`（暗底）· `#8A6D00`（骨白页文字） |
| 正文（暗底） | `#F3F1EA` · 次级 `rgba(243,241,234,.75)` · 三级 `rgba(243,241,234,.55)` |
| 正文（骨白页） | `#141414` · 次级 `#4A4A46` · 三级 `#6B6B66` |
| Hairline（暗底） | `rgba(243,241,234,.14)`；交互描边 `rgba(243,241,234,.25~.5)` |
| Hairline（骨白页） | `#D9D6CC`；强分隔 `#141414` |
| 适配度四级色阶（骨白页） | L4 `#0E6E6A` · L3 `rgba(14,110,106,.72)` · L2 `rgba(14,110,106,.42)` · L1 `rgba(14,110,106,.2)` |
| 首页渐变（仅此一处） | `linear-gradient(180deg,#2C5A5D 0%,#1F4144 55%,#14302F 100%)` |

### 字体
```
正文/标题：Inter + "Noto Sans SC"，权重 300 为默认，500 为小标题，700 仅用于斜体引子
UI 标签/数字：JetBrains Mono，权重 400/500，字距 .06–.16em，一律大写
Google Fonts：Inter:ital,wght@0,300;0,400;0,500;1,700 · Noto+Sans+SC:wght@300;400;500;700 · JetBrains+Mono:wght@400;500
```

### 字阶（1440 基准）
| 角色 | 规格 | 用途 |
|---|---|---|
| Display | `clamp(44px,5.4vw,80px)` / 1.08 / -.02em / 300 | 首页大标题 |
| H1 | `clamp(32px,3.6vw,54px)` / 1.1 / -.02em / 300 | 各阶段标题 |
| H2 | 22–26px / 1.25 / 400–500 | 分节标题 |
| H3 | 18px / 1.2 / 500 | 卡片标题 |
| Body | 15px / 1.7 / 300 | 说明文 |
| Small | 12–13px / 1.6 | 标签、注释 |
| Mono-XL | 64px（脉搏）· 48px（我的名单）· 30px（阵容六格）· 24px（卡片适配度） | 关键数字 |
| Mono-S | 10–12px / 字距 .1em / 大写 | 编号、eyebrow、状态 |

**标题句法**（品牌特征，必须保留）：`<em style="font-weight:700;font-style:italic">项目名，</em>` + 轻字重（300）陈述句。例：*iLands，*这是我们目前对你项目的理解。

### 间距与形
- 页面内边距：`56px clamp(24px,4vw,64px) 140px`；分节间距 56–88px；组内 12–24px
- **直角**：`border-radius: 0`，唯一例外是头像圆形（`border-radius:50%`）
- **无阴影**：选中态用 `box-shadow: inset 0 0 0 1px <accent>` 表示描边，不做投影
- 所有"表格感"用 `display:grid; gap:1px; background:<线色>` + 子元素填背景色实现 1px 网格
- 编号分隔符：`01 ///`（三斜杠，站点特有）
- 选中标记：卡片左上角 `8×8px` 实心方块（`position:absolute;left:0;top:0`）

### 动效
| 名称 | 规格 |
|---|---|
| `msm-in` | `opacity 0→1, translateY(10px)→0`，.25–.7s，用于内容进场 |
| `msm-line` | `stroke-dashoffset 2400→0`，2.4s `cubic-bezier(.2,.8,.2,1)`，首页圆环自描 |
| `msm-band` | `opacity .35↔1`，1.6s infinite，脉搏指示点与加载信号带 |
| 阶段转场（wipe） | 全屏 `#0E0E0E`，`opacity 0→1→1→0` 580ms `cubic-bezier(.2,.8,.2,1)`，`z-index:80`，中央一行 `02 /// 表达偏好`（label 另有 `translateY(6px)→0`） |
| Count Up | 420ms，`easeOutCubic`，逐 token tween（支持 `$14k – $45k` 这类含多个数字的字符串） |
| Text Type | 18ms/字，行间隔 1150ms |
| Dot Grid | gap 24px，dot r=1px，`rgba(243,241,234,.1)`，鼠标 190px 内向外推 4px 并放大 1.9× |
| 所有动效 | 必须实现 `prefers-reduced-motion: reduce` 的降级（wipe 直接不显示，其余立即到终态） |
| 焦点态 | `outline:2px solid #5FC4BB; outline-offset:2px`（`:focus-visible`） |

---

## 动效来源与位置（reactbits.dev 对应实现）
设计中的动效参考 [reactbits.dev](https://reactbits.dev)，但**本包内已改写为零依赖原生实现**（`motion.js` / `gradient-waves.js`），可直接复用；若目标工程偏好 React 组件，按下表换成 reactbits 对应组件即可，参数照抄本表。

| reactbits 组件 | 本包实现 | 用在哪里 | 参数 |
|---|---|---|---|
| **Waves / Gradient background** | `gradient-waves.js`（原生 WebGL2，等价于 ogl 版本，**不需要装 ogl**） | 仅首页 hero，叠在 SVG 圆环与信号带之下 | `horizon #14302F` `wave #24605C` `crest #7FD8CE` `speed .22` `amplitude 2.2` `tilt 1.18` `zoom 1.05` `height 6` `fog-depth 26` `opacity .9` `brightness 1.15` `grain-intensity .04` `parallax .6` `detail medium` `blend screen` `layer-opacity .72` |
| **Count Up** | `<count-up value>`（`motion.js`） | ① 脉搏候选数（Mono 64px）② 脉搏预算区间 ③ 我的名单人数（48px）与预算区间 ④ 名单卡片适配度（24px）⑤ 阵容价值六格数字（人/预算/平台/可对接/待复核/Signal） | 420ms，`easeOutCubic`；**逐 token tween**，支持 `$14k – $45k` 这类含多个数字的字符串同步滚动 |
| **Text Type** | `<type-line text active speed>`（`motion.js`） | 仅生成揭示页四行陈述 | 18ms/字，行间隔 1150ms，行未 active 时不渲染文字 |
| **Dot Grid** | `<dot-grid gap dot shift color>`（`motion.js`） | 仅阵容价值 `4.1` 注意力路径图背景 | `gap 24` `dot 1` `shift 4` `color rgba(243,241,234,.1)`；鼠标 190px 内的点向外推 4px 并放大 1.9×，缓动系数 .06 |
| **Pixel Transition / Fade** | `playWipe()` + CSS `msm-wipe`（在主原型内） | 阶段切换：Brief→配置、配置→当前理解、当前理解→揭示、揭示→名单、名单→阵容价值 | 580ms 全屏 `#0E0E0E`，`z-index:80`，`cubic-bezier(.2,.8,.2,1)`，中央一行 `02 /// 表达偏好`；`home` 与首次进入不放 |

**明确不要引入的效果**（会让专业服务工具看起来像 crypto 落地页）：Aurora、Orb、Liquid Chrome、Particles、Splash Cursor、卡片 3D 倾斜、任何渐变按钮或光晕。
除上表五处外，全站只允许三类动效：`msm-in` 内容进场、hairline / stroke 描边划入、`msm-band` 呼吸。

三个 web component 均自带：`prefers-reduced-motion` 降级、`IntersectionObserver` 离屏停帧、`visibilitychange` 后台停帧、`ResizeObserver` 自适应、`disconnectedCallback` 清理。复刻为 React 组件时**必须保留这四项**。

## Screens / Views

顶栏（所有页面固定）：高 56px，背景 `#0E0E0E`，底部 1px `rgba(243,241,234,.12)`；左侧 logo（`assets/mango-mark.png` 高 26px）+ `MangoLabs`（20px/400）+ 竖线 + `SIGNAL MAP`（Mono 12px/.12em）；右侧四段进度 `01 起点 / 02 偏好 / 03 名单 / 04 阵容`（当前段骨白，已完成段带 ✓，未达段 `rgba(243,241,234,.45)`）；最右自动保存提示。

### 1. 首页 `home`
- 布局：`min-height: max(calc(100vh - 56px), 720px)`，首页渐变背景 + 三层图形（由后至前）：`<gradient-waves>` 波浪（`mix-blend-mode:screen`，`opacity .72`，`pointer-events:none`）；SVG 发光圆环（cx 980 cy 640 r 300，双描边，外层 `feGaussianBlur stdDeviation=6`）+ 近黑地平线块 `#0F2526`（y=690）+ 五条阶梯信号带
- 内容：eyebrow `01 /// KOL 阵容配置器` → Display 两行标题 → 52ch 说明段 → 斜杠标签流（Mono 大写）→ 两个 CTA
- CTA：`MANGO 已为我准备了 BRIEF →`（52px 高，`border:1px solid rgba(243,241,234,.5)`，`background:rgba(0,0,0,.28)`，hover 边框转骨白+底色 `rgba(243,241,234,.08)`）/ `第一次接触 MANGO`（次级，边框 .3 透明度）
- Brief 码输入：**点击第一个 CTA 才展开**（再点收起，切页自动收起）；输入框 52px 高、无右边框，紧贴 `打开 BRIEF` 按钮（骨白底黑字）；占位符 `输入 Mango 顾问发给你的 Brief 码 · 演示输入 demo`；无效码显示 `#C9A227` 提示
- 下方：三段式说明（01 你表达偏好 / 02 名单随你成形 / 03 Mango 复核并执行）+ 四格数字网格（有报价创作者 / 平台 / 领域 Root 库 / 市场）+ DEMO 脚注

### 2. 你的 Brief `understand`（骨白）
- 布局：`grid-template-columns: minmax(0,1fr) minmax(320px,.7fr)`，左栏右边框 `#D9D6CC`
- 左：eyebrow `01 /// 你的 BRIEF` → 标题（斜体项目名 + "这是我们目前对你项目的理解。"）→ 字段表（`160px 1fr 64px` 三列，每行下边框 `#D9D6CC`）：项目 / 一句话定位 / 领域 / 阶段 / 主要市场，每行右侧 `修改` 切换为行内 input（下边框 `#0E6E6A`，Enter 或失焦保存）→ 上次沟通要点（项目符号列表）
- 右：`本次要做什么 · 可多选`（1px 网格按钮列，选中 `#141414` 底骨白字 + `■`，未选 `#FAF9F5` + `□`）→ `确认，继续配置阵容 →`（56px，`#141414` 底，hover `#0E6E6A`）+ `这是一个新 CAMPAIGN`
- **关键产品判断**：Mango 已对接过客户，不再问"你属于什么赛道"，领域/市场/预算全部预填、可改不必审。

### 3. 新访客起点 `start`（4 问，仅无 Brief 码时）
`grid-template-columns: minmax(0,1fr) 320px`；一屏一问（领域 → 市场 → 项目名与一句话 → 目的与预算），右栏实时显示项目草稿 + 阶梯信号带进度；每问可跳过；`下一步 →` / `上一步 ←`。

### 4. 偏好配置 `configure`
- 布局：`minmax(0,1fr) minmax(280px,clamp(280px,26vw,380px))`，右栏 sticky（`top:56px; height:calc(100vh - 56px)`）
- 左栏六组，均为 `grid-template-columns: 200px minmax(0,1fr)`、上边框 hairline、左列为编号+标题+说明：
  - `2.1` 领域细分与市场——默认折叠（按钮右侧 `+`/`−`），标注"沿用 BRIEF"
  - `2.2` 想影响的人群——可多选 + 自定义输入
  - `2.3` 想被哪种 Root 看到——卡片网格 `repeat(auto-fill,minmax(270px,1fr))`，每卡：编号+价值 / 类型名 / 为什么重要 / 代表人物 chip（点开弹窗）/ 底部三态按钮（最想进入 / 也很重要 / 不优先）
  - `2.4` 平台与内容形式——无报价平台显示"待接入"且 `disabled`、`opacity:.35`
  - `2.5` 预算与创作者量级——四档预算 1px 网格 + `$ $$ $$$ $$$$` 档位偏好
  - `2.6` 内容与品牌偏好
- 右栏"实时脉搏"：`<count-up>` 候选数（Mono 64px）+ 变化说明（`#5FC4BB`，如"新增 7 位候选"）+ 平台分布条 + 档位构成条（`flex` 按数量分配，`transition:flex .3s`）+ 预算区间（count-up）+ 目标圈层 chips + 当前最匹配前 5 + 底部 `下一步 · 确认 MANGO 的理解`
- **每次选择必须 300ms 内更新脉搏并给出中文变化说明**，同时自动保存。

### 5. Mango 当前理解 `criteria`
把偏好翻译成"更想要 / 不想要 / 待你确认"的判别标准清单，客户逐条 `认可 / 移除 / 改写`；显示"认可后如何影响排序"的说明；底部 `生成我的名单 →` / `全部认可` / `返回调整`。

### 6. 生成揭示 `reveal`
全屏渐变 + 底部阶梯信号带逐条点亮；四行 `<type-line>` 逐字打字（理解 → 从 N 位中筛出 M 位 → 角色构成 → 预算与对接状态），行间隔 1150ms；四行完成后出现 `查看名单 →`；点击任意处跳过。

### 7. 推荐名单 `lineup`（骨白）
- 布局同配置页两栏
- 顶部：理解陈述句 + `返回调整`；`Mango 建议起点`条（预选 6–8 人，说明策略：广撒网→多人中低档 / 精准→少人高档 + 圈层桥梁）
- 筛选行：搜索 / 平台 / 档位 / 分组 tab + 适配度色阶图例
- 卡片网格 `repeat(auto-fill,minmax(340px,1fr))`：左侧 4px 色阶条（`fitColor`）+ 左上选中方块；头像 48px 方块（`#141414`）；姓名 16px/500；Mono handle；右侧适配度（8px 色块 + Mono 24px `#0E6E6A` count-up + 等级词）；角色/平台/语言/市场 chip 行；一句话内容说明；档位 / 报价状态 / Signal 三格 1px 网格；底部四按钮 1px 网格（选择 / 换一个 / 备选 / 对比）
- 右栏"我的名单"：方案 A/B tab + 人数（48px count-up）+ 预算区间（count-up）+ **刚刚的变化**（黑底 `#141414` 骨白字，说明加入或移除的后果 + 替代建议）+ 角色 chips + 覆盖行 + 缺口提示（`#C9A227` 边框）+ 备选池 + `查看阵容价值 →`

### 8. 阵容价值 `overview`（深青）
- `4.0` 头部：陈述句 + Signal 陈述 + 六格数字（人 / 预算区间 / 平台 / 可对接 / 待复核 / Signal，全部 count-up）
- `4.1` 注意力路径图：四列（你的项目 14% → 已选 KOL 30% → 目标 Root 30% → 目标圈层 26%），HTML 节点（38px 高按钮，绝对定位）+ SVG 连线（`vector-effect:non-scaling-stroke`；实线=已核验 `#5FC4BB`，虚线 `4 5`=待核验）；背后 `<dot-grid>`；点击节点高亮其路径（其余 `opacity:.3`），点击连线看证据；右侧检视面板
- `4.2` 圈层覆盖矩阵：行=所选圈层，列=已选 KOL，格=Signal 数（多人覆盖 `#5FC4BB` / 单点 `#C9A227` / 缺口空白）
- `4.3` 可能进入谁的信息流：具名 Root 卡（头像/姓名/身份/为什么重要/经由谁）
- `4.4` 变化记录：本次会话所有增删换的时间线
- `4.5` 确认区（骨白）：初步名单表 + 仍需 Mango 核验 + Mango 接下来会做（四步）+ `确认初步阵容 →` + 次级（返回调整 / 请 Mango 再优化 / 保存稍后继续 / 导出）；确认后显示黑底回执卡片，文案必须明确"确认不是购买"

### 9. 覆盖层
- **KOL 详情抽屉**：右侧 `min(600px,100vw)`，骨白，左边框 `1px #141414`，`msm-in` 进场；顺序固定：身份 → 一句话 → **为什么是 86**（6 条分项横条，色阶按匹配度）→ 被看到/被互动双轴 → 报价对应内容形式（只显档位）→ 报价状态/商务路径/记录时间 → 关系 Signal 列表（含证据与已核验/待核验）→ Mango 判断 → 相似替代（一键换）→ 底部固定操作
- **Root 人物弹窗**：`min(520px,100%)`，`#152F31`，四行（圈层类型 / 为什么重要 / 行为习惯 / 免责声明）
- **对比弹窗**：最多 4 人，`140px repeat(n,1fr)` 1px 网格，10 行属性

---

## Interactions & Behavior
1. 每次偏好变更 → 重算 `matchKols` → 更新脉搏 + 中文变化说明 + 自动保存（1.6s 后提示淡出）
2. 名单增删 → `diffSummary` 生成 1–5 条后果（预算上限、平台得失、圈层唯一路径丢失、角色空缺、Signal 变化）+ 移除时给替代建议
3. 阶段切换 → 580ms 近黑 wipe（`home` 与 `reveal` 进入时不放 wipe 由 `wipeFor()` 决定）+ `window.scrollTo(0,0)` + `maxStage` 递增
4. 无效 Brief 码 → 输入框下方 `#C9A227` 提示，不跳页
5. 打印/导出：`@media print` 隐藏顶栏、侧栏、`[data-print-hide]`，阵容价值页强制黑白
6. 响应式：1280 / 1440 / 1920 已验证；`<768px` **未实现**，需按 AUDIT D3 补（单列、脉搏收为底部条、路径图改纵向列表）

## State Management
单一 store，键与原型一致（复刻时保持字段名以便对接后端）：
```ts
{ view, maxStage, project, prefs, plans:{A:{selected[],bench[]},B:{…}}, plan:'A'|'B',
  compare[], openKol, openRoot, compareOpen, filter{q,platform,tier,group},
  criteria[], history[], revealStep, revealed, confirmedAt, wipe{on,n,label}, pulseNote, saveNote }
```
- 持久化：`localStorage['mango-signal-map:<projectId>']`，字段 `{project,prefs,plans,plan,view,maxStage,revealed,confirmedAt}`；接后端后改为 `PUT /selection`
- 派生值（不要存）：`matches` / `groups` / `summary` / `graph` / `matrix` / `suggestion`，全部由 `prefs + plans` 计算，见 `data/adapter.js`

## 业务逻辑（可整体搬到后端）
`data/adapter.js` 是**唯一**业务逻辑来源，函数与权重：
- `scoreKol` → 适配度整数 + 6 条可解释分项。权重：内容领域 30 / 目标人群 20 / 市场语言 15 / 平台形式 15 / Root Signal 10 / 预算 10。无 Signal 数据时 Root 项 `pending`，不计入优势与限制
- `fitBand` → 四级（≥78 高度符合 / ≥65 符合 / ≥50 部分符合 / 其余 边界人选）
- `matchKols` → 排序 + `eligible`，读取**已认可**的判别标准加减分（演示为关键词匹配，**正式版必须由后端编译为规则 id**）
- `suggestLineup` → 按目的与预算生成建议阵容 + 中文说明
- `groupMatches` / `lineupSummary` / `diffSummary` / `describeKol`
- 详见 `API-ADAPTER.md`（9 个接口 + 数据模型 + 客户端可见性边界）

## 不可妥协的产品规则
1. **客户端绝不下发**：联系方式原文、供应商底价与原始金额、Mango 内部人名/介绍人、触达等级、谈判记录
2. 价格**只显示档位** `$ / $$ / $$$ / $$$$`；预算汇总为档位隐藏区间之和，且每处都必须标注"非报价，以 Mango 复核为准"
3. **无真实关系数据不画关系**：`signalState` 三态（full / partial / none）自然降级——none 时隐藏路径图、矩阵降级为"目标人群 × KOL"、Root 卡显示库代表人物并标注待补充。**禁止为视觉效果生成虚假关系**
4. 不确定的信息用独立状态标签（已确认 / 待复核 / 待补充），**不混进分数**
5. 每个数字都能展开成一句话解释
6. 一条链接服务所有项目方；项目差异只来自配置

## Assets
- `assets/mango-mark.png`（骨白）/ `assets/mango-mark-dark.png`（近黑）— 由客户提供的 logo 抠图去底而来；**生产环境请换成官方 SVG**
- `data/demo-kols.json` — 48 位演示候选，由客户导出的 `KOL完整报价名单_2026-09-07.xlsx` 派生。`id/name/handle/url/platform/followers/type/quotes/tier/quoteDate/quoteStatus/commercial` 为真实字段；`_demo` 下的 `markets/languages/domains/audiences/formats/bio/risk/engagement` 为**演示标注**，接 API 后由数据库提供
- 头像目前是首字母方块，后端提供 `avatarUrl` 后替换
- 无图标字体、无图片资源；所有图形为内联 SVG 或 canvas

## Files
| 文件 | 说明 |
|---|---|
| `Mango Signal Map v2.dc.html` | 主原型，全部 8 个视图 + 3 个覆盖层（969 行） |
| `Design System.dc.html` | 视觉规范页：色板、字阶与句法、按钮/状态/档位、选择卡三态、信号带母题、数字网格 |
| `motion.js` | `<count-up>` / `<type-line>` / `<dot-grid>` 三个零依赖 web component，可直接复用 |
| `gradient-waves.js` | 首页波浪着色器（原生 WebGL2，无需 ogl）。属性见文件头注释 |
| `data/adapter.js` | 数据适配层 + 全部业务逻辑 |
| `data/demo.js` | taxonomy、Root 库（按领域分套）、演示项目、演示 Signal |
| `data/demo-kols.json` | 演示候选数据 |
| `API-ADAPTER.md` | 接口契约、数据模型、可见性边界、后端待办 |
| `PLAN.md` | 产品原则、信息架构、五期计划（背景阅读） |
| `AUDIT.md` | 设计与产品审计，含**未执行**的改进项（P0/P1/P2） |
| `screenshots/` | 14 张 1440 宽实拍（见下表），复刻时逐屏对照 |
| `assets/` | logo 骨白版与近黑版 |

### screenshots 对照表
| 文件 | 对应视图 | 重点看 |
|---|---|---|
| `01-home-hero.png` | home | 三层图形叠加、Display 标题句法、两个 CTA |
| `02-home-brief-entry.png` | home | Brief 码输入展开态（贴合的输入框 + 骨白按钮） |
| `03-brief-understand.png` | understand | 字段表行内编辑、右栏多选目的（■/□） |
| `04-configure-top.png` | configure | 「沿用 BRIEF」折叠区、2.1 展开态 |
| `05-configure-roots-pulse.png` | configure | Root 卡三态、右栏实时脉搏全貌 |
| `06-criteria.png` | criteria | 判别标准三列、已移除条目的删除线样式、右上三格计数 |
| `07-reveal-typing.png` | reveal | 四行打字完成态、底部阶梯信号带、跳过提示 |
| `08-lineup-suggestion.png` | lineup | 「Mango 建议起点」条与策略说明 |
| `09-lineup-cards.png` | lineup | 卡片左侧色阶条、适配度与等级词、四按钮网格、适配度色阶图例 |
| `10-lineup-my-list.png` | lineup | 右栏「刚刚的变化」黑底卡、覆盖与缺口 |
| `11-overview-path-graph.png` | overview 4.1 | 四列路径图、实线/虚线、点阵背景、检视面板 |
| `12-overview-matrix-roots.png` | overview 4.2–4.3 | 覆盖矩阵配色（多人/单点/缺口）、具名 Root 卡 |
| `13-overview-history.png` | overview 4.4 | 变化记录时间线 |
| `14-confirm.png` | overview 4.5 | 骨白确认区、待核验清单、Mango 下一步、主 CTA |

## 交付后仍需完成（按优先级）
1. Brief 码由后端签发与校验（现为前端演示校验）
2. 判别标准 → 排序规则 id 的编译（现为关键词匹配）
3. `markets/languages/domains/audiences/formats/contentLine/avatarUrl` 接数据库，去掉演示标注
4. 移动端（<768px）
5. 英文版：taxonomy 与 Root 库需 `label_en`
6. 埋点：阶段进入、偏好变更、保留/移除、判别认可、提交
7. `AUDIT.md` 中未执行的 P1/P2（A4 预算刻度、A6 A/B 并排、A7 请 Mango 判断、B1 分布锚点、C3 真实头像等）

## 复刻验收清单
- [ ] 顶栏 56px `#0E0E0E`，四段进度含 ✓ 状态
- [ ] 首页三层图形叠加正确，波浪 `screen` 混合 72%，圆环 2.4s 自描
- [ ] Brief 码输入点击才展开，`demo` 可进入，错码有提示不跳页
- [ ] Brief 页五个字段可行内编辑并保存；目的可多选
- [ ] 配置页任一选择 300ms 内更新脉搏数字（带滚动）并给出中文说明
- [ ] Root 卡三态样式与代表人物弹窗
- [ ] 揭示页四行逐字打字，间隔 1150ms，可跳过
- [ ] 名单卡片色阶条 + 适配度 count-up + 四个操作
- [ ] 移除任一人时"刚刚的变化"给出后果 + 替代建议
- [ ] 路径图点击高亮、虚线=待核验、背后点阵随鼠标位移
- [ ] `signalState` 切到 `none` 时三处降级且无空白区
- [ ] 阶段切换有 580ms 近黑 wipe 与正确编号标签
- [ ] `prefers-reduced-motion` 下所有动效降级
- [ ] 焦点环 `#5FC4BB`，正文对比度 ≥ 4.5:1
- [ ] 刷新后回到同一阶段与同一名单
