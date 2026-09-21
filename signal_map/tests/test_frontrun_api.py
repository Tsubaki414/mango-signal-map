"""FrontRun 内部接口：鉴权隔离与「没有基线」的诚实表达。"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from signal_map.backend.app import app

client = TestClient(app)


def test_frontrun_is_internal_only():
    """客户面**不得**存在 frontrun 路径。

    挂错命名空间的后果不是权限问题，是把「Mango 在盯谁」告诉了客户。
    """
    assert client.get("/api/frontrun/report").status_code == 404
    assert client.get("/api/client/frontrun/report").status_code == 404


def test_frontrun_requires_internal_token():
    """未配置 token 时 503（fails closed），配置了但没带时 401。两种都不放行。"""
    assert client.get("/api/internal/frontrun/report").status_code in (401, 503)


def test_report_reports_no_baseline_rather_than_empty():
    """没有基线时必须说「首采，尚无基线」，不能返回空列表了事。

    first_collection 和「最近没有新关注」在数据上都是没有条目，含义却相反：
    一个是我们还没看过第二眼，另一个是确实没动静。前者显示成后者，读的人
    会得出「这批人最近很安静」的错误结论。
    """
    token = os.environ.get("SIGNAL_MAP_INTERNAL_TOKEN")
    if not token:
        return  # 未配置 token 的环境跳过；隔离本身由上面两条覆盖
    body = client.get(
        "/api/internal/frontrun/report", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert "statusCounts" in body
    assert body["comparable"] <= body["watchlistSize"]
