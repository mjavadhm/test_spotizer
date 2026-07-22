"""All user-facing texts - ported verbatim from the old bot's views."""
from datetime import datetime

WELCOME_MESSAGE = (
    "\U0001f3b5 Welcome to MusicDownloader Bot! \U0001f3b5\n\n"
    "\U0001f539 Download any track, album, or playlist effortlessly.\n"
    "\U0001f539 Get high-quality audio in your preferred format.\n"
    "\U0001f539 Option to receive albums & playlists as a ZIP file.\n\n"
    "\u2728 Available Commands:\n\n"
    "/settings \u2013 Customize your download preferences.\n"
    "/history \u2013 View your recent downloads.\n\n"
    "\U0001f3a7 Simply send a link from Deezer or Spotify, and let the music flow! \U0001f3a7"
)

HELP_MESSAGE = """\U0001f3b5 *MusicDownloader Bot Help* \U0001f3b5

*Available Commands:*
/start - Start the bot and see welcome message
/settings - Customize your download preferences
/history - View your recent downloads
/help - Show this help message

*How to Use:*
1. Send a Deezer or Spotify link to download music
2. Use /settings to set your preferred:
   \u2022 Download quality (MP3 128/320 or FLAC)
   \u2022 ZIP option for albums/playlists
3. View your download history with /history

*Supported Links:*
\u2022 Deezer: Tracks, Albums, Playlists
\u2022 Spotify: Tracks, Albums (Playlists coming soon)

*Need more help?*
If you have any issues or questions, feel free to contact support."""

ABOUT_MESSAGE = """\U0001f3b5 *About MusicDownloader Bot* \U0001f3b5

A powerful music downloading bot that helps you get your favorite music from Deezer and Spotify.

*Features:*
\u2022 High-quality audio downloads
\u2022 Multiple format support (MP3, FLAC)
\u2022 Album and playlist support
\u2022 Custom download settings
\u2022 Download history tracking

*Version:* 1.0.0
*Developer:* @YourUsername

Thank you for using MusicDownloader Bot! \U0001f3a7"""

ERROR_MESSAGES = {
    "invalid_url": "\u274c Invalid link. Please provide a valid Deezer or Spotify link.",
    "download_failed": "\u274c Download failed. Please try again or use a different link.",
    "general": "An error occurred. Please try again later.",
}

MEDIA_REPLY = (
    "I can help you download music from Deezer and Spotify. "
    "Please send me a link to download music!"
)

CAPTION = "@Spotizer_bot \U0001f3a7"


def _fmt_duration(seconds) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "N/A"
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------------------------------------------------------------- search

def search_prompt(query: str) -> str:
    return f"What would you like to search for '{query}'?"


def search_results_text(query: str, search_type: str, page: int) -> str:
    return f"Search results for '{query}' ({search_type.capitalize()}s):\nPage {page}"


# ---------------------------------------------------------------- cards

def track_info(track: dict) -> str:
    return (
        f"\U0001f3b5 *Track:* [{track.get('title')}]({track.get('link')})\n\n"
        f"\U0001f464 *Artist:* {track.get('artist') or 'N/A'}\n\n"
        f"\U0001f4bf *Album:* {track.get('album') or 'N/A'}\n\n"
        f"\U0001f4c5 *Released:* {track.get('release_date') or 'N/A'}\n\n"
        f"\u23f1 *Duration:* {_fmt_duration(track.get('duration'))}\n\n"
        f"\U0001f51e *Explicit:* {'Yes' if track.get('explicit') else 'No'}"
    )


def album_info(album: dict) -> str:
    return "\n".join(
        [
            f"\U0001f4c0 *Album:* [{album.get('title')}]({album.get('link')})",
            "",
            f"\U0001f464 *Artist:* {album.get('artist') or 'N/A'}",
            "",
            f"\U0001f4c5 *Release:* {album.get('release_date') or 'N/A'}",
            "",
            f"\U0001f3b5 *Tracks:* {album.get('nb_tracks') or 'N/A'}",
        ]
    )


def playlist_info(playlist: dict) -> str:
    lines = [f"\U0001f4d1 *Playlist:* [{playlist.get('title')}]({playlist.get('link')})"]
    if playlist.get("creator"):
        lines += ["", f"\U0001f464 *By:* {playlist['creator']}"]
    if playlist.get("description"):
        lines += ["", f"\U0001f4dd {playlist['description']}"]
    lines += ["", f"\U0001f3b5 *Tracks:* {playlist.get('nb_tracks') or 'N/A'}"]
    return "\n".join(lines)


def artist_info(artist: dict) -> str:
    followers = artist.get("nb_fans")
    followers_text = f"{followers:,}" if isinstance(followers, int) else "N/A"
    return (
        f"\U0001f3a8 *Artist:* [{artist.get('name')}]({artist.get('link')})\n\n"
        f"\U0001f465 *Followers:* {followers_text}\n\n"
        f"\U0001f4bf *Albums:* {artist.get('nb_albums') or 'N/A'}"
    )


# ---------------------------------------------------------------- lists

def list_header(content_type: str, action: str, name: str, artist: str | None = None) -> str:
    if content_type == "album":
        return f"Tracks in album '{name}'" + (f" by {artist}:" if artist else ":")
    if content_type == "artist" and action == "top_tracks":
        return f"Top '{name}' tracks:"
    if content_type == "artist" and action == "album":
        return f"Albums by '{name}':"
    if content_type == "artist" and action == "related":
        return f"Artists related to '{name}':"
    if content_type == "playlist":
        return f"Tracks in playlist '{name}':"
    return f"'{name}':"


# ---------------------------------------------------------------- history

def format_download_history(items: list[dict]) -> str:
    if not items:
        return "You haven't downloaded any tracks yet."
    text = "Your recent downloads:\n\n"
    for i, d in enumerate(items, 1):
        title = d.get("title") or d.get("source_id")
        artist = d.get("artist") or "Unknown"
        rating = d.get("user_rating")
        rating_text = " \U0001f44d" if rating == 1 else (" \U0001f44e" if rating == -1 else "")
        when = d.get("downloaded_at") or ""
        try:
            when = datetime.fromisoformat(str(when).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            when = str(when)[:16]
        text += (
            f"{i}. {title} - {artist}{rating_text}\n"
            f"   \U0001f3ad Type: {str(d.get('content_type') or '').capitalize()}\n"
            f"   \U0001f50a Quality: {d.get('quality')}\n"
            f"   \U0001f4c5 {when}\n\n"
        )
    return text


# ---------------------------------------------------------------- playlists (personal)

PLAYLIST_CREATION_MESSAGE = "\u270f\ufe0f Please enter a name for your new playlist:"
PLAYLIST_CREATION_WITH_TRACK_MESSAGE = (
    "\u270f\ufe0f Please enter a name for your new playlist.\n\n"
    "\U0001f4a1 The selected track will be added to this playlist."
)
CHOOSE_PLAYLIST_MESSAGE = "\U0001f3b6 Choose a playlist"
ADD_TO_PLAYLIST_MESSAGE = "Choose a playlist to add"
NO_PLAYLISTS_MESSAGE = "No playlists available please create one"


def playlist_created(name: str) -> str:
    return f"\u2705 Playlist '{name}' created successfully!"


def playlist_tracks_text(name: str, tracks: list[dict], page: int, total_pages: int, per_page: int = 5) -> str:
    text = f"\U0001f3b6 *{name}*\nTotal: {len(tracks)} track(s)\n\n"
    start = (page - 1) * per_page
    for i, item in enumerate(tracks[start : start + per_page], start + 1):
        track = item.get("track") or {}
        text += (
            f"{i}. *{track.get('title')}* - {track.get('artist') or 'Unknown'}\n"
            f"   \u23f1 {_fmt_duration(track.get('duration'))}\n\n"
        )
    text += f"\U0001f4c4 Page {page} of {total_pages}"
    return text


# ---------------------------------------------------------------- subscriptions

NO_SUBSCRIPTIONS_MESSAGE = (
    "You're not following any artists yet.\nOpen an artist and tap \U0001f514 Follow."
)
SUBSCRIPTIONS_HEADER = "\U0001f514 *Artists you follow* (tap to unfollow):"


def subscribed(name: str) -> str:
    return f"\U0001f514 You're now following {name}!"


ALREADY_SUBSCRIBED = "You're already following this artist."
UNSUBSCRIBED = "Unsubscribed."


def new_release_text(artist_name: str, album: dict) -> str:
    return (
        f"\U0001f514 *New release from {artist_name}!*\n\n"
        f"\U0001f4c0 *{album.get('title')}*\n"
        f"\U0001f4c5 {album.get('release_date') or ''}"
    )


# ---------------------------------------------------------------- recommendations

RECOMMEND_THINKING = "\U0001f916 Thinking... Analyzing your taste..."
RECOMMEND_HEADER = (
    "\U0001f3b5 *Recommended for You* \U0001f3b5\nTap a track below to download \U0001f447"
)
RECOMMEND_NO_HISTORY = (
    "We don't have enough history to recommend music yet. "
    "Download some songs and rate them!"
)


# ---------------------------------------------------------------- discography

def discography_selector_text(count: int) -> str:
    return (
        f"\U0001f4e5 Found {count} albums/EPs for this artist.\n\n"
        "Please select the items you want to download:"
    )


NO_ALBUMS_MESSAGE = "\u274c No albums found for this artist."
DISCOGRAPHY_CANCELLED = "\U0001f6ab Discography download cancelled by user."
DISCOGRAPHY_COMPLETE = "\u2705 Discography download complete!"


def discography_progress(artist_name: str, percent: int, status: str) -> str:
    filled = int(round(percent / 10))
    bar = "\u25a0" * filled + "\u25a1" * (10 - filled)
    return (
        f"\U0001f4e5 *{artist_name} Discography*\n\n"
        f"[{bar}] {percent}%\n"
        f"\u23f3 Status: {status}"
    )


# ---------------------------------------------------------------- ratings

RATED_LIKE = "\U0001f44d Liked!"
RATED_DISLIKE = "\U0001f44e Disliked!"
