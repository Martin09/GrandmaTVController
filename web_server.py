import asyncio
import logging
import sys

import jinja2
from aiohttp import web

from core import TVController, load_config

logger = logging.getLogger("GrandmaTV.Web")

# Lock to prevent concurrent TV commands - only one action at a time
_action_lock = asyncio.Lock()


def _silence_connection_reset_errors(loop: asyncio.AbstractEventLoop, context: dict):
    """
    Custom exception handler to suppress harmless Windows proactor errors.

    On Windows, when a remote host closes a connection abruptly, asyncio's
    proactor event loop raises ConnectionResetError during socket shutdown.
    These are harmless and just noise in the logs.
    """
    exception = context.get("exception")
    if isinstance(exception, ConnectionResetError):
        # Silently ignore - these are expected when the TV closes connections
        return
    # For all other exceptions, use the default handler
    loop.default_exception_handler(context)


# HTML Template
INDEX_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Grandma's Remote</title>
    <style>
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            user-select: none;
        }
        body {
            font-family: system-ui, -apple-system, sans-serif;
            height: 100vh;
            width: 100vw;
            overflow: hidden;
            background-color: #1a1a1a;
            display: grid;
            grid-template-columns: repeat({{ cols }}, 1fr);
            grid-template-rows: repeat({{ rows }}, 1fr);
        }
        .btn {
            border: none;
            color: white;
            font-size: 5vw;
            font-weight: bold;
            cursor: pointer;
            transition: opacity 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            text-align: center;
            padding: 20px;
            width: 100%;
            height: 100%;
        }
        .btn:active {
            opacity: 0.7;
        }
        /* Mobile specific adjustments */
        @media (max-width: 768px) {
            body {
                grid-template-columns: 1fr; /* Stack vertically on phones */
                grid-template-rows: repeat({{ total_buttons }}, 1fr);
            }
            .btn {
                font-size: 8vh;
            }
        }
        .status-overlay {
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 10px 20px;
            border-radius: 20px;
            display: none;
            z-index: 1000;
            font-size: 1.2rem;
            pointer-events: none;
        }
    </style>
</head>
<body>
    <div id="status" class="status-overlay">Sending...</div>

    {% for btn in buttons %}
    <button class="btn" 
            style="background-color: {{ btn.color }};" 
            onclick="triggerAction('{{ btn.action }}')">
        {{ btn.label }}
    </button>
    {% endfor %}

    <script>
        const statusEl = document.getElementById('status');

        async function triggerAction(actionName) {
            showStatus("Sending " + actionName + "...");
            
            try {
                const response = await fetch('/api/action/' + actionName, {
                    method: 'POST'
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    showStatus(data.message || "Success!", 2000);
                } else {
                    showStatus("Error: " + (data.error || "Unknown"), 3000);
                }
            } catch (err) {
                showStatus("Network Error", 3000);
                console.error(err);
            }
        }

        let timeout;
        function showStatus(text, duration) {
            statusEl.innerText = text;
            statusEl.style.display = 'block';
            clearTimeout(timeout);
            
            if (duration) {
                timeout = setTimeout(() => {
                    statusEl.style.display = 'none';
                }, duration);
            }
        }
    </script>
</body>
</html>
"""


async def handle_index(request: web.Request) -> web.Response:
    config = request.app["config"]
    web_config = config.get("web", {})
    buttons = web_config.get("buttons", [])

    # Simple logic to determine grid layout
    count = len(buttons)
    cols = 2 if count > 1 else 1
    rows = (count + 1) // 2

    # If explicit mobile behavior is needed, CSS handles it.
    # Jinja context
    context = {"buttons": buttons, "cols": cols, "rows": rows, "total_buttons": count}

    template = jinja2.Template(INDEX_TEMPLATE)
    html = template.render(**context)
    return web.Response(text=html, content_type="text/html")


async def handle_action(request: web.Request) -> web.Response:
    action_name = request.match_info["name"]
    config = request.app["config"]

    # Check if another action is already running
    if _action_lock.locked():
        logger.warning(f"Ignoring action '{action_name}' - another command is in progress")
        return web.json_response(
            {"status": "busy", "error": "Another command is already running, please wait."},
            status=429,
        )

    logger.info(f"Web request: Execute action '{action_name}'")

    async with _action_lock:
        try:
            msg = await TVController.execute_action_with_retry(action_name, config)
            return web.json_response({"status": "ok", "message": msg})
        except ValueError as e:
            return web.json_response({"status": "error", "error": str(e)}, status=400)
        except Exception as e:
            logger.exception("Web Action Failed")
            return web.json_response({"status": "error", "error": str(e)}, status=500)


async def _setup_exception_handler(app: web.Application):
    """Install custom exception handler on the running event loop."""
    if sys.platform == "win32":
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_silence_connection_reset_errors)


def create_app() -> web.Application:
    # Load config
    try:
        config = load_config()
    except FileNotFoundError:
        config = {}
        logger.warning("No config found, web interface will be empty.")

    app = web.Application()
    app["config"] = config

    # Install exception handler on startup (when loop is running)
    app.on_startup.append(_setup_exception_handler)

    app.router.add_get("/", handle_index)
    app.router.add_post("/api/action/{name}", handle_action)

    return app


def run_web_server():
    logging.basicConfig(level=logging.INFO)

    app = create_app()

    # Read config again or get from app
    config = app["config"]
    web_config = config.get("web", {})
    port = web_config.get("port", 8080)
    host = web_config.get("host", "0.0.0.0")  # Default to all interfaces

    logger.info(f"Starting web server on http://{host}:{port}")
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    run_web_server()
