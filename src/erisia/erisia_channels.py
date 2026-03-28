"""
Erisia v0.2 — Communication Channels
======================================
Multi-channel input/output inspired by OpenJarvis's 12+ channel support.

Starts with Telegram as the primary remote channel, with a BaseChannel
abstraction for future Discord, WhatsApp, etc.

Features:
  - Telegram bot with /ask, /goals, /health, /briefing commands
  - Push notifications for events (skill forged, mission complete)
  - Full tool dispatch through same pipeline as terminal
  - EventBus integration for real-time alerts

Requirements:
  pip install python-telegram-bot

Usage:
  Set TELEGRAM_BOT_TOKEN in your .env file
  The bot starts automatically when Erisia launches (if token is present)
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import threading
from abc import ABC, abstractmethod
from typing import Any, Optional, Callable

logger = logging.getLogger("erisia.channels")


def _load_telegram_ext() -> tuple[Any, Any, Any, Any]:
    """Lazily import telegram.ext so Telegram support can stay optional."""
    telegram_ext = importlib.import_module("telegram.ext")
    return (
        telegram_ext.ApplicationBuilder,
        telegram_ext.CommandHandler,
        telegram_ext.MessageHandler,
        telegram_ext.filters,
    )


# ═══════════════════════════════════════════════════════════════════════
# BASE CHANNEL ABSTRACTION
# ═══════════════════════════════════════════════════════════════════════

class BaseChannel(ABC):
    """Abstract base for all communication channels."""

    name: str = "base"

    @abstractmethod
    def start(self) -> None:
        """Start the channel listener."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the channel listener."""
        ...

    @abstractmethod
    def send_message(self, text: str, **kwargs: Any) -> None:
        """Send a message through this channel."""
        ...

    def is_running(self) -> bool:
        """Check if the channel is active."""
        return False


# ═══════════════════════════════════════════════════════════════════════
# TELEGRAM CHANNEL
# ═══════════════════════════════════════════════════════════════════════

class TelegramChannel(BaseChannel):
    """
    Telegram bot channel for remote Erisia interaction.

    Commands:
      /ask <query>  — Ask Erisia a question
      /goals        — View current goal stack
      /health       — Run system diagnostic
      /briefing     — Get a status briefing
      /skills       — List active skills
      /stats        — Telemetry summary
    """

    name = "telegram"

    def __init__(
        self,
        bot_token: str,
        query_handler: Optional[Callable[[str], str]] = None,
        allowed_user_ids: Optional[set[int]] = None,
    ) -> None:
        self._bot_token = bot_token
        self._query_handler = query_handler
        self._allowed_users = allowed_user_ids
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._app: Any = None

    def _is_authorized(self, user_id: int) -> bool:
        """Check if a user is authorized to interact."""
        if self._allowed_users is None:
            return True  # No restriction set
        return user_id in self._allowed_users

    def start(self) -> None:
        """Start the Telegram bot in a background thread."""
        if self._running:
            return

        try:
            _load_telegram_ext()
        except ImportError:
            logger.warning(
                "python-telegram-bot not installed. "
                "Run: pip install python-telegram-bot"
            )
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_bot,
            daemon=True,
            name="erisia-telegram",
        )
        self._thread.start()
        logger.info("Telegram channel started")

    def _run_bot(self) -> None:
        """Run the bot event loop in a dedicated thread."""
        try:
            (
                ApplicationBuilder,
                CommandHandler,
                MessageHandler,
                filters,
            ) = _load_telegram_ext()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            self._app = (
                ApplicationBuilder()
                .token(self._bot_token)
                .build()
            )

            # Register handlers
            self._app.add_handler(CommandHandler("start", self._cmd_start))
            self._app.add_handler(CommandHandler("ask", self._cmd_ask))
            self._app.add_handler(CommandHandler("goals", self._cmd_goals))
            self._app.add_handler(CommandHandler("health", self._cmd_health))
            self._app.add_handler(CommandHandler("briefing", self._cmd_briefing))
            self._app.add_handler(CommandHandler("skills", self._cmd_skills))
            self._app.add_handler(CommandHandler("stats", self._cmd_stats))

            # Handle plain text as queries
            self._app.add_handler(
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    self._handle_text,
                )
            )

            logger.info("Telegram bot polling...")
            self._app.run_polling(stop_signals=None)

        except Exception as exc:
            logger.error("Telegram bot error: %s", exc, exc_info=True)
            self._running = False

    async def _cmd_start(self, update: Any, context: Any) -> None:
        """Handle /start command."""
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text(
                "Access denied. You are not authorized to interact with Erisia."
            )
            return
        await update.message.reply_text(
            "Erisia v0.2 Online.\n\n"
            "Commands:\n"
            "/ask <query> - Ask me anything\n"
            "/goals - View active goals\n"
            "/health - System diagnostic\n"
            "/briefing - Status briefing\n"
            "/skills - List skills\n"
            "/stats - Telemetry stats\n\n"
            "Or just type a message and I'll respond."
        )

    async def _cmd_ask(self, update: Any, context: Any) -> None:
        """Handle /ask <query> command."""
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Access denied.")
            return

        query = " ".join(context.args) if context.args else ""
        if not query:
            await update.message.reply_text("Usage: /ask <your question>")
            return

        response = self._process_query(query)
        # Telegram has a 4096 char limit
        for chunk in self._chunk_text(response, 4000):
            await update.message.reply_text(chunk)

    async def _cmd_goals(self, update: Any, context: Any) -> None:
        """Handle /goals command."""
        if not self._is_authorized(update.effective_user.id):
            return
        try:
            from erisia.erisia_config import get_config
            from erisia.erisia_cognition import GoalStack

            cfg = get_config()
            gs = GoalStack(str(cfg.paths.goal_stack_file))
            summary = gs.summary_text(limit=8)
            await update.message.reply_text(f"Active Goals:\n\n{summary}")
        except Exception as exc:
            await update.message.reply_text(f"Error loading goals: {exc}")

    async def _cmd_health(self, update: Any, context: Any) -> None:
        """Handle /health command."""
        if not self._is_authorized(update.effective_user.id):
            return
        try:
            from erisia.erisia_doctor import doctor_as_tool
            result = doctor_as_tool()
            await update.message.reply_text(f"System Health:\n\n{result}")
        except Exception as exc:
            await update.message.reply_text(f"Error running diagnostic: {exc}")

    async def _cmd_briefing(self, update: Any, context: Any) -> None:
        """Handle /briefing command."""
        if not self._is_authorized(update.effective_user.id):
            return
        try:
            from erisia.erisia_learning import get_learning_loop
            from erisia.erisia_telemetry import get_telemetry_store

            learning = get_learning_loop()
            store = get_telemetry_store()
            daily = store.daily_summary(days=1)

            lines = ["Erisia Status Briefing", ""]
            lines.append(learning.summary_for_briefing())
            lines.append("")
            if daily:
                lines.append("## Today's Telemetry")
                lines.append(f"- Calls: {daily.get('total_calls', 0)}")
                lines.append(f"- Cost: ${daily.get('total_cost_usd', 0):.4f}")
                lines.append(f"- Local rate: {daily.get('local_rate', 0):.1f}%")
                lines.append(f"- Avg latency: {daily.get('avg_latency_ms', 0):.0f}ms")

            await update.message.reply_text("\n".join(lines))
        except Exception as exc:
            await update.message.reply_text(f"Error generating briefing: {exc}")

    async def _cmd_skills(self, update: Any, context: Any) -> None:
        """Handle /skills command."""
        if not self._is_authorized(update.effective_user.id):
            return
        try:
            from erisia.erisia_config import get_config
            cfg = get_config()
            skills_dir = cfg.paths.skills_dir
            pending_dir = cfg.paths.pending_skills_dir

            active = sorted(skills_dir.glob("*.py")) if skills_dir.exists() else []
            pending = sorted(pending_dir.glob("*.py")) if pending_dir.exists() else []

            lines = ["Active Skills:"]
            for s in active:
                lines.append(f"  - {s.stem}")
            if pending:
                lines.append("\nPending Approval:")
                for s in pending:
                    lines.append(f"  - {s.stem}")

            await update.message.reply_text("\n".join(lines))
        except Exception as exc:
            await update.message.reply_text(f"Error listing skills: {exc}")

    async def _cmd_stats(self, update: Any, context: Any) -> None:
        """Handle /stats command."""
        if not self._is_authorized(update.effective_user.id):
            return
        try:
            from erisia.erisia_telemetry import get_telemetry_store
            store = get_telemetry_store()
            daily = store.daily_summary(days=7)
            dist = store.engine_distribution(days=7)

            lines = ["7-Day Telemetry:"]
            lines.append(f"  Total calls: {daily.get('total_calls', 0)}")
            lines.append(f"  Total cost: ${daily.get('total_cost_usd', 0):.4f}")
            lines.append(f"  Avg latency: {daily.get('avg_latency_ms', 0):.0f}ms")
            lines.append(f"  Local rate: {daily.get('local_rate', 0):.1f}%")
            lines.append(f"  Fallback rate: {daily.get('fallback_rate', 0):.1f}%")

            if dist:
                lines.append("\nEngine Distribution:")
                for engine, count in dist.items():
                    lines.append(f"  {engine}: {count} calls")

            await update.message.reply_text("\n".join(lines))
        except Exception as exc:
            await update.message.reply_text(f"Error loading stats: {exc}")

    async def _handle_text(self, update: Any, context: Any) -> None:
        """Handle plain text messages as queries."""
        if not self._is_authorized(update.effective_user.id):
            return

        query = update.message.text.strip()
        if not query:
            return

        response = self._process_query(query)
        for chunk in self._chunk_text(response, 4000):
            await update.message.reply_text(chunk)

    def _process_query(self, query: str) -> str:
        """Route a query through the handler or return a simple response."""
        if self._query_handler:
            try:
                return self._query_handler(query)
            except Exception as exc:
                return f"Error processing query: {exc}"
        return "Query handler not connected. Telegram channel is in standalone mode."

    @staticmethod
    def _chunk_text(text: str, max_len: int = 4000) -> list[str]:
        """Split text into chunks that fit Telegram's message limit."""
        if len(text) <= max_len:
            return [text]
        chunks = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            # Find a good break point
            split_at = text.rfind("\n", 0, max_len)
            if split_at == -1:
                split_at = max_len
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
        return chunks

    def send_message(self, text: str, **kwargs: Any) -> None:
        """Send a push notification (requires chat_id)."""
        chat_id = kwargs.get("chat_id")
        if not chat_id or not self._app:
            return
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(
                self._app.bot.send_message(chat_id=chat_id, text=text)
            )
            loop.close()
        except Exception as exc:
            logger.error("Failed to send Telegram message: %s", exc)

    def stop(self) -> None:
        """Stop the Telegram bot."""
        self._running = False
        if self._app:
            try:
                self._app.stop_running()
            except Exception:
                pass
        logger.info("Telegram channel stopped")

    def is_running(self) -> bool:
        return self._running


# ═══════════════════════════════════════════════════════════════════════
# CHANNEL MANAGER
# ═══════════════════════════════════════════════════════════════════════

class ChannelManager:
    """
    Manages all communication channels.
    Starts/stops channels based on available configuration.
    """

    def __init__(self) -> None:
        self._channels: dict[str, BaseChannel] = {}
        self._query_handler: Optional[Callable[[str], str]] = None

    def set_query_handler(self, handler: Callable[[str], str]) -> None:
        """Set the function that processes incoming queries."""
        self._query_handler = handler

    def register_channel(self, channel: BaseChannel) -> None:
        """Register a channel."""
        self._channels[channel.name] = channel
        logger.info("Registered channel: %s", channel.name)

    def start_all(self) -> None:
        """Start all registered channels."""
        for name, channel in self._channels.items():
            try:
                channel.start()
            except Exception as exc:
                logger.error("Failed to start channel %s: %s", name, exc)

    def stop_all(self) -> None:
        """Stop all channels."""
        for name, channel in self._channels.items():
            try:
                channel.stop()
            except Exception as exc:
                logger.error("Failed to stop channel %s: %s", name, exc)

    def broadcast(self, text: str, **kwargs: Any) -> None:
        """Send a message to all active channels."""
        for channel in self._channels.values():
            if channel.is_running():
                try:
                    channel.send_message(text, **kwargs)
                except Exception:
                    pass

    def status(self) -> dict[str, bool]:
        """Return running status of all channels."""
        return {
            name: channel.is_running()
            for name, channel in self._channels.items()
        }


# ═══════════════════════════════════════════════════════════════════════
# AUTO-SETUP
# ═══════════════════════════════════════════════════════════════════════

_manager: Optional[ChannelManager] = None


def get_channel_manager() -> ChannelManager:
    """Return the global ChannelManager singleton."""
    global _manager
    if _manager is None:
        _manager = ChannelManager()
    return _manager


def auto_setup_channels(
    query_handler: Optional[Callable[[str], str]] = None,
) -> ChannelManager:
    """
    Auto-detect available channels from config and set them up.
    Call this during Erisia startup.
    """
    import os
    manager = get_channel_manager()

    if query_handler:
        manager.set_query_handler(query_handler)

    # Telegram
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_users_raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
    allowed_users = None
    if telegram_users_raw:
        try:
            allowed_users = {int(u.strip()) for u in telegram_users_raw.split(",") if u.strip()}
        except ValueError:
            pass

    if telegram_token:
        tg = TelegramChannel(
            bot_token=telegram_token,
            query_handler=query_handler,
            allowed_user_ids=allowed_users,
        )
        manager.register_channel(tg)
        logger.info("Telegram channel configured")
    else:
        logger.info(
            "TELEGRAM_BOT_TOKEN not set — Telegram channel disabled. "
            "Add it to config/.env to enable."
        )

    return manager
