"""
Diagnostic script to check notification access
Run this to see what's wrong
"""
import sys

# Must run before anything can import pythoncom: it defaults to initializing
# COM as STA on import, and a WinRT async call awaited from an STA thread
# with no Windows message pump running never completes - it just hangs.
# Forcing MTA (0) here avoids that.
sys.coinit_flags = 0

import asyncio
import socket
from pathlib import Path


def check_loopback_connectivity(timeout: float = 3.0) -> bool:
    """
    Quick synchronous self-test: can a local 127.0.0.1 TCP connection be
    established at all? asyncio's ProactorEventLoop needs this to succeed
    just to start up on Windows - if something intercepts/reroutes loopback
    traffic (observed cause: Proxifier with "Handle Direct Connections"
    enabled; likely also true of similar proxy/VPN/traffic-shaping tools),
    asyncio.run() hangs indefinitely with no error and no other symptom,
    before any code below this point ever runs. Bounded with a timeout so
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


if not check_loopback_connectivity():
    print(
        "ERROR: Could not establish a local 127.0.0.1 TCP connection within a few seconds.\n\n"
        "This app (and Python's asyncio on Windows generally) needs that to work - without it,\n"
        "asyncio.run() hangs indefinitely with no error and no other symptom.\n\n"
        "The most common cause is a proxy/VPN/traffic-interception tool capturing loopback\n"
        "connections (confirmed cause in one case: Proxifier's \"Handle Direct Connections\"\n"
        "option). Disable that, or add an explicit direct/bypass exception for\n"
        "127.0.0.1/localhost, then try again."
    )
    input("\nPress Enter to exit...")
    sys.exit(1)

print("=" * 60)
print("WinNotiForwarder - Diagnostics")
print("=" * 60)
print()

# Check Python version
print(f"Python version: {sys.version}")
print(f"Python executable: {sys.executable}")
print()

# Check if running as exe or script
if getattr(sys, 'frozen', False):
    print("Running as: COMPILED EXECUTABLE")
    app_dir = Path(sys.executable).parent
else:
    print("Running as: PYTHON SCRIPT")
    app_dir = Path(__file__).parent

print(f"App directory: {app_dir}")
print(f"Current directory: {Path.cwd()}")
print()

# Check if .env exists
env_file = app_dir / ".env"
print(f"Looking for .env at: {env_file}")
print(f".env exists: {env_file.exists()}")
print()

# Check dependencies
print("Checking dependencies...")
deps = {
    "winrt": "winrt-runtime",
    "requests": "requests",
    "dotenv": "python-dotenv"
}

for module, package in deps.items():
    try:
        __import__(module)
        print(f"  ✓ {module} installed")
    except ImportError:
        print(f"  ✗ {module} NOT installed - run: pip install {package}")

print()

# Try to import WinRT
try:
    from winrt.windows.ui.notifications.management import UserNotificationListener, UserNotificationListenerAccessStatus
    from winrt.windows.ui.notifications import NotificationKinds
    print("✓ WinRT modules imported successfully")
    print()
except Exception as e:
    print(f"✗ Failed to import WinRT modules: {e}")
    print()
    input("Press Enter to exit...")
    sys.exit(1)

# Try to get listener
async def test_listener():
    print("Testing UserNotificationListener...")
    try:
        listener = UserNotificationListener.current
        print(f"✓ Got listener: {listener}")
        print()

        print("Requesting notification access (up to 30s - this script has no package")
        print("identity, so this call may hang instead of returning; see below if it does)...")
        try:
            access_status = await asyncio.wait_for(listener.request_access_async(), timeout=30.0)
        except asyncio.TimeoutError:
            print()
            print("✗ TIMED OUT waiting for a response.")
            print()
            print("This script runs as a plain python.exe process with no Windows package")
            print("identity, so the OS permission broker has nothing to attach a decision to")
            print("and the call can hang indefinitely instead of returning UNSPECIFIED.")
            print()
            print("This is not fixable from here or from Settings. Build and register the")
            print("packaged exe instead - see packaging/README.md - then run:")
            print("  WinNotiForwarder.exe --diagnose")
            print("from the registered dist folder to check access using an exe that actually")
            print("has package identity.")
            print()
            input("Press Enter to exit...")
            sys.exit(1)

        print(f"Access status: {access_status}")

        if access_status == UserNotificationListenerAccessStatus.ALLOWED:
            print("✓ ACCESS GRANTED!")
            print()

            # Try to get notifications
            print("Trying to get notifications...")
            notifications = await listener.get_notifications_async(NotificationKinds.TOAST)

            if notifications:
                print(f"✓ Found {len(notifications)} notifications in Action Center")
            else:
                print("⚠ No notifications found in Action Center")
                print("  This is normal if Action Center is empty")

        elif access_status == UserNotificationListenerAccessStatus.DENIED:
            print("✗ ACCESS DENIED!")
            print()
            print("To fix this:")
            print("1. Open Settings > Privacy & Security > Notifications")
            print("2. Look for 'Python' or 'python.exe'")
            print("3. Toggle notification access ON")

        elif access_status == UserNotificationListenerAccessStatus.UNSPECIFIED:
            print("✗ ACCESS UNSPECIFIED - App not recognized by Windows")
            print()
            print("This is expected for a plain 'python main.py' process, not a transient bug.")
            print("UserNotificationListener requires the restricted 'userNotificationListener'")
            print("capability, which Windows only grants to apps with package identity")
            print("(MSIX / a signed sparse package). The system-wide python.exe has no such")
            print("identity, so there is no Settings toggle to flip - it will stay UNSPECIFIED")
            print("no matter how many times you retry.")
            print()
            print("Fix: build and register the packaged exe - see packaging/README.md - then")
            print("run 'WinNotiForwarder.exe --diagnose' from the registered dist folder.")

        else:
            print(f"✗ Unknown access status: {access_status}")

    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()

    print()
    input("Press Enter to exit...")

# Run the test
asyncio.run(test_listener())
