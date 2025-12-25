import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from aiohttp.client_exceptions import WSMessageTypeError
from aiowebostv import WebOsClient
from wakeonlan import send_magic_packet

# --- Logging Setup ---
# We configure basic logging here to ensure it's available when this module is imported.
# Applications can override this configuration.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("GrandmaTV")

# Suppress noisy httpx logs
logging.getLogger("httpx").setLevel(logging.WARNING)


# --- Action Definitions ---

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


# --- Configuration ---


@dataclass
class TVConfig:
    ip: str
    mac: str
    client_key: str | None = None
    config_file: Path = Path("config.yml")
    sequence: list[ActionStep] = field(default_factory=list)


def load_config(config_path: Path = Path("config.yml")) -> dict[str, Any]:
    """
    Load configuration from YAML file.
    Tries `config.yml` first, then `config.yml.example`.
    """
    example_path = Path("config.yml.example")

    # If custom path is passed (and not just default), strictly check it
    if config_path != Path("config.yml"):
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        return yaml.safe_load(config_path.read_text()) or {}

    # Default logic: try config.yml, fallback to example
    if config_path.exists():
        return yaml.safe_load(config_path.read_text()) or {}

    logger.warning(f"{config_path} not found; looking for {example_path}")
    if example_path.exists():
        return yaml.safe_load(example_path.read_text()) or {}

    raise FileNotFoundError("No configuration file found (config.yml or config.yml.example).")


# --- Service Modules ---


class WakeOnLanService:
    """Handles waking up devices via magic packets."""

    @staticmethod
    async def wake_device(mac_address: str, ip_address: str, repeat: int = 3):
        if not mac_address:
            raise ValueError("MAC address not configured.")

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
                logger.debug(f"[{i}] Launching App: {value}")
                await self.client.launch_app(value)

            elif action_type == "BTN":
                logger.debug(f"[{i}] Pressing Button: {value}")
                await self.client.button(value)

            if delay > 0:
                logger.debug(f"    ...waiting {delay}s")
                await asyncio.sleep(delay)

        logger.info("Sequence complete.")

        if self.client.is_connected():
            try:
                await self.client.disconnect()
            except ConnectionResetError:
                pass  # TV may forcibly close the connection (benign)
            logger.info("Disconnected.")

    async def turn_off(self):
        """Turns off the TV."""
        await self.connect()
        logger.info("Turning off TV...")
        await self.client.power_off()

    @classmethod
    async def execute_action_with_retry(cls, action_name: str, config_data: dict[str, Any]) -> str:
        """
        Executes an action by name with auto-retry on Wake-on-LAN.
        Returns a status message - never raises exceptions to ensure server stability.

        Handles connection errors (WSMessageTypeError, TimeoutError, OSError, etc.)
        by attempting to wake the TV and retrying once.
        """
        ip = config_data.get("ip", "")
        mac = config_data.get("mac", "")
        client_key = config_data.get("client_key")

        # Helper to check if error indicates TV is off/sleeping/unreachable
        def is_tv_off_error(e: Exception) -> bool:
            return isinstance(e, (WSMessageTypeError, TimeoutError, OSError, ConnectionError))

        try:
            if action_name == "turn_off":
                # For turn_off, if TV is already off, that's fine - consider it success
                try:
                    controller = cls(TVConfig(ip=ip, mac=mac, client_key=client_key))
                    await controller.turn_off()
                    return "TV turned off."
                except Exception as e:
                    if is_tv_off_error(e):
                        logger.info("TV appears to already be off or unreachable.")
                        return "TV is already off or unreachable."
                    logger.error(f"Unexpected error during turn_off: {e}")
                    return f"Failed to turn off TV: {e}"

            if action_name == "turn_on":
                try:
                    await WakeOnLanService.wake_device(mac, ip)
                    return "TV Wake-on-LAN sent."
                except Exception as e:
                    logger.error(f"Failed to send Wake-on-LAN: {e}")
                    return f"Failed to wake TV: {e}"

            if action_name not in ACTIONS:
                return f"Unknown action: {action_name}"

            sequence = ACTIONS[action_name]
            config = TVConfig(ip=ip, mac=mac, client_key=client_key, sequence=sequence)

            controller = cls(config)

            try:
                await controller.run_sequence()
                return f"Action '{action_name}' completed successfully!"
            except Exception as e:
                if not is_tv_off_error(e):
                    logger.error(f"Action '{action_name}' failed: {e}")
                    return f"Action '{action_name}' failed: {e}"

                logger.warning("TV appears to be off, attempting Wake-on-LAN...")

                # Wake the TV
                try:
                    await WakeOnLanService.wake_device(mac, ip)
                except Exception as wake_err:
                    logger.error(f"Wake-on-LAN failed: {wake_err}")
                    return f"TV is off and Wake-on-LAN failed: {wake_err}"

                # Extra delay to let TV fully stabilize after wake
                logger.info("Waiting 2s for TV to stabilize...")
                await asyncio.sleep(2)

                logger.info(f"Retrying action '{action_name}' after wake...")

                # Re-init controller with fresh client and retry
                try:
                    controller = cls(config)
                    await controller.run_sequence()
                    return f"TV was woken up. Action '{action_name}' completed successfully!"
                except Exception as retry_err:
                    logger.error(f"Retry after wake failed: {retry_err}")
                    return f"TV was woken but action failed: {retry_err}"

        except Exception as e:
            # Ultimate fallback - should never reach here, but ensures we never crash
            logger.exception(f"Unexpected critical error in execute_action_with_retry: {e}")
            return f"Unexpected error: {e}"
