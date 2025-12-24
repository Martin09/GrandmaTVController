import argparse
import asyncio
import json
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
    key_file: Path = Path("tv_key_store.json")
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
        self.client_key: str | None = self._load_key()
        # Initialize client with the key (if we have one)
        self.client = WebOsClient(config.ip, client_key=self.client_key)

    def _load_key(self) -> str | None:
        if self.config.key_file.exists():
            try:
                data = json.loads(self.config.key_file.read_text())
                return data.get("client_key")
            except json.JSONDecodeError:
                logger.warning("Key file corrupted. Re-pairing will be required.")
        return None

    def _save_key(self, key: str):
        data = {"client_key": key}
        self.config.key_file.write_text(json.dumps(data, indent=2))
        logger.info(f"Pairing key saved to {self.config.key_file}")

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


async def main():
    parser = argparse.ArgumentParser(description="Grandma's TV Controller")
    parser.add_argument("--action", type=str, default="channel_1", help=f"Choose action: {', '.join(ACTIONS.keys())}")
    args = parser.parse_args()

    sequence = ACTIONS.get(args.action)
    if not sequence:
        print(f"Error: Action '{args.action}' not found.")
        print(f"Available actions: {', '.join(ACTIONS.keys())}")
        sys.exit(1)

    # Load TV connection config from YAML. Prefer `config.yml`, fall back to# `config.yml.example`
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

    ip = cfg_data.get("ip")
    mac = cfg_data.get("mac")
    key_file_val = cfg_data.get("key_file")
    key_file = Path(key_file_val) if key_file_val else Path("tv_key_store.json")

    config = TVConfig(ip=ip, mac=mac, key_file=key_file, sequence=sequence)

    # # 1. Wake TV
    # await WakeOnLanService.wake_device(config.mac, config.ip) # FIXME: Not working

    # 2. Run Controller
    controller = TVController(config)
    await controller.run_sequence()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
