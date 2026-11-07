[CmdletBinding()]
param(
    [Parameter()]
    [switch]$SkipPython,

    [Parameter()]
    [switch]$SkipFrontend,

    [Parameter()]
    [switch]$Build
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($Build -and ($SkipPython -or $SkipFrontend)) {
    throw '-Build cannot be combined with -SkipPython or -SkipFrontend.'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$indexPath = Join-Path $projectRoot 'index.html'
$frontendAssetRoot = Join-Path $projectRoot 'assets'
$frontendStylePath = Join-Path $frontendAssetRoot 'dashboard.css'
$frontendModuleRoot = Join-Path $frontendAssetRoot 'js'
$frontendModulePaths = @(
    'core-http.js',
    'background-manager.js',
    'background-upload.js',
    'bing-daily.js',
    'bestdori.js',
    'search.js',
    'channel-switch.js',
    'service-status.js',
    'card-svg.js',
    'dialog-motion.js',
    'card-editor.js',
    'card-manager.js',
    'page-settings.js',
    'favicon-manager.js',
    'app.js'
) | ForEach-Object { Join-Path $frontendModuleRoot $_ }
$frontendModulePaths += @(Get-ChildItem -LiteralPath $frontendModuleRoot -Filter 'notes-*.js' -File | Sort-Object Name | Select-Object -ExpandProperty FullName)
$stateServicePath = Join-Path $projectRoot 'state-service'
$uninstallPath = Join-Path $PSScriptRoot 'uninstall-state-service.ps1'

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    Write-Host "Checking $Name..."
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE."
    }
}

function Test-BlockedRepositoryPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $normalized = $Path.Replace('\', '/').TrimStart('/')
    $lower = $normalized.ToLowerInvariant()
    $segments = @($lower.Split('/'))
    $leafName = [IO.Path]::GetFileName($lower)
    $extension = [IO.Path]::GetExtension($lower)
    $blockedRootPrefixes = @(
        'dist/',
        '.venv/',
        'build/',
        'temp/',
        'output/',
        'homelabwallpaperstate-data/'
        'homelabnotes-data/',
        'homelabnotes-backup/'
    )
    foreach ($prefix in $blockedRootPrefixes) {
        if ($lower.StartsWith($prefix, [StringComparison]::Ordinal)) {
            return $true
        }
    }
    if ($segments -contains '__pycache__') {
        return $true
    }
    if ($lower.StartsWith('backgrounds/', [StringComparison]::Ordinal) -and
        $lower -notin @('backgrounds/.gitignore', 'backgrounds/bg.jpg')) {
        return $true
    }
    if ($extension -in @('.exe', '.pyc', '.pyo', '.pyd', '.spec')) {
        return $true
    }
    if ($extension -in @('.jpg', '.jpeg', '.png', '.webp', '.avif', '.gif', '.svg') -and
        $lower -notin @('backgrounds/bg.jpg', 'assets/default-background.svg', 'assets/icons/homepage-badge.svg')) {
        return $true
    }
    if ($leafName -in @(
            'state.json',
            'state.json.bak',
            'dashboardcards.json',
            'dashboardcards.json.bak',
            'dashboardfavicon.json',
            'dashboardfavicon.json.bak',
            'dashboardpageicon.json',
            'dashboardpageicon.json.bak',
            'homelabwallpaperstate.json'
        ) -or
        $leafName -like 'state.json.corrupt-*' -or
        $leafName -like 'dashboardcards.json.corrupt-*' -or
        $leafName -like 'dashboardfavicon.json.corrupt-*' -or
        $leafName -like 'dashboardpageicon.json.corrupt-*' -or
        $leafName -like '.state-*.tmp' -or
        $leafName -like '.dashboardcards.json.*.tmp' -or
        $leafName -like '.dashboardfavicon.json.*.tmp' -or
        $leafName -like '.dashboardpageicon.json.*.tmp' -or
        $leafName -match '\.log(?:\..+)?$') {
        return $true
    }
    return $false
}

function Assert-RepositorySourceControlPolicy {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$Paths,

        [Parameter(Mandatory = $true)]
        [string]$Source
    )

    foreach ($candidatePath in $Paths) {
        if (Test-BlockedRepositoryPath -Path $candidatePath) {
            throw "Blocked private, runtime, generated, or multimedia path found in $Source files: $candidatePath"
        }
    }
}

$requiredFiles = @(
    'index.html',
    'assets\dashboard.css',
    'assets\js\core-http.js',
    'assets\js\background-manager.js',
    'assets\js\background-upload.js',
    'assets\js\bing-daily.js',
    'assets\js\bestdori.js',
    'assets\js\search.js',
    'assets\js\channel-switch.js',
    'assets\js\service-status.js',
    'assets\js\card-svg.js',
    'assets\js\dialog-motion.js',
    'assets\js\card-editor.js',
    'assets\js\card-manager.js',
    'assets\js\page-settings.js',
    'assets\js\favicon-manager.js',
    'assets\js\app.js',
    'README.md',
    'docs\deployment.md',
    'docs\maintenance.md',
    'CHANGELOG.md',
    'LICENSE',
    'ASSETS.md',
    'THIRD_PARTY_NOTICES.md',
    '.gitleaks.toml',
    '.gitignore',
    'assets\default-background.svg',
    'backgrounds\.gitignore',
    'backgrounds\bg.jpg',
    'licenses\Python-3.14.6.txt',
    'licenses\PyInstaller-6.21.0.txt',
    'licenses\Pillow-12.3.0.txt',
    'licenses\Send2Trash-2.1.0.txt',
    'requirements.txt',
    'state-service\main.py',
    'state-service\test_main.py',
    'state-service\homelab_state\__init__.py',
    'state-service\homelab_state\core.py',
    'state-service\homelab_state\config.py',
    'state-service\homelab_state\backgrounds.py',
    'state-service\homelab_state\cards.py',
    'state-service\homelab_state\favicon.py',
    'state-service\homelab_state\bestdori.py',
    'state-service\homelab_state\bing.py',
    'state-service\homelab_state\probe.py',
    'state-service\homelab_state\http_api.py',
    'state-service\homelab_state\launcher.py',
    'state-service\tests\__init__.py',
    'state-service\tests\helpers.py',
    'state-service\tests\test_config.py',
    'state-service\tests\test_backgrounds.py',
    'state-service\tests\test_cards.py',
    'state-service\tests\test_favicon.py',
    'state-service\tests\test_bestdori.py',
    'state-service\tests\test_bing.py',
    'state-service\tests\test_probe.py',
    'state-service\tests\test_http_api.py',
    'state-service\tests\test_launcher.py',
    'scripts\build-state-service.ps1',
    'scripts\package-release.ps1',
    'scripts\process-utils.ps1',
    'scripts\uninstall-state-service.ps1',
    'scripts\verify-project.ps1'
)
foreach ($relativePath in $requiredFiles) {
    $path = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required file is missing: $relativePath"
    }
}

$buildSource = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'build-state-service.ps1'))
$packageSource = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'package-release.ps1'))
$processUtilsSource = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'process-utils.ps1'))
$stateServiceSource = [IO.File]::ReadAllText((Join-Path $projectRoot 'state-service\main.py'))
$coreServiceSource = [IO.File]::ReadAllText(
    (Join-Path $projectRoot 'state-service\homelab_state\core.py')
)
$launcherSource = [IO.File]::ReadAllText(
    (Join-Path $projectRoot 'state-service\homelab_state\launcher.py')
)
$httpServiceSource = [IO.File]::ReadAllText(
    (Join-Path $projectRoot 'state-service\homelab_state\http_api.py')
)
$backgroundServiceSource = [IO.File]::ReadAllText(
    (Join-Path $projectRoot 'state-service\homelab_state\backgrounds.py')
)
$cardsServiceSource = [IO.File]::ReadAllText(
    (Join-Path $projectRoot 'state-service\homelab_state\cards.py')
)
$faviconServiceSource = [IO.File]::ReadAllText(
    (Join-Path $projectRoot 'state-service\homelab_state\favicon.py')
)
$launcherTestSource = [IO.File]::ReadAllText(
    (Join-Path $projectRoot 'state-service\tests\test_launcher.py')
)
$httpServiceTestSource = [IO.File]::ReadAllText(
    (Join-Path $projectRoot 'state-service\tests\test_http_api.py')
)
$cardsServiceTestSource = [IO.File]::ReadAllText(
    (Join-Path $projectRoot 'state-service\tests\test_cards.py')
)
$faviconServiceTestSource = [IO.File]::ReadAllText(
    (Join-Path $projectRoot 'state-service\tests\test_favicon.py')
)
$readmeSource = [IO.File]::ReadAllText((Join-Path $projectRoot 'README.md'))
$deploymentSource = [IO.File]::ReadAllText((Join-Path $projectRoot 'docs\deployment.md'))
$maintenanceSource = [IO.File]::ReadAllText((Join-Path $projectRoot 'docs\maintenance.md'))
$changelogSource = [IO.File]::ReadAllText((Join-Path $projectRoot 'CHANGELOG.md'))
$coreServiceVersionMatch = [regex]::Match($coreServiceSource, '(?m)^SERVICE_VERSION\s*=\s*(\d+)\s*$')
$buildServiceVersionMatch = [regex]::Match($buildSource, '(?m)^\$expectedServiceVersion\s*=\s*(\d+)\s*$')
if (-not $coreServiceVersionMatch.Success -or
    -not $buildServiceVersionMatch.Success -or
    $coreServiceVersionMatch.Groups[1].Value -ne $buildServiceVersionMatch.Groups[1].Value) {
    throw 'Source and packaged smoke-test service versions must match.'
}
$maintenanceContracts = @(
    @{ Name = 'PyInstaller one-file build'; Source = $buildSource; Marker = '--onefile' },
    @{ Name = 'packaged executable health check'; Source = $buildSource; Marker = '/healthz' },
    @{ Name = 'bounded redirected-process helper'; Source = $buildSource; Marker = 'Invoke-BuildRedirectedProcess' },
    @{ Name = 'asynchronous stdout drain'; Source = $processUtilsSource; Marker = 'ReadToEndAsync' },
    @{ Name = 'complete process-tree termination'; Source = $processUtilsSource; Marker = 'taskkill.exe' },
    @{ Name = 'process timeout exception'; Source = $processUtilsSource; Marker = '[TimeoutException]' },
    @{ Name = 'thin Python entry point'; Source = $stateServiceSource; Marker = 'from homelab_state.launcher import main' },
    @{ Name = 'source-mode main.py command'; Source = $launcherSource; Marker = 'parent.parent / "main.py"' },
    @{ Name = 'source-mode launcher regression'; Source = $launcherTestSource; Marker = 'test_executable_command_supports_source_mode' },
    @{ Name = 'HTTP connection limit'; Source = $httpServiceSource; Marker = 'MAX_HTTP_CONNECTIONS = 16' },
    @{ Name = 'HTTP request read deadline'; Source = $httpServiceSource; Marker = 'HTTP_REQUEST_READ_DEADLINE_SECONDS = 300.0' },
    @{ Name = 'strict transfer framing'; Source = $httpServiceSource; Marker = 'Transfer-Encoding is not supported' },
    @{ Name = 'request smuggling regression'; Source = $httpServiceTestSource; Marker = 'test_bodyless_routes_reject_unread_bytes_and_request_smuggling' },
    @{ Name = 'slow request regression'; Source = $httpServiceTestSource; Marker = 'test_request_read_deadline_cannot_be_extended_by_trickle_input' },
    @{ Name = 'background file quota'; Source = $backgroundServiceSource; Marker = 'MAX_BACKGROUND_FILES = 512' },
    @{ Name = 'thumbnail generation budget'; Source = $backgroundServiceSource; Marker = 'MAX_THUMBNAIL_GENERATION_ATTEMPTS = 16' },
    @{ Name = 'dashboard cards private state'; Source = $cardsServiceSource; Marker = 'CARDS_STATE_FILE_NAME = "DashboardCards.json"' },
    @{ Name = 'dashboard cards channel limit'; Source = $cardsServiceSource; Marker = 'MAX_CARDS_PER_CHANNEL = 32' },
    @{ Name = 'dashboard cards SVG sanitizer'; Source = $cardsServiceSource; Marker = 'def sanitize_svg(' },
    @{ Name = 'dashboard favicon private state'; Source = $faviconServiceSource; Marker = 'FAVICON_STATE_FILE_NAME = "DashboardFavicon.json"' },
    @{ Name = 'dashboard favicon shared SVG sanitizer'; Source = $faviconServiceSource; Marker = 'sanitize_svg(icon_svg, FAVICON_SANITIZER_SCOPE)' },
    @{ Name = 'dashboard favicon restart persistence regression'; Source = $faviconServiceTestSource; Marker = 'test_upload_is_sanitized_hashed_and_persists_across_restart' },
    @{ Name = 'dashboard favicon removed-state restart regression'; Source = $faviconServiceTestSource; Marker = 'test_replacement_invalidates_old_hash_and_removal_persists_tombstone' },
    @{ Name = 'dashboard cards concurrent initialization regression'; Source = $cardsServiceTestSource; Marker = 'test_concurrent_initialize_has_one_winner' },
    @{ Name = 'dashboard cards cross-channel concurrency regression'; Source = $cardsServiceTestSource; Marker = 'test_concurrent_different_channel_updates_both_persist' },
    @{ Name = 'Python unit tests'; Source = $buildSource; Marker = 'unittest discover' },
    @{ Name = 'packaged PATCH compatibility check'; Source = $buildSource; Marker = 'X-Requested-With' },
    @{ Name = 'packaged background upload check'; Source = $buildSource; Marker = '/api/background-files/upload' },
    @{ Name = 'packaged thumbnail inventory check'; Source = $buildSource; Marker = '$backgroundInventory' },
    @{ Name = 'packaged service status check'; Source = $buildSource; Marker = '/api/service-status' },
    @{ Name = 'packaged card initialization check'; Source = $buildSource; Marker = '/api/cards/initialize' },
    @{ Name = 'packaged card restart persistence check'; Source = $buildSource; Marker = 'did not preserve card state across restart' },
    @{ Name = 'packaged icon route coverage'; Source = $buildSource; Marker = 'foreach ($iconKind in @(''favicon'', ''page-icon''))' },
    @{ Name = 'packaged multi-format icon smoke'; Source = $buildSource; Marker = 'smoke-icons.py' },
    @{ Name = 'packaged icon resource check'; Source = $buildSource; Marker = '/api/$iconKind.svg?v=' },
    @{ Name = 'packaged favicon removal persistence check'; Source = $buildSource; Marker = 'did not preserve removed favicon state across restart' },
    @{ Name = 'temporary artifact hash'; Source = $buildSource; Marker = 'temporaryHash' },
    @{ Name = 'release file manifest'; Source = $packageSource; Marker = 'SHA256SUMS.txt' },
    @{ Name = 'release archive checksum'; Source = $packageSource; Marker = 'ArchiveSha256' },
    @{ Name = 'release license notices'; Source = $packageSource; Marker = 'THIRD_PARTY_NOTICES.md' },
    @{ Name = 'release default background source'; Source = $packageSource; Marker = 'assets/default-background.svg' },
    @{ Name = 'release stable stylesheet'; Source = $packageSource; Marker = 'assets/dashboard.css' },
    @{ Name = 'release module entry point'; Source = $packageSource; Marker = 'assets/js/app.js' },
    @{ Name = 'release background manager module'; Source = $packageSource; Marker = 'assets/js/background-manager.js' },
    @{ Name = 'release background upload module'; Source = $packageSource; Marker = 'assets/js/background-upload.js' },
    @{ Name = 'release Bestdori module'; Source = $packageSource; Marker = 'assets/js/bestdori.js' },
    @{ Name = 'release card editor module'; Source = $packageSource; Marker = 'assets/js/card-editor.js' },
    @{ Name = 'release card manager module'; Source = $packageSource; Marker = 'assets/js/card-manager.js' },
    @{ Name = 'release card SVG module'; Source = $packageSource; Marker = 'assets/js/card-svg.js' },
    @{ Name = 'release channel switch module'; Source = $packageSource; Marker = 'assets/js/channel-switch.js' },
    @{ Name = 'release core HTTP module'; Source = $packageSource; Marker = 'assets/js/core-http.js' },
    @{ Name = 'release search module'; Source = $packageSource; Marker = 'assets/js/search.js' },
    @{ Name = 'release service status module'; Source = $packageSource; Marker = 'assets/js/service-status.js' },
    @{ Name = 'release Notes rendering worker'; Source = $packageSource; Marker = 'assets/js/notes-render-worker.js' },
    @{ Name = 'release default page badge'; Source = $packageSource; Marker = 'assets/icons/homepage-badge.svg' },
    @{ Name = 'release vendored checksums'; Source = $packageSource; Marker = 'notes-dependencies.json' },
    @{ Name = 'release Notes dependency license'; Source = $packageSource; Marker = 'licenses/markdown-it-py-4.0.0.txt' },
    @{ Name = 'Zoraxy Virtual Directory documentation'; Source = $deploymentSource; Marker = 'MatchingPath' },
    @{ Name = 'strict v4 configuration documentation'; Source = $deploymentSource; Marker = '"version": 4' },
    @{ Name = 'loopback service probe documentation'; Source = $deploymentSource; Marker = 'probeConnectAddress' },
    @{ Name = 'dashboard cards backup documentation'; Source = $maintenanceSource; Marker = 'DashboardCards.json' }
)
foreach ($contract in $maintenanceContracts) {
    if (-not $contract.Source.Contains($contract.Marker)) {
        throw "Missing maintenance contract: $($contract.Name)"
    }
}
foreach ($document in @(
    @{ Name = 'README.md'; Source = $readmeSource },
    @{ Name = 'docs/deployment.md'; Source = $deploymentSource },
    @{ Name = 'docs/maintenance.md'; Source = $maintenanceSource },
    @{ Name = 'CHANGELOG.md'; Source = $changelogSource },
    @{ Name = 'ASSETS.md'; Source = [IO.File]::ReadAllText((Join-Path $projectRoot 'ASSETS.md')) },
    @{ Name = 'THIRD_PARTY_NOTICES.md'; Source = [IO.File]::ReadAllText((Join-Path $projectRoot 'THIRD_PARTY_NOTICES.md')) }
)) {
    $fenceCount = [regex]::Matches($document.Source, '(?m)^```').Count
    if ($fenceCount % 2 -ne 0) {
        throw "Unbalanced Markdown code fences in $($document.Name)."
    }
}

Write-Host 'Checking PowerShell syntax...'
$syntaxFailed = $false
Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' | ForEach-Object {
    $tokens = $null
    $parseErrors = $null
    [void][Management.Automation.Language.Parser]::ParseFile(
        $_.FullName,
        [ref]$tokens,
        [ref]$parseErrors
    )
    if ($parseErrors.Count -gt 0) {
        $syntaxFailed = $true
        foreach ($parseError in $parseErrors) {
            Write-Error "$($_.Name):$($parseError.Extent.StartLineNumber): $($parseError.Message)"
        }
    }
}
if ($syntaxFailed) {
    throw 'PowerShell syntax validation failed.'
}

Write-Host 'Checking uninstall behavior when the executable is missing...'
$missingExePath = Join-Path $projectRoot ".verify-missing-$PID.exe"
if (Test-Path -LiteralPath $missingExePath) {
    throw "Unable to reserve missing executable smoke-test path: $missingExePath"
}
$uninstallSmokeOutput = (& $uninstallPath -ExePath $missingExePath -WhatIf *>&1 | Out-String)
if (-not $uninstallSmokeOutput.Contains('intentionally preserved')) {
    throw 'Uninstall script did not continue after receiving a missing executable path.'
}

if ($SkipFrontend) {
    Write-Warning 'Frontend HTML, JavaScript, and timeout behavior checks were skipped by request.'
} else {
    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($null -eq $node) {
        throw 'Node.js was not found in PATH. Install it or rerun with -SkipFrontend for a partial static check.'
    }
    $frontendValidation = @'
const fs = require('fs');
const vm = require('vm');
const path = require('path');

(async () => {
const htmlDocument = fs.readFileSync(process.argv[2], 'utf8');
const stylesheet = fs.readFileSync(process.argv[3], 'utf8');
const modulePaths = process.argv.slice(4);
const moduleEntries = modulePaths.map((modulePath) => [
    path.basename(modulePath),
    fs.readFileSync(modulePath, 'utf8').replace(/\r\n?/g, '\n')
]);
const moduleSources = Object.fromEntries(
    moduleEntries.map(([name, source]) => [name, source.replace(/\r\n/g, '\n')])
);
const expectedModuleNames = [
    'core-http.js',
    'background-manager.js',
    'background-upload.js',
    'bing-daily.js',
    'bestdori.js',
    'search.js',
    'channel-switch.js',
    'service-status.js',
    'card-svg.js',
    'dialog-motion.js',
    'card-editor.js',
    'card-manager.js',
    'page-settings.js',
    'favicon-manager.js',
    'app.js'
];
expectedModuleNames.push(...Object.keys(moduleSources).filter((name) => /^notes-.*\.js$/.test(name)).sort());
if (JSON.stringify(Object.keys(moduleSources)) !== JSON.stringify(expectedModuleNames)) {
    throw new Error(`Unexpected ES module inputs: ${Object.keys(moduleSources).join(', ')}`);
}
for (const [name, source] of moduleEntries) {
    new vm.SourceTextModule(source, { identifier: `assets/js/${name}` });
    for (const match of source.matchAll(/\bfrom\s+['"](\.\/[^'"]+)['"]/g)) {
        const dependency = match[1].slice(2);
        if (!Object.prototype.hasOwnProperty.call(moduleSources, dependency)) {
            throw new Error(`${name} imports missing local module ${match[1]}.`);
        }
    }
}
const coreScript = moduleSources['core-http.js'];
const backgroundManagerScript = moduleSources['background-manager.js'];
const backgroundUploadScript = moduleSources['background-upload.js'];
const bingDailyScript = moduleSources['bing-daily.js'];
const bestdoriScript = moduleSources['bestdori.js'];
const searchScript = moduleSources['search.js'];
const channelScript = moduleSources['channel-switch.js'];
const serviceScript = moduleSources['service-status.js'];
const cardSvgScript = moduleSources['card-svg.js'];
const dialogMotionModuleScript = moduleSources['dialog-motion.js'];
const cardEditorScript = moduleSources['card-editor.js'];
const cardManagerScript = moduleSources['card-manager.js'];
const pageSettingsScript = moduleSources['page-settings.js'];
const faviconManagerScript = moduleSources['favicon-manager.js'];
const appScript = moduleSources['app.js'];
const runtimeScript = moduleEntries.map((entry) => entry[1]).join('\n');
const backgroundNetworkScript = coreScript.slice(coreScript.indexOf('function fetchWithTimeout('))
    + '\n'
    + backgroundManagerScript.slice(backgroundManagerScript.indexOf('function preloadBackground('));
const bestdoriRequestScript = backgroundManagerScript.slice(
    backgroundManagerScript.indexOf('function backgroundManagerIsBusy('),
    backgroundManagerScript.indexOf('function syncBackgroundControlsBusy(')
) + '\n' + bestdoriScript.slice(
    bestdoriScript.indexOf('function bestdoriErrorMessage('),
    bestdoriScript.indexOf('function cardDrawDialogIsOpen(')
);
const inlineScripts = [...htmlDocument.matchAll(/<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)]
    .map((match) => match[1]);
if (inlineScripts.length !== 1) {
    throw new Error(`Expected one inline bootstrap script, found ${inlineScripts.length}.`);
}
inlineScripts.forEach((source, index) => {
    new vm.Script(source, { filename: `inline-bootstrap-${index + 1}.js` });
});

const stylesheetReferences = [...htmlDocument.matchAll(/<link\b[^>]*\brel="stylesheet"[^>]*\bhref="([^"]+)"[^>]*>/gi)]
    .map((match) => match[1]);
const scriptReferences = [...htmlDocument.matchAll(/<script\b[^>]*\bsrc="([^"]+)"[^>]*><\/script>/gi)]
    .map((match) => match[1]);
if (stylesheetReferences.length !== 1
    || stylesheetReferences[0] !== '/assets/dashboard.css') {
    throw new Error(`Unexpected stylesheet references: ${stylesheetReferences.join(', ')}`);
}
if (scriptReferences.length !== 1 || scriptReferences[0] !== '/assets/js/app.js') {
    throw new Error(`Unexpected script references: ${scriptReferences.join(', ')}`);
}
if (!/<script\b[^>]*\btype="module"[^>]*\bsrc="\/assets\/js\/app\.js"[^>]*><\/script>/i.test(htmlDocument)) {
    throw new Error('Homepage must load its entry point as a native ES module.');
}
if (/<style\b/i.test(htmlDocument)) {
    throw new Error('Homepage must keep dashboard styles in the external stylesheet.');
}
if (/\/assets\/v\d+(?:\.\d+)+(?:\/|\\)/i.test(htmlDocument)) {
    throw new Error('Homepage must use stable asset paths without a version directory.');
}

// Preserve the existing contract searches while sourcing each concern from its
// deployed file. HTML remains first so structural index comparisons are stable.
const html = `${htmlDocument}\n${stylesheet}\n${runtimeScript}`;

const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
const duplicateIds = ids.filter((id, index) => ids.indexOf(id) !== index);
if (duplicateIds.length) throw new Error(`Duplicate ids: ${[...new Set(duplicateIds)].join(', ')}`);

const cards = [...html.matchAll(/<a\b[^>]*class="service-card"[^>]*>/g)].map((match) => match[0]);
if (cards.length === 0) throw new Error('Expected at least one service card.');
cards.forEach((tag, index) => {
    if (!/\btarget="_blank"/.test(tag)) throw new Error(`Service card ${index + 1} does not open in a new tab.`);
    const rel = tag.match(/\brel="([^"]+)"/);
    const tokens = rel ? rel[1].split(/\s+/) : [];
    if (!tokens.includes('noopener') || !tokens.includes('noreferrer')) {
        throw new Error(`Service card ${index + 1} is missing safe rel tokens.`);
    }
    if (/--card-index\s*:/.test(tag)) {
        throw new Error(`Service card ${index + 1} contains a manually assigned animation index.`);
    }
});
const expectedServiceCardIds = [
    'service-synology-nas',
    'service-qbittorrent',
    'service-peerbanhelper',
    'service-router-os',
    'service-speedtest',
    'service-astrbot',
    'service-gitea',
    'service-jellyfin',
    'service-zoraxy',
    'service-cliproxy',
    'service-ipmi-kvm'
];
const serviceCardIds = cards.map((tag) => tag.match(/\bdata-card-id="([^"]+)"/)?.[1] || '');
if (JSON.stringify(serviceCardIds) !== JSON.stringify(expectedServiceCardIds)) {
    throw new Error(`Static Services migration IDs/order mismatch: ${serviceCardIds.join(', ')}`);
}
const cliProxyTag = cards.find((tag) => tag.includes('data-card-id="service-cliproxy"')) || '';
if (!cliProxyTag.includes('data-probe-url="https://cliproxy.home.example.com/management.html/"')) {
    throw new Error('Static CLIProxy migration source lost its independent probe URL.');
}
const staticServiceSvgRoots = [...htmlDocument.matchAll(/<span\b[^>]*class="service-icon"[^>]*>\s*<svg\b([^>]*)>/g)]
    .map((match) => match[1]);
if (staticServiceSvgRoots.length !== expectedServiceCardIds.length
    || staticServiceSvgRoots.some((attributes) => (
        !attributes.includes('fill="none"')
        || !attributes.includes('stroke="currentColor"')
        || !attributes.includes('stroke-width="1.8"')
        || !attributes.includes('stroke-linecap="round"')
        || !attributes.includes('stroke-linejoin="round"')
    ))) {
    throw new Error('Static Services migration SVGs must carry their own safe drawing defaults.');
}
const serviceIconSvgRule = stylesheet.match(/\.service-icon svg\s*\{([^}]*)\}/)?.[1] || '';
if (/\b(?:fill|stroke|stroke-width|stroke-linecap|stroke-linejoin)\s*:/.test(serviceIconSvgRule)
    || /data-card-channel="services"[^}]*\{[^}]*(?:fill|stroke)\s*:/s.test(stylesheet)) {
    throw new Error('Service icon CSS must not override colors or strokes chosen by uploaded SVG files.');
}

const quickLinkPanelMatch = html.match(/<div\b[^>]*\bid="quick-links-panel"[^>]*\bhidden[^>]*>([\s\S]*?)<\/div>\s*<\/div>\s*<\/div>\s*<footer\b/);
if (!quickLinkPanelMatch) throw new Error('Missing the hidden static Quick Links panel.');
const quickLinkPanel = quickLinkPanelMatch[1];
const quickLinks = [...quickLinkPanel.matchAll(/<a\b([^>]*)\bclass="quick-link-card"([^>]*)>([\s\S]*?)<\/a>/g)]
    .map((match) => ({ tag: `<a${match[1]}class="quick-link-card"${match[2]}>`, body: match[3] }));
const expectedQuickLinks = [
    ['https://www.bilibili.com/', 'Bilibili', 'quick-bilibili'],
    ['https://github.com/', 'GitHub', 'quick-github'],
    ['https://mail.google.com/', 'Gmail', 'quick-gmail'],
    ['https://chatgpt.com/', 'ChatGPT', 'quick-chatgpt'],
    ['https://gemini.google.com/', 'Gemini', 'quick-gemini']
];
if (quickLinks.length !== expectedQuickLinks.length) {
    throw new Error(`Expected ${expectedQuickLinks.length} final Quick Links cards, found ${quickLinks.length}.`);
}
quickLinks.forEach(({ tag, body }, index) => {
    const [expectedHref, expectedName, expectedId] = expectedQuickLinks[index];
    if (!tag.includes(`href="${expectedHref}"`)
        || !tag.includes(`data-card-id="${expectedId}"`)
        || !tag.includes('target="_blank"')) {
        throw new Error(`Quick Link card ${index + 1} has the wrong destination or target.`);
    }
    const rel = tag.match(/\brel="([^"]+)"/);
    const tokens = rel ? rel[1].split(/\s+/) : [];
    if (!tokens.includes('noopener') || !tokens.includes('noreferrer')) {
        throw new Error(`Quick Link card ${index + 1} is missing safe rel tokens.`);
    }
    if (!body.includes(`<span class="quick-link-name">${expectedName}</span>`)
        || !/<svg\b[^>]*\bfocusable="false"/.test(body)) {
        throw new Error(`Quick Link card ${index + 1} is missing its expected name or inline SVG.`);
    }
    if (/service-state|service-desc|data-probe-url/.test(tag + body)) {
        throw new Error(`Quick Link card ${index + 1} must not participate in service status UI or probing.`);
    }
});
const allStaticCardIds = [...serviceCardIds, ...expectedQuickLinks.map((entry) => entry[2])];
if (new Set(allStaticCardIds).size !== 16) {
    throw new Error('The 11+5 static migration source must use 16 unique stable card IDs.');
}
if (/GitHub (?:Black|Blue|White)|Gemini \u00b7|quick-gemma-gradient/.test(quickLinkPanel)) {
    throw new Error('Unselected Stage 1A icon candidates remain in the final Quick Links panel.');
}
const quickLinkSvgMarkup = quickLinks.map(({ body }) => body).join('');
if (/<(?:script|foreignObject|image)\b/i.test(quickLinkSvgMarkup)
    || /\son[a-z]+\s*=/i.test(quickLinkSvgMarkup)
    || /(?:href|src)\s*=\s*["'](?:https?:|\/\/|data:)/i.test(quickLinkSvgMarkup)) {
    throw new Error('Quick Links inline SVG contains active or external content.');
}

const cardEditorLaunchTag = htmlDocument.match(/<button\b[^>]*\bid="card-editor-button"[^>]*>/)?.[0] || '';
if (!cardEditorLaunchTag.includes('type="button"')
    || !cardEditorLaunchTag.includes('role="menuitem"')
    || !cardEditorLaunchTag.includes('aria-haspopup="dialog"')
    || !cardEditorLaunchTag.includes('aria-controls="card-editor-dialog"')
    || !/\bdisabled(?:\s|>)/.test(cardEditorLaunchTag)) {
    throw new Error('The card editor launch button must remain a progressively enhanced disabled settings-menu item.');
}
const pageSettingsLaunchTag = htmlDocument.match(/<button\b[^>]*\bid="page-settings-button"[^>]*>/)?.[0] || '';
const faviconEditorLaunchTag = htmlDocument.match(/<button\b[^>]*\bid="favicon-editor-button"[^>]*>/)?.[0] || '';
if (!pageSettingsLaunchTag.includes('type="button"')
    || !pageSettingsLaunchTag.includes('aria-haspopup="menu"')
    || !pageSettingsLaunchTag.includes('aria-controls="page-settings-menu"')
    || !pageSettingsLaunchTag.includes('aria-expanded="false"')) {
    throw new Error('The page settings trigger must expose its popup-menu relationship and closed state.');
}
if (!faviconEditorLaunchTag.includes('role="menuitem"')
    || !faviconEditorLaunchTag.includes('aria-haspopup="dialog"')
    || !faviconEditorLaunchTag.includes('aria-controls="favicon-dialog"')) {
    throw new Error('The favicon editor launch button must be a settings-menu dialog trigger.');
}
if (!/<div\b[^>]*class="dashboard-footer-actions"[^>]*>[\s\S]*?id="background-picker-button"[\s\S]*?id="page-settings-button"[\s\S]*?<\/div>/.test(htmlDocument)
    || !/<div\b[^>]*id="page-settings-menu"[^>]*role="menu"[^>]*>[\s\S]*?id="card-editor-button"[\s\S]*?id="favicon-editor-button"[\s\S]*?<\/div>/.test(htmlDocument)) {
    throw new Error('The footer must keep background and page-settings controls in order, with card then favicon menu items.');
}
for (const requiredEditorId of [
    'card-editor-dialog',
    'card-editor-strip',
    'card-editor-form',
    'card-icon-select',
    'card-icon-input',
    'card-name-input',
    'card-description-input',
    'card-url-input',
    'card-probe-input',
    'card-delete-button',
    'card-save-button',
    'card-confirm-dialog',
    'card-confirm-cancel',
    'card-confirm-accept'
]) {
    if (!htmlDocument.includes(`id="${requiredEditorId}"`)) {
        throw new Error(`Missing configurable-card editor structure: ${requiredEditorId}.`);
    }
}
for (const requiredFaviconId of [
    'favicon-dialog',
    'favicon-close',
    'favicon-preview',
    'favicon-state-label',
    'favicon-select-button',
    'favicon-file-input',
    'favicon-remove-button',
    'favicon-save-button',
    'favicon-status',
    'favicon-remove-confirm-dialog',
    'favicon-remove-cancel',
    'favicon-remove-confirm',
    'favicon-remove-status'
]) {
    if (!htmlDocument.includes(`id="${requiredFaviconId}"`)) {
        throw new Error(`Missing favicon editor structure: ${requiredFaviconId}.`);
    }
}
const cardIconInputTag = htmlDocument.match(/<input\b[^>]*\bid="card-icon-input"[^>]*>/)?.[0] || '';
if (!cardIconInputTag.includes('type="file"') || !cardIconInputTag.includes('accept=".svg,image/svg+xml"')) {
    throw new Error('Card icons must be selected through an SVG-only file input.');
}
const faviconInputTag = htmlDocument.match(/<input\b[^>]*\bid="favicon-file-input"[^>]*>/)?.[0] || '';
if (!faviconInputTag.includes('type="file"') || !['.svg', '.png', '.jpg', '.webp', '.gif', '.ico'].every((format) => faviconInputTag.includes(format))) {
    throw new Error('The favicon file input must support all approved icon formats.');
}
if (!pageSettingsScript.includes("event.key === 'ArrowDown'")
    || !pageSettingsScript.includes("event.key === 'ArrowUp'")
    || !pageSettingsScript.includes("event.key === 'Home'")
    || !pageSettingsScript.includes("event.key === 'End'")
    || !pageSettingsScript.includes("event.key === 'Escape'")
    || !pageSettingsScript.includes("container.addEventListener('pointerdown'")
    || !pageSettingsScript.includes("container.addEventListener('focusout'")
    || !pageSettingsScript.includes("document.addEventListener('pointerdown'")
    || !pageSettingsScript.includes("document.addEventListener('pointerup'")
    || !pageSettingsScript.includes('!pointerIsInside && isOpen()')) {
    throw new Error('The page settings menu is missing required keyboard, focus, or outside-click behavior.');
}
const settingsMenuClickStart = pageSettingsScript.indexOf("menu.addEventListener('click'");
const settingsMenuPointerdownStart = pageSettingsScript.indexOf("container.addEventListener('pointerdown'", settingsMenuClickStart);
if (settingsMenuClickStart < 0
    || settingsMenuPointerdownStart < settingsMenuClickStart
    || !pageSettingsScript.slice(settingsMenuClickStart, settingsMenuPointerdownStart).includes('window.setTimeout(() => close(), 0)')
    || pageSettingsScript.slice(settingsMenuClickStart, settingsMenuPointerdownStart).includes('}, true);')) {
    throw new Error('Settings menu item clicks must defer closing until Edge finishes native dialog activation.');
}
const pageSettingsMenuRule = stylesheet.match(/\.page-settings-menu\s*\{([^}]*)\}/)?.[1] || '';
const pageSettingsVisibleRule = stylesheet.match(/\.page-settings\[data-menu-open="true"\]\s+\.page-settings-menu\s*\{([^}]*)\}/)?.[1] || '';
if (!/opacity:\s*0/.test(pageSettingsMenuRule)
    || !/translateY\(6px\)\s*scale\(0\.98\)/.test(pageSettingsMenuRule)
    || !/opacity\s+150ms\s+ease/.test(pageSettingsMenuRule)
    || !/transform\s+170ms\s+cubic-bezier\(0\.2,\s*0\.8,\s*0\.2,\s*1\)/.test(pageSettingsMenuRule)
    || !/pointer-events:\s*none/.test(pageSettingsMenuRule)
    || !/will-change:\s*opacity,\s*transform/.test(pageSettingsMenuRule)
    || !/opacity:\s*1/.test(pageSettingsVisibleRule)
    || !/translateY\(0\)\s*scale\(1\)/.test(pageSettingsVisibleRule)
    || !/pointer-events:\s*auto/.test(pageSettingsVisibleRule)) {
    throw new Error('The page settings menu lost its lightweight bidirectional motion or closing interaction guard.');
}

function makePageSettingsHarness(reducedMotion = false) {
    function makeEventTarget(extra = {}) {
        const listeners = new Map();
        return Object.assign({
            addEventListener(type, handler) {
                if (!listeners.has(type)) listeners.set(type, new Set());
                listeners.get(type).add(handler);
            },
            removeEventListener(type, handler) {
                listeners.get(type)?.delete(handler);
            },
            dispatch(type, event = {}) {
                Array.from(listeners.get(type) || []).forEach((handler) => handler(event));
            }
        }, extra);
    }

    const frames = new Map();
    const timers = new Map();
    let nextFrame = 1;
    let nextTimer = 1;
    let runtimeDocument;
    function makeMenuItem() {
        const attributes = new Map();
        return makeEventTarget({
            disabled: false,
            focusCount: 0,
            getAttribute(name) { return attributes.get(name) ?? null; },
            setAttribute(name, value) { attributes.set(name, String(value)); },
            focus() {
                this.focusCount += 1;
                runtimeDocument.activeElement = this;
            },
            closest(selector) { return selector === '[role="menuitem"]' ? this : null; }
        });
    }
    const items = [makeMenuItem(), makeMenuItem(), makeMenuItem()];
    const menu = makeEventTarget({
        hidden: true,
        querySelectorAll(selector) { return selector === '[role="menuitem"]' ? items : []; }
    });
    const triggerAttributes = new Map();
    const trigger = makeEventTarget({
        isConnected: true,
        focusCount: 0,
        getAttribute(name) { return triggerAttributes.get(name) ?? null; },
        setAttribute(name, value) { triggerAttributes.set(name, String(value)); },
        focus() {
            this.focusCount += 1;
            runtimeDocument.activeElement = this;
        }
    });
    const container = makeEventTarget({
        dataset: { menuOpen: 'false' },
        contains(target) {
            return target === this || target === trigger || target === menu || items.includes(target);
        }
    });
    const documentTarget = makeEventTarget();
    runtimeDocument = Object.assign(documentTarget, {
        activeElement: trigger,
        getElementById(id) {
            return {
                'page-settings': container,
                'page-settings-button': trigger,
                'page-settings-menu': menu
            }[id] || null;
        }
    });
    const windowTarget = makeEventTarget({
        matchMedia() { return { matches: reducedMotion }; },
        requestAnimationFrame(callback) {
            const id = nextFrame++;
            frames.set(id, callback);
            return id;
        },
        cancelAnimationFrame(id) { frames.delete(id); },
        setTimeout(callback, delay) {
            const id = nextTimer++;
            timers.set(id, { callback, delay });
            return id;
        },
        clearTimeout(id) { timers.delete(id); }
    });
    const executablePageSettings = pageSettingsScript.replace(/^export /, '');
    const context = vm.createContext({ document: runtimeDocument, Promise, window: windowTarget });
    new vm.Script(
        `${executablePageSettings}\n`
        + 'globalThis.initializePageSettings = initializePageSettings;'
    ).runInContext(context);
    const controller = context.initializePageSettings();
    return {
        container,
        controller,
        items,
        menu,
        trigger,
        frameCount() { return frames.size; },
        runNextFrame() {
            const entry = frames.entries().next().value;
            if (!entry) return;
            frames.delete(entry[0]);
            entry[1]();
        },
        runAllFrames() {
            while (frames.size) this.runNextFrame();
        },
        runTimers() {
            Array.from(timers.entries()).forEach(([id, timer]) => {
                timers.delete(id);
                timer.callback();
            });
        },
        timerCount() { return timers.size; },
        timerDelays() { return Array.from(timers.values(), (timer) => timer.delay); }
    };
}

const animatedSettings = makePageSettingsHarness();
if (!animatedSettings.menu.hidden
    || animatedSettings.container.dataset.menuOpen !== 'false'
    || animatedSettings.trigger.getAttribute('aria-expanded') !== 'false') {
    throw new Error('The page settings menu must initialize fully hidden and collapsed.');
}
animatedSettings.controller.open();
if (animatedSettings.menu.hidden
    || animatedSettings.container.dataset.menuOpen !== 'false'
    || animatedSettings.trigger.getAttribute('aria-expanded') !== 'true'
    || animatedSettings.items[0].focusCount !== 0
    || animatedSettings.frameCount() !== 1) {
    throw new Error('The page settings menu must expose an initial transparent frame before opening.');
}
animatedSettings.runNextFrame();
if (animatedSettings.container.dataset.menuOpen !== 'false' || animatedSettings.frameCount() !== 1) {
    throw new Error('The page settings menu reveal must wait for its second animation frame.');
}
animatedSettings.runNextFrame();
if (!animatedSettings.controller.isOpen()
    || animatedSettings.container.dataset.menuOpen !== 'true'
    || animatedSettings.items[0].focusCount !== 1) {
    throw new Error('The page settings menu did not reveal and focus after two animation frames.');
}
animatedSettings.controller.close({ restoreFocus: true });
if (animatedSettings.controller.isOpen()
    || animatedSettings.menu.hidden
    || animatedSettings.container.dataset.menuOpen !== 'false'
    || animatedSettings.trigger.getAttribute('aria-expanded') !== 'false'
    || animatedSettings.trigger.focusCount !== 1
    || JSON.stringify(animatedSettings.timerDelays()) !== JSON.stringify([220])) {
    throw new Error('The page settings menu closed before its exit transition or failed to restore trigger state.');
}
animatedSettings.menu.dispatch('transitionend', {
    target: animatedSettings.menu,
    propertyName: 'transform'
});
if (animatedSettings.menu.hidden) {
    throw new Error('A non-opacity transition ended the page settings menu prematurely.');
}
animatedSettings.menu.dispatch('transitionend', {
    target: animatedSettings.menu,
    propertyName: 'opacity'
});
if (!animatedSettings.menu.hidden || animatedSettings.timerCount() !== 0) {
    throw new Error('The page settings menu transition did not finish by hiding and cleaning its fallback timer.');
}

const fallbackSettings = makePageSettingsHarness();
fallbackSettings.controller.open();
fallbackSettings.runAllFrames();
fallbackSettings.controller.close();
fallbackSettings.runTimers();
if (!fallbackSettings.menu.hidden) {
    throw new Error('The page settings menu close timeout did not provide a bounded fallback.');
}

const interruptedSettings = makePageSettingsHarness();
interruptedSettings.controller.open();
interruptedSettings.runAllFrames();
interruptedSettings.controller.close();
interruptedSettings.controller.open({ last: true });
interruptedSettings.menu.dispatch('transitionend', {
    target: interruptedSettings.menu,
    propertyName: 'opacity'
});
interruptedSettings.runAllFrames();
interruptedSettings.runTimers();
if (interruptedSettings.menu.hidden
    || !interruptedSettings.controller.isOpen()
    || interruptedSettings.container.dataset.menuOpen !== 'true'
    || interruptedSettings.items[2].focusCount !== 1
    || interruptedSettings.timerCount() !== 0) {
    throw new Error('Reopening the page settings menu must cancel stale close completion.');
}

const staleRevealSettings = makePageSettingsHarness();
staleRevealSettings.controller.open();
staleRevealSettings.controller.close();
staleRevealSettings.runAllFrames();
staleRevealSettings.runTimers();
if (!staleRevealSettings.menu.hidden || staleRevealSettings.container.dataset.menuOpen !== 'false') {
    throw new Error('Closing during page settings entrance must cancel stale reveal frames.');
}

const reducedSettings = makePageSettingsHarness(true);
reducedSettings.controller.open();
if (reducedSettings.menu.hidden
    || reducedSettings.container.dataset.menuOpen !== 'true'
    || reducedSettings.items[0].focusCount !== 1
    || reducedSettings.frameCount() !== 0) {
    throw new Error('Reduced-motion page settings must reveal and focus immediately.');
}
reducedSettings.controller.close({ restoreFocus: true });
if (!reducedSettings.menu.hidden
    || reducedSettings.trigger.focusCount !== 1
    || reducedSettings.timerCount() !== 0) {
    throw new Error('Reduced-motion page settings must close and restore focus immediately.');
}
if (!faviconManagerScript.includes("new URL('/api/favicon', document.baseURI)")
    || !faviconManagerScript.includes("new URL('/api/favicon.svg', document.baseURI)")
    || !faviconManagerScript.includes("document.head.querySelector('link[data-managed-favicon=\"true\"]')")
    || !faviconManagerScript.includes('sanitizeSvgMarkup(await file.text())')
    || !faviconManagerScript.includes("JSON.stringify({ version: 1, iconSvg })")
    || !faviconManagerScript.includes("error.status === 404 ? 'unsupported' : 'unavailable'")
    || !faviconManagerScript.includes('await requestIcon(\'PATCH\', null)')) {
    throw new Error('The favicon manager is missing its API, managed-link, safe-draft, removal, or old-backend contract.');
}
// Page artwork updates are independent, atomic across all three slots, and recoverable.
const artworkTags = [...htmlDocument.matchAll(/<span\b[^>]*\bdata-site-icon\b[^>]*>/g)];
if (artworkTags.length !== 3 || artworkTags.some(([tag]) => !tag.includes('aria-hidden="true"'))
    || !htmlDocument.includes('id="page-icon-editor-button"')
    || !appScript.includes("kind: 'page-icon'")) {
    throw new Error('The page icon setting must control exactly three decorative artwork slots.');
}
const artworkLoads = [];
let artworkRasterMode = 'padded';
const artworkDocument = {
    createElement(tag) {
        if (tag !== 'canvas') throw new Error('Unexpected artwork element');
        const canvas = { width: 0, height: 0 };
        canvas.getContext = () => {
            if (artworkRasterMode === 'unavailable') return null;
            return {
                drawImage() {},
                getImageData() {
                    const data = new Uint8ClampedArray(canvas.width * canvas.height * 4);
                    if (artworkRasterMode === 'padded') {
                        for (let y = 64; y < 192; y += 1) {
                            for (let x = 32; x < 224; x += 1) {
                                data[(y * canvas.width + x) * 4 + 3] = 255;
                            }
                        }
                    }
                    return { data };
                }
            };
        };
        return canvas;
    }
};
const artworkContext = vm.createContext({
    document: artworkDocument,
    Image: class {
        constructor() {
            this.naturalWidth = 256;
            this.naturalHeight = 256;
            artworkLoads.push(this);
        }
    }
});
vm.runInContext(faviconManagerScript.slice(faviconManagerScript.indexOf('function pageIconCrop('),
    faviconManagerScript.indexOf('export function initializeFaviconManager(')), artworkContext);
const artworkSlots = Array.from({ length: 3 }, () => {
    const properties = new Map();
    const image = { src: '/default.svg', getAttribute() { return '/default.svg'; } };
    return { image, properties, querySelector() { return image; }, style: {
        setProperty(key, value) { properties.set(key, value); },
        removeProperty(key) { properties.delete(key); }
    } };
});
const artworkController = artworkContext.createPageIconController(artworkSlots);
artworkController.apply('/first.svg');
if (artworkSlots.some((slot) => slot.image.src !== '/default.svg')) {
    throw new Error('Page artwork changed before the SVG loaded.');
}
artworkController.apply('/second.svg');
artworkLoads[0].onload();
if (artworkSlots.some((slot) => slot.image.src !== '/default.svg')) {
    throw new Error('An old SVG load overwrote newer page artwork.');
}
artworkLoads[1].onload();
if (artworkSlots.some((slot) => slot.image.src !== '/second.svg'
    || Math.abs(Number(slot.properties.get('--badge-aspect')) - 194 / 130) > 1e-8
    || Math.abs(parseFloat(slot.properties.get('--badge-image-left')) + 100 * 31 / 194) > 1e-8
    || Math.abs(parseFloat(slot.properties.get('--badge-image-top')) + 100 * 63 / 130) > 1e-8)) {
    throw new Error('Page artwork lost its shared alpha crop or safety padding.');
}
artworkController.apply('/second.svg');
if (artworkLoads.length !== 2) throw new Error('Unchanged page artwork was loaded again.');
artworkController.apply('/failed.svg');
artworkLoads[2].onerror();
if (artworkSlots.some((slot) => slot.image.src !== '/default.svg' || slot.properties.size)) {
    throw new Error('Failed SVG loading must restore the default artwork and crop.');
}
artworkController.apply('/failed.svg');
if (artworkLoads.length !== 4) throw new Error('Failed SVG loading must remain retryable.');
artworkController.apply(null);
artworkLoads[3].onload();
if (artworkSlots.some((slot) => slot.image.src !== '/default.svg')) {
    throw new Error('Removing page artwork must cancel pending SVG loads.');
}
for (const mode of ['transparent', 'unavailable']) {
    artworkRasterMode = mode;
    artworkController.apply(`/${mode}.svg`);
    artworkLoads.at(-1).naturalWidth = 512;
    artworkLoads.at(-1).onload();
    if (artworkSlots.some((slot) => slot.properties.get('--badge-aspect') !== '2'
        || slot.properties.get('--badge-image-width') !== '100%')) {
        throw new Error('Transparent SVG or unavailable canvas must preserve the full image ratio.');
    }
}

const cardEditorDialogRule = stylesheet.match(/\.card-editor-dialog,\s*\.card-confirm-dialog\s*\{([^}]*)\}/)?.[1] || '';
const cardConfirmDialogRule = [...stylesheet.matchAll(/\.card-confirm-dialog\s*\{([^}]*)\}/g)]
    .map((match) => match[1])
    .find((rule) => /28rem/.test(rule)) || '';
const cardEditorMotionRule = [...stylesheet.matchAll(/\.card-editor-dialog\s*\{([^}]*)\}/g)]
    .map((match) => match[1])
    .find((rule) => /translateY\(12px\)/.test(rule)) || '';
const cardConfirmMotionRule = [...stylesheet.matchAll(/\.card-confirm-dialog\s*\{([^}]*)\}/g)]
    .map((match) => match[1])
    .find((rule) => /translateY\(8px\)/.test(rule)) || '';
const cardEditorStripRule = stylesheet.match(/\.card-editor-strip\s*\{([^}]*)\}/)?.[1] || '';
const cardEditorOptionRule = stylesheet.match(/\.card-editor-option\s*\{([^}]*)\}/)?.[1] || '';
const narrowEditorStart = stylesheet.indexOf('@media (max-width: 767px)');
const compactEditorStart = stylesheet.indexOf('@media (max-width: 520px)');
const pointerEditorStart = stylesheet.indexOf('@media (hover: none), (pointer: coarse)');
const narrowEditorRules = stylesheet.slice(narrowEditorStart, compactEditorStart);
const compactEditorRules = stylesheet.slice(compactEditorStart, pointerEditorStart);
if (!/width:\s*min\(68rem,\s*calc\(100vw - 2rem\)\)/.test(cardEditorDialogRule)
    || !/width:\s*min\(28rem,\s*calc\(100vw - 2rem\)\)/.test(cardConfirmDialogRule)
    || !/display:\s*grid/.test(cardEditorStripRule)
    || !/grid-template-columns:\s*repeat\(auto-fill,\s*minmax\(9rem,\s*1fr\)\)/.test(cardEditorStripRule)
    || !/grid-auto-rows:\s*minmax\(4\.5rem,\s*auto\)/.test(cardEditorStripRule)
    || !/max-height:\s*10\.2rem/.test(cardEditorStripRule)
    || !/overflow-x:\s*hidden/.test(cardEditorStripRule)
    || !/overflow-y:\s*auto/.test(cardEditorStripRule)
    || /display:\s*flex/.test(cardEditorStripRule)
    || !/width:\s*auto/.test(cardEditorOptionRule)
    || !/min-width:\s*0/.test(cardEditorOptionRule)) {
    throw new Error('Card selection must use a bounded responsive grid instead of a single horizontal strip.');
}
if (!/opacity:\s*0/.test(cardEditorMotionRule)
    || !/transform:\s*translateY\(12px\)\s*scale\(0\.98\)/.test(cardEditorMotionRule)
    || !/opacity\s+180ms\s+ease/.test(cardEditorMotionRule)
    || !/transform\s+220ms\s+cubic-bezier\(0\.2,\s*0\.8,\s*0\.2,\s*1\)/.test(cardEditorMotionRule)
    || !/opacity:\s*0/.test(cardConfirmMotionRule)
    || !/transform:\s*translateY\(8px\)\s*scale\(0\.98\)/.test(cardConfirmMotionRule)
    || !/opacity\s+160ms\s+ease/.test(cardConfirmMotionRule)
    || !/transform\s+180ms\s+cubic-bezier\(0\.2,\s*0\.8,\s*0\.2,\s*1\)/.test(cardConfirmMotionRule)
    || !/\.card-editor-dialog\.is-visible\s*\{[^}]*opacity:\s*1;[^}]*translateY\(0\)\s*scale\(1\)/.test(stylesheet)
    || !/\.card-confirm-dialog\.is-visible\s*\{[^}]*opacity:\s*1;[^}]*translateY\(0\)\s*scale\(1\)/.test(stylesheet)
    || !/\.card-editor-dialog\.is-closing\s*\{[^}]*pointer-events:\s*none;[^}]*transition-duration:\s*180ms/.test(stylesheet)
    || !/\.card-confirm-dialog\.is-closing\s*\{[^}]*pointer-events:\s*none;[^}]*transition-duration:\s*160ms/.test(stylesheet)
    || !/\.card-editor-dialog::backdrop\s*\{[^}]*transition:\s*opacity\s+220ms\s+ease/.test(stylesheet)
    || !/\.card-confirm-dialog::backdrop\s*\{[^}]*transition:\s*opacity\s+180ms\s+ease/.test(stylesheet)
    || !/\.card-editor-dialog\.is-visible::backdrop,\s*\.card-confirm-dialog\.is-visible::backdrop\s*\{[^}]*opacity:\s*1/.test(stylesheet)
    || !/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?\.card-editor-dialog,\s*\.card-confirm-dialog,\s*\.favicon-dialog,\s*\.favicon-remove-confirm-dialog\s*\{[^}]*transform:\s*none/.test(stylesheet)) {
    throw new Error('Card editor and confirmation dialogs lost their background-picker-aligned motion or reduced-motion contract.');
}
if (narrowEditorStart < 0
    || compactEditorStart < narrowEditorStart
    || pointerEditorStart < compactEditorStart
    || !/\.card-editor-strip\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/.test(narrowEditorRules)
    || !/\.card-editor-drag-handle\s*\{[^}]*display:\s*none/.test(narrowEditorRules)
    || !/\.card-editor-option\s*\{[^}]*grid-template-columns:\s*1fr/.test(narrowEditorRules)
    || !/\.card-editor-strip\s*\{[^}]*grid-template-columns:\s*1fr/.test(compactEditorRules)) {
    throw new Error('Card editor grid must follow the dashboard two-column and one-column responsive breakpoints.');
}

const configurableCardScripts = `${cardSvgScript}\n${cardEditorScript}\n${cardManagerScript}`;
if (/\.(?:innerHTML|outerHTML)\s*=|insertAdjacentHTML\s*\(/.test(configurableCardScripts)) {
    throw new Error('Configurable-card code must not inject untrusted markup through HTML parsing sinks.');
}
if (!cardManagerScript.includes("const CARD_CACHE_KEY = 'homelab-dashboard-cards-cache-v1'")
    || !cardManagerScript.includes("new URL('/api/cards', document.baseURI)")
    || !cardManagerScript.includes("new URL('/api/cards/initialize', document.baseURI)")
    || !cardManagerScript.includes('const staticSnapshot = extractStaticCards();')
    || cardManagerScript.indexOf('const staticSnapshot = extractStaticCards();') > cardManagerScript.indexOf('const state = await loadServerState();')
    || !cardManagerScript.includes('normalizeCardsResponse(JSON.parse(raw))')
    || !cardManagerScript.includes("if (response.status === 409 && response.payload?.error !== 'already_initialized')")
    || !cardManagerScript.includes('const confirmed = await getServerState();')
    || !/channel:\s*key,\r?\n\s*expectedRevision,/.test(cardManagerScript)
    || !cardManagerScript.includes("conflict.name = 'CardRevisionConflictError'")) {
    throw new Error('Card manager lost its frozen migration source, validated cache, initialization race, or revision contract.');
}
if (!cardManagerScript.includes("response.headers?.get?.('Content-Length')")
    || !cardManagerScript.includes('response.body.getReader()')
    || !cardManagerScript.includes('receivedBytes > MAX_RESPONSE_BYTES')
    || !cardManagerScript.includes('await reader.cancel()')
    || !cardManagerScript.includes('function removeCache()')
    || !cardManagerScript.includes('utf8ByteLength(serialized) > MAX_CACHE_BYTES')
    || !cardManagerScript.includes('removeCache();')) {
    throw new Error('Card responses and cache writes must remain byte-bounded without retaining stale oversized cache state.');
}
const readCacheStart = cardManagerScript.indexOf('    function readCache() {');
const readCacheEnd = cardManagerScript.indexOf('    function writeCache(state) {', readCacheStart);
const readCacheScript = cardManagerScript.slice(readCacheStart, readCacheEnd);
const invalidCacheCatch = readCacheScript.match(/catch\s*\(error\)\s*\{([\s\S]*?)\}/)?.[1] || '';
if (readCacheStart < 0
    || readCacheEnd < 0
    || !/if\s*\(!cached\.initialized\)\s*\{[\s\S]*?removeCache\(\);[\s\S]*?return null;/.test(readCacheScript)
    || invalidCacheCatch.indexOf('removeCache();') < 0
    || invalidCacheCatch.indexOf('removeCache();') > invalidCacheCatch.indexOf('return null;')) {
    throw new Error('Invalid or uninitialized card cache entries must be removed before fallback.');
}
if (!cardManagerScript.includes('grid.replaceChildren(fragment);')
    || !cardManagerScript.includes('name.textContent = card.name;')
    || !cardManagerScript.includes('icon.append(createSafeSvgElement(card.iconSvg));')
    || !cardManagerScript.includes("empty.className = 'card-empty-state'")) {
    throw new Error('Dynamic cards must render through safe DOM construction and support explicit empty channels.');
}
if (!cardEditorScript.includes("const BLANK_CARD_ID = '__new_card__'")
    || !cardEditorScript.includes('const MAX_CARDS = 32;')
    || !cardEditorScript.includes('function promoteBlankIfComplete()')
    || !cardEditorScript.includes("addItem.dataset.addCard = 'true'")
    || !cardEditorScript.includes('buildChangeSummary(originalCards, draftCards)')
    || !cardEditorScript.includes('await manager.saveChannel(channel, revision, draftCards)')
    || !cardEditorScript.includes("error.name === 'CardRevisionConflictError'")
    || !cardEditorScript.includes("window.addEventListener('beforeunload'")
    || !cardEditorScript.includes('function focusSelectedStripOption()')
    || !cardEditorScript.includes("selectedItem?.scrollIntoView({ block: 'nearest', inline: 'nearest' });")
    || !cardEditorScript.includes('export function moveCardToTargetPosition(cards, sourceId, targetId)')
    || !cardEditorScript.includes('const targetId = selectedId;')
    || !cardEditorScript.includes('targetCard === blankCard')
    || !cardEditorScript.includes('function serverValidationFeedback(error)')
    || !cardEditorScript.includes("field: 'probeUrl'")) {
    throw new Error('Card editor lost permanent blank-add, batched draft, dirty guard, or conflict preservation behavior.');
}
const dialogMotionStart = dialogMotionModuleScript.indexOf('export function createDialogMotionController(dialog, closeFallbackMs) {');
if (dialogMotionStart < 0) throw new Error('Shared dialog motion controller is missing.');
const dialogMotionScript = dialogMotionModuleScript.slice(dialogMotionStart).replace(/^export /, '');
function makeDialogMotionHarness(reducedMotion = false, fallbackMs = 300) {
    const classes = new Set();
    const attributes = new Set();
    const listeners = new Map();
    const animationFrames = [];
    const timers = new Map();
    let nextTimer = 1;
    const dialog = {
        open: false,
        closeCount: 0,
        showCount: 0,
        returnValue: '',
        classList: {
            add(...names) { names.forEach((name) => classes.add(name)); },
            contains(name) { return classes.has(name); },
            remove(...names) { names.forEach((name) => classes.delete(name)); }
        },
        addEventListener(type, handler) {
            if (!listeners.has(type)) listeners.set(type, new Set());
            listeners.get(type).add(handler);
        },
        removeEventListener(type, handler) {
            listeners.get(type)?.delete(handler);
        },
        dispatch(type, event = {}) {
            Array.from(listeners.get(type) || []).forEach((handler) => handler(event));
        },
        hasAttribute(name) { return name === 'open' && (this.open || attributes.has('open')); },
        setAttribute(name) {
            attributes.add(name);
            if (name === 'open') this.open = true;
        },
        removeAttribute(name) {
            attributes.delete(name);
            if (name === 'open') this.open = false;
        },
        showModal() {
            this.showCount += 1;
            this.open = true;
        },
        close(returnValue = '') {
            this.closeCount += 1;
            this.returnValue = returnValue;
            this.open = false;
            attributes.delete('open');
            this.dispatch('close', { target: this });
        }
    };
    const runtimeWindow = {
        clearTimeout(id) { timers.delete(id); },
        matchMedia() { return { matches: reducedMotion }; },
        requestAnimationFrame(callback) {
            animationFrames.push(callback);
            return animationFrames.length;
        },
        setTimeout(callback, delay) {
            if (delay !== fallbackMs) throw new Error(`Unexpected dialog close fallback: ${delay}.`);
            const id = nextTimer++;
            timers.set(id, callback);
            return id;
        }
    };
    const context = vm.createContext({ window: runtimeWindow });
    new vm.Script(
        `${dialogMotionScript}\n`
        + 'globalThis.createDialogMotionController = createDialogMotionController;'
    ).runInContext(context);
    return {
        classes,
        controller: context.createDialogMotionController(dialog, fallbackMs),
        dialog,
        runNextFrame() {
            const callback = animationFrames.shift();
            if (callback) callback();
        },
        runAllFrames() {
            while (animationFrames.length) animationFrames.shift()();
        },
        runTimers() {
            Array.from(timers.values()).forEach((callback) => callback());
        },
        timerCount() { return timers.size; }
    };
}

const animatedDialog = makeDialogMotionHarness();
let editorOpenFocuses = 0;
animatedDialog.controller.show(() => { editorOpenFocuses += 1; });
if (!animatedDialog.dialog.open
    || animatedDialog.dialog.showCount !== 1
    || animatedDialog.classes.has('is-visible')) {
    throw new Error('Card dialog must expose its initial hidden frame before entering.');
}
animatedDialog.runNextFrame();
if (animatedDialog.classes.has('is-visible')) {
    throw new Error('Card dialog reveal must wait for the second animation frame.');
}
animatedDialog.runNextFrame();
if (!animatedDialog.classes.has('is-visible') || editorOpenFocuses !== 1) {
    throw new Error('Card dialog did not reveal and focus after its two-frame entrance setup.');
}
let editorCloseFocuses = 0;
const animatedClose = animatedDialog.controller.hide('saved', () => { editorCloseFocuses += 1; });
const duplicateClose = animatedDialog.controller.hide('ignored');
if (duplicateClose !== animatedClose
    || animatedDialog.dialog.closeCount !== 0
    || !animatedDialog.classes.has('is-closing')
    || animatedDialog.classes.has('is-visible')) {
    throw new Error('Card dialog closed before its exit transition or accepted a duplicate close.');
}
animatedDialog.dialog.dispatch('transitionend', { target: animatedDialog.dialog, propertyName: 'transform' });
if (animatedDialog.dialog.closeCount !== 0) {
    throw new Error('A non-opacity transition ended the card dialog prematurely.');
}
animatedDialog.dialog.dispatch('transitionend', { target: animatedDialog.dialog, propertyName: 'opacity' });
if (!await animatedClose
    || animatedDialog.dialog.closeCount !== 1
    || animatedDialog.dialog.returnValue !== 'saved'
    || editorCloseFocuses !== 1
    || animatedDialog.classes.has('is-closing')
    || animatedDialog.timerCount() !== 0) {
    throw new Error('Card dialog transition completion did not close once, clean state, and restore focus.');
}

const staleRevealDialog = makeDialogMotionHarness();
staleRevealDialog.controller.show();
const staleClose = staleRevealDialog.controller.hide();
staleRevealDialog.runAllFrames();
if (staleRevealDialog.classes.has('is-visible')) {
    throw new Error('A stale entrance frame revealed a closing card dialog.');
}
staleRevealDialog.runTimers();
if (!await staleClose || staleRevealDialog.dialog.closeCount !== 1) {
    throw new Error('Card dialog close timeout did not provide a bounded fallback.');
}

const reducedDialog = makeDialogMotionHarness(true, 240);
let reducedOpenFocuses = 0;
let reducedCloseFocuses = 0;
reducedDialog.controller.show(() => { reducedOpenFocuses += 1; });
const reducedClose = reducedDialog.controller.hide('cancel', () => { reducedCloseFocuses += 1; });
if (!await reducedClose
    || reducedOpenFocuses !== 1
    || reducedCloseFocuses !== 1
    || reducedDialog.dialog.closeCount !== 1
    || reducedDialog.timerCount() !== 0) {
    throw new Error('Reduced-motion card dialogs must open, focus, and close immediately.');
}

const settleConfirmationStart = cardEditorScript.indexOf('    async function settleConfirmation(result) {');
const settleConfirmationEnd = cardEditorScript.indexOf('\n\n    function askConfirmation', settleConfirmationStart);
const settleConfirmationScript = cardEditorScript.slice(settleConfirmationStart, settleConfirmationEnd).replace(/^    /gm, '');
let releaseConfirmationClose;
const confirmationCloseGate = new Promise((resolve) => { releaseConfirmationClose = resolve; });
let confirmationResolved = 'pending';
let confirmationFocusRestored = false;
let confirmationReturnValue = null;
const confirmationReturnFocus = {
    isConnected: true,
    closest: () => null,
    focus: () => { confirmationFocusRestored = true; }
};
const confirmationContext = vm.createContext({
    confirmResolver: (value) => { confirmationResolved = value; },
    confirmReturnFocus: confirmationReturnFocus,
    confirmMotion: {
        isClosing: () => false,
        async hide(returnValue, restoreFocus) {
            confirmationReturnValue = returnValue;
            await confirmationCloseGate;
            restoreFocus();
            return true;
        }
    }
});
new vm.Script(
    `${settleConfirmationScript}\n`
    + 'globalThis.settleConfirmation = settleConfirmation;'
).runInContext(confirmationContext);
const confirmationSettlement = confirmationContext.settleConfirmation(true);
await Promise.resolve();
if (confirmationResolved !== 'pending' || confirmationReturnValue !== 'confirm') {
    throw new Error('Card confirmation resolved before its exit animation completed.');
}
releaseConfirmationClose();
await confirmationSettlement;
if (confirmationResolved !== true || !confirmationFocusRestored) {
    throw new Error('Card confirmation did not resolve and restore focus after closing.');
}

const requestCloseStart = cardEditorScript.indexOf('    async function requestClose() {');
const requestCloseEnd = cardEditorScript.indexOf('\n\n    function setBusy', requestCloseStart);
const openEditorStart = cardEditorScript.indexOf('    async function openEditor() {');
const openEditorEnd = cardEditorScript.indexOf('\n\n    async function deleteSelectedCard', openEditorStart);
const saveDraftStart = cardEditorScript.indexOf('    async function saveDraft(event) {');
const saveDraftEnd = cardEditorScript.indexOf('\n\n    async function importSvgFile', saveDraftStart);
const cardEditorEventStart = cardEditorScript.indexOf("    launchButton.addEventListener('click', openEditor);");
const cardEditorEventScript = cardEditorScript.slice(cardEditorEventStart);
const requestCloseScript = cardEditorScript.slice(requestCloseStart, requestCloseEnd);
const openEditorScript = cardEditorScript.slice(openEditorStart, openEditorEnd);
const saveDraftScript = cardEditorScript.slice(saveDraftStart, saveDraftEnd);
if (settleConfirmationStart < 0
    || settleConfirmationEnd < 0
    || requestCloseStart < 0
    || requestCloseEnd < 0
    || openEditorStart < 0
    || openEditorEnd < 0
    || saveDraftStart < 0
    || saveDraftEnd < 0
    || !requestCloseScript.includes("await editorMotion.hide('',")
    || requestCloseScript.includes('dialog.close(')
    || !openEditorScript.includes('editorMotion.show(() => {')
    || openEditorScript.includes('dialog.showModal(')
    || !saveDraftScript.includes("await editorMotion.hide('saved',")
    || saveDraftScript.includes('dialog.close(')
    || settleConfirmationScript.includes('confirmDialog.close(')
    || !cardEditorEventScript.includes("dialog.addEventListener('cancel'")
    || !cardEditorEventScript.includes('requestClose();')
    || !cardEditorEventScript.includes('if (event.target === dialog) requestClose();')) {
    throw new Error('Every card editor open/close path must use the animated controller without direct native closure.');
}
const moveCardStart = cardEditorScript.indexOf('export function moveCardToTargetPosition(cards, sourceId, targetId) {');
const moveCardEndMarker = '\n}\n\nfunction serverValidationFeedback(error) {';
const moveCardEnd = cardEditorScript.indexOf(moveCardEndMarker, moveCardStart);
if (moveCardStart < 0 || moveCardEnd < 0) {
    throw new Error('Card target-position reorder helper is missing.');
}
const moveCardScript = cardEditorScript.slice(moveCardStart, moveCardEnd + 2).replace(/^export /, '');
const moveCardContext = vm.createContext({});
new vm.Script(`${moveCardScript}\nglobalThis.moveCardToTargetPosition = moveCardToTargetPosition;`).runInContext(moveCardContext);
for (const [sourceId, targetId, expected] of [
    ['a', 'b', 'b,a,c'],
    ['a', 'c', 'b,c,a'],
    ['c', 'a', 'c,a,b'],
    ['c', 'b', 'a,c,b']
]) {
    const cards = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    if (!moveCardContext.moveCardToTargetPosition(cards, sourceId, targetId)
        || cards.map((card) => card.id).join(',') !== expected) {
        throw new Error(`Dragging ${sourceId} onto ${targetId} did not occupy the target card position.`);
    }
}
for (const [sourceId, targetId] of [['a', 'a'], ['a', 'missing'], ['missing', 'b']]) {
    const cards = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    if (moveCardContext.moveCardToTargetPosition(cards, sourceId, targetId)
        || cards.map((card) => card.id).join(',') !== 'a,b,c') {
        throw new Error('Invalid or same-card reorder unexpectedly changed the draft order.');
    }
}
const cardDropStart = cardEditorScript.indexOf("            item.addEventListener('drop', (event) => {");
const cardDropEnd = cardEditorScript.indexOf("            item.addEventListener('dragend', finishDrag);", cardDropStart);
const cardDropScript = cardEditorScript.slice(cardDropStart, cardDropEnd);
if (!cardDropScript.includes('moveCardToTargetPosition(draftCards, sourceId, card.id)')
    || /clientX|getBoundingClientRect|\bbefore\b/.test(cardDropScript)) {
    throw new Error('Card drop handling must use the whole hovered card as its target position.');
}
const svgImportStart = cardEditorScript.indexOf('    async function importSvgFile() {');
const svgImportEnd = cardEditorScript.indexOf("\n\n    launchButton.addEventListener('click', openEditor);", svgImportStart);
const svgImportScript = cardEditorScript.slice(svgImportStart, svgImportEnd);
const promoteBlankStart = cardEditorScript.indexOf('    function promoteBlankIfComplete() {');
const promoteBlankEnd = cardEditorScript.indexOf('\n\n    function updateSelectedField', promoteBlankStart);
const promoteBlankScript = cardEditorScript.slice(promoteBlankStart, promoteBlankEnd);
const svgReadIndex = svgImportScript.indexOf('await file.text()');
const stripFocusSnapshotIndex = svgImportScript.indexOf('const restoreStripFocus = strip.contains(document.activeElement);');
const stripRenderIndex = svgImportScript.indexOf('renderStrip();', stripFocusSnapshotIndex);
const stripFocusRestoreIndex = svgImportScript.indexOf('if (restoreStripFocus) focusSelectedStripOption();', stripRenderIndex);
if (svgImportStart < 0
    || svgImportEnd < 0
    || promoteBlankStart < 0
    || promoteBlankEnd < 0
    || svgReadIndex < 0
    || stripFocusSnapshotIndex < svgReadIndex
    || stripRenderIndex < stripFocusSnapshotIndex
    || stripFocusRestoreIndex < stripRenderIndex) {
    throw new Error('Delayed SVG imports must restore focus only when the replaced strip owned focus at redraw time.');
}
const validationFeedbackStart = cardEditorScript.indexOf('function serverValidationFeedback(error) {');
const validationFeedbackEnd = cardEditorScript.indexOf('\n\nexport function initializeCardEditor', validationFeedbackStart);
const validationFeedbackScript = cardEditorScript.slice(validationFeedbackStart, validationFeedbackEnd);
const badRequestStart = cardEditorScript.indexOf('            if (error.status === 400) {');
const badRequestEnd = cardEditorScript.indexOf("            console.warn('保存卡片失败。'", badRequestStart);
const badRequestScript = cardEditorScript.slice(badRequestStart, badRequestEnd);
const badRequestEnableIndex = badRequestScript.indexOf('setBusy(false);');
const badRequestSelectIndex = badRequestScript.indexOf('selectCard(feedback.cardId);');
const badRequestStatusIndex = badRequestScript.indexOf("setStatus(feedback.message, 'error');");
const badRequestFocusIndex = badRequestScript.indexOf('focusValidationField(feedback.field);');
if (!validationFeedbackScript.includes('CARD_ID_PATTERN.test(error.payload.cardId)')
    || !validationFeedbackScript.includes('({ ...feedback, cardId })')
    || badRequestEnableIndex < 0
    || badRequestSelectIndex < badRequestEnableIndex
    || badRequestStatusIndex < badRequestSelectIndex
    || badRequestFocusIndex < badRequestStatusIndex) {
    throw new Error('PATCH validation errors must select a safe server-identified draft card before focusing its field.');
}
async function verifyBadRequestCardTarget(cardId, expectedSelectedId, expectedSelectCount) {
    let selectedId = 'card-b';
    let selectCount = 0;
    const events = [];
    const context = vm.createContext({
        CARD_ID_PATTERN: /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/,
        draftCards: [{ id: 'card-a' }, { id: 'card-b' }],
        setBusy: (value) => events.push(`busy:${value}`),
        selectCard: (id) => {
            selectCount += 1;
            selectedId = id;
            events.push(`select:${id}`);
        },
        setStatus: () => events.push('status'),
        focusValidationField: (field) => events.push(`focus:${selectedId}:${field}`)
    });
    const executableFeedback = validationFeedbackScript;
    const executableBadRequest = badRequestScript.replace(/^ {12}/gm, '    ');
    new vm.Script(
        `${executableFeedback}\nasync function handleBadRequest(error) {\n${executableBadRequest}\n}\n`
        + 'globalThis.handleBadRequest = handleBadRequest;'
    ).runInContext(context);
    await context.handleBadRequest({
        status: 400,
        payload: {
            error: 'effective service probe URL is not allowed by the configured probe policy',
            ...(cardId === undefined ? {} : { cardId })
        }
    });
    if (selectedId !== expectedSelectedId
        || selectCount !== expectedSelectCount
        || events[0] !== 'busy:false'
        || events.at(-1) !== `focus:${expectedSelectedId}:probeUrl`) {
        throw new Error(`PATCH cardId targeting regression: ${events.join(',')}.`);
    }
}
await verifyBadRequestCardTarget('card-a', 'card-a', 1);
await verifyBadRequestCardTarget('missing', 'card-b', 0);
await verifyBadRequestCardTarget('../card-a', 'card-b', 0);
await verifyBadRequestCardTarget(undefined, 'card-b', 0);

async function verifyDelayedSvgImportFocus(activeAtStart, activeAtRedraw, expectedFocusRestores) {
    let resolveRead;
    const delayedMarkup = new Promise((resolve) => { resolveRead = resolve; });
    const stripButton = { location: 'strip' };
    const formInput = { location: 'form' };
    const documentState = { activeElement: activeAtStart === 'strip' ? stripButton : formInput };
    const targetCard = { id: 'service-test', iconSvg: '', name: 'Test' };
    let focusRestores = 0;
    let renders = 0;
    const context = vm.createContext({
        BLANK_CARD_ID: '__new_card__',
        cardSvgLimits: { rawBytes: 64 * 1024 },
        iconInput: { files: [{ size: 16, text: () => delayedMarkup }], value: 'test.svg' },
        selectedId: targetCard.id,
        currentCard: () => targetCard,
        blankCard: {},
        draftCards: [targetCard],
        strip: { contains: (element) => element === stripButton },
        document: documentState,
        sanitizeSvgMarkup: () => '<svg><path /></svg>',
        iconSelect: { removeAttribute: () => {} },
        renderPreview: () => {},
        renderStrip: () => {
            renders += 1;
            if (documentState.activeElement === stripButton) documentState.activeElement = null;
        },
        computeDirty: () => {},
        setStatus: () => {},
        promoteBlankIfComplete: () => {},
        focusSelectedStripOption: () => {
            focusRestores += 1;
            documentState.activeElement = stripButton;
        },
        focusValidationField: () => {},
        console
    });
    const executableImport = svgImportScript.replace(/^    /gm, '');
    new vm.Script(`${executableImport}\nglobalThis.importSvgFile = importSvgFile;`).runInContext(context);
    const operation = context.importSvgFile();
    documentState.activeElement = activeAtRedraw === 'strip' ? stripButton : formInput;
    resolveRead('<svg><path /></svg>');
    await operation;
    if (renders !== 1 || focusRestores !== expectedFocusRestores) {
        throw new Error(`Delayed SVG focus regression: renders=${renders}, restores=${focusRestores}.`);
    }
    if (activeAtRedraw === 'form' && documentState.activeElement !== formInput) {
        throw new Error('Delayed SVG import stole focus from a form field.');
    }
}
await verifyDelayedSvgImportFocus('strip', 'form', 0);
await verifyDelayedSvgImportFocus('form', 'strip', 1);

async function verifyBlankPromotionRestoresThePromotedCard() {
    let resolveRead;
    const delayedMarkup = new Promise((resolve) => { resolveRead = resolve; });
    const stripButton = { location: 'strip' };
    const documentState = { activeElement: stripButton };
    const originalBlank = {
        id: null,
        iconSvg: '',
        name: 'New card',
        description: 'Description',
        url: 'https://new.home.example.test/',
        probeUrl: ''
    };
    let renders = 0;
    let focusedId = null;
    const runtime = {
        BLANK_CARD_ID: '__new_card__',
        MAX_CARDS: 32,
        cardSvgLimits: { rawBytes: 64 * 1024 },
        iconInput: { files: [{ size: 16, text: () => delayedMarkup }], value: 'new.svg' },
        selectedId: '__new_card__',
        currentCard: () => runtime.blankCard,
        blankCard: originalBlank,
        draftCards: [],
        strip: { contains: (element) => element === stripButton },
        document: documentState,
        sanitizeSvgMarkup: () => '<svg><path /></svg>',
        iconSelect: { removeAttribute: () => {} },
        renderPreview: () => {},
        renderStrip: () => {
            renders += 1;
            if (documentState.activeElement === stripButton) documentState.activeElement = null;
        },
        renderSelectionFields: () => {},
        computeDirty: () => {},
        setStatus: () => {},
        normalizeDraftCard: (card) => ({ ...card }),
        generateCardId: () => 'card-promoted',
        makeBlankCard: () => ({ id: null, iconSvg: '', name: '', description: '', url: '', probeUrl: '' }),
        channel: 'services',
        focusSelectedStripOption: () => { focusedId = runtime.selectedId; },
        focusValidationField: () => {},
        console
    };
    const context = vm.createContext(runtime);
    const executablePromote = promoteBlankScript.replace(/^    /gm, '');
    const executableImport = svgImportScript.replace(/^    /gm, '');
    new vm.Script(
        `${executablePromote}\n${executableImport}\n`
        + 'globalThis.importSvgFile = importSvgFile;'
    ).runInContext(context);
    const operation = context.importSvgFile();
    resolveRead('<svg><path /></svg>');
    await operation;
    if (renders !== 2
        || context.draftCards.map((card) => card.id).join(',') !== 'card-promoted'
        || context.selectedId !== 'card-promoted'
        || focusedId !== 'card-promoted') {
        throw new Error('Blank-card SVG promotion did not restore focus to the promoted card.');
    }
}
await verifyBlankPromotionRestoresThePromotedCard();

function verifyCardCacheCase(raw, normalizeCardsResponse, expectedRemoval, expectedResult) {
    let stored = raw;
    let removals = 0;
    const context = vm.createContext({
        CARD_CACHE_KEY: 'test-card-cache',
        MAX_CACHE_BYTES: 1024,
        utf8ByteLength: (value) => Buffer.byteLength(value, 'utf8'),
        normalizeCardsResponse,
        localStorage: {
            getItem: () => stored,
            removeItem: () => {
                removals += 1;
                stored = null;
            }
        }
    });
    const executableCache = readCacheScript.replace(/^    /gm, '');
    new vm.Script(`${executableCache}\nglobalThis.readCache = readCache;`).runInContext(context);
    const result = context.readCache();
    if (removals !== expectedRemoval || result !== expectedResult) {
        throw new Error(`Invalid cache cleanup regression: removals=${removals}.`);
    }
}
verifyCardCacheCase('{', () => { throw new Error('unreachable'); }, 1, null);
verifyCardCacheCase('', () => { throw new Error('unreachable'); }, 1, null);
verifyCardCacheCase(null, () => { throw new Error('unreachable'); }, 0, null);
verifyCardCacheCase('{}', () => { throw new Error('invalid SVG or structure'); }, 1, null);
verifyCardCacheCase('{}', () => ({ initialized: false }), 1, null);
const validCachedCards = { initialized: true };
verifyCardCacheCase('{}', () => validCachedCards, 0, validCachedCards);
if (!cardSvgScript.includes("new DOMParser().parseFromString(svgMarkup, 'image/svg+xml')")
    || !cardSvgScript.includes('document.createElementNS(SVG_NAMESPACE')
    || !cardSvgScript.includes('/<!DOCTYPE|<!ENTITY/i')
    || !cardSvgScript.includes('MAX_SVG_ELEMENTS = 256')
    || !cardSvgScript.includes('MAX_SVG_DEPTH = 16')
    || !cardSvgScript.includes('MAX_CLEAN_SVG_BYTES = 24 * 1024')) {
    throw new Error('Frontend SVG handling lost its parser, namespace reconstruction, declaration rejection, or resource budgets.');
}
const svgAllowedElementsStart = cardSvgScript.indexOf('const ALLOWED_ELEMENTS = new Set([');
const svgAllowedElementsEnd = cardSvgScript.indexOf('\n]);', svgAllowedElementsStart);
const svgAllowedElementsScript = cardSvgScript.slice(svgAllowedElementsStart, svgAllowedElementsEnd);
const svgInputHelpersStart = cardSvgScript.indexOf('function isXmlnsDeclaration(attribute) {');
const svgTitleHelpersStart = cardSvgScript.indexOf('function validateTitleText(value) {');
const svgCollectStart = cardSvgScript.indexOf('function collectIdsAndCheckShape(root) {');
const svgValueValidationStart = cardSvgScript.indexOf('function validateAttributeValue(name, value, ids) {');
const svgCopyStart = cardSvgScript.indexOf('function copyElement(source, ids, depth = 1) {');
const svgSanitizeStart = cardSvgScript.indexOf('function sanitizeToElement(svgMarkup) {');
const svgTitleHelpersScript = cardSvgScript.slice(svgInputHelpersStart, svgCollectStart);
const svgCollectScript = cardSvgScript.slice(svgCollectStart, svgCopyStart);
const svgValueValidationScript = cardSvgScript.slice(svgValueValidationStart, svgCopyStart);
const svgCopyScript = cardSvgScript.slice(svgCopyStart, svgSanitizeStart);
const svgElementBudgetIndex = svgCollectScript.indexOf('elementCount += 1;');
const svgDepthBudgetIndex = svgCollectScript.indexOf('if (depth > MAX_SVG_DEPTH)');
const svgTitleBranchIndex = svgCollectScript.indexOf("if (element.localName === 'title') {");
const svgNormalizeAttributeIndex = svgCopyScript.indexOf('const normalized = normalizeInputAttribute(attribute);');
const svgStyleDropIndex = svgCopyScript.indexOf("if (attribute.localName === 'style') return;");
if (!cardSvgScript.includes('const MAX_SVG_TITLE_BYTES = 256;')
    || !cardSvgScript.includes("const XLINK_NAMESPACE = 'http://www.w3.org/1999/xlink';")
    || !cardSvgScript.includes("const XMLNS_NAMESPACE = 'http://www.w3.org/2000/xmlns/';")
    || svgAllowedElementsStart < 0
    || svgAllowedElementsEnd < 0
    || /['"](?:title|style)['"]/.test(svgAllowedElementsScript)
    || svgInputHelpersStart < 0
    || svgTitleHelpersStart < 0
    || svgCollectStart < 0
    || svgValueValidationStart < 0
    || svgCopyStart < 0
    || svgSanitizeStart < 0
    || !svgTitleHelpersScript.includes('attribute.namespaceURI === XMLNS_NAMESPACE')
    || !svgTitleHelpersScript.includes("attribute.name === 'xmlns'")
    || !svgTitleHelpersScript.includes("attribute.namespaceURI !== XLINK_NAMESPACE || attribute.localName !== 'href'")
    || !svgTitleHelpersScript.includes("return { name: 'href', outputName: 'href' };")
    || !svgTitleHelpersScript.includes('element.parentElement !== root')
    || !svgTitleHelpersScript.includes('!isXmlnsDeclaration(attribute)')
    || !svgTitleHelpersScript.includes('element.children.length !== 0')
    || !svgTitleHelpersScript.includes('Node.CDATA_SECTION_NODE')
    || !svgTitleHelpersScript.includes('Node.COMMENT_NODE')
    || !svgTitleHelpersScript.includes('!supportedNodeTypes.has(child.nodeType)')
    || !svgTitleHelpersScript.includes("validateTitleText(element.textContent || '')")
    || !svgCollectScript.includes('let titleCount = 0;')
    || !svgCollectScript.includes('titleCount > 1')
    || svgElementBudgetIndex < 0
    || svgDepthBudgetIndex < svgElementBudgetIndex
    || svgTitleBranchIndex < svgDepthBudgetIndex
    || svgNormalizeAttributeIndex < 0
    || svgStyleDropIndex < svgNormalizeAttributeIndex
    || !svgCopyScript.includes('if (normalized === null) return;')
    || !svgCopyScript.includes('document.createElementNS(SVG_NAMESPACE, source.localName)')
    || !svgCopyScript.includes('ATTRIBUTE_CASE.get(name) || outputName')
    || !svgCopyScript.includes("if (name === 'class' || name.startsWith('on'))")
    || !svgCopyScript.includes("if (child.localName === 'title') return;")) {
    throw new Error('Frontend SVG namespace/title/style compatibility lost its strict validation or canonical dropping contract.');
}

const svgTitleContext = vm.createContext({
    XLINK_NAMESPACE: 'http://www.w3.org/1999/xlink',
    XMLNS_NAMESPACE: 'http://www.w3.org/2000/xmlns/',
    MAX_SVG_TITLE_BYTES: 256,
    CONTROL_CHARACTER_PATTERN: /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/,
    utf8ByteLength: (value) => Buffer.byteLength(value, 'utf8'),
    svgError: (message) => {
        const error = new Error(message);
        error.name = 'CardSvgError';
        return error;
    },
    Node: { TEXT_NODE: 3, CDATA_SECTION_NODE: 4, COMMENT_NODE: 8 }
});
new vm.Script(
    `${svgTitleHelpersScript}\n`
    + 'globalThis.isXmlnsDeclaration = isXmlnsDeclaration;\n'
    + 'globalThis.normalizeInputAttribute = normalizeInputAttribute;\n'
    + 'globalThis.validateTitleText = validateTitleText;\n'
    + 'globalThis.validateTitleElement = validateTitleElement;'
).runInContext(svgTitleContext);
const titleRoot = {};
function svgTitleElement(text, overrides = {}) {
    return {
        parentElement: titleRoot,
        attributes: [],
        children: { length: 0 },
        childNodes: [{ nodeType: 3 }],
        textContent: text,
        ...overrides
    };
}
function expectInvalidSvgTitle(element, label) {
    let rejected = false;
    try {
        svgTitleContext.validateTitleElement(element, titleRoot);
    } catch (error) {
        rejected = error?.name === 'CardSvgError';
    }
    if (!rejected) throw new Error(`Invalid SVG title was accepted: ${label}.`);
}
svgTitleContext.validateTitleElement(svgTitleElement('Claude'), titleRoot);
svgTitleContext.validateTitleElement(svgTitleElement('a'.repeat(256)), titleRoot);
svgTitleContext.validateTitleElement(svgTitleElement('\u754c'.repeat(85)), titleRoot);
svgTitleContext.validateTitleElement(svgTitleElement('Claude', {
    attributes: [{
        namespaceURI: 'http://www.w3.org/2000/xmlns/',
        name: 'xmlns:svg',
        localName: 'svg'
    }]
}), titleRoot);
expectInvalidSvgTitle(svgTitleElement('   '), 'blank text');
expectInvalidSvgTitle(svgTitleElement('bad\u0001title'), 'control character');
expectInvalidSvgTitle(svgTitleElement('a'.repeat(257)), '257 ASCII bytes');
expectInvalidSvgTitle(svgTitleElement('\u754c'.repeat(86)), '258 UTF-8 bytes');
expectInvalidSvgTitle(svgTitleElement('Claude', { parentElement: {} }), 'nested title');
expectInvalidSvgTitle(svgTitleElement('Claude', {
    attributes: [{ namespaceURI: null, name: 'lang', localName: 'lang' }]
}), 'title attribute');
expectInvalidSvgTitle(svgTitleElement('Claude', { children: { length: 1 }, childNodes: [{ nodeType: 1 }] }), 'title element child');
for (const nodeType of [2, 5, 7]) {
    expectInvalidSvgTitle(svgTitleElement('Claude', { childNodes: [{ nodeType }] }), `non-text node ${nodeType}`);
}
svgTitleContext.validateTitleElement(svgTitleElement('Claude', { childNodes: [{ nodeType: 4 }] }), titleRoot);
svgTitleContext.validateTitleElement(svgTitleElement('Claude', { childNodes: [{ nodeType: 8 }, { nodeType: 3 }] }), titleRoot);

function svgInputAttribute(namespaceURI, name, localName, value) {
    return { namespaceURI, name, localName, value };
}
for (const declaration of [
    svgInputAttribute('http://www.w3.org/2000/xmlns/', 'xmlns', 'xmlns', 'http://www.w3.org/2000/svg'),
    svgInputAttribute('http://www.w3.org/2000/xmlns/', 'xmlns:xlink', 'xlink', 'http://www.w3.org/1999/xlink'),
    svgInputAttribute('http://www.w3.org/2000/xmlns/', 'xmlns:svg', 'svg', 'http://www.w3.org/2000/svg')
]) {
    if (svgTitleContext.normalizeInputAttribute(declaration) !== null) {
        throw new Error(`Frontend SVG retained input-only namespace declaration ${declaration.name}.`);
    }
}
const normalizedXlinkHref = svgTitleContext.normalizeInputAttribute(
    svgInputAttribute('http://www.w3.org/1999/xlink', 'xlink:href', 'href', '#base')
);
if (normalizedXlinkHref.name !== 'href' || normalizedXlinkHref.outputName !== 'href') {
    throw new Error('Frontend SVG did not normalize XLink href to a plain href attribute.');
}
function expectInvalidSvgNamespace(attribute, label) {
    let rejected = false;
    try {
        svgTitleContext.normalizeInputAttribute(attribute);
    } catch (error) {
        rejected = error?.name === 'CardSvgError';
    }
    if (!rejected) throw new Error(`Invalid SVG namespace attribute was accepted: ${label}.`);
}
expectInvalidSvgNamespace(
    svgInputAttribute('http://www.w3.org/1999/xlink', 'xlink:style', 'style', 'fill:red'),
    'xlink:style'
);
expectInvalidSvgNamespace(
    svgInputAttribute('https://evil.example/ns', 'unsafe:href', 'href', '#base'),
    'non-XLink href namespace'
);

const copiedSvgElements = [];
const svgCopyContext = vm.createContext({
    SVG_NAMESPACE: 'http://www.w3.org/2000/svg',
    XLINK_NAMESPACE: 'http://www.w3.org/1999/xlink',
    XMLNS_NAMESPACE: 'http://www.w3.org/2000/xmlns/',
    GLOBAL_ATTRIBUTES: new Set(),
    ELEMENT_ATTRIBUTES: new Map([
        ['lineargradient', new Set(['href'])],
        ['radialgradient', new Set(['href'])],
        ['path', new Set(['href'])]
    ]),
    ATTRIBUTE_CASE: new Map(),
    CONTROL_CHARACTER_PATTERN: /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/,
    SAFE_ID_PATTERN: /^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$/,
    LOCAL_REFERENCE_ATTRIBUTES: new Set(['fill', 'stroke', 'clip-path', 'mask']),
    LOCAL_URL_PATTERN: /^url\(\s*#([A-Za-z_][A-Za-z0-9_.:-]{0,127})\s*\)$/i,
    COLOR_ATTRIBUTES: new Set(['fill', 'stroke', 'color', 'stop-color']),
    SAFE_COLOR_PATTERN: /^(?:none|currentColor|transparent|inherit|[A-Za-z]+|#[0-9A-Fa-f]{3,8}|(?:rgb|rgba|hsl|hsla)\([0-9eE+.,%\s-]+\))$/,
    svgError: (message) => {
        const error = new Error(message);
        error.name = 'CardSvgError';
        return error;
    },
    Node: { ELEMENT_NODE: 1, TEXT_NODE: 3, COMMENT_NODE: 8 },
    document: {
        createElementNS: (namespaceURI, localName) => {
            const element = {
                namespaceURI,
                localName,
                attributes: [],
                children: [],
                setAttribute(name, value) { this.attributes.push({ name, value }); },
                append(child) { this.children.push(child); }
            };
            copiedSvgElements.push(element);
            return element;
        }
    }
});
new vm.Script(
    `${svgTitleHelpersScript}\n${svgValueValidationScript}\n${svgCopyScript}\n`
    + 'globalThis.copyElement = copyElement;'
).runInContext(svgCopyContext);
const xlinkGradientSource = {
    localName: 'linearGradient',
    nodeName: 'svg:linearGradient',
    attributes: [
        svgInputAttribute('http://www.w3.org/2000/xmlns/', 'xmlns:svg', 'svg', 'http://www.w3.org/2000/svg'),
        svgInputAttribute('http://www.w3.org/2000/xmlns/', 'xmlns:xlink', 'xlink', 'http://www.w3.org/1999/xlink'),
        svgInputAttribute(null, 'style', 'style', 'fill:url(https://evil.example/paint.svg)'),
        svgInputAttribute('http://www.w3.org/1999/xlink', 'xlink:href', 'href', '#base')
    ],
    childNodes: []
};
const copiedGradient = svgCopyContext.copyElement(xlinkGradientSource, new Set(['base']));
if (copiedGradient.namespaceURI !== 'http://www.w3.org/2000/svg'
    || copiedGradient.localName !== 'linearGradient'
    || copiedGradient.attributes.length !== 1
    || copiedGradient.attributes[0].name !== 'href'
    || copiedGradient.attributes[0].value !== '#base') {
    throw new Error(`Frontend SVG XLink canonicalization regression: ${JSON.stringify(copiedGradient)}.`);
}
function expectSvgCopyRejected(attribute, localName, label) {
    let rejected = false;
    try {
        svgCopyContext.copyElement({ localName, attributes: [attribute], childNodes: [] }, new Set(['base']));
    } catch (error) {
        rejected = error?.name === 'CardSvgError';
    }
    if (!rejected) throw new Error(`Unsafe SVG copy input was accepted: ${label}.`);
}
expectSvgCopyRejected(
    svgInputAttribute('http://www.w3.org/1999/xlink', 'xlink:href', 'href', 'https://evil.example/gradient.svg'),
    'linearGradient',
    'external XLink href'
);
expectSvgCopyRejected(
    svgInputAttribute('http://www.w3.org/1999/xlink', 'xlink:href', 'href', '#missing'),
    'linearGradient',
    'missing local XLink href target'
);
expectSvgCopyRejected(
    svgInputAttribute('http://www.w3.org/1999/xlink', 'xlink:href', 'href', '#base'),
    'path',
    'XLink href outside a gradient'
);
if (!serviceScript.includes('let requestGeneration = 0;')
    || !serviceScript.includes('if (activeController) activeController.abort();')
    || !serviceScript.includes('if (requestCards.length === 0)')
    || !serviceScript.includes('if (generation !== requestGeneration) return;')
    || !channelScript.includes('function resyncCards()')
    || !channelScript.includes("quickLinksGrid.style.removeProperty('--quick-links-width')")) {
    throw new Error('Dynamic card replacement must invalidate probes and resynchronize empty-channel layout.');
}
const settingsInitialization = appScript.indexOf("initializeFeature('网页设置菜单', initializePageSettings)");
const faviconInitialization = appScript.indexOf("initializeFeature('网站图标'");
const managerInitialization = appScript.indexOf('await initializeCardManager({');
const channelInitialization = appScript.indexOf("initializeFeature('双频道预览', initializeChannelSwitch)");
const statusInitialization = appScript.indexOf("initializeFeature('服务状态', initializeServiceStatus)");
if (settingsInitialization < 0
    || faviconInitialization < settingsInitialization
    || managerInitialization < faviconInitialization
    || channelInitialization < managerInitialization
    || statusInitialization < managerInitialization
    || !appScript.includes('cardManager.subscribe(() => {')
    || !appScript.includes('editorRestoreFocus: restorePageSettingsFocus')
    || !appScript.includes('restoreFocus: restorePageSettingsFocus')) {
    throw new Error('Card data must settle before channel/status controllers and notify both controllers after replacement.');
}
const channelSwitchTag = html.match(/<button\b[^>]*\bid="channel-switch"[^>]*>/);
if (!channelSwitchTag
    || !/\bdisabled(?:\s|>)/.test(channelSwitchTag[0])
    || !/\baria-pressed="false"/.test(channelSwitchTag[0])
    || !/\baria-controls="services-panel quick-links-panel"/.test(channelSwitchTag[0])) {
    throw new Error('The static channel switch must default to a disabled Services-safe state.');
}
for (const requiredId of [
    'channel-switch-anchor',
    'channel-switch-face-front',
    'channel-switch-face-back',
    'channel-switch-measure-services',
    'channel-switch-measure-quick-links',
    'channel-panel-rotor'
]) {
    if (!html.includes(`id="${requiredId}"`)) {
        throw new Error(`Missing Stage 2 motion structure: ${requiredId}.`);
    }
}
const sharedLeftFlipAnimations = html.match(/animation:\s*channel-flip-left 800ms/g) || [];
if (!/@keyframes channel-flip-left\s*\{[^}]*rotateY\(0deg\)[\s\S]*?rotateY\(-180deg\)/.test(html)
    || sharedLeftFlipAnimations.length !== 2
    || !/animation:\s*channel-flip-left 800ms[^;]*both;/.test(html)
    || !/animation:\s*channel-flip-left 800ms[^;]*200ms both;/.test(html)
    || !/@keyframes channel-switch-expand\s*\{[\s\S]*?40%,[\s\S]*?--channel-switch-quick-links-width/.test(html)
    || !/@keyframes channel-switch-contract\s*\{[\s\S]*?40%[^}]*--channel-switch-quick-links-width[\s\S]*?60%[^}]*--channel-switch-services-width/.test(html)
    || !/const channelSwitchFallbackMs = 1200;/.test(html)
    || !/\.channel-switch-face\s*\{[^}]*white-space:\s*nowrap;/.test(html)
    || !/\.channel-motion-ready\.is-channel-resetting \.channel-switch[\s\S]*?animation:\s*none !important;[\s\S]*?transition:\s*none !important;/.test(html)) {
    throw new Error('Stage 2R.2 must share one 800ms left flip, delay the panel by 200ms, contract at 40%-60%, and reset atomically.');
}
if (!/#service-count-label,[\s\S]*?\.dashboard-footer-brand,[\s\S]*?#last-checked\s*\{[^}]*transition:\s*opacity 180ms ease;/.test(html)
    || !/#service-count-label\.is-channel-copy-exiting,[\s\S]*?#last-checked\.is-channel-copy-exiting\s*\{[^}]*opacity:\s*0;/.test(html)
    || !/#service-count-label\.is-channel-copy-entering,[\s\S]*?#last-checked\.is-channel-copy-entering\s*\{[^}]*transition-duration:\s*240ms;/.test(html)
    || !/function transitionChannelCopy\(note, channel\)/.test(html)
    || !/function revealServicesFooterCopy\(\)/.test(html)) {
    throw new Error('Stage 2R.2 must cross-fade the Hero count and both footer side labels at 180ms/240ms.');
}
if (!/\.refresh-button\s*\{[^}]*--refresh-button-rest-opacity:\s*1;[^}]*--refresh-button-opacity-duration:\s*180ms;[^}]*opacity:\s*var\(--refresh-button-rest-opacity\);[^}]*transition:\s*opacity var\(--refresh-button-opacity-duration\) ease,/.test(html)
    || !/\.refresh-button:disabled\s*\{[^}]*--refresh-button-rest-opacity:\s*0\.72;/.test(html)
    || !/\.refresh-button\.is-channel-copy-exiting,[\s\S]*?\.refresh-button\.is-channel-copy-entering\s*\{[^}]*pointer-events:\s*none;/.test(html)
    || !/\.refresh-button\.is-channel-copy-exiting\s*\{[^}]*opacity:\s*0;/.test(html)
    || !/\.refresh-button\.is-channel-copy-entering\s*\{[^}]*--refresh-button-opacity-duration:\s*240ms;/.test(html)
    || !/refreshButton\.toggleAttribute\('inert', true\);/.test(html)
    || !/refreshButton\.toggleAttribute\('inert', showQuickLinks\);/.test(html)
    || !/function revealServicesFooterCopy\(\)\s*\{[\s\S]*?window\.getComputedStyle\(element\)\.opacity;[\s\S]*?window\.requestAnimationFrame/.test(html)) {
    throw new Error('The refresh button must share the 180ms/240ms channel fade while preserving its disabled opacity and interaction isolation.');
}
if (!/\.refresh-button\[hidden\]\s*\{[^}]*display:\s*none;/.test(html)) {
    throw new Error('Quick Links must visibly hide the refresh button even when its component display rule is active.');
}
if (!/function finishIntro\(\)\s*\{[\s\S]*?root\.classList\.remove\('intro-first', 'intro-play'\);/.test(html)
    || !/event\.animationName === 'copy-reveal'/.test(html)
    || !/introCleanupTimer = window\.setTimeout\(finishIntro, 1500\);/.test(html)) {
    throw new Error('Stage 2R.2 must remove the first-intro trigger classes after the initial page reveal.');
}
const expectedQuickLinkNotes = [
    '\u5927\u5bb6\u4e00\u8d77KIRAKIRA',
    ' \u2606 ',
    'DOKIDOKI \uff01',
    '\u30e2\u30ab\u3061\u3083\u3093\u3084\u3063\u3061\u3083\u3046\u3088\uff5e',
    '\u307e\u3093\u307e\u308b\u304a\u5c71\u306b\u5f69\u308a\u3092!',
    '\u4e38\u5c71\u5f69',
    '\u3067\u30fc\u3059!',
    'POPIPA! ',
    'PIPOPA!',
    ' POPIPAPAPIPOPA!',
    'Happy! Lucky! Smile! Yeah!!',
    '\u3042\u306a\u305f\u9054\u3001',
    'Roselia',
    '\u306b\u3059\u3079\u3066\u3092\u8ced\u3051\u308b\u899a\u609f\u306f\u3042\u308b?',
    '\u{1f91c}\u{1f62f}\u{1f446}\u3068\u270b\u{1f603}\u{1f44c}\u304c\u{1f590}\ufe0f\u{1f609}\u{1f590}\ufe0f\u308f\u{1f603}\u{1f450}\u30b0\u30eb\u30fc\u30d7\u2661',
    '\u306a\u3093\u3067\u300c',
    '\u6625\u65e5\u5f71',
    '\u300d\u3084\u3063\u305f\u306e\uff1f\uff01\uff01',
    '\u5922\u306f\u3001\u9650\u308a\u306a\u304f\u5927\u304d\u304f\uff01'
];
expectedQuickLinkNotes.forEach((note) => {
    if (!html.includes(note)) throw new Error(`Missing Stage 2R Quick Links note fragment: ${note}`);
});
if (!/href:\s*'https:\/\/www\.bilibili\.com\/video\/BV1H29SBCEvF'/.test(html)
    || !/href:\s*'https:\/\/www\.bilibili\.com\/video\/BV14z42167Uj'/.test(html)
    || !/link\.target = '_blank';/.test(html)
    || !/link\.rel = 'noopener noreferrer';/.test(html)
    || !/const channelNoteExitMs = 180;/.test(html)
    || !/const channelNoteEnterMs = 240;/.test(html)) {
    throw new Error('Stage 2R note rotation is missing its safe Haruhikage link or 180ms/240ms cross-fade.');
}
const servicesPanelTag = html.match(/<div\b[^>]*\bid="services-panel"[^>]*>/);
const quickLinksPanelTag = html.match(/<div\b[^>]*\bid="quick-links-panel"[^>]*>/);
if (!servicesPanelTag
    || !/\baria-hidden="false"/.test(servicesPanelTag[0])
    || !quickLinksPanelTag
    || !/\baria-hidden="true"/.test(quickLinksPanelTag[0])
    || !/\binert(?:\s|>)/.test(quickLinksPanelTag[0])
    || !/\bhidden(?:\s|>)/.test(quickLinksPanelTag[0])) {
    throw new Error('Stage 3 static fallback must expose Services and isolate the hidden Quick Links panel.');
}
if (!/@keyframes channel-mobile-button-exit\s*\{[\s\S]*?opacity:\s*0;[\s\S]*?translateY\(-4px\)/.test(html)
    || !/@keyframes channel-mobile-button-enter\s*\{[\s\S]*?opacity:\s*0;[\s\S]*?translateY\(4px\)[\s\S]*?opacity:\s*1;/.test(html)
    || !/@keyframes channel-mobile-panel-exit\s*\{[\s\S]*?opacity:\s*0;[\s\S]*?translateY\(-6px\)/.test(html)
    || !/@keyframes channel-mobile-card-enter\s*\{[\s\S]*?opacity:\s*0;[\s\S]*?translateY\(10px\)[\s\S]*?opacity:\s*1;/.test(html)
    || !/channel-mobile-button-exit 160ms ease both/.test(html)
    || !/channel-mobile-button-enter 240ms ease both/.test(html)
    || !/channel-mobile-panel-exit 180ms ease both/.test(html)
    || !/channel-mobile-card-enter 280ms[\s\S]*?var\(--channel-card-index, 0\) \* 25ms/.test(html)
    || !/const channelMobilePanelDelayMs = 120;/.test(html)
    || !/const channelMobileCardStaggerMs = 25;/.test(html)) {
    throw new Error('Stage 3 mobile motion must fade the button first, delay the grid by 120ms, and stagger cards by 25ms.');
}
if (!/matchMedia\('\(max-width: 767px\) and \(prefers-reduced-motion: no-preference\)'\)/.test(html)
    || !/function setChannelPanelInteractive\(panel, interactive\)[\s\S]*?toggleAttribute\('inert', !interactive\)/.test(html)
    || !/channelSwitch\.focus\(\{ preventScroll: true \}\)/.test(html)
    || !/function beginMobileChannelSwitch\(channel\)/.test(html)
    || !/function finishMobileChannelSwitch\(\)/.test(html)) {
    throw new Error('Stage 3 mobile motion is missing reduced-motion routing, panel isolation, or focus retention.');
}
if (!/\.channel-motion-ready \.channel-panel-stage\s*\{[^}]*height:\s*var\(--channel-panel-height, var\(--services-panel-height, auto\)\);/.test(html)
    || !/\.quick-links-grid\s*\{[^}]*width:\s*min\(100%, var\(--quick-links-width\)\);[^}]*justify-content:\s*center;/.test(html)
    || !/@media \(min-width: 981px\)\s*\{[\s\S]*?\.quick-links-panel:not\(\[hidden\]\)\s*\{[^}]*min-height:\s*var\(--services-panel-height, auto\);/.test(html)
    || !/@media \(max-width: 767px\)\s*\{[\s\S]*?\.service-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\);/.test(html)) {
    throw new Error('Final channel layout must keep the wide panel fixed and centered while preserving a natural narrow-screen grid.');
}
if (!/\.quick-link-name\s*\{[^}]*font-size:\s*1\.04rem;[^}]*font-weight:\s*700;[^}]*text-align:\s*center;/.test(html)) {
    throw new Error('Quick Link names must be enlarged and centered within the space to the right of the icon.');
}

const originalLinkMatch = html.match(/<a\b[^>]*\bid="background-original-link"[^>]*>/);
if (!originalLinkMatch) throw new Error('Missing the original-background link.');
const originalLinkTag = originalLinkMatch[0];
if (!/\btarget="_blank"/.test(originalLinkTag)) {
    throw new Error('The original-background link does not open in a new tab.');
}
const originalLinkRel = originalLinkTag.match(/\brel="([^"]+)"/);
const originalLinkRelTokens = originalLinkRel ? originalLinkRel[1].split(/\s+/) : [];
if (!originalLinkRelTokens.includes('noopener') || !originalLinkRelTokens.includes('noreferrer')) {
    throw new Error('The original-background link is missing safe rel tokens.');
}
if (/\bdownload(?:\s|=|>)/.test(originalLinkTag) || /\bhref=/.test(originalLinkTag)) {
    throw new Error('The original-background link must render in-browser and receive its validated href from JavaScript.');
}
const originalLinkIndex = html.indexOf('id="background-original-link"');
const bingDailyButtonIndex = html.indexOf('id="background-bing-daily-button"');
const bestdoriDrawButtonIndex = html.indexOf('id="background-draw-button"');
if (originalLinkIndex < 0
    || bingDailyButtonIndex < 0
    || bestdoriDrawButtonIndex < 0
    || !(originalLinkIndex < bingDailyButtonIndex && bingDailyButtonIndex < bestdoriDrawButtonIndex)) {
    throw new Error('The Bing daily button must remain between the original link and Bestdori draw button.');
}

const searchFormMatch = html.match(/<form\b[^>]*\bid="homepage-search-form"[^>]*>/);
if (!searchFormMatch) throw new Error('Missing the progressive-enhancement search form.');
const searchFormTag = searchFormMatch[0];
if (!/\baction="https:\/\/www\.bing\.com\/search"/.test(searchFormTag)
    || !/\bmethod="get"/i.test(searchFormTag)
    || !/\btarget="_blank"/.test(searchFormTag)) {
    throw new Error('The static search form must remain a native Bing GET form that opens a new tab.');
}
const searchFormRel = searchFormTag.match(/\brel="([^"]+)"/);
const searchFormRelTokens = searchFormRel ? searchFormRel[1].split(/\s+/) : [];
if (!searchFormRelTokens.includes('noopener') || !searchFormRelTokens.includes('noreferrer')) {
    throw new Error('The search form is missing safe rel tokens.');
}
if (/\bdata-search-enhanced=/.test(searchFormTag)) {
    throw new Error('The search form must not claim JavaScript enhancement before runtime initialization succeeds.');
}

const searchInputMatch = html.match(/<input\b[^>]*\bid="homepage-search-input"[^>]*>/);
const defaultSearchPlaceholder = '\u628a\u95ee\u9898\u4e22\u7ed9\u4e92\u8054\u7f51';
if (!searchInputMatch
    || !searchInputMatch[0].includes('type="search"')
    || !searchInputMatch[0].includes('name="q"')
    || !searchInputMatch[0].includes(`placeholder="${defaultSearchPlaceholder}"`)
    || !searchInputMatch[0].includes('pattern=".*\\S.*"')
    || !searchInputMatch[0].includes(' required')
    || searchInputMatch[0].includes('aria-keyshortcuts=')) {
    const inputTag = searchInputMatch ? searchInputMatch[0] : '';
    throw new Error(`The static Bing search input contract is incomplete: ${JSON.stringify([
        inputTag.includes('type="search"'),
        inputTag.includes('name="q"'),
        inputTag.includes(`placeholder="${defaultSearchPlaceholder}"`),
        inputTag.includes('pattern=".*\\S.*"'),
        inputTag.includes(' required'),
        !inputTag.includes('aria-keyshortcuts=')
    ])} ${inputTag || 'missing'}`);
}
for (const [id, expectedType] of [
    ['search-engine-button', 'button'],
    ['search-submit-button', 'submit']
]) {
    const button = html.match(new RegExp(`<button\\b[^>]*\\bid="${id}"[^>]*>`));
    if (!button || !new RegExp(`\\btype="${expectedType}"`).test(button[0])) {
        throw new Error(`${id} has the wrong button type.`);
    }
}
const searchEngineButtonMatch = html.match(/<button\b[^>]*\bid="search-engine-button"[^>]*>/);
const collapsedSearchAriaLabel = '\u4f7f\u7528 Bing \u641c\u7d22\uff0c\u70b9\u51fb\u6216\u6309 Alt \u52a0\u6ce2\u6d6a\u53f7\u952e\u5c55\u5f00\u641c\u7d22\u6846';
if (!searchEngineButtonMatch
    || !searchEngineButtonMatch[0].includes('aria-keyshortcuts="Alt+&#96;"')
    || !searchEngineButtonMatch[0].includes(`aria-label="${collapsedSearchAriaLabel}"`)) {
    throw new Error('The search engine pill is missing its Alt+Backquote shortcut accessibility contract.');
}
const searchShortcutKeycaps = [...html.matchAll(/<kbd\b[^>]*\bclass="search-shortcut-keycap"[^>]*>Alt \+ ~<\/kbd>/g)];
if (searchShortcutKeycaps.length !== 1
    || !/\baria-hidden="true"/.test(searchShortcutKeycaps[0][0])
    || !/<span\b[^>]*\bclass="search-engine-identity"[^>]*>[\s\S]*?search-engine-current-icon[\s\S]*?id="search-engine-name"[\s\S]*?<\/span>\s*<kbd\b[^>]*\bclass="search-shortcut-keycap"/.test(html)) {
    throw new Error('The collapsed search pill must center one decorative keycap beside its wrapped icon and name.');
}
const searchOptionIds = [...html.matchAll(/\bdata-search-engine="([^"]+)"/g)].map((match) => match[1]);
if (JSON.stringify(searchOptionIds) !== JSON.stringify(['bing', 'google', 'duckduckgo', 'baidu', 'bilibili'])) {
    throw new Error(`Search engine option order mismatch: ${searchOptionIds.join(', ')}`);
}
if (html.includes('search-engine-option-note')
    || html.includes('默认 · 国内可用')
    || html.includes('国际网络')
    || html.includes('隐私搜索')
    || html.includes('国内备用')) {
    throw new Error('Search engine options must contain only the icon and engine name.');
}
if (html.includes('search-expand-button')
    || html.includes('search-expand-engine-name')
    || html.includes('search-engine-chevron')) {
    throw new Error('Search expansion and engine selection must use one persistent icon-and-name pill without a chevron.');
}
if (html.indexOf('id="homepage-search-form"') < html.indexOf('class="hero-subtitle"')
    || html.indexOf('id="homepage-search-form"') > html.indexOf('<section class="dashboard"')) {
    throw new Error('The search form must remain below the Hero subtitle and above the service dashboard.');
}
for (const symbolId of ['search-logo-bing', 'search-logo-google', 'search-logo-duckduckgo', 'search-logo-baidu', 'search-logo-bilibili']) {
    if (!html.includes(`id="${symbolId}"`)) throw new Error(`Missing inline search icon symbol: ${symbolId}`);
}
if (!/<symbol\b[^>]*\bid="search-logo-baidu"[\s\S]*?<path\b[^>]*\bfill="currentColor"/.test(html)) {
    throw new Error('The Baidu search icon must use the high-contrast monochrome foreground color.');
}
if (!/<symbol\b[^>]*\bid="search-logo-bilibili"[\s\S]*?<path\b[^>]*\bfill="#FB7299"/.test(html)) {
    throw new Error('The Bilibili search icon must preserve the supplied pink foreground color.');
}
if (!/\.search-form\s*\{[^}]*--search-engine-width:\s*7rem;[^}]*--search-shortcut-width:\s*4\.15rem;[^}]*--search-collapsed-width:\s*calc\(var\(--search-engine-width\) \+ var\(--search-shortcut-width\)\);[^}]*--search-motion-duration:\s*300ms;/.test(html)
    || !/data-current-engine="google"[\s\S]*?--search-engine-width:\s*7\.5rem;/.test(html)
    || !/data-current-engine="duckduckgo"[\s\S]*?--search-engine-width:\s*10rem;/.test(html)
    || !/data-current-engine="bilibili"[\s\S]*?--search-engine-width:\s*8rem;/.test(html)
    || !/\.search-shell\s*\{[^}]*transition:\s*[^}]*width\s+var\(--search-motion-duration\)\s+cubic-bezier\(0\.2,\s*0\.8,\s*0\.2,\s*1\)/.test(html)
    || !/\.search-form\[data-search-enhanced="true"\]\s+\.search-engine-button\s*\{[^}]*width:\s*calc\(var\(--search-collapsed-width\) - 2px\);[^}]*display:\s*inline-flex;/.test(html)
    || !/\.search-form\[data-search-enhanced="true"\]\s+\.search-engine-button\s*\{[^}]*transition:\s*[^}]*width\s+var\(--search-motion-duration\)[^}]*height\s+var\(--search-motion-duration\)[^}]*margin-left\s+var\(--search-motion-duration\)[^}]*padding\s+var\(--search-motion-duration\)[^}]*gap\s+var\(--search-motion-duration\)/.test(html)
    || !/data-expanded="true"\]\s+\.search-engine-button\s*\{[^}]*width:\s*calc\(var\(--search-engine-width\) - 1\.25rem\);/.test(html)
    || !/\.search-engine-identity\s*\{[^}]*display:\s*inline-flex;[^}]*gap:\s*0\.5rem;/.test(html)
    || !/\.search-engine-identity\s*\{[^}]*opacity:\s*1;[^}]*transform:\s*translateX\(0\);[^}]*opacity\s+90ms[^}]*transform\s+90ms/.test(html)
    || !/\.search-engine-identity-viewport\s*\{[^}]*display:\s*inline-flex;[^}]*overflow:\s*hidden;/.test(html)
    || !/data-engine-switch-phase="out"\]\[data-engine-switch-direction="previous"\][^}]*\.search-engine-identity\s*\{[^}]*opacity:\s*0;[^}]*translateX\(0\.7rem\)/.test(html)
    || !/data-engine-switch-phase="out"\]\[data-engine-switch-direction="next"\][^}]*\.search-engine-identity\s*\{[^}]*opacity:\s*0;[^}]*translateX\(-0\.7rem\)/.test(html)
    || !/data-engine-switch-phase="position"\]\[data-engine-switch-direction="previous"\][^}]*\.search-engine-identity\s*\{[^}]*translateX\(-0\.7rem\)/.test(html)
    || !/data-engine-switch-phase="position"\]\[data-engine-switch-direction="next"\][^}]*\.search-engine-identity\s*\{[^}]*translateX\(0\.7rem\)/.test(html)
    || !/\.search-shortcut-keycap\s*\{[^}]*width:\s*3\.6rem;[^}]*flex:\s*0\s+0\s+3\.6rem;[^}]*transition:\s*[^}]*width\s+var\(--search-motion-duration\)[^}]*max-width\s+var\(--search-motion-duration\)[^}]*flex-basis\s+var\(--search-motion-duration\)[^}]*opacity\s+160ms/.test(html)
    || !/data-expanded="true"\]\s+\.search-shortcut-keycap\s*\{[^}]*width:\s*0;[^}]*max-width:\s*0;[^}]*flex-basis:\s*0;[^}]*opacity:\s*0;/.test(html)
    || !/\.search-engine-name\s*\{[^}]*overflow:\s*visible;[^}]*line-height:\s*1\.35;/.test(html)
    || !/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?transition-duration:\s*0\.01ms\s*!important;[\s\S]*?transition-delay:\s*0ms\s*!important;/.test(html)) {
    throw new Error('The persistent engine pill is missing its keycap-aware 300ms transition, adaptive engine width, or reduced-motion contract.');
}

if (!/\.background-library-section\s*\{[^}]*--background-section-motion-duration:\s*300ms;/.test(html)
    || !/\.background-library-toggle\s*\{[^}]*transition:\s*padding-bottom\s+var\(--background-section-motion-duration\)\s+cubic-bezier\(0\.2,\s*0\.8,\s*0\.2,\s*1\)/.test(html)
    || !/\.background-library-chevron\s*\{[^}]*transition:\s*transform\s+var\(--background-section-motion-duration\)\s+cubic-bezier\(0\.2,\s*0\.8,\s*0\.2,\s*1\)/.test(html)
    || !/\.background-section-collapse\s*\{[^}]*grid-template-rows\s+var\(--background-section-motion-duration\)\s+cubic-bezier\(0\.2,\s*0\.8,\s*0\.2,\s*1\)[^}]*opacity\s+var\(--background-section-motion-duration\)\s+ease[^}]*transform\s+var\(--background-section-motion-duration\)\s+cubic-bezier\(0\.2,\s*0\.8,\s*0\.2,\s*1\)/.test(html)) {
    throw new Error('The background-library section motion must use one shared 300ms duration.');
}

const requiredMarkers = [
    '/api/background-preferences',
    'const backgroundInventoryEndpoint',
    '/api/background-files/upload',
    '/api/background-files/remove',
    '/api/bestdori/characters',
    '/api/background-files/draw',
    '/api/bestdori/history/reconcile',
    '/api/bestdori/history/reset',
    '/api/background-files/bing-daily',
    '/api/service-status',
    'homelab-background-state-cache-v1',
    'data-background-mode="landscape"',
    'data-background-mode="portrait"',
    'id="background-upload-button"',
    'id="background-upload-dialog"',
    'id="background-upload-progress"',
    'id="background-upload-list"',
    'id="background-upload-stop"',
    'id="background-upload-retry"',
    '.background-upload-actions .background-action-button[hidden]',
    'id="background-delete-button"',
    'id="background-original-link"',
    'id="background-bing-daily-button"',
    'id="background-delete-dialog"',
    'id="background-draw-button"',
    'id="card-draw-dialog"',
    'id="card-history-reset-dialog"',
    'fetchWithTimeout',
    'classifyServiceStatus',
    'data-state="warning"',
    'data-probe-url="https://cliproxy.home.example.com/management.html/"',
    "credentials: 'same-origin'",
    "'Content-Type': 'application/json'",
    'data-state="unknown"',
    'backgroundScanPending',
    'backgroundDrawPending',
    'bingDailyPending',
    'buildBestdoriDrawRequest',
    'payload.background && payload.background.background',
    'reconcileSharedBackgroundPreference',
    'validateBackgroundUploadFile',
    'backgroundModesUsingFile',
    'normalizeBackgroundThumbnailUrl',
    'normalizeBackgroundInventory',
    'readBoundedResponseText',
    'const requiredBackgroundUrls = [',
    'let remainingUnselectedSlots = Math.max(0, maximumBackgroundInventoryItems - requiredCount)',
    'discovered.size >= maximumBackgroundInventoryItems',
    'requestBackgroundInventory',
    'requestLegacyBackgroundListing',
    'isBestdoriBackgroundName',
    'isBingBackgroundName',
    "const backgroundLibrarySources = ['ordinary', 'bing', 'bestdori']",
    'const backgroundSectionTransitionFallbackMs = 400',
    'section.dataset.backgroundSection = source',
    'option.dataset.backgroundSource = source',
    "className = 'background-section-collapse'",
    "className = 'background-section-clip'",
    'grid-template-rows: 0fr',
    'transform: translateY(-4px)',
    "sectionContent.toggleAttribute('inert'",
    'runAfterBackgroundSectionTransition',
    'window.setTimeout(finish, backgroundSectionTransitionFallbackMs)',
    "newBadge.className = 'background-option-new-badge'",
    "presentBackgroundResults({ locateNames: [fileName] })",
    "option.classList.add('is-newly-added-entering')",
    'background-newly-added-wiggle 600ms',
    '.background-option-new-badge[hidden]',
    "className = 'background-section-empty'",
    'setBackgroundSectionExpanded(source, true, { afterTransition: sectionReady })',
    'const inventorySummary =',
    "request.setRequestHeader('Content-Type', 'application/octet-stream')",
    "errorCode === 'trash_unavailable'",
    'result.payload && result.payload.preferences',
    'id="service-count-label"',
    'initializeServiceCards',
    "'X-Requested-With': 'homelab-dashboard'",
    'homelab-search-engine-v1',
    'id="homepage-search-form"',
    'id="homepage-search-input"',
    'id="search-engine-list"',
    'id="search-status"',
    'initializeHomepageSearch',
    'data-search-enhanced'
];
requiredMarkers.forEach((marker) => {
    if (!html.includes(marker)) throw new Error(`Missing frontend contract marker: ${marker}`);
});

const uploadInputTag = html.match(/<input\b[^>]*id="background-upload-input"[^>]*>/i)?.[0] || '';
if (!/\bmultiple(?:\s|>|=)/i.test(uploadInputTag)
    || !/\baccept="[^"]*\.jpg[^"]*\.avif[^"]*"/i.test(uploadInputTag)) {
    throw new Error('Background upload input must support multiple filtered image selections.');
}

if (runtimeScript.includes('window.open(')) {
    throw new Error('New-tab navigation must use native safe links or forms instead of window.open().');
}
const helperStart = backgroundNetworkScript.indexOf('function fetchWithTimeout(');
const helperEnd = backgroundNetworkScript.indexOf('function syncBackgroundControlsBusy(', helperStart);
if (helperStart < 0 || helperEnd < 0) throw new Error('Unable to extract timeout helpers for behavior checks.');

const context = vm.createContext({
    AbortController,
    console: { warn() {} },
    Error,
    Promise,
    TextDecoder,
    TextEncoder,
    Uint8Array,
    URL,
    fetch: null,
    Image: null,
    window: { setTimeout, clearTimeout }
});
vm.runInContext(
    backgroundNetworkScript.slice(helperStart, helperEnd)
        + '\nglobalThis.__helpers = { fetchWithTimeout, preloadBackground };',
    context
);

async function expectRejection(promise, label) {
    let rejected = false;
    try {
        await promise;
    } catch (error) {
        rejected = true;
    }
    if (!rejected) throw new Error(`${label} did not reject.`);
}

context.fetch = () => Promise.resolve({ ok: true });
await expectRejection(
    context.__helpers.fetchWithTimeout('https://example.invalid/', {}, 20, null, () => new Promise(() => {})),
    'Response-body timeout'
);

context.AbortController = undefined;
context.fetch = () => new Promise(() => {});
await expectRejection(
    context.__helpers.fetchWithTimeout('https://example.invalid/', {}, 20),
    'Fetch timeout without AbortController'
);

let lastImage = null;
context.Image = class {
    constructor() {
        lastImage = this;
    }
    set src(value) {
        this.currentSource = value;
    }
};
await expectRejection(
    context.__helpers.preloadBackground({ href: 'https://example.invalid/hang.jpg', pathname: '/hang.jpg' }, 20),
    'Image preload timeout'
);
if (lastImage.onload !== null || lastImage.onerror !== null || lastImage.currentSource !== 'data:,') {
    throw new Error('Image timeout did not clean up handlers and cancel the source.');
}

const backgroundManagerHelperStart = backgroundManagerScript.indexOf('function normalizeBackgroundName(');
const backgroundManagerHelperEnd = backgroundManagerScript.indexOf('function backgroundUrlFromName(', backgroundManagerHelperStart);
if (backgroundManagerHelperStart < 0 || backgroundManagerHelperEnd < 0) {
    throw new Error('Unable to extract background file manager helpers for behavior checks.');
}
vm.runInContext(
    backgroundManagerScript.slice(backgroundManagerHelperStart, backgroundManagerHelperEnd)
        + '\nglobalThis.__backgroundManagerHelpers = { normalizeBackgroundName, normalizeBackgroundThumbnailUrl, readBoundedResponseText, normalizeBackgroundInventory, validateBackgroundUploadFile, backgroundModesUsingFile, isBestdoriBackgroundName, isBingBackgroundName, backgroundSourceForName };',
    context
);
context.backgroundExtensionPattern = /\.(?:jpe?g|png|webp|avif)$/i;
context.maximumBackgroundResponseBytes = 512 * 1024;
context.maximumBackgroundInventoryItems = 512;
context.pageBase = new URL('https://home.example.test/');
context.backgroundDirectory = new URL('backgrounds/', context.pageBase);
context.backgroundThumbnailDirectory = new URL('thumbnails/', context.backgroundDirectory);
context.location = { origin: context.pageBase.origin };
context.backgroundUrlFromName = (fileName) => new URL(
    encodeURIComponent(fileName),
    context.backgroundDirectory
);

const safeThumbnail = context.__backgroundManagerHelpers.normalizeBackgroundThumbnailUrl(
    'thumbnails/thumbnail-0123456789abcdef.webp?v=123-456'
);
if (!safeThumbnail || safeThumbnail.href !== 'https://home.example.test/backgrounds/thumbnails/thumbnail-0123456789abcdef.webp?v=123-456') {
    throw new Error('A valid same-origin thumbnail URL was rejected.');
}
[
    'https://images.example.invalid/thumbnail.webp?v=1',
    'thumbnails/../outside.webp?v=1',
    'thumbnails/nested/thumb.webp?v=1',
    'thumbnails/thumb.jpg?v=1',
    'thumbnails/thumb.webp?v=1&extra=2',
    'thumbnails/thumb.webp'
].forEach((candidate) => {
    if (context.__backgroundManagerHelpers.normalizeBackgroundThumbnailUrl(candidate)) {
        throw new Error(`Unsafe thumbnail URL was accepted: ${candidate}`);
    }
});

const normalizedInventory = context.__backgroundManagerHelpers.normalizeBackgroundInventory({
    version: 1,
    backgrounds: [
        {
            name: 'wallpaper.jpg',
            thumbnail: 'thumbnails/thumbnail-0123456789abcdef.webp?v=123-456'
        },
        { name: 'fallback.png', thumbnail: null }
    ],
    thumbnails: { generated: 1, reused: 1, failed: 0, deferred: 0 },
    total: 2,
    truncated: false
});
if (!normalizedInventory
    || normalizedInventory.backgrounds.length !== 2
    || normalizedInventory.backgrounds[0].url.href !== 'https://home.example.test/backgrounds/wallpaper.jpg'
    || !normalizedInventory.backgrounds[0].thumbnailUrl
    || normalizedInventory.backgrounds[1].thumbnailUrl !== null
    || normalizedInventory.thumbnails.generated !== 1
    || normalizedInventory.thumbnails.deferred !== 0
    || normalizedInventory.total !== 2
    || normalizedInventory.truncated) {
    throw new Error('A valid background inventory was not normalized correctly.');
}
if (context.__backgroundManagerHelpers.normalizeBackgroundInventory({
    version: 1,
    backgrounds: [
        { name: 'duplicate.jpg', thumbnail: null },
        { name: 'duplicate.jpg', thumbnail: null }
    ],
    thumbnails: { generated: 0, reused: 0, failed: 0 }
})) {
    throw new Error('A background inventory containing duplicate original names was accepted.');
}
const tooManyBackgrounds = Array.from({ length: 513 }, (_, index) => ({
    name: `wallpaper-${index}.jpg`,
    thumbnail: null
}));
if (context.__backgroundManagerHelpers.normalizeBackgroundInventory({
    version: 1,
    backgrounds: tooManyBackgrounds,
    thumbnails: { generated: 0, reused: 0, failed: 513, deferred: 0 },
    total: 513,
    truncated: false
})) {
    throw new Error('A background inventory larger than 512 items was accepted.');
}

const boundedText = await context.__backgroundManagerHelpers.readBoundedResponseText({
    headers: { get: () => '5' },
    body: {
        getReader() {
            let sent = false;
            return {
                async read() {
                    if (sent) return { done: true };
                    sent = true;
                    return { done: false, value: new Uint8Array([104, 101, 108, 108, 111]) };
                },
                async cancel() {},
                releaseLock() {}
            };
        }
    }
}, 5);
if (boundedText !== 'hello') {
    throw new Error('The bounded response reader did not decode a valid response.');
}
await expectRejection(
    context.__backgroundManagerHelpers.readBoundedResponseText({
        headers: { get: () => null },
        body: {
            getReader() {
                let sent = false;
                return {
                    async read() {
                        if (sent) return { done: true };
                        sent = true;
                        return { done: false, value: new Uint8Array(6) };
                    },
                    async cancel() {},
                    releaseLock() {}
                };
            }
        }
    }, 5),
    'Oversized streamed background response'
);
await expectRejection(
    context.__backgroundManagerHelpers.readBoundedResponseText({
        headers: { get: () => null },
        text: async () => 'unbounded'
    }, 5),
    'Unbounded non-streaming background response'
);

const validUpload = { name: 'wallpaper.avif', size: 50 * 1024 * 1024 };
if (context.__backgroundManagerHelpers.validateBackgroundUploadFile(validUpload) !== '') {
    throw new Error('A supported upload at the 50 MiB boundary was rejected.');
}
if (!context.__backgroundManagerHelpers.validateBackgroundUploadFile({ name: 'wallpaper.gif', size: 1024 })) {
    throw new Error('An unsupported upload extension was accepted.');
}
if (!context.__backgroundManagerHelpers.validateBackgroundUploadFile({ name: 'wallpaper.png', size: 0 })) {
    throw new Error('An empty upload was accepted.');
}
if (!context.__backgroundManagerHelpers.validateBackgroundUploadFile({
    name: 'wallpaper.webp',
    size: 50 * 1024 * 1024 + 1
})) {
    throw new Error('An upload larger than 50 MiB was accepted.');
}
const affectedModes = context.__backgroundManagerHelpers.backgroundModesUsingFile('shared.jpg', {
    landscape: 'shared.jpg',
    portrait: 'shared.jpg'
});
if (JSON.stringify(Array.from(affectedModes)) !== JSON.stringify(['landscape', 'portrait'])) {
    throw new Error('Background deletion impact modes were not derived from both preferences.');
}

const bestdoriBackgroundNames = [
    '\u6237\u5c71\u9999\u6f84_\u4e09\u661f_\u6d4b\u8bd5\u5361\u9762_101_after_training.png',
    '\u7c73\u6b47\u5c14_\u4e94\u661f_\u6d77\u8fb9\u5047\u65e5_987_normal.png',
    '\u89d2\u8272 ID 40_\u56db\u661f_\u6807\u9898_345_normal (2).png',
    '\u89d2\u8272_\u56db\u661f_\u6807\u9898_345_after_training (10).png'
];
bestdoriBackgroundNames.forEach((fileName) => {
    if (!context.__backgroundManagerHelpers.isBestdoriBackgroundName(fileName)
        || context.__backgroundManagerHelpers.backgroundSourceForName(fileName) !== 'bestdori') {
        throw new Error(`Bestdori-generated background name was not classified correctly: ${fileName}`);
    }
});

const bingBackgroundNames = [
    'bing_cn_20260820_OHR.China_ZH-CN100.jpg',
    'bing_row_20260820_OHR.World_ROW200.jpg',
    'bing_us_20260820_OHR.America_EN-US300 (2).jpg'
];
bingBackgroundNames.forEach((fileName) => {
    if (!context.__backgroundManagerHelpers.isBingBackgroundName(fileName)
        || context.__backgroundManagerHelpers.backgroundSourceForName(fileName) !== 'bing') {
        throw new Error(`Bing-generated background name was not classified correctly: ${fileName}`);
    }
});

const ordinaryBackgroundNames = [
    'bg.jpg',
    'vacation.png',
    '\u89d2\u8272_\u4e94\u661f_\u6807\u9898_123_normal.jpg',
    '\u89d2\u8272_\u516d\u661f_\u6807\u9898_123_normal.png',
    '\u89d2\u8272_\u4e94\u661f_\u6807\u9898_normal.png',
    '\u89d2\u8272_\u4e94\u661f_\u6807\u9898_123_unknown.png',
    '\u89d2\u8272_\u4e94\u661f_\u6807\u9898_123_normal (1).png',
    'bing_row_20260820_OHR.World_EN-US200.jpg',
    'bing_unknown_20260820_OHR.World_ROW200.jpg'
];
ordinaryBackgroundNames.forEach((fileName) => {
    if (context.__backgroundManagerHelpers.isBestdoriBackgroundName(fileName)
        || context.__backgroundManagerHelpers.isBingBackgroundName(fileName)
        || context.__backgroundManagerHelpers.backgroundSourceForName(fileName) !== 'ordinary') {
        throw new Error(`Ordinary background name was misclassified: ${fileName}`);
    }
});

const backgroundSortStart = backgroundManagerScript.indexOf('function mergeAndSortBackgrounds(');
const backgroundSortEnd = backgroundManagerScript.indexOf('async function requestBackgroundInventory(', backgroundSortStart);
if (backgroundSortStart < 0 || backgroundSortEnd < 0) {
    throw new Error('Unable to extract the background natural-order helper.');
}
context.backgroundModes = ['landscape', 'portrait'];
context.defaultBackground = context.backgroundUrlFromName('bg.jpg');
context.currentBackground = context.backgroundUrlFromName('selected-10.jpg');
context.backgroundPreferences = {
    landscape: 'selected-10.jpg',
    portrait: 'selected-2.jpg'
};
context.backgroundUrlForMode = (mode) => context.backgroundUrlFromName(
    context.backgroundPreferences[mode]
);
context.backgroundFileName = (url) => decodeURIComponent(url.pathname.split('/').pop());
vm.runInContext(
    backgroundManagerScript.slice(backgroundSortStart, backgroundSortEnd)
        + '\nglobalThis.__mergeAndSortBackgrounds = mergeAndSortBackgrounds;',
    context
);
const makeBackgroundRecord = (name) => ({
    url: context.backgroundUrlFromName(name),
    thumbnailUrl: null
});
const naturallyOrderedInput = [
    'wallpaper-10.jpg',
    'selected-10.jpg',
    'alpha.jpg',
    'bg.jpg',
    'selected-2.jpg',
    'wallpaper-2.jpg'
].map(makeBackgroundRecord);
const expectedNaturalOrder = [
    'bg.jpg',
    'alpha.jpg',
    'selected-2.jpg',
    'selected-10.jpg',
    'wallpaper-2.jpg',
    'wallpaper-10.jpg'
];
const naturalOrder = Array.from(
    context.__mergeAndSortBackgrounds(naturallyOrderedInput),
    (background) => context.backgroundFileName(background.url)
);
if (JSON.stringify(naturalOrder) !== JSON.stringify(expectedNaturalOrder)) {
    throw new Error(`Selected backgrounds changed the natural wallpaper order: ${naturalOrder.join(', ')}`);
}
context.currentBackground = context.backgroundUrlFromName('wallpaper-10.jpg');
context.backgroundPreferences.landscape = 'wallpaper-10.jpg';
const orderAfterSelectionChange = Array.from(
    context.__mergeAndSortBackgrounds(naturallyOrderedInput),
    (background) => context.backgroundFileName(background.url)
);
if (JSON.stringify(orderAfterSelectionChange) !== JSON.stringify(expectedNaturalOrder)) {
    throw new Error('Changing the selected wallpaper reordered the visible background library.');
}

context.currentBackground = context.backgroundUrlFromName('wallpaper-0512.jpg');
context.backgroundPreferences = {
    landscape: 'wallpaper-0512.jpg',
    portrait: 'wallpaper-0511.jpg'
};
const overflowInput = Array.from({ length: 513 }, (_, index) => (
    makeBackgroundRecord(`wallpaper-${String(index).padStart(4, '0')}.jpg`)
));
const overflowOrder = Array.from(
    context.__mergeAndSortBackgrounds(overflowInput),
    (background) => context.backgroundFileName(background.url)
);
if (overflowOrder.length !== context.maximumBackgroundInventoryItems
    || overflowOrder[0] !== 'bg.jpg'
    || overflowOrder[overflowOrder.length - 2] !== 'wallpaper-0511.jpg'
    || overflowOrder[overflowOrder.length - 1] !== 'wallpaper-0512.jpg'
    || overflowOrder.includes('wallpaper-0509.jpg')
    || overflowOrder.includes('wallpaper-0510.jpg')) {
    throw new Error('The capped wallpaper list did not retain selected backgrounds in natural order.');
}

const renderFunctionStart = backgroundManagerScript.indexOf('function renderBackgroundOptions(');
const renderFunctionEnd = backgroundManagerScript.indexOf('function focusBackgroundOption(', renderFunctionStart);
if (renderFunctionStart < 0 || renderFunctionEnd < 0) {
    throw new Error('Unable to extract the grouped background renderer.');
}
const groupedRendererSource = backgroundManagerScript.slice(renderFunctionStart, renderFunctionEnd);
if (!groupedRendererSource.includes('backgroundLibrarySources.forEach')
    || !groupedRendererSource.includes('backgroundLibraryExpanded[source]')
    || !groupedRendererSource.includes('groupedBackgrounds.ordinary.length')
    || !groupedRendererSource.includes('groupedBackgrounds.bing.length')
    || !groupedRendererSource.includes('groupedBackgrounds.bestdori.length')
    || !groupedRendererSource.includes('setBackgroundSectionExpanded(source')
    || !groupedRendererSource.includes("sectionContent.setAttribute('aria-hidden'")
    || !groupedRendererSource.includes("sectionContent.toggleAttribute('inert'")
    || !groupedRendererSource.includes('option.dataset.url = url.href')
    || !groupedRendererSource.includes('thumbnail.src = thumbnailSource.href')
    || !groupedRendererSource.includes("thumbnail.addEventListener('error'")
    || !groupedRendererSource.includes('thumbnail.src = url.href')
    || !groupedRendererSource.includes('{ once: true }')
    || !groupedRendererSource.includes('saveSharedBackgroundPreference(editingBackgroundMode, url)')
    || groupedRendererSource.includes('sectionGrid.hidden')) {
    throw new Error('Grouped background renderer is missing thumbnail, original-selection, count, collapse, or toggle behavior.');
}

const inventoryRequestStart = backgroundManagerScript.indexOf('async function requestBackgroundInventory(');
const inventoryRequestEnd = backgroundManagerScript.indexOf('async function requestLegacyBackgroundListing(', inventoryRequestStart);
if (inventoryRequestStart < 0 || inventoryRequestEnd < 0) {
    throw new Error('Unable to extract the background inventory request.');
}
vm.runInContext(
    backgroundManagerScript.slice(inventoryRequestStart, inventoryRequestEnd)
        + '\nglobalThis.__requestBackgroundInventory = requestBackgroundInventory;',
    context
);
context.backgroundInventoryEndpoint = { href: 'https://home.example.test/api/background-files' };
context.normalizeBackgroundInventory = (payload) => payload.version === 1 ? payload : null;
let observedInventoryRequest = null;
context.fetchWithTimeout = async (resource, options, timeout, controller, transform) => {
    observedInventoryRequest = { resource, options, timeout, controller };
    return transform({
        ok: true,
        status: 200,
        headers: {
            get: (name) => name === 'Content-Length' ? '135' : null
        },
        text: async () => JSON.stringify({
            version: 1,
            backgrounds: [{ name: 'wallpaper.jpg', thumbnail: null }],
            thumbnails: { generated: 0, reused: 0, failed: 1, deferred: 0 },
            total: 1,
            truncated: false
        })
    });
};
const inventoryController = { signal: 'test-controller' };
const requestedInventory = await context.__requestBackgroundInventory(inventoryController);
if (requestedInventory.version !== 1
    || observedInventoryRequest.resource !== context.backgroundInventoryEndpoint.href
    || observedInventoryRequest.options.cache !== 'no-store'
    || observedInventoryRequest.options.credentials !== 'same-origin'
    || observedInventoryRequest.options.headers.Accept !== 'application/json'
    || observedInventoryRequest.timeout !== 120000
    || observedInventoryRequest.controller !== inventoryController) {
    throw new Error('Background inventory request contract mismatch.');
}

const scanFunctionStart = backgroundManagerScript.indexOf('async function scanBackgroundDirectory(');
const scanFunctionEnd = backgroundManagerScript.indexOf('function setEditingBackgroundMode(', scanFunctionStart);
if (scanFunctionStart < 0 || scanFunctionEnd < 0) {
    throw new Error('Unable to extract the background scan fallback workflow.');
}
vm.runInContext(
    backgroundManagerScript.slice(scanFunctionStart, scanFunctionEnd)
        + '\nglobalThis.__scanBackgroundDirectory = scanBackgroundDirectory;',
    context
);
context.AbortController = class {
    abort() {}
};
context.backgroundScanController = null;
context.backgroundScanSequence = 0;
context.backgroundScanPending = false;
context.backgroundPreferencesConnection = 'online';
context.editingBackgroundMode = 'landscape';
context.backgroundModeLabels = { landscape: '横屏', portrait: '竖屏' };
context.syncBackgroundControlsBusy = () => {};
const observedBackgroundStatuses = [];
context.setBackgroundStatus = (message, state) => {
    observedBackgroundStatuses.push({ message, state });
};
context.requestBackgroundInventory = async () => {
    throw new Error('simulated inventory outage');
};
let legacyListingRequestCount = 0;
context.requestLegacyBackgroundListing = async () => {
    legacyListingRequestCount += 1;
    return [{ url: new URL('https://home.example.test/backgrounds/bg.jpg'), thumbnailUrl: null }];
};
context.mergeAndSortBackgrounds = (backgrounds) => backgrounds;
context.renderBackgroundOptions = () => ({ total: 1, ordinary: 1, bing: 0, bestdori: 0 });
context.renderKnownBackgroundFallback = () => ({ total: 1, ordinary: 1, bing: 0, bestdori: 0 });
await context.__scanBackgroundDirectory();
if (legacyListingRequestCount !== 1
    || !observedBackgroundStatuses.some(
        ({ message }) => message.includes('\u539f\u56fe\u52a0\u8f7d\u964d\u7ea7\u6a21\u5f0f')
    )
    || context.backgroundScanPending) {
    throw new Error('Inventory failure did not enter and announce the legacy original-loading fallback.');
}

const transitionHelperStart = backgroundManagerScript.indexOf('function cancelBackgroundSectionTransition(');
const transitionHelperEnd = backgroundManagerScript.indexOf('function focusBackgroundOption(', transitionHelperStart);
if (transitionHelperStart < 0 || transitionHelperEnd < 0) {
    throw new Error('Unable to extract background section transition helpers.');
}

const transitionListeners = new Map();
const transitionTimers = new Map();
const transitionTimerDelays = new Map();
let transitionTimerId = 0;
let reduceBackgroundMotion = false;
const transitionContentAttributes = new Map();
const transitionContent = {
    dataset: { expanded: 'true' },
    offsetHeight: 100,
    setAttribute(name, value) {
        transitionContentAttributes.set(name, String(value));
    },
    toggleAttribute(name, force) {
        if (force) transitionContentAttributes.set(name, '');
        else transitionContentAttributes.delete(name);
    },
    addEventListener(type, listener) {
        transitionListeners.set(type, listener);
    },
    removeEventListener(type, listener) {
        if (transitionListeners.get(type) === listener) transitionListeners.delete(type);
    }
};
const transitionToggleAttributes = new Map();
const transitionToggle = {
    setAttribute(name, value) {
        transitionToggleAttributes.set(name, String(value));
    }
};
const transitionSection = {
    dataset: { backgroundSection: 'ordinary' },
    querySelector(selector) {
        if (selector === '.background-library-toggle') return transitionToggle;
        if (selector === '.background-section-collapse') return transitionContent;
        return null;
    }
};
const transitionContext = vm.createContext({
    backgroundLibrarySources: ['ordinary', 'bing', 'bestdori'],
    backgroundLibraryExpanded: { ordinary: true, bing: true, bestdori: true },
    backgroundSectionTransitionFallbackMs: 400,
    backgroundSectionTransitionCleanups: new Map(),
    backgroundGrid: {
        querySelectorAll() {
            return [transitionSection];
        }
    },
    window: {
        matchMedia() {
            return { matches: reduceBackgroundMotion };
        },
        setTimeout(callback, delay) {
            const timerId = ++transitionTimerId;
            transitionTimers.set(timerId, callback);
            transitionTimerDelays.set(timerId, delay);
            return timerId;
        },
        clearTimeout(timerId) {
            transitionTimers.delete(timerId);
            transitionTimerDelays.delete(timerId);
        }
    }
});
vm.runInContext(
    backgroundManagerScript.slice(transitionHelperStart, transitionHelperEnd)
        + '\nglobalThis.__backgroundTransitionHelpers = { setBackgroundSectionExpanded };',
    transitionContext
);

if (!transitionContext.__backgroundTransitionHelpers.setBackgroundSectionExpanded('ordinary', false)
    || transitionToggleAttributes.get('aria-expanded') !== 'false'
    || transitionContent.dataset.expanded !== 'false'
    || transitionContentAttributes.get('aria-hidden') !== 'true'
    || !transitionContentAttributes.has('inert')) {
    throw new Error('Collapsing a background section did not synchronize visual and accessibility state.');
}

let completedBackgroundFocus = 0;
if (!transitionContext.__backgroundTransitionHelpers.setBackgroundSectionExpanded('ordinary', true, {
    afterTransition() {
        completedBackgroundFocus += 1;
    }
}) || completedBackgroundFocus !== 0
    || !transitionListeners.has('transitionend')
    || Array.from(transitionTimerDelays.values())[0] !== 400) {
    throw new Error('Animated background expansion did not defer its completion callback.');
}
transitionListeners.get('transitionend')({
    target: transitionContent,
    propertyName: 'grid-template-rows'
});
if (completedBackgroundFocus !== 1
    || transitionTimers.size !== 0
    || transitionTimerDelays.size !== 0
    || transitionContentAttributes.has('inert')
    || transitionContentAttributes.get('aria-hidden') !== 'false') {
    throw new Error('Background expansion did not complete once or restore accessible content.');
}

let immediateBackgroundFocus = 0;
transitionContext.__backgroundTransitionHelpers.setBackgroundSectionExpanded('ordinary', true, {
    afterTransition() {
        immediateBackgroundFocus += 1;
    }
});
if (immediateBackgroundFocus !== 1
    || transitionTimers.size !== 0
    || transitionTimerDelays.size !== 0
    || transitionListeners.size !== 0) {
    throw new Error('An already expanded background section did not complete focus immediately.');
}

let fallbackBackgroundFocus = 0;
transitionContext.__backgroundTransitionHelpers.setBackgroundSectionExpanded('ordinary', false);
transitionContext.__backgroundTransitionHelpers.setBackgroundSectionExpanded('ordinary', true, {
    afterTransition() {
        fallbackBackgroundFocus += 1;
    }
});
const fallbackTransitionTimer = Array.from(transitionTimers.values())[0];
fallbackTransitionTimer();
if (fallbackBackgroundFocus !== 1
    || transitionTimers.size !== 0
    || transitionTimerDelays.size !== 0
    || transitionListeners.size !== 0) {
    throw new Error('Background transition timeout fallback did not complete and clean up.');
}

transitionContext.__backgroundTransitionHelpers.setBackgroundSectionExpanded('ordinary', false);
transitionContext.__backgroundTransitionHelpers.setBackgroundSectionExpanded('ordinary', true, {
    afterTransition() {
        completedBackgroundFocus += 10;
    }
});
const staleTransitionListener = transitionListeners.get('transitionend');
transitionContext.__backgroundTransitionHelpers.setBackgroundSectionExpanded('ordinary', false);
staleTransitionListener({ target: transitionContent, propertyName: 'grid-template-rows' });
if (completedBackgroundFocus !== 1
    || transitionTimers.size !== 0
    || transitionTimerDelays.size !== 0) {
    throw new Error('Reversing a background transition did not cancel its stale completion callback.');
}

reduceBackgroundMotion = true;
transitionContext.__backgroundTransitionHelpers.setBackgroundSectionExpanded('ordinary', true, {
    afterTransition() {
        completedBackgroundFocus += 1;
    }
});
if (completedBackgroundFocus !== 2
    || transitionTimers.size !== 0
    || transitionTimerDelays.size !== 0
    || transitionListeners.size !== 0) {
    throw new Error('Reduced-motion background expansion did not complete immediately.');
}

const presentationHelperStart = backgroundManagerScript.indexOf('function focusBackgroundOption(');
const presentationHelperEnd = backgroundManagerScript.indexOf('function backgroundDeleteDialogIsOpen(', presentationHelperStart);
if (presentationHelperStart < 0 || presentationHelperEnd < 0) {
    throw new Error('Unable to extract the batch background result presenter.');
}
function makePresentationOption(fileName, source) {
    const classes = new Set();
    const badge = { hidden: true };
    return {
        fileName,
        dataset: {
            url: new URL(encodeURIComponent(fileName), 'https://home.example.test/backgrounds/').href,
            backgroundSource: source
        },
        disabled: false,
        isConnected: true,
        offsetWidth: 160,
        scrollCalls: 0,
        classList: {
            add: (...names) => names.forEach((name) => classes.add(name)),
            remove: (...names) => names.forEach((name) => classes.delete(name)),
            toggle(name, force) {
                if (force) classes.add(name);
                else classes.delete(name);
            },
            contains: (name) => classes.has(name)
        },
        querySelector(selector) {
            return selector === '.background-option-new-badge' ? badge : null;
        },
        closest() { return null; },
        scrollIntoView() { this.scrollCalls += 1; },
        addEventListener(type, callback) {
            if (type === 'animationend') this.animationEnd = callback;
        },
        badge
    };
}
const presentationOptions = [
    makePresentationOption('bing_cn_20260821_OHR.Test_ZH-CN100.jpg', 'bing'),
    makePresentationOption('holiday.png', 'ordinary'),
    makePresentationOption('existing-bestdori.png', 'bestdori')
];
const presentationHighlights = new Set();
const normalizePresentationName = (value) => (
    typeof value === 'string' && /^[^/\\]+\.(?:jpe?g|png|webp|avif)$/i.test(value) ? value : null
);
const expandedPresentationSources = [];
let focusedPresentationOption = null;
let reducePresentationMotion = false;
let deferPresentationReveal = false;
let deferredPresentationReveal = null;
const presentationContext = vm.createContext({
    URL,
    Set,
    Array,
    highlightedBackgroundNames: presentationHighlights,
    backgroundPresentationToken: 0,
    backgroundLibrarySources: ['ordinary', 'bing', 'bestdori'],
    backgroundGrid: {
        querySelectorAll(selector) {
            return selector === '.background-option' ? presentationOptions : [];
        }
    },
    normalizeBackgroundName: normalizePresentationName,
    backgroundUrlFromName(value) {
        const fileName = normalizePresentationName(value);
        return fileName ? new URL(encodeURIComponent(fileName), 'https://home.example.test/backgrounds/') : null;
    },
    updateBackgroundSelection() {
        presentationOptions.forEach((option) => {
            const isNew = presentationHighlights.has(option.fileName);
            option.classList.toggle('is-newly-added', isNew);
            option.badge.hidden = !isNew;
        });
    },
    backgroundDialogIsOpen: () => true,
    focusWithoutScroll(option) {
        focusedPresentationOption = option;
    },
    setBackgroundSectionExpanded(source, expanded, options) {
        expandedPresentationSources.push(source);
        if (deferPresentationReveal) deferredPresentationReveal = options.afterTransition;
        else options.afterTransition();
        return true;
    },
    window: {
        matchMedia() {
            return { matches: reducePresentationMotion };
        }
    }
});
vm.runInContext(
    backgroundManagerScript.slice(presentationHelperStart, presentationHelperEnd)
        + '\nglobalThis.__backgroundPresentation = { focusBackgroundOption, clearBackgroundResultPresentation, presentBackgroundResults };',
    presentationContext
);
if (!presentationContext.__backgroundPresentation.presentBackgroundResults({
    createdNames: [
        'bing_cn_20260821_OHR.Test_ZH-CN100.jpg',
        'holiday.png',
        'bing_cn_20260821_OHR.Test_ZH-CN100.jpg',
        '../unsafe.png'
    ],
    locateNames: ['existing-bestdori.png']
})
    || JSON.stringify(expandedPresentationSources) !== JSON.stringify(['bing', 'ordinary', 'bestdori'])
    || focusedPresentationOption !== presentationOptions[0]
    || presentationOptions[0].scrollCalls !== 1
    || !presentationOptions[0].classList.contains('is-newly-added')
    || !presentationOptions[1].classList.contains('is-newly-added')
    || presentationOptions[2].classList.contains('is-newly-added')
    || !presentationOptions[0].classList.contains('is-newly-added-entering')
    || presentationOptions[0].badge.hidden
    || presentationOptions[2].badge.hidden === false) {
    throw new Error('Batch background results did not expand, highlight, animate, scroll, and focus in operation order.');
}
presentationContext.__backgroundPresentation.clearBackgroundResultPresentation();
if (presentationContext.highlightedBackgroundNames.size !== 0
    || presentationOptions.some((option) => option.classList.contains('is-newly-added') || !option.badge.hidden)) {
    throw new Error('Closing or replacing a background result batch did not clear its persistent markers.');
}
reducePresentationMotion = true;
expandedPresentationSources.length = 0;
if (!presentationContext.__backgroundPresentation.presentBackgroundResults({
    locateNames: ['existing-bestdori.png']
})
    || focusedPresentationOption !== presentationOptions[2]
    || presentationOptions[2].classList.contains('is-newly-added')
    || presentationOptions[2].classList.contains('is-newly-added-entering')) {
    throw new Error('Existing-only or reduced-motion background presentation applied an incorrect new-item effect.');
}
presentationContext.__backgroundPresentation.clearBackgroundResultPresentation();
reducePresentationMotion = false;
deferPresentationReveal = true;
focusedPresentationOption = null;
presentationContext.__backgroundPresentation.presentBackgroundResults({ createdNames: ['holiday.png'] });
presentationContext.__backgroundPresentation.clearBackgroundResultPresentation();
deferredPresentationReveal();
if (focusedPresentationOption !== null
    || presentationOptions[1].classList.contains('is-newly-added-entering')) {
    throw new Error('A stale background expansion callback restored focus or animation after its batch was cleared.');
}

const bestdoriHelperStart = bestdoriRequestScript.indexOf('function backgroundManagerIsBusy(');
const bestdoriHelperEnd = bestdoriRequestScript.length;
if (bestdoriHelperStart < 0 || bestdoriHelperEnd < 0) {
    throw new Error('Unable to extract Bestdori request helpers for behavior checks.');
}
vm.runInContext(
    bestdoriRequestScript.slice(bestdoriHelperStart, bestdoriHelperEnd)
        + '\nglobalThis.__bestdoriHelpers = { backgroundManagerIsBusy, bestdoriErrorMessage, buildBestdoriDrawRequest, requestBestdoriJson };',
    context
);

context.backgroundScanPending = false;
context.backgroundWritePending = false;
context.backgroundDeletePending = false;
context.backgroundUploadController = { isBusy: () => false };
context.bingDailyController = { isBusy: () => false };
context.bestdoriController = { isBusy: () => false };
if (context.__bestdoriHelpers.backgroundManagerIsBusy()) {
    throw new Error('Idle background manager was reported as busy.');
}
context.bestdoriController = { isBusy: () => true };
if (!context.__bestdoriHelpers.backgroundManagerIsBusy()) {
    throw new Error('Bestdori work was not included in the shared background-manager busy state.');
}
context.bestdoriController = { isBusy: () => false };
context.bingDailyController = { isBusy: () => true };
if (!context.__bestdoriHelpers.backgroundManagerIsBusy()) {
    throw new Error('Bing daily work was not included in the shared background-manager busy state.');
}
context.bingDailyController = { isBusy: () => false };

const cardDrawStatusHelperStart = bestdoriScript.indexOf('function setCardDrawStatus(');
const cardDrawStatusHelperEnd = bestdoriScript.indexOf('function syncCardDrawControls(', cardDrawStatusHelperStart);
if (cardDrawStatusHelperStart < 0 || cardDrawStatusHelperEnd < 0) {
    throw new Error('Unable to extract Bestdori status helpers.');
}
let cardDrawModeForStatus = 'character';
let cardDrawRaritiesForStatus = [3, 4, 5];
const cardDrawStatusElement = {
    textContent: '',
    dataset: {}
};
const cardDrawStatusContext = vm.createContext({
    cardDrawStatus: cardDrawStatusElement,
    cardDrawCharactersLoaded: false,
    cardCharacterSelect: {
        options: []
    },
    selectedCardDrawMode: () => cardDrawModeForStatus,
    selectedCardDrawRarities: () => cardDrawRaritiesForStatus
});
vm.runInContext(
    bestdoriScript.slice(cardDrawStatusHelperStart, cardDrawStatusHelperEnd)
        + '\nglobalThis.__resetCardDrawStatus = resetCardDrawStatus;',
    cardDrawStatusContext
);

cardDrawRaritiesForStatus = [];
cardDrawStatusContext.__resetCardDrawStatus();
if (cardDrawStatusElement.textContent !== '\u8bf7\u81f3\u5c11\u9009\u62e9\u4e00\u4e2a\u661f\u7ea7\u3002'
    || cardDrawStatusElement.dataset.kind !== 'error') {
    throw new Error('An empty rarity selection did not show the Bestdori validation status.');
}

cardDrawModeForStatus = 'random';
cardDrawRaritiesForStatus = [4];
cardDrawStatusContext.__resetCardDrawStatus();
if (!cardDrawStatusElement.textContent.includes('\u5168\u968f\u673a\u6a21\u5f0f')
    || Object.prototype.hasOwnProperty.call(cardDrawStatusElement.dataset, 'kind')) {
    throw new Error('Restoring a rarity in random mode did not clear the stale validation status.');
}

cardDrawModeForStatus = 'character';
cardDrawStatusContext.cardDrawCharactersLoaded = true;
cardDrawStatusContext.cardCharacterSelect.options = Array.from({ length: 40 }, (_, index) => ({
    value: String(index + 1)
}));
cardDrawStatusContext.__resetCardDrawStatus();
if (!cardDrawStatusElement.textContent.includes('\u5df2\u8bfb\u53d6 40 \u4e2a\u89d2\u8272')
    || cardDrawStatusElement.dataset.kind !== 'success') {
    throw new Error('Loaded character mode did not restore its ready status.');
}

cardDrawStatusContext.cardDrawCharactersLoaded = false;
cardDrawStatusContext.__resetCardDrawStatus();
if (cardDrawStatusElement.textContent !== '\u8bf7\u9009\u62e9\u89d2\u8272\u4e0e\u81f3\u5c11\u4e00\u4e2a\u661f\u7ea7\u3002'
    || Object.prototype.hasOwnProperty.call(cardDrawStatusElement.dataset, 'kind')) {
    throw new Error('Unloaded character mode did not restore its neutral status.');
}

const cardDrawCloseStart = bestdoriScript.indexOf('function closeCardDrawDialog(');
const cardDrawCloseEnd = bestdoriScript.indexOf('async function openCardDrawDialog(', cardDrawCloseStart);
if (cardDrawCloseStart < 0 || cardDrawCloseEnd < 0) {
    throw new Error('Unable to extract the Bestdori dialog closer.');
}
let cardDrawDialogClassRemoved = false;
let cardDrawFocusRestored = false;
cardDrawStatusContext.cardDrawCharactersLoaded = true;
cardDrawStatusContext.cardDrawDialog = {
    open: true,
    classList: {
        remove(name) {
            if (name === 'is-visible') cardDrawDialogClassRemoved = true;
        }
    },
    close() {
        this.open = false;
    },
    removeAttribute() {
        this.open = false;
    }
};
cardDrawStatusContext.cardDrawDialogIsOpen = () => cardDrawStatusContext.cardDrawDialog.open;
cardDrawStatusContext.backgroundDrawPending = false;
cardDrawStatusContext.backgroundDrawButton = {};
cardDrawStatusContext.focusWithoutScroll = () => {
    cardDrawFocusRestored = true;
};
vm.runInContext(
    bestdoriScript.slice(cardDrawCloseStart, cardDrawCloseEnd)
        + '\nglobalThis.__closeCardDrawDialog = closeCardDrawDialog;',
    cardDrawStatusContext
);
cardDrawStatusElement.textContent = '\u6b63\u5728\u4ece Bestdori \u62bd\u53d6\u5e76\u4e0b\u8f7d\u5361\u9762\uff0c\u8bf7\u7a0d\u5019\u2026';
if (!cardDrawStatusContext.__closeCardDrawDialog('close')
    || cardDrawStatusContext.cardDrawDialog.open
    || !cardDrawDialogClassRemoved
    || !cardDrawFocusRestored
    || !cardDrawStatusElement.textContent.includes('\u5df2\u8bfb\u53d6 40 \u4e2a\u89d2\u8272')) {
    throw new Error('Closing the Bestdori dialog did not clear its stale operational status.');
}

cardDrawStatusContext.cardDrawDialog.open = true;
cardDrawStatusContext.backgroundDrawPending = true;
cardDrawStatusElement.textContent = '\u6b63\u5728\u4ece Bestdori \u62bd\u53d6\u5e76\u4e0b\u8f7d\u5361\u9762\uff0c\u8bf7\u7a0d\u5019\u2026';
if (cardDrawStatusContext.__closeCardDrawDialog('close')
    || !cardDrawStatusContext.cardDrawDialog.open
    || cardDrawStatusElement.textContent !== '\u6b63\u5728\u4ece Bestdori \u62bd\u53d6\u5e76\u4e0b\u8f7d\u5361\u9762\uff0c\u8bf7\u7a0d\u5019\u2026') {
    throw new Error('An active Bestdori request was closed or had its progress status cleared.');
}
cardDrawStatusContext.backgroundDrawPending = false;

const cardDrawBindingStart = bestdoriScript.indexOf('cardDrawModeInputs.forEach((input) => {');
const cardDrawBindingEnd = bestdoriScript.indexOf("cardHistoryReconcile.addEventListener('click'", cardDrawBindingStart);
if (cardDrawBindingStart < 0 || cardDrawBindingEnd < 0) {
    throw new Error('Unable to inspect Bestdori form-change bindings.');
}
const cardDrawBindingSource = bestdoriScript.slice(cardDrawBindingStart, cardDrawBindingEnd);
if ((cardDrawBindingSource.match(/resetCardDrawStatus\(\);/g) || []).length !== 3) {
    throw new Error('Mode, rarity, and character changes must all refresh the Bestdori status.');
}

const originalLinkHelperStart = backgroundManagerScript.indexOf('function syncBackgroundOriginalLink(');
const originalLinkHelperEnd = backgroundManagerScript.indexOf('function syncBackgroundControlsBusy(', originalLinkHelperStart);
if (originalLinkHelperStart < 0 || originalLinkHelperEnd < 0) {
    throw new Error('Unable to extract the original-background link helper.');
}
const originalLinkHelperSource = backgroundManagerScript.slice(originalLinkHelperStart, originalLinkHelperEnd);
if (originalLinkHelperSource.includes('backgroundPreferencesConnection')
    || originalLinkHelperSource.includes('backgroundManagerIsBusy')
    || originalLinkHelperSource.includes('thumbnailUrl')) {
    throw new Error('The original-background link was coupled to API availability, busy state, or thumbnails.');
}
const originalLinkAttributes = new Map();
const originalLinkContext = vm.createContext({
    URL,
    backgroundPreferences: {
        landscape: 'wallpaper.jpg',
        portrait: 'phone.png'
    },
    editingBackgroundMode: 'landscape',
    backgroundModeLabels: {
        landscape: '\u6a2a\u5c4f',
        portrait: '\u7ad6\u5c4f'
    },
    normalizeBackgroundName(value) {
        return typeof value === 'string' && !value.includes('/') ? value : null;
    },
    backgroundUrlFromName(value) {
        return new URL(encodeURIComponent(value), 'https://home.example.test/backgrounds/');
    },
    backgroundOriginalLink: {
        setAttribute(name, value) {
            originalLinkAttributes.set(name, String(value));
        },
        removeAttribute(name) {
            originalLinkAttributes.delete(name);
        }
    }
});
vm.runInContext(
    originalLinkHelperSource
        + '\nglobalThis.__syncBackgroundOriginalLink = syncBackgroundOriginalLink;',
    originalLinkContext
);
originalLinkContext.__syncBackgroundOriginalLink();
if (originalLinkAttributes.get('href') !== 'https://home.example.test/backgrounds/wallpaper.jpg'
    || originalLinkAttributes.get('aria-disabled') !== 'false'
    || !originalLinkAttributes.get('aria-label').includes('\u6a2a\u5c4f')
    || !originalLinkAttributes.get('aria-label').includes('wallpaper.jpg')) {
    throw new Error('The original-background link did not target the saved landscape original.');
}
originalLinkContext.editingBackgroundMode = 'portrait';
originalLinkContext.__syncBackgroundOriginalLink();
if (originalLinkAttributes.get('href') !== 'https://home.example.test/backgrounds/phone.png'
    || !originalLinkAttributes.get('aria-label').includes('\u7ad6\u5c4f')) {
    throw new Error('Switching background mode did not update the original-background link.');
}
originalLinkContext.backgroundPreferences.portrait = '../unsafe.png';
originalLinkContext.__syncBackgroundOriginalLink();
if (originalLinkAttributes.has('href') || originalLinkAttributes.get('aria-disabled') !== 'true') {
    throw new Error('An invalid original-background name did not disable the link.');
}

const bingHelperStart = bingDailyScript.indexOf('function bingDailyErrorMessage(');
const bingHelperEnd = bingDailyScript.indexOf('async function fetchBingDaily(', bingHelperStart);
if (bingHelperStart < 0 || bingHelperEnd < 0) {
    throw new Error('Unable to extract Bing daily response helpers.');
}
const bingContext = vm.createContext({
    URL,
    Error,
    Promise,
    console: { warn() {} },
    normalizeBackgroundName: (value) => typeof value === 'string' && /\.jpg$/i.test(value) ? value : null
});
vm.runInContext(
    bingDailyScript.slice(bingHelperStart, bingHelperEnd)
        + '\nglobalThis.__bingHelpers = { bingDailyErrorMessage, normalizeBingDailyPayload, requestBingDaily, resultMessage };',
    bingContext
);
bingContext.bingDailyEndpoint = { href: 'https://home.example.test/api/background-files/bing-daily' };
let observedBingRequest = null;
const validBingPayload = {
    version: 1,
    results: [
        {
            slot: 'cn',
            market: 'zh-CN',
            status: 'created',
            background: 'bing_cn_20260820_OHR.China_ZH-CN100.jpg'
        },
        {
            slot: 'international',
            market: 'en-US',
            fallback: true,
            status: 'existing',
            background: 'bing_us_20260820_OHR.America_EN-US300.jpg'
        }
    ]
};
bingContext.fetchWithTimeout = async (resource, options, timeout, controller, transform) => {
    observedBingRequest = { resource, options, timeout, controller };
    return transform({ ok: true, status: 200, json: async () => validBingPayload });
};
const normalizedBingResults = await bingContext.__bingHelpers.requestBingDaily();
if (normalizedBingResults.length !== 2
    || normalizedBingResults[0].slot !== 'cn'
    || normalizedBingResults[1].market !== 'en-US'
    || !normalizedBingResults[1].fallback
    || observedBingRequest.resource !== bingContext.bingDailyEndpoint.href
    || observedBingRequest.options.method !== 'POST'
    || observedBingRequest.options.credentials !== 'same-origin'
    || observedBingRequest.options.headers['X-Requested-With'] !== 'homelab-dashboard'
    || observedBingRequest.options.headers['Content-Type'] !== 'application/json'
    || observedBingRequest.options.body !== '{}'
    || observedBingRequest.timeout !== 120000
    || observedBingRequest.controller !== null) {
    throw new Error('Bing daily JSON request or response normalization contract mismatch.');
}
if (!bingContext.__bingHelpers.resultMessage(normalizedBingResults[1]).includes('\u5df2\u56de\u9000 US')) {
    throw new Error('Bing US fallback was not exposed in the user-facing result message.');
}
bingContext.fetchWithTimeout = async (resource, options, timeout, controller, transform) => transform({
    ok: false,
    status: 404,
    json: async () => ({ error: 'not found' })
});
await expectRejection(bingContext.__bingHelpers.requestBingDaily(), 'Old-backend Bing daily rejection');
if (!bingContext.__bingHelpers.bingDailyErrorMessage({}, 404).includes('\u8bf7\u5148\u5347\u7ea7\u540e\u7aef')) {
    throw new Error('Old backend Bing daily failure was not mapped to an upgrade message.');
}
if (!bingContext.__bingHelpers.bingDailyErrorMessage(
    { error: 'request body is not allowed for this route' },
    400
).includes('\u8bf7\u5148\u5347\u7ea7\u540e\u7aef')) {
    throw new Error('Old backend body rejection was not mapped to an upgrade message.');
}

const bingWorkflowStart = bingDailyScript.indexOf('async function fetchBingDaily(');
const bingWorkflowEnd = bingDailyScript.indexOf('function syncAvailability(', bingWorkflowStart);
if (bingWorkflowStart < 0 || bingWorkflowEnd < 0) {
    throw new Error('Unable to extract the Bing daily workflow.');
}
const bingWorkflowSource = bingDailyScript.slice(bingWorkflowStart, bingWorkflowEnd);
if (bingWorkflowSource.includes("requestBackgroundPreferences('PATCH'")
    || bingWorkflowSource.includes('requestBackgroundPreferences(')) {
    throw new Error('Bing daily download must not automatically apply a background preference.');
}
vm.runInContext(
    bingWorkflowSource + '\nglobalThis.__fetchBingDaily = fetchBingDaily;',
    bingContext
);
bingContext.isManagerBusy = () => false;
bingContext.isManagerOnline = () => true;
bingContext.bingDailyPending = false;
bingContext.syncManagerControls = () => {};
bingContext.syncAvailability = () => {};
const bingStatuses = [];
bingContext.setManagerStatus = (message, state) => bingStatuses.push({ message, state });
bingContext.requestBingDaily = async () => [
    {
        slot: 'cn',
        market: 'zh-CN',
        fallback: false,
        status: 'created',
        background: 'bing_cn_20260820_OHR.China_ZH-CN100.jpg'
    },
    {
        slot: 'international',
        market: 'en-US',
        fallback: true,
        status: 'failed',
        error: 'bing_unavailable'
    }
];
let bingScanCount = 0;
let bingScanMessage = '';
bingContext.scanBackgroundDirectory = async (options) => {
    bingScanCount += 1;
    bingScanMessage = options.successMessage;
};
let clearedBingPresentation = 0;
let presentedBingResults = null;
bingContext.clearBackgroundResultPresentation = () => {
    clearedBingPresentation += 1;
};
bingContext.presentBackgroundResults = (results) => {
    presentedBingResults = results;
    return true;
};
bingContext.resultMessage = bingContext.__bingHelpers.resultMessage;
if (!await bingContext.__fetchBingDaily()
    || bingScanCount !== 1
    || !bingScanMessage.includes('\u6bcf\u65e5\u4e00\u56fe\u90e8\u5206\u5b8c\u6210')
    || !bingScanMessage.includes('\u5f53\u524d\u80cc\u666f\u672a\u66f4\u6539')
    || !bingScanMessage.includes('HTTP \u4ee3\u7406')
    || clearedBingPresentation !== 1
    || JSON.stringify(presentedBingResults) !== JSON.stringify({
        createdNames: ['bing_cn_20260820_OHR.China_ZH-CN100.jpg'],
        locateNames: []
    })
    || bingContext.bingDailyPending) {
    throw new Error('Partial Bing daily success did not refresh once, present all created images, and preserve preferences.');
}
bingContext.requestBingDaily = async () => [
    {
        slot: 'cn',
        market: 'zh-CN',
        fallback: false,
        status: 'existing',
        background: 'bing_cn_20260820_OHR.China_ZH-CN100.jpg'
    },
    {
        slot: 'international',
        market: 'ROW',
        fallback: false,
        status: 'existing',
        background: 'bing_row_20260820_OHR.World_ROW200.jpg'
    }
];
presentedBingResults = null;
if (!await bingContext.__fetchBingDaily()
    || clearedBingPresentation !== 2
    || JSON.stringify(presentedBingResults) !== JSON.stringify({
        createdNames: [],
        locateNames: [
            'bing_cn_20260820_OHR.China_ZH-CN100.jpg',
            'bing_row_20260820_OHR.World_ROW200.jpg'
        ]
    })) {
    throw new Error('Existing-only Bing daily results were marked as new instead of being located without highlights.');
}

const bestdoriErrorCases = [
    ['bestdori_operation_in_progress', '\u6b63\u5728\u8fdb\u884c'],
    ['bestdori_card_pool_exhausted', '\u6ca1\u6709\u5c1a\u672a\u62bd\u53d6\u7684\u5361\u9762'],
    ['bestdori_history_unavailable', '\u62bd\u5361\u8bb0\u5f55\u4e0d\u53ef\u7528'],
    ['bestdori_unavailable', '\u65e0\u6cd5\u8fde\u63a5 Bestdori'],
    ['bestdori_invalid_image', '\u56fe\u7247\u65e0\u6548']
];
for (const [errorCode, expectedText] of bestdoriErrorCases) {
    const message = context.__bestdoriHelpers.bestdoriErrorMessage({ error: errorCode }, 409, 'Bestdori');
    if (!message.includes(expectedText) || message.includes(errorCode)) {
        throw new Error(`Bestdori error code ${errorCode} was not mapped to a user-facing message.`);
    }
}

const characterDrawRequest = context.__bestdoriHelpers.buildBestdoriDrawRequest('character', [5, 3, 5], '4');
if (JSON.stringify(characterDrawRequest) !== JSON.stringify({ mode: 'character', rarities: [3, 5], characterId: 4 })) {
    throw new Error('Character draw payload normalization mismatch.');
}
const randomDrawRequest = context.__bestdoriHelpers.buildBestdoriDrawRequest('random', [4, 5], '4');
if (JSON.stringify(randomDrawRequest) !== JSON.stringify({ mode: 'random', rarities: [4, 5] })) {
    throw new Error('Random draw payload must not include a character id.');
}
await expectRejection(
    Promise.resolve().then(() => context.__bestdoriHelpers.buildBestdoriDrawRequest('random', [], null)),
    'Empty Bestdori rarity selection'
);

let observedBestdoriRequest = null;
context.fetchWithTimeout = async (resource, options, timeout, controller, transform) => {
    observedBestdoriRequest = { resource, options, timeout, controller };
    return transform({
        ok: true,
        status: 201,
        json: async () => ({ background: { background: 'drawn.png' }, card: { id: 123 } })
    });
};
context.testBestdoriEndpoint = { href: 'https://home.example.test/api/background-files/draw' };
const bestdoriPayload = await context.__bestdoriHelpers.requestBestdoriJson(
    context.testBestdoriEndpoint,
    characterDrawRequest,
    90000
);
if (bestdoriPayload.background.background !== 'drawn.png'
    || observedBestdoriRequest.resource !== context.testBestdoriEndpoint.href
    || observedBestdoriRequest.options.method !== 'POST'
    || observedBestdoriRequest.options.credentials !== 'same-origin'
    || observedBestdoriRequest.options.headers['X-Requested-With'] !== 'homelab-dashboard'
    || observedBestdoriRequest.timeout !== 90000
    || observedBestdoriRequest.controller !== null
    || JSON.stringify(JSON.parse(observedBestdoriRequest.options.body)) !== JSON.stringify(characterDrawRequest)) {
    throw new Error('Bestdori JSON POST request contract mismatch.');
}
context.fetchWithTimeout = async (resource, options, timeout, controller, transform) => transform({
    ok: false,
    status: 409,
    json: async () => ({ error: { code: 'pool_exhausted', message: '\u6ca1\u6709\u672a\u91cd\u590d\u5361\u9762' } })
});
await expectRejection(
    context.__bestdoriHelpers.requestBestdoriJson(context.testBestdoriEndpoint, randomDrawRequest),
    'Bestdori API error'
);

const drawFunctionStart = bestdoriScript.indexOf('async function drawBestdoriCard(');
const drawFunctionEnd = bestdoriScript.indexOf('async function reconcileBestdoriHistory(', drawFunctionStart);
if (drawFunctionStart < 0 || drawFunctionEnd < 0) {
    throw new Error('Unable to extract the Bestdori draw workflow.');
}
const drawFunctionSource = bestdoriScript.slice(drawFunctionStart, drawFunctionEnd);
if (drawFunctionSource.includes("requestBackgroundPreferences('PATCH'")) {
    throw new Error('A successful card draw must not automatically apply the downloaded background.');
}
vm.runInContext(
    drawFunctionSource + '\nglobalThis.__drawBestdoriCard = drawBestdoriCard;',
    context
);
context.bestdoriDrawEndpoint = { href: 'https://home.example.test/api/background-files/draw' };
context.backgroundPreferencesConnection = 'online';
context.isManagerBusy = () => context.backgroundManagerIsBusy();
context.isManagerOnline = () => context.backgroundPreferencesConnection === 'online';
context.syncManagerControls = () => {};
context.setManagerStatus = () => {};
context.backgroundDrawPending = false;
context.cardCharacterSelect = { value: '4' };
context.selectedCardDrawMode = () => 'character';
context.selectedCardDrawRarities = () => [3, 5];
context.buildBestdoriDrawRequest = context.__bestdoriHelpers.buildBestdoriDrawRequest;
context.syncBackgroundControlsBusy = () => {};
context.syncCardDrawControls = () => {};
context.normalizeBackgroundName = (value) => typeof value === 'string' && value.endsWith('.png') ? value : null;
let observedDrawCall = null;
context.requestBestdoriJson = async (endpoint, body, timeout) => {
    observedDrawCall = { endpoint, body, timeout };
    return {
        background: { background: '\u7c73\u6b47\u5c14_\u4e94\u661f_\u6d4b\u8bd5_123_after_training.png' },
        card: { id: 123, characterName: '\u7c73\u6b47\u5c14', rarity: 5, title: '\u6d4b\u8bd5' }
    };
};
let scanSuccessMessage = '';
context.scanBackgroundDirectory = async (options) => {
    scanSuccessMessage = options.successMessage;
};
let closedAfterDraw = false;
let presentedAfterDraw = null;
let patchCallCount = 0;
context.closeCardDrawDialog = (returnValue, restoreFocus) => {
    closedAfterDraw = returnValue === 'drawn' && restoreFocus === false;
};
context.clearBackgroundResultPresentation = () => {};
context.presentBackgroundResults = (results) => {
    presentedAfterDraw = results;
    return true;
};
context.requestBackgroundPreferences = async () => {
    patchCallCount += 1;
    throw new Error('PATCH must not be called by draw workflow.');
};
context.setCardDrawStatus = () => {};
context.setBackgroundStatus = () => {};
if (!await context.__drawBestdoriCard()
    || observedDrawCall.endpoint !== context.bestdoriDrawEndpoint
    || observedDrawCall.timeout !== 90000
    || JSON.stringify(observedDrawCall.body) !== JSON.stringify({ mode: 'character', rarities: [3, 5], characterId: 4 })
    || !scanSuccessMessage.includes('\u5f53\u524d\u80cc\u666f\u672a\u66f4\u6539')
    || !closedAfterDraw
    || JSON.stringify(presentedAfterDraw) !== JSON.stringify({
        createdNames: ['\u7c73\u6b47\u5c14_\u4e94\u661f_\u6d4b\u8bd5_123_after_training.png']
    })
    || patchCallCount !== 0) {
    throw new Error('Successful Bestdori draw did not refresh/present the new image without applying the background.');
}

let drawErrorStatus = '';
context.backgroundDrawPending = false;
context.requestBestdoriJson = async () => { throw new Error('simulated draw failure'); };
context.setCardDrawStatus = (message, kind) => {
    if (kind === 'error') drawErrorStatus = message;
};
context.setBackgroundStatus = () => {};
if (await context.__drawBestdoriCard() || !drawErrorStatus.includes('simulated draw failure')) {
    throw new Error('Bestdori draw failure was not reported without claiming success.');
}

const resetOpenStart = bestdoriScript.indexOf('function openCardHistoryResetDialog(');
const resetOpenEnd = bestdoriScript.indexOf('function closeCardHistoryResetDialog(', resetOpenStart);
if (resetOpenStart < 0 || resetOpenEnd < 0) {
    throw new Error('Unable to extract Bestdori reset confirmation opener.');
}
vm.runInContext(
    bestdoriScript.slice(resetOpenStart, resetOpenEnd)
        + '\nglobalThis.__openCardHistoryResetDialog = openCardHistoryResetDialog;',
    context
);
context.cardHistoryResetDialog = {
    open: false,
    classList: { add() {} },
    showModal() { this.open = true; },
    setAttribute() { this.open = true; }
};
context.cardHistoryResetStatus = { textContent: '' };
context.cardHistoryResetConfirm = { focus() {} };
context.syncCardHistoryResetControls = () => {};
context.focusWithoutScroll = () => {};
let resetRequestCount = 0;
context.requestBestdoriJson = async () => {
    resetRequestCount += 1;
    return { removed: 1, remaining: 0 };
};
if (!context.__openCardHistoryResetDialog() || !context.cardHistoryResetDialog.open || resetRequestCount !== 0) {
    throw new Error('Opening reset confirmation sent a reset request before the second confirmation.');
}

const resetFunctionStart = bestdoriScript.indexOf('async function resetBestdoriHistory(');
const resetFunctionEnd = bestdoriScript.indexOf("backgroundDrawButton.addEventListener('click'", resetFunctionStart);
if (resetFunctionStart < 0 || resetFunctionEnd < 0) {
    throw new Error('Unable to extract Bestdori history reset workflow.');
}
vm.runInContext(
    bestdoriScript.slice(resetFunctionStart, resetFunctionEnd)
        + '\nglobalThis.__resetBestdoriHistory = resetBestdoriHistory;',
    context
);
context.cardHistoryResetDialogIsOpen = () => true;
context.bestdoriResetEndpoint = { href: 'https://home.example.test/api/bestdori/history/reset' };
context.backgroundDrawPending = false;
context.syncCardHistoryResetControls = () => {};
let resetClosed = false;
context.closeCardHistoryResetDialog = (returnValue) => {
    resetClosed = returnValue === 'reset';
};
context.setCardDrawStatus = () => {};
context.requestBestdoriJson = async (endpoint, body, timeout) => {
    resetRequestCount += 1;
    if (endpoint !== context.bestdoriResetEndpoint
        || JSON.stringify(body) !== '{}'
        || timeout !== 30000) {
        throw new Error('History reset request contract mismatch.');
    }
    return { removed: 7, remaining: 0 };
};
if (!await context.__resetBestdoriHistory() || resetRequestCount !== 1 || !resetClosed) {
    throw new Error('Confirmed Bestdori history reset did not complete exactly once.');
}

if (!backgroundUploadScript.includes('const successfulUploadAutoCloseMs = 1200;')
    || !backgroundUploadScript.includes("session.items.every((item) => item.status === 'success')")
    || !backgroundUploadScript.includes("closeBackgroundUploadDialog('success')")
    || !backgroundUploadScript.includes('presentBackgroundResults({ createdNames: uploadedNames })')
    || !backgroundUploadScript.includes('uploadedNames: []')
    || !backgroundUploadScript.includes('session.uploadedNames.push(result.uploadedName)')
    || !backgroundUploadScript.includes('clearBackgroundResultPresentation()')
    || backgroundUploadScript.includes('lastUploadedName')) {
    throw new Error('Background upload completion is missing batch presentation, aggregation, or delayed all-success close behavior.');
}

const uploadCloseStart = backgroundUploadScript.indexOf('function clearBackgroundUploadAutoClose(');
const uploadCloseEnd = backgroundUploadScript.indexOf('function requestBackgroundUpload(', uploadCloseStart);
if (uploadCloseStart < 0 || uploadCloseEnd < 0) {
    throw new Error('Unable to extract the completed upload dialog close workflow.');
}
let clearedUploadTimer = 0;
let closedUploadValue = '';
let presentedUploadNames = null;
let uploadFallbackFocus = 0;
const uploadCloseDialog = {
    open: true,
    classList: { remove() {} },
    close(value) {
        this.open = false;
        closedUploadValue = value;
    },
    removeAttribute() {}
};
const uploadCloseContext = vm.createContext({
    Array,
    backgroundUploadAutoCloseTimer: 77,
    backgroundUploadSession: {
        running: false,
        uploadedNames: ['first.png', 'second.png']
    },
    backgroundUploadDialog: uploadCloseDialog,
    backgroundUploadDialogIsOpen: () => uploadCloseDialog.open,
    backgroundUploadButton: {},
    focusWithoutScroll() {
        uploadFallbackFocus += 1;
    },
    presentBackgroundResults(results) {
        presentedUploadNames = results;
    },
    window: {
        clearTimeout(timer) {
            clearedUploadTimer = timer;
        }
    }
});
vm.runInContext(
    backgroundUploadScript.slice(uploadCloseStart, uploadCloseEnd)
        + '\nglobalThis.__closeCompletedUpload = closeBackgroundUploadDialog;',
    uploadCloseContext
);
if (!uploadCloseContext.__closeCompletedUpload('success')
    || clearedUploadTimer !== 77
    || closedUploadValue !== 'success'
    || JSON.stringify(presentedUploadNames) !== JSON.stringify({ createdNames: ['first.png', 'second.png'] })
    || uploadFallbackFocus !== 0
    || uploadCloseContext.backgroundUploadSession !== null) {
    throw new Error('Closing a completed upload did not reveal the full successful batch exactly once.');
}

const uploadFunctionStart = backgroundUploadScript.indexOf('function requestBackgroundUpload(');
const uploadFunctionEnd = backgroundUploadScript.indexOf("backgroundUploadButton.addEventListener('click'", uploadFunctionStart);
if (uploadFunctionStart < 0 || uploadFunctionEnd < 0) {
    throw new Error('Unable to extract the background upload implementation.');
}
const uploadFunctionSource = backgroundUploadScript.slice(uploadFunctionStart, uploadFunctionEnd);
if (!uploadFunctionSource.includes('endpoint.searchParams.set(\'name\', file.name)')
    || !uploadFunctionSource.includes("request.open('POST', endpoint.href, true)")
    || !uploadFunctionSource.includes("request.setRequestHeader('Content-Type', 'application/octet-stream')")
    || !uploadFunctionSource.includes("request.setRequestHeader('X-Requested-With', 'homelab-dashboard')")
    || !uploadFunctionSource.includes("request.upload.addEventListener('progress'")
    || !uploadFunctionSource.includes('request.timeout = 60000')
    || !uploadFunctionSource.includes('request.send(file)')
    || !uploadFunctionSource.includes('backgroundUploadRequest.abort()')
    || !uploadFunctionSource.includes("item.retryable && ['failed', 'cancelled'].includes(item.status)")
    || uploadFunctionSource.includes("requestBackgroundPreferences('PATCH'")) {
    throw new Error('Background upload request or no-auto-select contract mismatch.');
}

const uploadQueueStart = backgroundUploadScript.indexOf('async function executeBackgroundUploadQueue(');
const uploadQueueEnd = backgroundUploadScript.indexOf('async function runBackgroundUploadQueue(', uploadQueueStart);
if (uploadQueueStart < 0 || uploadQueueEnd < 0) {
    throw new Error('Unable to extract the sequential background upload queue.');
}
vm.runInContext(
    backgroundUploadScript.slice(uploadQueueStart, uploadQueueEnd)
        + '\nglobalThis.__executeBackgroundUploadQueue = executeBackgroundUploadQueue;',
    context
);

let activeUploads = 0;
let maximumActiveUploads = 0;
const uploadOrder = [];
const settledOrder = [];
const sequentialResult = await context.__executeBackgroundUploadQueue(
    ['first', 'second', 'third'],
    async (item) => {
        activeUploads += 1;
        maximumActiveUploads = Math.max(maximumActiveUploads, activeUploads);
        uploadOrder.push(item);
        await Promise.resolve();
        activeUploads -= 1;
        if (item === 'second') throw Object.assign(new Error('single-file failure'), { isGlobal: false });
        return `${item}-ok`;
    },
    async (item, result, error) => {
        settledOrder.push([item, result, error ? error.message : null]);
    },
    () => false
);
if (maximumActiveUploads !== 1
    || JSON.stringify(uploadOrder) !== JSON.stringify(['first', 'second', 'third'])
    || JSON.stringify(settledOrder.map((entry) => entry[0])) !== JSON.stringify(uploadOrder)
    || sequentialResult.processed !== 3
    || sequentialResult.remaining.length !== 0) {
    throw new Error('Background upload queue is not strictly sequential or did not continue after a per-file failure.');
}

const globalStopResult = await context.__executeBackgroundUploadQueue(
    ['first', 'second', 'third'],
    async (item) => {
        if (item === 'second') throw Object.assign(new Error('global failure'), { isGlobal: true });
        return item;
    },
    async () => {},
    () => false
);
if (globalStopResult.processed !== 2
    || JSON.stringify(Array.from(globalStopResult.remaining)) !== JSON.stringify(['third'])) {
    throw new Error('A global background upload failure did not stop the remaining queue.');
}

const removalRequestStart = backgroundManagerScript.indexOf('async function requestBackgroundRemoval(');
const removalRequestEnd = backgroundManagerScript.indexOf('async function confirmBackgroundDeletion(', removalRequestStart);
if (removalRequestStart < 0 || removalRequestEnd < 0) {
    throw new Error('Unable to extract the background removal request for behavior checks.');
}
vm.runInContext(
    backgroundManagerScript.slice(removalRequestStart, removalRequestEnd)
        + '\nglobalThis.__requestBackgroundRemoval = requestBackgroundRemoval;',
    context
);
context.backgroundRemoveEndpoint = { href: 'https://home.example.test/api/background-files/remove' };
let observedRemovalOptions = null;
context.fetchWithTimeout = async (resource, options, timeout, controller, transform) => {
    observedRemovalOptions = { resource, options, timeout, controller };
    return transform({
        ok: false,
        status: 409,
        json: async () => ({
            error: 'trash_unavailable',
            confirmationToken: 'single-use-token',
            expiresIn: 60
        })
    });
};
const permanentConfirmation = await context.__requestBackgroundRemoval({ background: 'shared.jpg' });
if (!permanentConfirmation.requiresPermanentConfirmation
    || permanentConfirmation.confirmationToken !== 'single-use-token'
    || permanentConfirmation.expiresIn !== 60) {
    throw new Error('Trash failure did not produce the permanent-delete confirmation state.');
}
if (observedRemovalOptions.resource !== context.backgroundRemoveEndpoint.href
    || observedRemovalOptions.options.method !== 'POST'
    || observedRemovalOptions.options.credentials !== 'same-origin'
    || observedRemovalOptions.timeout !== 10000
    || observedRemovalOptions.controller !== null
    || JSON.parse(observedRemovalOptions.options.body).background !== 'shared.jpg') {
    throw new Error('Background removal request contract mismatch.');
}
context.fetchWithTimeout = async (resource, options, timeout, controller, transform) => transform({
    ok: true,
    status: 200,
    json: async () => ({
        deletionMode: 'trash',
        preferences: {
            version: 1,
            landscape: 'bg.jpg',
            portrait: 'bg.jpg',
            updatedAt: '2026-07-24T00:00:00Z'
        }
    })
});
const successfulRemoval = await context.__requestBackgroundRemoval({ background: 'shared.jpg' });
if (successfulRemoval.requiresPermanentConfirmation
    || successfulRemoval.payload.preferences.landscape !== 'bg.jpg') {
    throw new Error('Successful removal did not retain the nested preference response.');
}

const searchSourceStart = searchScript.indexOf("const SEARCH_ENGINE_STORAGE_KEY = 'homelab-search-engine-v1'");
const searchSourceEnd = searchScript.indexOf('export function initializeSearch(', searchSourceStart);
if (searchSourceStart < 0 || searchSourceEnd < 0) {
    throw new Error('Unable to extract the homepage search implementation.');
}
const searchSource = searchScript.slice(searchSourceStart, searchSourceEnd);
if (/\bfetch\s*\(|\bXMLHttpRequest\b|\bsendBeacon\b/.test(searchSource)) {
    throw new Error('Homepage search initialization, input, and engine switching must not send network requests.');
}
if (/localStorage\.(?:getItem|setItem)\s*\(|\bsessionStorage\b/.test(searchSource)
    || !searchSource.includes("window.localStorage.removeItem(SEARCH_ENGINE_STORAGE_KEY)")) {
    throw new Error('Search engine selection must be in-memory only and clear its legacy localStorage key.');
}
const submitBindingIndex = searchSource.indexOf("form.addEventListener('submit'");
const enhancementFlagIndex = searchSource.indexOf("form.dataset.searchEnhanced = 'true'");
const shortcutBindingIndex = searchSource.indexOf("document.addEventListener('keydown', handleGlobalSearchShortcut)");
const engineShortcutBindingIndex = searchSource.indexOf("input.addEventListener('keydown', controller.handleSearchEngineShortcut)");
const engineShortcutAriaIndex = searchSource.indexOf("input.setAttribute('aria-keyshortcuts', 'Alt+J Alt+L')");
if (submitBindingIndex < 0
    || enhancementFlagIndex < 0
    || enhancementFlagIndex < submitBindingIndex
    || shortcutBindingIndex < enhancementFlagIndex
    || engineShortcutBindingIndex < 0
    || engineShortcutBindingIndex > enhancementFlagIndex
    || engineShortcutAriaIndex < enhancementFlagIndex) {
    throw new Error('The search form claims enhancement before its event bindings are installed.');
}
if (!searchSource.includes("event.code !== 'Backquote'")
    || !searchSource.includes('|| !event.altKey')
    || !searchSource.includes("event.getModifierState('AltGraph')")
    || !searchSource.includes("document.querySelector('dialog[open]')")) {
    throw new Error('The public Alt+Backquote shortcut guard is incomplete.');
}
if (!searchSource.includes('const SEARCH_ENGINE_SWITCH_DEBOUNCE_MS = 90;')
    || !searchSource.includes('const SEARCH_ENGINE_TRACK_PHASE_MS = 90;')
    || !searchSource.includes("event.code === 'KeyJ'")
    || !searchSource.includes("event.code === 'KeyL'")
    || !searchSource.includes('activeElement() !== input')
    || !searchSource.includes('queuedEngineIndex === null ? currentIndex : queuedEngineIndex')
    || !searchSource.includes('queuedEngineStepDelta += step')
    || !searchSource.includes('revision !== engineSwitchRevision')
    || !searchSource.includes('queueRevision !== engineSwitchQueueRevision')
    || !searchSource.includes('if (isEngineSwitchAnimationActive()) return targetEngine')) {
    throw new Error('The second-stage Alt+J/L debounce or directional switching contract is incomplete.');
}

const clearedLegacySearchKeys = [];
const searchContext = vm.createContext({
    console: { warn() {} },
    document: { activeElement: null },
    window: {
        setTimeout,
        clearTimeout,
        localStorage: {
            removeItem(key) { clearedLegacySearchKeys.push(key); }
        }
    }
});
vm.runInContext(
    searchSource
        + '\nglobalThis.__searchHelpers = { SEARCH_ENGINE_SWITCH_DEBOUNCE_MS, SEARCH_ENGINE_TRACK_PHASE_MS, SEARCH_EMPTY_PLACEHOLDERS, SEARCH_ENGINES, resolveSearchEngine, clearLegacySearchEnginePreference, isSearchShortcutEditableTarget, shouldHandleSearchFocusShortcut, handleHomepageSearchFocusShortcut, createHomepageSearchController };',
    searchContext
);
const expectedEmptySearchPlaceholders = [
    'Null \u4e5f\u7b97\u95ee\u9898\u5417\uff1f',
    '\u5173\u952e\u8bcd\u4e0d\u4f1a\u81ea\u5df1\u957f\u51fa\u6765\u54e6',
    '\u8fd9\u91cc\u662f\u4e0d\u662f\u8be5\u5199\u70b9\u5565\uff1f',
    '\u641c\u7d22\u6846\u4e5f\u9700\u8981\u4e00\u70b9\u7ebf\u7d22',
    '\u4e92\u8054\u7f51\u8fd8\u5728\u7b49\u4f60\u5f00\u53e3',
    '\u5148\u7ed9\u6211\u4e00\u4e2a\u5173\u952e\u8bcd\u5427',
    '\u7a7a\u6c14\u4e0d\u80fd\u62ff\u6765\u641c\u7d22\u5566',
    '\u641c\u7d22\u5f15\u64ce\u8fd8\u6ca1\u5b66\u4f1a\u8bfb\u5fc3\u672f'
];
if (!Object.isFrozen(searchContext.__searchHelpers.SEARCH_EMPTY_PLACEHOLDERS)
    || JSON.stringify(Array.from(searchContext.__searchHelpers.SEARCH_EMPTY_PLACEHOLDERS))
        !== JSON.stringify(expectedEmptySearchPlaceholders)) {
    throw new Error('The fixed empty-search feedback registry or order is incorrect.');
}
const expectedSearchEngines = [
    ['bing', 'Bing', 'https://www.bing.com/search', 'q', '#search-logo-bing'],
    ['google', 'Google', 'https://www.google.com/search', 'q', '#search-logo-google'],
    ['duckduckgo', 'DuckDuckGo', 'https://duckduckgo.com/', 'q', '#search-logo-duckduckgo'],
    ['baidu', '\u767e\u5ea6', 'https://www.baidu.com/s', 'wd', '#search-logo-baidu'],
    ['bilibili', '\u54d4\u54e9\u54d4\u54e9', 'https://search.bilibili.com/all', 'keyword', '#search-logo-bilibili']
];
const actualSearchEngines = Array.from(searchContext.__searchHelpers.SEARCH_ENGINES, (engine) => [
    engine.id,
    engine.name,
    engine.action,
    engine.queryParameter,
    engine.symbol
]);
if (JSON.stringify(actualSearchEngines) !== JSON.stringify(expectedSearchEngines)) {
    throw new Error('The fixed search engine registry or order is incorrect.');
}
for (const invalidEngineId of ['', '__proto__', 'constructor', 'toString', 'unknown']) {
    if (searchContext.__searchHelpers.resolveSearchEngine(invalidEngineId).id !== 'bing') {
        throw new Error(`Unsafe or unknown search engine id did not fall back to Bing: ${invalidEngineId}`);
    }
}
searchContext.__searchHelpers.clearLegacySearchEnginePreference();
if (JSON.stringify(clearedLegacySearchKeys) !== JSON.stringify(['homelab-search-engine-v1'])) {
    throw new Error('Search initialization did not remove the legacy persisted engine preference.');
}
searchContext.window.localStorage = {
    removeItem() { throw new Error('storage disabled'); }
};
searchContext.__searchHelpers.clearLegacySearchEnginePreference();

function makeSearchShortcutEvent(overrides = {}) {
    let prevented = false;
    const event = {
        code: 'Backquote',
        altKey: true,
        ctrlKey: false,
        shiftKey: false,
        metaKey: false,
        isComposing: false,
        repeat: false,
        target: { closest() { return null; } },
        getModifierState() { return false; },
        preventDefault() { prevented = true; },
        wasPrevented() { return prevented; }
    };
    return Object.assign(event, overrides);
}

for (const targetName of ['body', 'link', 'button']) {
    const shortcutCalls = [];
    const event = makeSearchShortcutEvent({
        target: { targetName, closest() { return null; } }
    });
    const handled = searchContext.__searchHelpers.handleHomepageSearchFocusShortcut(event, {
        setSearchMenuOpen(...args) { shortcutCalls.push(['menu', ...args]); },
        setSearchExpanded(...args) { shortcutCalls.push(['expanded', ...args]); }
    });
    if (!handled
        || !event.wasPrevented()
        || JSON.stringify(shortcutCalls) !== JSON.stringify([
            ['menu', false, false],
            ['expanded', true, true, false]
        ])) {
        throw new Error(`Alt+Backquote did not move search focus from ${targetName}.`);
    }
}

for (const editableSelector of [
    'input',
    'textarea',
    'select',
    '[contenteditable]',
    '[role="textbox"]',
    '[role="combobox"]'
]) {
    const target = {
        closest(selector) {
            if (!selector.includes(editableSelector)) {
                throw new Error(`Editable selector is missing ${editableSelector}.`);
            }
            return {};
        }
    };
    if (!searchContext.__searchHelpers.isSearchShortcutEditableTarget(target)) {
        throw new Error(`Alt+Backquote did not exclude ${editableSelector}.`);
    }
}

const blockedShortcutCases = [
    ['non-Backquote key', { code: 'KeyK' }, false],
    ['missing Alt', { altKey: false }, false],
    ['Control', { ctrlKey: true }, false],
    ['Shift', { shiftKey: true }, false],
    ['Meta', { metaKey: true }, false],
    ['IME composition', { isComposing: true }, false],
    ['key repeat', { repeat: true }, false],
    ['AltGraph', { getModifierState(name) { return name === 'AltGraph'; } }, false],
    ['open dialog', {}, true],
    ['search input', { target: { closest() { return {}; } } }, false]
];
for (const [caseName, overrides, dialogOpen] of blockedShortcutCases) {
    const shortcutCalls = [];
    const event = makeSearchShortcutEvent(overrides);
    const handled = searchContext.__searchHelpers.handleHomepageSearchFocusShortcut(event, {
        setSearchMenuOpen(...args) { shortcutCalls.push(['menu', ...args]); },
        setSearchExpanded(...args) { shortcutCalls.push(['expanded', ...args]); }
    }, dialogOpen);
    if (handled || event.wasPrevented() || shortcutCalls.length) {
        throw new Error(`Alt+Backquote was not ignored for ${caseName}.`);
    }
}

function makeSearchClassList() {
    const values = new Set();
    return {
        add(...items) { items.forEach((item) => values.add(item)); },
        remove(...items) { items.forEach((item) => values.delete(item)); },
        contains(item) { return values.has(item); }
    };
}
let activeSearchElement = null;
const searchElements = [];
function makeSearchElement() {
    const element = {
        dataset: {},
        attributes: {},
        classList: makeSearchClassList(),
        textContent: '',
        hidden: false,
        tabIndex: -1,
        focusCount: 0,
        blurCount: 0,
        offsetWidth: 416,
        setAttribute(name, value) {
            this.attributes[name] = String(value);
            if (name === 'action') this.action = String(value);
            if (name === 'name') this.name = String(value);
        },
        removeAttribute(name) { delete this.attributes[name]; },
        focus() {
            activeSearchElement = this;
            this.focusCount += 1;
        },
        blur() {
            if (activeSearchElement === this) activeSearchElement = null;
            this.blurCount += 1;
        },
        contains(candidate) { return searchElements.includes(candidate); }
    };
    searchElements.push(element);
    return element;
}

const searchForm = makeSearchElement();
const searchInput = makeSearchElement();
searchInput.value = 'keep this query';
searchInput.placeholder = '\u628a\u95ee\u9898\u4e22\u7ed9\u4e92\u8054\u7f51';
const searchEngineButton = makeSearchElement();
const searchEngineIdentity = makeSearchElement();
const searchEngineName = makeSearchElement();
const searchSubmitButton = makeSearchElement();
const searchMenu = makeSearchElement();
searchMenu.hidden = true;
const searchStatus = makeSearchElement();
const searchOptions = expectedSearchEngines.map(([id]) => {
    const option = makeSearchElement();
    option.dataset.searchEngine = id;
    return option;
});
const searchCurrentUses = [makeSearchElement()];
const searchTimers = [];
const searchSchedule = (callback, delay) => {
    const timer = { callback, delay, cancelled: false };
    searchTimers.push(timer);
    return timer;
};
const searchCancelSchedule = (timer) => {
    timer.cancelled = true;
};
const searchController = searchContext.__searchHelpers.createHomepageSearchController({
    form: searchForm,
    input: searchInput,
    engineButton: searchEngineButton,
    engineIdentity: searchEngineIdentity,
    engineName: searchEngineName,
    submitButton: searchSubmitButton,
    menu: searchMenu,
    options: searchOptions,
    status: searchStatus,
    currentEngineUses: searchCurrentUses
}, {
    schedule: searchSchedule,
    cancelSchedule: searchCancelSchedule,
    activeElement: () => activeSearchElement,
    reduceMotion: () => false
});

searchController.applySearchEngine('bing');
if (searchController.currentEngine().id !== 'bing'
    || searchForm.action !== 'https://www.bing.com/search'
    || searchInput.name !== 'q'
    || searchForm.dataset.currentEngine !== 'bing'
    || searchEngineName.textContent !== 'Bing'
    || searchEngineButton.attributes['aria-controls'] !== 'homepage-search-input'
    || searchEngineButton.attributes['aria-expanded'] !== 'false'
    || searchEngineButton.attributes['aria-label'] !== '\u4f7f\u7528 Bing \u641c\u7d22\uff0c\u70b9\u51fb\u6216\u6309 Alt \u52a0\u6ce2\u6d6a\u53f7\u952e\u5c55\u5f00\u641c\u7d22\u6846'
    || Object.prototype.hasOwnProperty.call(searchEngineButton.attributes, 'aria-haspopup')
    || searchInput.value !== 'keep this query') {
    throw new Error('A new search controller did not start from the in-memory Bing default.');
}
searchController.applySearchEngine('baidu');
if (searchForm.action !== 'https://www.baidu.com/s'
    || searchInput.name !== 'wd'
    || searchInput.value !== 'keep this query') {
    throw new Error('In-memory search engine selection changed the query or form contract.');
}
searchController.applySearchEngine('bilibili');
if (searchForm.action !== 'https://search.bilibili.com/all'
    || searchInput.name !== 'keyword'
    || searchEngineName.textContent !== '\u54d4\u54e9\u54d4\u54e9'
    || searchInput.value !== 'keep this query') {
    throw new Error('Bilibili selection changed the query or form contract.');
}

searchController.setSearchExpanded(true);
if (searchEngineButton.attributes['aria-controls'] !== 'search-engine-list'
    || searchEngineButton.attributes['aria-haspopup'] !== 'listbox'
    || searchEngineButton.attributes['aria-expanded'] !== 'false') {
    throw new Error('Expanding search did not convert the persistent pill into the listbox trigger.');
}
searchController.setSearchMenuOpen(true);
if (searchEngineButton.attributes['aria-expanded'] !== 'true') {
    throw new Error('Opening the search engine menu did not update the persistent pill state.');
}
searchController.selectSearchEngine('duckduckgo');
if (!searchController.isExpanded()
    || searchController.isMenuOpen()
    || searchForm.dataset.currentEngine !== 'duckduckgo'
    || activeSearchElement !== searchInput
    || searchInput.value !== 'keep this query') {
    throw new Error('Selecting an engine from the expanded state did not preserve and refocus the query.');
}

searchController.setSearchMenuOpen(true);
searchInput.value = 'preserve shortcut query';
let globalShortcutPrevented = false;
const globalShortcutHandled = searchContext.__searchHelpers.handleHomepageSearchFocusShortcut({
    code: 'Backquote',
    altKey: true,
    ctrlKey: false,
    shiftKey: false,
    metaKey: false,
    isComposing: false,
    repeat: false,
    target: { closest() { return null; } },
    getModifierState() { return false; },
    preventDefault() { globalShortcutPrevented = true; }
}, searchController);
if (!globalShortcutHandled
    || !globalShortcutPrevented
    || searchController.isMenuOpen()
    || !searchController.isExpanded()
    || activeSearchElement !== searchInput
    || searchInput.value !== 'preserve shortcut query') {
    throw new Error('Alt+Backquote did not close the engine menu, focus search, and preserve the current query.');
}

searchController.setSearchMenuOpen(true);
activeSearchElement = searchOptions[0];
let menuPrevented = false;
searchController.handleSearchMenuKeydown({
    key: 'ArrowUp',
    preventDefault() { menuPrevented = true; }
});
if (!menuPrevented || activeSearchElement !== searchOptions[searchOptions.length - 1]) {
    throw new Error('Search engine menu keyboard wrapping is broken.');
}
searchController.handleSearchMenuKeydown({ key: 'Escape', preventDefault() {} });
if (searchController.isMenuOpen() || activeSearchElement !== searchEngineButton) {
    throw new Error('Escape did not close the search engine menu and restore focus.');
}
searchController.setSearchExpanded(false);
searchController.setSearchMenuOpen(true);
if (searchController.isExpanded()
    || searchController.isMenuOpen()
    || searchEngineButton.attributes['aria-controls'] !== 'homepage-search-input'
    || Object.prototype.hasOwnProperty.call(searchEngineButton.attributes, 'aria-haspopup')) {
    throw new Error('The collapsed persistent pill exposed the engine menu instead of the search input.');
}

function createEngineShortcutHarness(reduceMotion = false, timing = null) {
    const form = makeSearchElement();
    const input = makeSearchElement();
    input.value = 'shortcut query';
    const engineButton = makeSearchElement();
    const engineIdentity = makeSearchElement();
    const engineName = makeSearchElement();
    const submitButton = makeSearchElement();
    const menu = makeSearchElement();
    const status = makeSearchElement();
    const options = expectedSearchEngines.map(([id]) => {
        const option = makeSearchElement();
        option.dataset.searchEngine = id;
        return option;
    });
    const timers = [];
    const controller = searchContext.__searchHelpers.createHomepageSearchController({
        form,
        input,
        engineButton,
        engineIdentity,
        engineName,
        submitButton,
        menu,
        options,
        status,
        currentEngineUses: [makeSearchElement()]
    }, {
        schedule(callback, delay) {
            if (timing) return timing.schedule(callback, delay);
            const timer = { callback, delay, cancelled: false };
            timers.push(timer);
            return timer;
        },
        cancelSchedule(timer) {
            if (timing) {
                timing.cancel(timer);
            } else {
                timer.cancelled = true;
            }
        },
        activeElement: () => activeSearchElement,
        reduceMotion: () => reduceMotion
    });
    controller.applySearchEngine('bing');
    controller.setSearchExpanded(true, true, false);
    return {
        controller,
        form,
        input,
        engineIdentity,
        status,
        timers
    };
}

function createDeterministicSearchTiming() {
    let now = 0;
    let nextId = 1;
    const scheduled = [];
    function schedule(callback, delay) {
        const timer = {
            id: nextId++,
            callback,
            delay,
            dueAt: now + delay,
            cancelled: false
        };
        scheduled.push(timer);
        return timer;
    }
    function cancel(timer) {
        timer.cancelled = true;
    }
    function advance(duration) {
        const targetTime = now + duration;
        while (true) {
            const nextTimer = scheduled
                .filter((timer) => !timer.cancelled && timer.dueAt <= targetTime)
                .sort((left, right) => left.dueAt - right.dueAt || left.id - right.id)[0];
            if (!nextTimer) break;
            nextTimer.cancelled = true;
            now = nextTimer.dueAt;
            nextTimer.callback();
        }
        now = targetTime;
    }
    return { schedule, cancel, advance, scheduled, now: () => now };
}

function makeEngineShortcutEvent(code, overrides = {}) {
    let prevented = false;
    const event = {
        code,
        altKey: true,
        ctrlKey: false,
        shiftKey: false,
        metaKey: false,
        isComposing: false,
        repeat: false,
        getModifierState() { return false; },
        preventDefault() { prevented = true; },
        wasPrevented() { return prevented; }
    };
    return Object.assign(event, overrides);
}

const engineShortcutHarness = createEngineShortcutHarness(false);
const firstNextEvent = makeEngineShortcutEvent('KeyL');
const repeatedNextEvent = makeEngineShortcutEvent('KeyL', { repeat: true });
if (!engineShortcutHarness.controller.handleSearchEngineShortcut(firstNextEvent)
    || !engineShortcutHarness.controller.handleSearchEngineShortcut(repeatedNextEvent)
    || !firstNextEvent.wasPrevented()
    || !repeatedNextEvent.wasPrevented()
    || engineShortcutHarness.timers.length !== 2
    || !engineShortcutHarness.timers[0].cancelled
    || engineShortcutHarness.timers[1].delay !== 90
    || engineShortcutHarness.controller.currentEngine().id !== 'bing') {
    throw new Error('Rapid Alt+L input was not merged into one 90ms pending switch.');
}

engineShortcutHarness.timers[1].callback();
if (engineShortcutHarness.form.dataset.engineSwitchDirection !== 'next'
    || engineShortcutHarness.form.dataset.engineSwitchPhase !== 'out'
    || engineShortcutHarness.controller.currentEngine().id !== 'bing'
    || engineShortcutHarness.timers[2].delay !== 90) {
    throw new Error('The merged engine switch did not begin one rightward 180ms track.');
}
engineShortcutHarness.timers[2].callback();
if (engineShortcutHarness.controller.currentEngine().id !== 'duckduckgo'
    || engineShortcutHarness.form.dataset.engineSwitchPhase !== 'in'
    || engineShortcutHarness.input.value !== 'shortcut query'
    || activeSearchElement !== engineShortcutHarness.input
    || engineShortcutHarness.status.textContent !== '\u5df2\u5207\u6362\u5230 DuckDuckGo\u3002'
    || engineShortcutHarness.timers[3].delay !== 90) {
    throw new Error('The merged switch did not apply only its final engine at track midpoint.');
}
engineShortcutHarness.timers[3].callback();
if (Object.prototype.hasOwnProperty.call(engineShortcutHarness.form.dataset, 'engineSwitchPhase')
    || Object.prototype.hasOwnProperty.call(engineShortcutHarness.form.dataset, 'engineSwitchDirection')) {
    throw new Error('The engine track did not clean up after its second 90ms phase.');
}

engineShortcutHarness.controller.applySearchEngine('bilibili');
const wrapNextEvent = makeEngineShortcutEvent('KeyL');
engineShortcutHarness.controller.handleSearchEngineShortcut(wrapNextEvent);
engineShortcutHarness.timers[4].callback();
engineShortcutHarness.timers[5].callback();
engineShortcutHarness.timers[6].callback();
if (!wrapNextEvent.wasPrevented()
    || engineShortcutHarness.controller.currentEngine().id !== 'bing'
    || engineShortcutHarness.form.dataset.currentEngine !== 'bing') {
    throw new Error('Alt+L did not wrap from Bilibili to Bing.');
}

const wrapPreviousEvent = makeEngineShortcutEvent('KeyJ');
engineShortcutHarness.controller.handleSearchEngineShortcut(wrapPreviousEvent);
engineShortcutHarness.timers[7].callback();
if (engineShortcutHarness.form.dataset.engineSwitchDirection !== 'previous') {
    throw new Error('Alt+J did not begin a leftward engine track.');
}
engineShortcutHarness.timers[8].callback();
engineShortcutHarness.timers[9].callback();
if (!wrapPreviousEvent.wasPrevented()
    || engineShortcutHarness.controller.currentEngine().id !== 'bilibili') {
    throw new Error('Alt+J did not wrap from Bing to Bilibili.');
}

engineShortcutHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyJ'));
engineShortcutHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
engineShortcutHarness.timers[11].callback();
if (engineShortcutHarness.controller.currentEngine().id !== 'bilibili'
    || Object.prototype.hasOwnProperty.call(engineShortcutHarness.form.dataset, 'engineSwitchPhase')) {
    throw new Error('Opposing rapid engine shortcuts did not cancel without animation.');
}

const blockedEngineShortcutCases = [
    ['missing Alt', makeEngineShortcutEvent('KeyL', { altKey: false })],
    ['Control', makeEngineShortcutEvent('KeyL', { ctrlKey: true })],
    ['Shift', makeEngineShortcutEvent('KeyL', { shiftKey: true })],
    ['Meta', makeEngineShortcutEvent('KeyL', { metaKey: true })],
    ['IME composition', makeEngineShortcutEvent('KeyL', { isComposing: true })],
    ['AltGraph', makeEngineShortcutEvent('KeyL', { getModifierState(name) { return name === 'AltGraph'; } })],
    ['unrelated key', makeEngineShortcutEvent('KeyK')]
];
for (const [caseName, event] of blockedEngineShortcutCases) {
    activeSearchElement = engineShortcutHarness.input;
    const timerCount = engineShortcutHarness.timers.length;
    if (engineShortcutHarness.controller.handleSearchEngineShortcut(event)
        || event.wasPrevented()
        || engineShortcutHarness.timers.length !== timerCount) {
        throw new Error(`Engine shortcut was not ignored for ${caseName}.`);
    }
}
activeSearchElement = searchEngineButton;
const unfocusedEngineEvent = makeEngineShortcutEvent('KeyL');
if (engineShortcutHarness.controller.handleSearchEngineShortcut(unfocusedEngineEvent)
    || unfocusedEngineEvent.wasPrevented()) {
    throw new Error('Alt+L switched engines while the search input did not own focus.');
}
activeSearchElement = engineShortcutHarness.input;

const cancelPendingEvent = makeEngineShortcutEvent('KeyL');
engineShortcutHarness.controller.handleSearchEngineShortcut(cancelPendingEvent);
const cancelPendingTimer = engineShortcutHarness.timers[engineShortcutHarness.timers.length - 1];
engineShortcutHarness.controller.setSearchExpanded(false, false, false);
if (!cancelPendingTimer.cancelled
    || Object.prototype.hasOwnProperty.call(engineShortcutHarness.form.dataset, 'engineSwitchPhase')) {
    throw new Error('Collapsing search did not cancel a pending engine shortcut and track.');
}

const reducedEngineShortcutHarness = createEngineShortcutHarness(true);
const reducedNextEvent = makeEngineShortcutEvent('KeyL');
reducedEngineShortcutHarness.controller.handleSearchEngineShortcut(reducedNextEvent);
reducedEngineShortcutHarness.timers[0].callback();
if (!reducedNextEvent.wasPrevented()
    || reducedEngineShortcutHarness.controller.currentEngine().id !== 'google'
    || reducedEngineShortcutHarness.timers.length !== 1
    || Object.prototype.hasOwnProperty.call(reducedEngineShortcutHarness.form.dataset, 'engineSwitchPhase')) {
    throw new Error('Reduced motion did not apply the final engine immediately after debounce.');
}

const animationWindowTiming = createDeterministicSearchTiming();
const animationWindowHarness = createEngineShortcutHarness(false, animationWindowTiming);
animationWindowHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
animationWindowTiming.advance(100);
if (animationWindowHarness.form.dataset.engineSwitchPhase !== 'out'
    || animationWindowHarness.controller.currentEngine().id !== 'bing') {
    throw new Error('The deterministic shortcut scenario did not enter the first animation window.');
}
animationWindowHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
animationWindowTiming.advance(100);
animationWindowHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
animationWindowTiming.advance(400);
if (animationWindowHarness.controller.currentEngine().id !== 'baidu'
    || animationWindowHarness.form.dataset.currentEngine !== 'baidu'
    || animationWindowHarness.input.value !== 'shortcut query'
    || activeSearchElement !== animationWindowHarness.input
    || Object.prototype.hasOwnProperty.call(animationWindowHarness.form.dataset, 'engineSwitchPhase')) {
    throw new Error('Three Alt+L presses spanning 100ms animation windows did not accumulate from Bing to Baidu.');
}

const entryWindowTiming = createDeterministicSearchTiming();
const entryWindowHarness = createEngineShortcutHarness(false, entryWindowTiming);
entryWindowHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
entryWindowTiming.advance(190);
if (entryWindowHarness.controller.currentEngine().id !== 'google'
    || entryWindowHarness.form.dataset.engineSwitchPhase !== 'in') {
    throw new Error('The deterministic shortcut scenario did not reach the engine-entry phase.');
}
entryWindowHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
entryWindowTiming.advance(400);
if (entryWindowHarness.controller.currentEngine().id !== 'duckduckgo'
    || Object.prototype.hasOwnProperty.call(entryWindowHarness.form.dataset, 'engineSwitchPhase')) {
    throw new Error('An Alt+L press during engine entry lost its projected target.');
}

const staleFinishTiming = createDeterministicSearchTiming();
const staleFinishHarness = createEngineShortcutHarness(false, staleFinishTiming);
staleFinishHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
staleFinishTiming.advance(190);
const staleFinishTimer = staleFinishTiming.scheduled[staleFinishTiming.scheduled.length - 1];
if (staleFinishHarness.controller.currentEngine().id !== 'google'
    || staleFinishHarness.form.dataset.engineSwitchPhase !== 'in'
    || !staleFinishTimer
    || staleFinishTimer.dueAt !== 270) {
    throw new Error('The stale-finish regression did not reach the expected animation boundary.');
}
staleFinishHarness.controller.setSearchExpanded(false, false, false);
staleFinishHarness.controller.setSearchExpanded(true, true, false);
staleFinishHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
if (!staleFinishTimer.cancelled) {
    throw new Error('A superseded animation finish timer was not cancelled.');
}
staleFinishTimer.callback();
staleFinishTiming.advance(400);
if (staleFinishHarness.controller.currentEngine().id !== 'duckduckgo'
    || Object.prototype.hasOwnProperty.call(staleFinishHarness.form.dataset, 'engineSwitchPhase')) {
    throw new Error('A stale animation finish callback cleared the newer logical engine target.');
}

const continuousTrackTiming = createDeterministicSearchTiming();
const continuousTrackHarness = createEngineShortcutHarness(false, continuousTrackTiming);
continuousTrackHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
continuousTrackTiming.advance(160);
const activeMidpointTimer = continuousTrackTiming.scheduled[continuousTrackTiming.scheduled.length - 1];
if (continuousTrackHarness.form.dataset.engineSwitchPhase !== 'out'
    || !activeMidpointTimer
    || activeMidpointTimer.dueAt !== 180) {
    throw new Error('The continuous-track regression did not reach the outgoing animation phase.');
}
continuousTrackHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
if (continuousTrackHarness.form.dataset.engineSwitchPhase !== 'out'
    || activeMidpointTimer.cancelled) {
    throw new Error('A key during the outgoing phase restarted the visible engine track.');
}
continuousTrackTiming.advance(180);
if (continuousTrackHarness.form.dataset.engineSwitchPhase !== 'out'
    || continuousTrackHarness.controller.currentEngine().id !== 'google') {
    throw new Error('The first engine track did not continue into the accumulated follow-up target.');
}
continuousTrackHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
continuousTrackTiming.advance(400);
if (continuousTrackHarness.controller.currentEngine().id !== 'baidu'
    || Object.prototype.hasOwnProperty.call(continuousTrackHarness.form.dataset, 'engineSwitchPhase')) {
    throw new Error('The uninterrupted animation track did not finish at the latest logical target.');
}

for (const firstInterval of [160, 170, 180, 190, 200]) {
    for (const secondInterval of [160, 170, 180, 190, 200]) {
        const boundaryTiming = createDeterministicSearchTiming();
        const boundaryHarness = createEngineShortcutHarness(false, boundaryTiming);
        boundaryHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
        boundaryTiming.advance(firstInterval);
        boundaryHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
        boundaryTiming.advance(secondInterval);
        boundaryHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyL'));
        boundaryTiming.advance(400);
        if (boundaryHarness.controller.currentEngine().id !== 'baidu'
            || Object.prototype.hasOwnProperty.call(boundaryHarness.form.dataset, 'engineSwitchPhase')) {
            throw new Error(
                `Alt+L boundary intervals ${firstInterval}/${secondInterval}ms did not accumulate to Baidu.`
            );
        }
    }
}

const reverseWindowTiming = createDeterministicSearchTiming();
const reverseWindowHarness = createEngineShortcutHarness(false, reverseWindowTiming);
reverseWindowHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyJ'));
reverseWindowTiming.advance(120);
reverseWindowHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyJ'));
reverseWindowTiming.advance(120);
reverseWindowHarness.controller.handleSearchEngineShortcut(makeEngineShortcutEvent('KeyJ'));
reverseWindowTiming.advance(400);
if (reverseWindowHarness.controller.currentEngine().id !== 'duckduckgo') {
    throw new Error('Three reverse Alt+J presses spanning animation windows did not accumulate correctly.');
}

let previousEmptyFeedbackTimer = null;
const expectedEmptySearchSequence = [
    ...expectedEmptySearchPlaceholders,
    expectedEmptySearchPlaceholders[0]
];
for (const expectedFeedback of expectedEmptySearchSequence) {
    searchInput.value = ' \t ';
    let blankPrevented = false;
    if (searchController.handleSearchSubmit({
        preventDefault() { blankPrevented = true; }
    }) !== false
        || !blankPrevented
        || searchInput.value !== ''
        || searchInput.placeholder !== expectedFeedback
        || searchStatus.textContent !== `\u8bf7\u8f93\u5165\u641c\u7d22\u5185\u5bb9\u3002${expectedFeedback}`
        || !searchForm.classList.contains('is-empty-search')
        || !searchController.isExpanded()
        || activeSearchElement !== searchInput) {
        throw new Error(`Blank search did not show the planned feedback: ${expectedFeedback}`);
    }
    const currentEmptyFeedbackTimer = [...searchTimers]
        .reverse()
        .find((timer) => timer.delay === 1800 && !timer.cancelled);
    if (!currentEmptyFeedbackTimer
        || (previousEmptyFeedbackTimer && !previousEmptyFeedbackTimer.cancelled)) {
        throw new Error('Repeated blank search did not replace the previous feedback timer.');
    }
    previousEmptyFeedbackTimer = currentEmptyFeedbackTimer;
}
previousEmptyFeedbackTimer.callback();
if (searchInput.placeholder !== '\u628a\u95ee\u9898\u4e22\u7ed9\u4e92\u8054\u7f51'
    || searchForm.classList.contains('is-empty-search')) {
    throw new Error('Empty-search feedback did not reset after 1800ms.');
}

const validQuery = '中文 & URL https://example.com/?a=1';
searchInput.value = validQuery;
let validPrevented = false;
if (!searchController.handleSearchSubmit({
    preventDefault() { validPrevented = true; }
}) || validPrevented || searchInput.value !== validQuery) {
    throw new Error('A valid native search submit was blocked or cleared synchronously.');
}
const submitTimer = [...searchTimers].reverse().find((timer) => timer.delay === 0 && !timer.cancelled);
if (!submitTimer) throw new Error('Valid search submit did not schedule post-navigation cleanup.');
submitTimer.callback();
if (searchInput.value !== '' || searchController.isExpanded() || searchController.isMenuOpen()) {
    throw new Error('Post-submit cleanup did not clear and collapse the search form.');
}

searchInput.value = '';
let postSearchBlankPrevented = false;
searchController.handleSearchSubmit({
    preventDefault() { postSearchBlankPrevented = true; }
});
if (!postSearchBlankPrevented
    || searchInput.placeholder !== expectedEmptySearchPlaceholders[1]) {
    throw new Error('A valid search unexpectedly reset the empty-feedback sequence.');
}

searchController.applySearchEngine('bing');
searchController.setSearchExpanded(false);
searchInput.value = '';
let postStateChangeBlankPrevented = false;
searchController.handleSearchSubmit({
    preventDefault() { postStateChangeBlankPrevented = true; }
});
if (!postStateChangeBlankPrevented
    || searchInput.placeholder !== expectedEmptySearchPlaceholders[2]) {
    throw new Error('Engine switching or collapsing unexpectedly reset the empty-feedback sequence.');
}

const resetSearchForm = makeSearchElement();
const resetSearchInput = makeSearchElement();
resetSearchInput.value = '';
const resetSearchStatus = makeSearchElement();
const resetSearchController = searchContext.__searchHelpers.createHomepageSearchController({
    form: resetSearchForm,
    input: resetSearchInput,
    engineButton: makeSearchElement(),
    engineIdentity: makeSearchElement(),
    engineName: makeSearchElement(),
    submitButton: makeSearchElement(),
    menu: makeSearchElement(),
    options: expectedSearchEngines.map(([id]) => {
        const option = makeSearchElement();
        option.dataset.searchEngine = id;
        return option;
    }),
    status: resetSearchStatus,
    currentEngineUses: [makeSearchElement()]
}, {
    schedule: searchSchedule,
    cancelSchedule: searchCancelSchedule,
    activeElement: () => activeSearchElement,
    reduceMotion: () => false
});
let resetBlankPrevented = false;
resetSearchController.handleSearchSubmit({
    preventDefault() { resetBlankPrevented = true; }
});
if (!resetBlankPrevented
    || resetSearchInput.placeholder !== expectedEmptySearchPlaceholders[0]
    || resetSearchStatus.textContent
        !== `\u8bf7\u8f93\u5165\u641c\u7d22\u5185\u5bb9\u3002${expectedEmptySearchPlaceholders[0]}`) {
    throw new Error('A new search controller did not restart the empty-feedback sequence.');
}

const channelHelperStart = channelScript.indexOf('const serviceChannelDescription =');
const channelHelperEnd = channelScript.lastIndexOf('    try {');
if (channelHelperStart < 0 || channelHelperEnd < 0) {
    throw new Error('Unable to extract Stage 2R channel helpers for behavior checks.');
}
const channelHelperSource = channelScript.slice(channelHelperStart, channelHelperEnd);
if (/refreshStatuses|fetchWithTimeout|serviceStatusEndpoint/.test(channelHelperSource)) {
    throw new Error('Channel switching must not initiate or alter service-status requests.');
}
function makeStageOneStyle() {
    return {
        properties: {},
        setProperty(name, value) { this.properties[name] = value; },
        removeProperty(name) { delete this.properties[name]; }
    };
}
function makeClassList() {
    const values = new Set();
    return {
        add(...items) { items.forEach((value) => values.add(value)); },
        remove(...items) { items.forEach((value) => values.delete(value)); },
        contains(value) { return values.has(value); },
        toggle(value, force) {
            if (force === true) values.add(value);
            else if (force === false) values.delete(value);
            else if (values.has(value)) values.delete(value);
            else values.add(value);
            return values.has(value);
        }
    };
}
function makeStageOneElement(extra = {}) {
    return {
        hidden: false,
        disabled: false,
        dataset: {},
        attributes: {},
        classList: makeClassList(),
        style: makeStageOneStyle(),
        textContent: '',
        setAttribute(name, value) { this.attributes[name] = value; },
        removeAttribute(name) { delete this.attributes[name]; },
        toggleAttribute(name, force) {
            if (force) this.attributes[name] = '';
            else delete this.attributes[name];
        },
        focus(options) {
            this.focusCalls = (this.focusCalls || 0) + 1;
            this.lastFocusOptions = options;
        },
        addEventListener(name, handler) {
            this[`${name}Handler`] = handler;
        },
        replaceChildren(...children) {
            this.children = children;
            this.textContent = children.map((child) => (
                typeof child === 'string' ? child : child.textContent
            )).join('');
        },
        ...extra
    };
}
let nextChannelTimerId = 1;
const channelTimers = new Map();
function runChannelTimer(delay) {
    const entry = [...channelTimers.entries()].find(([, timer]) => timer.delay === delay);
    if (!entry) throw new Error(`Missing channel timer for ${delay}ms.`);
    channelTimers.delete(entry[0]);
    entry[1].callback();
}
const channelComputedStyleReads = [];
const channelContext = vm.createContext({
    Error,
    Number,
    console: { warn() {} },
    document: {
        createElement() { return makeStageOneElement(); }
    },
    window: {
        getComputedStyle(element) {
            channelComputedStyleReads.push(element);
            return { columnGap: '12px', opacity: '0' };
        },
        requestAnimationFrame(callback) {
            callback();
            return null;
        },
        cancelAnimationFrame() {},
        addEventListener() {},
        setTimeout(callback, delay) {
            const timerId = nextChannelTimerId++;
            channelTimers.set(timerId, { callback, delay });
            return timerId;
        },
        clearTimeout(timerId) { channelTimers.delete(timerId); }
    }
});
channelContext.cards = [
    makeStageOneElement({ getBoundingClientRect() { return { width: 180 }; } }),
    makeStageOneElement({ getBoundingClientRect() { return { width: 180 }; } })
];
channelContext.quickLinks = Array.from({ length: 5 }, () => makeStageOneElement());
channelContext.serviceCountLabel = makeStageOneElement();
channelContext.refreshButton = makeStageOneElement({
    disabled: true,
    attributes: { 'aria-busy': 'true' }
});
channelContext.lastChecked = makeStageOneElement({ textContent: 'service status' });
channelContext.dashboard = makeStageOneElement();
channelContext.channelSwitchAnchor = makeStageOneElement();
channelContext.channelSwitch = makeStageOneElement({ disabled: true });
channelContext.channelSwitchLabel = { textContent: 'Services' };
channelContext.channelSwitchFaceFront = { textContent: 'Services' };
channelContext.channelSwitchFaceBack = { textContent: 'Quick Links' };
channelContext.channelSwitchMeasureServices = makeStageOneElement({
    getBoundingClientRect() { return { width: 88 }; }
});
channelContext.channelSwitchMeasureQuickLinks = makeStageOneElement({
    getBoundingClientRect() { return { width: 116 }; }
});
channelContext.serviceChannelNote = makeStageOneElement();
channelContext.channelPanelStage = makeStageOneElement();
channelContext.channelPanelRotor = makeStageOneElement();
channelContext.servicesPanel = makeStageOneElement();
channelContext.quickLinksPanel = makeStageOneElement({ hidden: true });
channelContext.serviceGrid = makeStageOneElement({
    getBoundingClientRect() { return { height: 190 }; }
});
channelContext.quickLinksGrid = makeStageOneElement({
    getBoundingClientRect() { return { height: 90 }; }
});
channelContext.dashboardFooterBrand = makeStageOneElement({
    textContent: 'HomeLab \u00b7 Private service gateway'
});
channelContext.channelMotionMedia = {
    matches: true,
    addEventListener(name, handler) {
        if (name === 'change') this.changeHandler = handler;
    }
};
channelContext.channelMobileMotionMedia = {
    matches: false,
    addEventListener(name, handler) {
        if (name === 'change') this.changeHandler = handler;
    }
};
vm.runInContext(
    channelHelperSource
        + '\nconst initializeChannelSwitch = initializeChannelSwitchController;'
        + '\nglobalThis.__channelHelpers = { initializeChannelSwitch, setActiveChannel, configureChannelMotion, finishChannelSwitch, renderChannelNote, quickLinkChannelNotes, restoreStaticServicesChannel };',
    channelContext
);
channelContext.__channelHelpers.initializeChannelSwitch();
if (channelContext.channelSwitch.disabled
    || typeof channelContext.channelSwitch.clickHandler !== 'function'
    || typeof channelContext.channelPanelRotor.animationendHandler !== 'function'
    || !channelContext.dashboard.classList.contains('channel-motion-ready')
    || channelContext.servicesPanel.hidden
    || !channelContext.quickLinksPanel.hidden
    || channelContext.serviceCountLabel.textContent !== '\u0032 \u4e2a\u5bb6\u5ead\u670d\u52a1\u5165\u53e3'
    || channelContext.servicesPanel.attributes['aria-hidden'] !== 'false'
    || Object.prototype.hasOwnProperty.call(channelContext.servicesPanel.attributes, 'inert')
    || channelContext.quickLinksPanel.attributes['aria-hidden'] !== 'true'
    || !Object.prototype.hasOwnProperty.call(channelContext.quickLinksPanel.attributes, 'inert')
    || channelContext.refreshButton.hidden
    || Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')
    || !channelContext.refreshButton.disabled
    || channelContext.refreshButton.attributes['aria-busy'] !== 'true'
    || channelContext.cards[1].style.properties['--channel-card-index'] !== '1'
    || channelContext.quickLinks[4].style.properties['--channel-card-index'] !== '4') {
    throw new Error('Stage 2R channel switch did not initialize its settled Services state.');
}
channelContext.quickLinksGrid.getBoundingClientRect = () => ({ height: 310 });
channelContext.__channelHelpers.configureChannelMotion();
if (channelContext.channelPanelStage.style.properties['--channel-panel-height'] !== '310px'
    || channelContext.servicesPanel.hidden
    || !channelContext.quickLinksPanel.hidden) {
    throw new Error('Stage 2R layout did not size the stage to the taller hidden Quick Links grid.');
}
channelContext.quickLinksGrid.getBoundingClientRect = () => ({ height: 90 });
channelContext.__channelHelpers.configureChannelMotion();
if (channelContext.channelPanelStage.style.properties['--channel-panel-height'] !== '190px'
    || channelContext.servicesPanel.hidden
    || !channelContext.quickLinksPanel.hidden) {
    throw new Error('Stage 2R layout did not restore the Services-dominant stage height after remeasurement.');
}
channelContext.channelSwitch.clickHandler();
if (channelContext.dashboard.dataset.activeChannel !== 'services'
    || channelContext.servicesPanel.hidden
    || channelContext.quickLinksPanel.hidden
    || channelContext.channelSwitch.attributes['aria-disabled'] !== 'true'
    || !channelContext.channelSwitch.classList.contains('is-channel-flipping')
    || !channelContext.channelSwitch.classList.contains('is-switching-to-quick-links')
    || !channelContext.channelPanelRotor.classList.contains('is-channel-flipping')
    || !channelContext.quickLinksPanel.classList.contains('is-channel-incoming')
    || !channelContext.serviceChannelNote.classList.contains('is-note-exiting')
    || !channelContext.serviceCountLabel.classList.contains('is-channel-copy-exiting')
    || !channelContext.refreshButton.classList.contains('is-channel-copy-exiting')
    || !Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')
    || !channelContext.dashboardFooterBrand.classList.contains('is-channel-copy-exiting')
    || !channelContext.lastChecked.classList.contains('is-channel-copy-exiting')
    || !Object.prototype.hasOwnProperty.call(channelContext.servicesPanel.attributes, 'inert')
    || !Object.prototype.hasOwnProperty.call(channelContext.quickLinksPanel.attributes, 'inert')
    || channelContext.channelSwitch.focusCalls !== 1
    || !channelContext.channelSwitch.lastFocusOptions.preventScroll) {
    throw new Error('Stage 2R did not prepare the same-direction Quick Links flip.');
}
if (channelContext.quickLinksGrid.style.properties['--quick-card-width'] !== '180px'
    || channelContext.quickLinksGrid.style.properties['--quick-links-width'] !== '948px'
    || channelContext.channelPanelStage.style.properties['--services-panel-height'] !== '190px'
    || channelContext.channelPanelStage.style.properties['--channel-panel-height'] !== '190px'
    || channelContext.channelSwitchAnchor.style.properties['--channel-switch-services-width'] !== '88px'
    || channelContext.channelSwitchAnchor.style.properties['--channel-switch-quick-links-width'] !== '116px') {
    throw new Error('Stage 2R layout did not preserve measured button, card, and panel dimensions.');
}
channelContext.channelSwitch.clickHandler();
if (!channelContext.channelSwitch.classList.contains('is-switching-to-quick-links')
    || [...channelTimers.values()].filter((timer) => timer.delay === 1200).length !== 1) {
    throw new Error('Stage 2R did not ignore repeated switching or schedule its 1200ms fallback.');
}
runChannelTimer(180);
if (channelContext.serviceChannelNote.textContent !== '\u5927\u5bb6\u4e00\u8d77KIRAKIRA \u2606 DOKIDOKI \uff01'
    || !channelContext.serviceChannelNote.classList.contains('is-note-entering')
    || channelContext.serviceCountLabel.textContent !== '\u0035 \u4e2a\u5feb\u6377\u5165\u53e3'
    || !channelContext.serviceCountLabel.classList.contains('is-channel-copy-entering')) {
    throw new Error('Stage 2R.2 did not reveal the first Quick Links note and Hero count after their 180ms fade-out.');
}
runChannelTimer(200);
if (channelContext.dashboard.dataset.activeChannel !== 'quick-links'
    || channelContext.channelSwitchLabel.textContent !== 'Quick Links'
    || channelContext.channelSwitch.attributes['aria-pressed'] !== 'true'
    || channelContext.serviceCountLabel.textContent !== '\u0035 \u4e2a\u5feb\u6377\u5165\u53e3'
    || !channelContext.refreshButton.hidden
    || !Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')
    || channelContext.refreshButton.classList.contains('is-channel-copy-exiting')
    || !channelContext.dashboardFooterBrand.hidden
    || !channelContext.lastChecked.hidden
    || channelContext.dashboardFooterBrand.classList.contains('is-channel-copy-exiting')
    || channelContext.lastChecked.classList.contains('is-channel-copy-exiting')) {
    throw new Error('Stage 2R did not commit Quick Links chrome at the 200ms panel start.');
}
channelContext.channelPanelRotor.animationendHandler({ animationName: 'channel-flip-left' });
if (!channelContext.servicesPanel.hidden
    || channelContext.quickLinksPanel.hidden
    || channelContext.channelSwitch.attributes['aria-disabled']
    || channelContext.channelSwitch.classList.contains('is-channel-flipping')
    || channelContext.channelSwitchFaceFront.textContent !== 'Quick Links'
    || channelContext.channelSwitchFaceBack.textContent !== 'Services'
    || channelContext.quickLinksPanel.attributes['aria-hidden'] !== 'false'
    || Object.prototype.hasOwnProperty.call(channelContext.quickLinksPanel.attributes, 'inert')
    || channelContext.servicesPanel.attributes['aria-hidden'] !== 'true'
    || !Object.prototype.hasOwnProperty.call(channelContext.servicesPanel.attributes, 'inert')
    || !Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')
    || !channelContext.refreshButton.disabled
    || channelContext.refreshButton.attributes['aria-busy'] !== 'true'
    || channelContext.channelSwitch.focusCalls !== 2
    || channelTimers.size !== 0) {
    throw new Error('Stage 2R did not normalize the completed Quick Links flip without residual state.');
}
channelContext.channelSwitch.clickHandler();
if (!channelContext.channelSwitch.classList.contains('is-switching-to-services')
    || !channelContext.channelPanelRotor.classList.contains('is-channel-flipping')
    || !channelContext.refreshButton.classList.contains('is-channel-copy-exiting')
    || !Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')
    || !channelContext.dashboardFooterBrand.classList.contains('is-channel-copy-exiting')
    || !channelContext.lastChecked.classList.contains('is-channel-copy-exiting')) {
    throw new Error('Stage 2R Services return did not reuse the same left-flip animation.');
}
runChannelTimer(180);
if (channelContext.serviceChannelNote.textContent
        !== '\u72b6\u6001\u8868\u793a\u5165\u53e3\u8282\u70b9\u7ecf Zoraxy \u5230\u4e0a\u6e38\u7684\u670d\u52a1\u7aef\u94fe\u8def\uff0c\u4e0d\u4ee3\u8868\u5e94\u7528\u5185\u90e8\u5065\u5eb7\u3002'
    || channelContext.serviceCountLabel.textContent !== '\u0032 \u4e2a\u5bb6\u5ead\u670d\u52a1\u5165\u53e3'
    || !channelContext.serviceCountLabel.classList.contains('is-channel-copy-entering')) {
    throw new Error('Stage 2R.2 did not restore the Services description and Hero count through the shared fade.');
}
runChannelTimer(200);
if (channelContext.dashboardFooterBrand.hidden
    || channelContext.lastChecked.hidden
    || channelContext.refreshButton.hidden
    || !channelContext.refreshButton.classList.contains('is-channel-copy-entering')
    || !Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')
    || !channelComputedStyleReads.includes(channelContext.refreshButton)
    || !channelComputedStyleReads.includes(channelContext.dashboardFooterBrand)
    || !channelComputedStyleReads.includes(channelContext.lastChecked)
    || !channelContext.dashboardFooterBrand.classList.contains('is-channel-copy-entering')
    || !channelContext.lastChecked.classList.contains('is-channel-copy-entering')
    || channelContext.dashboardFooterBrand.textContent !== 'HomeLab \u00b7 Private service gateway'
    || channelContext.lastChecked.textContent !== 'service status') {
    throw new Error('Stage 2R.2 did not restore both footer side labels through a 240ms fade without overwriting status text.');
}
runChannelTimer(240);
runChannelTimer(240);
if (channelContext.refreshButton.classList.contains('is-channel-copy-entering')
    || Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')
    || !channelContext.refreshButton.disabled
    || channelContext.refreshButton.attributes['aria-busy'] !== 'true') {
    throw new Error('The refresh button did not complete its 240ms Services fade without altering the active refresh state.');
}
channelContext.channelPanelRotor.animationendHandler({ animationName: 'channel-flip-left' });
if (channelContext.serviceCountLabel.classList.contains('is-channel-copy-entering')
    || channelContext.refreshButton.classList.contains('is-channel-copy-entering')
    || channelContext.dashboardFooterBrand.classList.contains('is-channel-copy-entering')
    || channelContext.lastChecked.classList.contains('is-channel-copy-entering')) {
    throw new Error('Stage 2R.2 left auxiliary copy fade classes behind after the Services flip.');
}
channelContext.channelSwitch.clickHandler();
runChannelTimer(180);
if (channelContext.serviceChannelNote.textContent !== '\u30e2\u30ab\u3061\u3083\u3093\u3084\u3063\u3061\u3083\u3046\u3088\uff5e') {
    throw new Error('Stage 2R Quick Links notes did not advance only on the next Quick Links entry.');
}
runChannelTimer(200);
channelContext.channelPanelRotor.animationendHandler({ animationName: 'channel-flip-left' });
const haruhikageNote = channelContext.__channelHelpers.quickLinkChannelNotes[7];
channelContext.__channelHelpers.renderChannelNote(haruhikageNote);
const haruhikageLink = channelContext.serviceChannelNote.children[1];
if (haruhikageLink.textContent !== '\u6625\u65e5\u5f71'
    || haruhikageLink.href !== 'https://www.bilibili.com/video/BV14z42167Uj'
    || haruhikageLink.target !== '_blank'
    || haruhikageLink.rel !== 'noopener noreferrer') {
    throw new Error('Stage 2R Haruhikage note link did not retain its exact safe destination.');
}
channelContext.channelMotionMedia.matches = false;
channelContext.channelMobileMotionMedia.matches = true;
channelContext.__channelHelpers.configureChannelMotion();
if (channelContext.dashboard.classList.contains('channel-motion-ready')
    || !channelContext.dashboard.classList.contains('channel-mobile-motion-ready')) {
    throw new Error('Stage 3 did not select the mobile motion path below 768px.');
}
channelContext.channelSwitch.clickHandler();
if (!channelContext.dashboard.classList.contains('is-mobile-channel-switching')
    || !channelContext.channelSwitch.classList.contains('is-mobile-channel-fading-out')
    || !channelContext.refreshButton.classList.contains('is-channel-copy-exiting')
    || !Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')
    || channelContext.quickLinksPanel.hidden
    || !channelContext.servicesPanel.hidden
    || !Object.prototype.hasOwnProperty.call(channelContext.quickLinksPanel.attributes, 'inert')
    || !Object.prototype.hasOwnProperty.call(channelContext.servicesPanel.attributes, 'inert')) {
    throw new Error('Stage 3 mobile switching did not immediately isolate both channel panels and fade the button.');
}
channelContext.channelSwitch.clickHandler();
if ([...channelTimers.values()].filter((timer) => timer.delay === 1200).length !== 1) {
    throw new Error('Stage 3 mobile switching did not ignore a repeated click.');
}
runChannelTimer(120);
if (!channelContext.quickLinksPanel.classList.contains('is-mobile-channel-outgoing')) {
    throw new Error('Stage 3 mobile outgoing grid did not begin its fade after 120ms.');
}
runChannelTimer(160);
if (!channelContext.channelSwitch.classList.contains('is-mobile-channel-fading-in')
    || !channelContext.channelSwitch.classList.contains('is-mobile-target-services')
    || channelContext.channelSwitchFaceFront.textContent !== 'Services') {
    throw new Error('Stage 3 mobile button did not swap labels while transparent.');
}
runChannelTimer(180);
runChannelTimer(200);
if (channelContext.refreshButton.hidden
    || !channelContext.refreshButton.classList.contains('is-channel-copy-entering')
    || !Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')) {
    throw new Error('The mobile Services return did not reveal the refresh button through its 240ms fade.');
}
runChannelTimer(240);
runChannelTimer(240);
runChannelTimer(300);
if (!channelContext.quickLinksPanel.hidden
    || channelContext.servicesPanel.hidden
    || !channelContext.servicesPanel.classList.contains('is-mobile-channel-incoming')
    || channelContext.servicesPanel.attributes['aria-hidden'] !== 'true'
    || !Object.prototype.hasOwnProperty.call(channelContext.servicesPanel.attributes, 'inert')) {
    throw new Error('Stage 3 mobile incoming cards became interactive before their staggered entrance completed.');
}
runChannelTimer(605);
if (channelContext.dashboard.dataset.activeChannel !== 'services'
    || channelContext.servicesPanel.hidden
    || !channelContext.quickLinksPanel.hidden
    || channelContext.servicesPanel.attributes['aria-hidden'] !== 'false'
    || Object.prototype.hasOwnProperty.call(channelContext.servicesPanel.attributes, 'inert')
    || !Object.prototype.hasOwnProperty.call(channelContext.quickLinksPanel.attributes, 'inert')
    || channelContext.refreshButton.hidden
    || Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')
    || channelContext.channelSwitch.attributes['aria-disabled']
    || channelContext.dashboard.classList.contains('is-mobile-channel-switching')
    || channelContext.channelSwitch.focusCalls !== 8
    || channelTimers.size !== 0) {
    throw new Error('Stage 3 mobile Services entrance did not settle into one interactive active panel.');
}
channelContext.channelSwitch.clickHandler();
if (!channelContext.refreshButton.classList.contains('is-channel-copy-exiting')
    || !Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')) {
    throw new Error('The mobile Quick Links switch did not start the refresh-button fade immediately.');
}
runChannelTimer(120);
runChannelTimer(160);
runChannelTimer(180);
runChannelTimer(200);
runChannelTimer(300);
if (!channelContext.quickLinksPanel.classList.contains('is-mobile-channel-incoming')
    || !channelContext.refreshButton.hidden
    || !Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')
    || channelContext.quickLinksPanel.attributes['aria-hidden'] !== 'true'
    || !Object.prototype.hasOwnProperty.call(channelContext.quickLinksPanel.attributes, 'inert')) {
    throw new Error('Stage 3 mobile Quick Links cards were focusable during their staggered entrance.');
}
runChannelTimer(680);
if (channelContext.dashboard.dataset.activeChannel !== 'quick-links'
    || channelContext.quickLinksPanel.attributes['aria-hidden'] !== 'false'
    || Object.prototype.hasOwnProperty.call(channelContext.quickLinksPanel.attributes, 'inert')
    || channelContext.channelSwitch.focusCalls !== 10
    || channelTimers.size !== 0) {
    throw new Error('Stage 3 mobile Quick Links entrance did not restore interaction after its last card.');
}
channelContext.channelMobileMotionMedia.matches = false;
channelContext.__channelHelpers.configureChannelMotion();
channelContext.channelSwitch.clickHandler();
if (channelContext.servicesPanel.hidden
    || !channelContext.quickLinksPanel.hidden
    || channelContext.refreshButton.hidden
    || Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')
    || channelContext.dashboard.classList.contains('channel-motion-ready')
    || channelContext.dashboard.classList.contains('channel-mobile-motion-ready')
    || channelContext.servicesPanel.attributes['aria-hidden'] !== 'false'
    || Object.prototype.hasOwnProperty.call(channelContext.servicesPanel.attributes, 'inert')
    || channelTimers.size !== 0) {
    throw new Error('Stage 3 reduced-motion fallback did not remain an instant accessible channel switch.');
}
channelContext.__channelHelpers.restoreStaticServicesChannel();
if (!channelContext.channelSwitch.disabled
    || channelContext.dashboard.dataset.activeChannel !== 'services'
    || channelContext.quickLinksPanel.hidden !== true
    || channelContext.refreshButton.hidden
    || Object.prototype.hasOwnProperty.call(channelContext.refreshButton.attributes, 'inert')
    || channelContext.refreshButton.classList.contains('is-channel-copy-exiting')
    || channelContext.refreshButton.classList.contains('is-channel-copy-entering')
    || channelContext.serviceChannelNote.textContent
        !== '\u72b6\u6001\u8868\u793a\u5165\u53e3\u8282\u70b9\u7ecf Zoraxy \u5230\u4e0a\u6e38\u7684\u670d\u52a1\u7aef\u94fe\u8def\uff0c\u4e0d\u4ee3\u8868\u5e94\u7528\u5185\u90e8\u5065\u5eb7\u3002') {
    throw new Error('Stage 2R JavaScript failure fallback did not preserve static Services.');
}

const serviceHelperStart = serviceScript.indexOf('function setCardState(');
const serviceHelperEnd = serviceScript.indexOf("refreshButton.addEventListener('click', refreshStatuses);", serviceHelperStart);
if (serviceHelperStart < 0 || serviceHelperEnd < 0) {
    throw new Error('Unable to extract service status helpers for behavior checks.');
}
const serviceHelperSource = serviceScript.slice(serviceHelperStart, serviceHelperEnd);
if (!/const targets = requestCards\.map\(\(card\) => card\.dataset\.probeUrl \|\| card\.href\);/.test(serviceHelperSource)
    || /\bquickLinks\b/.test(serviceHelperSource)) {
    throw new Error('Service probing must continue to derive its batch exclusively from service cards.');
}
vm.runInContext(
    serviceHelperSource
        + '\nglobalThis.__serviceHelpers = { initializeServiceCards, classifyServiceStatus, serviceStatusLabel, normalizeServiceStatusResult, refreshStatuses, resync };',
    context
);

const statusCases = [
    [200, 'online', '\u670d\u52a1\u7aef\u53ef\u8fbe'],
    [204, 'online', '\u670d\u52a1\u7aef\u53ef\u8fbe'],
    [301, 'online', '\u670d\u52a1\u7aef\u53ef\u8fbe'],
    [302, 'online', '\u670d\u52a1\u7aef\u53ef\u8fbe'],
    [401, 'warning', '\u5f02\u5e38 \u00b7 HTTP 401'],
    [404, 'warning', '\u5f02\u5e38 \u00b7 HTTP 404'],
    [500, 'offline', '\u4e0d\u53ef\u7528 \u00b7 HTTP 500'],
    [502, 'offline', '\u4e0d\u53ef\u7528 \u00b7 HTTP 502'],
    [521, 'offline', '\u4e0d\u53ef\u7528 \u00b7 HTTP 521']
];
for (const [status, expectedState, expectedLabel] of statusCases) {
    const state = context.__serviceHelpers.classifyServiceStatus(status);
    const label = context.__serviceHelpers.serviceStatusLabel(state, status, null);
    if (state !== expectedState || label !== expectedLabel) throw new Error(`HTTP ${status} classification mismatch.`);
}
const redirected = context.__serviceHelpers.serviceStatusLabel('offline', 302, 'redirect_rejected');
if (redirected !== '\u91cd\u5b9a\u5411\u88ab\u62d2\u7edd \u00b7 HTTP 302') throw new Error('Rejected redirect label mismatch.');

function makeCard(href, probeUrl = null) {
    const stateElement = { dataset: {}, label: { textContent: '' }, querySelector() { return this.label; } };
    return {
        href,
        isConnected: true,
        dataset: probeUrl ? { probeUrl } : {},
        stateElement,
        style: {
            properties: {},
            setProperty(name, value) { this.properties[name] = value; }
        },
        querySelector() { return stateElement; }
    };
}
context.cards = [
    makeCard('https://one.home.example.test/'),
    makeCard('https://two.home.example.test/', 'https://two.home.example.test/status')
];
context.document = {
    querySelectorAll(selector) {
        if (selector !== '#service-grid .service-card') throw new Error(`Unexpected service card selector: ${selector}`);
        return context.cards;
    }
};
context.dashboard = { dataset: { activeChannel: 'services' } };
context.requestGeneration = 0;
context.activeController = null;
context.serviceCountLabel = { textContent: '' };
context.refreshButton = { disabled: false, attributes: {}, setAttribute(name, value) { this.attributes[name] = value; } };
context.lastChecked = { textContent: '' };
context.statusSummary = { textContent: '' };
context.serviceStatusEndpoint = { href: 'https://home.example.test/api/service-status' };
context.__serviceHelpers.initializeServiceCards();
if (context.serviceCountLabel.textContent !== '\u0032 \u4e2a\u5bb6\u5ead\u670d\u52a1\u5165\u53e3') {
    throw new Error('Dynamic service count label mismatch.');
}
if (context.cards[0].style.properties['--card-index'] !== '0'
    || context.cards[1].style.properties['--card-index'] !== '1') {
    throw new Error('Service card animation indexes were not assigned from DOM order.');
}
let requestCount = 0;
let observedResource = null;
let observedOptions = null;
let serviceWarning = null;
context.console.warn = (...values) => { serviceWarning = values; };
context.fetchWithTimeout = async (resource, options, timeout, controller, transform) => {
    requestCount += 1;
    observedResource = resource;
    observedOptions = options;
    if (context.lastChecked.textContent !== '\u6b63\u5728\u68c0\u6d4b \u0032 \u4e2a\u5165\u53e3\u2026') {
        throw new Error('In-progress service count label mismatch.');
    }
    if (timeout !== 8000 || !controller || typeof controller.abort !== 'function') {
        throw new Error('Unexpected service API timeout contract.');
    }
    return transform({ ok: true, json: async () => ({ results: [
        { target: 'https://one.home.example.test/', status: 200, latencyMs: 5, error: null },
        { target: 'https://two.home.example.test/status', status: null, latencyMs: 5000, error: 'timeout' }
    ] }) });
};
await context.__serviceHelpers.refreshStatuses();
if (requestCount !== 1 || observedResource !== context.serviceStatusEndpoint.href) throw new Error('Refresh did not send one batch request.');
if (observedOptions.method !== 'POST' || observedOptions.credentials !== 'same-origin' || observedOptions.cache !== 'no-store') {
    throw new Error('Service status request options mismatch.');
}
const requestBody = JSON.parse(observedOptions.body);
if (requestBody.targets[1] !== context.cards[1].dataset.probeUrl) throw new Error('Probe URL override was not used.');
if (context.cards[0].stateElement.dataset.state !== 'online' || context.cards[1].stateElement.dataset.state !== 'offline') {
    throw new Error(`Batch results were not rendered correctly: ${context.cards.map((card) => card.stateElement.dataset.state).join(', ')}; ${serviceWarning?.[1]?.message || 'no warning'}.`);
}

context.fetchWithTimeout = async () => { throw new Error('simulated API failure'); };
await context.__serviceHelpers.refreshStatuses();
if (context.cards.some((card) => card.stateElement.dataset.state !== 'unknown')
    || context.refreshButton.disabled || context.refreshButton.attributes['aria-busy'] !== 'false') {
    throw new Error('API failure did not restore controls and render unknown states.');
}

context.cards = [];
let emptyServiceRequestCount = 0;
context.fetchWithTimeout = async () => {
    emptyServiceRequestCount += 1;
    throw new Error('An empty Services channel must not issue a request.');
};
context.__serviceHelpers.resync({ refresh: false });
await context.__serviceHelpers.refreshStatuses();
if (emptyServiceRequestCount !== 0
    || !context.refreshButton.disabled
    || context.refreshButton.attributes['aria-busy'] !== 'false'
    || context.lastChecked.textContent !== '\u6682\u65e0\u670d\u52a1\u5165\u53e3') {
    throw new Error('Empty Services did not stay request-free with a disabled refresh control.');
}

console.log(`Frontend checks passed: ${inlineScripts.length} inline bootstrap, ${moduleEntries.length + 1} external assets, ${cards.length} cards, ${ids.length} unique ids.`);
})().catch((error) => {
    console.error(error && error.stack ? error.stack : error);
    process.exitCode = 1;
});
'@
    $frontendValidationPath = Join-Path ([IO.Path]::GetTempPath()) "homelab-frontend-validation-$PID-$([guid]::NewGuid().ToString('N')).js"
    try {
        [IO.File]::WriteAllText($frontendValidationPath, $frontendValidation, [Text.UTF8Encoding]::new($false))
        Invoke-CheckedCommand -Name 'frontend HTML and JavaScript' -Command {
            & $node.Source --experimental-vm-modules $frontendValidationPath $indexPath $frontendStylePath @frontendModulePaths
        }
    } finally {
        if (Test-Path -LiteralPath $frontendValidationPath -PathType Leaf) {
            Remove-Item -LiteralPath $frontendValidationPath -Force
        }
    }
    Invoke-CheckedCommand -Name 'icon metadata contracts' -Command {
        & $node.Source --experimental-vm-modules --test (Join-Path $PSScriptRoot 'test-icons-ui.mjs')
    }
    foreach ($notesTest in @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'test-notes-*.mjs' -File)) {
        Invoke-CheckedCommand -Name $notesTest.Name -Command {
            & $node.Source --test $notesTest.FullName
        }
    }
}

$git = Get-Command git -ErrorAction SilentlyContinue
if ($null -eq $git) {
    throw 'Git was not found in PATH.'
}

Push-Location -LiteralPath $projectRoot
try {
    Invoke-CheckedCommand -Name 'unstaged git diff whitespace' -Command {
        & $git.Source diff --check
    }
    Invoke-CheckedCommand -Name 'staged git diff whitespace' -Command {
        & $git.Source diff --cached --check
    }

    Write-Host 'Checking source-control exclusions...'
    $trackedFiles = @(& $git.Source ls-files --cached)
    if ($LASTEXITCODE -ne 0) {
        throw 'git ls-files --cached failed.'
    }
    $untrackedFiles = @(& $git.Source ls-files --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) {
        throw 'git ls-files --others --exclude-standard failed.'
    }
    Assert-RepositorySourceControlPolicy -Paths $trackedFiles -Source 'tracked'
    Assert-RepositorySourceControlPolicy -Paths $untrackedFiles -Source 'untracked'

    Write-Host 'Checking the exact homepage icon exception...'
    if (Test-BlockedRepositoryPath -Path 'assets/icons/homepage-badge.svg') {
        throw 'The official homepage icon must be eligible for source control.'
    }
    foreach ($probe in @(
        'assets/icons/other.svg',
        'assets/icons/homepage-badge.png',
        'assets/other/homepage-badge.svg',
        'output/homepage-badge.svg',
        'output/preview.html'
    )) {
        if (-not (Test-BlockedRepositoryPath -Path $probe)) {
            throw "The homepage icon exception admitted an unrelated asset: $probe"
        }
    }
    & $git.Source check-ignore --no-index --quiet -- 'assets/icons/homepage-badge.svg'
    if ($LASTEXITCODE -ne 1) {
        throw 'The official homepage icon must not match a Git ignore rule.'
    }

    $ignoreProbes = @(
        'backgrounds/verify-probe.jpg',
        'dist/verify-probe.exe',
        'output/verify-probe.svg',
        'output/nested/preview.html',
        'state.json',
        'state.json.bak',
        'state.json.corrupt-verify',
        '.state-verify.tmp',
        'DashboardCards.json',
        'DashboardCards.json.bak',
        'DashboardCards.json.corrupt-verify',
        '.DashboardCards.json.verify.tmp',
        'DashboardFavicon.json',
        'DashboardFavicon.json.bak',
        'DashboardFavicon.json.corrupt-verify',
        '.DashboardFavicon.json.verify.tmp',
        'DashboardPageIcon.json',
        'DashboardPageIcon.json.bak',
        'DashboardPageIcon.json.corrupt-verify',
        '.DashboardPageIcon.json.verify.tmp',
        'service.log',
        'service.log.1',
        'HomeLabWallpaperState.json',
        'HomeLabWallpaperState-data/state.json',
        'HomeLabWallpaperState-data/DashboardCards.json',
        'HomeLabWallpaperState-data/DashboardFavicon.json',
        'temp/verify-probe.svg'
    )
    foreach ($probe in $ignoreProbes) {
        & $git.Source check-ignore --no-index --quiet -- $probe
        if ($LASTEXITCODE -ne 0) {
            throw "Git ignore rule is missing for: $probe"
        }
    }

    Write-Host 'Checking forced-tracking and staged-only regression behavior...'
    $temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    $gitPolicyProbe = Join-Path $temporaryBase "Zoraxy-verify-git-policy-$PID-$([Guid]::NewGuid().ToString('N'))"
    $resolvedProbe = [IO.Path]::GetFullPath($gitPolicyProbe)
    if (-not $resolvedProbe.StartsWith($temporaryBase, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Temporary Git policy probe escaped the temporary directory: $resolvedProbe"
    }
    $null = New-Item -ItemType Directory -Path $resolvedProbe
    try {
        Push-Location -LiteralPath $resolvedProbe
        try {
            & $git.Source init --quiet
            if ($LASTEXITCODE -ne 0) {
                throw 'Unable to initialize temporary Git policy repository.'
            }
            [IO.File]::WriteAllText(
                (Join-Path $resolvedProbe '.gitignore'),
                "/.venv/`n",
                [Text.UTF8Encoding]::new($false)
            )
            $forcedDirectory = Join-Path $resolvedProbe '.venv'
            $null = New-Item -ItemType Directory -Path $forcedDirectory
            [IO.File]::WriteAllBytes((Join-Path $forcedDirectory 'forced.pyc'), [byte[]](1, 2, 3))
            [IO.File]::WriteAllText(
                (Join-Path $resolvedProbe 'DashboardCards.json'),
                '{"version":1}',
                [Text.UTF8Encoding]::new($false)
            )
            [IO.File]::WriteAllText(
                (Join-Path $resolvedProbe 'DashboardFavicon.json'),
                '{"version":1}',
                [Text.UTF8Encoding]::new($false)
            )
            [IO.File]::WriteAllText(
                (Join-Path $resolvedProbe 'staged-only.txt'),
                "trailing whitespace `n",
                [Text.UTF8Encoding]::new($false)
            )
            & $git.Source add -f -- '.venv/forced.pyc' 'DashboardCards.json' 'DashboardFavicon.json' 'staged-only.txt'
            if ($LASTEXITCODE -ne 0) {
                throw 'Unable to stage temporary Git policy probes.'
            }

            $forcedTrackedFiles = @(& $git.Source ls-files --cached)
            if ($LASTEXITCODE -ne 0) {
                throw 'Unable to inspect temporary forced-tracked files.'
            }
            $policyRejected = $false
            try {
                Assert-RepositorySourceControlPolicy -Paths $forcedTrackedFiles -Source 'temporary tracked'
            } catch {
                $policyRejected = $true
            }
            if (-not $policyRejected) {
                throw 'Forced-tracked private or generated files bypassed the repository policy.'
            }

            & $git.Source diff --check
            if ($LASTEXITCODE -ne 0) {
                throw 'Unstaged whitespace check unexpectedly inspected staged-only content.'
            }
            & $git.Source diff --cached --check *> $null
            if ($LASTEXITCODE -eq 0) {
                throw 'Staged whitespace check did not reject staged-only trailing whitespace.'
            }
        }
        finally {
            Pop-Location
        }
    }
    finally {
        if (Test-Path -LiteralPath $resolvedProbe) {
            Remove-Item -LiteralPath $resolvedProbe -Recurse -Force
        }
    }
}
finally {
    Pop-Location
}

if ($SkipPython) {
    Write-Warning 'Python syntax and unit tests were skipped by request.'
} else {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $pythonPath = (Resolve-Path -LiteralPath $venvPython).Path
    } else {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $python) {
            throw 'Python was not found. Create .venv, install Python, or rerun with -SkipPython for static checks only.'
        }
        $pythonPath = $python.Source
    }

    Push-Location -LiteralPath $stateServicePath
    try {
        $pythonSources = @(
            Get-ChildItem -LiteralPath $stateServicePath -Filter '*.py' -File -Recurse |
                Select-Object -ExpandProperty FullName
        )
        Invoke-CheckedCommand -Name 'Python syntax' -Command {
            & $pythonPath -m py_compile @pythonSources
        }
        Invoke-CheckedCommand -Name 'Python unit tests' -Command {
            & $pythonPath -m unittest discover -s . -p 'test_*.py' -v
        }
    }
    finally {
        Pop-Location
    }
}

if ($Build) {
    & (Join-Path $PSScriptRoot 'build-state-service.ps1')
}

Write-Host 'Project verification completed successfully.'
