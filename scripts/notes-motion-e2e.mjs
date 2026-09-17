// Read-only browser regression for Notes introduction and synchronized panel motion.
// Requires a running local preview; never writes Notes, settings or fixture data.
// Usage: node scripts/notes-motion-e2e.mjs [http://127.0.0.1:8765/]
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import path from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';

const require = createRequire(import.meta.url);
let playwright;
try { playwright = require(process.env.PLAYWRIGHT_MODULE || 'playwright'); }
catch { playwright = require(path.join(homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const base = new URL(process.argv[2] || 'http://127.0.0.1:8765/');
if (!['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname)) throw new Error('Use a local preview URL.');
const output = path.resolve('output/notes-motion-e2e');
await mkdir(output, { recursive: true });
const report = { url: base.href, checks: [], traces: {}, errors: [], writes: [] };
const browser = await playwright.chromium.launch({ channel: 'msedge', headless: true });
const near = (actual, expected, tolerance = 2) => assert.ok(Math.abs(actual - expected) <= tolerance, `Expected ${actual} near ${expected} ± ${tolerance}`);
const phaseOrder = (samples) => samples.map((sample) => sample.phase).filter((phase, index, phases) => phase && phase !== phases[index - 1]);

function installProbe() {
    window.notesMotionProbe = { samples: [] };
    function capture(frameTime = document.timeline.currentTime ?? performance.now()) {
        const shell = document.getElementById('notes-shell');
        const hero = document.querySelector('.hero-copy');
        const panel = document.getElementById('notes-panel');
        const capsule = document.getElementById('notes-open');
        if (!shell || !hero || !panel || !capsule) return null;
        const style = getComputedStyle(shell);
        // Do not mistake unstyled HTML before its blocking CSS arrives for a frame.
        if (style.width === 'auto' || style.borderTopStyle === 'none') return null;
        const box = shell.getBoundingClientRect();
        const heroStyle = getComputedStyle(hero);
        const panelStyle = getComputedStyle(panel);
        const searchStyle = getComputedStyle(document.querySelector('.search-shell'));
        const result = {
            t: frameTime, phase: shell.dataset.motion || '', open: shell.dataset.open,
            width: box.width, height: box.height, radius: style.borderTopLeftRadius,
            opacity: Number(style.opacity), heroOpacity: Number(heroStyle.opacity),
            animation: style.animationName, heroAnimation: heroStyle.animationName,
            delay: style.animationDelay, heroDelay: heroStyle.animationDelay,
            duration: style.animationDuration, heroDuration: heroStyle.animationDuration,
            intro: document.documentElement.classList.contains('intro-first'), skip: document.documentElement.classList.contains('intro-skip'),
            panelOpacity: Number(panelStyle.opacity), panelHidden: panel.hidden, panelInert: panel.inert,
            capsuleHidden: capsule.hidden, capsuleInert: capsule.inert,
            background: style.backgroundColor, blur: style.backdropFilter,
            searchBackground: searchStyle.backgroundColor, searchBlur: searchStyle.backdropFilter,
        };
        return result;
    }
    window.notesMotionCapture = capture;
    const tick = (frameTime) => {
        // Use the same animation timeline as WAAPI, not callback execution time:
        // a delayed callback can run immediately before the following frame.
        const sample = capture(frameTime);
        if (sample) {
            window.notesMotionProbe.samples.push(sample);
            if (window.notesMotionProbe.samples.length > 1800) window.notesMotionProbe.samples.shift();
        }
        requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
}

async function pageFor(options = {}) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, locale: 'zh-CN', ...options });
    const page = await context.newPage();
    await page.addInitScript(installProbe);
    page.setDefaultTimeout(10000);
    page.on('pageerror', (error) => report.errors.push(error.message));
    page.on('request', (request) => {
        if (new URL(request.url()).pathname.startsWith('/api/notes') && request.method() !== 'GET') report.writes.push(`${request.method()} ${request.url()}`);
    });
    return { page, context };
}
async function check(name, action) {
    await action();
    report.checks.push(name);
    console.log(`PASS ${name}`);
}
async function waitPhase(page, phase) {
    await page.waitForFunction((value) => document.getElementById('notes-shell')?.dataset.motion === value, phase);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function motion(page, action, finalPhase) {
    return page.evaluate(async ({ action, finalPhase }) => {
        const probe = window.notesMotionProbe;
        probe.samples = [window.notesMotionCapture()].filter(Boolean);
        document.getElementById(action).click();
        const started = performance.now();
        while (document.getElementById('notes-shell').dataset.motion !== finalPhase) {
            if (performance.now() - started > 5000) throw new Error(`Motion did not reach ${finalPhase}`);
            await new Promise(requestAnimationFrame);
        }
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return probe.samples;
    }, { action, finalPhase });
}
function finalState(sample, open) {
    assert.equal(sample.phase, open ? 'open' : 'closed');
    assert.equal(sample.open, String(open));
    assert.equal(sample.panelHidden, !open);
    assert.equal(sample.panelInert, !open);
    assert.equal(sample.capsuleHidden, open);
    assert.equal(sample.capsuleInert, open);
    if (open) near(sample.panelOpacity, 1, 0.01);
}
function assertRoundedCorners(samples) {
    for (const sample of samples) {
        const parts = sample.radius.split(/\s+/).map(parseFloat);
        assert.ok(!sample.radius.includes('%'), 'Corners must not become percentage-based ellipses.');
        assert.ok(parts[0] <= 30, `Oversized intermediate corner radius: ${sample.radius}`);
        if (parts.length > 1) near(parts[0], parts[1], 0.05);
    }
}
function assertStageDuration(samples, phase, duration) {
    const stage = samples.filter((sample) => sample.phase === phase);
    assert.ok(stage.length >= 3, `Missing observable ${phase} frames`);
    const elapsed = stage.at(-1).t - stage[0].t;
    assert.ok(elapsed >= duration - 85, `${phase} ended too quickly: ${elapsed} ms`);
    assert.ok(elapsed <= duration + 450, `${phase} did not settle: ${elapsed} ms`);
}
function assertSynchronizedResize(samples, phase) {
    const initial = samples[0];
    const last = samples.at(-1);
    const frames = samples.filter((row) => row.phase === phase);
    assertStageDuration(samples, phase, 300);
    let previous;
    let intermediateFrames = 0;
    for (const sample of frames) {
        const widthProgress = (sample.width - initial.width) / (last.width - initial.width);
        const heightProgress = (sample.height - initial.height) / (last.height - initial.height);
        near(widthProgress, heightProgress, 0.015);
        assert.ok(widthProgress >= -0.01 && widthProgress <= 1.01, 'Size must stay between its starting and final dimensions.');
        assert.ok(sample.panelHidden || sample.panelOpacity < 0.02, 'Text appeared while the dimensions were changing.');
        if (widthProgress > 0.05 && widthProgress < 0.95) intermediateFrames++;
        if (previous) {
            const change = widthProgress - previous.progress;
            const elapsed = sample.t - previous.t;
            assert.ok(change >= -0.01, 'Resize must not reverse direction.');
            // Allow skipped frames, but reject a discontinuity faster than the easing curve.
            assert.ok(change <= 4.25 * elapsed / 300 + 0.01, `Size jumped ${change} over ${elapsed}ms.`);
        }
        previous = { progress: widthProgress, t: sample.t };
    }
    assert.ok(intermediateFrames >= 3, 'Both dimensions must interpolate together through observable intermediate sizes.');
}
function assertOpenTrace(samples) {
    const last = samples.at(-1);
    assert.deepEqual(phaseOrder(samples), ['closed', 'expanding', 'revealing', 'open']);
    assertSynchronizedResize(samples, 'expanding');
    assertStageDuration(samples, 'revealing', 120);
    for (const sample of samples.filter((row) => row.phase === 'revealing')) {
        near(sample.width, last.width);
        near(sample.height, last.height);
    }
    assert.ok(samples.some((row) => row.phase === 'revealing' && row.panelOpacity > 0.05 && row.panelOpacity < 0.95), 'Final content opacity did not interpolate.');
    assertRoundedCorners(samples);
    finalState(last, true);
}
function assertCloseTrace(samples) {
    const initial = samples[0];
    const last = samples.at(-1);
    assert.deepEqual(phaseOrder(samples), ['open', 'fading', 'collapsing', 'closed']);
    assertStageDuration(samples, 'fading', 120);
    for (const sample of samples.filter((row) => row.phase === 'fading')) { near(sample.width, initial.width); near(sample.height, initial.height); }
    assertSynchronizedResize(samples, 'collapsing');
    assertRoundedCorners(samples);
    finalState(last, false);
}
async function introducedPage(options = {}) {
    const pair = await pageFor(options);
    const { page } = pair;
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    await page.route('**/assets/js/app.js', async (route) => { await gate; await route.continue(); });
    await page.goto(base.href, { waitUntil: 'commit' });
    await page.waitForFunction(() => window.notesMotionProbe?.samples.length >= 5);
    const before = await page.evaluate(() => window.notesMotionProbe.samples);
    release();
    await page.waitForLoadState('load');
    await page.waitForFunction(() => !document.documentElement.classList.contains('intro-first'));
    const intro = await page.evaluate(() => window.notesMotionProbe.samples);
    await page.unroute('**/assets/js/app.js');
    return { ...pair, before, intro };
}

try {
    const desktop = await introducedPage();
    const { page } = desktop;
    await check('first intro keeps Notes hidden and reveals it with hero-copy timing', async () => {
        assert.ok(desktop.before.every((row) => row.intro && row.opacity < 0.01 && row.heroOpacity < 0.01));
        const moving = desktop.intro.filter((row) => row.intro && row.heroOpacity > 0.01 && row.heroOpacity < 0.99);
        assert.ok(moving.length >= 5, 'Expected visible intro interpolation.');
        for (const sample of moving) {
            near(sample.opacity, sample.heroOpacity, 0.025);
            assert.equal(sample.animation, sample.heroAnimation);
            assert.equal(sample.delay, sample.heroDelay);
            assert.equal(sample.duration, sample.heroDuration);
        }
        report.traces.desktopIntro = desktop.intro;
    });
    await check('collapsed Notes matches search transparency and 16px blur', async () => {
        const sample = await page.evaluate(() => window.notesMotionCapture());
        assert.equal(sample.background, 'rgba(8, 20, 36, 0.48)');
        assert.equal(sample.background, sample.searchBackground);
        assert.equal(sample.blur, 'blur(16px)');
        assert.equal(sample.blur, sample.searchBlur);
        finalState(sample, false);
    });
    await check('desktop resizes both axes together in 300ms with content fading after opening and before closing', async () => {
        const opened = await motion(page, 'notes-open', 'open');
        report.traces.desktopOpen = opened;
        assertOpenTrace(opened);
        await page.waitForFunction(() => !document.getElementById('notes-new').disabled);
        assert.equal(await page.locator('.notes-row-actions [data-action="delete"]:disabled').count(), 0, 'List actions must unlock when the opening animation finishes.');
        await page.screenshot({ path: path.join(output, 'desktop-expanded.png'), fullPage: true });
        const closed = await motion(page, 'notes-close', 'closed');
        report.traces.desktopClose = closed;
        assertCloseTrace(closed);
        await page.screenshot({ path: path.join(output, 'desktop-collapsed.png'), fullPage: true });
    });
    await check('same-session refresh skips first intro and leaves capsule usable', async () => {
        await page.reload({ waitUntil: 'load' });
        await page.waitForFunction(() => window.notesMotionProbe?.samples.length > 3);
        const samples = await page.evaluate(() => window.notesMotionProbe.samples);
        assert.ok(samples.every((row) => row.skip && !row.intro));
        finalState(samples.at(-1), false);
        assert.ok(await page.locator('#notes-open').isVisible());
    });
    await check('resize during opening and repeated clicks settle into consistent states', async () => {
        await page.evaluate(() => document.getElementById('notes-open').click());
        await page.waitForFunction(() => document.getElementById('notes-shell').dataset.motion === 'expanding');
        await page.setViewportSize({ width: 980, height: 800 });
        await waitPhase(page, 'open');
        const resized = await page.evaluate(() => {
            const parent = document.getElementById('notes-shell').parentElement;
            const style = getComputedStyle(parent);
            return { sample: window.notesMotionCapture(), parentWidth: parent.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) };
        });
        finalState(resized.sample, true);
        near(resized.sample.width, resized.parentWidth, 3);
        await page.evaluate(() => { document.getElementById('notes-close').click(); document.getElementById('notes-close').click(); });
        await waitPhase(page, 'closed');
        await page.evaluate(() => { document.getElementById('notes-open').click(); document.getElementById('notes-open').click(); });
        await waitPhase(page, 'open');
        await page.evaluate(() => document.getElementById('notes-close').click());
        await waitPhase(page, 'closed');
        finalState(await page.evaluate(() => window.notesMotionCapture()), false);
    });
    await desktop.context.close();

    const mobile = await introducedPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await check('mobile intro and synchronized motion preserve list-first navigation', async () => {
        assert.ok(mobile.before.every((row) => row.opacity < 0.01 && row.heroOpacity < 0.01));
        for (const row of mobile.intro.filter((sample) => sample.intro)) near(row.opacity, row.heroOpacity, 0.025);
        const opened = await motion(mobile.page, 'notes-open', 'open');
        report.traces.mobileOpen = opened;
        assertOpenTrace(opened);
        assert.equal(await mobile.page.locator('#notes-shell').getAttribute('data-view'), 'list');
        assert.equal(await mobile.page.locator('#notes-detail').isVisible(), false);
        await mobile.page.screenshot({ path: path.join(output, 'mobile-expanded.png'), fullPage: true });
        const closed = await motion(mobile.page, 'notes-close', 'closed');
        report.traces.mobileClose = closed;
        assertCloseTrace(closed);
    });
    await mobile.context.close();

    const reduced = await pageFor({ reducedMotion: 'reduce' });
    await check('reduced motion skips introduction and opening/closing stages', async () => {
        await reduced.page.goto(base.href, { waitUntil: 'load' });
        const opened = await motion(reduced.page, 'notes-open', 'open');
        assert.ok(opened.every((row) => ['closed', 'open'].includes(row.phase)));
        finalState(opened.at(-1), true);
        const closed = await motion(reduced.page, 'notes-close', 'closed');
        assert.ok(closed.every((row) => ['closed', 'open'].includes(row.phase)));
        finalState(closed.at(-1), false);
        assert.ok(closed.every((row) => row.skip && !row.intro));
        report.traces.reducedOpen = opened;
        report.traces.reducedClose = closed;
    });
    await reduced.context.close();

    const slow = await pageFor();
    await check('slow Notes GET does not block opening animation or enable stale editing', async () => {
        await slow.page.goto(base.href, { waitUntil: 'load' });
        await slow.page.waitForFunction(() => !document.documentElement.classList.contains('intro-first'));
        let release;
        const gate = new Promise((resolve) => { release = resolve; });
        await slow.page.route(/\/api\/notes$/, async (route) => { await gate; await route.continue(); });
        const opened = await motion(slow.page, 'notes-open', 'open');
        report.traces.slowGetOpen = opened;
        assertOpenTrace(opened);
        assert.equal(await slow.page.locator('#notes-new').isDisabled(), true);
        assert.equal(await slow.page.locator('#notes-body').evaluate((body) => body.readOnly), true);
        release();
        await slow.page.waitForFunction(() => !document.getElementById('notes-new').disabled);
        await motion(slow.page, 'notes-close', 'closed');
    });
    await slow.context.close();

    assert.deepEqual(report.errors, [], 'Unexpected page runtime errors');
    assert.deepEqual(report.writes, [], 'Motion regression must never write user Notes data');
    console.log(`Notes motion: ${report.checks.length} checks passed, no Notes writes or runtime errors.`);
} catch (error) {
    report.failure = error.stack || String(error);
    console.error(report.failure);
    process.exitCode = 1;
} finally {
    await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    await browser.close();
    console.log(`Motion traces and screenshots: ${output}`);
}
