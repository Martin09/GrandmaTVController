import argparse
import asyncio
import logging
import sys

from core import ACTIONS, TVController, WakeOnLanService, load_config

# --- Main Entry Point ---

logger = logging.getLogger("GrandmaTV")


async def main(action: str | None = None) -> None:
    """Main entry point for TV control.

    Args:
        action: The action to execute (e.g., 'channel_1'). Auto-wakes TV if connection fails.
                If None, only wakes the TV (for debugging).
    """

    try:
        cfg_data = load_config()
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)

    # Wake-only mode (no action specified)
    if not action:
        mac = cfg_data.get("mac") or ""
        ip = cfg_data.get("ip") or ""
        if not mac:
            logger.error("MAC address not configured in config.yml")
            sys.exit(1)
        await WakeOnLanService.wake_device(mac, ip)
        return

    # Run action with auto-retry
    try:
        msg = await TVController.execute_action_with_retry(action, cfg_data)
        logger.info(msg)
    except Exception as e:
        logger.error(f"Failed to execute action '{action}': {e}")
        sys.exit(1)


def run_bot() -> None:
    """Run the Telegram bot (manages its own event loop)."""
    from telegram_bot import TelegramBotService, load_telegram_config

    try:
        cfg_data, telegram_config = load_telegram_config()
    except (FileNotFoundError, ValueError) as e:
        logger.error(str(e))
        sys.exit(1)

    bot = TelegramBotService(cfg_data, telegram_config)
    bot.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grandma's TV Controller")
    parser.add_argument("--action", type=str, help=f"Run action (auto-wakes if TV is off): {', '.join(ACTIONS.keys())}")
    parser.add_argument("--bot", action="store_true", help="Run as Telegram bot")
    args = parser.parse_args()

    try:
        if args.bot:
            run_bot()
        elif args.action:
            asyncio.run(main(action=args.action))
        else:
            # Default to wake-only mode if no other arguments are provided
            logger.info("No action specified, defaulting to Wake-on-LAN mode...")
            asyncio.run(main())

    except KeyboardInterrupt:
        pass
