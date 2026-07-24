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

$requiredFiles = @(
    'index.html',
    'README.md',
    'CHANGELOG.md',
    '.gitignore',
    'backgrounds\.gitignore',
    'requirements.txt',
    'state-service\main.py',
    'state-service\test_main.py',
    'scripts\build-state-service.ps1',
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
$readmeSource = [IO.File]::ReadAllText((Join-Path $projectRoot 'README.md'))
$changelogSource = [IO.File]::ReadAllText((Join-Path $projectRoot 'CHANGELOG.md'))
$maintenanceContracts = @(
    @{ Name = 'PyInstaller one-file build'; Source = $buildSource; Marker = '--onefile' },
    @{ Name = 'packaged executable health check'; Source = $buildSource; Marker = '/healthz' },
    @{ Name = 'Python unit tests'; Source = $buildSource; Marker = 'unittest discover' },
    @{ Name = 'packaged PATCH compatibility check'; Source = $buildSource; Marker = 'X-Requested-With' },
    @{ Name = 'packaged service status check'; Source = $buildSource; Marker = '/api/service-status' },
    @{ Name = 'temporary artifact hash'; Source = $buildSource; Marker = 'temporaryHash' },
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
    @{ Name = 'CHANGELOG.md'; Source = $changelogSource }
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
const html = fs.readFileSync(process.argv[2], 'utf8');
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

const requiredMarkers = [
    '/api/background-preferences',
    '/api/service-status',
    'homelab-background-state-cache-v1',
    'data-background-mode="landscape"',
    'data-background-mode="portrait"',
    'fetchWithTimeout',
    'classifyServiceStatus',
    'data-state="warning"',
    'data-probe-url="https://cliproxy.home.c10h14n2o5.com/management.html/"',
    "credentials: 'same-origin'",
    "'Content-Type': 'application/json'",
    'data-state="unknown"',
    'backgroundScanPending',
    'reconcileSharedBackgroundPreference',
    'id="service-count-label"',
    'initializeServiceCards',
    "'X-Requested-With': 'homelab-dashboard'"
];
requiredMarkers.forEach((marker) => {
    if (!html.includes(marker)) throw new Error(`Missing frontend contract marker: ${marker}`);
});

const runtimeScript = scripts[1];
const helperStart = runtimeScript.indexOf('function fetchWithTimeout(');
const helperEnd = runtimeScript.indexOf('function syncBackgroundControlsBusy(', helperStart);
if (helperStart < 0 || helperEnd < 0) throw new Error('Unable to extract timeout helpers for behavior checks.');

const context = vm.createContext({
    AbortController,
    console: { warn() {} },
    Error,
    Promise,
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

const serviceHelperStart = runtimeScript.indexOf('function setCardState(');
const serviceHelperEnd = runtimeScript.indexOf('initializeServiceCards();', serviceHelperStart);
if (serviceHelperStart < 0 || serviceHelperEnd < 0) {
    throw new Error('Unable to extract service status helpers for behavior checks.');
}
vm.runInContext(
    runtimeScript.slice(serviceHelperStart, serviceHelperEnd)
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
    $encodedValidation = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($frontendValidation))
    $nodeBootstrap = "eval(Buffer.from(process.argv[1], 'base64').toString('utf8'))"
    Invoke-CheckedCommand -Name 'frontend HTML and JavaScript' -Command {
        & $node.Source -e $nodeBootstrap $encodedValidation $indexPath
    }
}

$git = Get-Command git -ErrorAction SilentlyContinue
if ($null -eq $git) {
    throw 'Git was not found in PATH.'
}

Push-Location -LiteralPath $projectRoot
try {
    Invoke-CheckedCommand -Name 'git diff whitespace' -Command {
        & $git.Source diff --check
    }

    Write-Host 'Checking source-control exclusions...'
    $candidateFiles = @(& $git.Source ls-files --cached --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) {
        throw 'git ls-files failed.'
    }
    $blockedMediaExtensions = @('.exe', '.jpg', '.jpeg', '.png', '.webp', '.avif', '.gif', '.svg')
    foreach ($candidateFile in $candidateFiles) {
        $normalized = $candidateFile.Replace('\', '/')
        $extension = [IO.Path]::GetExtension($normalized).ToLowerInvariant()
        $leafName = [IO.Path]::GetFileName($normalized).ToLowerInvariant()
        if ($normalized.StartsWith('dist/', [StringComparison]::OrdinalIgnoreCase) -or
            ($blockedMediaExtensions -contains $extension -and $normalized -ne 'backgrounds/bg.jpg') -or
            $leafName -in @('state.json', 'state.json.bak', 'service.log', 'service.log.1', 'homelabwallpaperstate.json') -or
            $leafName -like 'state.json.corrupt-*' -or
            $leafName -like '.state-*.tmp') {
            throw "Runtime or multimedia file would enter source control: $candidateFile"
        }
        if ($normalized.StartsWith('backgrounds/', [StringComparison]::OrdinalIgnoreCase) -and
            $normalized -notin @('backgrounds/.gitignore', 'backgrounds/bg.jpg')) {
            throw "Unexpected source-controlled background file: $candidateFile"
        }
    }

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
        'HomeLabWallpaperState-data/state.json'
    )
    foreach ($probe in $ignoreProbes) {
        & $git.Source check-ignore --no-index --quiet -- $probe
        if ($LASTEXITCODE -ne 0) {
            throw "Git ignore rule is missing for: $probe"
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
