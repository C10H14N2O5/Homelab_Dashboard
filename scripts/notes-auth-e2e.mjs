// Isolated protected preview only. Usage: node scripts/notes-auth-e2e.mjs URL PASSWORD_FILE
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile, mkdir } from 'node:fs/promises';
import { homedir } from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
const require = createRequire(import.meta.url);
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(path.join(homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const base = new URL(process.argv[2] || 'http://127.0.0.1:8766/');
if (!['127.0.0.1', 'localhost'].includes(base.hostname)) throw new Error('Use an isolated loopback preview');
const password = await readFile(process.argv[3], 'utf8');
const out = 'output/notes-auth-e2e'; await mkdir(out, { recursive: true });
let token;
async function api(endpoint, method = 'GET', body) {
    const response = await fetch(new URL('/api/notes' + endpoint, base), { method, headers: { Origin: base.origin, 'X-Requested-With': 'homelab-dashboard', ...(token ? { 'X-Notes-Token': token } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    const result = await response.json(); if (!response.ok) throw Object.assign(new Error(`API failed: ${response.status}`), { status: response.status }); return result;
}
token = (await api('/auth/unlock', 'POST', { password })).token;
const id = randomUUID();
await api('/' + id, 'PUT', { expectedRevision: null, operationId: randomUUID(), title: 'QA authorization', body: '# Private test body' });
const browser = await playwright.chromium.launch({ channel: process.env.NOTES_TEST_BROWSER || 'msedge', headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, locale: 'zh-CN' });
const page = await context.newPage(); page.setDefaultTimeout(12000);
let pageToken;
const errors = [];
const responses = [];
page.on('response', (response) => { if (new URL(response.url()).pathname.startsWith('/api/notes')) responses.push({ path: new URL(response.url()).pathname, status: response.status() }); });
page.on('pageerror', (error) => errors.push(error.message));
page.on('request', (request) => { const current = request.headers()['x-notes-token']; if (current) pageToken = current; });
page.on('dialog', (dialog) => dialog.accept());
async function open(target = page) { await target.locator('#notes-open').click(); await target.locator('#notes-auth-form').waitFor({ state: 'visible' }); await target.waitForFunction(() => !document.querySelector('#notes-password').disabled); await target.waitForTimeout(800); }
async function unlock(target = page) { await target.waitForFunction(() => !document.querySelector('#notes-panel').inert && !document.querySelector('#notes-password').disabled); await target.locator('#notes-password').fill(password); assert.equal(await target.locator('#notes-password').inputValue(), password); await target.locator('#notes-unlock').click(); await target.waitForFunction(() => document.querySelector('#notes-auth-form').hidden && !document.querySelector('#notes-new').disabled); }
async function select() { await page.locator(`[data-note-id="${id}"] .notes-note-select`).click(); await page.locator('#notes-mode-edit').click(); await page.locator('#notes-body').waitFor({ state: 'visible' }); }
try {
    await page.goto(base.href); await open();
    await page.screenshot({ path: out + '/desktop-locked.png' });
    assert.equal(await page.locator('#notes-list').textContent(), '');
    await page.locator('#notes-password').fill('wrong'); await page.locator('#notes-unlock').click();
    await page.waitForFunction(() => document.querySelector('#notes-auth-message').textContent.includes('密码错误'));
    await unlock(); console.log('PASS locked screen and correct/incorrect password');
    await Promise.all([page.waitForNavigation(), page.evaluate(() => location.reload())]); await page.locator('#notes-open').click();
    try { await page.waitForFunction(() => document.querySelector('#notes-auth-form').hidden && !document.querySelector('#notes-new').disabled); }
    catch (error) { console.log('Reload state:', await page.evaluate(() => ({ type: navigation.activation?.navigationType, key: navigation.currentEntry?.key, storage: !!sessionStorage.getItem('homelab-notes-reload-v1'), message: document.querySelector('#notes-auth-message').textContent }))); throw error; }
    assert.equal(await page.evaluate(() => sessionStorage.getItem('homelab-notes-reload-v1')), null);
    console.log('PASS ordinary reload retains access and consumes handoff');
    const independent = await context.newPage(); await independent.goto(base.href); await open(independent);
    assert.equal(await independent.locator('#notes-list').textContent(), ''); await independent.close(); await page.bringToFront();
    console.log('PASS independent same-browser tab stays locked');
    await select(); await page.locator('#notes-body').fill('Save before manual lock'); await page.locator('#notes-lock').click();
    await page.locator('#notes-auth-form').waitFor({ state: 'visible' });
    assert.equal((await api('/' + id)).note.body, 'Save before manual lock');
    assert.equal(await page.locator('#notes-body').inputValue(), ''); console.log('PASS lock saves dirty body and clears contents');
    await unlock(); await select(); await page.locator('#notes-body').fill('Preserve on save failure');
    const failure = async (route) => route.request().method() === 'PUT' ? route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"notes_unavailable"}' }) : route.continue();
    await page.route('**/api/notes/' + id, failure); await page.locator('#notes-lock').click();
    await page.waitForFunction(() => !document.querySelector('#notes-lock').disabled);
    assert.equal(await page.locator('#notes-auth-form').isVisible(), false);
    assert.equal(await page.locator('#notes-body').inputValue(), 'Preserve on save failure');
    await page.unroute('**/api/notes/' + id, failure);
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    try { await page.waitForFunction(() => !document.querySelector('#notes-body').readOnly); }
    catch (error) { console.log('Recovery state:', await page.evaluate(() => ({ debug: window.__notesDebug?.(), hidden: document.hidden, locked: document.querySelector('#notes-shell').dataset.locked, motion: document.querySelector('#notes-shell').dataset.motion, switchPhase: document.querySelector('#notes-editor-pane').dataset.switchPhase, status: document.querySelector('#notes-save-status').textContent, notice: document.querySelector('#notes-notice').textContent }))); console.log('Recent request statuses:', responses.slice(-12)); throw error; }
    const captured = token; token = pageToken; await api('/auth/lock', 'POST', {}); token = captured;
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.locator('#notes-auth-form').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#notes-body').inputValue(), '');
    await unlock(); await select();
    assert.equal(await page.locator('#notes-body').inputValue(), 'Preserve on save failure');
    console.log('PASS failed save retains draft; revoked access hides and unlock restores it');
    await page.locator('#notes-save').click();
    await page.waitForFunction(() => document.querySelector('#notes-save').disabled && !document.querySelector('#notes-save-status').textContent.includes('正在保存'));
    const remote = (await api('/' + id)).note;
    await page.locator('#notes-body').fill('Local conflict draft');
    await api('/' + id, 'PUT', { expectedRevision: remote.revision, operationId: randomUUID(), title: remote.title, body: 'Concurrent remote body' });
    await page.locator('#notes-lock').click();
    await page.waitForFunction(() => !document.querySelector('#notes-lock').disabled && !document.querySelector('#notes-conflict').hidden);
    assert.equal(await page.locator('#notes-auth-form').isVisible(), false);
    assert.equal(await page.locator('#notes-body').inputValue(), 'Local conflict draft');
    await page.locator('#notes-conflict-reload').click();
    await page.waitForFunction(() => document.querySelector('#notes-title').value === 'QA authorization' && document.querySelector('#notes-conflict').hidden);
    console.log('PASS lock with revision conflict retains local body');
    await select();
    let releaseUpload;
    let uploadSeen;
    const receivedUpload = new Promise((resolve) => { uploadSeen = resolve; });
    const uploadGate = new Promise((resolve) => { releaseUpload = resolve; });
    const holdUpload = async (route) => { uploadSeen(); await uploadGate; await route.continue(); };
    await page.route('**/api/notes/uploads?**', holdUpload);
    await page.locator('#notes-image-input').setInputFiles({ name: 'auth-lock.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==', 'base64') });
    await receivedUpload; await page.locator('#notes-lock').click();
    assert.equal(await page.locator('#notes-auth-form').isVisible(), false);
    assert.match(await page.locator('#notes-save-status').textContent(), /等待图片/);
    releaseUpload();
    await page.locator('#notes-auth-form').waitFor({ state: 'visible', timeout: 30000 });
    await page.unroute('**/api/notes/uploads?**', holdUpload);
    assert.ok((await api('/' + id)).note.attachments.length > 0);
    await unlock(); await select();
    console.log('PASS lock waits for in-flight image upload and saves its attachment');
    await page.locator('#notes-mode-preview').click();
    const imageNote = await page.locator('.notes-note-select').filter({ hasText: 'Markdown 示例' }).count();
    if (imageNote) {
        await page.locator('.notes-note-select').filter({ hasText: 'Markdown 示例' }).click();
        await page.waitForFunction(() => [...document.querySelectorAll('#notes-preview img')].some((image) => image.src.startsWith('blob:') && image.naturalWidth));
        const popup = page.waitForEvent('popup'); await page.locator('#notes-preview .notes-image-link').click(); const viewer = await popup;
        await viewer.waitForLoadState(); assert.ok(viewer.url().startsWith('blob:')); await viewer.close();
        console.log('PASS authenticated image preview and original view use Blob URLs');
    }
    const mobile = await context.newPage(); await mobile.setViewportSize({ width: 390, height: 844 }); await mobile.goto(base.href); await open(mobile);
    await mobile.screenshot({ path: out + '/mobile-locked.png' }); await unlock(mobile); await mobile.screenshot({ path: out + '/mobile-unlocked.png' }); await mobile.close();
    const unavailable = await context.newPage();
    await unavailable.addInitScript(() => Object.defineProperty(window, 'sessionStorage', { get() { throw new Error('Unavailable'); } }));
    await unavailable.goto(base.href); await open(unavailable); await unlock(unavailable); await unavailable.reload(); await open(unavailable); await unavailable.close();
    console.log('PASS mobile viewport and refresh with unavailable storage requires password');
    const settingsPage = await context.newPage(); await settingsPage.goto(base.href);
    await settingsPage.locator('#page-settings-button').click(); await settingsPage.locator('#notes-settings-button').click();
    await settingsPage.locator('#notes-auth-form').waitFor({ state: 'visible' });
    assert.equal(await settingsPage.locator('#notes-settings-dialog').isVisible(), false);
    await unlock(settingsPage); await settingsPage.locator('#notes-settings-dialog').waitFor({ state: 'visible' });
    await settingsPage.waitForFunction(() => document.querySelector('#notes-primary-path').textContent.includes('notes-primary'));
    assert.match(await settingsPage.locator('#notes-primary-path').textContent(), /notes-primary/);
    await settingsPage.close(); console.log('PASS settings cannot bypass unlock and continues after password verification');
    const reduced = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1280, height: 900 } });
    const reducedPage = await reduced.newPage(); await reducedPage.goto(base.href); await open(reducedPage); await unlock(reducedPage);
    assert.equal(await reducedPage.evaluate(() => document.querySelector('#notes-auth-form').getAnimations().length + document.querySelector('.notes-workspace').getAnimations().length), 0);
    await reduced.close(); console.log('PASS reduced motion skips authorization interpolation');
    const legacy = await context.newPage(); await legacy.route('**/api/notes/auth', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
    await legacy.goto(base.href); await legacy.locator('#notes-open').click(); await legacy.waitForFunction(() => document.querySelector('#notes-auth-message').textContent.includes('升级后端')); await legacy.close();
    assert.deepEqual(errors, []); console.log('PASS old backend fails closed; no uncaught page errors');
} finally {
    await browser.close();
    const note = (await api('/' + id)).note;
    await api('/' + id, 'DELETE', { expectedRevision: note.revision, operationId: randomUUID() });
    await api('/auth/lock', 'POST', {});
}
