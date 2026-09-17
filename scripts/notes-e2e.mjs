// Requires an isolated running Notes preview. Does not run with offline unit checks.
// Usage: node scripts/notes-e2e.mjs [http://127.0.0.1:8765/]
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

const require = createRequire(import.meta.url);
let playwright;
try { playwright = require(process.env.PLAYWRIGHT_MODULE || 'playwright'); }
catch {
    playwright = require(path.join(homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
}
const base = new URL(process.argv[2] || 'http://127.0.0.1:8765/');
if (!['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname)) throw new Error('Use an isolated local preview, never a production URL.');
const prefix = `QA-${new Date().toISOString().replace(/[:.]/g, '-')}-${randomUUID().slice(0, 6)}`;
const completed = [];
const errors = [];
let expectedRequestError = false;
let browser;
let originalSettings;
let originalOrder;

async function api(endpoint = '', method = 'GET', body) {
    const response = await fetch(new URL('/api/notes' + endpoint, base), {
        method,
        headers: { Origin: base.origin, 'X-Requested-With': 'homelab-dashboard', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const result = await response.json();
    if (!response.ok) throw Object.assign(new Error(`API ${method} ${endpoint}: ${response.status} ${JSON.stringify(result)}`), { status: response.status });
    return result;
}
async function eventually(check, message, timeout = 15000) {
    const deadline = Date.now() + timeout;
    let result;
    while (Date.now() < deadline) {
        result = await check();
        if (result) return result;
        await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error(`Timed out: ${message}`);
}
async function test(name, action) {
    await action();
    completed.push(name);
    console.log(`PASS ${name}`);
}
function watch(page) {
    page.on('dialog', (dialog) => dialog.accept());
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
        if (message.type() === 'error' && !expectedRequestError && /\/api\/notes|notes-/i.test(message.location().url || '')) errors.push(message.text());
    });
}
async function open(page) {
    await page.locator('#notes-open').click();
    await page.waitForFunction(() => document.querySelector('#notes-list').children.length > 0 && !document.querySelector('#notes-new').disabled);
    await page.locator('#notes-panel').waitFor({ state: 'visible' });
}
async function activeId(page) {
    return page.locator('#notes-list [data-active="true"]').getAttribute('data-note-id');
}
async function select(page, id) {
    await page.locator(`[data-note-id="${id}"] [data-action="select"]`).click();
    await page.waitForFunction((target) => document.querySelector('#notes-list [data-active="true"]')?.dataset.noteId === target, id);
}
async function edit(page) {
    await page.locator('#notes-mode-edit').click();
    await page.locator('#notes-body').waitFor({ state: 'visible' });
}
async function save(page, id, body) {
    await page.locator('#notes-save').click();
    await eventually(async () => {
        try { return (await api('/' + id)).note.body === body; }
        catch (error) { if (error.status === 404) return false; throw error; }
    }, 'expected note persisted');
    await page.waitForFunction(() => document.querySelector('#notes-save').disabled && !document.querySelector('#notes-save-status').textContent.includes('正在保存'));
}
async function create(page, title, body) {
    await page.locator('#notes-new').click();
    await page.locator('#notes-title').fill(title);
    await page.locator('#notes-body').fill(body);
    const id = await activeId(page);
    await save(page, id, body);
    return id;
}

try {
    const initial = await api();
    originalSettings = (await api('/settings')).settings;
    originalOrder = initial.order.ids;
    browser = await playwright.chromium.launch({ channel: 'msedge', headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, locale: 'zh-CN' });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    watch(page);
    await page.goto(base.href, { waitUntil: 'domcontentloaded' });
    await open(page);
    let first;
    let second;

    await test('desktop opens two-column Notes and saves shared settings through UI', async () => {
        await page.locator('#notes-detail').waitFor({ state: 'visible' });
        assert.equal(await page.locator('.notes-sidebar').isVisible(), true);
        assert.equal(await page.locator('#notes-detail').isVisible(), true);
        await page.locator('#notes-settings-inline').click();
        const form = page.locator('#notes-settings-form');
        await eventually(async () => (await form.locator('[name="autoSaveSeconds"]').inputValue()) !== '', 'settings populated');
        await form.locator('[name="autoSaveEnabled"]').uncheck();
        await form.locator('[name="saveOnSwitch"]').uncheck();
        await form.locator('[name="collapseOnBlur"]').uncheck();
        await form.locator('[name="autoSaveSeconds"]').fill('75');
        await form.locator('[type="submit"]').click();
        await page.locator('#notes-settings-feedback').filter({ hasText: '设置已保存' }).waitFor();
        const stored = (await api('/settings')).settings;
        assert.equal(stored.autoSaveEnabled, false);
        assert.equal(stored.saveOnSwitch, false);
        assert.equal(stored.autoSaveSeconds, 75);
        await page.locator('#notes-settings-dialog [aria-label="关闭"]').click();
    });

    const initialBody = '# E2E 标题\n\n- [ ] 浏览器任务\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```python\nprint("ok")\n```';
    await test('create, manually save and reload into Markdown preview', async () => {
        first = await create(page, prefix + '-first', initialBody);
        await page.reload({ waitUntil: 'domcontentloaded' });
        await open(page);
        assert.equal(await activeId(page), first);
        await page.locator('#notes-preview h1').waitFor();
        assert.equal(await page.locator('#notes-body').isVisible(), false);
        assert.equal(await page.locator('#notes-preview table').count(), 1);
        assert.ok(await page.locator('#notes-preview .hljs-built_in').count());
    });

    await test('preview task checkbox updates only its source marker and persists', async () => {
        await page.locator('#notes-preview input[type="checkbox"]').check();
        const checkedBody = initialBody.replace('- [ ]', '- [x]');
        await save(page, first, checkedBody);
        assert.equal((await api('/' + first)).note.body, checkedBody);
    });

    await test('arrow sorting persists order without modifying note bodies', async () => {
        second = await create(page, prefix + '-second', 'second note');
        const before = (await api()).order.ids;
        assert.equal(before[0], second);
        const row = page.locator(`[data-note-id="${second}"]`);
        await row.hover();
        await row.locator('[data-action="down"]').click();
        await eventually(async () => (await api()).order.ids[1] === second, 'down arrow persisted');
        await row.hover();
        await row.locator('[data-action="up"]').click();
        await eventually(async () => (await api()).order.ids[0] === second, 'up arrow persisted');
        assert.equal((await api('/' + second)).note.body, 'second note');
    });

    await test('new input during an in-flight save remains dirty and needs its own save', async () => {
        await select(page, first);
        await edit(page);
        await page.locator('#notes-body').fill('snapshot sent to server');
        let intercepted;
        let release;
        const received = new Promise((resolve) => { intercepted = resolve; });
        const blocked = new Promise((resolve) => { release = resolve; });
        const routePattern = new RegExp('/api/notes/' + first + '$');
        const handler = async (route) => {
            if (route.request().method() !== 'PUT') { await route.continue(); return; }
            const response = await route.fetch();
            intercepted();
            await blocked;
            await route.fulfill({ response });
        };
        await page.route(routePattern, handler);
        await page.locator('#notes-save').click();
        await received;
        await page.locator('#notes-body').fill('newer typing while save was pending');
        release();
        await page.waitForFunction(() => !document.querySelector('#notes-save').disabled);
        await page.unroute(routePattern, handler);
        assert.equal((await api('/' + first)).note.body, 'snapshot sent to server');
        assert.equal(await page.locator('#notes-body').inputValue(), 'newer typing while save was pending');
        await save(page, first, 'newer typing while save was pending');
    });

    await test('concurrent server edit produces conflict and save-as-new preserves both copies', async () => {
        await page.locator('#notes-body').fill('local conflict draft');
        const current = (await api('/' + first)).note;
        await api('/' + first, 'PUT', { title: current.title, body: 'external concurrent content', expectedRevision: current.revision, operationId: randomUUID() });
        expectedRequestError = true;
        await page.locator('#notes-save').click();
        await page.locator('#notes-conflict').waitFor({ state: 'visible' });
        await page.locator('#notes-conflict-copy').click();
        await eventually(async () => {
            const id = await activeId(page);
            if (id === first) return false;
            try { return (await api('/' + id)).note.body === 'local conflict draft'; } catch { return false; }
        }, 'conflict copy saved');
        await page.waitForFunction(() => document.querySelector('#notes-save').disabled);
        expectedRequestError = false;
        assert.equal((await api('/' + first)).note.body, 'external concurrent content');
        assert.equal(await page.locator('#notes-conflict').isVisible(), false);
    });

    await test('local IndexedDB draft is offered after reload and restores without silent server overwrite', async () => {
        await select(page, second);
        await edit(page);
        const draft = prefix + ' unsaved draft after refresh';
        await page.locator('#notes-body').fill(draft);
        await page.waitForFunction(async (body) => {
            const database = await new Promise((resolve, reject) => { const r = indexedDB.open('homelab-notes-recovery-v1', 1); r.onsuccess = () => resolve(r.result); r.onerror = reject; });
            return new Promise((resolve) => { const r = database.transaction('drafts').objectStore('drafts').getAll(); r.onsuccess = () => { const found = r.result.some((item) => item.body === body); database.close(); resolve(found); }; });
        }, draft);
        await page.reload({ waitUntil: 'domcontentloaded' });
        await open(page);
        const dialog = page.locator('#notes-recovery-dialog');
        await dialog.waitFor({ state: 'visible' });
        const recovery = dialog.locator('.notes-recovery-item').filter({ hasText: prefix + '-second' });
        await recovery.getByRole('button', { name: '恢复草稿', exact: true }).click();
        assert.equal(await page.locator('#notes-body').inputValue(), draft);
        assert.equal((await api('/' + second)).note.body, 'second note');
        await save(page, second, draft);
    });

    await test('late conflict-copy save cannot discard new typing in the original note', async () => {
        const title = prefix + '-conflict-race';
        const original = await create(page, title, 'original race baseline');
        await page.locator('#notes-body').fill('draft copied into new note');
        const serverNote = (await api('/' + original)).note;
        await api('/' + original, 'PUT', { title, body: 'external race update', expectedRevision: serverNote.revision, operationId: randomUUID() });
        expectedRequestError = true;
        await page.locator('#notes-save').click();
        await page.locator('#notes-conflict').waitFor({ state: 'visible' });
        let intercepted;
        let release;
        let copyId;
        const received = new Promise((resolve) => { intercepted = resolve; });
        const blocked = new Promise((resolve) => { release = resolve; });
        const routePattern = /\/api\/notes\/[a-f0-9-]{36}$/;
        const handler = async (route) => {
            if (route.request().method() !== 'PUT' || route.request().postDataJSON().title !== title || route.request().url().endsWith(original)) { await route.continue(); return; }
            copyId = route.request().url().split('/').pop();
            const response = await route.fetch();
            intercepted();
            await blocked;
            await route.fulfill({ response });
        };
        await page.route(routePattern, handler);
        await page.locator('#notes-conflict-copy').click();
        await received;
        await select(page, original);
        await edit(page);
        await page.locator('#notes-body').fill('new original typing while copied note save is delayed');
        release();
        await eventually(async () => page.locator('.notes-toast').textContent().then((text) => /另存/.test(text)), 'copy completion reported');
        await page.unroute(routePattern, handler);
        assert.equal(await page.locator('#notes-body').inputValue(), 'new original typing while copied note save is delayed');
        assert.equal((await api('/' + copyId)).note.body, 'draft copied into new note');
        assert.equal((await api('/' + original)).note.body, 'external race update');
        assert.match(await page.locator('#notes-save-status').textContent(), /未同步/);
        await page.locator('#notes-conflict-reload').click();
        await page.waitForFunction(() => document.querySelector('#notes-conflict').hidden);
        expectedRequestError = false;
    });

    await test('switching to a slow first read makes stale editor readonly until requested note arrives', async () => {
        const slowId = randomUUID();
        await api('/' + slowId, 'PUT', { title: prefix + '-slow-read', body: 'the newly selected note body', expectedRevision: null, operationId: randomUUID() });
        await page.locator('#notes-close').click();
        await open(page);
        let intercepted;
        let release;
        const received = new Promise((resolve) => { intercepted = resolve; });
        const blocked = new Promise((resolve) => { release = resolve; });
        const routePattern = new RegExp('/api/notes/' + slowId + '$');
        const handler = async (route) => {
            if (route.request().method() !== 'GET') { await route.continue(); return; }
            const response = await route.fetch();
            intercepted();
            await blocked;
            await route.fulfill({ response });
        };
        await page.route(routePattern, handler);
        await page.locator(`[data-note-id="${slowId}"] [data-action="select"]`).click();
        await received;
        const editableDuringLoad = await page.evaluate(() => {
            const body = document.querySelector('#notes-body');
            const title = document.querySelector('#notes-title');
            return { body: !body.hidden && !body.readOnly && !body.disabled, title: title.getBoundingClientRect().width > 0 && !title.readOnly && !title.disabled };
        });
        assert.deepEqual(editableDuringLoad, { body: false, title: false });
        release();
        await page.waitForFunction((value) => document.querySelector('#notes-title').value === value, prefix + '-slow-read');
        await page.unroute(routePattern, handler);
        await edit(page);
        assert.equal(await page.locator('#notes-body').inputValue(), 'the newly selected note body');
        assert.equal((await api('/' + slowId)).note.body, 'the newly selected note body');
    });

    await test('discard-and-reload locks input until the delayed server version arrives', async () => {
        const title = prefix + '-discard-race';
        const id = await create(page, title, 'discard baseline');
        await page.locator('#notes-body').fill('local draft to discard');
        const current = (await api('/' + id)).note;
        await api('/' + id, 'PUT', { title, body: 'server copy selected for reload', expectedRevision: current.revision, operationId: randomUUID() });
        expectedRequestError = true;
        await page.locator('#notes-save').click();
        await page.locator('#notes-conflict').waitFor({ state: 'visible' });
        let intercepted;
        let release;
        const received = new Promise((resolve) => { intercepted = resolve; });
        const blocked = new Promise((resolve) => { release = resolve; });
        const routePattern = new RegExp('/api/notes/' + id + '$');
        const handler = async (route) => {
            if (route.request().method() !== 'GET') { await route.continue(); return; }
            const response = await route.fetch();
            intercepted();
            await blocked;
            await route.fulfill({ response });
        };
        await page.route(routePattern, handler);
        await page.locator('#notes-conflict-reload').click();
        await received;
        assert.equal(await page.locator('#notes-body').evaluate((element) => element.readOnly), true);
        assert.equal(await page.locator('#notes-title').evaluate((element) => element.readOnly), true);
        await page.locator('#notes-body').click();
        await page.keyboard.type('this typing must be blocked while resolving');
        assert.equal(await page.locator('#notes-body').inputValue(), 'local draft to discard');
        release();
        await page.waitForFunction(() => document.querySelector('#notes-conflict').hidden);
        await page.unroute(routePattern, handler);
        await page.locator('#notes-preview').filter({ hasText: 'server copy selected for reload' }).waitFor();
        assert.equal((await api('/' + id)).note.body, 'server copy selected for reload');
        await edit(page);
        await page.locator('#notes-body').fill('new input after resolution');
        await save(page, id, 'new input after resolution');
        expectedRequestError = false;
    });

    await test('saving another note retains a remotely deleted note with an unresolved local draft', async () => {
        const deletedId = await create(page, prefix + '-remote-delete', 'remote-delete baseline');
        await page.locator('#notes-body').fill('local draft must remain reachable');
        const current = (await api('/' + deletedId)).note;
        await api('/' + deletedId, 'DELETE', { expectedRevision: current.revision, operationId: randomUUID() });
        expectedRequestError = true;
        await page.locator('#notes-save').click();
        await page.locator('#notes-conflict').waitFor({ state: 'visible' });
        await page.locator('#notes-new').click();
        await page.locator('#notes-title').fill(prefix + '-after-remote-delete');
        await page.locator('#notes-body').fill('other note saved while deleted original conflicts');
        const otherId = await activeId(page);
        await page.locator('#notes-save').click();
        await eventually(async () => { try { return (await api('/' + otherId)).note.body === 'other note saved while deleted original conflicts'; } catch { return false; } }, 'other note saved');
        await page.waitForFunction(() => !document.querySelector('#notes-save-status').textContent.includes('正在保存'));
        const retained = page.locator(`[data-note-id="${deletedId}"]`);
        assert.equal(await retained.count(), 1);
        assert.match(await retained.locator('small').textContent(), /冲突/);
        await select(page, deletedId);
        await edit(page);
        assert.equal(await page.locator('#notes-body').inputValue(), 'local draft must remain reachable');
        assert.equal(await page.locator('#notes-conflict').isVisible(), true);
        await page.locator('#notes-conflict-reload').click();
        await retained.waitFor({ state: 'detached' });
        expectedRequestError = false;
        assert.equal((await api('/' + otherId)).note.body, 'other note saved while deleted original conflicts');
    });

    await test('pending image upload stays with original note after switching and renders original bytes', async () => {
        await select(page, first);
        await edit(page);
        let intercepted;
        let release;
        const received = new Promise((resolve) => { intercepted = resolve; });
        const blocked = new Promise((resolve) => { release = resolve; });
        const routePattern = /\/api\/notes\/uploads\?/;
        const handler = async (route) => { intercepted(); await blocked; await route.continue(); };
        await page.route(routePattern, handler);
        const chooser = page.waitForEvent('filechooser');
        await page.locator('#notes-image').click();
        const fileChooser = await chooser;
        const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jVAAAAABJRU5ErkJggg==', 'base64');
        await fileChooser.setFiles({ name: prefix + '.png', mimeType: 'image/png', buffer: png });
        await received;
        await select(page, second);
        const secondBefore = (await api('/' + second)).note.body;
        release();
        await eventually(async () => page.locator('#notes-list [data-note-id="' + first + '"] small').textContent().then((text) => text.includes('未同步')), 'original note dirty after upload');
        await page.unroute(routePattern, handler);
        await select(page, first);
        await eventually(async () => (await page.locator('#notes-body').inputValue()).includes('/api/notes/images/'), 'uploaded Markdown inserted');
        const body = await page.locator('#notes-body').inputValue();
        await save(page, first, body);
        assert.equal((await api('/' + second)).note.body, secondBefore);
        const imageUrl = /\/api\/notes\/images\/[a-f0-9-]+/.exec(body)?.[0];
        const response = await fetch(new URL(imageUrl, base));
        assert.equal(response.headers.get('content-type'), 'image/png');
        assert.deepEqual(Buffer.from(await response.arrayBuffer()), png);
        await page.locator('#notes-mode-preview').click();
        await page.waitForFunction(() => [...document.querySelectorAll('#notes-preview img')].some((image) => image.complete && image.naturalWidth > 0));
    });

    await test('collapse saves modifications and reopening retains same-page display mode', async () => {
        await edit(page);
        const body = (await page.locator('#notes-body').inputValue()) + '\n\nSaved by collapse';
        await page.locator('#notes-body').fill(body);
        await page.locator('#notes-close').click();
        await page.locator('#notes-open').waitFor({ state: 'visible' });
        assert.equal((await api('/' + first)).note.body, body);
        await open(page);
        assert.equal(await page.locator('#notes-body').isVisible(), true);
    });

    await test('failed close-save preserves the open panel and its draft until retry succeeds', async () => {
        await select(page, first);
        await edit(page);
        const before = (await api('/' + first)).note.body;
        const body = before + '\n\nPreserve this draft after a failed close';
        await page.locator('#notes-body').fill(body);
        const endpoint = new URL('/api/notes/' + first, base).href;
        const failWrite = (route) => route.request().method() === 'PUT'
            ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'primary_unavailable', message: 'Temporary test failure' }) })
            : route.continue();
        expectedRequestError = true;
        await page.route(endpoint, failWrite);
        try {
            await page.locator('#notes-close').click();
            await page.locator('#notes-keep-draft').waitFor({ state: 'visible' });
            assert.equal(await page.locator('#notes-shell').getAttribute('data-motion'), 'open');
            assert.equal(await page.locator('#notes-shell').getAttribute('data-open'), 'true');
            assert.equal(await page.locator('#notes-body').inputValue(), body);
            assert.equal((await api('/' + first)).note.body, before);
        } finally { await page.unroute(endpoint, failWrite); expectedRequestError = false; }
        await page.evaluate(() => window.dispatchEvent(new Event('focus')));
        await page.waitForFunction(() => !document.getElementById('notes-save').disabled);
        await save(page, first, body);
    });

    await test('mobile always opens list, including reopen after a selected detail', async () => {
        const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, locale: 'zh-CN' });
        const mobile = await mobileContext.newPage();
        watch(mobile);
        await mobile.goto(base.href, { waitUntil: 'domcontentloaded' });
        await open(mobile);
        assert.equal(await mobile.locator('#notes-shell').getAttribute('data-view'), 'list');
        assert.equal(await mobile.locator('.notes-sidebar').isVisible(), true);
        assert.equal(await mobile.locator('#notes-detail').isVisible(), false);
        await select(mobile, first);
        assert.equal(await mobile.locator('#notes-detail').isVisible(), true);
        await mobile.locator('#notes-close').click();
        await open(mobile);
        assert.equal(await mobile.locator('#notes-shell').getAttribute('data-view'), 'list');
        assert.equal(await mobile.locator('#notes-detail').isVisible(), false);
        await mobileContext.close();
    });

    await test('UI delete removes only selected QA note', async () => {
        const row = page.locator(`[data-note-id="${second}"]`);
        await row.hover();
        await row.locator('[data-action="delete"]').click();
        await eventually(async () => !(await api()).notes.some((note) => note.id === second), 'deleted note absent');
        assert.ok((await api()).notes.some((note) => note.id === first));
    });
    assert.deepEqual(errors, [], 'Unexpected Notes browser console/runtime errors');
    console.log(`Notes E2E: ${completed.length} flows passed; no Notes browser runtime errors.`);
} catch (error) {
    console.error(error.stack || error);
    console.error(`Completed ${completed.length} flows: ${completed.join('; ')}`);
    process.exitCode = 1;
} finally {
    if (browser) await browser.close();
    try {
        const current = await api();
        for (const entry of current.notes.filter((note) => note.title.startsWith(prefix))) {
            const { note } = await api('/' + entry.id);
            await api('/' + entry.id, 'DELETE', { expectedRevision: note.revision, operationId: randomUUID() });
        }
        if (originalSettings) {
            const now = (await api('/settings')).settings;
            const { autoSaveEnabled, autoSaveSeconds, saveOnSwitch, collapseOnBlur } = originalSettings;
            await api('/settings', 'PATCH', { expectedRevision: now.revision, operationId: randomUUID(), autoSaveEnabled, autoSaveSeconds, saveOnSwitch, collapseOnBlur });
        }
        const remaining = await api();
        if (originalOrder && remaining.notes.length === originalOrder.length && originalOrder.every((id) => remaining.notes.some((note) => note.id === id))) {
            await api('/order', 'PATCH', { expectedRevision: remaining.order.revision, operationId: randomUUID(), ids: originalOrder });
        }
        console.log(`Cleaned only QA notes with prefix ${prefix}; restored shared settings and original order when applicable.`);
    } catch (error) { console.error(`QA cleanup failed: ${error.message}`); process.exitCode = 1; }
}
