"""待建联候选层 — Mango 内部的 BD 管线状态。

这一层回答的问题和 ``models.py`` 不同。``models.py`` 描述的是 **可以卖的供给**：
有报价、能进客户筛选池的对象。这里描述的是 **还不能卖的对象**：刚被发现、
还没建联、还没报价，但值得 Mango 去谈的账号，以及谈到哪一步了。

三条结构规则，和 ``models.py`` 的三条并列：

1. **不建第二套 creator 主数据。** 每个可投放候选都解析到唯一一条
   ``Creator``；这里只存 **BD 流程状态**（谁负责、谈到哪、下一步做什么）和
   **来源方的原始说法**。同一个账号出现在三份名单里，仍然只有一条 Creator。
   机构类候选没有 Creator（机构不是创作者），用 ``org_name`` 标识，它与
   KOL 的代理关系存在 ``ProcurementRoute``（路径 2），不存在这里。

2. **外部名单的评分和分层是输入，不是结论。** ``source_*`` 列原样保存
   附件里的 score / tier / 描述，并由 ``source_claims_status`` 永久标记为
   ``unverified``。筛选逻辑一律不读这些列 —— 读了就等于把别人的判断当成
   Mango 的判断。它们只用于给人看「对方当时是怎么说的」。

3. **商业合作证据分三件事记，不合并。** 「有邮箱」「过去接过第三方广告」
   「现在确认愿意接这个项目」是三个独立事实，强度递增，且第三件只能来自
   真实回复。``CommercialSignal`` 存单条证据，``STANCE_BY_SIGNAL`` 决定它
   支持哪一件；一条证据永远不会同时支持三件。自我推广（卖自己的课程、
   社群、产品）单独有类型，且 **不支持任何一件** —— 它证明的是这个人会
   发广告给自己，不是他会承接第三方投放。
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .models import Base, Creator, now

# =============================================================================
# 受控词表
# =============================================================================

#: 候选是什么。规格要求「目标人物本身、可投放 KOL、媒体渠道和供应商分别标记」，
#: 因为它们进入完全不同的流程：目标人物不询价，供应商不投放，媒体渠道按库存
#: 而不是按个人声量采购。
#:
#: ``target_person`` 是最容易被搞错的一个。一位投资人或研究者可能和目标圈层
#: 关系极强，但关系强度不能替代商业意愿 —— 他们默认落在「仅作目标或观察」，
#: 除非另有明确的承接第三方合作证据。
OBJECT_KINDS = (
    "kol",             # 可投放的个人创作者
    "media_channel",   # 媒体 / newsletter / podcast，按库存采购
    "supplier",        # 经纪人 / 机构 / MCN / 预订平台，本身不投放
    "target_person",   # 客户想触达的对象，不是投放对象
    # 下面两类是 LLM 身份分类加进来的。它们和 ``target_person`` 的区别在于
    # **是不是客户想触达的人**：SEC 委员对加密客户是目标，对 AI 客户什么都不是。
    # 但两种情况下都一样买不到，所以它们独立成类。
    "public_figure",   # 有影响力但不售卖内容位：创始人/高管/投资人/监管者/学者/记者
    "organization",    # 公司 / 产品 / 会议 / 机构官方号
    "unknown",
)

OBJECT_KIND_LABELS_ZH = {
    "kol": "可投放 KOL",
    "media_channel": "媒体渠道",
    "supplier": "供应商 / 代理入口",
    "target_person": "目标人物",
    "public_figure": "公众人物（不售卖内容位）",
    "organization": "机构 / 产品官方号",
    "unknown": "待分类",
}

#: 买不到的身份。**不是「排名低」，是根本不进可投放名单** —— 一个 SEC 委员
#: 出现在客户的投放候选里，是产品级别的尴尬，不是排序问题。
#:
#: ``unknown`` 不在其中：判不出来从不淘汰候选，这是全系统一致的规则。
NON_BUYABLE_OBJECT_KINDS = frozenset({
    "target_person", "public_figure", "organization",
})

#: BD 跟进状态。``new`` 之外的每一步都必须由人写入，没有任何自动流转 ——
#: 「已联系」是一个事实陈述，脚本无权代替人做这个陈述。
BD_STATUSES = (
    "new",           # 已入库，未建联
    "contacted",     # 已发出建联，未回复
    "replied",       # 有回复，合作意愿未定
    "negotiating",   # 在谈条件
    "quoted",        # 已取得报价（此时应已有 Quote 记录）
    "declined",      # 对方明确拒绝
    "parked",        # 暂缓，理由写在 bd_status_note
)

BD_STATUS_LABELS_ZH = {
    "new": "未建联", "contacted": "已建联待回复", "replied": "已回复",
    "negotiating": "议价中", "quoted": "已有报价", "declined": "已拒绝",
    "parked": "暂缓",
}

#: 一条商业合作证据的类型。和 ``STANCE_BY_SIGNAL`` 配对使用。
COMMERCIAL_SIGNAL_TYPES = (
    # --- 证明「过去接过第三方广告」 ---
    "third_party_sponsorship",      # 第三方品牌的付费合作案例
    # --- 证明「公开表示承接商业合作」 ---
    "accepts_brand_work_statement", # 明确写明接受品牌合作/广告/赞助/付费测评
    "media_kit",                    # 媒体资料包
    "public_rate_card",             # 公开报价单
    "partnership_page",             # 商务合作页面
    # --- 只证明「有可执行联系路径」 ---
    "business_email",               # 公开商务邮箱
    "booking_form",                 # 合作表单 / 预订入口
    "dm_intake",                    # 明确用于接单的私信入口
    "managed_by",                   # 已确认代理该账号的经纪人/机构/供应商
    # --- 只能由真实回复产生 ---
    "confirmed_willing_reply",      # 对方回复确认愿意承接本项目
    # --- 记录但不支持任何一件事 ---
    "work_or_press_email",          # 雇主域名 / 标注为媒体联系的邮箱
    "self_promotion_only",          # 仅推广自有产品/课程/社群
    "declined_commercial",          # 明确拒绝商业合作
)

COMMERCIAL_SIGNAL_LABELS_ZH = {
    "third_party_sponsorship": "第三方品牌付费合作案例",
    "accepts_brand_work_statement": "公开说明接受品牌合作",
    "media_kit": "媒体资料包",
    "public_rate_card": "公开报价单",
    "partnership_page": "商务合作页面",
    "business_email": "公开商务邮箱",
    "booking_form": "合作表单 / 预订入口",
    "dm_intake": "接单私信入口",
    "managed_by": "已确认经纪人 / 机构代理",
    "confirmed_willing_reply": "本人回复确认愿意承接",
    "work_or_press_email": "工作 / 媒体联系邮箱（不是商务合作入口）",
    "self_promotion_only": "仅推广自有产品（不构成承接第三方投放）",
    "declined_commercial": "明确拒绝商业合作",
}

#: 三个独立事实。规格原文：「有邮箱」「过去接过广告」「现在愿意接这个项目」
#: 分别记录。强度递增，且任何一条证据只映射到其中一个。
#:
#: ``None`` 是有意义的答案，不是遗漏：自我推广和明确拒绝都会被记录下来并展示，
#: 但它们不支持上面任何一件事。把 ``self_promotion_only`` 记成「愿意承接」是
#: 这一层要防的主要误判 —— 一个天天推销自己课程的账号，并没有因此证明他愿意
#: 帮别人打广告。
STANCES = ("contactable", "has_commercial_history", "confirmed_willing")

STANCE_LABELS_ZH = {
    "contactable": "有可执行联系路径",
    "has_commercial_history": "过去承接过第三方合作",
    "confirmed_willing": "已确认愿意承接本项目",
}

STANCE_BY_SIGNAL: dict[str, str | None] = {
    "third_party_sponsorship": "has_commercial_history",
    # 下面四条都是「他自己说他接」，比「他确实接过」弱，比「有个邮箱」强。
    # 归到 has_commercial_history 会把声明当成案例，所以它们只算联系路径的
    # 加强，真正的分档由 commercial_maturity() 依据证据种类给出。
    "accepts_brand_work_statement": "contactable",
    "media_kit": "contactable",
    "public_rate_card": "contactable",
    "partnership_page": "contactable",
    "business_email": "contactable",
    "booking_form": "contactable",
    "dm_intake": "contactable",
    "managed_by": "contactable",
    "confirmed_willing_reply": "confirmed_willing",
    # 记录、展示，但**不支持任何一件事**。一个 NYT 记者的 nytimes.com 邮箱能
    # 联系到人，不代表他会接品牌投放 —— 之前把这类邮箱算成「有可执行联系路径」，
    # 结果 Salesforce CEO、NYT 和 Bloomberg 的记者都排进了建联队列。
    "work_or_press_email": None,
    "self_promotion_only": None,
    "declined_commercial": None,
}

#: 「他自己说他接」这一类。单独列出来，是因为它比一个裸邮箱强得多，
#: 但仍然不是成交案例 —— 两者混在一起会让「有联系方式」冒充「有合作证据」。
STATED_ACCEPTANCE_SIGNALS = frozenset({
    "accepts_brand_work_statement", "media_kit",
    "public_rate_card", "partnership_page",
})

#: 只有回复能产生的类型。写入时强制要求 ``verified_by`` 和 ``verified_at``：
#: 「现在愿意接这个项目」是一句需要有人负责的话。
REQUIRES_HUMAN_VERIFICATION = frozenset({"confirmed_willing_reply"})

#: 代理关系的确认程度。规格原文：不把找到一家机构就认定存在代理关系。
#: 默认值是最弱的那一档，升级必须有人写入。
REPRESENTATION_STATUSES = (
    "public_entry_unverified",  # 只是找到了一个公开入口
    "claimed_by_creator",       # 创作者自己声明由谁代理
    "confirmed",                # 双方确认
    "denied",                   # 确认不代理
)

#: 下一步该做什么。规格原文：明确下一步是找联系人、确认合作意愿、询价还是补充样例。
NEXT_STEP_KINDS = (
    "find_contact",        # 补商务路径
    "confirm_willingness", # 确认合作意愿
    "request_quote",       # 询价
    "collect_samples",     # 补样例 / 近期作品
    "research_only",       # 只做观察，不建联
    "none",
)

NEXT_STEP_LABELS_ZH = {
    "find_contact": "补充商务路径（找到可执行联系人）",
    "confirm_willingness": "确认是否承接第三方投放",
    "request_quote": "询价",
    "collect_samples": "补充近期样例与受众证据",
    "research_only": "仅作观察，本轮不建联",
    "none": "无",
}

#: 四档建联优先级。顺序即优先顺序。
BD_PRIORITIES = (
    "priority_inquiry",     # 优先询价
    "confirm_willingness",  # 先确认合作意愿
    "need_bd_path",         # 优先补充商务路径
    "observe_only",         # 仅作目标或观察
)

BD_PRIORITY_LABELS_ZH = {
    "priority_inquiry": "优先询价",
    "confirm_willingness": "先确认合作意愿",
    "need_bd_path": "优先补充商务路径",
    "observe_only": "仅作目标或观察",
}


# =============================================================================
# 表
# =============================================================================


class BDCandidate(Base):
    """一个待建联对象的流程状态。

    自然键是 ``(platform, handle)``，不是自增 id。这是有意的：附件会被重新
    导入，``creators`` 表会被 ``migrate_from_bd`` 整表重建，任何依赖自增 id
    的连接都会在某次重建后静默指向别人。自然键让重复导入变成幂等更新，也让
    重建后能按名字重新接上。

    ``creator_id`` 是「这个候选对应哪条供给记录」的答案，可为空：机构类候选
    没有 Creator，尚未解析的候选也可能暂时为空。它由导入脚本每次重新解析，
    不是一次写死的引用。
    """

    __tablename__ = "bd_candidates"
    __table_args__ = (
        UniqueConstraint("platform", "handle", name="uq_bd_candidate_account"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: 唯一的供给主记录。机构类候选为 NULL。
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id"), index=True)
    #: 机构名（供应商 / 经纪公司）。与 ``creator_id`` 二选一。
    org_name: Mapped[str | None] = mapped_column(String(255))

    platform: Mapped[str] = mapped_column(String(40), index=True)
    #: 小写归一后的账号名，或机构的 slug。自然键的一半。
    handle: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    profile_url: Mapped[str | None] = mapped_column(String(500))

    #: **人的判定。** 只有人写这一列。
    object_kind: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    #: 为什么这样标。规格要求四类分别标记，那就必须能回答「凭什么」。
    object_kind_basis: Mapped[str | None] = mapped_column(Text)

    #: **机器的建议，单独一列。** 和 ``XAccount.root_suggested_type`` 同一条
    #: 纪律：建议写进决定列的那天，就没人分得清哪些身份是人确认过的了。
    object_kind_suggested: Mapped[str | None] = mapped_column(String(30), index=True)
    #: 模型引用的原文 + 置信度。没有引用的判断一律丢弃，不落库。
    object_kind_suggestion_basis: Mapped[str | None] = mapped_column(Text)
    object_kind_suggested_by: Mapped[str | None] = mapped_column(String(120))

    #: 模型判定的内容领域，逗号分隔。同样是**建议**。
    #:
    #: 存在的理由：简介关键词只能对约一半的发现候选推出领域，剩下那一半的
    #: ``domains`` 是空的，于是**任何领域过滤都拦不住他们** —— 一个讲邻里八卦
    #: 的账号会因为被科技名人关注而出现在 AI 项目的名单里。领域判不出来不该
    #: 淘汰人（那条规则不变），但也不该让人躲过筛选。
    verticals_suggested: Mapped[str | None] = mapped_column(String(255))
    verticals_suggestion_basis: Mapped[str | None] = mapped_column(Text)

    @property
    def effective_object_kind(self) -> str:
        """展示与过滤用的身份：**人的判定优先，其次才是机器建议。**"""
        if self.object_kind and self.object_kind != "unknown":
            return self.object_kind
        return self.object_kind_suggested or "unknown"
    #: 目标人物的分组（行业超级大佬 / VC / 垂类专家…）。来源方的说法，未核验。
    target_group: Mapped[str | None] = mapped_column(String(120))
    #: 领域套别：ai / crypto / finance / tech。Root 库按领域分套 —— 做加密的
    #: 客户不该看到一屏 AI 研究员，那既没用也显得系统没在听他说话。
    domain_group: Mapped[str | None] = mapped_column(String(30), index=True)

    # --- BD 流程状态 ---------------------------------------------------------
    bd_status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    bd_status_note: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(120))
    #: 最近一次真实发出的建联。只由人写入，脚本不会碰。
    last_contact_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    #: 规则算出的下一步，可被人覆盖。``next_step_is_manual`` 记住是否被覆盖过，
    #: 否则下一次重新筛选会把人写的判断冲掉。
    next_step_kind: Mapped[str] = mapped_column(String(30), default="none")
    next_step: Mapped[str | None] = mapped_column(Text)
    next_step_is_manual: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- 来源方的原始说法：待核验输入，筛选逻辑不读 --------------------------
    source_name: Mapped[str] = mapped_column(String(120), index=True)
    source_ref: Mapped[str | None] = mapped_column(String(255))
    #: 附件里的评分，原样保存为字符串（"96.03"、"S"、"A"）。不参与任何排序。
    source_score_raw: Mapped[str | None] = mapped_column(String(60))
    #: 附件里的分层（"A_paid_reachable"、"S"）。不参与任何判断。
    source_tier_raw: Mapped[str | None] = mapped_column(String(60))
    #: 附件里的描述、备注、理由，原文保留。
    source_notes_raw: Mapped[str | None] = mapped_column(Text)
    #: 永远是 ``unverified``，除非有人逐条核验过。存在的意义是：任何读到
    #: 上面三列的人，都同时读到这一列。
    source_claims_status: Mapped[str] = mapped_column(String(20), default="unverified")

    first_ingested_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    last_seen_in_source_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)

    creator: Mapped[Creator | None] = relationship()
    signals: Mapped[list["CommercialSignal"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class CommercialSignal(Base):
    """一条商业合作证据，带来源和核验时间。

    挂在 ``creator_id`` 上而不是候选上：这是关于这个人的事实，一份新名单不会
    让他重新变得不接广告。机构类候选没有 Creator，才退回挂在候选上。

    ``verified_at`` 为空表示「找到了，还没有人核验过」，这是导入后的正常状态，
    不是缺陷。任何展示都必须把它读成「未核验」。
    """

    __tablename__ = "commercial_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id"), index=True)
    bd_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("bd_candidates.id"), index=True
    )

    signal_type: Mapped[str] = mapped_column(String(40), index=True)

    #: 证据本身。``evidence_quote`` 是原文片段 —— 和 ``audience_types_evidence``
    #: 同样的理由：主页简介会改，不存原文这条判断日后无法复核。
    evidence_url: Mapped[str | None] = mapped_column(String(500))
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    #: 具体的值（邮箱地址、表单链接、机构名）。仅内部可见。
    value: Mapped[str | None] = mapped_column(String(500))

    #: 我们看到它的时间，与「它发生的时间」不同。
    observed_at: Mapped[dt.date | None] = mapped_column(Date)
    #: 证据里写明的发生时间（赞助视频的发布日期等）。未知即 NULL。
    occurred_at: Mapped[dt.date | None] = mapped_column(Date)

    #: 有人确认过这条证据成立。NULL = 未核验。
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    verified_by: Mapped[str | None] = mapped_column(String(120))

    #: FACT_STATUSES。导入的一律是 research_lead。
    fact_status: Mapped[str] = mapped_column(String(30), default="research_lead")
    #: True 表示这条是从别处推断的，不是直接读到的。推断出来的邮箱不允许存在
    #: —— 规格明确「不猜邮箱」—— 所以这个标记主要用于代理关系一类的推断。
    is_inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)

    source_system: Mapped[str | None] = mapped_column(String(60))
    source_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    candidate: Mapped["BDCandidate | None"] = relationship(back_populates="signals")

    @property
    def stance(self) -> str | None:
        """这条证据支持三件事里的哪一件，或者一件都不支持。"""
        return STANCE_BY_SIGNAL.get(self.signal_type)
