[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^V\d+\.\d+\.\d+$')]
    [string]$Version,

    [string]$ExecutablePath,
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

$resolvedExecutable = (Resolve-Path -LiteralPath $ExecutablePath).ProviderPath
$executable = Get-Item -LiteralPath $resolvedExecutable
if ($executable.PSIsContainer -or $executable.Extension -ne '.exe' -or $executable.Length -eq 0 -or
    ($executable.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'ExecutablePath must point to a nonempty, regular Windows .exe file.'
}

# Deliberately enumerate distributable files; deployment and build directories may contain private state.
$requiredRelativeFiles = @(
    'index.html',
    'assets/default-background.svg',
    'assets/dashboard.css',
    'assets/icons/homepage-badge.svg',
    'assets/js/app.js',
    'assets/js/background-manager.js',
    'assets/js/background-upload.js',
    'assets/js/bestdori.js',
    'assets/js/bing-daily.js',
    'assets/js/card-editor.js',
    'assets/js/card-manager.js',
    'assets/js/card-svg.js',
    'assets/js/channel-switch.js',
    'assets/js/core-http.js',
    'assets/js/dialog-motion.js',
    'assets/js/favicon-manager.js',
    'assets/js/notes-api.js',
    'assets/js/notes-auth.js',
    'assets/js/notes-drafts.js',
    'assets/js/notes-manager.js',
    'assets/js/notes-markdown.js',
    'assets/js/notes-motion.js',
    'assets/js/notes-render-worker.js',
    'assets/js/notes-selection-motion.js',
    'assets/js/page-settings.js',
    'assets/js/search.js',
    'assets/js/service-status.js',
    'assets/vendor/notes-dependencies.json',
    'assets/vendor/dompurify/LICENSE',
    'assets/vendor/dompurify/purify.mjs',
    'assets/vendor/highlightjs/LICENSE',
    'assets/vendor/highlightjs/bash.mjs',
    'assets/vendor/highlightjs/core.mjs',
    'assets/vendor/highlightjs/css.mjs',
    'assets/vendor/highlightjs/diff.mjs',
    'assets/vendor/highlightjs/ini.mjs',
    'assets/vendor/highlightjs/javascript.mjs',
    'assets/vendor/highlightjs/json.mjs',
    'assets/vendor/highlightjs/powershell.mjs',
    'assets/vendor/highlightjs/python.mjs',
    'assets/vendor/highlightjs/sql.mjs',
    'assets/vendor/highlightjs/typescript.mjs',
    'assets/vendor/highlightjs/xml.mjs',
    'assets/vendor/highlightjs/yaml.mjs',
    'assets/vendor/markdown-it/LICENSE',
    'assets/vendor/markdown-it/markdown-it.mjs',
    'backgrounds/bg.jpg',
    'README.md',
    'CHANGELOG.md',
    'docs/deployment.md',
    'docs/maintenance.md',
    'LICENSE',
    'ASSETS.md',
    'THIRD_PARTY_NOTICES.md',
    'licenses/Python-3.14.6.txt',
    'licenses/PyInstaller-6.21.0.txt',
    'licenses/Pillow-12.3.0.txt',
    'licenses/Send2Trash-2.1.0.txt',
    'licenses/markdown-it-py-4.0.0.txt',
    'licenses/markdown-it-py-4.0.0-LICENSE.markdown-it.txt',
    'licenses/mdurl-0.1.2.txt'
)

$sources = @{}
foreach ($relativePath in $requiredRelativeFiles) {
    $sourcePath = $projectRoot
    foreach ($segment in ($relativePath -split '/')) {
        $sourcePath = Join-Path $sourcePath $segment
        $item = Get-Item -LiteralPath $sourcePath
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Release paths cannot contain links or junctions: $relativePath"
        }
    }
    if ($item.PSIsContainer) {
        throw "Required release file is not a regular file: $relativePath"
    }
    $sources.Add($relativePath, $item.FullName)
}

$manifestPath = 'assets/vendor/notes-dependencies.json'
$manifest = Get-Content -LiteralPath $sources[$manifestPath] -Raw -Encoding UTF8 | ConvertFrom-Json
$verifiedVendorPaths = @{}
foreach ($dependency in $manifest.dependencies) {
    foreach ($file in $dependency.files) {
        $relativePath = 'assets/vendor/' + $file.path
        if (-not $sources.ContainsKey($relativePath) -or $relativePath -eq $manifestPath -or
            $verifiedVendorPaths.ContainsKey($relativePath)) {
            throw "Unexpected or duplicate vendored release file: $relativePath"
        }
        $actualHash = (Get-FileHash -LiteralPath $sources[$relativePath] -Algorithm SHA256).Hash
        if ($actualHash -ne $file.sha256) {
            throw "Vendored file SHA-256 mismatch: $relativePath"
        }
        $verifiedVendorPaths.Add($relativePath, $true)
    }
}
foreach ($relativePath in $requiredRelativeFiles) {
    if ($relativePath.StartsWith('assets/vendor/') -and $relativePath -ne $manifestPath -and
        -not $verifiedVendorPaths.ContainsKey($relativePath)) {
        throw "Missing vendored file checksum: $relativePath"
    }
}

$null = New-Item -ItemType Directory -Path $OutputDirectory -Force
$resolvedOutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).ProviderPath
$packageName = "zoraxy-homelab-dashboard-$Version-windows-x64"
$archivePath = Join-Path $resolvedOutputDirectory "$packageName.zip"
$archiveChecksumPath = "$archivePath.sha256"
if ((Test-Path -LiteralPath $archivePath) -or (Test-Path -LiteralPath $archiveChecksumPath)) {
    throw "Refusing to overwrite an existing release archive or checksum: $archivePath"
}

$workspaceName = '.package-' + [guid]::NewGuid().ToString('N')
$workspacePath = Join-Path $resolvedOutputDirectory $workspaceName
$null = New-Item -ItemType Directory -Path $workspacePath
try {
    $stagingDirectory = Join-Path $workspacePath 'staging'
    $null = New-Item -ItemType Directory -Path $stagingDirectory
    Copy-Item -LiteralPath $resolvedExecutable -Destination (Join-Path $stagingDirectory 'HomeLabWallpaperState.exe')

    foreach ($relativePath in $requiredRelativeFiles) {
        $destinationPath = Join-Path $stagingDirectory $relativePath
        $destinationParent = Split-Path -Parent $destinationPath
        $null = New-Item -ItemType Directory -Path $destinationParent -Force
        Copy-Item -LiteralPath $sources[$relativePath] -Destination $destinationPath
    }

    $hashLines = Get-ChildItem -LiteralPath $stagingDirectory -File -Recurse |
        Sort-Object FullName |
        ForEach-Object {
            $relativePath = $_.FullName.Substring($stagingDirectory.Length + 1).Replace('\', '/')
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $relativePath"
        }
    $utf8 = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllLines((Join-Path $stagingDirectory 'SHA256SUMS.txt'), [string[]]$hashLines, $utf8)

    $temporaryArchive = Join-Path $workspacePath "$packageName.zip"
    $temporaryChecksum = "$temporaryArchive.sha256"
    Compress-Archive -Path (Join-Path $stagingDirectory '*') -DestinationPath $temporaryArchive -CompressionLevel Optimal
    $archiveHash = (Get-FileHash -LiteralPath $temporaryArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText($temporaryChecksum, "$archiveHash  $packageName.zip`n", $utf8)

    [IO.File]::Move($temporaryArchive, $archivePath)
    try {
        [IO.File]::Move($temporaryChecksum, $archiveChecksumPath)
    }
    catch {
        Remove-Item -LiteralPath $archivePath
        throw
    }
}
finally {
    $resolvedWorkspace = (Resolve-Path -LiteralPath $workspacePath).ProviderPath
    $expectedParent = $resolvedOutputDirectory.TrimEnd('\', '/')
    $actualParent = (Split-Path -Parent $resolvedWorkspace).TrimEnd('\', '/')
    if ($actualParent -eq $expectedParent -and (Split-Path -Leaf $resolvedWorkspace) -eq $workspaceName) {
        Remove-Item -LiteralPath $resolvedWorkspace -Recurse -Force
    }
    else {
        throw "Unexpected release workspace location: $resolvedWorkspace"
    }
}

[PSCustomObject]@{
    Archive        = $archivePath
    ArchiveSha256  = $archiveHash
    Checksum       = $archiveChecksumPath
}
