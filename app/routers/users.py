import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_platform
from ..models import LinkCode, User, UserIdentity, UserSettings
from ..schemas.users import (
    LinkCodeResponse,
    LinkRequest,
    ResolveRequest,
    ResolveResponse,
    SettingsModel,
    SettingsUpdate,
)

# unambiguous alphabet (no 0/O, 1/I/L) for codes the user types by hand
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
LINK_CODE_TTL_MINUTES = 15


def _generate_code(length: int = 6) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

router = APIRouter(prefix="/users", tags=["users"])


async def _get_or_create_settings(db: AsyncSession, user_id: int) -> UserSettings:
    settings = await db.get(UserSettings, user_id)
    if settings is None:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


@router.post("/resolve", response_model=ResolveResponse)
async def resolve_user(
    body: ResolveRequest,
    platform: str = Depends(get_platform),
    db: AsyncSession = Depends(get_db),
) -> ResolveResponse:
    """First call every bot makes on /start: identity -> unified user_id."""
    result = await db.execute(
        select(UserIdentity).where(
            UserIdentity.platform == platform,
            UserIdentity.platform_user_id == body.platform_user_id,
        )
    )
    identity = result.scalar_one_or_none()
    is_new = identity is None

    if identity is None:
        user = User(display_name=body.display_name)
        db.add(user)
        await db.flush()
        identity = UserIdentity(
            user_id=user.user_id,
            platform=platform,
            platform_user_id=body.platform_user_id,
        )
        db.add(identity)
        db.add(UserSettings(user_id=user.user_id))
        await db.commit()
        user_id = user.user_id
    else:
        user_id = identity.user_id

    settings = await _get_or_create_settings(db, user_id)
    return ResolveResponse(
        user_id=user_id,
        is_new=is_new,
        settings=SettingsModel(
            quality=settings.quality, make_zip=settings.make_zip, language=settings.language
        ),
    )


@router.post("/{user_id}/link-code", response_model=LinkCodeResponse)
async def create_link_code(
    user_id: int,
    platform: str = Depends(get_platform),
    db: AsyncSession = Depends(get_db),
) -> LinkCodeResponse:
    """Called by the BOT: generate a short one-time code the user can type
    into the app to become the same user (shared history & settings)."""
    if await db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")

    # one active code per user: drop previous ones
    await db.execute(delete(LinkCode).where(LinkCode.user_id == user_id))

    code = _generate_code()
    # regenerate on the (unlikely) collision with another user's active code
    while await db.get(LinkCode, code) is not None:
        code = _generate_code()

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=LINK_CODE_TTL_MINUTES)
    db.add(LinkCode(code=code, user_id=user_id, expires_at=expires_at))
    await db.commit()
    return LinkCodeResponse(
        code=code,
        expires_at=expires_at.isoformat(),
        expires_in_seconds=LINK_CODE_TTL_MINUTES * 60,
    )


@router.post("/link", response_model=ResolveResponse)
async def link_with_code(
    body: LinkRequest,
    platform: str = Depends(get_platform),
    db: AsyncSession = Depends(get_db),
) -> ResolveResponse:
    """Called by the APP: exchange a link code for the bot user's user_id.
    The app's device identity is attached to that user from now on."""
    link = await db.get(LinkCode, body.code.strip().upper())
    if link is None:
        raise HTTPException(status_code=404, detail="Invalid link code")
    if _as_utc(link.expires_at) < datetime.now(timezone.utc):
        await db.delete(link)
        await db.commit()
        raise HTTPException(status_code=410, detail="Link code expired")

    user_id = link.user_id

    # attach (or re-point) this device identity to the bot user
    result = await db.execute(
        select(UserIdentity).where(
            UserIdentity.platform == platform,
            UserIdentity.platform_user_id == body.platform_user_id,
        )
    )
    identity = result.scalar_one_or_none()
    if identity is None:
        db.add(
            UserIdentity(
                user_id=user_id,
                platform=platform,
                platform_user_id=body.platform_user_id,
            )
        )
    else:
        identity.user_id = user_id

    user = await db.get(User, user_id)
    if user is not None and body.display_name and not user.display_name:
        user.display_name = body.display_name

    await db.delete(link)  # one-time use
    await db.commit()

    settings = await _get_or_create_settings(db, user_id)
    return ResolveResponse(
        user_id=user_id,
        is_new=False,
        settings=SettingsModel(
            quality=settings.quality, make_zip=settings.make_zip, language=settings.language
        ),
    )


@router.get("/{user_id}/settings", response_model=SettingsModel)
async def get_settings(
    user_id: int,
    platform: str = Depends(get_platform),
    db: AsyncSession = Depends(get_db),
) -> SettingsModel:
    if await db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    settings = await _get_or_create_settings(db, user_id)
    return SettingsModel(
        quality=settings.quality, make_zip=settings.make_zip, language=settings.language
    )


@router.patch("/{user_id}/settings", response_model=SettingsModel)
async def update_settings(
    user_id: int,
    body: SettingsUpdate,
    platform: str = Depends(get_platform),
    db: AsyncSession = Depends(get_db),
) -> SettingsModel:
    if await db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    settings = await _get_or_create_settings(db, user_id)
    if body.quality is not None:
        if body.quality not in ("MP3_128", "MP3_320", "FLAC"):
            raise HTTPException(status_code=400, detail="quality must be MP3_128/MP3_320/FLAC")
        settings.quality = body.quality
    if body.make_zip is not None:
        settings.make_zip = body.make_zip
    if body.language is not None:
        settings.language = body.language
    await db.commit()
    return SettingsModel(
        quality=settings.quality, make_zip=settings.make_zip, language=settings.language
    )
