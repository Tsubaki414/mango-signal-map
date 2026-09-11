"""Replace one exact legacy Runway relationship overclaim in ConnectorBrief.

The row is archived research rather than a live relationship source, but the
stored text itself must still not claim that Jennie -> Cristobal was verified.
This migration is exact-match, idempotent, and creates an online SQLite backup
before changing a matching database.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backup_db import backup_database  # noqa: E402
from backend.db import DB_PATH, get_session  # noqa: E402
from backend.models import ConnectorBrief  # noqa: E402


OLD_VALUES = {
    "Ask one structured question per company: actual person, nature of relationship, last interaction, and willingness to make a contextual intro. Runway already has a verified Jennie -> Cristobal path.",
    "针对每家公司都问一个结构化的问题：实际联系的是谁、关系的性质、最近一次互动时间，以及是否愿意做有上下文的引荐。Runway 已经有一条经过核实的 Jennie -> Cristobal 路径。",
}
CORRECTED_VALUE = (
    "针对每家公司问一个结构化问题：实际联系人是谁、关系性质、最近互动，以及是否愿意做有上下文的引荐。"
    "Runway 当前只有 Jennie → Runway/Cristóbal 的单向关注研究线索；尚未确认真实认识或愿意引荐。"
)


def main() -> int:
    session = get_session()
    try:
        rows = (
            session.query(ConnectorBrief)
            .filter(ConnectorBrief.connector_name == "Jennie Liu")
            .filter(ConnectorBrief.best_current_action.in_(OLD_VALUES))
            .all()
        )
        if not rows:
            print("corrected 0 stale connector brief(s)")
            return 0

        backup_database(
            db_path=DB_PATH,
            backup_dir=Path(DB_PATH).parent / "backups",
            label="pre_connector_brief_fix",
        )
        for row in rows:
            row.best_current_action = CORRECTED_VALUE
        session.commit()
        print(f"corrected {len(rows)} stale connector brief(s)")
        return len(rows)
    finally:
        session.close()


if __name__ == "__main__":
    main()
