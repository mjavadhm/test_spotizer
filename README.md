# Spotizer API

Multi-client backend for Spotizer. Telegram bot, Bale bot, the Android app, and any future client are **thin clients** on top of this single REST API.

- **Bots** (Telegram/Bale): job-based flow, temp files with TTL, caching via each platform's `file_id`.
- **Android app**: `/v1/tracks/*` endpoints — search + direct download + **streaming with seek** (HTTP Range / 206), so the user can listen first and download after.
- Streaming is served from a **permanent local library** (`MUSIC_DIR`): a track is downloaded from Deezer at most once per quality; after that stream/download are instant.
- No Telethon, no JWT — one shared secret per client (`X-Client-Key`).

## Run (dev)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit: CLIENT_KEYS, DEEZER_ARL, DATABASE_URL
uvicorn app.main:app --reload
```

OpenAPI docs: http://localhost:8000/docs

## Client auth (optional)

Auth is **off by default**: if `CLIENT_KEYS` in `.env` is empty, every request passes.

To enable simple API-key auth, define keys in `.env`:

```
CLIENT_KEYS=android:secret1,telegram:secret2
```

Clients then send the key in the `X-Client-Key` header, or as a `?key=<client-key>` query param (for media players / download managers that can't set headers). The backend derives the platform from the key — clients never send a `platform` field.

## Android app flow (v1: search + download, stream optional)

1. **Search** — `GET /v1/search?q=eminem&type=track&limit=20` (Deezer metadata: id, title, artist, album, cover, duration).
2. **(optional) Status** — `GET /v1/tracks/{deezer_track_id}/status?quality=MP3_320` → `{ "cached": true/false, ... }` so the app can show "instant" vs "preparing".
3. **Stream (listen before download)** — point ExoPlayer/Media3 at:
   `GET /v1/tracks/{id}/stream?quality=MP3_320`
   - Range requests supported (seek works, 206 Partial Content).
   - Cache miss → the server downloads the track on demand (deduped: many listeners share one download), then streams it. Request waits up to `ON_DEMAND_TIMEOUT_SECONDS`; on 504 just retry.
4. **Download** — `GET /v1/tracks/{id}/download?quality=MP3_320[&user_id=123]`
   - `Content-Disposition: attachment; filename="Artist - Title.mp3"`, Range supported (resumable via DownloadManager).
   - Pass `user_id` (from `POST /v1/users/resolve`) to log the download into history.
5. **Lyrics** — `GET /v1/tracks/{id}/lyrics?quality=MP3_320` → synced `.lrc` text (404 if unavailable).

Qualities: `MP3_128`, `MP3_320`, `FLAC`. Every track downloaded via a bot job is also archived into the library, so the stream cache grows for free.

## Typical flow (what a bot does)

1. `/start` → `POST /v1/users/resolve` → get `user_id`.
2. User sends a link → `POST /v1/links/resolve` → show info card + download button.
3. Button pressed → `POST /v1/downloads`.
   - `cached: true` → send `platform_file_id` immediately. Done.
   - else → got `job_id`, poll `GET /v1/downloads/{job_id}` every 2–3s and edit the progress message.
4. `status: ready` → fetch bytes from each file's `temp_url`, upload to the platform.
5. `POST /v1/files/report` with the resulting `platform_file_id` → next time it's a cache hit.

## Notes

- **deemix**: `app/services/downloader.py` calls the deemix CLI in a subprocess. If you
  prefer the deemix python API already used in the old Spotizer `services/deezer_service.py`,
  replace the body of `Downloader.download` — the interface stays the same.
- **ARL**: set `DEEZER_ARL` in `.env`; it's written to deemix's config as `.arl`.
- **Migrations**: `init_db()` runs `create_all` on startup (fine for dev). For production,
  set up alembic (`alembic init`, point it at `app.db.Base.metadata`).
- **Webhook**: `callback_url` is accepted and stored but not delivered yet — polling only for now.

## Structure

```
app/
├── main.py        # FastAPI app + lifespan (db init, workers, cleanup)
├── config.py      # env settings
├── deps.py        # X-Client-Key -> platform
├── db.py          # async engine/session
├── models/        # SQLAlchemy models
├── schemas/       # Pydantic request/response
├── routers/       # users / catalog / downloads / tracks (stream) / history
└── services/      # deezer client / deemix downloader / queue / library / cleanup

bot_telegram/      # Telegram bot (thin client over this API) — see its README
```

## Streaming internals

- `services/library.py` — permanent track store under `MUSIC_DIR/{track_id}/`; on-demand downloads are deduplicated per `(track_id, quality)` and shielded from client disconnects.
- `routers/tracks.py` — Range header parsing + `StreamingResponse` (ported from the old `backend/routers/stream.py`, but serving local files instead of Telegram/Telethon).
- Library files are **not** touched by the TTL cleanup (that only affects job temp files).

## Local diagnostics and request correlation

The app sends a fresh UUID in `X-Request-ID` for each HTTP call. The API validates
that header (UUID or 32 hex digits), generates a replacement when absent/invalid,
and echoes the accepted ID on successful, validation, authorization and failed
responses. An unhandled error before response headers produces:

```json
{"error":"internal_server_error","request_id":"<the same ID as the response header>"}
```

After streaming starts, an error is logged but cannot replace the partial audio
with JSON. A proxy-generated failure may not reach the application at all.

Configuration (environment variables or `.env`):

```dotenv
DIAGNOSTICS_DIR=./diagnostics
DIAGNOSTICS_MAX_BYTES=20971520
```

This is a **20 MiB size-only journal by default, with no time expiry**. Oldest
segments rotate when the next event would exceed the cap. The directory must be
writable, private, and on a persistent local volume shared by workers. File locks
coordinate rotation across workers. This Linux deployment uses `fcntl.flock`.
Do not serve this directory through HTTP or commit it. Disk-full/write failures
must not break an otherwise successful API request.

JSONL records contain time, route **template**, method, HTTP status, elapsed time,
request ID and, for exceptions, type, stack-frame file/line/function and a limited
operational category. Request/response bodies, raw queries, credentials, raw
exception messages, SQL parameters and user content are not recorded here. The
same sanitized records are emitted under `spotizer.diagnostics` to stderr.
HTTP errors, streaming exceptions, download worker/archive errors, startup,
shutdown and cleanup errors are covered. This is not monitoring for OS kills,
resource exhaustion, or every third-party logger.

To investigate an app report, find its request ID in `diagnostics/*.jsonl` (or
your service's console logs). Match the stack line with the deployed revision.
A category such as `schema_missing_table` is a clue, not an automatic repair.
The existing download/status HTTP 500 still needs a fresh trace after deployment.

### Reverse proxies

Preserve an incoming app `X-Request-ID`; do not blindly replace it with a proxy
ID. An optional Nginx configuration uses a generated fallback only when missing:

```nginx
# http context
map $http_x_request_id $spotizer_request_id {
    ""      $request_id;
    default $http_x_request_id;
}
log_format spotizer_safe '$time_iso8601 $request_method $status '
                         'rid=$upstream_http_x_request_id proxy_rid=$request_id '
                         'duration=$request_time';
# Inside the existing API location:
# proxy_set_header X-Request-ID $spotizer_request_id;
# access_log /var/log/nginx/spotizer-safe.log spotizer_safe;
```

Nginx passes upstream response headers by default. Avoid adding a second
`X-Request-ID` response header. Avoid `$request`, `$request_uri`, query strings and
credential headers in diagnostic access logs. Configure console/proxy log rotation
separately: the journal's 20 MiB cap does **not** cap Docker, journald, Nginx or
third-party logs. Export server JSONL files only through trusted admin access;
there is intentionally no public diagnostics endpoint or automatic upload.

Deploy the changed code and restart the service to activate it. No database
migration is required for diagnostics. Test with:

```bash
pip install pytest httpx
python -m pytest -q tests/test_diagnostics.py
```
