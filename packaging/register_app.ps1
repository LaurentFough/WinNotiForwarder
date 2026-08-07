<#
.SYNOPSIS
  Builds, signs, and registers the sparse package identity for Windows
  Notification Forwarder so UserNotificationListener can move past
  UNSPECIFIED. Run -Unregister to remove it again.

.DESCRIPTION
  See packaging/README.md for the full explanation of why this is needed.
  On a normal (-Register, the default) run this:
    1. Locates MakeAppx.exe / SignTool.exe (Windows 10/11 SDK)
    2. Creates (or reuses) a self-signed certificate whose Subject matches
       AppxManifest.xml's Identity Publisher, and trusts it for this user
    3. Packs packaging/AppxManifest.xml into a .msix
    4. Signs the .msix
    5. Registers it against -DistPath via Add-AppxPackage -ExternalLocation
       (per-user - this does NOT require an elevated/Administrator prompt)

  You must build the exe first: pyinstaller packaging/notification_forwarder.spec

.PARAMETER DistPath
  Folder containing the built NotificationForwarder.exe. Defaults to
  ..\dist relative to this script (PyInstaller's default onefile output
  folder). Package identity attaches to this exact folder path, so don't
  move the exe elsewhere afterwards without re-running this script.

.PARAMETER Unregister
  Remove the registered identity package instead of registering it.

.EXAMPLE
  pyinstaller packaging/notification_forwarder.spec
  powershell -ExecutionPolicy Bypass -File packaging/register_app.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging/register_app.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [string]$DistPath,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

# $PSScriptRoot / $MyInvocation.MyCommand.Path are unreliable here: Windows
# PowerShell 5.1 has a known bug where both come back empty for scripts run
# from a UNC path (including mapped network/VM shared-folder drives, e.g. a
# Z: drive that's actually \\host.lan\... under the hood). Rather than trust
# invocation metadata, locate this script's directory by checking the
# filesystem directly, relative to the current directory - which matches
# the documented usage (run from the repo root).
function Resolve-PackagingDir {
    $candidates = @()
    if ($PSScriptRoot) { $candidates += $PSScriptRoot }
    if ($MyInvocation.MyCommand.Path) { $candidates += (Split-Path -Parent $MyInvocation.MyCommand.Path) }
    $candidates += (Join-Path $PWD.Path "packaging")  # invoked from repo root (documented usage)
    $candidates += $PWD.Path                          # invoked from inside packaging/ already

    foreach ($c in $candidates) {
        if ($c -and (Test-Path (Join-Path $c "AppxManifest.xml"))) {
            return (Resolve-Path $c).Path
        }
    }
    throw "Could not locate packaging/AppxManifest.xml. Run this script from the repository root: powershell -ExecutionPolicy Bypass -File packaging/register_app.ps1`nIf you're on a UNC/mapped network path (e.g. a VM shared folder), that's likely why - see packaging/README.md."
}

$ScriptRoot = Resolve-PackagingDir

if (-not $DistPath) { $DistPath = Join-Path $ScriptRoot "..\dist" }

$PackageName = "WinNotiForwarder"
$CertSubject = "CN=WinNotiForwarder"
$ManifestDir = $ScriptRoot
$OutDir      = Join-Path $ScriptRoot "out"
$MsixPath    = Join-Path $OutDir "$PackageName.msix"
$PfxPath     = Join-Path $OutDir "$PackageName.pfx"
$CerPath     = Join-Path $OutDir "$PackageName.cer"

function Find-SdkTool {
    param([string]$ToolName)

    $onPath = Get-Command $ToolName -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    $roots = @(
        "${Env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "$Env:ProgramFiles\Windows Kits\10\bin"
    )
    foreach ($root in $roots) {
        if (Test-Path $root) {
            $found = Get-ChildItem -Path $root -Recurse -Filter $ToolName -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match '\\x64\\' } |
                Sort-Object FullName -Descending |
                Select-Object -First 1
            if ($found) { return $found.FullName }
        }
    }
    return $null
}

if ($Unregister) {
    Write-Host "Unregistering $PackageName..."
    $pkg = Get-AppxPackage -Name $PackageName -ErrorAction SilentlyContinue
    if ($pkg) {
        $pkg | Remove-AppxPackage
        Write-Host "Removed $PackageName."
    } else {
        Write-Host "$PackageName is not currently registered. Nothing to do."
    }
    exit 0
}

# --- Register flow ---

$DistPath = (Resolve-Path -Path $DistPath -ErrorAction SilentlyContinue)
if (-not $DistPath) {
    throw "DistPath does not exist. Build the exe first: pyinstaller packaging/notification_forwarder.spec"
}
$DistPath = $DistPath.Path

$ExePath = Join-Path $DistPath "NotificationForwarder.exe"
if (-not (Test-Path $ExePath)) {
    throw "NotificationForwarder.exe not found in '$DistPath'. Build it first: pyinstaller packaging/notification_forwarder.spec"
}

$devMode = Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock" -Name "AllowDevelopmentWithoutDevLicense" -ErrorAction SilentlyContinue
if (-not $devMode -or $devMode.AllowDevelopmentWithoutDevLicense -ne 1) {
    Write-Warning "Developer Mode does not appear to be enabled. If registration fails below, enable it via Settings > Privacy & security > For developers > Developer Mode, then re-run this script."
}

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

Write-Host "Locating Windows SDK signing tools..."
$MakeAppx = Find-SdkTool "makeappx.exe"
$SignTool = Find-SdkTool "signtool.exe"
if (-not $MakeAppx -or -not $SignTool) {
    throw "makeappx.exe / signtool.exe not found. Install the Windows 10/11 SDK (the 'Windows SDK Signing Tools for Desktop Apps' component is enough) from https://developer.microsoft.com/windows/downloads/windows-sdk/ and re-run."
}
Write-Host "  MakeAppx: $MakeAppx"
Write-Host "  SignTool: $SignTool"

Write-Host "Preparing signing certificate ($CertSubject)..."
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq $CertSubject } | Select-Object -First 1
if (-not $cert) {
    Write-Host "  No existing certificate found, creating one..."
    $cert = New-SelfSignedCertificate -Type Custom -Subject $CertSubject `
        -KeyUsage DigitalSignature -FriendlyName "$PackageName Identity Cert" `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}Subject Type:End Entity")
} else {
    Write-Host "  Reusing existing certificate (thumbprint $($cert.Thumbprint))."
}

$pfxPassword = ConvertTo-SecureString -String ([System.Guid]::NewGuid().ToString("N")) -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath $PfxPath -Password $pfxPassword | Out-Null
Export-Certificate -Cert $cert -FilePath $CerPath | Out-Null

$trusted = Get-ChildItem Cert:\CurrentUser\TrustedPeople | Where-Object { $_.Thumbprint -eq $cert.Thumbprint }
if (-not $trusted) {
    Write-Host "  Trusting certificate in CurrentUser\TrustedPeople..."
    Import-Certificate -FilePath $CerPath -CertStoreLocation Cert:\CurrentUser\TrustedPeople | Out-Null
}

Write-Host "Packing identity package..."
& $MakeAppx pack /o /d $ManifestDir /nv /p $MsixPath
if ($LASTEXITCODE -ne 0) { throw "MakeAppx pack failed (exit code $LASTEXITCODE)." }

Write-Host "Signing identity package..."
$plainPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($pfxPassword))
& $SignTool sign /fd SHA256 /a /f $PfxPath /p $plainPassword $MsixPath
if ($LASTEXITCODE -ne 0) { throw "SignTool sign failed (exit code $LASTEXITCODE)." }

Write-Host "Registering identity package against '$DistPath'..."
$existing = Get-AppxPackage -Name $PackageName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "  Removing previously registered version first..."
    $existing | Remove-AppxPackage
}
Add-AppxPackage -Path $MsixPath -ExternalLocation $DistPath

Write-Host ""
Write-Host "Done. Run NotificationForwarder.exe from exactly this folder:"
Write-Host "  $DistPath"
Write-Host "(moving/copying it elsewhere breaks the identity link - re-run this script"
Write-Host "against the new location if you need to move it)."
Write-Host ""
Write-Host "Verify access with:"
Write-Host "  $ExePath --diagnose"
