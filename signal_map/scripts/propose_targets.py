"""从已判定的公众人物里，提名一批目标人物候选。

为什么是这个池子
----------------
目标人物的定义是「有影响力但**不卖内容位**」—— 而这正是
``classify_account_roles`` 判定的 ``public_figure``。所以那 8,781 个
public_figure 本来就是目标人物的自然候选池，不必另起一套判定。

为什么不按粉丝量排
------------------
按粉丝量排出来是奥巴马、C 罗、金·卡戴珊 —— 有名，但和 AI / 加密 / 金融科技
项目毫无关系。这里用「**被现有目标人物共同关注的数量**」排序：让现有目标
人物给新目标人物背书，结果天然落在同一个生态里。

调用顺序是有意的
----------------
LLM 判断便宜（一次调用一个账号），X 关注列表采集贵（一个账号一次完整分页）。
所以**先 LLM 后采集**：模型先把明显不是目标人物的剔掉，只对通过的人花采集
额度。反过来做要多花几倍。

写什么、不写什么
----------------
只写提案文件，**不碰配置，也不碰 root_type / root_review_status**。谁是客户
想影响的人必须由人定 —— 模型的作用是把审阅的那一分钟变便宜，不是替人决定。

用法::

    python -m signal_map.scripts.propose_targets --group ai --limit 20
    python -m signal_map.scripts.propose_targets --all --min-endorse 6
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sqlalchemy import text  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.llm_classify import (  # noqa: E402
    NOT_A_ROOT,
    ROOT_TYPE_LABELS_ZH,
    LLMError,
    classify_root,
)

CONFIG = REPO_ROOT / "config" / "signal_map_circles.json"
OUT = REPO_ROOT / "signal_map" / "docs" / "目标人物候选_待勾选.md"

#: 关注数超过这个值时，「被他关注」的含金量下降 —— 他可能只是关注得多。
PROMISCUOUS_FOLLOWING = 5000


def _load_dotenv() -> None:
    env = REPO_ROOT / ".env"
    if not env.exists():
        return
    import os

    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


CANDIDATE_SQL = text(
    """
    WITH tg AS (
        SELECT x.id FROM x_accounts x
        WHERE LOWER(x.handle) IN (SELECT value FROM json_each(:handles))
    ),
    cnt AS (
        SELECT f.target_id, COUNT(DISTINCT f.source_id) n
        FROM follow_edges f JOIN tg ON tg.id = f.source_id
        WHERE f.disappeared_at IS NULL
        GROUP BY f.target_id
        HAVING COUNT(DISTINCT f.source_id) >= :min_endorse
    )
    SELECT x.id, x.handle, x.followers, x.following, cnt.n, x.bio
    FROM cnt
    JOIN x_accounts x ON x.id = cnt.target_id
    JOIN bd_candidates b ON LOWER(b.handle) = LOWER(x.handle)
    WHERE COALESCE(NULLIF(b.object_kind, 'unknown'), b.object_kind_suggested) = 'public_figure'
      AND x.bio IS NOT NULL AND x.bio <> ''
    ORDER BY cnt.n DESC, x.followers DESC
    LIMIT :cap
    """
)

REACH_SQL = text(
    """
    SELECT COUNT(DISTINCT s.creator_id)
    FROM follow_edges fe
    JOIN x_accounts x2 ON x2.id = fe.target_id
    JOIN social_accounts s ON s.platform_uid = x2.rest_id
    WHERE fe.source_id = :aid AND fe.disappeared_at IS NULL
      AND s.creator_id IN (SELECT creator_id FROM quotes WHERE internal_cost_usd IS NOT NULL)
    """
)


def propose(group: str, *, min_endorse: int, cap: int, judge: bool) -> list[dict]:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    have = {t["handle"].lower() for t in cfg["targets"]}
    handles = [t["handle"].lower() for t in cfg["targets"] if t["group"] == group]

    session = sm_db.get_session()
    rows = session.execute(
        CANDIDATE_SQL,
        {"handles": json.dumps(handles), "min_endorse": min_endorse, "cap": cap * 3},
    ).all()

    out: list[dict] = []
    for aid, handle, followers, following, endorse, bio in rows:
        if handle.lower() in have:
            continue
        reach = session.execute(REACH_SQL, {"aid": aid}).scalar() or 0
        item = {
            "handle": handle,
            "followers": followers or 0,
            "following": following or 0,
            "endorse": endorse,
            "reach": reach,
            "bio": (bio or "").replace("\n", " ")[:90],
            "type": None,
            "basis": None,
        }
        if judge:
            try:
                verdict = classify_root(f"@{handle}\n{bio}")
                # not_a_root 是**有价值的答案**，不是失败。多数公众人物并不是
                # 这个客户想影响的人。
                if verdict.root_type and verdict.root_type != NOT_A_ROOT:
                    item["type"] = ROOT_TYPE_LABELS_ZH.get(
                        verdict.root_type, verdict.root_type
                    )
                    item["basis"] = verdict.evidence
                    item["confidence"] = verdict.confidence
            except LLMError as exc:  # 判不出来就留空，不猜
                item["basis"] = f"（判定失败：{exc}）"
        out.append(item)
        if len(out) >= cap:
            break
    session.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--group", default="ai")
    ap.add_argument("--all", action="store_true", help="三个领域都跑")
    ap.add_argument("--min-endorse", type=int, default=5)
    ap.add_argument("--limit", type=int, default=18)
    ap.add_argument("--no-llm", action="store_true", help="只出清单，不调模型")
    args = ap.parse_args()

    _load_dotenv()
    groups = ["ai", "crypto", "finance"] if args.all else [args.group]
    result = {
        g: propose(g, min_endorse=args.min_endorse, cap=args.limit, judge=not args.no_llm)
        for g in groups
    }
    for g, items in result.items():
        judged = [i for i in items if i["type"]]
        print(f"{g}: {len(items)} 位候选，模型认为其中 {len(judged)} 位像目标人物")
        for i in judged:
            print(f"   @{i['handle']:<20} {i['type']:<10} 背书 {i['endorse']:>2}  依据：{(i['basis'] or '')[:40]}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.with_suffix(".json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n明细写入 {OUT.with_suffix('.json')}")


if __name__ == "__main__":
    main()
