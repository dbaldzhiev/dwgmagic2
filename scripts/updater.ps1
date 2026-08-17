<#
.SYNOPSIS
    Replaces a DWGMAGIC installation with a newer onedir bundle.
.DESCRIPTION
    Downloads the release bundle zip, extracts it, and mirrors it over the
    install directory. Nothing is installed and no interpreter is required —
    the bundle is already self-contained.

    Invoked by dwgmagic.update.launch_updater, which copies this script to TEMP
    first so the updater is not inside the directory it is replacing.
.PARAMETER AppDir
    The DWGMAGIC installation directory to update.
.PARAMETER PackageUrl
    Download URL of the release's *-win64.zip bundle.
.PARAMETER Relaunch
    Reopen the GUI once the update completes.
#>
param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [Parameter(Mandatory = $true)][string]$PackageUrl,
    [switch]$Relaunch
)

$ErrorActionPreference = "Stop"
$LogPath = Join-Path $env:TEMP "dwgmagic2_update.log"
Start-Transcript -Path $LogPath -Force | Out-Null

$ZipPath = Join-Path $env:TEMP "dwgmagic2_update.zip"
$ExtractDir = Join-Path $env:TEMP "dwgmagic2_update_extract"

try {
    $AppDir = (Resolve-Path -LiteralPath $AppDir).ProviderPath
    Write-Host "Updating DWGMAGIC at $AppDir"

    Write-Host "Downloading $PackageUrl ..."
    $ProgressPreference = "SilentlyContinue"  # the progress UI makes this ~10x slower
    Invoke-WebRequest -Uri $PackageUrl -OutFile $ZipPath -UseBasicParsing

    if (Test-Path $ExtractDir) { Remove-Item -Recurse -Force $ExtractDir }
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir -Force

    # The zip may wrap the payload in a single top-level folder.
    $Source = $ExtractDir
    $entries = @(Get-ChildItem -Force $ExtractDir)
    if ($entries.Count -eq 1 -and $entries[0].PSIsContainer) {
        $Source = $entries[0].FullName
    }

    if (-not (Test-Path (Join-Path $Source "dwgmagic2w.exe"))) {
        throw "Downloaded bundle does not contain dwgmagic2w.exe — refusing to swap."
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
        $running = @(Get-Process -Name "dwgmagic2", "dwgmagic2w" -ErrorAction SilentlyContinue)
        if ($running.Count -eq 0) { break }
        Start-Sleep -Seconds 1
    }

    Write-Host "Applying update..."
    # /MIR clears files left over from the previous version; logs/ is ours, not the bundle's.
    robocopy $Source $AppDir /MIR /XD "logs" /R:2 /W:5 /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }

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
    Write-Host "Details were logged to $LogPath"
    Write-Host "Press Enter to close..."
    Read-Host | Out-Null
    exit 1
}
finally {
    Remove-Item -Force $ZipPath -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $ExtractDir -ErrorAction SilentlyContinue
    Stop-Transcript | Out-Null
}
