from fastapi import Depends, Header, HTTPException, Query, status

from .config import get_settings
from .db import get_db  # noqa: F401  (re-exported for routers)


async def get_platform(
    x_client_key: str | None = Header(default=None, alias="X-Client-Key"),
    key: str | None = Query(default=None, include_in_schema=False),
) -> str:
    """Resolve the calling client's platform (telegram / bale / android / ...) from its API key.

    - If CLIENT_KEYS is empty in .env -> auth is DISABLED, every request passes
      (platform = "default").
    - Otherwise the key is read from the X-Client-Key header, or from a `?key=`
      query param as a fallback for players/download managers that cannot set headers.
    """
    mapping = get_settings().client_key_map()
    if not mapping:
        return "default"  # no keys configured -> open API

    token = x_client_key or key
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Client-Key header",
        )
    platform = mapping.get(token)
    if not platform:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client key",
        )
    return platform


PlatformDep = Depends(get_platform)
