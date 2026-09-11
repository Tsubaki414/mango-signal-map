"""建表 + 补列：待建联候选层。

``create_all`` 只会新建表，不会给已有表补列 —— 这是 SQLAlchemy 的行为，不是
bug，但它意味着「加了一个字段」必须有一个像这样的脚本，否则线上库会安静地
缺列，直到某次写入炸掉。

新表：``bd_candidates``、``commercial_signals``
补列：``contact_methods``（来源与核验）、``procurement_routes``（代理确认程度）

幂等：每一列先查再加，重复运行没有副作用。

    python -m signal_map.scripts.add_bd_candidate_tables
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import inspect  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.models import Base  # noqa: E402

#: ``(表, 列, DDL 类型与默认值)``。默认值一律取「最弱的读法」：
#: 未核验、未推断、公开入口未确认。老数据不会因为加了一列就变得更可信。
_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("x_accounts", "avatar_url", "VARCHAR(500)"),
    ("x_accounts", "median_views", "FLOAT"),
    ("x_accounts", "recent_posts_counted", "INTEGER"),
    ("x_accounts", "latest_post_at", "DATE"),
    ("x_accounts", "posts_observed_at", "DATETIME"),
    ("bd_candidates", "domain_group", "VARCHAR(30)"),
    ("bd_candidates", "object_kind_suggested", "VARCHAR(30)"),
    ("bd_candidates", "verticals_suggested", "VARCHAR(255)"),
    ("bd_candidates", "verticals_suggestion_basis", "TEXT"),
    ("bd_candidates", "object_kind_suggestion_basis", "TEXT"),
    ("bd_candidates", "object_kind_suggested_by", "VARCHAR(120)"),
    ("briefs", "target_root_handles", "TEXT"),
    ("briefs", "domain_group", "VARCHAR(30)"),
    ("contact_methods", "verified_by", "VARCHAR(120)"),
    ("contact_methods", "source_url", "VARCHAR(500)"),
    ("contact_methods", "source_note", "VARCHAR(255)"),
    ("contact_methods", "observed_at", "DATE"),
    ("contact_methods", "is_inferred", "BOOLEAN DEFAULT 0 NOT NULL"),
    ("procurement_routes", "verified_by", "VARCHAR(120)"),
    (
        "procurement_routes", "representation_status",
        "VARCHAR(30) DEFAULT 'public_entry_unverified' NOT NULL",
    ),
    ("procurement_routes", "source_url", "VARCHAR(500)"),
    ("procurement_routes", "contact_value", "VARCHAR(500)"),
)


def main() -> None:
    sm_db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    inspector = inspect(sm_db.engine)
    before = set(inspector.get_table_names())
    Base.metadata.create_all(sm_db.engine)
    after = set(inspect(sm_db.engine).get_table_names())
    for name in sorted(after - before):
        print(f"created table {name}")

    added = 0
    with sm_db.engine.begin() as connection:
        for table, column, ddl in _COLUMNS:
            if table not in after:
                print(f"skip {table}.{column}: table missing")
                continue
            existing = {
                row[1]
                for row in connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            }
            if column in existing:
                continue
            connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            print(f"added column {table}.{column}")
            added += 1

    print(f"\ndone: {len(after - before)} table(s) created, {added} column(s) added")
    if not (after - before) and not added:
        print("schema already current")


if __name__ == "__main__":
    main()
