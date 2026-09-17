// Browser regression with intercepted Notes fixtures; never writes preview data.
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
if (!['localhost', '127.0.0.1', '[::1]'].includes(base.hostname)) throw new Error('Use a local preview.');
const output = path.resolve('output/notes-selection-e2e');
await mkdir(output, { recursive: true });
const report = { checks: [], traces: {}, errors: [], writes: [] };
const browser = await playwright.chromium.launch({ channel: 'msedge', headless: true });
const notes = Array.from({ length: 18 }, (_, i) => ({ id: `00000000-0000-4000-8000-${String(i + 1).padStart(12, '0')}`, title: `切换示例 ${i + 1}`, body: `# 正文 ${i + 1}\n\n只属于笔记 ${i + 1} 的内容。\n\n- [ ] 项目 ${i + 1}`, revision: `revision-${i}`, updatedAt: '2026-09-18T00:00:00Z' }));
const settings = { autoSaveEnabled: false, autoSaveSeconds: 60, saveOnSwitch: false, collapseOnBlur: false };
const row = (i) => `[data-note-id="${notes[i].id}"] [data-action="select"]`;
async function check(name, action) { await action(); report.checks.push(name); console.log(`PASS ${name}`); }
async function ready(page, i) {
    await page.waitForFunction(({ id, title }) => document.querySelector('#notes-list [data-active="true"]')?.dataset.noteId === id && document.getElementById('notes-title').value === title && document.getElementById('notes-editor-pane').dataset.switchPhase === 'idle' && !document.getElementById('notes-mode-edit').disabled, notes[i]);
}
async function select(page, i) { await page.locator(row(i)).click(); await ready(page, i); }
async function fixture(options = {}) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, ...options });
    const page = await context.newPage();
    const gates = new Map();
    page.on('pageerror', error => report.errors.push(error.message));
    await page.route('**/api/notes**', async route => {
        const request = route.request();
        if (request.method() !== 'GET') { report.writes.push(request.method()); await route.abort(); return; }
        const endpoint = new URL(request.url()).pathname.slice('/api/notes'.length);
        let body;
        if (!endpoint) body = { collectionId: 'selection-motion-fixture', notes, order: { ids: notes.map(note => note.id), revision: 'order-1' }, settings };
        else if (endpoint === '/status') body = { collectionId: 'selection-motion-fixture', enabled: true, primaryAvailable: true, backupAvailable: true, backupPending: 0 };
        else if (endpoint === '/settings') body = { settings };
        else {
            const note = notes.find(item => '/' + item.id === endpoint);
            assert.ok(note, `Unexpected fixture request: ${endpoint}`);
            const gate = gates.get(note.id);
            if (gate) { gate.received(); await gate.blocked; }
            body = { note };
        }
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto(base.href, { waitUntil: 'load' });
    await page.locator('#notes-open').click();
    await page.waitForFunction(() => document.getElementById('notes-shell').dataset.motion === 'open' && !document.getElementById('notes-new').disabled);
    return { page, context, gates };
}
function hold(gates, i) {
    let release, received;
    const blocked = new Promise(resolve => { release = resolve; });
    const requested = new Promise(resolve => { received = resolve; });
    gates.set(notes[i].id, { blocked, received });
    return { requested, release: () => { gates.delete(notes[i].id); release(); } };
}
async function trace(page, i) {
    return page.evaluate(async selector => {
        const pane = document.getElementById('notes-editor-pane');
        const read = t => ({ t, phase: pane.dataset.switchPhase, opacity: Number(getComputedStyle(pane).opacity), title: document.getElementById('notes-title').value, preview: document.getElementById('notes-preview').textContent, y: new DOMMatrixReadOnly(getComputedStyle(document.querySelector('.notes-list-highlight')).transform).m42, readOnly: document.getElementById('notes-body').readOnly });
        const samples = [read(document.timeline.currentTime)];
        document.querySelector(selector).click();
        const start = performance.now();
        do {
            if (performance.now() - start > 5000) throw new Error('Selection did not settle.');
            samples.push(read(await new Promise(requestAnimationFrame)));
        } while (pane.dataset.switchPhase !== 'idle' || performance.now() - start < 400);
        return samples;
    }, row(i));
}
async function aligned(page) {
    const positions = await page.evaluate(() => {
        const active = document.querySelector('#notes-list [data-active="true"]').getBoundingClientRect();
        const light = document.querySelector('.notes-list-highlight').getBoundingClientRect();
        return { dx: active.x - light.x, dy: active.y - light.y, dw: active.width - light.width, dh: active.height - light.height };
    });
    assert.ok(Object.values(positions).every(delta => Math.abs(delta) < 2), JSON.stringify(positions));
}
try {
    const desktop = await fixture();
    const { page, gates } = desktop;
    await check('body fades out before replacement and fades in only after target Markdown is ready', async () => {
        await select(page, 3); await select(page, 0);
        const samples = await trace(page, 3); report.traces.switch = samples;
        assert.ok(samples.some(item => item.phase === 'fading-out' && item.opacity > .05 && item.opacity < .95));
        assert.ok(samples.some(item => item.phase === 'fading-in' && item.opacity > .05 && item.opacity < .95));
        for (const item of samples.filter(item => item.phase === 'fading-out')) assert.equal(item.title, notes[0].title);
        for (const item of samples.filter(item => item.phase === 'fading-in')) { assert.equal(item.title, notes[3].title); assert.match(item.preview, /只属于笔记 4/); assert.equal(item.readOnly, true); }
        assert.equal(samples.at(-1).readOnly, false);
        const start = samples[0], end = samples.at(-1);
        const intermediate = samples.filter(item => item.y > start.y + 2 && item.y < end.y - 2);
        assert.ok(intermediate.length >= 3, 'Highlight must move through intermediate positions.');
        assert.ok(intermediate.some(item => (item.y - start.y) / (end.y - start.y) > (item.t - start.t) / 280 + .1), 'Highlight should ease out, not move linearly.');
        await aligned(page);
        await page.locator('#notes-panel').screenshot({ path: path.join(output, 'desktop.png') });
    });
    await check('fast repeated selections keep the latest target and do not reveal a late response', async () => {
        const slow = hold(gates, 5);
        await page.locator(row(5)).click(); await slow.requested;
        await select(page, 2);
        slow.release();
        await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 350)));
        await ready(page, 2);
        assert.match(await page.locator('#notes-preview').textContent(), /只属于笔记 3/);
        await page.evaluate(selectors => { document.querySelector(selectors[0]).click(); document.querySelector(selectors[1]).click(); document.querySelector(selectors[2]).click(); }, [row(0), row(3), row(2)]);
        await ready(page, 2); await aligned(page);
    });
    await check('slow selection locks stale editing and closing cancels its transition cleanly', async () => {
        const slow = hold(gates, 6);
        await page.locator(row(6)).click(); await slow.requested;
        await page.waitForFunction(() => document.getElementById('notes-editor-pane').dataset.switchPhase === 'loading');
        assert.equal(await page.locator('#notes-body').evaluate(el => el.readOnly), true);
        assert.equal(await page.locator('#notes-mode-edit').isDisabled(), true);
        assert.equal(await page.locator('#notes-editor-pane').evaluate(el => getComputedStyle(el).opacity), '0');
        await page.locator('#notes-close').click();
        await page.waitForFunction(() => document.getElementById('notes-shell').dataset.motion === 'closed');
        slow.release();
        await page.locator('#notes-open').click();
        await ready(page, 6);
        assert.equal(await page.locator('#notes-editor-pane').evaluate(el => getComputedStyle(el).opacity), '1');
        const missingPath = `**/api/notes/${notes[7].id}`;
        await page.route(missingPath, route => route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'note_missing' }) }));
        await select(page, 7);
        await page.unroute(missingPath);
        await select(page, 7);
        assert.match(await page.locator('#notes-preview').textContent(), /只属于笔记 8/);
    });
    await check('highlight stays aligned while the list scrolls, resizes and rerenders', async () => {
        await select(page, 14); await aligned(page);
        await page.setViewportSize({ width: 1000, height: 800 });
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        await aligned(page);
        await page.locator('#notes-mode-edit').click();
        await page.locator('#notes-title').fill('重绘后的高光位置');
        await aligned(page);
    });
    await desktop.context.close();
    const mobile = await fixture({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await check('mobile keeps list-first navigation and fades in when revisiting the selected note', async () => {
        assert.equal(await mobile.page.locator('#notes-detail').isVisible(), false);
        await select(mobile.page, 1);
        await mobile.page.locator('#notes-back').click();
        assert.equal(await mobile.page.locator('#notes-detail').isVisible(), false);
        const samples = await trace(mobile.page, 1); report.traces.mobile = samples;
        assert.ok(samples.some(item => item.phase === 'fading-in' && item.opacity > .05 && item.opacity < .95));
        await mobile.page.locator('#notes-panel').screenshot({ path: path.join(output, 'mobile.png') });
    });
    await mobile.context.close();
    const reduced = await fixture({ reducedMotion: 'reduce' });
    await check('reduced motion skips visual interpolation without skipping content loading', async () => {
        const samples = await trace(reduced.page, 3); report.traces.reduced = samples;
        assert.ok(samples.every(item => item.opacity === 0 || item.opacity === 1));
        await ready(reduced.page, 3); await aligned(reduced.page);
        assert.match(await reduced.page.locator('#notes-preview').textContent(), /只属于笔记 4/);
    });
    await reduced.context.close();
    assert.deepEqual(report.errors, []); assert.deepEqual(report.writes, []);
    console.log(`Notes selection: ${report.checks.length} checks passed; no server writes or runtime errors.`);
} catch (error) { report.failure = error.stack; console.error(error); process.exitCode = 1; }
finally { await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); await browser.close(); }
