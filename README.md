# GrandmaTVBot

A simple Python tool to automate LG WebOS TV actions (app launches, remote button presses) via macros. Designed for easy use by non-technical users.

## Features

- Wake up your LG TV via Wake-on-LAN
- Run customizable action sequences (macros) using apps and remote buttons
- Store TV connection info in a YAML config file

## Setup

1. **Install dependencies:**
   - Python 3.12+
   - Install required packages using [uv](https://github.com/astral-sh/uv):

```sh
uv sync
```

1. **Configure your TV:**
   - Copy `config.yml.example` to `config.yml` and fill in your TV's IP and MAC address.

```sh
cp config.yml.example config.yml
# Edit config.yml with your details
```

## Usage

Run the controller with a chosen macro:

```sh
uv run main.py --action channel_1
```

Available actions: `channel_1`, `channel_2` (see `main.py` for details).

### Telegram Bot Mode

Run as a Telegram bot for remote control:

```sh
uv run main.py --bot
```

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token
2. Add your bot token to `config.yml` under `telegram.bot_token`
3. Message your bot, it will log unauthorized chat IDs for you to add
4. Add authorized chat IDs to `telegram.allowed_chat_ids` in `config.yml`

## Notes

- During your first run you will need to approve access on your TV.
- Your pairing key is stored in `config.yml` after first run.
- `config.yml` is ignored by git; commit only the example config.

## License

MIT
