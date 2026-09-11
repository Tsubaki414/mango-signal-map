"""从**已缓存的**接口响应里回填头像，不发一次新请求。

客户名单上每张卡都空着一个圆圈时，整份交付读起来就像没做完。而这个字段在
Mango 已经付过费的响应里一直躺着 —— 之前只是没有从里面取。

两种响应形状都读：

* 单账号 ``user`` 响应：``…result.data.user.result.avatar.image_url``
* 批量 ``get-users-v2`` 响应：``result[].profile_image_url_https``

``_normal.jpg`` 会被换成 ``_400x400.jpg``。X 的默认尺寸是 48px，放进卡片是糊的；
换后缀不产生任何请求，只是同一张图的另一个尺寸。

    python -m signal_map.scripts.backfill_avatars --dry-run
    python -m signal_map.scripts.backfill_avatars
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.observation_models import XAccount  # noqa: E402

#: **两个**缓存目录。``RapidXClient`` 的默认值是仓库根的 ``data/cache/rapidx``，
#: 但有些脚本显式传了 ``signal_map/data/cache/rapidx``。只扫其中一个，会漏掉
#: 批量资料补采写下的那 8,571 份响应 —— 第一次跑就漏了，只回填出 2,848 个。
CACHE_DIRS = (
    REPO_ROOT / "data" / "cache" / "rapidx",
    REPO_ROOT / "signal_map" / "data" / "cache" / "rapidx",
)

#: 单账号响应的包装层。和 ``collect_target_following`` 同样的坑：``result``
#: 出现两次，必须循环下钻而不是按名单剥一遍。
_WRAPPERS = ("result", "data", "user")


def _descend(node):
    for _ in range(8):
        if not isinstance(node, dict):
            return node
        nested = next((k for k in _WRAPPERS if isinstance(node.get(k), dict)), None)
        if nested is None:
            return node
        node = node[nested]
    return node


def _hi_res(url: str | None) -> str | None:
    """把 48px 的默认尺寸换成 400px。不发请求，只是换个后缀。"""
    if not url:
        return None
    return url.replace("_normal.", "_400x400.")


def avatars_from_cache() -> dict[str, str]:
    """``{rest_id: avatar_url}``，扫一遍本地缓存。"""
    found: dict[str, str] = {}
    for cache_dir in CACHE_DIRS:
        if not cache_dir.exists():
            continue
        for path in cache_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            result = payload.get("result")
            # 批量响应：一个 list，字段是旧版命名。
            if isinstance(result, list):
                for row in result:
                    if not isinstance(row, dict):
                        continue
                    rest_id = str(row.get("id_str") or row.get("id") or "")
                    url = _hi_res(row.get("profile_image_url_https"))
                    if rest_id and url:
                        found[rest_id] = url
                continue

            # 单账号响应：嵌套的 avatar.image_url。
            node = _descend(payload)
            if not isinstance(node, dict):
                continue
            rest_id = str(node.get("rest_id") or node.get("id_str") or "")
            avatar = node.get("avatar")
            url = _hi_res(avatar.get("image_url") if isinstance(avatar, dict) else None)
            if not url:
                legacy = node.get("legacy")
                if isinstance(legacy, dict):
                    url = _hi_res(legacy.get("profile_image_url_https"))
            if rest_id and url:
                found[rest_id] = url
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    avatars = avatars_from_cache()
    print(f"缓存里找到 {len(avatars):,} 个头像（未发任何请求）")

    session = sm_db.get_session()
    stats: Counter = Counter()
    try:
        for chunk_start in range(0, len(avatars), 900):
            chunk = list(avatars)[chunk_start : chunk_start + 900]
            for account in session.scalars(
                select(XAccount).where(XAccount.rest_id.in_(chunk))
            ):
                url = avatars.get(account.rest_id)
                if not url or account.avatar_url == url:
                    stats["unchanged"] += 1
                    continue
                if not args.dry_run:
                    account.avatar_url = url
                stats["updated"] += 1
        if not args.dry_run:
            session.commit()
    finally:
        session.close()

    for key, value in sorted(stats.items()):
        print(f"  {key:12} {value:,}")
    if args.dry_run:
        print("\n[DRY RUN] 未写入")


if __name__ == "__main__":
    main()
