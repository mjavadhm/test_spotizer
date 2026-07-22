"""Per-user forum topics for organizing downloads (artist/album/playlist).

Port of the old bot's utils/topic_manager.py. Since this bot is stateless
(all durable data lives in the API), topic thread ids are stored in a small
local JSON file next to the bot.

Note: forum topics only work when the chat supports them (forum supergroups).
In plain private chats Telegram rejects createForumTopic; we then fall back
to the main chat, exactly like the old bot did.
"""
import json
import logging
import os
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)

TOPICS_FILE = Path(os.getenv("TOPICS_FILE", "topics.json"))


def _load() -> dict:
    try:
        return json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        TOPICS_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        logger.warning("Could not persist topics file: %s", e)


def _key(user_id: int, topic_type: str, topic_key: str) -> str:
    return f"{user_id}:{topic_type}:{topic_key}"


def get_topic_thread_id(user_id: int, topic_type: str, topic_key: str):
    """Return stored thread_id for this item, or None."""
    return _load().get(_key(user_id, topic_type, str(topic_key)))


def remove_topic(user_id: int, topic_type: str, topic_key: str) -> None:
    """Delete a stale topic record (e.g. user deleted the topic)."""
    data = _load()
    data.pop(_key(user_id, topic_type, str(topic_key)), None)
    _save(data)


async def get_or_create_topic(bot, user_id: int, topic_type: str, topic_key: str, title: str):
    """Return (thread_id, created). thread_id is None if creation failed."""
    existing = get_topic_thread_id(user_id, topic_type, topic_key)
    if existing:
        return existing, False

    try:
        topic = await bot.create_forum_topic(chat_id=user_id, name=str(title)[:128])
        thread_id = topic.message_thread_id
    except Exception as e:
        logger.warning("Could not create topic for user %s: %s", user_id, e)
        return None, False

    data = _load()
    data[_key(user_id, topic_type, str(topic_key))] = thread_id
    _save(data)
    return thread_id, True


async def send_safely(send_func, user_id: int, topic_type: str, topic_key: str, thread_id, **kwargs):
    """Send a message into a topic; on failure fall back to main chat and clean up.

    send_func: a bound bot method like bot.send_audio / bot.send_document / bot.send_message
    kwargs: all original arguments of that call (chat_id, document, caption, ...)
    """
    if thread_id:
        try:
            return await send_func(message_thread_id=thread_id, **kwargs)
        except TelegramBadRequest as e:
            logger.warning("Topic send failed (%s); falling back to main chat", e)
            remove_topic(user_id, topic_type, topic_key)
    return await send_func(**kwargs)
