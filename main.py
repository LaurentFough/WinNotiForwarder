"""
WinNotiForwarder
Forwards Windows notifications to multiple channels (FCM, Pushbullet, Ntfy)
"""
import sys

# Defensive measure for a related (but distinct) class of hang: pythoncom
# defaults to initializing COM as STA on import, which can happen implicitly
# via a PyInstaller Windows runtime hook even though this project never
# imports pywin32 directly. A WinRT async call awaited from an STA thread
# with no Windows message pump running never completes. This has to run
# before anything could import pythoncom.
sys.coinit_flags = 0

import asyncio
import logging
import logging.handlers
import socket
from pathlib import Path

from config import Config
from winrt_listener import WindowsNotificationListener
from providers import ProviderManager, FCMProvider, PushbulletProvider, NtfyProvider

EVENT_LOG_SOURCE = "WinNotiForwarder"


def get_app_dir() -> Path:
    """
    Directory this app's own files (exe or script) live in - not whatever
    the current working directory happens to be at launch time, which
    depends entirely on how the caller invoked it. Log files and .env
    lookup both need this to behave consistently regardless of invocation
    style (double-click, `cmd /c`, launched from an unrelated directory, a
    Windows service manager, etc).
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def check_loopback_connectivity(timeout: float = 3.0) -> bool:
    """
    Quick synchronous self-test: can a local 127.0.0.1 TCP connection be
    established at all? asyncio's ProactorEventLoop needs this to succeed
    just to start up on Windows - if something intercepts/reroutes loopback
    traffic (observed cause: Proxifier with "Handle Direct Connections"
    enabled; likely also true of similar proxy/VPN/traffic-shaping tools),
    asyncio.run() hangs indefinitely with no error and no other symptom,
    before any of this app's own code ever runs. Bounded with a timeout so
    we fail fast with an actionable message instead of hanging forever.
    """
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        port = server.getsockname()[1]

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(timeout)
        client.connect(('127.0.0.1', port))
        client.close()
        server.close()
        return True
    except OSError:
        return False


def setup_logging(log_filename: str) -> Path:
    """
    Configure logging: console + a rotating file in this app's own
    directory (see get_app_dir - not the current working directory) + a
    best-effort Windows Event Log handler. UTF-8 throughout for Windows
    console compatibility.

    Args:
        log_filename: e.g. "WinNotiForwarder.log" for a normal run, or a
                      distinct name for --diagnose so one-off diagnostic
                      checks don't clutter the main operational log.

    Returns:
        The full path of the log file, for printing/reference.
    """
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    log_path = get_app_dir() / log_filename

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format))

    # Rotating so a long-running background service doesn't grow this
    # file unbounded: 5MB per file, keep 3 old ones.
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(log_format))

    handlers = [console_handler, file_handler]

    # Best-effort Windows Event Log integration (Application log, source
    # "WinNotiForwarder") - useful when running as an NSSM/Windows service,
    # where nobody's watching a console. Requires pywin32, which isn't a
    # hard dependency of this project; skip quietly (falling back to
    # console+file only) if it's unavailable or registration fails (e.g.
    # no permission to register the event source on first run).
    if sys.platform == 'win32':
        try:
            event_handler = logging.handlers.NTEventLogHandler(EVENT_LOG_SOURCE)
            # WARNING+ only - the Event Log isn't the place for routine
            # per-notification INFO chatter, just things worth an admin's
            # attention.
            event_handler.setLevel(logging.WARNING)
            event_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
            handlers.append(event_handler)
        except Exception:
            pass

    logging.basicConfig(level=logging.INFO, format=log_format, handlers=handlers, force=True)

    if not any(isinstance(h, logging.handlers.NTEventLogHandler) for h in handlers):
        logging.getLogger(__name__).info(
            "Windows Event Log integration unavailable (pywin32 missing or "
            "event source registration failed) - continuing with console+file logging only."
        )

    # Ensure stdout uses UTF-8 on Windows
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding='utf-8', errors='replace',
            line_buffering=True, write_through=True
        )

    return log_path


class WinNotiForwarder:
    """Main application class that coordinates notification listening and forwarding"""

    def __init__(self):
        """Initialize the notification forwarder"""
        self.logger = logging.getLogger(__name__)
        self.config = Config()
        self.provider_manager: ProviderManager = ProviderManager()
        self.listener: WindowsNotificationListener = None

    async def run(self):
        """Main application loop"""
        try:
            # Validate configuration
            self.logger.info("=" * 60)
            self.logger.info("Starting WinNotiForwarder")
            self.logger.info("=" * 60)

            if not self.config.validate():
                self.logger.error("Invalid configuration. Please check your .env file.")
                return

            # Initialize providers
            self.logger.info("Initializing notification providers...")

            # Add FCM provider if enabled
            if self.config.fcm_enabled:
                fcm = FCMProvider(
                    service_account_file=self.config.fcm_service_account_file,
                    topic=self.config.fcm_topic
                )
                self.provider_manager.add_provider(fcm)

            # Add Pushbullet provider if enabled
            if self.config.pushbullet_enabled:
                pushbullet = PushbulletProvider(
                    api_token=self.config.pushbullet_api_token
                )
                self.provider_manager.add_provider(pushbullet)

            # Add Ntfy provider if enabled
            if self.config.ntfy_enabled:
                ntfy = NtfyProvider(
                    server_url=self.config.ntfy_server_url,
                    topic=self.config.ntfy_topic,
                    username=self.config.ntfy_username if self.config.ntfy_username else None,
                    password=self.config.ntfy_password if self.config.ntfy_password else None,
                    verify_ssl=self.config.ntfy_verify_ssl
                )
                self.provider_manager.add_provider(ntfy)

            # Check if any providers are enabled
            provider_count = self.provider_manager.get_provider_count()
            if provider_count == 0:
                self.logger.error("No notification providers enabled!")
                return

            self.logger.info(f"{self.provider_manager.get_summary()}")
            self.logger.info("=" * 60)

            # Initialize notification listener
            self.listener = WindowsNotificationListener(self._on_notification_received)

            # Request notification access
            self.logger.info("Requesting notification access...")
            if not await self.listener.request_access():
                self.logger.error(
                    "Failed to get notification access. "
                    "Please grant permission in Windows Settings > Privacy > Notifications"
                )
                return

            # Start listening
            self.logger.info("Starting notification listener...")
            self.logger.info("Forwarding Windows notifications to enabled providers. Press Ctrl+C to stop.")
            self.logger.info("=" * 60)

            await self.listener.start_listening()

        except KeyboardInterrupt:
            self.logger.info("Received shutdown signal")
        except Exception as e:
            self.logger.error(f"Application error: {e}", exc_info=True)
        finally:
            await self.shutdown()

    def _on_notification_received(self, notification: dict):
        """
        Callback function called when a notification is received

        Args:
            notification: Dict containing app_name, title, text, timestamp
        """
        try:
            app_name = notification.get("app_name", "Unknown")
            title = notification.get("title", "")
            text = notification.get("text", "")

            self.logger.info(f"Received notification from {app_name}: {title}")

            # Check if we should forward this notification
            if not self.config.should_forward_notification(app_name):
                self.logger.debug(f"Skipping notification from {app_name} (filtered)")
                return

            # Forward to all enabled providers
            results = self.provider_manager.send_notification(
                title=title,
                body=text,
                source_app=app_name
            )

        except Exception as e:
            self.logger.error(f"Error handling notification: {e}", exc_info=True)

    async def shutdown(self):
        """Cleanup resources"""
        self.logger.info("Shutting down...")
        if self.listener:
            await self.listener.stop_listening()
        self.logger.info("Shutdown complete")


async def run_diagnose():
    """
    Check notification-listener access status using this exact executable.

    Must be run via the same built exe that has package identity registered
    (see packaging/README.md) - running `python main.py --diagnose` from a
    plain python.exe reports UNSPECIFIED for the same reason (no package
    identity). If it hangs indefinitely instead of reporting any status at
    all, see check_loopback_connectivity()'s docstring - that's a different,
    unrelated failure mode.
    """
    log_path = setup_logging("WinNotiForwarder-diagnose.log")

    print("=" * 60)
    print("Notification Access Diagnosis")
    print("=" * 60)
    print(f"Running from: {sys.executable if getattr(sys, 'frozen', False) else __file__}")
    print(f"Log file: {log_path}")
    print()

    listener = WindowsNotificationListener(callback=lambda n: None)
    print("Requesting notification access (up to 30s)...")
    granted = await listener.request_access()

    if granted:
        print("✓ ACCESS GRANTED - the app can read Windows notifications.")
    else:
        print("✗ Access not granted. Check the log output above for the reason.")

    input("\nPress Enter to exit...")


async def main():
    """Application entry point"""
    if "--diagnose" in sys.argv:
        await run_diagnose()
        return

    log_path = setup_logging("WinNotiForwarder.log")

    app_dir = get_app_dir()

    # Check if .env file exists in app directory
    env_file = app_dir / ".env"
    logging.info(f"Log file: {log_path}")
    logging.info(f"Looking for .env file at: {env_file}")
    logging.info(f"Current working directory: {Path.cwd()}")
    logging.info(f"App directory: {app_dir}")

    if not env_file.exists():
        logging.error(
            f"No .env file found at {env_file}!"
        )
        logging.error("Please copy .env.example to .env and configure it in the same folder as the executable.")
        input("\nPress Enter to exit...")
        return

    logging.info(f"Found .env file at: {env_file}")

    # Change to app directory so dotenv can find .env
    import os
    os.chdir(app_dir)

    # Create and run the forwarder
    forwarder = WinNotiForwarder()
    await forwarder.run()


if __name__ == "__main__":
    # Runs before asyncio.run() specifically because that's where the hang
    # this guards against actually happens - see check_loopback_connectivity's
    # docstring. A plain synchronous check here can't hit the same failure.
    if not check_loopback_connectivity():
        print(
            "ERROR: Could not establish a local 127.0.0.1 TCP connection "
            "within a few seconds.\n\n"
            "This app (and Python's asyncio on Windows generally) needs "
            "that to work - without it, asyncio.run() hangs indefinitely "
            "with no error and no other symptom, before any of this app's "
            "own code runs.\n\n"
            "The most common cause is a proxy/VPN/traffic-interception "
            "tool capturing loopback connections (confirmed cause in one "
            "case: Proxifier's \"Handle Direct Connections\" option). "
            "Disable that, or add an explicit direct/bypass exception for "
            "127.0.0.1/localhost, then try again.",
            file=sys.stderr
        )
        input("\nPress Enter to exit...")
        sys.exit(1)

    # Run the async application
    asyncio.run(main())
