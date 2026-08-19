[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^V\d+\.\d+\.\d+$')]
    [string]$Version,

    [Parameter()]
    [string]$ExecutablePath,

    [Parameter()]
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ExecutablePath) {
    $ExecutablePath = Join-Path $projectRoot 'dist\HomeLabWallpaperState.exe'
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectRoot 'dist\release'
}

$resolvedExecutable = (Resolve-Path -LiteralPath $ExecutablePath -ErrorAction Stop).Path
if ([IO.Path]::GetExtension($resolvedExecutable) -ne '.exe') {
    throw 'ExecutablePath must point to a Windows .exe file.'
}

$requiredRelativeFiles = @(
    'index.html',
    'assets\default-background.svg',
    'assets\dashboard.css',
    'assets\js\app.js',
    'assets\js\background-manager.js',
    'assets\js\background-upload.js',
    'assets\js\bestdori.js',
    'assets\js\card-editor.js',
    'assets\js\card-manager.js',
    'assets\js\card-svg.js',
    'assets\js\channel-switch.js',
    'assets\js\core-http.js',
    'assets\js\search.js',
    'assets\js\service-status.js',
    'backgrounds\bg.jpg',
    'README.md',
    'LICENSE',
    'ASSETS.md',
    'THIRD_PARTY_NOTICES.md',
    'licenses\Python-3.14.6.txt',
    'licenses\PyInstaller-6.21.0.txt',
    'licenses\Pillow-12.3.0.txt',
    'licenses\Send2Trash-2.1.0.txt'
)
foreach ($relativePath in $requiredRelativeFiles) {
    $sourcePath = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Required release file is missing: $relativePath"
    }
}

$null = New-Item -ItemType Directory -Path $OutputDirectory -Force
$resolvedOutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path
$packageName = "zoraxy-homelab-dashboard-$Version-windows-x64"
$stagingDirectory = Join-Path $resolvedOutputDirectory ".package-$PID"
$archivePath = Join-Path $resolvedOutputDirectory "$packageName.zip"
$archiveChecksumPath = "$archivePath.sha256"

if (Test-Path -LiteralPath $stagingDirectory) {
    throw "Refusing to reuse an existing staging directory: $stagingDirectory"
}

try {
    $null = New-Item -ItemType Directory -Path $stagingDirectory
    Copy-Item -LiteralPath $resolvedExecutable -Destination (Join-Path $stagingDirectory 'HomeLabWallpaperState.exe')

    foreach ($relativePath in $requiredRelativeFiles) {
        $sourcePath = Join-Path $projectRoot $relativePath
        $destinationPath = Join-Path $stagingDirectory $relativePath
        $destinationParent = Split-Path -Parent $destinationPath
        if (-not (Test-Path -LiteralPath $destinationParent)) {
            $null = New-Item -ItemType Directory -Path $destinationParent -Force
        }
        Copy-Item -LiteralPath $sourcePath -Destination $destinationPath
    }

    $hashLines = Get-ChildItem -LiteralPath $stagingDirectory -File -Recurse |
        Sort-Object FullName |
        ForEach-Object {
            $relativePath = $_.FullName.Substring($stagingDirectory.Length + 1).Replace('\', '/')
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $relativePath"
        }
    [IO.File]::WriteAllLines(
        (Join-Path $stagingDirectory 'SHA256SUMS.txt'),
        [string[]]$hashLines,
        [Text.UTF8Encoding]::new($false)
    )

    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    Compress-Archive -Path (Join-Path $stagingDirectory '*') -DestinationPath $archivePath -CompressionLevel Optimal

    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText(
        $archiveChecksumPath,
        "$archiveHash  $([IO.Path]::GetFileName($archivePath))`n",
        [Text.UTF8Encoding]::new($false)
    )
}
finally {
    if (Test-Path -LiteralPath $stagingDirectory) {
        Remove-Item -LiteralPath $stagingDirectory -Recurse -Force
    }
}

[PSCustomObject]@{
    Archive        = $archivePath
    ArchiveSha256  = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Checksum       = $archiveChecksumPath
}
