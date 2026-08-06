"""
Diagnostic script to check notification access
Run this to see what's wrong
"""
import sys
import asyncio
from pathlib import Path

print("=" * 60)
print("Windows Notification Forwarder - Diagnostics")
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
            print("  NotificationForwarder.exe --diagnose")
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
            print("run 'NotificationForwarder.exe --diagnose' from the registered dist folder.")

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
