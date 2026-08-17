<#
.SYNOPSIS
    Builds tectonica.dll from tectonica\ and copies it to the repository root.
.DESCRIPTION
    Requires the Autodesk ObjectARX SDK to be installed locally — tectonica.csproj
    references it by absolute path and it is not redistributable, which is why
    this cannot run on a GitHub-hosted runner and releases are cut locally.
.PARAMETER Configuration
    MSBuild configuration to build (default: Release).
#>
param(
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SolutionPath = Join-Path $RepoRoot "tectonica\tectonica.sln"

if (-not (Test-Path $SolutionPath)) {
    throw "Could not find $SolutionPath"
}

function Find-MSBuild {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $msbuildPath = & $vswhere -latest -requires Microsoft.Component.MSBuild -find MSBuild\**\Bin\MSBuild.exe | Select-Object -First 1
        if ($msbuildPath) { return $msbuildPath }
    }

    $candidates = @(
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\MSBuild.exe",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Enterprise\MSBuild\Current\Bin\MSBuild.exe",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }

    throw "MSBuild.exe not found. Install Visual Studio or the Visual Studio Build Tools."
}

$MSBuild = Find-MSBuild
Write-Host "Using MSBuild: $MSBuild"

& $MSBuild $SolutionPath "/t:Restore" "/nologo" "/verbosity:minimal"
if ($LASTEXITCODE -ne 0) { throw "NuGet restore failed with exit code $LASTEXITCODE" }

& $MSBuild $SolutionPath "/p:Configuration=$Configuration" "/p:Platform=Any CPU" "/nologo" "/verbosity:minimal"
if ($LASTEXITCODE -ne 0) { throw "MSBuild failed with exit code $LASTEXITCODE" }

$BuiltDll = Join-Path $RepoRoot "tectonica\tectonica\bin\$Configuration\net8.0-windows\tectonica.dll"
if (-not (Test-Path $BuiltDll)) {
    throw "Build succeeded but $BuiltDll was not found."
}

Copy-Item -Path $BuiltDll -Destination (Join-Path $RepoRoot "tectonica.dll") -Force
Write-Host "tectonica.dll -> $RepoRoot\tectonica.dll"
