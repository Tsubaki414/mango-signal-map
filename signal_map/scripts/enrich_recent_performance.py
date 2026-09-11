"""采近期内容表现：播放中位数、最近发布日期。

粉丝数说明的是历史积累，**播放中位数说明的是现在还有多少人在看**。两者经常
差一个量级，而客户判断值不值这个价看的是后者。名单上只有粉丝数的时候，一张卡
读起来像半成品 —— 因为它确实少了最关键的那个数。

两条纪律
--------
* **中位数，不是平均数。** 一条爆款会把均值抬到看不出常态。BD 时期的数据里
  就有这个问题，`SocialAccount` 两个字段都存，客户面只读中位数。
* **只算原创帖。** 转推的播放量属于原作者，混进来会让一个天天转发的账号看起来
  很有量。

只对**能出现在客户名单里的账号**跑：被判定为可投放（``kol`` / ``media_channel``）
且已经有领域的那些。别的账号进不了名单，采了也没人看。

    python -m signal_map.scripts.enrich_recent_performance --dry-run
    python -m signal_map.scripts.enrich_recent_performance --limit 200
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.bd_models import BDCandidate  # noqa: E402
from signal_map.backend.observation_models import XAccount  # noqa: E402
from signal_map.scripts.collect_follow_graph import _load_dotenv  # noqa: E402

#: 一次读多少条。20 条够算一个稳定的中位数，再多只是多花钱。
POSTS_PER_ACCOUNT = 20

#: 结果多久算新鲜。播放量会随时间涨，但一个月内的中位数足够做判断。
FRESH_DAYS = 30


def targets(session: Session, limit: int | None) -> list[XAccount]:
    """能出现在客户名单里、且还没采过近期表现的账号。"""
    buyable = {
        (candidate.handle or "").lower()
        for candidate in session.scalars(select(BDCandidate))
        if candidate.handle
        and candidate.effective_object_kind in ("kol", "media_channel")
        and candidate.verticals_suggested
    }
    if not buyable:
        return []
    cutoff = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=FRESH_DAYS)
    rows = [
        account
        for account in session.scalars(select(XAccount).where(XAccount.handle.is_not(None)))
        if (account.handle or "").lower() in buyable
        and (account.posts_observed_at is None or account.posts_observed_at < cutoff)
    ]
    rows.sort(key=lambda a: -(a.followers or 0))
    return rows[:limit] if limit else rows


def _tweet_stats(payload: dict) -> tuple[list[int], dt.date | None]:
    """从时间线里取出**原创帖**的播放量和最新日期。

    响应嵌套很深且不稳定，所以是递归找 ``views`` 节点，不是硬取路径。转推
    （``retweeted_status_result``）跳过：那条播放量属于原作者。
    """
    views: list[int] = []
    latest: dt.date | None = None

    def walk(node) -> None:
        nonlocal latest
        if isinstance(node, dict):
            counts = node.get("views")
            legacy = node.get("legacy")
            if isinstance(counts, dict) and isinstance(legacy, dict):
                if "retweeted_status_result" not in legacy:
                    raw = counts.get("count")
                    if raw is not None:
                        try:
                            views.append(int(raw))
                        except (TypeError, ValueError):
                            pass
                    stamp = legacy.get("created_at")
                    if stamp:
                        try:
                            when = dt.datetime.strptime(
                                stamp, "%a %b %d %H:%M:%S %z %Y"
                            ).date()
                            latest = max(latest, when) if latest else when
                        except ValueError:
                            pass
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return views, latest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    _load_dotenv()
    session = sm_db.get_session()
    stats: Counter = Counter()
    try:
        accounts = targets(session, args.limit)
        print(f"待采账号：{len(accounts)}")
        if args.dry_run:
            for account in accounts[:20]:
                print(f"  @{account.handle:20} {account.followers or 0:>10,} 粉")
            print("\n[DRY RUN] 未调用接口，也未写入")
            return
        if not accounts:
            return

        from mangobd.rapid_x import RapidXClient

        client = RapidXClient()
        now = dt.datetime.now(dt.UTC).replace(tzinfo=None)

        for index, account in enumerate(accounts, 1):
            try:
                payload = client.get_user_tweets(
                    account.rest_id, count=POSTS_PER_ACCOUNT,
                    force=args.force, cache_ttl_seconds=86_400 * 7,
                )
            except Exception as exc:  # noqa: BLE001 -- 一个失败不该终止整轮
                stats["errors"] += 1
                print(f"  ! @{account.handle}: {str(exc)[:60]}")
                continue

            views, latest = _tweet_stats(payload)
            # 采到了但读不出播放量，也要记下「采过」—— 否则每次重跑都会再花
            # 一次钱问同一个问题。
            account.posts_observed_at = now
            if not views:
                stats["no_views"] += 1
                continue

            account.median_views = float(statistics.median(views))
            account.recent_posts_counted = len(views)
            account.latest_post_at = latest
            stats["updated"] += 1
            age = f"，最近发布 {latest}" if latest else ""
            print(
                f"  [{index}/{len(accounts)}] @{account.handle:20} "
                f"中位播放 {account.median_views:>9,.0f}（{len(views)} 条）{age}"
            )
            if index % 20 == 0:
                session.commit()
        session.commit()
    finally:
        session.close()

    print("\n--- summary ---")
    for key, value in sorted(stats.items()):
        print(f"  {key:14} {value:,}")


if __name__ == "__main__":
    main()
