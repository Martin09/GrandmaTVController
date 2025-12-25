import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from aiowebostv import WebOsClient
from wakeonlan import send_magic_packet

# --- Configuration ---

# Setup Logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("GrandmaTV")

# Suppress noisy httpx logs (Telegram API polling)
logging.getLogger("httpx").setLevel(logging.WARNING)

# --- Action Definitions ---

# We define two types of actions for our macros:
# 1. ("APP", "app_id", delay)    -> Launch an app directly
# 2. ("BTN", "key_name", delay)  -> Press a remote button

ActionStep = tuple[str, str, float]

ACTIONS: dict[str, list[ActionStep]] = {
    "channel_1": [
        ("BTN", "HOME", 1.0),
        ("APP", "cz.tmobile.tvgo", 10.0),
        ("BTN", "RIGHT", 1.0),
        ("BTN", "ENTER", 1.0),
        ("BTN", "1", 1.0),
        ("BTN", "ENTER", 1.0),
        ("BTN", "ENTER", 1.0),
        ("BTN", "RIGHT", 1.0),
        ("BTN", "ENTER", 0.0),
    ],
    "channel_2": [
        ("BTN", "HOME", 1.0),
        ("APP", "cz.tmobile.tvgo", 10.0),
        ("BTN", "RIGHT", 1.0),
        ("BTN", "ENTER", 1.0),
        ("BTN", "2", 1.0),
        ("BTN", "ENTER", 1.0),
        ("BTN", "ENTER", 1.0),
        ("BTN", "RIGHT", 1.0),
        ("BTN", "ENTER", 0.0),
    ],
}


@dataclass
class TVConfig:
    ip: str
    mac: str
    client_key: str | None = None
    config_file: Path = Path("config.yml")
    sequence: list[ActionStep] = field(default_factory=list)


# --- Service Modules ---


class WakeOnLanService:
    """Handles waking up devices via magic packets."""

    @staticmethod
    async def wake_device(mac_address: str, ip_address: str, repeat: int = 3):
        logger.info(f"Sending Wake-on-LAN to {mac_address}...")
        for _ in range(repeat):
            send_magic_packet(mac_address)
            await asyncio.sleep(0.5)

        logger.info("Waiting 12s for TV network stack to initialize...")
        await asyncio.sleep(12)


class TVController:
    """
    Manages the connection and command execution for LG WebOS TV.
    """

    def __init__(self, config: TVConfig):
        self.config = config
        self.client_key: str | None = config.client_key
        # Initialize client with the key (if we have one)
        self.client = WebOsClient(config.ip, client_key=self.client_key)

    def _save_key(self, key: str):
        self.config.client_key = key
        # Read existing yaml to preserve other fields, then update key
        if self.config.config_file.exists():
            data = yaml.safe_load(self.config.config_file.read_text()) or {}
        else:
            data = {}

        data["client_key"] = key

        # Note: This will overwrite comments in the file
        self.config.config_file.write_text(yaml.dump(data, default_flow_style=False))
        logger.info(f"Pairing key saved to {self.config.config_file}")

    async def connect(self):
        """Connects to TV. Handles pairing if key is missing."""
        if self.client.is_connected():
            return
        logger.info(f"Connecting to TV at {self.config.ip}...")
        await self.client.connect()

        # Check if a new key was generated during connection
        current_key = self.client.client_key
        if current_key and current_key != self.client_key:
            logger.info("New pairing key detected.")
            self.client_key = current_key
            self._save_key(current_key)

        logger.info("Connected and authenticated.")

    async def run_sequence(self):
        """Executes the list of actions (Apps or Buttons)."""
        await self.connect()

        logger.info("Starting action sequence...")

        for i, (action_type, value, delay) in enumerate(self.config.sequence, 1):
            if action_type == "APP":
                logger.info(f"[{i}] Launching App: {value}")
                await self.client.launch_app(value)

            elif action_type == "BTN":
                logger.info(f"[{i}] Pressing Button: {value}")
                await self.client.button(value)

            if delay > 0:
                logger.info(f"    ...waiting {delay}s")
                await asyncio.sleep(delay)

        logger.info("Sequence complete.")

        if self.client.is_connected():
            await self.client.disconnect()
            logger.info("Disconnected.")


# --- Main Entry Point ---


async def main(action: str | None = None) -> None:
    """Main entry point for TV control.

    Args:
        action: The action to execute (e.g., 'channel_1'). Auto-wakes TV if connection fails.
                If None, only wakes the TV (for debugging).
    """
    from aiohttp.client_exceptions import WSMessageTypeError

    # Load TV connection config from YAML. Prefer `config.yml`, fall back to `config.yml.example`
    cfg_path = Path("config.yml")
    example_path = Path("config.yml.example")
    if cfg_path.exists():
        cfg_data = yaml.safe_load(cfg_path.read_text()) or {}
    else:
        logger.warning("config.yml not found; using config.yml.example")
        if example_path.exists():
            cfg_data = yaml.safe_load(example_path.read_text()) or {}
        else:
            logger.error("No configuration file found (config.yml or config.yml.example). Exiting.")
            sys.exit(1)

    ip = cfg_data.get("ip") or ""
    mac = cfg_data.get("mac") or ""
    client_key = cfg_data.get("client_key")

    # Wake-only mode (no action specified)
    if not action:
        if not mac:
            logger.error("MAC address not configured in config.yml")
            sys.exit(1)
        await WakeOnLanService.wake_device(mac, ip)
        return

    # Run action with auto-retry on connection failure
    sequence = ACTIONS.get(action)
    if not sequence:
        print(f"Error: Action '{action}' not found.")
        print(f"Available actions: {', '.join(ACTIONS.keys())}")
        sys.exit(1)

    config = TVConfig(ip=ip, mac=mac, client_key=client_key, sequence=sequence)
    controller = TVController(config)

    try:
        await controller.run_sequence()
    except WSMessageTypeError:
        logger.warning("TV appears to be off, attempting Wake-on-LAN...")
        if not mac:
            logger.error("MAC address not configured for Wake-on-LAN retry.")
            sys.exit(1)
        await WakeOnLanService.wake_device(mac, ip)
        logger.info(f"Retrying action '{action}' after wake...")
        controller = TVController(config)
        await controller.run_sequence()


def run_bot() -> None:
    """Run the Telegram bot (manages its own event loop)."""
    from telegram_bot import TelegramBotService, load_config

    try:
        cfg_data, telegram_config = load_config()
    except (FileNotFoundError, ValueError) as e:
        logger.error(str(e))
        sys.exit(1)

    bot = TelegramBotService(cfg_data, telegram_config)
    bot.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grandma's TV Controller")
    parser.add_argument("--action", type=str, help=f"Run action (auto-wakes if TV is off): {', '.join(ACTIONS.keys())}")
    parser.add_argument("--bot", action="store_true", help="Run as Telegram bot")
    parser.add_argument("--wake", action="store_true", help="Wake up the TV via Wake-on-LAN only (for debugging)")
    args = parser.parse_args()

    try:
        if args.bot:
            run_bot()
        elif args.wake:
            asyncio.run(main())
        elif args.action:
            asyncio.run(main(action=args.action))
        else:
            parser.print_help()
    except KeyboardInterrupt:
        pass
