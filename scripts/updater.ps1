<#
.SYNOPSIS
    Replaces a DWGMAGIC installation with a newer onedir bundle.
.DESCRIPTION
    Downloads the release bundle zip, extracts it, and mirrors it over the
    install directory. Nothing is installed and no interpreter is required:
    the bundle is already self-contained.

    Keep this file pure ASCII. Windows PowerShell 5.1 reads a .ps1 with no BOM
    using the system ANSI codepage, so a UTF-8 character here arrives mangled
    (an em dash becomes a smart quote) and the script fails to parse.
    scripts/release.ps1 enforces this at build time.

    Invoked by dwgmagic.update.launch_updater, which copies this script to TEMP
    first so the updater is not inside the directory it is replacing.
.PARAMETER AppDir
    The DWGMAGIC installation directory to update.
.PARAMETER PackageUrl
    Download URL of the release's *-win64.zip bundle.
.PARAMETER Relaunch
    Reopen the GUI once the update completes.
.PARAMETER Sha256
    Expected SHA-256 of the bundle zip (from the GitHub asset digest). When
    given, a download that does not match is refused.
.PARAMETER Version
    Version being installed; refreshes the Installed apps entry, which
    otherwise keeps showing the version the installer originally wrote.
#>
param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [Parameter(Mandatory = $true)][string]$PackageUrl,
    [switch]$Relaunch,
    [string]$Sha256 = "",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$LogPath = Join-Path $env:TEMP "dwgmagic2_update.log"
Start-Transcript -Path $LogPath -Force | Out-Null

$ZipPath = Join-Path $env:TEMP "dwgmagic2_update.zip"
$ExtractDir = Join-Path $env:TEMP "dwgmagic2_update_extract"
$BackupDir = Join-Path $env:TEMP "dwgmagic2_update_backup"
$Swapping = $false

# Inno Setup's uninstaller lives in the install directory but not in the
# bundle. Mirroring without excluding it deleted it, so Settings > Apps could
# no longer uninstall DWGMAGIC after its first in-app update.
$KeepFiles = @("unins*.exe", "unins*.dat", "unins*.msg")
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8E3D9F42-6C7B-4A15-9E2D-1F0B7C4A5D63}_is1"

function Get-AppProcesses {
    # Only this installation's processes: another copy elsewhere, or another
    # user's session (whose Path is unreadable), does not hold our files.
    @(Get-Process -Name "dwgmagic2", "dwgmagic2w" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($AppDir, [System.StringComparison]::OrdinalIgnoreCase) })
}

try {
    $AppDir = (Resolve-Path -LiteralPath $AppDir).ProviderPath
    Write-Host "Updating DWGMAGIC at $AppDir"

    Write-Host "Downloading $PackageUrl ..."
    # Windows PowerShell 5.1 on older .NET defaults to TLS 1.0/1.1, which
    # GitHub refuses.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = "SilentlyContinue"  # the progress UI makes this ~10x slower
    Invoke-WebRequest -Uri $PackageUrl -OutFile $ZipPath -UseBasicParsing

    if ($Sha256) {
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash
        if ($actual -ne $Sha256) {
            throw "Downloaded bundle is corrupt or was altered (SHA-256 $actual, expected $Sha256) - refusing to swap."
        }
        Write-Host "Checksum verified."
    }

    if (Test-Path $ExtractDir) { Remove-Item -Recurse -Force $ExtractDir }
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir -Force

    # The zip may wrap the payload in a single top-level folder.
    $Source = $ExtractDir
    $entries = @(Get-ChildItem -Force $ExtractDir)
    if ($entries.Count -eq 1 -and $entries[0].PSIsContainer) {
        $Source = $entries[0].FullName
    }

    if (-not (Test-Path (Join-Path $Source "dwgmagic2w.exe"))) {
        throw "Downloaded bundle does not contain dwgmagic2w.exe - refusing to swap."
    }

    # The mirror below purges files missing from the bundle. Releases normally
    # ship tectonica.dll, but carry the installed one across if this one does not,
    # so a DLL-less release cannot silently strip a working plugin.
    $installedDll = Join-Path $AppDir "tectonica.dll"
    $bundledDll = Join-Path $Source "tectonica.dll"
    if ((Test-Path $installedDll) -and -not (Test-Path $bundledDll)) {
        Write-Host "Bundle has no tectonica.dll; preserving the installed one."
        Copy-Item -LiteralPath $installedDll -Destination $bundledDll -Force
    }

    # Wait for the app to release its files; the GUI exits right after launching us.
    Write-Host "Waiting for DWGMAGIC to exit..."
    for ($i = 0; $i -lt 30; $i++) {
        if ((Get-AppProcesses).Count -eq 0) { break }
        Start-Sleep -Seconds 1
    }
    # Copying over files a running copy holds open fails halfway and leaves a
    # mix of two versions. Stop here instead, with the installation untouched.
    if ((Get-AppProcesses).Count -gt 0) {
        throw "DWGMAGIC is still running (another window or a --cli run). Close it and click Update again."
    }

    # Keep the current installation so a failed swap can be rolled back.
    Write-Host "Backing up the current installation..."
    if (Test-Path $BackupDir) { Remove-Item -Recurse -Force $BackupDir }
    robocopy $AppDir $BackupDir /MIR /XD "logs" /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Could not back up the installation (robocopy exit code $LASTEXITCODE)" }

    Write-Host "Applying update..."
    $Swapping = $true
    # /MIR clears files left over from the previous version; logs/ and the
    # uninstaller are ours, not the bundle's.
    robocopy $Source $AppDir /MIR /XD "logs" /XF @KeepFiles /R:2 /W:5 /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }
    $Swapping = $false

    if ($Version -and (Test-Path $UninstallKey)) {
        Set-ItemProperty -Path $UninstallKey -Name "DisplayVersion" -Value $Version
        Set-ItemProperty -Path $UninstallKey -Name "DisplayName" -Value "DWGMAGIC $Version"
    }

    Write-Host "Update complete."
    if ($Relaunch) {
        $launcher = Join-Path $AppDir "dwgmagic2w.exe"
        if (Test-Path $launcher) {
            Write-Host "Relaunching DWGMAGIC..."
            Start-Process -FilePath $launcher -WorkingDirectory $AppDir
        }
    }
    Start-Sleep -Seconds 2
}
catch {
    Write-Host ""
    Write-Host "Update failed: $_" -ForegroundColor Red
    if ($Swapping -and (Test-Path $BackupDir)) {
        Write-Host "Restoring the previous version..."
        robocopy $BackupDir $AppDir /MIR /XD "logs" /XF @KeepFiles /R:2 /W:5 /NFL /NDL /NJH /NJS | Out-Null
        if ($LASTEXITCODE -ge 8) {
            Write-Host "Restore failed (robocopy exit code $LASTEXITCODE). Reinstall DWGMAGIC from the releases page." -ForegroundColor Red
        }
        else {
            Write-Host "The previous version was restored."
        }
    }
    Write-Host "Details were logged to $LogPath"
    Write-Host "Press Enter to close..."
    Read-Host | Out-Null
    # The GUI closed itself to make way for the update; reopen whatever
    # version is installed now rather than leave the user with nothing.
    $launcher = Join-Path $AppDir "dwgmagic2w.exe"
    if ($Relaunch -and (Test-Path $launcher) -and (Get-AppProcesses).Count -eq 0) {
        Start-Process -FilePath $launcher -WorkingDirectory $AppDir
    }
    exit 1
}
finally {
    Remove-Item -Force $ZipPath -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $ExtractDir -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $BackupDir -ErrorAction SilentlyContinue
    Stop-Transcript | Out-Null
}
