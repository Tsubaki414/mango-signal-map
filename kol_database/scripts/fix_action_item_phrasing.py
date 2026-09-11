"""One-time content fix: rewrite the 22 ActionItem.primary_next_action rows
that were phrased as "Solomon 应先向 X 核实..." (hardcoding a specific
person as the executor, and reading as one dense run-on sentence) into a
generic, executor-agnostic numbered checklist.

Idempotent (checks for the old phrasing before rewriting) so it's safe to
run on every boot, same pattern as add_created_at_columns.py -- this is
what actually lets a local data-content fix reach the already-populated
live Railway volume, since a plain redeploy does not touch existing data.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session  # noqa: E402
from backend.models import ActionItem  # noqa: E402

PATTERN = re.compile(
    r"^Solomon 应先向 (?P<connector>\S+) 核实其与 (?P<target>.+?) ?是否存在私人层面的关系、最近是否有过互动，以及是否愿意提供有背景说明的引荐介绍；"
    r"如果只是关注了对方账号，则当天改为联系 (?P<fallback>\S+)，不再假设存在可用的暖介绍关系。$"
)

KITEAI_OLD = (
    "Solomon 以已观察到的 X（推特）直接关注/公开互动作为背景，直接联系 KITE AI（@GoKiteAI），"
    "首先询问当前的合作伙伴关系/增长负责人是谁；不要把公司账号之间的相互关注当作人际关系。"
    "若 3 个工作日内无回应，则改走 https://luma.com/user/usr-DDHoBsvM69ysEt4。"
)
KITEAI_NEW = (
    "1. 以已观察到的 X（推特）关注/公开互动为背景，直接联系 KITE AI 官方账号（@GoKiteAI）。\n"
    "2. 首先询问当前的合作伙伴关系/增长负责人是谁。\n"
    "3. 不要把公司账号之间的相互关注当作真实人际关系。\n"
    "4. 若 3 个工作日内无回应，改走 https://luma.com/user/usr-DDHoBsvM69ysEt4。"
)


def main():
    session = get_session()
    n = 0

    for a in session.query(ActionItem).filter(ActionItem.primary_next_action.like("%Solomon 应先向%")).all():
        m = PATTERN.match(a.primary_next_action)
        if not m:
            print(f"  [skip] id={a.id} did not match expected template, leaving as-is")
            continue
        connector, target, fallback = m.group("connector"), m.group("target"), m.group("fallback")
        joiner = "" if target[-1] in "）」" else " "
        a.primary_next_action = (
            f"1. 核实 {connector} 是否与 {target}{joiner}存在真实的私人关系、最近是否有过互动。\n"
            f"2. 如果关系属实，询问对方是否愿意提供一次有背景说明的引荐。\n"
            f"3. 如果只是双方账号互相关注、并无真实互动，则不要假设暖介绍可用。\n"
            f"4. 改为直接联系 {fallback}。"
        )
        n += 1

    kiteai = session.query(ActionItem).filter(ActionItem.primary_next_action == KITEAI_OLD).one_or_none()
    if kiteai:
        kiteai.primary_next_action = KITEAI_NEW
        n += 1

    session.commit()
    print(f"rewrote {n} action item(s)")


if __name__ == "__main__":
    main()
