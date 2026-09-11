"""Company-specific creator matching for campaign proposals.

The previous recommender treated an ``AI`` category overlap plus any parsed
rate as sufficient evidence.  That is useful for finding inventory, but it
cannot answer whether a creator can deliver a filmmaker residency, a developer
build challenge, or an enterprise enablement series.  This module makes the
commercial brief explicit and returns field-level evidence for every match.

The profiles are intentionally conservative.  A creator who only matches the
generic word ``AI`` receives no company-fit role.  Missing specialist supply is
reported as a gap instead of being filled with a generic distribution account.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CapabilityRole:
    key: str
    label_zh: str
    keywords: tuple[str, ...]
    proposed_deliverable_zh: str
    priced_scope_terms: tuple[str, ...]
    preferred_platforms: tuple[str, ...] = ()
    region_terms: tuple[str, ...] = ()
    allowed_creator_classes: tuple[str, ...] = ()
    profile_required: bool = False
    explanation_zh: str = ""


@dataclass(frozen=True)
class CampaignProfile:
    key: str
    thesis_zh: str
    roles: tuple[CapabilityRole, ...]
    lean_count: int
    recommended_count: int
    premium_count: int
    lean_purpose_zh: str
    recommended_purpose_zh: str
    premium_purpose_zh: str
    unpriced_scope_by_tier: dict[str, tuple[str, ...]]
    official_basis_urls: tuple[str, ...] = ()


def _role(
    key: str,
    label: str,
    keywords: Iterable[str],
    deliverable: str,
    priced_terms: Iterable[str],
    *,
    platforms: Iterable[str] = (),
    region_terms: Iterable[str] = (),
    allowed_classes: Iterable[str] = (),
    profile_required: bool = False,
    explanation: str = "",
) -> CapabilityRole:
    return CapabilityRole(
        key=key,
        label_zh=label,
        keywords=tuple(term.casefold() for term in keywords),
        proposed_deliverable_zh=deliverable,
        priced_scope_terms=tuple(term.casefold() for term in priced_terms),
        preferred_platforms=tuple(platforms),
        region_terms=tuple(term.casefold() for term in region_terms),
        allowed_creator_classes=tuple(allowed_classes),
        profile_required=profile_required,
        explanation_zh=explanation,
    )


RUNWAY_PROFILE = CampaignProfile(
    key="runway_filmmaker_program",
    thesis_zh="从创作者筛选、作品工作坊到成片和 festival submission 的 APAC filmmaker program。",
    roles=(
        _role(
            "ai_filmmaker",
            "AI filmmaker / 叙事创作者",
            ("filmmaker", "filmmaking", "ai film", "film maker", "narrative film", "cinematic"),
            "完成一支可用于 Runway AIF 路径的叙事作品，并公开拆解生成式视频工作流",
            (
                "film production",
                "ai film production",
                "narrative film",
                "short film",
                "影片制作",
                "电影制作",
                "完整影片",
            ),
            platforms=("YouTube", "Instagram", "TikTok"),
            profile_required=True,
            explanation="对应 AIF 的完整叙事作品和生成式视频使用要求。",
        ),
        _role(
            "vfx_motion_educator",
            "VFX / motion / cinema educator",
            ("vfx", "after effects", "motion design", "motion graphics", "animation", "filmmakers learning"),
            "主持制作工作坊，交付 VFX 或 motion workflow 教程与可复用教学材料",
            (
                "vfx tutorial",
                "after effects tutorial",
                "motion tutorial",
                "workshop",
                "course",
                "教程",
                "工作坊",
            ),
            platforms=("YouTube", "Instagram"),
            profile_required=True,
            explanation="负责把工具使用转成可执行的创作教学，而不是只做曝光。",
        ),
        _role(
            "creative_director_studio",
            "创意导演 / 工作室",
            ("creative director", "creative studio", "art director", "production studio", "generative technologist"),
            "参与 creative brief、作品审核和成片质量把关",
            (
                "creative direction",
                "art direction",
                "production supervision",
                "creative brief",
                "consulting",
                "创意指导",
                "顾问",
            ),
            platforms=("Instagram", "YouTube", "X"),
            profile_required=True,
            explanation="对应 residency 的创意指导与作品完成责任。",
        ),
        _role(
            "japan_asia_creative",
            "日本 / 亚洲创意节点",
            ("ai art", "artistic creation", "visual storytelling", "design", "creative", "filmmaker"),
            "在日本或亚洲创作者社区完成招募、当地内容和线下或线上分发",
            (
                "creator recruitment",
                "community recruitment",
                "community activation",
                "event",
                "workshop",
                "创作者招募",
                "社区招募",
                "工作坊",
            ),
            platforms=("YouTube", "Instagram", "X"),
            region_terms=("japan", "japanese", "tokyo", "asia", "apac", "korea", "singapore"),
            profile_required=True,
            explanation="只有地区字段或公开简介明确命中时，才承担区域角色。",
        ),
        _role(
            "film_media_community",
            "影视媒体 / 创意社区",
            ("film festival", "filmmaker community", "cinema", "film media", "creative community", "new media"),
            "为 workshop、放映或 festival submission 提供社区招募与作品分发",
            ("newsletter", "event", "sponsorship", "video", "post", "community"),
            platforms=("YouTube", "Instagram", "X"),
            allowed_classes=("Media / Community Account", "Community Leader"),
            explanation="这是渠道角色，不与个人 filmmaker 混为一类。",
        ),
    ),
    lean_count=3,
    recommended_count=4,
    premium_count=7,
    lean_purpose_zh="验证 1 个作品工作流和 1 场小型线上 workshop，先确认创作者质量与完成率。",
    recommended_purpose_zh="形成可交付的 filmmaker cohort，覆盖教学、作品制作、区域招募和 submission 准备。",
    premium_purpose_zh="升级为 residency 加区域放映单元，增加创意指导、媒体分发和多作品制作能力。",
    unpriced_scope_by_tier={
        "lean": ("作品制作费或 grant", "workshop 策划与主持", "AIF submission 管理"),
        "recommended": ("filmmaker residency 运营", "作品制作费或 grant", "导师与评审费用", "submission 管理"),
        "premium": ("residency 全程运营", "多部作品制作费或 grant", "场地、放映与 festival partner 成本", "导师与评审费用"),
    },
    official_basis_urls=("https://aif.runwayml.com/", "https://aif.runwayml.com/terms-film", "https://runwayml.com/studios"),
)


DEVELOPER_ROLES = (
    _role(
        "developer_builder",
        "开发者 builder",
        ("developer", "software engineer", "programming", "coding", "build apps", "ai engineer", "no code"),
        "完成可运行项目，从 setup 到部署展示真实工作流",
        ("youtube", "video", "tutorial", "thread", "workshop", "dedicated"),
        platforms=("YouTube", "X"),
    ),
    _role(
        "technical_educator",
        "技术型讲解者",
        ("technical educator", "tutorial", "education", "teaching", "developer education", "cloud", "api"),
        "交付教程、代码或模板，并解释实现限制",
        ("youtube", "video", "tutorial", "thread", "article", "newsletter"),
        platforms=("YouTube", "X"),
    ),
    _role(
        "founder_operator",
        "Founder / operator creator",
        ("founder", "startup", "entrepreneur", "product builder", "indie hacker", "operations"),
        "用真实业务场景验证产品激活、留存或团队使用",
        ("video", "youtube", "case study", "thread", "workshop", "newsletter"),
    ),
    _role(
        "developer_community",
        "开发者社区 / build event",
        ("developer community", "hackathon", "build night", "meetup", "student developer", "coding community"),
        "组织 build night、项目展示和参与者复盘",
        ("event", "workshop", "community", "sponsorship", "newsletter"),
    ),
)


PRODUCTIVITY_ROLES = (
    _role(
        "workflow_educator",
        "工作流教育者",
        ("productivity", "workflow", "tutorial", "education", "automation", "knowledge work"),
        "用真实工作流制作教程，并跟踪激活和复用",
        ("youtube", "video", "tutorial", "thread", "newsletter", "reel"),
    ),
    _role(
        "business_operator",
        "业务 / 增长 operator",
        ("marketing", "growth", "sales", "business", "operations", "consultant", "ecommerce"),
        "以业务任务和转化目标验证产品价值",
        ("case study", "video", "youtube", "thread", "newsletter", "workshop"),
    ),
    _role(
        "founder_educator",
        "创业者 / 教育 creator",
        ("founder", "entrepreneur", "educator", "teacher", "student", "course", "online education"),
        "面向创业者或学习者交付模板、教程和案例",
        ("youtube", "video", "course", "tutorial", "newsletter", "workshop"),
    ),
    _role(
        "regional_distribution",
        "区域内容与社区分发",
        ("community", "regional", "asia", "apac", "japan", "india", "localization", "bilingual"),
        "完成当地语言内容、社区触达和区域归因",
        ("post", "video", "reel", "newsletter", "event", "community"),
    ),
)


GAMMA_PROFILE = CampaignProfile(
    key="gamma_japan_creator_activation",
    thesis_zh="以日本本地 creator 教育、workshop 和 localized Gamma 101 为主线，并用既有 productivity tutorial 合作模式验证激活。",
    roles=PRODUCTIVITY_ROLES,
    lean_count=3,
    recommended_count=6,
    premium_count=9,
    lean_purpose_zh="用一位现有可定价教程 creator，加两位公司特定候选，先验证内容到激活路径。",
    recommended_purpose_zh="加入 presentation 垂类和 founder/workflow 角色，验证重复教程分发与 Teams 意向。",
    premium_purpose_zh="覆盖全部已发现的公司特定历史候选；日本本地 creator 层仍须完成商业核验后才能成为可发送方案。",
    unpriced_scope_by_tier={
        "lean": ("未记录报价的 creator", "Mango 服务费"),
        "recommended": ("未记录报价的 creator", "Mango 服务费", "内容本地化与项目管理"),
        "premium": (
            "未记录报价的 creator",
            "日本本地 creator 商业核验与报价",
            "Mango 服务费",
            "内容本地化、workshop 与项目管理",
        ),
    },
    official_basis_urls=(
        "https://careers.gamma.app/growth-marketing-manager",
        "https://careers.gamma.app/regional-community-manager-japan",
    ),
)


VOICE_ROLES = (
    _role(
        "voice_audio_creator",
        "Voice / audio creator",
        ("voice", "audio", "podcast", "sound", "voiceover", "music production"),
        "展示真实语音或音频生产工作流，并提供可听的前后对比",
        ("audio", "podcast", "video", "youtube", "reel", "tutorial"),
    ),
    *DEVELOPER_ROLES[:2],
    PRODUCTIVITY_ROLES[3],
)


VISUAL_VIDEO_ROLES = (
    _role(
        "ai_video_storyteller",
        "AI video storyteller",
        ("ai video", "video creator", "visual storytelling", "cinematic", "film", "animation"),
        "制作完整视觉作品和过程拆解，而不是只转发功能素材",
        ("video", "reel", "youtube", "content creation", "production"),
        platforms=("YouTube", "Instagram", "TikTok"),
    ),
    _role(
        "visual_educator",
        "视觉工作流教育者",
        ("design education", "vfx", "motion", "tutorial", "creative workflow", "video production"),
        "交付可复用的视觉工作流教程和制作模板",
        ("video", "reel", "youtube", "tutorial", "workshop"),
    ),
    _role(
        "creative_professional",
        "创意专业人士 / 工作室",
        ("creative director", "art director", "studio", "designer", "filmmaker", "agency"),
        "用专业 brief 和成片标准验证产品能力",
        ("production", "video", "reel", "case study", "project"),
    ),
    PRODUCTIVITY_ROLES[3],
)


PROFILE_OVERRIDES: dict[str, CampaignProfile] = {
    "company:runway": RUNWAY_PROFILE,
}


def _profile(
    key: str,
    thesis: str,
    roles: tuple[CapabilityRole, ...],
    *,
    counts: tuple[int, int, int] = (3, 6, 9),
) -> CampaignProfile:
    return CampaignProfile(
        key=key,
        thesis_zh=thesis,
        roles=roles,
        lean_count=counts[0],
        recommended_count=counts[1],
        premium_count=counts[2],
        lean_purpose_zh="用最小角色组合验证一个清晰场景和一条可归因转化路径。",
        recommended_purpose_zh="覆盖核心内容、教育和分发角色，形成可复用的多 creator 试点。",
        premium_purpose_zh="增加高可信 creator、平台覆盖和区域分发，但仍不把未报价的项目成本计入总额。",
        unpriced_scope_by_tier={
            "lean": ("Mango 服务费",),
            "recommended": ("Mango 服务费", "内容本地化与项目管理"),
            "premium": ("Mango 服务费", "内容本地化与项目管理", "额外制作或线下活动成本"),
        },
    )


PROFILE_OVERRIDES.update(
    {
        "company:cursor": _profile("cursor_developer_cohort", "中文开发者教程、build night 与 retained-usage 试点。", DEVELOPER_ROLES),
        "company:replit": _profile("replit_build_to_publish", "让技术 creator 从构建到发布真实应用，并跟踪激活与留存。", DEVELOPER_ROLES),
        "company:tavus": _profile("tavus_video_agent_builders", "由开发者和 agency 交付可投入生产的视频 agent demo。", DEVELOPER_ROLES + VISUAL_VIDEO_ROLES[:1]),
        "company:elevenlabs": _profile("elevenlabs_voice_workflows", "连接 voice creator、API 教程和区域本地化的可归因试点。", VOICE_ROLES, counts=(3, 7, 10)),
        "company:wisprflow": _profile("wispr_voice_productivity", "用 voice workflow 覆盖创业者、开发者和高频写作者。", (VOICE_ROLES[0],) + PRODUCTIVITY_ROLES[:3]),
        "company:gamma": GAMMA_PROFILE,
        "company:perplexity": _profile("perplexity_answer_workflows", "用研究、知识工作和教育场景验证 Pro 激活。", PRODUCTIVITY_ROLES, counts=(3, 7, 10)),
        "company:gumloop": _profile("gumloop_automation_challenge", "以 automation workflow challenge 验证账户激活、模板复用和团队场景。", DEVELOPER_ROLES + PRODUCTIVITY_ROLES[:2]),
        "company:synthesia": _profile("synthesia_enablement_series", "围绕培训、销售赋能和内部沟通的 business education 系列。", PRODUCTIVITY_ROLES + VISUAL_VIDEO_ROLES[:1]),
        "company:creatify": _profile("creatify_ad_lab", "以 performance marketing、UGC 和 ecommerce creator 运行本地化广告实验。", PRODUCTIVITY_ROLES[1:] + VISUAL_VIDEO_ROLES[:2]),
        "company:pixverse": _profile("pixverse_creator_production", "用 AI video、教程和区域 creator 验证作品到订阅的漏斗。", VISUAL_VIDEO_ROLES, counts=(3, 7, 10)),
        "company:hedra": _profile("hedra_character_storytelling", "用视觉 storyteller 和 agency 展示可复用 character-video workflow。", VISUAL_VIDEO_ROLES),
        "company:openart": _profile("openart_visual_cohort", "用视觉叙事 creator、项目 brief 和 affiliate 跟踪完成区域试点。", VISUAL_VIDEO_ROLES),
        "company:meshy": _profile(
            "meshy_3d_creator_lab",
            "覆盖游戏资产、3D artist、maker 和前后对比教程的 creator lab。",
            (
                _role("3d_artist", "3D artist", ("3d artist", "3d design", "3d model", "blender", "cgi"), "制作可交付 3D 资产并展示完整迭代过程", ("video", "youtube", "tutorial", "project", "reel")),
                _role("game_developer", "Game developer", ("game developer", "game design", "gaming", "unity", "unreal"), "把生成资产放入真实 game workflow", ("video", "youtube", "tutorial", "project")),
                _role("maker_educator", "Maker / 3D 教育者", ("maker", "3d printing", "tutorial", "education", "design workflow"), "交付可打印物件或教学案例", ("video", "youtube", "tutorial", "workshop", "reel")),
                PRODUCTIVITY_ROLES[3],
            ),
        ),
    }
)


def campaign_profile_for(company) -> CampaignProfile:
    """Return a company override or a conservative ICP/category fallback."""
    if company.company_id in PROFILE_OVERRIDES:
        return PROFILE_OVERRIDES[company.company_id]
    dossier = getattr(company, "research_dossier", None)
    icp = (getattr(dossier, "icp_type", "") or "").casefold()
    category = (getattr(company, "category", "") or "").casefold()
    if "developer" in icp or "api" in icp or any(term in category for term in ("developer", "api", "coding")):
        return _profile("developer_tool_fallback", "技术 creator 完成真实 build、教程和激活复盘。", DEVELOPER_ROLES)
    if any(term in category for term in ("video", "image", "visual", "creative")):
        return _profile("visual_product_fallback", "视觉 creator 交付真实作品、过程拆解和分发。", VISUAL_VIDEO_ROLES)
    return _profile("productivity_fallback", "用真实业务工作流和可归因内容验证产品需求。", PRODUCTIVITY_ROLES)


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _signal_fields(creator) -> dict[str, str]:
    profiles = []
    content = []
    platforms = []
    for account in getattr(creator, "social_accounts", []) or []:
        platforms.append(account.platform or "")
        profiles.extend((account.bio or "", account.content_summary or ""))
        content.extend(item.text or "" for item in (getattr(account, "recent_content", []) or [])[:12])
    deliverables = [rate.deliverable or "" for rate in getattr(creator, "rate_cards", []) or []]
    return {
        "categories": _clean(getattr(creator, "categories", "")),
        "profile": _clean(" ".join(profiles)),
        "recent_content": _clean(" ".join(content)),
        "deliverables": _clean(" ".join(deliverables)),
        "region": _clean(" ".join((getattr(creator, "region", "") or "", getattr(creator, "language", "") or ""))),
        "platforms": _clean(" ".join(platforms)),
    }


def _matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    folded = text.casefold()
    return [term for term in terms if term in folded]


def _short_field_evidence(fields: dict[str, str], role: CapabilityRole) -> tuple[list[str], list[str]]:
    evidence = []
    all_hits: list[str] = []
    profile_hit_count = 0
    for field_name, label in (("categories", "类别"), ("profile", "公开简介"), ("recent_content", "近期内容")):
        hits = _matched_terms(fields[field_name], role.keywords)
        if hits:
            all_hits.extend(hits)
            evidence.append(f"{label}命中 {', '.join(hits[:3])}")
            if field_name in {"categories", "profile"}:
                profile_hit_count += len(hits)
    recent_occurrences = sum(fields["recent_content"].casefold().count(term) for term in role.keywords)
    if role.profile_required and profile_hit_count == 0:
        return [], []
    # One incidental word in one post title is too weak to qualify someone
    # for a client-facing specialist role.  Without category/profile support,
    # require repeated content evidence.
    if profile_hit_count == 0 and recent_occurrences < 3:
        return [], []
    if role.region_terms:
        region_hits = _matched_terms(fields["region"] + " " + fields["profile"], role.region_terms)
        if region_hits:
            all_hits.extend(region_hits)
            evidence.append(f"地区信号命中 {', '.join(region_hits[:2])}")
        else:
            return [], []
    return sorted(set(all_hits)), evidence


def match_creator_to_campaign(creator, company) -> dict:
    """Return structured, source-field-backed fit evidence for one creator."""
    profile = campaign_profile_for(company)
    fields = _signal_fields(creator)
    platforms = {item.strip() for item in fields["platforms"].split() if item.strip()}
    matches = []
    for role in profile.roles:
        if role.allowed_creator_classes and getattr(creator, "creator_class", None) not in role.allowed_creator_classes:
            continue
        terms, evidence = _short_field_evidence(fields, role)
        if not terms:
            continue
        platform_match = sorted(platforms.intersection(role.preferred_platforms)) if role.preferred_platforms else []
        score = 30 + min(len(terms), 4) * 6 + (8 if platform_match else 0)
        matches.append(
            {
                "key": role.key,
                "label": role.label_zh,
                "score": score,
                "matched_terms": terms,
                "evidence": evidence,
                "preferred_platform_match": platform_match,
                "proposed_deliverable": role.proposed_deliverable_zh,
                "explanation": role.explanation_zh,
                "priced_scope_terms": list(role.priced_scope_terms),
            }
        )
    matches.sort(key=lambda row: (-row["score"], row["key"]))
    best = matches[0] if matches else None
    return {
        "profile_key": profile.key,
        "profile_thesis": profile.thesis_zh,
        "matched_capabilities": matches,
        "best_capability": best,
        "fit_score": sum(row["score"] for row in matches[:2]),
        "fit_level": "core_fit" if len(matches) >= 2 else "role_fit" if matches else "generic_only",
        "generic_only": not bool(matches),
        "signal_fields": fields,
    }


def quote_scope_fit(rate, capability: dict | None) -> dict:
    """Whether a historical rate actually prices the proposed role.

    A filmmaker's USD 150 X post is real inventory, but it is not a USD 150
    film-production quote.  The API keeps the rate visible while refusing to
    roll it into a program budget unless the deliverable wording overlaps the
    capability's priced scope.
    """
    deliverable = _clean(getattr(rate, "deliverable", ""))
    # Raw quote text often contains the seller's entire rate menu.  Matching
    # against it can make a cheap newsletter line look like a video quote just
    # because another line in the same email mentions YouTube.  Only the
    # parsed deliverable for this exact rate card can establish scope fit.
    text = deliverable.casefold()
    terms = tuple((capability or {}).get("priced_scope_terms") or ())
    hits = [term for term in terms if term in text]
    if hits:
        return {
            "status": "aligned",
            "price_included": True,
            "reason": f"现有报价交付物命中该角色所需范围：{', '.join(hits[:3])}。",
        }
    return {
        "status": "separate_quote_required",
        "price_included": False,
        "reason": "现有报价只证明可联系和历史价格，不覆盖本方案的核心交付；需另行询价。",
    }


def profile_public_json(profile: CampaignProfile) -> dict:
    return {
        "key": profile.key,
        "thesis": profile.thesis_zh,
        "required_capabilities": [
            {
                "key": role.key,
                "label": role.label_zh,
                "proposed_deliverable": role.proposed_deliverable_zh,
                "explanation": role.explanation_zh,
            }
            for role in profile.roles
        ],
        "tier_purposes": {
            "lean": profile.lean_purpose_zh,
            "recommended": profile.recommended_purpose_zh,
            "premium": profile.premium_purpose_zh,
        },
        "tier_target_counts": {
            "lean": profile.lean_count,
            "recommended": profile.recommended_count,
            "premium": profile.premium_count,
        },
        "unpriced_scope_by_tier": {
            key: list(value) for key, value in profile.unpriced_scope_by_tier.items()
        },
        "official_basis_urls": list(profile.official_basis_urls),
    }
