<#
.SYNOPSIS
    Builds and publishes a DWGMAGIC release from this machine.
.DESCRIPTION
    Releases are cut locally rather than in CI because tectonica.dll needs the
    proprietary Autodesk ObjectARX SDK, which cannot be installed on a
    GitHub-hosted runner. Everything else (tests, the PyInstaller bundle, the
    Inno Setup installer) happens here too so that one command produces the
    complete set of assets.

    Produces two release assets:
      dwgmagic2-setup-vX.Y.Z.exe   the installer end users download
      dwgmagic2-vX.Y.Z-win64.zip   the bundle the in-app updater swaps in

.PARAMETER SkipTectonica
    Reuse an existing tectonica.dll instead of rebuilding it. The DLL must
    already be present at the repository root.
.PARAMETER SkipTests
    Skip pytest. Intended for iterating on packaging, not for real releases.
.PARAMETER NoPublish
    Build and package, but do not create the GitHub release.
.PARAMETER Python
    Interpreter used to create the build environment (default: py -3.12).
#>
param(
    [switch]$SkipTectonica,
    [switch]$SkipTests,
    [switch]$NoPublish,
    [string]$Python = "py -3.12"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot

function Invoke-Step {
    param([string]$Name, [scriptblock]$Body)
    Write-Host ""
    Write-Host "=== $Name" -ForegroundColor Cyan
    & $Body
}

function Invoke-WithRetry {
    param([string]$Name, [scriptblock]$Body, [int]$Attempts = 8, [int]$DelaySeconds = 20)
    for ($i = 1; $i -le $Attempts; $i++) {
        Write-Host "  $Name (attempt $i/$Attempts)"
        if (& $Body) { return }
        if ($i -lt $Attempts) { Start-Sleep -Seconds $DelaySeconds }
    }
    throw "$Name failed after $Attempts attempts"
}

try {
    # --- Version -----------------------------------------------------------
    $initPath = Join-Path $RepoRoot "dwgmagic\__init__.py"
    $match = Select-String -Path $initPath -Pattern '__version__\s*=\s*"([^"]+)"'
    if (-not $match) { throw "Could not read __version__ from $initPath" }
    $Version = $match.Matches[0].Groups[1].Value
    $Tag = "v$Version"
    Write-Host "Releasing DWGMAGIC $Tag" -ForegroundColor Green

    if (-not $NoPublish) {
        $existing = git tag --list $Tag
        if ($existing) { throw "Tag $Tag already exists. Bump __version__ in dwgmagic\__init__.py first." }
        $dirty = git status --porcelain
        if ($dirty) { throw "Working tree is not clean. Commit or stash before releasing." }
    }

    # --- Build environment -------------------------------------------------
    $VenvDir = Join-Path $RepoRoot ".venv-build"
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"
    Invoke-Step "Build environment" {
        if (-not (Test-Path $VenvPython)) {
            $exe, $pyArgs = $Python.Split(" ")
            & $exe @pyArgs -m venv $VenvDir
            if ($LASTEXITCODE -ne 0) { throw "Failed to create build venv with '$Python'" }
        }
        & $VenvPython -m pip install --upgrade pip --quiet
        & $VenvPython -m pip install -r requirements.txt pyinstaller pytest --quiet
        if ($LASTEXITCODE -ne 0) { throw "Dependency install failed" }
    }

    if (-not $SkipTests) {
        Invoke-Step "Tests" {
            & $VenvPython -m pytest -q
            if ($LASTEXITCODE -ne 0) { throw "Tests failed - refusing to release." }
        }
    }

    # --- tectonica.dll -----------------------------------------------------
    $DllPath = Join-Path $RepoRoot "tectonica.dll"
    if ($SkipTectonica) {
        if (-not (Test-Path $DllPath)) { throw "-SkipTectonica was passed but $DllPath does not exist." }
        Write-Host "Reusing existing tectonica.dll"
    }
    else {
        Invoke-Step "tectonica.dll" {
            & (Join-Path $PSScriptRoot "build_tectonica.ps1")
        }
    }

    # --- PyInstaller bundle ------------------------------------------------
    $DistDir = Join-Path $RepoRoot "dist\dwgmagic2"
    Invoke-Step "PyInstaller bundle" {
        & $VenvPython -m PyInstaller dwgmagic2.spec --noconfirm --clean
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
    }

    Invoke-Step "Bundle payload" {
        # updater.ps1 is run by Windows PowerShell 5.1, which reads a BOM-less
        # .ps1 using the system ANSI codepage. A single UTF-8 character in it
        # arrives mangled and the whole script fails to parse - which silently
        # breaks in-app updating for everyone already on the previous version.
        $updaterSrc = Join-Path $PSScriptRoot "updater.ps1"
        $updaterText = [System.IO.File]::ReadAllText($updaterSrc)
        $nonAscii = ($updaterText.ToCharArray() | Where-Object { [int]$_ -gt 127 })
        if ($nonAscii.Count -gt 0) {
            $shown = ($nonAscii | Select-Object -Unique | ForEach-Object { "'$_'" }) -join ", "
            throw "updater.ps1 must be pure ASCII; found $($nonAscii.Count) non-ASCII character(s): $shown"
        }

        # APP_ROOT is the executable's directory when frozen, so these two live
        # beside the exe rather than inside _internal.
        Copy-Item $DllPath (Join-Path $DistDir "tectonica.dll") -Force
        Copy-Item $updaterSrc (Join-Path $DistDir "updater.ps1") -Force
        Write-Host "tectonica.dll and updater.ps1 placed next to the executables"
    }

    # --- Installer ---------------------------------------------------------
    $Iscc = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Iscc) { throw "Inno Setup 6 not found. Install it: winget install JRSoftware.InnoSetup" }

    Invoke-Step "Installer" {
        & $Iscc "/DAppVersion=$Version" (Join-Path $RepoRoot "installer\dwgmagic2.iss") | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }
    }

    # --- Update bundle zip -------------------------------------------------
    $ZipName = "dwgmagic2-$Tag-win64.zip"
    $ZipPath = Join-Path $RepoRoot "installer\Output\$ZipName"
    Invoke-Step "Update bundle" {
        if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
        Compress-Archive -Path (Join-Path $DistDir "*") -DestinationPath $ZipPath
    }

    $SetupPath = Join-Path $RepoRoot "installer\Output\dwgmagic2-setup-$Tag.exe"
    if (-not (Test-Path $SetupPath)) { throw "Expected installer at $SetupPath" }

    Write-Host ""
    Write-Host "Assets:" -ForegroundColor Green
    Get-Item $SetupPath, $ZipPath | ForEach-Object {
        Write-Host ("  {0}  ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB))
    }

    # --- Publish -----------------------------------------------------------
    if ($NoPublish) {
        Write-Host ""
        Write-Host "-NoPublish set; stopping before the GitHub release." -ForegroundColor Yellow
        return
    }

    Invoke-Step "GitHub release" {
        if (-not (git tag --list $Tag)) { git tag -a $Tag -m "DWGMAGIC $Tag" }
        git push origin $Tag

        # Creating the release and uploading each asset are retried separately:
        # a partial failure during a GitHub incident otherwise leaves the tag
        # pushed with no release, and re-running the whole build to recover.
        Invoke-WithRetry "create release" {
            $out = gh release create $Tag --title "DWGMAGIC $Tag" --generate-notes 2>&1
            if ($LASTEXITCODE -eq 0) { return $true }
            if ($out -match "already exists") { Write-Host "  release already exists"; return $true }
            Write-Host "  $($out | Select-Object -Last 1)"
            return $false
        }

        foreach ($asset in @($SetupPath, $ZipPath)) {
            Invoke-WithRetry "upload $(Split-Path -Leaf $asset)" {
                $out = gh release upload $Tag $asset --clobber 2>&1
                if ($LASTEXITCODE -eq 0) { return $true }
                Write-Host "  $($out | Select-Object -Last 1)"
                return $false
            }
        }
    }

    Write-Host ""
    Write-Host "Released $Tag" -ForegroundColor Green
}
finally {
    Pop-Location
}
