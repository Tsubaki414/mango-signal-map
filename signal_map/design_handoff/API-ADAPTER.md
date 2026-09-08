# Mango Signal Map · 前端与数据适配说明

面向后端 / Claude Code 接 API 时使用。页面只经 `data/adapter.js` 取数与计算；替换其中 `load*()` 的实现即可，UI 无需改动。

## 文件
| 文件 | 作用 |
|---|---|
| `Mango Signal Map v2.dc.html` | 主流程单文件应用（首页 → Brief/起点 → 偏好配置 → Mango 当前理解 → 揭示 → 推荐名单 → 阵容价值 → 确认） |
| `Design System.dc.html` | 视觉规范页（色彩、字阶、按钮、状态、选择卡、信号带母题） |
| `gradient-waves.js` | 首屏波浪着色器（零依赖原生 WebGL2；不在视口/后台自动停帧）。属性：`horizon/wave/crest/speed/amplitude/tilt/zoom/height/fog-depth/detail/brightness/opacity/blend/layer-opacity/parallax/mouse` |
| `data/adapter.js` | 数据适配层 + 全部业务逻辑 |
| `data/demo.js` | taxonomy、Root 库（按领域分套）、演示项目、演示 Signal |
| `data/demo-kols.json` | 由 `KOL完整报价名单_2026-09-07.xlsx` 派生的演示候选（`_demo` 字段为前端补充标注） |
| `PLAN.md` / `AUDIT.md` | 开发计划 / 设计与产品审计（含未执行项） |

## 需要的 API
| 用途 | 建议接口 | 返回 |
|---|---|---|
| 全库汇总（首页数字） | `GET /api/meta` | `{kols, platforms, domains, markets}` |
| 分类体系 | `GET /api/taxonomy` | `Taxonomy` |
| Root 库 | `GET /api/roots?domainGroup=` | `RootType[]` |
| Brief（预建项目） | `GET /api/projects/:token` | `Project`；token 无效返回 404 |
| 新访客建草稿 | `POST /api/projects` | `Project` |
| 候选池 | `GET /api/projects/:id/candidates` | `KOL[]` |
| 关系 Signal | `GET /api/projects/:id/signals` | `Signal[]`（无数据返回 `[]`，前端自动降级） |
| 保存进度 | `PUT /api/projects/:id/selection` | `{prefs, plans, plan, view, maxStage}` |
| 提交初步阵容 | `POST /api/projects/:id/submissions` | 回执 |

## 数据模型
```ts
Project { id; token; name; tagline; domainGroup:'ai'|'crypto'|'finance'; domains:string[]; stage;
          markets:string[]; goals:string[]; goal; customGoal?; budget?:'b1'..'b4';
          signalState:'full'|'partial'|'none'; lastNotes:string[]; contactName? }

KOL { id; name; handle; url; platform; followers; type;
      quotes:{format;formatCode;usd;raw}[];      // 客户端不显示 usd，仅用于分档
      tier:1|2|3|4;                              // 按单条主内容 USD：<500 / 500–2k / 2k–8k / >8k
      quoteDate; quoteStatus:'historical_unverified'|'confirmed';
      commercial:'contactable'|'route_pending';
      markets[]; languages[]; domains[]; audiences[]; formats[];   // 目前为演示标注，正式版由后端提供
      contentLine?;                              // 一句话说明内容做什么（有则优先于前端生成）
      avatarUrl?; bio; risk[]; engagement{medianViews;replyQuality} }

Signal { kolId; rootId; type:'follow'|'reply'|'quote'|'co_appear'|'media'; date; count; verified; evidence }

RootType { id; label; why; value; people:{id;name;handle;role;why;behavior}[] }

Selection { prefs{markets,languages,domains,audiences,rootTypes,platforms,formats,budget,tiers,brand,criteria},
            plans:{A:{selected,bench},B:{...}}, plan, view, maxStage }
```

## 客户端可见性边界
**不下发**：联系方式原文、供应商底价与原始金额、Mango 内部人名/介绍人、触达等级、谈判记录。
价格只以档位 `$ / $$ / $$$ / $$$$` 显示；预算汇总为档位隐藏区间之和，并始终标注"非报价，以 Mango 复核为准"。

## adapter.js 关键函数（可整体搬到后端）
- `scoreKol(k, prefs, roots, signals)` → 适配度 `fit`（整数）+ 6 条可解释分项。权重：内容领域 30 / 目标人群 20 / 市场语言 15 / 平台形式 15 / Root Signal 10 / 预算 10。无 Signal 数据时 Root 项标 `pending`，不计入优势与限制；`seen`/`engaged` 双轴仅在有 Signal 时计算。
- `fitBand(fit)` → 四级色阶（≥78 高度符合 / ≥65 符合 / ≥50 部分符合 / 其余 边界人选），名单用深浅表达，数字为辅。
- `matchKols` → 排序 + `eligible`；读取 `prefs.criteria` 中 **已认可** 的判别标准（want/avoid）参与加减分。演示期为关键词匹配，正式版应由后端把 criteria 编译为规则 id。
- `suggestLineup(matches, prefs, project)` → 「Mango 建议起点」：按目的与预算生成策略（广撒网 → 多人中低档；精准 → 少人高档 + 圈层桥梁），并返回一句中文说明。
- `groupMatches` → 动态分组；`lineupSummary` / `diffSummary` → 阵容汇总与每次增删的后果说明。
- `describeKol` → 一句话内容说明（`contentLine` 优先）。

## 关系数据三态
`signalState = 'full' | 'partial' | 'none'`（项目配置；Tweaks 面板可覆盖为 `project/full/partial/none`）：
- **full**：注意力路径图 + 圈层覆盖矩阵 + 具名 Root 卡
- **partial**：只显示已核验 Signal，文案提示其余在补充
- **none**：隐藏路径图，覆盖矩阵降级为「目标人群 × KOL」，Root 卡显示库代表人物并标注待补充

## 前端状态
`localStorage['mango-signal-map:<projectId>']` = `{project, prefs, plans, plan, view, maxStage, revealed, confirmedAt}`。
接 API 后改为 `PUT /selection` 同步；`listSaved()` 用于列出本机已存项目。

## 交付前仍需后端配合
1. Brief 码由后端签发与校验（现为前端校验演示）。
2. 判别标准编译为规则 id（见上）。
3. `markets / languages / domains / audiences / formats / contentLine / avatarUrl` 由数据库提供，替换演示标注。
4. 英文版：taxonomy 与 Root 库需带 `label_en`。
5. 移动端布局（<768px）与埋点事件（阶段进入、偏好变更、保留/移除、判别认可、提交）。
