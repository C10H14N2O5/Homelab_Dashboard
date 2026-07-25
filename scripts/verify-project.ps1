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
if ($readmeSource.Contains('index.txt')) {
    throw 'README.md must not list the removed index.txt file.'
}
$maintenanceContracts = @(
    @{ Name = 'PyInstaller one-file build'; Source = $buildSource; Marker = '--onefile' },
    @{ Name = 'packaged executable health check'; Source = $buildSource; Marker = '/healthz' },
    @{ Name = 'Python unit tests'; Source = $buildSource; Marker = 'unittest discover' },
    @{ Name = 'packaged PATCH compatibility check'; Source = $buildSource; Marker = 'X-Requested-With' },
    @{ Name = 'packaged background upload check'; Source = $buildSource; Marker = '/api/background-files/upload' },
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

const requiredMarkers = [
    '/api/background-preferences',
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
    'isBestdoriBackgroundName',
    "const backgroundLibrarySources = ['ordinary', 'bestdori']",
    'section.dataset.backgroundSection = source',
    'option.dataset.backgroundSource = source',
    "className = 'background-section-collapse'",
    "className = 'background-section-clip'",
    'grid-template-rows: 0fr',
    'transform: translateY(-4px)',
    "sectionContent.toggleAttribute('inert'",
    'runAfterBackgroundSectionTransition',
    'afterTransition: focusOption',
    "className = 'background-section-empty'",
    'setBackgroundSectionExpanded(backgroundSourceForName(fileName), true, {',
    'const inventorySummary =',
    "'Content-Type': 'application/octet-stream'",
    "errorCode === 'trash_unavailable'",
    'result.payload && result.payload.preferences',
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

const backgroundManagerHelperStart = runtimeScript.indexOf('function normalizeBackgroundName(');
const backgroundManagerHelperEnd = runtimeScript.indexOf('function backgroundUrlFromName(', backgroundManagerHelperStart);
if (backgroundManagerHelperStart < 0 || backgroundManagerHelperEnd < 0) {
    throw new Error('Unable to extract background file manager helpers for behavior checks.');
}
vm.runInContext(
    runtimeScript.slice(backgroundManagerHelperStart, backgroundManagerHelperEnd)
        + '\nglobalThis.__backgroundManagerHelpers = { normalizeBackgroundName, validateBackgroundUploadFile, backgroundModesUsingFile, isBestdoriBackgroundName, backgroundSourceForName };',
    context
);
context.backgroundExtensionPattern = /\.(?:jpe?g|png|webp|avif)$/i;

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

const renderFunctionStart = runtimeScript.indexOf('function renderBackgroundOptions(');
const renderFunctionEnd = runtimeScript.indexOf('function focusBackgroundOption(', renderFunctionStart);
if (renderFunctionStart < 0 || renderFunctionEnd < 0) {
    throw new Error('Unable to extract the grouped background renderer.');
}
const groupedRendererSource = runtimeScript.slice(renderFunctionStart, renderFunctionEnd);
if (!groupedRendererSource.includes('backgroundLibrarySources.forEach')
    || !groupedRendererSource.includes('backgroundLibraryExpanded[source]')
    || !groupedRendererSource.includes('groupedUrls.ordinary.length')
    || !groupedRendererSource.includes('groupedUrls.bestdori.length')
    || !groupedRendererSource.includes('setBackgroundSectionExpanded(source')
    || !groupedRendererSource.includes("sectionContent.setAttribute('aria-hidden'")
    || !groupedRendererSource.includes("sectionContent.toggleAttribute('inert'")
    || groupedRendererSource.includes('sectionGrid.hidden')) {
    throw new Error('Grouped background renderer is missing section order, counts, collapse state, or toggle behavior.');
}

const transitionHelperStart = runtimeScript.indexOf('function cancelBackgroundSectionTransition(');
const transitionHelperEnd = runtimeScript.indexOf('function focusBackgroundOption(', transitionHelperStart);
if (transitionHelperStart < 0 || transitionHelperEnd < 0) {
    throw new Error('Unable to extract background section transition helpers.');
}

const transitionListeners = new Map();
const transitionTimers = new Map();
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
        setTimeout(callback) {
            const timerId = ++transitionTimerId;
            transitionTimers.set(timerId, callback);
            return timerId;
        },
        clearTimeout(timerId) {
            transitionTimers.delete(timerId);
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
}) || completedBackgroundFocus !== 0 || !transitionListeners.has('transitionend')) {
    throw new Error('Animated background expansion did not defer its completion callback.');
}
transitionListeners.get('transitionend')({
    target: transitionContent,
    propertyName: 'grid-template-rows'
});
if (completedBackgroundFocus !== 1
    || transitionTimers.size !== 0
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
if (immediateBackgroundFocus !== 1 || transitionTimers.size !== 0 || transitionListeners.size !== 0) {
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
if (fallbackBackgroundFocus !== 1 || transitionTimers.size !== 0 || transitionListeners.size !== 0) {
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
if (completedBackgroundFocus !== 1 || transitionTimers.size !== 0) {
    throw new Error('Reversing a background transition did not cancel its stale completion callback.');
}

reduceBackgroundMotion = true;
transitionContext.__backgroundTransitionHelpers.setBackgroundSectionExpanded('ordinary', true, {
    afterTransition() {
        completedBackgroundFocus += 1;
    }
});
if (completedBackgroundFocus !== 2 || transitionTimers.size !== 0 || transitionListeners.size !== 0) {
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
