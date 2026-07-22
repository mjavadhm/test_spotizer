"""All inline keyboards - ported 1:1 from the old bot's views.

Callback grammar is identical to the old bot, with one internal difference:
search/page callbacks carry a short query-id instead of the raw query text
(Telegram's 64-byte callback_data limit). Button labels are unchanged.
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _trunc(text: str, limit: int = 60) -> str:
    text = text or ""
    return text[:limit - 3] + "..." if len(text) > limit else text


# ---------------------------------------------------------------- search

def search_type_keyboard(qid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Search Tracks", callback_data=f"search:track:{qid}"),
                InlineKeyboardButton(text="Search Albums", callback_data=f"search:album:{qid}"),
            ],
            [
                InlineKeyboardButton(text="Search Playlists", callback_data=f"search:playlist:{qid}"),
                InlineKeyboardButton(text="Search Artist", callback_data=f"search:artist:{qid}"),
            ],
        ]
    )


def search_results_keyboard(
    results: list[dict], search_type: str, page: int, qid: str
) -> InlineKeyboardMarkup:
    rows = []
    for item in results:
        if search_type == "track":
            label = f"\U0001f3b5 {item.get('title')} - {item.get('artist') or ''}"
        elif search_type == "album":
            label = f"\U0001f4c0 {item.get('title')} by {item.get('artist') or ''}"
        elif search_type == "artist":
            label = f"\U0001f464 {item.get('name')}"
        else:  # playlist
            label = f"\U0001f4d1 {item.get('title')} ({item.get('nb_tracks') or 0} tracks)"
        rows.append(
            [
                InlineKeyboardButton(
                    text=_trunc(label),
                    callback_data=f"select:{search_type}:{item.get('id')}",
                )
            ]
        )

    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="\u2b05\ufe0f Previous", callback_data=f"page:{page - 1}:{search_type}:{qid}"
            )
        )
    nav.append(InlineKeyboardButton(text="\u274c", callback_data="delete"))
    if len(results) == 5:
        nav.append(
            InlineKeyboardButton(
                text="Next \u27a1\ufe0f", callback_data=f"page:{page + 1}:{search_type}:{qid}"
            )
        )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------- cards

def track_keyboard(track: dict) -> InlineKeyboardMarkup:
    tid = track.get("id")
    rows = [[InlineKeyboardButton(text="\u2b07\ufe0f Download", callback_data=f"download:track:{tid}")]]
    if track.get("artist_id"):
        rows.append(
            [
                InlineKeyboardButton(
                    text=_trunc(f"\U0001f3a8 Artist:{track.get('artist') or ''}"),
                    callback_data=f"select:artist:{track['artist_id']}",
                )
            ]
        )
    if track.get("album_id"):
        rows.append(
            [
                InlineKeyboardButton(
                    text=_trunc(f"\U0001f4c0 Album:{track.get('album') or ''}"),
                    callback_data=f"select:album:{track['album_id']}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="\u2795 Add to Playlist",
                callback_data=f"playlist:add:get_playlist:{tid}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="\u274c", callback_data="delete")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def album_keyboard(album: dict) -> InlineKeyboardMarkup:
    aid = album.get("id")
    rows = [
        [InlineKeyboardButton(text="\u2b07\ufe0f Download Album", callback_data=f"download:album:{aid}")],
        [InlineKeyboardButton(text="\U0001f9f5 Create Topic", callback_data=f"mktopic:album:{aid}")],
        [InlineKeyboardButton(text="\U0001f4cb View Tracks", callback_data=f"view:album:track:{aid}:1")],
    ]
    if album.get("artist_id"):
        rows.append(
            [
                InlineKeyboardButton(
                    text=_trunc(f"\U0001f3a8 Artist:{album.get('artist') or ''}"),
                    callback_data=f"select:artist:{album['artist_id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="\u274c", callback_data="delete")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def playlist_keyboard(playlist: dict) -> InlineKeyboardMarkup:
    pid = playlist.get("id")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="\U0001f4cb View Tracks", callback_data=f"view:playlist:tracks:{pid}:1")],
            [InlineKeyboardButton(text="\u2b07\ufe0f Download Playlist", callback_data=f"download:playlist:{pid}")],
            [InlineKeyboardButton(text="\U0001f9f5 Create Topic", callback_data=f"mktopic:playlist:{pid}")],
            [InlineKeyboardButton(text="\u274c", callback_data="delete")],
        ]
    )


def artist_keyboard(artist: dict) -> InlineKeyboardMarkup:
    aid = artist.get("id")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="\U0001f51d Top Tracks", callback_data=f"view:artist:top_tracks:{aid}:1")],
            [InlineKeyboardButton(text="\U0001f4bf Albums", callback_data=f"view:artist:album:{aid}:1")],
            [InlineKeyboardButton(text="\U0001f465 Related Artists", callback_data=f"view:artist:related:{aid}:1")],
            [InlineKeyboardButton(text="\u2b07\ufe0f Download Discography (Slow)", callback_data=f"download:artist:{aid}")],
            [InlineKeyboardButton(text="\U0001f514 Follow", callback_data=f"sub:add:{aid}")],
            [InlineKeyboardButton(text="\U0001f9f5 Create Topic", callback_data=f"mktopic:artist:{aid}")],
            [InlineKeyboardButton(text="\u274c", callback_data="delete")],
        ]
    )


# ---------------------------------------------------------------- lists

def list_keyboard(
    items: list[dict],
    content_type: str,
    action: str,
    spoid: str,
    page: int,
    per_page: int = 8,
) -> InlineKeyboardMarkup:
    """Paginated list used for album tracks / artist albums / top tracks / related."""
    start = (page - 1) * per_page
    page_items = items[start : start + per_page]

    if action == "top_tracks" or (content_type == "album" and action == "track"):
        item_type = "track"
    elif action == "album":
        item_type = "album"
    elif action == "related":
        item_type = "artist"
    elif action == "tracks":  # deezer playlist tracks
        item_type = "track"
    else:
        item_type = content_type

    rows = []
    for item in page_items:
        name = item.get("title") or item.get("name") or ""
        artist = item.get("artist")
        label = f"{name} - {artist}" if artist else f"{name}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=_trunc(label),
                    callback_data=f"select:{item_type}:{item.get('id')}",
                )
            ]
        )

    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="\u2b05\ufe0f Previous",
                callback_data=f"view:{content_type}:{action}:{spoid}:{page - 1}",
            )
        )
    nav.append(InlineKeyboardButton(text="\U0001f519 Back", callback_data=f"select:{content_type}:{spoid}"))
    if start + per_page < len(items):
        nav.append(
            InlineKeyboardButton(
                text="Next \u27a1\ufe0f",
                callback_data=f"view:{content_type}:{action}:{spoid}:{page + 1}",
            )
        )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------- settings

def settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    quality = settings.get("quality", "MP3_320")
    make_zip = settings.get("make_zip", True)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Change Quality: {quality}", callback_data="setting:change_quality")],
            [InlineKeyboardButton(text=f"Make ZIP: {'Yes' if make_zip else 'No'}", callback_data="setting:toggle_zip")],
        ]
    )


def quality_keyboard(current: str) -> InlineKeyboardMarkup:
    rows = []
    for opt in ("MP3_128", "MP3_320", "FLAC"):
        label = f"{opt} \u2705" if opt == current else opt
        rows.append([InlineKeyboardButton(text=label, callback_data=f"set_quality:{opt}")])
    rows.append([InlineKeyboardButton(text="Back", callback_data="set_quality:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------- rating

def rating_keyboard(download_id: int, current: int | None = None) -> InlineKeyboardMarkup:
    like = "\U0001f44d \u2713" if current == 1 else "\U0001f44d"
    dislike = "\U0001f44e \u2713" if current == -1 else "\U0001f44e"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=like, callback_data=f"rate:like:{download_id}"),
                InlineKeyboardButton(text=dislike, callback_data=f"rate:dislike:{download_id}"),
            ]
        ]
    )


# ---------------------------------------------------------------- recommendations

def recommendations_keyboard(tracks: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for t in tracks:
        label = _trunc(f"\u2b07\ufe0f {t.get('artist')} - {t.get('title')}")
        rows.append([InlineKeyboardButton(text=label, callback_data=f"download:track:{t.get('id')}")])
    rows.append([InlineKeyboardButton(text="\U0001f5d1 Close", callback_data="delete")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------- subscriptions

def subscriptions_keyboard(subs: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"\u274c {s.get('artist_name') or s.get('artist_id')}",
                callback_data=f"sub:remove:{s.get('artist_id')}",
            )
        ]
        for s in subs
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------- personal playlists

def add_to_playlist_keyboard(playlists: list[dict], track_id: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="New Playlist", callback_data=f"playlist:new_and_add:{track_id}")]]
    for p in playlists:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_trunc(p.get("name") or ""),
                    callback_data=f"playlist:add:{p.get('playlist_id')}:{track_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def playlists_list_keyboard(playlists: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=_trunc(p.get("name") or ""),
                callback_data=f"select_playlist:{p.get('playlist_id')}",
            )
        ]
        for p in playlists
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def playlist_details_keyboard(playlist_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="\U0001f4cb View Tracks", callback_data=f"playlist:view_tracks:{playlist_id}")],
            [InlineKeyboardButton(text="\u2b07\ufe0f Download All", callback_data=f"playlist:download_all:{playlist_id}")],
            [InlineKeyboardButton(text="\U0001f5d1\ufe0f Delete Playlist", callback_data=f"playlist:delete:{playlist_id}")],
        ]
    )


def playlist_tracks_keyboard(
    page_tracks: list[dict], playlist_id: int, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """page_tracks: items of PlaylistTracksResponse.tracks for the current page."""
    rows = []
    for item in page_tracks:
        track = item.get("track") or {}
        label = _trunc(f"{track.get('title')} - {track.get('artist') or ''}", 40)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"\u2b07\ufe0f {label}",
                    callback_data=f"download:track:{track.get('id')}",
                ),
                InlineKeyboardButton(
                    text="\u274c",
                    callback_data=f"playlist:remove_track:{playlist_id}:{item.get('playlist_track_id')}",
                ),
            ]
        )

    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(text="\u25c0\ufe0f Previous", callback_data=f"playlist:page:{playlist_id}:{page - 1}")
        )
    if page < total_pages:
        nav.append(
            InlineKeyboardButton(text="Next \u25b6\ufe0f", callback_data=f"playlist:page:{playlist_id}:{page + 1}")
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="\U0001f519 Back to Playlists", callback_data="playlist:back_to_list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def playlist_download_confirm_keyboard(playlist_id: int, track_count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"\u2705 Confirm Download ({track_count} tracks)",
                    callback_data=f"playlist:confirm_download:{playlist_id}",
                )
            ],
            [InlineKeyboardButton(text="\u274c Cancel", callback_data=f"playlist:view_tracks:{playlist_id}")],
        ]
    )


# ---------------------------------------------------------------- discography

def discography_select_keyboard(
    albums: list[dict], selected: set, sid: str, page: int, per_page: int = 8
) -> InlineKeyboardMarkup:
    start = (page - 1) * per_page
    rows = []
    for idx in range(start, min(start + per_page, len(albums))):
        a = albums[idx]
        year = (a.get("release_date") or "")[:4]
        mark = "\u2705" if idx in selected else "\u2b1c"
        label = _trunc(f"{mark} {a.get('title')}" + (f" ({year})" if year else ""))
        rows.append([InlineKeyboardButton(text=label, callback_data=f"dsel:t:{sid}:{idx}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="\u2b05\ufe0f Previous", callback_data=f"dsel:p:{sid}:{page - 1}"))
    if start + per_page < len(albums):
        nav.append(InlineKeyboardButton(text="Next \u27a1\ufe0f", callback_data=f"dsel:p:{sid}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(text="Select All", callback_data=f"dsel:all:{sid}"),
            InlineKeyboardButton(text="\U0001f4e5 Download Selected", callback_data=f"dsel:go:{sid}"),
        ]
    )
    rows.append([InlineKeyboardButton(text="\u274c Cancel", callback_data=f"dsel:cancel:{sid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def discography_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="\u274c Cancel Download", callback_data="cancel_disc")]]
    )
