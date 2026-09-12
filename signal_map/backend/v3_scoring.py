"""适配度、分项与推荐理由。

排序主依据是**连接**，不是综合分
------------------------------
``fit`` 只是一个可展开的解释，真正的排序键是「重点圈层的连接权重」
（``focus_score``）。把所有维度揉成一个综合分再排，会稀释「这个人能把内容送进
你要影响的人的视野」这个唯一清晰的信号 —— 见 v3 README 第一节第 3 条。

所以这里返回的是 ``fit`` **和** ``parts``：分数永远和它的五条分项一起出现，
没有任何界面只显示一个孤零零的数字。

推荐理由是模板填空，不是模型生成
--------------------------------
``reason()`` 按动词价数分句：及物动词（回复／引用）→「X 回复或引用过他的内容」；
``co_appear`` →「与 X 同场出现过」；纯关注 →「X 都在关注他」。语法结构不同，
不能共用一个模板 —— 这是中文表达问题，不是措辞偏好。

权重与阈值抄自 ``design_handoff_v3/data/v3.js``，改动等于改产品。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 边的类型权重。互动比关注重，因为它证明对方**读过并回应**，而不只是订阅了。
EDGE_WEIGHTS = {"cofollow": 1.0, "reply": 1.6, "quote": 1.5, "co_appear": 1.3}

EDGE_LABELS = {"cofollow": "关注", "reply": "回复", "quote": "引用", "co_appear": "同场出现"}

#: 未核验的边打六折。当前全库 human_confirmed 为 0，所以实际上所有边都在打折 ——
#: 这是应该的：分数要如实反映「这些关系还没有人确认过」。
UNVERIFIED_FACTOR = 0.6

#: 被标为本轮重点的圈层，连接权重放大。只影响排序，**不过滤任何人**。
FOCUS_BOOST = 2.2

#: 关系强度三级 → 客户端口径。
#:
#: ``AUDIT-v3`` 要求显示 H0–H3 分级，而 v3 README 第三节明确禁止输出这类内部
#: 代号。这里实现**分级本身**，用 README 定义的客户端说法，不输出代号。
#:
#: 对应关系（README 第三节）：
#:   strong  多次回复／引用／同场，且主题相关
#:   medium  单次互动，或双向关注
#:   weak    仅单向关注
#: 另加 none：连一条公开记录都没有 —— 它不是"弱"，是"没有"，两者不能混。
STRENGTH_LABELS = {
    "strong": "多次互动 · 主题相关",
    "medium": "有过互动",
    "weak": "仅单向关注",
    "none": "暂无公开记录",
}

#: 每一级对应的处置口径。分级如果不落到"所以该怎么做"，就只是个装饰标签。
STRENGTH_ADVICE = {
    "strong": "可优先深挖，仍需身份与商业复核",
    "medium": "适合做二跳桥接",
    "weak": "只作补充证据，不单独推荐",
    "none": "不作为关系证据",
}


#: 四级带。词面照抄设计包：不用「缺口／匹配一般」这类评判人的说法。
BANDS = ((78, "强连接", 4), (64, "路径清晰", 3), (50, "可选择性补充", 2), (0, "下一步可拓展", 1))


@dataclass
class Part:
    id: str
    label: str
    weight: int
    value: float
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "weight": self.weight,
            "pct": round(self.value * 100),
            "note": self.note,
        }


@dataclass
class Scored:
    fit: int
    band_word: str
    band_level: int
    strength: str = "none"
    strength_label: str = ""
    strength_advice: str = ""
    parts: list[Part] = field(default_factory=list)
    focus_score: float = 0.0
    focus_note: str = ""
    circles: list[str] = field(default_factory=list)
    reason: str = ""
    overlap_text: str = ""


def band_of(fit: int) -> tuple[str, int]:
    for floor, word, level in BANDS:
        if fit >= floor:
            return word, level
    return BANDS[-1][1], BANDS[-1][2]


def _name_list(names: list[str], total: int) -> str:
    """最多点名两位，其余折成「等 N 位」。列一长串人名读起来像凑数。"""
    shown = [n for n in names[:2] if n]
    if not shown:
        return ""
    if total > len(shown):
        return f"{'、'.join(shown)} 等 {total} 位"
    return "、".join(shown)


def score(
    *,
    edges: list[dict[str, Any]],
    circle_of_target: dict[str, str],
    target_names: dict[str, str],
    domains: list[str],
    audiences: list[str],
    markets: list[str],
    tier: int | None,
    prefs: dict[str, list[str]],
    focus: set[str],
    labels: dict[str, dict[str, str]],
    collected_per_circle: dict[str, int] | None = None,
) -> Scored:
    collected_per_circle = collected_per_circle or {}
    def lbl(kind: str, key: str) -> str:
        return labels.get(kind, {}).get(key, key)

    def weight(e: dict[str, Any]) -> float:
        base = EDGE_WEIGHTS.get(e.get("type", "cofollow"), 1.0)
        verified = 1.0 if e.get("verified") else UNVERIFIED_FACTOR
        boost = FOCUS_BOOST if circle_of_target.get(e.get("targetId", "")) in focus else 1.0
        return base * verified * boost

    reach = sum(weight(e) for e in edges)
    # 除以 6 再封顶：六条中等强度的连接就足以拿满这一项。再多不是更强，
    # 是这个人连所有圈层 —— 那说明阈值松了，不是他更合适。
    reach_n = min(1.0, reach / 6)

    want_d, want_a, want_m = prefs.get("domains", []), prefs.get("audiences", []), prefs.get("markets", [])

    # 「客户没提要求」和「我们不知道这个人写什么」必须分开。
    #
    # 这两种情况一度都给 0.55，于是 MrBeast（简介只有 "I want to make the world
    # a better place"，方向和人群全空）靠 11 条关注边把连接项打满，算出 83 分
    # 「强连接」—— 缺数据在加分，而规则本该是缺数据降低置信度。
    #
    # 0.2 不是惩罚，是「这一项没有证据支持」的诚实取值：它仍然进池子、仍然
    # 可被保留，只是不该和有明确方向的人拿同一个分数。
    UNKNOWN = 0.2
    if not domains:
        dom = UNKNOWN
    elif want_d:
        dom = len([d for d in domains if d in want_d]) / len(want_d)
    else:
        dom = 0.55
    if not audiences:
        aud = UNKNOWN
    elif want_a:
        aud = len([a for a in audiences if a in want_a]) / len(want_a)
    else:
        aud = 0.55
    if want_m:
        mk = 1.0 if (set(markets) & set(want_m) or "global" in markets) else 0.15
    else:
        mk = 0.6
    bud = 0.6 if tier is None else (1.0 if tier <= 2 else 0.7 if tier == 3 else 0.4)

    parts = [
        Part("reach", "进入目标圈层的机会", 40, reach_n,
             f"与 {len(edges)} 位目标人物有公开连接" if edges else "暂无公开连接记录"),
        Part("domain", "内容方向契合", 22, min(1.0, dom * 1.35),
             "、".join(lbl("domains", d) for d in domains) or "方向待确认"),
        Part("audience", "面向人群契合", 18, min(1.0, aud * 1.45),
             "、".join(lbl("audiences", a) for a in audiences) or "人群待确认"),
        Part("market", "市场与语言", 10, mk,
             "/".join(lbl("markets", m) for m in markets) or "市场待确认"),
        Part("budget", "预算匹配", 10, bud,
             f"{'$' * tier} 档 · 单条主内容" if tier else "报价待确认"),
    ]
    total_w = sum(p.weight for p in parts)
    fit = round(sum(p.weight * p.value for p in parts) / total_w * 100)
    word, level = band_of(fit)

    # 内容方向不明的人**不得进最高档**。
    #
    # 「强连接」这个词客户会读成「这人很合适」，而连接强只说明名人关注他，
    # 不说明他写的东西和这个项目有关。MrBeast 被 11 位 AI 目标人物关注是
    # 事实，但他是泛娱乐创作者 —— 把他标成强连接是用连接冒充相关性。
    if not domains and level > 2:
        word, level = BANDS[2][1], BANDS[2][2]

    # 算不算"进了这个圈层"，要看**圈层内**有几位目标人物连着他，不是一位就算。
    #
    # 只要一位就算的话，AI 领域有 86% 的候选都"命中"风险投资人 —— 那个标签就
    # 不再说明任何事，客户标记重点也看不出区别。设计包自己的诊断线是：单圈层
    # 命中率超过 60% 说明连接过于分散。
    #
    # 门槛随该圈层**已采集到的**目标人物数量浮动：只采到 1-2 位的圈层（AI 的
    # 媒体圈层就是 1/5）用 ≥1，否则整个圈层会归零 —— 那是数据缺口，不该由
    # 判定规则来惩罚。
    per_circle: dict[str, int] = {}
    for e in edges:
        c = circle_of_target.get(e.get("targetId", ""))
        if c:
            per_circle[c] = per_circle.get(c, 0) + 1
    circles = sorted(
        c for c, n in per_circle.items()
        if n >= (2 if collected_per_circle.get(c, 0) >= 3 else 1)
    )
    hit = [c for c in circles if c in focus]
    focus_score = sum(
        EDGE_WEIGHTS.get(e.get("type", "cofollow"), 1.0)
        * (1.0 if e.get("verified") else UNVERIFIED_FACTOR)
        for e in edges
        if circle_of_target.get(e.get("targetId", "")) in focus
    )
    focus_note = (
        f"命中重点：{'、'.join(lbl('circles', c) for c in hit)}" if focus and hit else ""
    )

    # —— 推荐理由 ——
    trans = [e for e in edges if e.get("type") in ("reply", "quote")]
    shared = [e for e in edges if e.get("type") == "co_appear"]
    follows = [e for e in edges if e.get("type") == "cofollow"]
    bits: list[str] = []
    if trans:
        verbs = "或".join(dict.fromkeys(EDGE_LABELS[e["type"]] for e in trans))
        who = _name_list([target_names.get(e["targetId"], "") for e in trans], len(trans))
        bits.append(f"{who}{verbs}过他的内容。")
    elif shared:
        who = _name_list([target_names.get(e["targetId"], "") for e in shared], len(shared))
        bits.append(f"与 {who}同场出现过。")
    elif follows:
        who = _name_list([target_names.get(e["targetId"], "") for e in follows], len(follows))
        bits.append(f"{who}都在关注他。")

    d_hit = [lbl("domains", d) for d in domains if d in want_d]
    a_hit = [lbl("audiences", a) for a in audiences if a in want_a]
    if d_hit and a_hit:
        bits.append(f"写{'与'.join(d_hit)}，读者以{'、'.join(a_hit)}为主。")
    elif d_hit:
        bits.append(f"内容集中在{'与'.join(d_hit)}。")
    elif a_hit:
        bits.append(f"读者以{'、'.join(a_hit)}为主。")
    elif not bits:
        first_market = lbl("markets", markets[0]) if markets else "全球"
        bits.append(f"面向{first_market}读者的 X 创作者。")

    # —— 关系强度 ——
    #
    # 取所有边里**最强**的那一条。理由：一条多次互动的边不该被十条单向关注
    # 拉低平均值 —— 客户问的是「最强的那条路有多强」，不是「平均有多强」。
    interactions = [e for e in edges if e.get("type") in ("reply", "quote", "co_appear")]
    repeated = [e for e in interactions if (e.get("count") or 1) > 1]
    if repeated:
        strength = "strong"
    elif interactions:
        strength = "medium"
    elif edges:
        strength = "weak"
    else:
        strength = "none"

    kinds = "、".join(dict.fromkeys(EDGE_LABELS.get(e.get("type", ""), "") for e in edges if e.get("type")))
    overlap_text = f"{len(edges)} 位目标人物 · {kinds}" if edges else "暂无公开连接记录"

    return Scored(
        fit=fit,
        strength=strength,
        strength_label=STRENGTH_LABELS[strength],
        strength_advice=STRENGTH_ADVICE[strength],
        band_word=word,
        band_level=level,
        parts=parts,
        focus_score=focus_score,
        focus_note=focus_note,
        circles=circles,
        reason="".join(bits),
        overlap_text=overlap_text,
    )
