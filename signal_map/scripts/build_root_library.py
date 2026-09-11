"""按领域建 Root 库 —— 每个名字都过一遍公开资料核验。

为什么不是从 roster centrality 反推
-----------------------------------
AI 那套 Root 库是人给的（iLands 的 62 位）。crypto / finance 没有现成的名单，
而从 Mango 自己的 roster 反推行不通：库里只有 **11 位 crypto、4 位 finance**
创作者，用他们的共同关注去代表整个加密圈，得到的是「Mango 恰好签了谁」的
放大版 —— 这正是 CLAUDE.md 里已经记过一次的教训。

所以走种子路线：``config/root_seeds.json`` 里是**机器提出的候选名单**，这个
脚本逐个调 X 公开资料接口核验，然后落库为 ``pending``。

三条纪律
--------
1. **不建占位记录。** 账号查不到、改过名、私密 —— 一律跳过并报出来。写一条
   ``handle:xxx`` 占位曾经让整张关系图静默返回全零。
2. **``root_type`` 留空，``root_review_status`` 是 ``pending``。** 「谁是这个
   领域的目标人物」是人的判断。种子名单里的 ``why`` 存进
   ``source_notes_raw`` 并永久标 ``unverified``，筛选逻辑一行都不读它。
3. **幂等。** 改完配置重跑即可，已存在的按自然键更新，不会产生第二条。

用法::

    python -m signal_map.scripts.build_root_library --domain crypto --dry-run
    python -m signal_map.scripts.build_root_library --domain crypto
    python -m signal_map.scripts.build_root_library --all
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.bd_models import BDCandidate  # noqa: E402
from signal_map.scripts.collect_follow_graph import _load_dotenv, _upsert_account  # noqa: E402
from signal_map.scripts.collect_target_following import _profile_fields  # noqa: E402

SEEDS_PATH = REPO_ROOT / "config" / "root_seeds.json"
SOURCE_NAME = "root_seed_config"


def load_seeds(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def build(
    session: Session, client, domain: str, config: dict, *, dry_run: bool, force: bool,
) -> Counter:
    stats: Counter = Counter()
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)

    for group in config["groups"]:
        for person in group["people"]:
            handle = person["handle"].lstrip("@")
            stats["seeded"] += 1

            if dry_run:
                print(f"  [{group['name']}] @{handle} — {person['why']}")
                continue

            # 先核验账号真的存在。这一步是种子名单和事实之间唯一的闸门。
            try:
                payload = client.get_user(handle, force=force, cache_ttl_seconds=86_400 * 7)
            except Exception as exc:  # noqa: BLE001 -- 一个查不到不该终止整轮
                print(f"  ! @{handle}: 资料读取失败（{str(exc)[:60]}），跳过")
                stats["unresolved"] += 1
                continue

            profile = _profile_fields(payload)
            rest_id = profile.pop("rest_id", None)
            if not rest_id:
                print(f"  ! @{handle}: 未返回 rest_id，跳过（不建占位记录）")
                stats["unresolved"] += 1
                continue

            resolved = profile.get("display_name") or handle
            account = _upsert_account(
                session, rest_id, handle=handle, role="target", **profile
            )
            # X 会把 handle 规范化；以接口返回的为准，否则后面按 handle join 会漏。
            handle = (account.handle or handle).lower()

            candidate = session.scalar(
                select(BDCandidate).where(
                    BDCandidate.platform == "X",
                    func.lower(BDCandidate.handle) == handle,
                )
            )
            if candidate is None:
                candidate = BDCandidate(
                    platform="X", handle=handle, source_name=SOURCE_NAME,
                    first_ingested_at=now,
                )
                session.add(candidate)
                stats["new"] += 1
            else:
                stats["updated"] += 1

            candidate.display_name = resolved
            candidate.profile_url = f"https://x.com/{account.handle}"
            candidate.object_kind = "target_person"
            candidate.object_kind_basis = (
                f"{config['label']}领域 Root 种子名单，圈层「{group['name']}」；待人工确认"
            )
            candidate.target_group = group["name"]
            candidate.domain_group = domain
            candidate.source_ref = f"root_seeds.json:{domain}/{group['name']}/{handle}"
            candidate.source_notes_raw = f"{person['why']}；圈层说明：{group['note']}"
            # 永远 unverified：种子理由是机器提的，不是核验过的结论。
            candidate.source_claims_status = "unverified"
            candidate.last_seen_in_source_at = now

            followers = account.followers or 0
            print(f"  ✓ @{account.handle:20} {followers:>10,} 粉 · {group['name']}")
            stats["resolved"] += 1

    if not dry_run:
        session.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", help="crypto / finance / ...")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--seeds", type=Path, default=SEEDS_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="绕过资料缓存")
    args = parser.parse_args()

    seeds = load_seeds(args.seeds)
    if not args.all and not args.domain:
        parser.error(f"传 --domain（可选：{', '.join(sorted(seeds))}）或 --all")
    domains = sorted(seeds) if args.all else [args.domain]
    for domain in domains:
        if domain not in seeds:
            parser.error(f"{domain} 不在种子配置里；可选：{', '.join(sorted(seeds))}")

    _load_dotenv()
    client = None
    if not args.dry_run:
        from mangobd.rapid_x import RapidXClient

        client = RapidXClient()

    session = sm_db.get_session()
    try:
        for domain in domains:
            config = seeds[domain]
            print(f"\n=== {domain}（{config['label']}）===")
            stats = build(
                session, client, domain, config,
                dry_run=args.dry_run, force=args.force,
            )
            print("  " + "  ".join(f"{k}={v}" for k, v in sorted(stats.items())))
    finally:
        session.close()

    if args.dry_run:
        print("\n[DRY RUN] 未写入，也未调用任何接口")
    else:
        print(
            "\n下一步：\n"
            "  1. 人工过一遍 —— 这些是 pending 候选，不是已确认 Root。\n"
            "  2. python -m signal_map.scripts.collect_target_following "
            "--from-candidates --domain <领域>\n"
            "     采集他们关注了谁，发现池才会真的按领域变化。"
        )


if __name__ == "__main__":
    main()
