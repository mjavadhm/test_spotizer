# Spotizer Telegram Bot

Thin client on top of the Spotizer API — no database, no deemix, no Telethon here.
Everything (users, search, downloads, cache, history) goes through the API.

## Run

```bash
cd bot_telegram
pip install -r requirements.txt
cp .env.example .env   # set BOT_TOKEN (+ API_BASE_URL, CLIENT_KEY if auth is enabled)
python bot.py
```

## What it does (full parity with the old Spotizer bot)

| User action | Bot behaviour |
|---|---|
| `/start` `/help` `/about` | old bot's welcome/help/about texts, `POST /v1/users/resolve` → unified `user_id` |
| text message | "What would you like to search for '…'?" → Tracks / Albums / Playlists / Artist |
| search results | 5 per page, Previous/Next, tap → full card (cover + info + actions) |
| track card | Download, Artist, Album, ➕ Add to Playlist, ❌ |
| album card | Download Album, 🧵 Create Topic, 📋 View Tracks, Artist, ❌ |
| artist card | 🔝 Top Tracks, 💿 Albums, 👥 Related Artists, ⬇️ Discography, 🔔 Follow, 🧵 Topic |
| Deezer/Spotify link | `POST /v1/links/resolve` → download starts with user settings |
| download | `POST /v1/downloads` (quality/ZIP from `/settings`) → cache hit? resend by `file_id` : poll job, send audio/zip, `POST /v1/files/report` → rating keyboard 👍/👎 on tracks |
| `/settings` | Change Quality (MP3_128/320/FLAC) + Make ZIP toggle |
| `/history` | last downloads incl. 👍/👎 rating |
| `/newplaylist` `/playlists` | personal playlists: create, add tracks, view (paginated), remove tracks, Download All, delete |
| `/recommend` | LLM (Gemini) picks based on liked/disliked history → tap to download |
| `/subscriptions` + 🔔 Follow | follow artists; a background task polls `GET /v1/subscriptions/new-releases` and notifies about new albums |
| 🧵 Create Topic | in forum chats sends that item's files into its own topic (falls back to main chat otherwise) |
| `/link` | `POST /v1/users/{id}/link-code` → 6-char code for the Android app |

## Notes

- Standard Bot API caps uploads at 50MB — for FLAC albums set `TELEGRAM_API_SERVER`
  to a [local Bot API server](https://github.com/tdlib/telegram-bot-api) (2GB limit).
- The Bale bot can reuse this exact structure: only the SDK calls differ, the
  API client (`api_client.py`) is platform-agnostic (just change `CLIENT_KEY`).
