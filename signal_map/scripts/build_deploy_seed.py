"""把 168MB 的工作库裁成一个能部署的种子库。

为什么需要这一步
----------------
``signal_map.db`` 是 168MB，而且在 ``.gitignore`` 里 —— Railway 从 Git 构建，
拿不到它。直接把 168MB 塞进镜像也不合适：每次部署都要重传，而里面绝大部分行
客户端一行都读不到。

裁掉的是什么
------------
体积几乎全在两张观察层的表上：``follow_edges``（512,932 行）和 ``x_accounts``
（282,879 行）。但 v3 客户接口只会沿着**配置里那 47 位目标人物**去走关注边，
所以其余的边在客户面永远不可达。

* ``follow_edges``：只保留 source 是这 47 位目标人物的边。
* ``x_accounts``：只保留目标人物本身、上面那些边指向的账号、以及 bd_candidates
  里出现过的账号。
* ``roster_centrality``：v3 接口完全不读，整表丢弃。

**这是部署用的快照，不是备份。** 采集脚本要跑在完整的工作库上；裁过的库跑
``collect_follow_graph`` 会得到残缺结果。

用法::

    python -m signal_map.scripts.build_deploy_seed
    python -m signal_map.scripts.build_deploy_seed --out /tmp/seed.db
"""

from __future__ import annotations

import argparse
import json

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

SOURCE = REPO_ROOT / "signal_map" / "data" / "signal_map.db"
DEFAULT_OUT = REPO_ROOT / "signal_map" / "data" / "deploy_seed.db"
CIRCLES = REPO_ROOT / "config" / "signal_map_circles.json"

#: v3 接口一行都不读，直接丢。
DROP_TABLES = ("roster_centrality", "follow_snapshots")

#: 客户会话不属于种子：它是客户在生产环境里产生的，烧进镜像会在每次
#: 重新播种时把线上的会话覆盖回开发机上的那几条。
#:
#: 其余几张是**永不出门**的内部数据。客户接口一行都不读它们，但种子库会进
#: Git、会躺在一台对外的服务器上，而 Git 历史删不掉。接口有白名单挡着是一回
#: 事，把数据放到它本来就不必去的地方是另一回事 —— 最稳的防泄露是它压根不在
#: 那台机器上。
#:
#: 如果以后要把 Mango 的内部作业面也部署出去，那是一次**单独的、带鉴权的**
#: 部署，用完整库，不走这个种子。
CLEAR_TABLES = (
    "v3_sessions",
    "contact_methods",      # 联系方式：CLAUDE.md「internal only, forever」
    "quote_messages",       # 议价原文
    "procurement_routes",   # 采购路径（路径 2，客户只该看到「由 Mango 对接」）
    "outreach_logs",        # 建联记录
    "internal_tasks",       # 内部工单
)

#: 整列抹掉：报价原话（"Thread post : $150"）。接口只下发档位，
#: 但原话里连金额带语气都在，没有任何理由出现在对外的机器上。
#: ``internal_cost_usd`` 必须留着 —— 服务端要用它算 $ 档位，且从不序列化。
CLEAR_COLUMNS = (("quotes", "raw_segment"), ("quotes", "parse_notes"))


def target_handles() -> list[str]:
    raw = json.loads(CIRCLES.read_text(encoding="utf-8"))
    return [t["handle"].lower() for t in raw["targets"]]


def build(source: Path, out: Path) -> None:
    if out.exists():
        out.unlink()
    print(f"快照 {source.name} ({source.stat().st_size / 1e6:.0f}MB) …")

    # 必须走 SQLite 的备份 API，**不能用 shutil.copy2**。
    #
    # 库跑在 WAL 模式下，最近的写入还在 ``-wal`` 里没落盘。文件复制只拿主库
    # 文件，于是种子库会安静地少掉一批最新改动 —— 实测丢了 v3_sessions 整张表
    # 和 briefs.access_code 这一列，而后者正是 Brief 码的安全修复所在：线上
    # 会变成「码校验根本不存在」，而构建、启动、健康检查全是绿的。
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(out)
    src.backup(dst)
    src.close()
    dst.close()

    db = sqlite3.connect(out)
    db.execute("PRAGMA foreign_keys=OFF")

    handles = target_handles()
    marks = ",".join("?" * len(handles))
    target_ids = [
        r[0]
        for r in db.execute(
            f"SELECT id FROM x_accounts WHERE LOWER(handle) IN ({marks})", handles
        )
    ]
    print(f"目标人物解析到 {len(target_ids)}/{len(handles)} 个账号")

    db.execute("CREATE TEMP TABLE keep_src(id INTEGER PRIMARY KEY)")
    db.executemany("INSERT OR IGNORE INTO keep_src VALUES (?)", [(i,) for i in target_ids])

    before = db.execute("SELECT COUNT(*) FROM follow_edges").fetchone()[0]
    db.execute("DELETE FROM follow_edges WHERE source_id NOT IN (SELECT id FROM keep_src)")
    after = db.execute("SELECT COUNT(*) FROM follow_edges").fetchone()[0]
    print(f"follow_edges  {before:,} -> {after:,}")

    # 保留：目标人物本身 + 边指向的账号 + bd_candidates 里出现过的账号。
    before = db.execute("SELECT COUNT(*) FROM x_accounts").fetchone()[0]
    db.execute(
        """
        DELETE FROM x_accounts WHERE id NOT IN (
            SELECT id FROM keep_src
            UNION SELECT target_id FROM follow_edges
            UNION SELECT x.id FROM x_accounts x
                   JOIN bd_candidates b ON LOWER(b.handle) = LOWER(x.handle)
        )
        """
    )
    after = db.execute("SELECT COUNT(*) FROM x_accounts").fetchone()[0]
    print(f"x_accounts    {before:,} -> {after:,}")

    for table in DROP_TABLES:
        try:
            n = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            db.execute(f"DELETE FROM {table}")
            print(f"{table:<13} {n:,} -> 0（接口不读）")
        except sqlite3.OperationalError:
            pass

    for table in CLEAR_TABLES:
        try:
            n = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            db.execute(f"DELETE FROM {table}")
            print(f"{table:<19} {n:,} -> 0（不出门）")
        except sqlite3.OperationalError:
            pass

    for table, column in CLEAR_COLUMNS:
        try:
            db.execute(f"UPDATE {table} SET {column} = NULL")
            print(f"{table}.{column:<12} 整列抹掉")
        except sqlite3.OperationalError:
            pass

    db.commit()

    # 抹完之后立刻自检。忘了加一张表比加错一张表更容易发生，而且没人会发现。
    leaked = []
    for table in CLEAR_TABLES:
        try:
            if db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                leaked.append(table)
        except sqlite3.OperationalError:
            pass
    for table, column in CLEAR_COLUMNS:
        try:
            if db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL"
            ).fetchone()[0]:
                leaked.append(f"{table}.{column}")
        except sqlite3.OperationalError:
            pass
    if leaked:
        raise SystemExit(f"种子库里仍有内部数据，拒绝产出：{leaked}")
    print("VACUUM …")
    db.execute("VACUUM")
    db.close()
    print(f"\n完成：{out}  {out.stat().st_size / 1e6:.0f}MB")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    build(args.source, args.out)


if __name__ == "__main__":
    main()
