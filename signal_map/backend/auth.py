"""两个 API 命名空间共用的鉴权。

放在单独模块里，是因为它现在有两个消费者（``app`` 和 ``projects_api``），而
**两处各写一份 token 校验是不能接受的**：其中一处漏掉一个检查，不会有任何报错，
只会安静地多开一扇门。同一个理由已经写在 ``db.session_dependency`` 上 —— 一个
覆盖点，不是两个。
"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import session_dependency
from .models import Client


def bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


def current_client(
    authorization: str | None = Header(default=None),
    session: Session = Depends(session_dependency),
) -> Client:
    """Resolve the calling client, or 401.

    Everything downstream is scoped to this object. There is deliberately no
    "client_id" request parameter anywhere in the client API -- if there were,
    a caller could ask for someone else's data.
    """
    token = bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    client = session.scalar(select(Client).where(Client.api_token == token))
    if client is None:
        raise HTTPException(status_code=401, detail="invalid token")
    return client


def require_internal(authorization: str | None = Header(default=None)) -> str:
    """Guard for Mango's own surface.

    Fails closed: with no ``SIGNAL_MAP_INTERNAL_TOKEN`` configured the internal
    API is unavailable rather than open.
    """
    expected = os.environ.get("SIGNAL_MAP_INTERNAL_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="internal API not configured")
    token = bearer(authorization)
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid internal token")
    return token
