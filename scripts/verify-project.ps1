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
    'scripts\install-state-service.ps1',
    'scripts\uninstall-state-service.ps1',
    'scripts\verify-project.ps1'
)
foreach ($relativePath in $requiredFiles) {
    $path = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required file is missing: $relativePath"
    }
}

$installSource = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'install-state-service.ps1'))
$buildSource = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'build-state-service.ps1'))
$readmeSource = [IO.File]::ReadAllText((Join-Path $projectRoot 'README.md'))
$changelogSource = [IO.File]::ReadAllText((Join-Path $projectRoot 'CHANGELOG.md'))
$maintenanceContracts = @(
    @{ Name = 'deployment executable hash check'; Source = $installSource; Marker = 'ExpectedSha256' },
    @{ Name = 'PyInstaller one-file build'; Source = $buildSource; Marker = '--onefile' },
    @{ Name = 'packaged executable health check'; Source = $buildSource; Marker = '/healthz' },
    @{ Name = 'Python unit tests'; Source = $buildSource; Marker = 'unittest discover' },
    @{ Name = 'packaged PATCH compatibility check'; Source = $buildSource; Marker = 'X-Requested-With' },
    @{ Name = 'temporary artifact hash'; Source = $buildSource; Marker = 'temporaryHash' },
    @{ Name = 'Zoraxy Virtual Directory documentation'; Source = $readmeSource; Marker = 'MatchingPath' },
    @{ Name = 'service probe CORS header documentation'; Source = $readmeSource; Marker = 'Access-Control-Allow-Origin' },
    @{ Name = 'service probe downstream direction documentation'; Source = $readmeSource; Marker = 'Client << Zoraxy' }
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
if (cards.length !== 11) throw new Error(`Expected 11 service cards, found ${cards.length}.`);
cards.forEach((tag, index) => {
    if (!/\btarget="_blank"/.test(tag)) throw new Error(`Service card ${index + 1} does not open in a new tab.`);
    const rel = tag.match(/\brel="([^"]+)"/);
    const tokens = rel ? rel[1].split(/\s+/) : [];
    if (!tokens.includes('noopener') || !tokens.includes('noreferrer')) {
        throw new Error(`Service card ${index + 1} is missing safe rel tokens.`);
    }
});

const requiredMarkers = [
    '/api/background-preferences',
    'homelab-background-state-cache-v1',
    'data-background-mode="landscape"',
    'data-background-mode="portrait"',
    'fetchWithTimeout',
    'classifyServiceStatus',
    'data-state="warning"',
    'data-probe-url="https://cliproxy.home.c10h14n2o5.com/management.html/"',
    "mode: 'cors'",
    'backgroundScanPending',
    'reconcileSharedBackgroundPreference',
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
const serviceHelperEnd = runtimeScript.indexOf('async function refreshStatuses(', serviceHelperStart);
if (serviceHelperStart < 0 || serviceHelperEnd < 0) {
    throw new Error('Unable to extract service status helpers for behavior checks.');
}
vm.runInContext(
    runtimeScript.slice(serviceHelperStart, serviceHelperEnd)
        + '\nglobalThis.__serviceHelpers = { classifyServiceStatus, serviceStatusLabel, probe };',
    context
);

const statusCases = [
    [200, 'online', '\u53ef\u8fde\u63a5'],
    [204, 'online', '\u53ef\u8fde\u63a5'],
    [301, 'online', '\u53ef\u8fde\u63a5'],
    [302, 'online', '\u53ef\u8fde\u63a5'],
    [401, 'warning', '\u5f02\u5e38 \u00b7 HTTP 401'],
    [404, 'warning', '\u5f02\u5e38 \u00b7 HTTP 404'],
    [500, 'offline', '\u4e0d\u53ef\u7528 \u00b7 HTTP 500'],
    [502, 'offline', '\u4e0d\u53ef\u7528 \u00b7 HTTP 502'],
    [521, 'offline', '\u4e0d\u53ef\u7528 \u00b7 HTTP 521']
];
let observedOptions = null;
let observedResource = null;
let canceledBodies = 0;
const stateElement = {
    dataset: {},
    label: { textContent: '' },
    querySelector() { return this.label; }
};
const card = {
    href: 'https://service.home.example/',
    dataset: { probeUrl: 'https://probe.home.example/health' },
    querySelector() { return stateElement; }
};

for (const [status, expectedState, expectedLabel] of statusCases) {
    context.fetch = (resource, options) => {
        observedResource = resource;
        observedOptions = options;
        return Promise.resolve({
            status,
            body: {
                cancel() {
                    canceledBodies += 1;
                    return Promise.resolve();
                }
            }
        });
    };
    const result = await context.__serviceHelpers.probe(card);
    if (result.state !== expectedState || result.status !== status) {
        throw new Error(`HTTP ${status} classified as ${result.state}, expected ${expectedState}.`);
    }
    if (stateElement.dataset.state !== expectedState || stateElement.label.textContent !== expectedLabel) {
        throw new Error(`HTTP ${status} rendered an unexpected card state or label.`);
    }
}
if (!observedOptions || observedOptions.mode !== 'cors' || observedOptions.credentials !== 'omit'
    || observedOptions.redirect !== 'follow' || observedOptions.cache !== 'no-store') {
    throw new Error('Service probe fetch options do not enforce the CORS status contract.');
}
if (observedResource !== card.dataset.probeUrl) {
    throw new Error('Service probe did not use the card-specific probe URL override.');
}
if (canceledBodies !== statusCases.length) {
    throw new Error('Service probe did not cancel every unused response body.');
}

delete card.dataset.probeUrl;
context.fetch = (resource) => {
    observedResource = resource;
    return Promise.reject(new Error('simulated network failure'));
};
const failedProbe = await context.__serviceHelpers.probe(card);
if (failedProbe.state !== 'offline' || failedProbe.status !== null
    || stateElement.dataset.state !== 'offline' || stateElement.label.textContent !== '\u4e0d\u53ef\u8fde\u63a5') {
    throw new Error('Service probe request failure did not render the offline fallback.');
}
if (observedResource !== card.href) {
    throw new Error('Service probe did not fall back to the card href.');
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
            $blockedMediaExtensions -contains $extension -or
            $leafName -in @('state.json', 'state.json.bak', 'service.log', 'homelabwallpaperstate.json') -or
            $leafName -like 'state.json.corrupt-*' -or
            $leafName -like '.state-*.tmp') {
            throw "Runtime or multimedia file would enter source control: $candidateFile"
        }
        if ($normalized.StartsWith('backgrounds/', [StringComparison]::OrdinalIgnoreCase) -and
            $normalized -ne 'backgrounds/.gitignore') {
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
        'service.log'
        'HomeLabWallpaperState.json'
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
