# Granting real notification-listener access (MSIX identity)

## Why this exists

`UserNotificationListener` (the WinRT API this app uses to read other apps'
notifications) requires the restricted `userNotificationListener`
capability. Windows only grants restricted capabilities to apps that have
**package identity** - normally an MSIX package. A plain `python.exe`
process launched via `python main.py` has no package identity, so
`RequestAccessAsync` can never return `ALLOWED`. In practice this shows up
as either a permanent `UNSPECIFIED` status or the call hanging indefinitely
(there's no app for the OS permission broker to attach a decision to).

The fix is to give the app package identity without switching to a full
MSIX install: a **sparse package** ("packaging with external location").
It registers a small signed identity manifest against the folder containing
your built exe; Windows then treats anything run from that folder as
having that identity - no change to how you launch or distribute the app.

Restricted capabilities only need Microsoft Store approval when you submit
to the Store. Sideloading an app that declares one (which is what this
does) does not require approval.

This is genuinely fiddly Windows plumbing (SDK signing tools, cert trust
stores, exact manifest field matching) and has not been validated against
a real Windows 11 install as part of writing it - if a step here doesn't
match what you see on your machine, the [Troubleshooting](#troubleshooting)
table below and the Windows **Event Viewer** (Applications and Services
Logs > Microsoft > Windows > AppxDeployment-Server) are the best next
steps.

## Prerequisites

- **PyInstaller**: `pip install pyinstaller`
- **`MakeAppx.exe` and `SignTool.exe`** - you do **not** need the full
  Windows SDK for these (that installer's "everything" option is several
  GB). Three ways to get them, in order of convenience; `register_app.ps1`
  auto-detects whichever one you use, no flags needed:

  - **Option A - direct download, zero extra tooling (recommended).**
    Just PowerShell + internet access; no `nuget.exe`, no `dotnet`, no
    installer. This downloads the
    [`Microsoft.Windows.SDK.BuildTools`](https://www.nuget.org/packages/Microsoft.Windows.SDK.BuildTools)
    NuGet package (~21MB) directly and unzips it - a `.nupkg` file is just
    a zip in disguise:
    ```powershell
    $dest = "packaging\sdk-tools"
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    Invoke-WebRequest -Uri "https://www.nuget.org/api/v2/package/Microsoft.Windows.SDK.BuildTools" -OutFile "$dest\buildtools.zip"
    Expand-Archive -Path "$dest\buildtools.zip" -DestinationPath $dest -Force
    ```
    `register_app.ps1` recursively searches `packaging\sdk-tools\` for
    `makeappx.exe`/`signtool.exe`, so the exact nested folder layout
    inside the package doesn't matter.

  - **Option B - `nuget` CLI**, if you already have it (or prefer it to a
    raw `Invoke-WebRequest`). Get `nuget.exe` itself from
    [nuget.org/downloads](https://www.nuget.org/downloads) - it's a
    single standalone `.exe`, nothing to install, just put it on your
    `PATH` or reference it by full path:
    ```powershell
    nuget install Microsoft.Windows.SDK.BuildTools -OutputDirectory packaging\sdk-tools
    ```
    (If you have the .NET SDK / Visual Studio installed, `dotnet` also
    works but only from inside a project via `dotnet add package` - the
    direct download in Option A or `nuget.exe` in Option B are simpler
    for a one-off tool fetch like this.)

  - **Option C - the SDK installer**, if you'd rather have a "real"
    install (e.g. you'll reuse these tools for other projects). Run the
    [Windows SDK installer](https://developer.microsoft.com/windows/downloads/windows-sdk/)
    and on the feature-selection screen, deselect everything except
    **"Windows SDK Signing Tools for Desktop Apps"** - still much smaller
    than a full install.
- **Developer Mode** enabled: Settings > Privacy & security > For
  developers > Developer Mode. (Sideloading also works without it via a
  provisioning trust prompt, but Developer Mode is the simplest path.)

## Steps

1. Build the exe (embeds `packaging/app.manifest`, which links the exe to
   the identity package by Publisher/Name/ApplicationId):

   ```powershell
   pip install pyinstaller
   pyinstaller packaging/notification_forwarder.spec
   ```

   This produces `dist/NotificationForwarder.exe`.

2. Register the identity package against that `dist` folder:

   ```powershell
   powershell -ExecutionPolicy Bypass -File packaging/register_app.ps1
   ```

   This does **not** require running as Administrator (per-user
   registration via `Add-AppxPackage -ExternalLocation`).

3. Copy your `.env` (and `service-account.json` if using FCM) into
   `dist/`, next to `NotificationForwarder.exe`.

4. Verify access using the built exe - not `python`, which still has no
   identity even after step 2:

   ```powershell
   dist\NotificationForwarder.exe --diagnose
   ```

   This should now show a real consent prompt and, once you accept it,
   report `ACCESS GRANTED`.

5. Run it normally: `dist\NotificationForwarder.exe`

**Important:** the registered identity is tied to the exact folder path
you passed as `-DistPath` (default `dist/`). If you move or rename that
folder, re-run `register_app.ps1` against the new location.

## Removing it

```powershell
powershell -ExecutionPolicy Bypass -File packaging/register_app.ps1 -Unregister
```

## Troubleshooting

| Error | Cause | Fix |
| --- | --- | --- |
| `0x800B0109` / `CERT_E_UNTRUSTEDROOT` | Self-signed cert isn't trusted | `register_app.ps1` should handle this automatically; if it still fails, manually import `packaging/out/WinNotiForwarder.cer` into `Cert:\CurrentUser\TrustedPeople` |
| `0x80073CF9` | This exact package version is already registered | Re-run `register_app.ps1` - it removes the previous registration first, but if it was registered some other way, run `Get-AppxPackage WinNotiForwarder \| Remove-AppxPackage` manually first |
| Access still `UNSPECIFIED`/hangs after registering | `app.manifest`'s `publisher`/`packageName`/`applicationId` don't match `AppxManifest.xml`'s `Identity`/`Application` values, or you ran the exe from somewhere other than the registered `-DistPath` | Re-check the two manifests match exactly; confirm you're running the exe from the exact registered folder |
| `makeappx.exe` / `signtool.exe` not found | Signing tools not present anywhere `register_app.ps1` looks | Use any of the three options in [Prerequisites](#prerequisites) - the direct-download option (A) has no extra tooling requirements and is fastest |

If you get this working end-to-end and find something in this doc or
the scripts that needed a tweak, a PR is very welcome.
