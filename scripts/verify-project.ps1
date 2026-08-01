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
        'homelabwallpaperstate-data/'
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
        $lower -notin @('backgrounds/bg.jpg', 'assets/default-background.svg')) {
        return $true
    }
    if ($leafName -in @('state.json', 'state.json.bak', 'homelabwallpaperstate.json') -or
        $leafName -like 'state.json.corrupt-*' -or
        $leafName -like '.state-*.tmp' -or
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
    'README.md',
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
$stateServiceTestSource = [IO.File]::ReadAllText((Join-Path $projectRoot 'state-service\test_main.py'))
$readmeSource = [IO.File]::ReadAllText((Join-Path $projectRoot 'README.md'))
$changelogSource = [IO.File]::ReadAllText((Join-Path $projectRoot 'CHANGELOG.md'))
if ($readmeSource.Contains('index.txt')) {
    throw 'README.md must not list the removed index.txt file.'
}
$maintenanceContracts = @(
    @{ Name = 'PyInstaller one-file build'; Source = $buildSource; Marker = '--onefile' },
    @{ Name = 'packaged executable health check'; Source = $buildSource; Marker = '/healthz' },
    @{ Name = 'bounded redirected-process helper'; Source = $buildSource; Marker = 'Invoke-BuildRedirectedProcess' },
    @{ Name = 'asynchronous stdout drain'; Source = $processUtilsSource; Marker = 'ReadToEndAsync' },
    @{ Name = 'complete process-tree termination'; Source = $processUtilsSource; Marker = 'taskkill.exe' },
    @{ Name = 'process timeout exception'; Source = $processUtilsSource; Marker = '[TimeoutException]' },
    @{ Name = 'HTTP connection limit'; Source = $stateServiceSource; Marker = 'MAX_HTTP_CONNECTIONS = 16' },
    @{ Name = 'HTTP request read deadline'; Source = $stateServiceSource; Marker = 'HTTP_REQUEST_READ_DEADLINE_SECONDS = 300.0' },
    @{ Name = 'strict transfer framing'; Source = $stateServiceSource; Marker = 'Transfer-Encoding is not supported' },
    @{ Name = 'request smuggling regression'; Source = $stateServiceTestSource; Marker = 'test_bodyless_routes_reject_unread_bytes_and_request_smuggling' },
    @{ Name = 'slow request regression'; Source = $stateServiceTestSource; Marker = 'test_request_read_deadline_cannot_be_extended_by_trickle_input' },
    @{ Name = 'background file quota'; Source = $stateServiceSource; Marker = 'MAX_BACKGROUND_FILES = 512' },
    @{ Name = 'thumbnail generation budget'; Source = $stateServiceSource; Marker = 'MAX_THUMBNAIL_GENERATION_ATTEMPTS = 16' },
    @{ Name = 'Python unit tests'; Source = $buildSource; Marker = 'unittest discover' },
    @{ Name = 'packaged PATCH compatibility check'; Source = $buildSource; Marker = 'X-Requested-With' },
    @{ Name = 'packaged background upload check'; Source = $buildSource; Marker = '/api/background-files/upload' },
    @{ Name = 'packaged thumbnail inventory check'; Source = $buildSource; Marker = '$backgroundInventory' },
    @{ Name = 'packaged service status check'; Source = $buildSource; Marker = '/api/service-status' },
    @{ Name = 'temporary artifact hash'; Source = $buildSource; Marker = 'temporaryHash' },
    @{ Name = 'release file manifest'; Source = $packageSource; Marker = 'SHA256SUMS.txt' },
    @{ Name = 'release archive checksum'; Source = $packageSource; Marker = 'ArchiveSha256' },
    @{ Name = 'release license notices'; Source = $packageSource; Marker = 'THIRD_PARTY_NOTICES.md' },
    @{ Name = 'Zoraxy Virtual Directory documentation'; Source = $readmeSource; Marker = 'MatchingPath' },
    @{ Name = 'strict v2 configuration documentation'; Source = $readmeSource; Marker = '"version": 2' },
    @{ Name = 'loopback service probe documentation'; Source = $readmeSource; Marker = 'probeConnectAddress' },
    @{ Name = 'CORS removal documentation'; Source = $readmeSource; Marker = 'AstrBot' }
)
foreach ($contract in $maintenanceContracts) {
    if (-not $contract.Source.Contains($contract.Marker)) {
        throw "Missing maintenance contract: $($contract.Name)"
    }
}
foreach ($document in @(
    @{ Name = 'README.md'; Source = $readmeSource },
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

(async () => {
const html = fs.readFileSync(process.argv[2] || process.argv[1], 'utf8');
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map((match) => match[1]);
if (scripts.length !== 2) throw new Error(`Expected 2 inline scripts, found ${scripts.length}.`);
scripts.forEach((source, index) => new vm.Script(source, { filename: `inline-script-${index + 1}.js` }));

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

const quickLinkPanelMatch = html.match(/<div\b[^>]*\bid="quick-links-panel"[^>]*\bhidden[^>]*>([\s\S]*?)<\/div>\s*<\/div>\s*<\/div>\s*<footer\b/);
if (!quickLinkPanelMatch) throw new Error('Missing the hidden static Quick Links panel.');
const quickLinkPanel = quickLinkPanelMatch[1];
const quickLinks = [...quickLinkPanel.matchAll(/<a\b([^>]*)\bclass="quick-link-card"([^>]*)>([\s\S]*?)<\/a>/g)]
    .map((match) => ({ tag: `<a${match[1]}class="quick-link-card"${match[2]}>`, body: match[3] }));
const expectedQuickLinks = [
    ['https://www.bilibili.com/', 'Bilibili'],
    ['https://github.com/', 'GitHub'],
    ['https://mail.google.com/', 'Gmail'],
    ['https://chatgpt.com/', 'ChatGPT'],
    ['https://gemini.google.com/', 'Gemini']
];
if (quickLinks.length !== expectedQuickLinks.length) {
    throw new Error(`Expected ${expectedQuickLinks.length} final Quick Links cards, found ${quickLinks.length}.`);
}
quickLinks.forEach(({ tag, body }, index) => {
    const [expectedHref, expectedName] = expectedQuickLinks[index];
    if (!tag.includes(`href="${expectedHref}"`) || !tag.includes('target="_blank"')) {
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
if (/GitHub (?:Black|Blue|White)|Gemini \u00b7|quick-gemma-gradient/.test(quickLinkPanel)) {
    throw new Error('Unselected Stage 1A icon candidates remain in the final Quick Links panel.');
}
const quickLinkSvgMarkup = quickLinks.map(({ body }) => body).join('');
if (/<(?:script|foreignObject|image)\b/i.test(quickLinkSvgMarkup)
    || /\son[a-z]+\s*=/i.test(quickLinkSvgMarkup)
    || /(?:href|src)\s*=\s*["'](?:https?:|\/\/|data:)/i.test(quickLinkSvgMarkup)) {
    throw new Error('Quick Links inline SVG contains active or external content.');
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
if (html.indexOf('id="background-original-link"') > html.indexOf('id="background-draw-button"')) {
    throw new Error('The original-background link must remain to the left of the Bestdori draw button.');
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
    || !searchInputMatch[0].includes(' required')) {
    const inputTag = searchInputMatch ? searchInputMatch[0] : '';
    throw new Error(`The static Bing search input contract is incomplete: ${JSON.stringify([
        inputTag.includes('type="search"'),
        inputTag.includes('name="q"'),
        inputTag.includes(`placeholder="${defaultSearchPlaceholder}"`),
        inputTag.includes('pattern=".*\\S.*"'),
        inputTag.includes(' required')
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
const searchOptionIds = [...html.matchAll(/\bdata-search-engine="([^"]+)"/g)].map((match) => match[1]);
if (JSON.stringify(searchOptionIds) !== JSON.stringify(['bing', 'google', 'duckduckgo', 'baidu'])) {
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
for (const symbolId of ['search-logo-bing', 'search-logo-google', 'search-logo-duckduckgo', 'search-logo-baidu']) {
    if (!html.includes(`id="${symbolId}"`)) throw new Error(`Missing inline search icon symbol: ${symbolId}`);
}
if (!/<symbol\b[^>]*\bid="search-logo-baidu"[\s\S]*?<path\b[^>]*\bfill="currentColor"/.test(html)) {
    throw new Error('The Baidu search icon must use the high-contrast monochrome foreground color.');
}
if (!/\.search-form\s*\{[^}]*--search-collapsed-width:\s*7rem;[^}]*--search-motion-duration:\s*300ms;/.test(html)
    || !/data-current-engine="duckduckgo"[\s\S]*?--search-collapsed-width:\s*10rem;/.test(html)
    || !/\.search-shell\s*\{[^}]*transition:\s*[^}]*width\s+var\(--search-motion-duration\)\s+cubic-bezier\(0\.2,\s*0\.8,\s*0\.2,\s*1\)/.test(html)
    || !/\.search-form\[data-search-enhanced="true"\]\s+\.search-engine-button\s*\{[^}]*width:\s*calc\(var\(--search-collapsed-width\) - 2px\);[^}]*display:\s*inline-flex;/.test(html)
    || !/\.search-form\[data-search-enhanced="true"\]\s+\.search-engine-button\s*\{[^}]*transition:\s*[^}]*width\s+var\(--search-motion-duration\)[^}]*height\s+var\(--search-motion-duration\)[^}]*margin-left\s+var\(--search-motion-duration\)[^}]*padding\s+var\(--search-motion-duration\)[^}]*gap\s+var\(--search-motion-duration\)/.test(html)
    || !/data-expanded="true"\]\s+\.search-engine-button\s*\{[^}]*width:\s*calc\(var\(--search-collapsed-width\) - 1\.25rem\);/.test(html)
    || !/\.search-engine-name\s*\{[^}]*overflow:\s*visible;[^}]*line-height:\s*1\.35;/.test(html)
    || !/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?transition-duration:\s*0\.01ms\s*!important;[\s\S]*?transition-delay:\s*0ms\s*!important;/.test(html)) {
    throw new Error('The persistent engine pill is missing its 300ms continuous size transition or unclipped text contract.');
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
    '/api/service-status',
    'homelab-background-state-cache-v1',
    'data-background-mode="landscape"',
    'data-background-mode="portrait"',
    'id="background-upload-button"',
    'id="background-delete-button"',
    'id="background-original-link"',
    'id="background-delete-dialog"',
    'id="background-draw-button"',
    'id="card-draw-dialog"',
    'id="card-history-reset-dialog"',
    'fetchWithTimeout',
    'classifyServiceStatus',
    'data-state="warning"',
    'data-probe-url="https://cliproxy.home.c10h14n2o5.com/management.html/"',
    "credentials: 'same-origin'",
    "'Content-Type': 'application/json'",
    'data-state="unknown"',
    'backgroundScanPending',
    'backgroundDrawPending',
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
    "const backgroundLibrarySources = ['ordinary', 'bestdori']",
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
    'afterTransition: focusOption',
    "className = 'background-section-empty'",
    'setBackgroundSectionExpanded(backgroundSourceForName(fileName), true, {',
    'const inventorySummary =',
    "'Content-Type': 'application/octet-stream'",
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

const runtimeScript = scripts[1];
if (runtimeScript.includes('window.open(')) {
    throw new Error('New-tab navigation must use native safe links or forms instead of window.open().');
}
const helperStart = runtimeScript.indexOf('function fetchWithTimeout(');
const helperEnd = runtimeScript.indexOf('function syncBackgroundControlsBusy(', helperStart);
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
    runtimeScript.slice(helperStart, helperEnd)
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

const backgroundManagerHelperStart = runtimeScript.indexOf('function normalizeBackgroundName(');
const backgroundManagerHelperEnd = runtimeScript.indexOf('function backgroundUrlFromName(', backgroundManagerHelperStart);
if (backgroundManagerHelperStart < 0 || backgroundManagerHelperEnd < 0) {
    throw new Error('Unable to extract background file manager helpers for behavior checks.');
}
vm.runInContext(
    runtimeScript.slice(backgroundManagerHelperStart, backgroundManagerHelperEnd)
        + '\nglobalThis.__backgroundManagerHelpers = { normalizeBackgroundName, normalizeBackgroundThumbnailUrl, readBoundedResponseText, normalizeBackgroundInventory, validateBackgroundUploadFile, backgroundModesUsingFile, isBestdoriBackgroundName, backgroundSourceForName };',
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

const ordinaryBackgroundNames = [
    'bg.jpg',
    'vacation.png',
    '\u89d2\u8272_\u4e94\u661f_\u6807\u9898_123_normal.jpg',
    '\u89d2\u8272_\u516d\u661f_\u6807\u9898_123_normal.png',
    '\u89d2\u8272_\u4e94\u661f_\u6807\u9898_normal.png',
    '\u89d2\u8272_\u4e94\u661f_\u6807\u9898_123_unknown.png',
    '\u89d2\u8272_\u4e94\u661f_\u6807\u9898_123_normal (1).png'
];
ordinaryBackgroundNames.forEach((fileName) => {
    if (context.__backgroundManagerHelpers.isBestdoriBackgroundName(fileName)
        || context.__backgroundManagerHelpers.backgroundSourceForName(fileName) !== 'ordinary') {
        throw new Error(`Ordinary background name was misclassified as Bestdori: ${fileName}`);
    }
});

const backgroundSortStart = runtimeScript.indexOf('function mergeAndSortBackgrounds(');
const backgroundSortEnd = runtimeScript.indexOf('async function requestBackgroundInventory(', backgroundSortStart);
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
    runtimeScript.slice(backgroundSortStart, backgroundSortEnd)
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

const renderFunctionStart = runtimeScript.indexOf('function renderBackgroundOptions(');
const renderFunctionEnd = runtimeScript.indexOf('function focusBackgroundOption(', renderFunctionStart);
if (renderFunctionStart < 0 || renderFunctionEnd < 0) {
    throw new Error('Unable to extract the grouped background renderer.');
}
const groupedRendererSource = runtimeScript.slice(renderFunctionStart, renderFunctionEnd);
if (!groupedRendererSource.includes('backgroundLibrarySources.forEach')
    || !groupedRendererSource.includes('backgroundLibraryExpanded[source]')
    || !groupedRendererSource.includes('groupedBackgrounds.ordinary.length')
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

const inventoryRequestStart = runtimeScript.indexOf('async function requestBackgroundInventory(');
const inventoryRequestEnd = runtimeScript.indexOf('async function requestLegacyBackgroundListing(', inventoryRequestStart);
if (inventoryRequestStart < 0 || inventoryRequestEnd < 0) {
    throw new Error('Unable to extract the background inventory request.');
}
vm.runInContext(
    runtimeScript.slice(inventoryRequestStart, inventoryRequestEnd)
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

const scanFunctionStart = runtimeScript.indexOf('async function scanBackgroundDirectory(');
const scanFunctionEnd = runtimeScript.indexOf('function setEditingBackgroundMode(', scanFunctionStart);
if (scanFunctionStart < 0 || scanFunctionEnd < 0) {
    throw new Error('Unable to extract the background scan fallback workflow.');
}
vm.runInContext(
    runtimeScript.slice(scanFunctionStart, scanFunctionEnd)
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
context.renderBackgroundOptions = () => ({ total: 1, ordinary: 1, bestdori: 0 });
context.renderKnownBackgroundFallback = () => ({ total: 1, ordinary: 1, bestdori: 0 });
await context.__scanBackgroundDirectory();
if (legacyListingRequestCount !== 1
    || !observedBackgroundStatuses.some(
        ({ message }) => message.includes('\u539f\u56fe\u52a0\u8f7d\u964d\u7ea7\u6a21\u5f0f')
    )
    || context.backgroundScanPending) {
    throw new Error('Inventory failure did not enter and announce the legacy original-loading fallback.');
}

const transitionHelperStart = runtimeScript.indexOf('function cancelBackgroundSectionTransition(');
const transitionHelperEnd = runtimeScript.indexOf('function focusBackgroundOption(', transitionHelperStart);
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
    backgroundLibrarySources: ['ordinary', 'bestdori'],
    backgroundLibraryExpanded: { ordinary: true, bestdori: true },
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
    runtimeScript.slice(transitionHelperStart, transitionHelperEnd)
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

const bestdoriHelperStart = runtimeScript.indexOf('function backgroundManagerIsBusy(');
const bestdoriHelperEnd = runtimeScript.indexOf('function syncBackgroundControlsBusy(', bestdoriHelperStart);
if (bestdoriHelperStart < 0 || bestdoriHelperEnd < 0) {
    throw new Error('Unable to extract Bestdori request helpers for behavior checks.');
}
vm.runInContext(
    runtimeScript.slice(bestdoriHelperStart, bestdoriHelperEnd)
        + '\nglobalThis.__bestdoriHelpers = { backgroundManagerIsBusy, bestdoriErrorMessage, buildBestdoriDrawRequest, requestBestdoriJson };',
    context
);

context.backgroundScanPending = false;
context.backgroundWritePending = false;
context.backgroundUploadPending = false;
context.backgroundDeletePending = false;
context.backgroundDrawPending = false;
if (context.__bestdoriHelpers.backgroundManagerIsBusy()) {
    throw new Error('Idle background manager was reported as busy.');
}
context.backgroundDrawPending = true;
if (!context.__bestdoriHelpers.backgroundManagerIsBusy()) {
    throw new Error('Bestdori work was not included in the shared background-manager busy state.');
}
context.backgroundDrawPending = false;

const cardDrawStatusHelperStart = runtimeScript.indexOf('function setCardDrawStatus(');
const cardDrawStatusHelperEnd = runtimeScript.indexOf('function syncCardDrawControls(', cardDrawStatusHelperStart);
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
    runtimeScript.slice(cardDrawStatusHelperStart, cardDrawStatusHelperEnd)
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

const cardDrawCloseStart = runtimeScript.indexOf('function closeCardDrawDialog(');
const cardDrawCloseEnd = runtimeScript.indexOf('async function openCardDrawDialog(', cardDrawCloseStart);
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
    runtimeScript.slice(cardDrawCloseStart, cardDrawCloseEnd)
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

const cardDrawBindingStart = runtimeScript.indexOf('cardDrawModeInputs.forEach((input) => {');
const cardDrawBindingEnd = runtimeScript.indexOf("cardHistoryReconcile.addEventListener('click'", cardDrawBindingStart);
if (cardDrawBindingStart < 0 || cardDrawBindingEnd < 0) {
    throw new Error('Unable to inspect Bestdori form-change bindings.');
}
const cardDrawBindingSource = runtimeScript.slice(cardDrawBindingStart, cardDrawBindingEnd);
if ((cardDrawBindingSource.match(/resetCardDrawStatus\(\);/g) || []).length !== 3) {
    throw new Error('Mode, rarity, and character changes must all refresh the Bestdori status.');
}

const originalLinkHelperStart = runtimeScript.indexOf('function syncBackgroundOriginalLink(');
const originalLinkHelperEnd = runtimeScript.indexOf('function syncBackgroundControlsBusy(', originalLinkHelperStart);
if (originalLinkHelperStart < 0 || originalLinkHelperEnd < 0) {
    throw new Error('Unable to extract the original-background link helper.');
}
const originalLinkHelperSource = runtimeScript.slice(originalLinkHelperStart, originalLinkHelperEnd);
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

const drawFunctionStart = runtimeScript.indexOf('async function drawBestdoriCard(');
const drawFunctionEnd = runtimeScript.indexOf('async function reconcileBestdoriHistory(', drawFunctionStart);
if (drawFunctionStart < 0 || drawFunctionEnd < 0) {
    throw new Error('Unable to extract the Bestdori draw workflow.');
}
const drawFunctionSource = runtimeScript.slice(drawFunctionStart, drawFunctionEnd);
if (drawFunctionSource.includes("requestBackgroundPreferences('PATCH'")) {
    throw new Error('A successful card draw must not automatically apply the downloaded background.');
}
vm.runInContext(
    drawFunctionSource + '\nglobalThis.__drawBestdoriCard = drawBestdoriCard;',
    context
);
context.bestdoriDrawEndpoint = { href: 'https://home.example.test/api/background-files/draw' };
context.backgroundPreferencesConnection = 'online';
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
let focusedAfterDraw = '';
let patchCallCount = 0;
context.closeCardDrawDialog = (returnValue, restoreFocus) => {
    closedAfterDraw = returnValue === 'drawn' && restoreFocus === false;
};
context.focusBackgroundOption = (name) => {
    focusedAfterDraw = name;
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
    || focusedAfterDraw !== '\u7c73\u6b47\u5c14_\u4e94\u661f_\u6d4b\u8bd5_123_after_training.png'
    || patchCallCount !== 0) {
    throw new Error('Successful Bestdori draw did not refresh/focus without applying the background.');
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

const resetOpenStart = runtimeScript.indexOf('function openCardHistoryResetDialog(');
const resetOpenEnd = runtimeScript.indexOf('function closeCardHistoryResetDialog(', resetOpenStart);
if (resetOpenStart < 0 || resetOpenEnd < 0) {
    throw new Error('Unable to extract Bestdori reset confirmation opener.');
}
vm.runInContext(
    runtimeScript.slice(resetOpenStart, resetOpenEnd)
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

const resetFunctionStart = runtimeScript.indexOf('async function resetBestdoriHistory(');
const resetFunctionEnd = runtimeScript.indexOf('function backgroundDeleteDialogIsOpen(', resetFunctionStart);
if (resetFunctionStart < 0 || resetFunctionEnd < 0) {
    throw new Error('Unable to extract Bestdori history reset workflow.');
}
vm.runInContext(
    runtimeScript.slice(resetFunctionStart, resetFunctionEnd)
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

const uploadFunctionStart = runtimeScript.indexOf('async function uploadBackgroundFile(');
const uploadFunctionEnd = runtimeScript.indexOf('function backgroundDeleteDialogIsOpen(', uploadFunctionStart);
if (uploadFunctionStart < 0 || uploadFunctionEnd < 0) {
    throw new Error('Unable to extract the background upload implementation.');
}
const uploadFunctionSource = runtimeScript.slice(uploadFunctionStart, uploadFunctionEnd);
if (!uploadFunctionSource.includes('endpoint.searchParams.set(\'name\', file.name)')
    || !uploadFunctionSource.includes("method: 'POST'")
    || !uploadFunctionSource.includes("'Content-Type': 'application/octet-stream'")
    || !uploadFunctionSource.includes('}, 60000, null')
    || uploadFunctionSource.includes("requestBackgroundPreferences('PATCH'")) {
    throw new Error('Background upload request or no-auto-select contract mismatch.');
}

const removalRequestStart = runtimeScript.indexOf('async function requestBackgroundRemoval(');
const removalRequestEnd = runtimeScript.indexOf('async function confirmBackgroundDeletion(', removalRequestStart);
if (removalRequestStart < 0 || removalRequestEnd < 0) {
    throw new Error('Unable to extract the background removal request for behavior checks.');
}
vm.runInContext(
    runtimeScript.slice(removalRequestStart, removalRequestEnd)
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

const searchSourceStart = runtimeScript.indexOf("const SEARCH_ENGINE_STORAGE_KEY = 'homelab-search-engine-v1'");
const searchSourceEnd = runtimeScript.indexOf('const homepageSearchForm', searchSourceStart);
if (searchSourceStart < 0 || searchSourceEnd < 0) {
    throw new Error('Unable to extract the homepage search implementation.');
}
const searchSource = runtimeScript.slice(searchSourceStart, searchSourceEnd);
if (/\bfetch\s*\(|\bXMLHttpRequest\b|\bsendBeacon\b/.test(searchSource)) {
    throw new Error('Homepage search initialization, input, and engine switching must not send network requests.');
}
const submitBindingIndex = searchSource.indexOf("form.addEventListener('submit'");
const enhancementFlagIndex = searchSource.indexOf("form.dataset.searchEnhanced = 'true'");
if (submitBindingIndex < 0
    || enhancementFlagIndex < 0
    || enhancementFlagIndex < submitBindingIndex) {
    throw new Error('The search form claims enhancement before its event bindings are installed.');
}

const searchContext = vm.createContext({
    console: { warn() {} },
    document: { activeElement: null },
    window: { setTimeout, clearTimeout }
});
vm.runInContext(
    searchSource
        + '\nglobalThis.__searchHelpers = { SEARCH_EMPTY_PLACEHOLDERS, SEARCH_ENGINES, resolveSearchEngine, readStoredSearchEngineId, storeSearchEngineId, createHomepageSearchController };',
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
    ['baidu', '\u767e\u5ea6', 'https://www.baidu.com/s', 'wd', '#search-logo-baidu']
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
if (searchContext.__searchHelpers.readStoredSearchEngineId({
    getItem() { throw new Error('storage disabled'); }
}) !== 'bing') {
    throw new Error('A storage read failure did not fall back to Bing.');
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
const searchStorageValues = new Map([[`homelab-search-engine-v1`, 'google']]);
const searchStorageWrites = [];
const searchStorage = {
    getItem(key) { return searchStorageValues.has(key) ? searchStorageValues.get(key) : null; },
    setItem(key, value) {
        searchStorageWrites.push([key, value]);
        searchStorageValues.set(key, value);
    }
};
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
    engineName: searchEngineName,
    submitButton: searchSubmitButton,
    menu: searchMenu,
    options: searchOptions,
    status: searchStatus,
    currentEngineUses: searchCurrentUses
}, {
    storage: searchStorage,
    schedule: searchSchedule,
    cancelSchedule: searchCancelSchedule,
    activeElement: () => activeSearchElement
});

searchController.applySearchEngine(
    searchContext.__searchHelpers.readStoredSearchEngineId(searchStorage)
);
if (searchController.currentEngine().id !== 'google'
    || searchForm.action !== 'https://www.google.com/search'
    || searchInput.name !== 'q'
    || searchForm.dataset.currentEngine !== 'google'
    || searchEngineName.textContent !== 'Google'
    || searchEngineButton.attributes['aria-controls'] !== 'homepage-search-input'
    || searchEngineButton.attributes['aria-expanded'] !== 'false'
    || Object.prototype.hasOwnProperty.call(searchEngineButton.attributes, 'aria-haspopup')
    || searchInput.value !== 'keep this query') {
    throw new Error('Stored search engine initialization changed the query or applied the wrong form contract.');
}
searchController.applySearchEngine('baidu', true);
if (searchForm.action !== 'https://www.baidu.com/s'
    || searchInput.name !== 'wd'
    || searchInput.value !== 'keep this query'
    || JSON.stringify(searchStorageWrites) !== JSON.stringify([['homelab-search-engine-v1', 'baidu']])) {
    throw new Error('Search engine selection was not safely persisted as an id-only preference.');
}
searchContext.__searchHelpers.storeSearchEngineId({
    setItem() { throw new Error('storage disabled'); }
}, 'google');

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
    activeElement: () => activeSearchElement
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

const channelHelperStart = runtimeScript.indexOf('const serviceChannelDescription =');
const channelHelperEnd = runtimeScript.indexOf('function setCardState(', channelHelperStart);
if (channelHelperStart < 0 || channelHelperEnd < 0) {
    throw new Error('Unable to extract Stage 2R channel helpers for behavior checks.');
}
const channelHelperSource = runtimeScript.slice(channelHelperStart, channelHelperEnd);
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

const serviceHelperStart = runtimeScript.indexOf('function setCardState(');
const serviceHelperEnd = runtimeScript.indexOf('initializeServiceCards();', serviceHelperStart);
if (serviceHelperStart < 0 || serviceHelperEnd < 0) {
    throw new Error('Unable to extract service status helpers for behavior checks.');
}
const serviceHelperSource = runtimeScript.slice(serviceHelperStart, serviceHelperEnd);
if (!/const targets = cards\.map\(\(card\) => card\.dataset\.probeUrl \|\| card\.href\);/.test(serviceHelperSource)
    || /\bquickLinks\b/.test(serviceHelperSource)) {
    throw new Error('Service probing must continue to derive its batch exclusively from service cards.');
}
vm.runInContext(
    serviceHelperSource
        + '\nglobalThis.__serviceHelpers = { initializeServiceCards, classifyServiceStatus, serviceStatusLabel, normalizeServiceStatusResult, refreshStatuses };',
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
context.fetchWithTimeout = async (resource, options, timeout, controller, transform) => {
    requestCount += 1;
    observedResource = resource;
    observedOptions = options;
    if (context.lastChecked.textContent !== '\u6b63\u5728\u68c0\u6d4b \u0032 \u4e2a\u5165\u53e3\u2026') {
        throw new Error('In-progress service count label mismatch.');
    }
    if (timeout !== 8000 || controller !== null) throw new Error('Unexpected service API timeout contract.');
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
    throw new Error('Batch results were not rendered correctly.');
}

context.fetchWithTimeout = async () => { throw new Error('simulated API failure'); };
await context.__serviceHelpers.refreshStatuses();
if (context.cards.some((card) => card.stateElement.dataset.state !== 'unknown')
    || context.refreshButton.disabled || context.refreshButton.attributes['aria-busy'] !== 'false') {
    throw new Error('API failure did not restore controls and render unknown states.');
}

console.log(`Frontend checks passed: ${scripts.length} scripts, ${cards.length} cards, ${ids.length} unique ids.`);
})().catch((error) => {
    console.error(error && error.stack ? error.stack : error);
    process.exitCode = 1;
});
'@
    $frontendValidationPath = Join-Path ([IO.Path]::GetTempPath()) "homelab-frontend-validation-$PID-$([guid]::NewGuid().ToString('N')).js"
    try {
        [IO.File]::WriteAllText($frontendValidationPath, $frontendValidation, [Text.UTF8Encoding]::new($false))
        Invoke-CheckedCommand -Name 'frontend HTML and JavaScript' -Command {
            & $node.Source $frontendValidationPath $indexPath
        }
    } finally {
        if (Test-Path -LiteralPath $frontendValidationPath -PathType Leaf) {
            Remove-Item -LiteralPath $frontendValidationPath -Force
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

    $ignoreProbes = @(
        'backgrounds/verify-probe.jpg',
        'dist/verify-probe.exe',
        'state.json',
        'state.json.bak',
        'state.json.corrupt-verify',
        '.state-verify.tmp',
        'service.log',
        'service.log.1',
        'HomeLabWallpaperState.json',
        'HomeLabWallpaperState-data/state.json',
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
                (Join-Path $resolvedProbe 'staged-only.txt'),
                "trailing whitespace `n",
                [Text.UTF8Encoding]::new($false)
            )
            & $git.Source add -f -- '.venv/forced.pyc' 'staged-only.txt'
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
        Invoke-CheckedCommand -Name 'Python syntax' -Command {
            & $pythonPath -m py_compile .\main.py .\test_main.py
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
