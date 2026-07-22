"""All bot handlers - full parity with the old Spotizer bot, backed by the API."""
import asyncio
import logging
import math
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import keyboards as kb
import topics
import views
from api_client import APIError, api
from states import PlaylistCreationStates

logger = logging.getLogger("spotizer.bot")
router = Router()

URL_RE = re.compile(r"https?://\S+")

POLL_INTERVAL = 2.5  # seconds
POLL_MAX_TRIES = 240  # ~10 minutes per job
SEARCH_PAGE_SIZE = 5
PLAYLIST_PAGE_SIZE = 5

# telegram_user_id -> api user_id (resolve is idempotent, cheap cache)
_user_ids: dict[int, int] = {}
# short query id -> query text (callback_data is limited to 64 bytes)
_queries: dict[str, str] = {}
# discography selection sessions
_disc_sessions: dict[str, dict] = {}
# progress message id -> session id (for the Cancel Download button)
_disc_by_msg: dict[int, str] = {}


async def _user_id_for(tg_user) -> int:
    uid = _user_ids.get(tg_user.id)
    if uid is None:
        data = await api.resolve_user(str(tg_user.id), tg_user.full_name)
        uid = data["user_id"]
        _user_ids[tg_user.id] = uid
    return uid


def _remember_query(query: str) -> str:
    qid = uuid.uuid4().hex[:10]
    _queries[qid] = query
    if len(_queries) > 2000:  # basic bound
        for key in list(_queries)[:1000]:
            _queries.pop(key, None)
    return qid


# ======================================================================
# Commands
# ======================================================================


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    try:
        await _user_id_for(message.from_user)
    except APIError:
        await message.reply("Error registering user. Please try again.")
        return
    await message.reply(views.WELCOME_MESSAGE)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.reply(views.HELP_MESSAGE, parse_mode="Markdown")


@router.message(Command("about"))
async def cmd_about(message: Message) -> None:
    await message.reply(views.ABOUT_MESSAGE, parse_mode="Markdown")


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    try:
        user_id = await _user_id_for(message.from_user)
        settings = await api.get_user_settings(user_id)
    except APIError:
        await message.reply("Error accessing settings. Please try again.")
        return
    await message.reply("\u2699\ufe0f Your settings:", reply_markup=kb.settings_keyboard(settings))


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    try:
        user_id = await _user_id_for(message.from_user)
        data = await api.get_history(user_id, page=1, page_size=5)
    except APIError:
        await message.reply("Error retrieving download history.")
        return
    await message.reply(views.format_download_history(data.get("items", [])))


@router.message(Command("link"))
async def cmd_link(message: Message) -> None:
    user_id = await _user_id_for(message.from_user)
    data = await api.create_link_code(user_id)
    minutes = data.get("expires_in_seconds", 900) // 60
    await message.answer(
        "\U0001f517 Your app link code:\n\n"
        f"<code>{data['code']}</code>\n\n"
        f"Enter this code in the app. (valid for {minutes} minutes, single use)",
        parse_mode="HTML",
    )


@router.message(Command("newplaylist"))
async def cmd_newplaylist(message: Message, state: FSMContext) -> None:
    await state.set_state(PlaylistCreationStates.waiting_for_name)
    await message.reply(views.PLAYLIST_CREATION_MESSAGE)


@router.message(Command("playlists"))
async def cmd_playlists(message: Message) -> None:
    try:
        user_id = await _user_id_for(message.from_user)
        playlists = await api.get_playlists(user_id)
    except APIError:
        await message.reply("Error retrieving playlists.")
        return
    if playlists:
        await message.reply(
            views.CHOOSE_PLAYLIST_MESSAGE, reply_markup=kb.playlists_list_keyboard(playlists)
        )
    else:
        await message.reply("You have no playlists.")


@router.message(Command("recommend"))
async def cmd_recommend(message: Message) -> None:
    user_id = await _user_id_for(message.from_user)
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    processing = await message.reply(views.RECOMMEND_THINKING)
    try:
        data = await api.get_recommendations(user_id)
    except APIError:
        await processing.delete()
        await message.reply("Error getting recommendations.")
        return
    await processing.delete()
    if not data.get("has_history"):
        await message.reply(views.RECOMMEND_NO_HISTORY)
        return
    tracks = data.get("tracks", [])
    if not tracks:
        await message.reply("Couldn't find any recommendations right now. Try again later!")
        return
    await message.reply(
        views.RECOMMEND_HEADER,
        reply_markup=kb.recommendations_keyboard(tracks),
        parse_mode="Markdown",
    )


@router.message(Command("subscriptions"))
async def cmd_subscriptions(message: Message) -> None:
    try:
        user_id = await _user_id_for(message.from_user)
        subs = await api.get_subscriptions(user_id)
    except APIError:
        await message.reply("Error loading subscriptions.")
        return
    if not subs:
        await message.reply(views.NO_SUBSCRIPTIONS_MESSAGE)
        return
    await message.reply(
        views.SUBSCRIPTIONS_HEADER,
        reply_markup=kb.subscriptions_keyboard(subs),
        parse_mode="Markdown",
    )


# ======================================================================
# Playlist creation FSM
# ======================================================================


@router.message(StateFilter(PlaylistCreationStates.waiting_for_name), F.text)
async def on_playlist_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    await state.clear()
    try:
        user_id = await _user_id_for(message.from_user)
        await api.create_playlist(user_id, name)
    except APIError:
        await message.reply("Error creating playlist. Please try again.")
        return
    await message.reply(views.playlist_created(name))


@router.message(StateFilter(PlaylistCreationStates.waiting_for_name_with_track), F.text)
async def on_playlist_name_with_track(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    data = await state.get_data()
    track_id = data.get("track_id")
    await state.clear()
    try:
        user_id = await _user_id_for(message.from_user)
        await api.create_playlist(user_id, name, track_id=track_id)
    except APIError:
        await message.reply("Error creating playlist. Please try again.")
        return
    await message.reply(views.playlist_created(name))


# ======================================================================
# Media messages
# ======================================================================


@router.message(F.audio | F.document | F.voice)
async def on_media(message: Message) -> None:
    await message.reply(views.MEDIA_REPLY)


# ======================================================================
# Links -> download with user's settings (like the old bot)
# ======================================================================


@router.message(StateFilter(None), F.text.regexp(URL_RE))
async def on_link(message: Message) -> None:
    url = URL_RE.search(message.text).group(0)
    try:
        info = await api.resolve_link(url)
    except APIError:
        await message.reply(views.ERROR_MESSAGES["invalid_url"])
        return
    await _download_and_send(message, message.from_user, info["content_type"], info["source_id"])


# ======================================================================
# Plain text -> search
# ======================================================================


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def on_text(message: Message) -> None:
    query = message.text.strip()
    if not query:
        return
    qid = _remember_query(query)
    await message.reply(views.search_prompt(query), reply_markup=kb.search_type_keyboard(qid))


async def _render_search(cb: CallbackQuery, search_type: str, qid: str, page: int) -> None:
    query = _queries.get(qid)
    if not query:
        await cb.answer("This search has expired. Please send your query again.", show_alert=True)
        return
    try:
        data = await api.search(
            query, type_=search_type, limit=SEARCH_PAGE_SIZE, offset=(page - 1) * SEARCH_PAGE_SIZE
        )
    except APIError:
        await cb.answer(views.ERROR_MESSAGES["general"], show_alert=True)
        return
    results = data.get("results", [])
    if not results and page == 1:
        await cb.message.edit_text(f"No {search_type}s found for '{query}'.")
        await cb.answer()
        return
    if not results:
        await cb.answer("No more results.", show_alert=True)
        return
    await cb.message.edit_text(
        views.search_results_text(query, search_type, page),
        reply_markup=kb.search_results_keyboard(results, search_type, page, qid),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("search:"))
async def cb_search(cb: CallbackQuery) -> None:
    _, search_type, qid = cb.data.split(":", 2)
    await _render_search(cb, search_type, qid, 1)


@router.callback_query(F.data.startswith("page:"))
async def cb_page(cb: CallbackQuery) -> None:
    _, page, search_type, qid = cb.data.split(":", 3)
    await _render_search(cb, search_type, qid, int(page))


# ======================================================================
# Cards (select:)
# ======================================================================


async def _send_card(message: Message, text: str, cover: str | None, markup) -> None:
    if cover:
        try:
            await message.answer_photo(cover, caption=text, reply_markup=markup, parse_mode="Markdown")
            return
        except Exception:  # noqa: BLE001 - bad cover URL etc.
            pass
    await message.answer(text, reply_markup=markup, parse_mode="Markdown")


@router.callback_query(F.data.startswith("select:"))
async def cb_select(cb: CallbackQuery) -> None:
    _, item_type, item_id = cb.data.split(":", 2)
    await cb.answer()
    try:
        if item_type == "track":
            t = await api.get_track(item_id)
            await _send_card(
                cb.message, views.track_info(t), t.get("cover_xl") or t.get("cover_big"), kb.track_keyboard(t)
            )
        elif item_type == "album":
            a = await api.get_album(item_id)
            await _send_card(
                cb.message, views.album_info(a), a.get("cover_xl") or a.get("cover_big"), kb.album_keyboard(a)
            )
        elif item_type == "playlist":
            p = await api.get_playlist(item_id)
            await _send_card(
                cb.message, views.playlist_info(p), p.get("picture_xl") or p.get("picture_big"), kb.playlist_keyboard(p)
            )
        elif item_type == "artist":
            a = await api.get_artist(item_id)
            await _send_card(
                cb.message, views.artist_info(a), a.get("picture_xl") or a.get("picture_big"), kb.artist_keyboard(a)
            )
    except APIError:
        await cb.message.answer(views.ERROR_MESSAGES["general"])


# ======================================================================
# Lists (view:)
# ======================================================================


@router.callback_query(F.data.startswith("view:"))
async def cb_view(cb: CallbackQuery) -> None:
    _, content_type, action, spoid, page = cb.data.split(":", 4)
    page = int(page)
    await cb.answer()
    try:
        if content_type == "album":
            album = await api.get_album(spoid)
            items = album.get("tracks", [])
            header = views.list_header("album", action, album.get("title"), album.get("artist"))
        elif content_type == "playlist":
            playlist = await api.get_playlist(spoid)
            items = playlist.get("tracks", [])
            header = views.list_header("playlist", action, playlist.get("title"))
        else:  # artist
            artist = await api.get_artist(spoid)
            name = artist.get("name")
            if action == "top_tracks":
                items = await api.get_artist_top(spoid, limit=50)
            elif action == "album":
                items = await api.get_artist_albums(spoid)
            else:  # related
                items = await api.get_artist_related(spoid, limit=50)
            header = views.list_header("artist", action, name)
    except APIError:
        await cb.message.answer(views.ERROR_MESSAGES["general"])
        return

    if not items:
        await cb.message.answer("Nothing found here.")
        return

    markup = kb.list_keyboard(items, content_type, action, spoid, page)
    try:
        await cb.message.edit_text(header, reply_markup=markup)
    except Exception:  # noqa: BLE001 - source message is a photo card
        await cb.message.answer(header, reply_markup=markup)


# ======================================================================
# Settings callbacks
# ======================================================================


@router.callback_query(F.data.startswith("setting:"))
async def cb_setting(cb: CallbackQuery) -> None:
    action = cb.data.split(":", 1)[1]
    user_id = await _user_id_for(cb.from_user)
    settings = await api.get_user_settings(user_id)
    if action == "change_quality":
        await cb.message.edit_reply_markup(
            reply_markup=kb.quality_keyboard(settings.get("quality", "MP3_320"))
        )
        await cb.answer()
    elif action == "toggle_zip":
        new_value = not settings.get("make_zip", True)
        settings = await api.update_user_settings(user_id, make_zip=new_value)
        await cb.message.edit_reply_markup(reply_markup=kb.settings_keyboard(settings))
        await cb.answer(f"Make ZIP: {'Yes' if new_value else 'No'}")


@router.callback_query(F.data.startswith("set_quality:"))
async def cb_set_quality(cb: CallbackQuery) -> None:
    choice = cb.data.split(":", 1)[1]
    user_id = await _user_id_for(cb.from_user)
    if choice == "back":
        settings = await api.get_user_settings(user_id)
        await cb.message.edit_reply_markup(reply_markup=kb.settings_keyboard(settings))
        await cb.answer()
        return
    await api.update_user_settings(user_id, quality=choice)
    await cb.message.edit_reply_markup(reply_markup=kb.quality_keyboard(choice))
    await cb.answer(f"Quality set to {choice} \u2705")


# ======================================================================
# Ratings
# ======================================================================


@router.callback_query(F.data.startswith("rate:"))
async def cb_rate(cb: CallbackQuery) -> None:
    _, action, download_id = cb.data.split(":", 2)
    rating = 1 if action == "like" else -1
    user_id = await _user_id_for(cb.from_user)
    try:
        await api.rate_download(user_id, int(download_id), rating)
    except APIError:
        await cb.answer(views.ERROR_MESSAGES["general"], show_alert=True)
        return
    await cb.answer(views.RATED_LIKE if rating == 1 else views.RATED_DISLIKE)
    try:
        await cb.message.edit_reply_markup(
            reply_markup=kb.rating_keyboard(int(download_id), rating)
        )
    except Exception:  # noqa: BLE001 - markup unchanged
        pass


# ======================================================================
# Subscriptions (follow artists)
# ======================================================================


@router.callback_query(F.data.startswith("sub:"))
async def cb_sub(cb: CallbackQuery) -> None:
    _, action, artist_id = cb.data.split(":", 2)
    user_id = await _user_id_for(cb.from_user)
    if action == "add":
        try:
            res = await api.subscribe(user_id, artist_id)
        except APIError:
            await cb.answer(views.ERROR_MESSAGES["general"], show_alert=True)
            return
        if res.get("already_subscribed"):
            await cb.answer(views.ALREADY_SUBSCRIBED, show_alert=True)
        else:
            await cb.answer(views.subscribed(res.get("artist_name") or "this artist"), show_alert=True)
    else:  # remove
        try:
            await api.unsubscribe(user_id, artist_id)
        except APIError:
            pass
        await cb.answer(views.UNSUBSCRIBED)
        subs = await api.get_subscriptions(user_id)
        try:
            if subs:
                await cb.message.edit_reply_markup(reply_markup=kb.subscriptions_keyboard(subs))
            else:
                await cb.message.edit_text(views.NO_SUBSCRIPTIONS_MESSAGE)
        except Exception:  # noqa: BLE001
            pass


# ======================================================================
# Topics
# ======================================================================


@router.callback_query(F.data.startswith("mktopic:"))
async def cb_mktopic(cb: CallbackQuery) -> None:
    _, content_type, item_id = cb.data.split(":", 2)
    try:
        if content_type == "album":
            title = (await api.get_album(item_id)).get("title")
        elif content_type == "playlist":
            title = (await api.get_playlist(item_id)).get("title")
        else:
            title = (await api.get_artist(item_id)).get("name")
    except APIError:
        await cb.answer(views.ERROR_MESSAGES["general"], show_alert=True)
        return
    thread_id, created = await topics.get_or_create_topic(
        cb.message.bot, cb.message.chat.id, content_type, item_id, title or "Downloads"
    )
    if thread_id is None:
        await cb.answer(
            "Could not create a topic here. Topics only work in forum chats; "
            "files will be sent to the main chat instead.",
            show_alert=True,
        )
    elif created:
        await cb.answer(f"\U0001f9f5 Topic '{title}' created! Downloads will be sent there.", show_alert=True)
    else:
        await cb.answer("Topic already exists. Downloads will be sent there.", show_alert=True)


# ======================================================================
# Personal playlists callbacks
# ======================================================================


async def _render_playlist_tracks(cb: CallbackQuery, playlist_id: int, page: int) -> None:
    user_id = await _user_id_for(cb.from_user)
    data = await api.get_playlist_tracks(user_id, playlist_id)
    tracks = data.get("tracks", [])
    total_pages = max(1, math.ceil(len(tracks) / PLAYLIST_PAGE_SIZE))
    page = min(max(1, page), total_pages)
    text = views.playlist_tracks_text(data.get("name"), tracks, page, total_pages, PLAYLIST_PAGE_SIZE)
    start = (page - 1) * PLAYLIST_PAGE_SIZE
    markup = kb.playlist_tracks_keyboard(
        tracks[start : start + PLAYLIST_PAGE_SIZE], playlist_id, page, total_pages
    )
    try:
        await cb.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    except Exception:  # noqa: BLE001
        await cb.message.answer(text, reply_markup=markup, parse_mode="Markdown")


@router.callback_query(F.data.startswith("select_playlist:"))
async def cb_select_playlist(cb: CallbackQuery) -> None:
    playlist_id = int(cb.data.split(":", 1)[1])
    user_id = await _user_id_for(cb.from_user)
    playlists = await api.get_playlists(user_id)
    playlist = next((p for p in playlists if p.get("playlist_id") == playlist_id), None)
    if playlist is None:
        await cb.answer("Playlist not found.", show_alert=True)
        return
    text = f"\U0001f3b6 *{playlist.get('name')}*\nTotal: {playlist.get('track_count', 0)} track(s)"
    try:
        await cb.message.edit_text(
            text, reply_markup=kb.playlist_details_keyboard(playlist_id), parse_mode="Markdown"
        )
    except Exception:  # noqa: BLE001
        await cb.message.answer(
            text, reply_markup=kb.playlist_details_keyboard(playlist_id), parse_mode="Markdown"
        )
    await cb.answer()


@router.callback_query(F.data.startswith("playlist:"))
async def cb_playlist(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    action = parts[1]
    user_id = await _user_id_for(cb.from_user)

    if action == "add" and parts[2] == "get_playlist":
        # "➕ Add to Playlist" pressed on a track card
        track_id = parts[3]
        playlists = await api.get_playlists(user_id)
        text = views.ADD_TO_PLAYLIST_MESSAGE if playlists else views.NO_PLAYLISTS_MESSAGE
        await cb.message.answer(text, reply_markup=kb.add_to_playlist_keyboard(playlists, track_id))
        await cb.answer()

    elif action == "new_and_add":
        track_id = parts[2]
        await state.set_state(PlaylistCreationStates.waiting_for_name_with_track)
        await state.update_data(track_id=track_id)
        await cb.message.answer(views.PLAYLIST_CREATION_WITH_TRACK_MESSAGE)
        await cb.answer()

    elif action == "add":
        playlist_id, track_id = int(parts[2]), parts[3]
        try:
            res = await api.add_playlist_track(user_id, playlist_id, track_id)
        except APIError:
            await cb.answer(views.ERROR_MESSAGES["general"], show_alert=True)
            return
        if res.get("already_exists"):
            await cb.answer("This track is already in that playlist.", show_alert=True)
        else:
            await cb.answer("\u2705 Added to playlist!", show_alert=True)

    elif action in ("view_tracks", "page"):
        playlist_id = int(parts[2])
        page = int(parts[3]) if action == "page" else 1
        await _render_playlist_tracks(cb, playlist_id, page)
        await cb.answer()

    elif action == "remove_track":
        playlist_id, playlist_track_id = int(parts[2]), int(parts[3])
        try:
            await api.remove_playlist_track(user_id, playlist_id, playlist_track_id)
        except APIError:
            pass
        await cb.answer("Track removed.")
        await _render_playlist_tracks(cb, playlist_id, 1)

    elif action == "delete":
        playlist_id = int(parts[2])
        try:
            await api.delete_playlist(user_id, playlist_id)
        except APIError:
            await cb.answer(views.ERROR_MESSAGES["general"], show_alert=True)
            return
        await cb.answer("\U0001f5d1 Playlist deleted.")
        playlists = await api.get_playlists(user_id)
        if playlists:
            await cb.message.edit_text(
                views.CHOOSE_PLAYLIST_MESSAGE, reply_markup=kb.playlists_list_keyboard(playlists)
            )
        else:
            await cb.message.edit_text("You have no playlists.")

    elif action == "back_to_list":
        playlists = await api.get_playlists(user_id)
        if playlists:
            await cb.message.edit_text(
                views.CHOOSE_PLAYLIST_MESSAGE, reply_markup=kb.playlists_list_keyboard(playlists)
            )
        else:
            await cb.message.edit_text("You have no playlists.")
        await cb.answer()

    elif action == "download_all":
        playlist_id = int(parts[2])
        data = await api.get_playlist_tracks(user_id, playlist_id)
        count = len(data.get("tracks", []))
        if count == 0:
            await cb.answer("This playlist is empty.", show_alert=True)
            return
        await cb.message.edit_text(
            f"\u2b07\ufe0f Download all {count} tracks from '{data.get('name')}'?",
            reply_markup=kb.playlist_download_confirm_keyboard(playlist_id, count),
        )
        await cb.answer()

    elif action == "confirm_download":
        playlist_id = int(parts[2])
        data = await api.get_playlist_tracks(user_id, playlist_id)
        tracks = data.get("tracks", [])
        await cb.answer()
        try:
            await cb.message.delete()
        except Exception:  # noqa: BLE001
            pass
        for item in tracks:
            track = item.get("track") or {}
            if not track.get("id"):
                continue
            await _download_and_send(cb.message, cb.from_user, "track", str(track["id"]))


# ======================================================================
# Delete button
# ======================================================================


@router.callback_query(F.data == "delete")
async def cb_delete(cb: CallbackQuery) -> None:
    try:
        await cb.message.delete()
    except Exception:  # noqa: BLE001
        pass
    await cb.answer()


# ======================================================================
# Download flow (tracks / albums / playlists / discography)
# ======================================================================


@router.callback_query(F.data.startswith("download:"))
async def cb_download(cb: CallbackQuery) -> None:
    _, content_type, source_id = cb.data.split(":", 2)
    await cb.answer()
    if content_type == "artist":
        await _start_discography(cb, source_id)
        return
    await _download_and_send(cb.message, cb.from_user, content_type, source_id)


async def _send_media(
    message: Message,
    f: dict,
    media,  # FSInputFile or telegram file_id string
    thread_ctx: tuple | None,
    reply_markup=None,
):
    """Send an audio/zip either to the main chat or into its topic (with fallback)."""
    bot = message.bot
    chat_id = message.chat.id
    if f["kind"] == "audio":
        func = bot.send_audio
        kwargs = dict(
            chat_id=chat_id,
            audio=media,
            title=f.get("title"),
            performer=f.get("artist"),
            duration=f.get("duration"),
            caption=views.CAPTION,
            reply_markup=reply_markup,
        )
    else:
        func = bot.send_document
        caption = views.CAPTION
        if f.get("title"):
            caption += f"\n\U0001f4c0 {f['title']}"
        kwargs = dict(chat_id=chat_id, document=media, caption=caption, reply_markup=reply_markup)

    if thread_ctx:
        topic_type, topic_key, thread_id = thread_ctx
        return await topics.send_safely(func, chat_id, topic_type, topic_key, thread_id, **kwargs)
    return await func(**kwargs)


async def _download_and_send(
    message: Message,
    tg_user,
    content_type: str,
    source_id: str,
    quiet: bool = False,
) -> bool:
    """Full download pipeline: create job (user settings apply), poll, send, report.

    Returns True on success. `quiet` suppresses the status message (discography).
    """
    user_id = await _user_id_for(tg_user)
    try:
        settings = await api.get_user_settings(user_id)
    except APIError:
        settings = {}
    quality = settings.get("quality", "MP3_320")

    # If the user created a topic for this item, send files there
    thread_ctx = None
    if content_type in ("album", "playlist", "artist"):
        thread_id = topics.get_topic_thread_id(message.chat.id, content_type, source_id)
        if thread_id:
            thread_ctx = (content_type, source_id, thread_id)

    status = None if quiet else await message.answer("\u23f3 Processing...")

    try:
        res = await api.create_download(user_id, content_type, source_id)
    except APIError:
        if status:
            await status.edit_text(views.ERROR_MESSAGES["download_failed"])
        return False

    # 1) cache hit -> resend stored telegram file_ids instantly
    if res.get("cached"):
        if status:
            await status.delete()
        download_id = res.get("download_id")
        for f in res.get("files") or []:
            if not f.get("platform_file_id"):
                continue
            markup = (
                kb.rating_keyboard(download_id)
                if download_id and f["kind"] == "audio" and content_type == "track"
                else None
            )
            await _send_media(message, f, f["platform_file_id"], thread_ctx, markup)
        return True

    # 2) new job -> poll until ready
    job_id = res["job_id"]
    job = None
    last_text = None
    for _ in range(POLL_MAX_TRIES):
        await asyncio.sleep(POLL_INTERVAL)
        try:
            job = await api.get_job(job_id)
        except APIError:
            continue
        if job["status"] in ("ready", "failed", "cancelled"):
            break
        if status:
            text = f"\u2b07\ufe0f Downloading... {job.get('progress', 0)}%"
            step = job.get("current_step")
            if step:
                text += f"\n{step}"
            if text != last_text:
                last_text = text
                try:
                    await status.edit_text(text)
                except Exception:  # noqa: BLE001
                    pass

    if job is None or job["status"] != "ready":
        if status:
            await status.edit_text(views.ERROR_MESSAGES["download_failed"])
        return False

    # 3) fetch temp files, send to user, report file_ids back (feeds the cache)
    if status:
        try:
            await status.edit_text("\U0001f4e4 Uploading...")
        except Exception:  # noqa: BLE001
            pass
    tmp_dir = Path(tempfile.mkdtemp(prefix="spotizer_bot_"))
    ok = False
    try:
        for f in job.get("files") or []:
            if f["kind"] not in ("audio", "zip"):
                continue  # lyrics are embedded in the audio files
            if not f.get("temp_url"):
                continue
            try:
                path = await api.download_to(f["temp_url"], tmp_dir)
            except APIError:
                continue

            sent = await _send_media(message, f, FSInputFile(path), thread_ctx)
            file_id = None
            if f["kind"] == "audio" and sent.audio:
                file_id = sent.audio.file_id
            elif sent.document:
                file_id = sent.document.file_id
            ok = True

            if file_id:
                try:
                    report = await api.report_file(
                        user_id=user_id,
                        job_id=job_id,
                        source_id=source_id,
                        content_type=content_type,
                        quality=quality,
                        platform_file_id=file_id,
                        kind=f["kind"],
                        title=f.get("title"),
                        artist=f.get("artist"),
                        duration=f.get("duration"),
                    )
                    download_id = report.get("download_id")
                    # attach the rating keyboard to sent tracks
                    if download_id and f["kind"] == "audio" and content_type == "track":
                        try:
                            await sent.edit_reply_markup(reply_markup=kb.rating_keyboard(download_id))
                        except Exception:  # noqa: BLE001
                            pass
                except APIError:
                    logger.exception("file report failed")
        if status:
            await status.delete()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return ok


# ======================================================================
# Discography download
# ======================================================================


async def _start_discography(cb: CallbackQuery, artist_id: str) -> None:
    try:
        artist = await api.get_artist(artist_id)
        albums = await api.get_artist_albums(artist_id)
    except APIError:
        await cb.message.answer(views.ERROR_MESSAGES["general"])
        return
    if not albums:
        await cb.message.answer(views.NO_ALBUMS_MESSAGE)
        return

    sid = uuid.uuid4().hex[:8]
    _disc_sessions[sid] = {
        "albums": albums,
        "selected": set(range(len(albums))),  # everything selected by default
        "artist_id": artist_id,
        "artist_name": artist.get("name") or "Artist",
        "cancelled": False,
        "page": 1,
    }
    await cb.message.answer(
        views.discography_selector_text(len(albums)),
        reply_markup=kb.discography_select_keyboard(albums, _disc_sessions[sid]["selected"], sid, 1),
    )


@router.callback_query(F.data.startswith("dsel:"))
async def cb_dsel(cb: CallbackQuery) -> None:
    parts = cb.data.split(":")
    action, sid = parts[1], parts[2]
    session = _disc_sessions.get(sid)
    if session is None:
        await cb.answer("This selection has expired. Open the artist again.", show_alert=True)
        return

    if action == "t":
        idx = int(parts[3])
        if idx in session["selected"]:
            session["selected"].discard(idx)
        else:
            session["selected"].add(idx)
        await cb.message.edit_reply_markup(
            reply_markup=kb.discography_select_keyboard(
                session["albums"], session["selected"], sid, session["page"]
            )
        )
        await cb.answer()

    elif action == "p":
        session["page"] = int(parts[3])
        await cb.message.edit_reply_markup(
            reply_markup=kb.discography_select_keyboard(
                session["albums"], session["selected"], sid, session["page"]
            )
        )
        await cb.answer()

    elif action == "all":
        if len(session["selected"]) == len(session["albums"]):
            session["selected"] = set()
        else:
            session["selected"] = set(range(len(session["albums"])))
        await cb.message.edit_reply_markup(
            reply_markup=kb.discography_select_keyboard(
                session["albums"], session["selected"], sid, session["page"]
            )
        )
        await cb.answer()

    elif action == "cancel":
        _disc_sessions.pop(sid, None)
        try:
            await cb.message.delete()
        except Exception:  # noqa: BLE001
            pass
        await cb.answer()

    elif action == "go":
        if not session["selected"]:
            await cb.answer("Select at least one item first.", show_alert=True)
            return
        await cb.answer()
        await _run_discography(cb, sid)


async def _run_discography(cb: CallbackQuery, sid: str) -> None:
    session = _disc_sessions[sid]
    albums = [session["albums"][i] for i in sorted(session["selected"])]
    artist_name = session["artist_name"]
    total = len(albums)

    progress = cb.message
    try:
        await progress.edit_text(
            views.discography_progress(artist_name, 0, "Preparing to download..."),
            reply_markup=kb.discography_cancel_keyboard(),
            parse_mode="Markdown",
        )
    except Exception:  # noqa: BLE001
        progress = await cb.message.answer(
            views.discography_progress(artist_name, 0, "Preparing to download..."),
            reply_markup=kb.discography_cancel_keyboard(),
            parse_mode="Markdown",
        )
    _disc_by_msg[progress.message_id] = sid

    for i, album in enumerate(albums, 1):
        if session["cancelled"]:
            try:
                await progress.edit_text(views.DISCOGRAPHY_CANCELLED)
            except Exception:  # noqa: BLE001
                pass
            _cleanup_disc(sid, progress.message_id)
            return
        percent = int((i - 1) / total * 100)
        try:
            await progress.edit_text(
                views.discography_progress(
                    artist_name, percent, f"Downloading album {i}/{total}..."
                ),
                reply_markup=kb.discography_cancel_keyboard(),
                parse_mode="Markdown",
            )
        except Exception:  # noqa: BLE001
            pass
        await _download_and_send(cb.message, cb.from_user, "album", str(album["id"]), quiet=True)

    try:
        await progress.delete()
    except Exception:  # noqa: BLE001
        pass
    await cb.message.answer(views.DISCOGRAPHY_COMPLETE)
    _cleanup_disc(sid, progress.message_id)


def _cleanup_disc(sid: str, message_id: int) -> None:
    _disc_sessions.pop(sid, None)
    _disc_by_msg.pop(message_id, None)


@router.callback_query(F.data == "cancel_disc")
async def cb_cancel_disc(cb: CallbackQuery) -> None:
    sid = _disc_by_msg.get(cb.message.message_id)
    session = _disc_sessions.get(sid) if sid else None
    if session is None:
        await cb.answer("Nothing to cancel.")
        return
    session["cancelled"] = True
    await cb.answer("Cancelling...")


# ======================================================================
# Background subscription checker (new releases)
# ======================================================================


async def subscription_checker(bot) -> None:
    """Periodically poll the API for new releases and notify followers."""
    platform = os.getenv("PLATFORM_NAME", "default")
    interval = int(os.getenv("SUB_CHECK_INTERVAL_SECONDS", "3600"))
    while True:
        await asyncio.sleep(interval)
        try:
            notifications = await api.check_new_releases()
        except Exception:  # noqa: BLE001 - API down etc.
            logger.exception("new releases check failed")
            continue
        for note in notifications:
            album = note.get("album") or {}
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="\u2b07\ufe0f Download Album",
                            callback_data=f"download:album:{album.get('id')}",
                        )
                    ],
                    [InlineKeyboardButton(text="\u274c", callback_data="delete")],
                ]
            )
            for ident in note.get("identities", []):
                if ident.get("platform") != platform:
                    continue
                try:
                    await bot.send_message(
                        int(ident["platform_user_id"]),
                        views.new_release_text(note.get("artist_name") or "an artist", album),
                        parse_mode="Markdown",
                        reply_markup=markup,
                    )
                except Exception:  # noqa: BLE001 - user blocked bot etc.
                    logger.warning("could not notify %s", ident.get("platform_user_id"))
