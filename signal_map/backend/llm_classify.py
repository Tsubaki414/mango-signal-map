"""LLM audience classification for Signal Map.

Keyword matching was not good enough. It produced judgments like:

    OtakuMachine -> creators   because the bio said "edits"
    TheAva_AI    -> founders   because the bio said "Passive income"

The second is shallow and the first is simply wrong: an anime-edits channel is
watched by *anime fans*, not by video editors. The regex was matching **what
the creator does** and labelling it **who watches**, which are different
questions. No amount of extra patterns fixes that, because the distinction is
semantic.

So the judgment is made by a model reading the creator's actual content, under
constraints that keep it auditable:

* **Closed vocabulary.** Output is validated against ``AUDIENCE_TYPES``;
  anything else is dropped rather than accepted as a new category.
* **Evidence is mandatory.** Every assigned type must quote the content that
  justified it. A type returned without a usable quote is discarded -- this is
  what stops the model from free-associating.
* **"Unknown" is a valid answer** and the prompt says so. A vague bio should
  return nothing, not a plausible-sounding guess.
* **Provenance is stored.** Model id and classification date travel with the
  result, so a later model change is visible rather than silent.

This does not contradict the rule that recommendation reasons are never
model-authored. That rule governs the *reason text shown to a client*, which
stays rule-generated from stored fields. This is structured extraction from
real content into a fixed enum, which a human then reviews.

Provider-agnostic: xAI (Grok) and OpenAI expose the same
``/v1/chat/completions`` schema, so either key works and the choice is made
by which one is present. xAI wins when both are set.

    XAI_API_KEY   + XAI_MODEL      -> https://api.x.ai/v1      (grok-4)
    OPENAI_API_KEY + OPENAI_MODEL  -> https://api.openai.com/v1 (gpt-4o-mini)

Responses are cached to disk keyed by the exact content classified *and* the
model, so re-running is free, a content change re-triggers classification, and
switching model does not silently reuse the previous model's verdicts.
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi

from .normalize import AUDIENCE_TYPE_LABELS_ZH, AUDIENCE_TYPES, VERTICALS
from .observation_models import ROOT_TYPE_LABELS_ZH, ROOT_TYPES

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "llm"
CACHE_TTL_SECONDS = 90 * 24 * 3600

#: (env key, env model var, base url, default model). Ordered by preference.
#: xAI default is the non-reasoning variant on purpose: this is closed-
#: vocabulary extraction at temperature 0, where a reasoning model costs more
#: and adds nothing. Override with XAI_MODEL if a harder judgment is needed.
PROVIDERS = (
    (
        "XAI_API_KEY",
        "XAI_MODEL",
        "https://api.x.ai/v1/chat/completions",
        "grok-4.20-0309-non-reasoning",
    ),
    ("OPENAI_API_KEY", "OPENAI_MODEL", "https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
)

_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class Provider:
    name: str
    key: str
    url: str
    model: str


def resolve_provider() -> Provider:
    """Pick the first provider with a key set. Raises if none is configured."""
    for key_var, model_var, url, default_model in PROVIDERS:
        key = os.environ.get(key_var, "").strip()
        if key:
            return Provider(
                name=key_var.replace("_API_KEY", "").lower(),
                key=key,
                url=url,
                model=os.environ.get(model_var, "").strip() or default_model,
            )
    raise LLMError(
        "No LLM key configured. Set XAI_API_KEY (Grok) or OPENAI_API_KEY in the repo-root .env."
    )


_AUDIENCE_MENU = "\n".join(
    f"- {key} ({AUDIENCE_TYPE_LABELS_ZH[key]})" for key in AUDIENCE_TYPES
)

SYSTEM_PROMPT = f"""You classify social-media creators for Mango Labs, a KOL agency.

Your ONE job: identify WHO WATCHES this creator -- their audience.

This is NOT the same as what the creator does for a living. Read carefully:
- An anime-edits channel is watched by ANIME FANS (consumers), not by video editors.
- A channel teaching Python is watched by DEVELOPERS and LEARNERS, not by teachers.
- A "make passive income with AI" channel is watched by people who WANT income
  (consumers, aspiring founders), not by established founders.
- A design-resources account is watched by DESIGNERS.
Confusing the creator's craft with their audience is the single most common
error here. Do not make it.

Pick 0-3 audience types from this closed list. Use the exact keys:
{_AUDIENCE_MENU}

Rules:
1. Every type you return MUST be justified by a short verbatim quote from the
   supplied content. If you cannot quote something that supports it, do not
   return that type.
2. Returning an EMPTY list is correct and expected when the content is vague,
   generic, or only says the topic without revealing the audience. "This
   account posts about AI" tells you the topic, NOT who watches. Return [] --
   do not guess. A wrong audience is worse than an admitted unknown.
3. Order by how central the audience is, most central first.
4. Judge only from the supplied content. Do not use outside knowledge about
   this person, and do not infer from the account name alone.
5. IGNORE business-contact lines -- "Brand collaborations open", "📩 DM for
   partnerships", "Business inquiries: x@y.com", "Contact:", agency or manager
   names. Those say who the creator WORKS WITH commercially, not who watches
   them, and are present on nearly every account. Never cite one as evidence.

Respond ONLY with compact JSON:
{{"audience_types": [{{"type": "<key>", "evidence": "<verbatim quote, <=12 words>"}}],
  "confidence": "high|medium|low",
  "note": "<<=20 words on what the content shows, or why you returned []>"}}"""


@dataclass
class AudienceVerdict:
    """One classification result, with everything needed to audit it."""

    types: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    confidence: str = "low"
    note: str | None = None
    model: str | None = None
    from_cache: bool = False

    @property
    def types_csv(self) -> str | None:
        return ",".join(self.types) if self.types else None

    @property
    def evidence_text(self) -> str | None:
        """``developers:"builds with the API"; students:"beginner guide"``."""
        if not self.evidence:
            return None
        return "; ".join(self.evidence)


_VERTICAL_MENU = ", ".join(VERTICALS)

VERTICAL_PROMPT = f"""You assign content verticals to a social-media creator for a KOL agency.

Pick 1-3 verticals from this closed list, using the exact keys:
{_VERTICAL_MENU}

Rules:
1. Judge only from the supplied content. Do not use outside knowledge.
2. Every vertical must be justified by a short verbatim quote from the content.
   No quote, no vertical.
3. Returning an EMPTY list is correct when the content is too vague to tell.
   A wrong vertical sends a creator into the wrong client's shortlist, which is
   worse than an admitted unknown.
4. Order by centrality: the vertical the account is mostly about comes first.
5. "ai" is extremely common here and therefore low-information on its own. If
   the account is about AI *applied to* something (design, coding, marketing,
   video), include that second vertical too.

Respond ONLY with compact JSON:
{{"verticals": [{{"key": "<key>", "evidence": "<verbatim quote, <=12 words>"}}],
  "confidence": "high|medium|low"}}"""


@dataclass
class VerticalVerdict:
    keys: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    confidence: str = "low"
    model: str | None = None
    from_cache: bool = False

    @property
    def csv(self) -> str | None:
        return ",".join(self.keys) if self.keys else None

    @property
    def evidence_text(self) -> str | None:
        return "; ".join(self.evidence) if self.evidence else None


def _parse_verticals(content: str, model: str) -> VerticalVerdict:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"model returned non-JSON: {content[:160]}") from exc

    keys: list[str] = []
    evidence: list[str] = []
    for item in data.get("verticals") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        quote = str(item.get("evidence", "")).strip()
        if key not in VERTICALS or key in keys or len(quote) < 3:
            continue
        keys.append(key)
        evidence.append(f'{key}:"{quote[:80]}"')
        if len(keys) >= 3:
            break

    confidence = str(data.get("confidence", "low")).strip().lower()
    return VerticalVerdict(
        keys=keys,
        evidence=evidence,
        confidence=confidence if confidence in {"high", "medium", "low"} else "low",
        model=model,
    )


def classify_verticals(profile_text: str, *, force: bool = False, timeout: int = 45) -> VerticalVerdict:
    """Assign content verticals under the same guardrails as audience.

    Separate call rather than one combined prompt: a model asked for two
    different judgments at once tends to let the easier one anchor the harder,
    and caching them separately means fixing one prompt does not invalidate
    the other's verdicts.
    """
    if not profile_text.strip():
        return VerticalVerdict()

    provider = resolve_provider()
    model = provider.model
    digest = hashlib.sha256(
        f"{model}\n{hashlib.sha256(VERTICAL_PROMPT.encode()).hexdigest()[:8]}\n{profile_text}".encode()
    ).hexdigest()[:24]
    cache = CACHE_DIR / f"verticals-{digest}.json"

    if not force and cache.exists() and time.time() - cache.stat().st_mtime <= CACHE_TTL_SECONDS:
        try:
            verdict = _parse_verticals(cache.read_text(encoding="utf-8"), model)
            verdict.from_cache = True
            return verdict
        except (LLMError, OSError):
            pass

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": VERTICAL_PROMPT},
            {"role": "user", "content": profile_text},
        ],
        "temperature": 0,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }
    response = _post(payload, provider, timeout)
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"unexpected response shape: {str(response)[:200]}") from exc

    verdict = _parse_verticals(content, model)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(content, encoding="utf-8")
    return verdict


#: Returned when the account is not a Root at all. Deliberately **not** a
#: member of ``ROOT_TYPES``: "reject this candidate" is a different kind of
#: answer from "this is an investor", and putting it in the type vocabulary
#: would eventually let it be written to ``root_type`` as though it were one.
NOT_A_ROOT = "not_a_root"

_ROOT_MENU = """- industry_leader (行业大佬): recognised across the whole industry. Founders/
  chief scientists of major labs and companies, people whose opinion moves the
  field. Recognition extends well beyond one niche.
- vertical_expert (垂直专家): deep, credible authority in ONE specific area.
  Respected by practitioners of that area, not famous outside it.
- investor (投资人): invests capital -- VC, angel, fund partner, accelerator.
- media (媒体节点): a publication, newsletter, podcast or news account whose
  job is to report and distribute.
- community_leader (社区领袖): runs or anchors a community, DAO, meetup or
  large group -- their standing comes from convening people.
- enterprise_buyer (企业决策者): holds a budget or buying role inside a
  company -- CTO, Head of Marketing, VP, procurement.
- project (项目/产品账号): the official account of a product or company, not a
  person."""

ROOT_PROMPT = f"""You classify X (Twitter) accounts for Mango Labs, a KOL agency.

Mango's clients want their message to reach specific KINDS OF PEOPLE. An
account worth reaching is called a **Root**. Your job: decide what kind of Root
this account is, or whether it is not a Root at all.

Root types (use the exact key):
{_ROOT_MENU}

CRITICAL — the most common wrong answer here:
Most accounts you will see are **content creators / influencers**: they post
tips, tutorials, AI news roundups, growth advice, threads. A creator with a
large following is NOT a Root. They are the same kind of account Mango sells;
reaching them accomplishes nothing for a client. Return "{NOT_A_ROOT}" for:
- accounts whose bio is about making content, threads, prompts, tutorials
- ghostwriters, growth/personal-brand services, "DM for collab"
- "AI news" accounts run by one person for reach rather than journalism
- anyone whose stated identity is "creator", "influencer", "content"

An account is a Root because of WHO THEY ARE (their role, company, capital,
publication), never because of how many followers they have.

Rules:
1. The type MUST be justified by a short verbatim quote from the supplied
   content. No quote, no type.
2. "{NOT_A_ROOT}" is a normal, frequent, correct answer. So is low confidence.
   A wrong Root puts a false attention path in front of a paying client.
3. Judge only from the supplied content. Do not use outside knowledge about
   this person, and do not infer from follower count or the handle alone.
4. If the content is too thin to tell, return "{NOT_A_ROOT}" with confidence
   "low" and say so in the note. Do not guess a flattering type.
5. industry_leader is rare. Use vertical_expert unless the content shows
   recognition beyond one niche.

Respond ONLY with compact JSON:
{{"root_type": "<key or {NOT_A_ROOT}>",
  "evidence": "<verbatim quote from the content, <=15 words>",
  "verticals": ["<topic>", ...],
  "confidence": "high|medium|low",
  "note": "<<=20 words>"}}"""


@dataclass
class RootVerdict:
    """One Root-type suggestion. A suggestion, never a decision."""

    root_type: str | None = None
    evidence: str | None = None
    verticals: list[str] = field(default_factory=list)
    confidence: str = "low"
    note: str | None = None
    model: str | None = None
    from_cache: bool = False

    @property
    def is_root(self) -> bool:
        return self.root_type is not None and self.root_type != NOT_A_ROOT

    @property
    def basis(self) -> str | None:
        """The stored, human-readable justification.

        Names the model, because a suggestion whose author is unrecorded
        becomes indistinguishable from a human judgment the moment it is read
        back six months later.
        """
        if self.evidence is None:
            return None
        label = ROOT_TYPE_LABELS_ZH.get(self.root_type or "", self.root_type or "")
        if not self.is_root:
            label = "非 Root（疑为同类创作者）"
        return f'{self.model} 判断为「{label}」，依据：“{self.evidence}”（置信度 {self.confidence}）'


def _parse_root(content: str, model: str) -> RootVerdict:
    """Validate against ``ROOT_TYPES`` + the ``not_a_root`` sentinel.

    A type without a usable quote is downgraded to ``not_a_root`` rather than
    dropped to ``None``: the model did answer, it just did not justify itself,
    and silently returning "no opinion" would hide that from the reviewer.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"model returned non-JSON: {content[:160]}") from exc

    key = str(data.get("root_type", "")).strip()
    quote = str(data.get("evidence", "")).strip()
    confidence = str(data.get("confidence", "low")).strip().lower()
    if key not in ROOT_TYPES and key != NOT_A_ROOT:
        key = NOT_A_ROOT
    if key != NOT_A_ROOT and len(quote) < 3:
        key = NOT_A_ROOT
        quote = quote or "模型未给出可引用依据"

    verticals = [
        v for v in (str(x).strip().lower() for x in (data.get("verticals") or []))
        if v in VERTICALS
    ][:3]

    return RootVerdict(
        root_type=key,
        evidence=quote or None,
        verticals=verticals,
        confidence=confidence if confidence in {"high", "medium", "low"} else "low",
        note=(str(data.get("note", "")).strip() or None),
        model=model,
    )


def classify_root(profile_text: str, *, force: bool = False, timeout: int = 45) -> RootVerdict:
    """Suggest what kind of Root an account is. Never writes a decision.

    Cached on content + model + prompt fingerprint, like the others: editing
    this prompt changes what a verdict means, so old answers must not be reused.
    """
    if not profile_text.strip():
        return RootVerdict(root_type=NOT_A_ROOT, note="no content available to classify")

    provider = resolve_provider()
    model = provider.model
    fingerprint = hashlib.sha256(ROOT_PROMPT.encode()).hexdigest()[:8]
    digest = hashlib.sha256(f"{model}\n{fingerprint}\n{profile_text}".encode()).hexdigest()[:24]
    cache = CACHE_DIR / f"root-{digest}.json"

    if not force and cache.exists() and time.time() - cache.stat().st_mtime <= CACHE_TTL_SECONDS:
        try:
            verdict = _parse_root(cache.read_text(encoding="utf-8"), model)
            verdict.from_cache = True
            return verdict
        except (LLMError, OSError):
            pass

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": ROOT_PROMPT},
            {"role": "user", "content": profile_text},
        ],
        "temperature": 0,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }
    response = _post(payload, provider, timeout)
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"unexpected response shape: {str(response)[:200]}") from exc

    verdict = _parse_root(content, model)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(content, encoding="utf-8")
    return verdict


def build_profile_text(
    display_name: str | None = None,
    bio: str | None = None,
    content_summary: str | None = None,
    posts: list[str] | None = None,
    verticals: str | None = None,
    platform: str | None = None,
) -> str:
    """Assemble the evidence block the model is allowed to reason over.

    Only real, stored content goes in. The display name is included but the
    prompt forbids classifying from it alone, because an account called
    "AI Playbook" tells you a topic and nothing about who watches.
    """
    parts: list[str] = []
    if display_name:
        parts.append(f"Account name: {display_name}")
    if platform:
        parts.append(f"Platform: {platform}")
    if verticals:
        parts.append(f"Topic tags on file: {verticals}")
    if bio:
        parts.append(f"Profile bio/description: {bio.strip()[:600]}")
    if content_summary:
        parts.append(f"Content summary: {content_summary.strip()[:600]}")
    if posts:
        sample = " | ".join(post.strip().replace("\n", " ") for post in posts[:20])
        parts.append(f"Recent posts/videos: {sample[:2500]}")
    return "\n".join(parts)


def _prompt_fingerprint() -> str:
    """Short hash of the system prompt.

    Part of the cache key: editing the prompt changes what a verdict *means*,
    so cached answers from an older prompt must not be silently reused. This
    bit me once already -- a rule added to stop the model citing
    "Brand collaborations open" as audience evidence did not take effect for
    already-cached creators.
    """
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:8]


def _cache_path(profile_text: str, model: str) -> Path:
    digest = hashlib.sha256(
        f"{model}\n{_prompt_fingerprint()}\n{profile_text}".encode()
    ).hexdigest()[:24]
    return CACHE_DIR / f"audience-{digest}.json"


def _post(payload: dict, provider: Provider, timeout: int) -> dict:
    request = Request(
        provider.url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {provider.key}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        # Surface billing/quota plainly -- it is the most common failure and
        # reads as a mysterious 429 otherwise.
        raise LLMError(f"{provider.name} HTTP {exc.code}: {body}") from exc
    except (URLError, TimeoutError) as exc:
        raise LLMError(f"{provider.name} request failed: {str(exc)[:200]}") from exc


def _parse_verdict(content: str, model: str) -> AudienceVerdict:
    """Validate the model's JSON against the closed vocabulary.

    Anything outside ``AUDIENCE_TYPES``, and anything without a usable
    evidence quote, is dropped. The model does not get to widen the schema.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"model returned non-JSON: {content[:160]}") from exc

    types: list[str] = []
    evidence: list[str] = []
    for item in data.get("audience_types") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("type", "")).strip()
        quote = str(item.get("evidence", "")).strip()
        if key not in AUDIENCE_TYPES or key in types:
            continue
        if len(quote) < 3:
            # Unjustified type -- this is the guard that stops free association.
            continue
        types.append(key)
        evidence.append(f'{key}:"{quote[:80]}"')
        if len(types) >= 3:
            break

    confidence = str(data.get("confidence", "low")).strip().lower()
    return AudienceVerdict(
        types=types,
        evidence=evidence,
        confidence=confidence if confidence in {"high", "medium", "low"} else "low",
        note=(str(data.get("note", "")).strip() or None),
        model=model,
    )


def classify_audience(
    profile_text: str,
    *,
    force: bool = False,
    timeout: int = 45,
) -> AudienceVerdict:
    """Classify one creator's audience. Raises ``LLMError`` on API failure.

    An empty ``profile_text`` short-circuits to an empty verdict rather than
    spending a call to be told there is nothing there.
    """
    if not profile_text.strip():
        return AudienceVerdict(note="no content available to classify")

    provider = resolve_provider()
    model = provider.model
    cache = _cache_path(profile_text, model)
    if not force and cache.exists() and time.time() - cache.stat().st_mtime <= CACHE_TTL_SECONDS:
        try:
            verdict = _parse_verdict(cache.read_text(encoding="utf-8"), model)
            verdict.from_cache = True
            return verdict
        except (LLMError, OSError):
            pass  # bad cache entry -- fall through and re-ask

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": profile_text},
        ],
        # Deterministic: the same profile must not drift between runs, or the
        # stored evidence stops matching the stored types.
        "temperature": 0,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }
    response = _post(payload, provider, timeout)
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"unexpected OpenAI response shape: {str(response)[:200]}") from exc

    verdict = _parse_verdict(content, model)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(content, encoding="utf-8")
    return verdict


# =============================================================================
# 账号身份：这个人卖不卖内容位
# =============================================================================
#
# 存在的理由，一句话：**@HesterPeirce（SEC 委员）出现在了客户的可投放候选里。**
#
# 在这之前我用简介关键词清过好几轮 —— CEO、记者、会议号、feedback 邮箱、职务
# 信箱。每一轮都清掉一批，然后下一批数据换个形态又冒出来。关键词分不清「有影响
# 力」和「售卖内容位」，因为这两件事在简介里长得一模一样：都是一个人 + 一份履历。
# 这是语义判断，不是模式匹配，和当初 `OtakuMachine → creators` 是同一类问题。

#: 闭集。``unclear`` 是**正常且频繁的正确答案**，不是兜底。
ACCOUNT_KINDS = ("creator", "media", "public_figure", "organization", "unclear")

#: 买不到的两类。``unclear`` 不在其中 —— 判不出来从不淘汰候选。
NON_BUYABLE_ACCOUNT_KINDS = frozenset({"public_figure", "organization"})

_ACCOUNT_MENU = """- creator: an individual who SELLS ACCESS TO THEIR AUDIENCE. Posts tutorials,
  reviews, threads, videos, newsletters as their work. Takes sponsorships,
  brand deals, paid reviews. This is what Mango can buy.
- media: a publication, newsletter, podcast or news outlet. Sells advertising
  or sponsored placements as inventory. Also buyable, but as media, not as a
  person.
- public_figure: has real influence but does NOT sell placements. Founders,
  CEOs, executives, investors, VCs, regulators, government officials,
  academics, staff journalists at a publication. People you want to REACH,
  not people you can BUY.
- organization: the official account of a company, product, protocol,
  conference, university or institution."""

ACCOUNT_PROMPT = f"""You classify X (Twitter) accounts for Mango Labs, a KOL agency.

Mango buys sponsored content. The only question you answer is:
**Can Mango pay this account to publish something?**

Categories (use the exact key):
{_ACCOUNT_MENU}

CRITICAL — the failure this exists to prevent:
An SEC commissioner, a Fortune 500 CEO, a New York Times reporter and a
conference account all reached a client's "who can we book" list, because each
had a public email in their bio. Influence is not purchasability. A person can
be extremely well known, be followed by everyone in the industry, and still
have never sold a placement in their life.

Decide by ROLE, not by fame, follower count, or having a contact address:
- Runs a company / fund / agency, or holds an executive title -> public_figure
- Invests capital, sits on boards, allocates funds -> public_figure
- Government, regulator, central bank, policy body -> public_figure
- Employed reporter/editor at a named outlet -> public_figure
  (the OUTLET itself would be media, the reporter is not)
- Professor, researcher, lab scientist -> public_figure, unless the bio shows
  they also sell sponsored content
- Their stated work IS making content: tutorials, reviews, threads, videos,
  courses, newsletters -> creator
- A brand, product, protocol, event or institution speaking as "we" ->
  organization

Rules:
1. The answer MUST be justified by a short verbatim quote from the supplied
   content. No quote, no answer -- return "unclear".
2. "unclear" is a normal, frequent, correct answer. Thin bios are common.
   Do not guess. A wrong "creator" puts an unbuyable person in front of a
   paying client; a wrong "unclear" only costs one human minute.
3. Judge ONLY from the supplied content. Do not use outside knowledge about
   who this person is, and never infer from the handle or follower count.
4. Someone can be both -- a founder who also runs a big newsletter. Choose
   what they would be PAID AS. If the bio leads with a company role, that is
   public_figure.
5. A business email, "DM for collabs" or a booking link does NOT make someone
   a creator. Executives publish contact addresses too.

Respond ONLY with compact JSON:
{{"kind": "<key>",
  "evidence": "<verbatim quote from the content, <=15 words>",
  "confidence": "high|medium|low",
  "note": "<<=20 words>"}}"""


@dataclass
class AccountVerdict:
    """一条身份建议。**是建议，不是决定** —— 写进 ``object_kind_suggested``。"""

    kind: str = "unclear"
    evidence: str | None = None
    confidence: str = "low"
    note: str | None = None
    model: str | None = None
    from_cache: bool = False

    @property
    def buyable(self) -> bool | None:
        """能不能买。``None`` = 判不出来，**不等于不能买**。"""
        if self.kind == "unclear":
            return None
        return self.kind not in NON_BUYABLE_ACCOUNT_KINDS

    @property
    def basis(self) -> str:
        """落库的依据串。引用原文 + 置信度，缺一不可复核。"""
        parts = [f"模型判定为 {self.kind}（{self.confidence}）"]
        if self.evidence:
            parts.append(f"依据原文：「{self.evidence}」")
        if self.note:
            parts.append(self.note)
        return "；".join(parts)


def _parse_account(content: str, model: str) -> AccountVerdict:
    """按闭集校验，没有引用就降级为 ``unclear``。

    降级而不是丢弃：模型确实答了，只是没给依据。静默返回「没意见」会让复核的人
    看不出区别，而这两种情况需要不同的后续动作。
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"model returned non-JSON: {content[:160]}") from exc

    kind = str(data.get("kind", "")).strip().lower()
    quote = str(data.get("evidence", "")).strip()
    confidence = str(data.get("confidence", "low")).strip().lower()
    if kind not in ACCOUNT_KINDS:
        kind = "unclear"
    if kind != "unclear" and len(quote) < 3:
        kind = "unclear"
        quote = quote or "模型未给出可引用依据"

    return AccountVerdict(
        kind=kind,
        evidence=quote or None,
        confidence=confidence if confidence in {"high", "medium", "low"} else "low",
        note=(str(data.get("note", "")).strip() or None),
        model=model,
    )


def classify_account(profile_text: str, *, force: bool = False, timeout: int = 45) -> AccountVerdict:
    """判断一个账号卖不卖内容位。缓存键含 prompt 指纹。

    改 prompt 就改变了一条判断的含义，所以旧答案不能复用 —— 这条曾经回归过一次，
    现在由测试守着。
    """
    if not profile_text.strip():
        return AccountVerdict(note="no content available to classify")

    provider = resolve_provider()
    model = provider.model
    fingerprint = hashlib.sha256(ACCOUNT_PROMPT.encode()).hexdigest()[:8]
    digest = hashlib.sha256(f"{model}\n{fingerprint}\n{profile_text}".encode()).hexdigest()[:24]
    cache = CACHE_DIR / f"account-{digest}.json"

    if not force and cache.exists() and time.time() - cache.stat().st_mtime <= CACHE_TTL_SECONDS:
        try:
            verdict = _parse_account(cache.read_text(encoding="utf-8"), model)
            verdict.from_cache = True
            return verdict
        except (LLMError, OSError):
            pass

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": ACCOUNT_PROMPT},
            {"role": "user", "content": profile_text},
        ],
        "temperature": 0,
        "max_tokens": 220,
        "response_format": {"type": "json_object"},
    }
    response = _post(payload, provider, timeout)
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"unexpected response shape: {str(response)[:200]}") from exc

    verdict = _parse_account(content, model)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(content, encoding="utf-8")
    return verdict
