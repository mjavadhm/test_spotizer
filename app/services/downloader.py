"""deemix wrapper + lyrics.

Runs the deemix CLI in a subprocess and collects the produced files
(audio + .lrc synced lyrics + playlists).

Lyrics model (same as the current Spotizer bot):
- deemix config enables syncedLyrics, so deemix MAY produce .lrc itself
- independently of deemix, lyrics are fetched from LRCLib etc. via the
  `syncedlyrics` package (synced if available, plain text as fallback) and:
    * embedded into the audio file tags (USLT for mp3 / LYRICS for flac)
      -> music players display them, synced players play them synced
    * saved as a .lrc file next to the track (delivered as kind="lrc")
- for single tracks the lyrics search runs IN PARALLEL with the deemix
  download (metadata comes from the public Deezer API before download);
  for albums/playlists all searches run concurrently (max 5 at a time)
"""

import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

from ..config import get_settings

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg"}
EXTRA_KINDS = {".lrc": "lrc", ".m3u": "m3u", ".m3u8": "m3u"}

BITRATE_MAP = {
    "MP3_128": "128",
    "MP3_320": "320",
    "FLAC": "flac",
}

TRACK_URL_RE = re.compile(r"deezer\.com(?:/[a-z]{2})?/track/(\d+)")


@dataclass
class DownloadedFile:
    path: Path
    kind: str  # audio / lrc / m3u
    title: str | None = None
    artist: str | None = None
    duration: int | None = None
    size: int | None = None


@dataclass
class DownloadResult:
    success: bool
    error: str | None = None
    files: list[DownloadedFile] = field(default_factory=list)


def _read_tags(path: Path) -> tuple[str | None, str | None, int | None]:
    """Best-effort title/artist/duration via mutagen."""
    try:
        import mutagen

        audio = mutagen.File(path, easy=True)
        if audio is None:
            return None, None, None
        title = (audio.get("title") or [None])[0]
        artist = (audio.get("artist") or [None])[0]
        duration = int(audio.info.length) if audio.info else None
        return title, artist, duration
    except Exception:
        return None, None, None


# ---------------------------------------------------------------------------
# Lyrics helpers (network search + tag embedding), independent from deemix
# ---------------------------------------------------------------------------

def _search_lyrics_sync(title: str, artist: str) -> str | None:
    """Search lyrics on LRCLib etc. Synced if available, plain otherwise."""
    try:
        import syncedlyrics
    except ImportError:
        return None
    try:
        return syncedlyrics.search(f"{title} {artist}")
    except Exception:
        return None


async def _search_lyrics(title: str, artist: str) -> str | None:
    return await asyncio.to_thread(_search_lyrics_sync, title, artist)


def _apply_lyrics_sync(path: Path, lrc: str) -> Path | None:
    """Write .lrc next to the file + embed lyrics into the audio tags."""
    lrc_path: Path | None = path.with_suffix(".lrc")
    try:
        lrc_path.write_text(lrc, encoding="utf-8")
    except Exception:
        lrc_path = None
    try:
        ext = path.suffix.lower()
        if ext == ".mp3":
            from mutagen.id3 import ID3, USLT

            tags = ID3(str(path))
            tags.setall("USLT", [USLT(encoding=3, lang="eng", desc="", text=lrc)])
            tags.save()
        elif ext == ".flac":
            from mutagen.flac import FLAC

            audio = FLAC(str(path))
            audio["LYRICS"] = lrc
            audio.save()
    except Exception:
        pass  # missing lyrics must never break a download
    return lrc_path


class Downloader:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _ensure_config(self) -> Path:
        """Prepare deemix config under $XDG_CONFIG_HOME/deemix.

        - writes the ARL cookie to .arl
        - enables syncedLyrics so .lrc files are saved next to tracks
          (deemix default is false)
        - keeps embedded (unsynced) lyrics in tags enabled
        Returns the directory to use as XDG_CONFIG_HOME.
        """
        config_home = Path(self.settings.TEMP_DIR) / "config"
        deemix_dir = config_home / "deemix"
        deemix_dir.mkdir(parents=True, exist_ok=True)

        arl = self.settings.DEEZER_ARL.strip()
        if arl:
            (deemix_dir / ".arl").write_text(arl)

        # Default config matches the field's proven-working deemix setup
        cfg_path = deemix_dir / "config.json"
        cfg: dict = {
            "downloadLocation": "",
            "tracknameTemplate": "%artist% - %title%",
            "albumTracknameTemplate": "%tracknumber% - %title%",
            "playlistTracknameTemplate": "%artist% - %title%",
            "createPlaylistFolder": True,
            "createAlbumFolder": True,
            "createArtistFolder": True,
            "includeSingles": True,
            "includeEPs": True,
            "maxBitrate": 3,
            "queueConcurrency": 3,
            "fallbackBitrate": True,
            "fallbackSearch": True,
            "overwriteFile": "n",
            "createM3U8File": False,
            "embeddedArtworkSize": 800,
            "saveArtwork": False,
            "tags": {
                "title": True,
                "artist": True,
                "album": True,
                "cover": True,
                "trackNumber": True,
                "discNumber": True,
                "date": True,
                "year": True,
                "genre": True,
                "lyrics": True,
            },
        }
        if cfg_path.exists():
            try:
                cfg.update(json.loads(cfg_path.read_text()))
            except Exception:
                pass
        if arl:
            cfg["arl"] = arl
        cfg["syncedLyrics"] = True  # save .lrc lyric files
        tags = cfg.get("tags") or {}
        tags["lyrics"] = True  # embed unsynced lyrics into audio tags
        cfg["tags"] = tags
        cfg_path.write_text(json.dumps(cfg, indent=2))
        return config_home

    @staticmethod
    async def _prefetch_track_lyrics(url: str) -> "asyncio.Task[str | None] | None":
        """For single-track urls, start the lyrics search BEFORE the download.

        Title/artist come from the public Deezer API (no ARL needed), so the
        search runs in parallel with the deemix subprocess.
        """
        match = TRACK_URL_RE.search(url)
        if not match:
            return None
        try:
            async with aiohttp.ClientSession() as session:
                api_url = "https://api.deezer.com/track/" + match.group(1)
                async with session.get(api_url) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
        except Exception:
            return None
        title = data.get("title")
        artist = (data.get("artist") or {}).get("name")
        if not title or not artist:
            return None
        return asyncio.create_task(_search_lyrics(title, artist))

    async def _add_lyrics(
        self,
        files: list[DownloadedFile],
        lyrics_task: "asyncio.Task[str | None] | None",
    ) -> list[DownloadedFile]:
        """Fetch + attach lyrics for audio files that have no .lrc yet."""
        lrc_stems = {f.path.with_suffix("") for f in files if f.kind == "lrc"}
        targets = [
            f
            for f in files
            if f.kind == "audio"
            and f.path.with_suffix("") not in lrc_stems
            and f.title
            and f.artist
        ]
        if not targets:
            if lyrics_task is not None:
                lyrics_task.cancel()
            return files

        if lyrics_task is not None and len(targets) == 1:
            # single track: result was fetched in parallel with the download
            texts = [await lyrics_task]
        else:
            if lyrics_task is not None:
                lyrics_task.cancel()
            sem = asyncio.Semaphore(5)

            async def _fetch(f: DownloadedFile) -> str | None:
                async with sem:
                    return await _search_lyrics(f.title or "", f.artist or "")

            texts = list(await asyncio.gather(*[_fetch(f) for f in targets]))

        for f, lrc in zip(targets, texts):
            if not lrc:
                continue
            lrc_path = await asyncio.to_thread(_apply_lyrics_sync, f.path, lrc)
            if lrc_path is not None and lrc_path.exists():
                files.append(
                    DownloadedFile(
                        path=lrc_path,
                        kind="lrc",
                        title=f.title,
                        artist=f.artist,
                        size=lrc_path.stat().st_size,
                    )
                )
        return files

    async def download(self, url: str, quality: str, dest_dir: Path) -> DownloadResult:
        """Download a deezer url into dest_dir and return the produced files."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        config_home = self._ensure_config()

        # kick off the lyrics search in parallel with the download (tracks only)
        lyrics_task = await self._prefetch_track_lyrics(url)

        bitrate = BITRATE_MAP.get(quality, "320")
        # deemix's CLI only accepts the short flags -p / -b.
        cmd = [
            sys.executable,
            "-m",
            "deemix",
            "-p",
            str(dest_dir),
            "-b",
            bitrate,
            url,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={"XDG_CONFIG_HOME": str(config_home), "PATH": "/usr/bin:/usr/local/bin"},
            )
            output, _ = await proc.communicate()
            log = output.decode(errors="replace") if output else ""
        except FileNotFoundError:
            if lyrics_task is not None:
                lyrics_task.cancel()
            return DownloadResult(success=False, error="deemix is not installed")
        except Exception as exc:  # noqa: BLE001
            if lyrics_task is not None:
                lyrics_task.cancel()
            return DownloadResult(success=False, error=f"deemix failed to start: {exc}")

        files = self._collect_files(dest_dir)
        if not any(f.kind == "audio" for f in files):
            if lyrics_task is not None:
                lyrics_task.cancel()
            tail = log.strip().splitlines()[-3:] if log else []
            return DownloadResult(
                success=False,
                error="No audio produced. " + " | ".join(tail) if tail else "No audio produced.",
            )

        files = await self._add_lyrics(files, lyrics_task)
        return DownloadResult(success=True, files=files)

    @staticmethod
    def _collect_files(dest_dir: Path) -> list[DownloadedFile]:
        found: list[DownloadedFile] = []
        for path in sorted(dest_dir.rglob("*")):
            if not path.is_file():
                continue
            ext = path.suffix.lower()
            if ext in AUDIO_EXTS:
                title, artist, duration = _read_tags(path)
                found.append(
                    DownloadedFile(
                        path=path,
                        kind="audio",
                        title=title or path.stem,
                        artist=artist,
                        duration=duration,
                        size=path.stat().st_size,
                    )
                )
            elif ext in EXTRA_KINDS:
                found.append(
                    DownloadedFile(path=path, kind=EXTRA_KINDS[ext], title=path.stem, size=path.stat().st_size)
                )
        return found


downloader = Downloader()
