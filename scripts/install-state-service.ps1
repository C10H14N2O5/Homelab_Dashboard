[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ExePath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Fa-f0-9]{64}$')]
    [string]$ExpectedSha256,

    [Parameter()]
    [string]$PublicWebRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Test-PathInsideDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Directory
    )

    $trimCharacters = [char[]]@(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $candidate = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath($Directory).TrimEnd($trimCharacters)
    if ([string]::Equals($candidate.TrimEnd($trimCharacters), $root, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    return $candidate.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

$resolvedExePath = (Resolve-Path -LiteralPath $ExePath).Path
if (-not (Test-Path -LiteralPath $resolvedExePath -PathType Leaf) -or
    [IO.Path]::GetExtension($resolvedExePath) -ne '.exe') {
    throw "ExePath must point to an existing .exe file: $ExePath"
}
if ([IO.Path]::GetFileName($resolvedExePath) -ne 'HomeLabWallpaperState.exe') {
    throw 'The executable must be named HomeLabWallpaperState.exe.'
}

$actualSha256 = (Get-FileHash -LiteralPath $resolvedExePath -Algorithm SHA256).Hash
if (-not [string]::Equals($actualSha256, $ExpectedSha256, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Executable SHA256 mismatch. Expected $ExpectedSha256 but found $actualSha256"
}

if (-not [string]::IsNullOrWhiteSpace($PublicWebRoot)) {
    $resolvedPublicWebRoot = (Resolve-Path -LiteralPath $PublicWebRoot).Path
    if (-not (Test-Path -LiteralPath $resolvedPublicWebRoot -PathType Container)) {
        throw "PublicWebRoot must point to an existing directory: $PublicWebRoot"
    }
    if (Test-PathInsideDirectory -Path $resolvedExePath -Directory $resolvedPublicWebRoot) {
        throw "The executable must remain outside the public Web root: $resolvedPublicWebRoot"
    }
}

$legacyTask = Get-ScheduledTask -TaskName 'HomeLabWallpaperState' -ErrorAction SilentlyContinue
if ($null -ne $legacyTask) {
    Write-Warning 'The legacy SYSTEM scheduled task still exists. Run uninstall-state-service.ps1 as Administrator before completing the migration.'
}

if ($PSCmdlet.ShouldProcess($resolvedExePath, 'Launch first-run configuration and monitor')) {
    Write-Host 'Executable verified.'
    Write-Host "SHA256: $actualSha256"
    Write-Host 'The application will validate the Zoraxy paths, create its JSON configuration, and offer to create the current-user Startup shortcut.'
    Start-Process -FilePath $resolvedExePath -WorkingDirectory (Split-Path -Parent $resolvedExePath)
}
