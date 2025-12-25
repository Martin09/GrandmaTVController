"""Telegram Bot Service for TV Controller.

This module provides a Telegram bot interface to control the TV
via predefined action commands like /channel_1, /channel_2, etc.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from aiohttp.client_exceptions import WSMessageTypeError
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Import from main module
from main import ACTIONS, TVConfig, TVController, WakeOnLanService

logger = logging.getLogger("GrandmaTV.TelegramBot")


@dataclass
class TelegramConfig:
    """Configuration for the Telegram bot."""

    bot_token: str
    allowed_chat_ids: list[int]


def load_config(config_path: Path = Path("config.yml")) -> tuple[dict[str, Any], TelegramConfig]:
    """Load configuration from YAML file.

    Args:
        config_path: Path to the configuration file.

    Returns:
        Tuple of (raw config dict, TelegramConfig).

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValueError: If required telegram config is missing.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    cfg_data = yaml.safe_load(config_path.read_text()) or {}

    telegram_cfg = cfg_data.get("telegram", {})
    bot_token = telegram_cfg.get("bot_token")

    if not bot_token or bot_token == "YOUR_BOT_TOKEN_HERE":
        raise ValueError("Telegram bot_token is not configured. Please set 'telegram.bot_token' in config.yml")

    allowed_chat_ids = telegram_cfg.get("allowed_chat_ids", [])

    return cfg_data, TelegramConfig(bot_token=bot_token, allowed_chat_ids=allowed_chat_ids)


class TelegramBotService:
    """Telegram bot service for TV control.

    Provides command handlers for each defined action and manages
    authorization based on allowed chat IDs.
    """

    def __init__(self, cfg_data: dict[str, Any], telegram_config: TelegramConfig):
        """Initialize the Telegram bot service.

        Args:
            cfg_data: Raw configuration dictionary (for TV settings).
            telegram_config: Telegram-specific configuration.
        """
        self.cfg_data = cfg_data
        self.telegram_config = telegram_config
        self.application: Application | None = None

    def _is_authorized(self, chat_id: int) -> bool:
        """Check if a chat ID is authorized to use the bot.

        Args:
            chat_id: The Telegram chat ID to check.

        Returns:
            True if authorized, False otherwise.
        """
        # If no allowed_chat_ids configured, deny all (secure by default)
        if not self.telegram_config.allowed_chat_ids:
            logger.warning(
                f"UNAUTHORIZED: Chat ID {chat_id} attempted access. "
                f"No allowed_chat_ids configured. Add this ID to config.yml to allow access."
            )
            return False

        if chat_id not in self.telegram_config.allowed_chat_ids:
            logger.warning(
                f"UNAUTHORIZED: Chat ID {chat_id} attempted access. "
                f"To allow this chat, add {chat_id} to 'telegram.allowed_chat_ids' in config.yml"
            )
            return False

        return True

    def _create_tv_config(self, action_name: str) -> TVConfig:
        """Create a TVConfig for the given action.

        Args:
            action_name: Name of the action to execute.

        Returns:
            Configured TVConfig instance.
        """
        sequence = ACTIONS.get(action_name, [])
        return TVConfig(
            ip=self.cfg_data.get("ip", ""),
            mac=self.cfg_data.get("mac", ""),
            client_key=self.cfg_data.get("client_key"),
            sequence=sequence,
        )

    async def _execute_action(self, action_name: str) -> str:
        """Execute a TV action with auto-retry on connection failure.

        Args:
            action_name: Name of the action to execute.

        Returns:
            Status message describing the result.
        """
        if action_name not in ACTIONS:
            return f"Unknown action: {action_name}"

        try:
            config = self._create_tv_config(action_name)
            controller = TVController(config)
            await controller.run_sequence()
            return f"Action '{action_name}' completed successfully!"
        except WSMessageTypeError:
            logger.warning(f"TV appears to be off during '{action_name}', attempting Wake-on-LAN...")

            # Try to wake the TV and retry
            mac = self.cfg_data.get("mac", "")
            ip = self.cfg_data.get("ip", "")
            if not mac:
                return "TV appears to be off and MAC address is not configured for Wake-on-LAN."

            try:
                await WakeOnLanService.wake_device(mac, ip)
                logger.info(f"Retrying action '{action_name}' after wake...")
                config = self._create_tv_config(action_name)
                controller = TVController(config)
                await controller.run_sequence()
                return f"TV was woken up. Action '{action_name}' completed successfully!"
            except Exception as retry_error:
                logger.exception(f"Retry failed for '{action_name}'")
                return f"Failed to wake TV or execute action: {retry_error}"
        except Exception as e:
            logger.exception(f"Error executing action '{action_name}'")
            return f"Error executing '{action_name}': {e}"

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle the /start command - show available actions.

        Args:
            update: Telegram update object.
            context: Callback context.
        """
        if not update.effective_chat or not update.message:
            return

        chat_id = update.effective_chat.id

        if not self._is_authorized(chat_id):
            await update.message.reply_text(
                "You are not authorized to use this bot.\nContact the administrator to request access."
            )
            return

        action_list = "\n".join(f"/{action}" for action in ACTIONS.keys())
        await update.message.reply_text(
            f"*Grandma's TV Controller*\n\n"
            f"Available commands:\n/wake\n{action_list}\n\n"
            f"Tap a command to control the TV!",
            parse_mode="Markdown",
        )

    async def wake_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle the /wake command - wake up the TV via Wake-on-LAN.

        Args:
            update: Telegram update object.
            context: Callback context.
        """
        if not update.effective_chat or not update.message:
            return

        chat_id = update.effective_chat.id

        if not self._is_authorized(chat_id):
            await update.message.reply_text(
                "You are not authorized to use this bot.\nContact the administrator to request access."
            )
            return

        logger.info(f"Waking TV for chat {chat_id}")

        status_msg = await update.message.reply_text("Sending Wake-on-LAN to TV...")

        try:
            mac = self.cfg_data.get("mac", "")
            ip = self.cfg_data.get("ip", "")

            if not mac:
                await status_msg.edit_text("Error: MAC address not configured.")
                return

            await WakeOnLanService.wake_device(mac, ip)
            await status_msg.edit_text("Wake-on-LAN sent! TV should be waking up.")
        except Exception as e:
            logger.exception("Error sending Wake-on-LAN")
            await status_msg.edit_text(f"Error: {e}")

    async def action_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE, action_name: str) -> None:
        """Handle an action command.

        Args:
            update: Telegram update object.
            context: Callback context.
            action_name: Name of the action to execute.
        """
        if not update.effective_chat or not update.message:
            return

        chat_id = update.effective_chat.id

        if not self._is_authorized(chat_id):
            await update.message.reply_text(
                "You are not authorized to use this bot.\nContact the administrator to request access."
            )
            return

        logger.info(f"Executing action '{action_name}' for chat {chat_id}")

        # Send "working on it" message
        status_msg = await update.message.reply_text(f"Executing '{action_name}'...")

        # Execute the action
        result = await self._execute_action(action_name)

        # Update with result
        await status_msg.edit_text(result)

    def _create_action_handler(self, action_name: str):
        """Create a command handler for a specific action.

        Args:
            action_name: Name of the action.

        Returns:
            Async handler function for the action.
        """

        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await self.action_handler(update, context, action_name)

        return handler

    async def _set_bot_commands(self, app: Application) -> None:
        """Set the bot's command menu in Telegram.

        Args:
            app: The Telegram application instance.
        """
        commands = [
            BotCommand("start", "Show available actions"),
            BotCommand("wake", "Wake up the TV"),
        ]
        commands.extend(BotCommand(action, f"Execute {action} on TV") for action in ACTIONS.keys())
        await app.bot.set_my_commands(commands)
        logger.info("Bot commands menu updated")

    def run(self) -> None:
        """Start the bot and run until interrupted."""
        logger.info("Starting Telegram bot...")

        # Build the application with post_init callback
        self.application = (
            Application.builder().token(self.telegram_config.bot_token).post_init(self._set_bot_commands).build()
        )

        # Add /start handler
        self.application.add_handler(CommandHandler("start", self.start_command))

        # Add /wake handler
        self.application.add_handler(CommandHandler("wake", self.wake_command))
        logger.info("Registered command: /wake")

        # Add handler for each action
        for action_name in ACTIONS.keys():
            handler = self._create_action_handler(action_name)
            self.application.add_handler(CommandHandler(action_name, handler))
            logger.info(f"Registered command: /{action_name}")

        logger.info("Bot is running. Press Ctrl+C to stop.")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)


def main() -> None:
    """Entry point for running the Telegram bot standalone."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        cfg_data, telegram_config = load_config()
    except (FileNotFoundError, ValueError) as e:
        logger.error(str(e))
        return

    bot = TelegramBotService(cfg_data, telegram_config)
    bot.run()


if __name__ == "__main__":
    main()
