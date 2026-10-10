// Isolated preview only: settings path display, rebind and local draft retention.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { cp, mkdir, realpath, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import path from 'node:path';

const require = createRequire(import.meta.url);
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(path.join(homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const base = new URL(process.argv[2] || 'http://127.0.0.1:8765/');
assert.equal(base.hostname, '127.0.0.1');
const root = path.resolve(process.argv[3] || 'output/notes-links-preview');
assert.equal(path.dirname(root), path.resolve('output'), 'Only a workspace output preview is allowed');
const entry = path.join(root, 'notes-backup-link');
const original = await realpath(entry);
const moved = path.join(root, `backup-ui-${Date.now()}`);
const output = path.resolve('output/notes-links-e2e');
await mkdir(output, { recursive: true });

async function api(suffix, method = 'GET', payload) {
    const response = await fetch(new URL('/api/notes' + suffix, base), { method, headers: { Origin: base.origin, 'X-Requested-With': 'homelab-dashboard', 'Content-Type': 'application/json' }, ...(payload === undefined ? {} : { body: JSON.stringify(payload) }) });
    const result = await response.json();
    assert.equal(response.ok, true, JSON.stringify(result));
    return result;
}
async function job(response) {
    for (let i = 0; i < 150; i++) {
        const result = (await api('/jobs/' + response.job.id)).job;
        if (result.status !== 'running') { assert.equal(result.status, 'complete', JSON.stringify(result)); return result; }
        await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error('Maintenance job did not finish');
}
function repoint(target) {
    const result = spawnSync(path.resolve('.venv/Scripts/python.exe'), ['-c', 'import sys; sys.path.insert(0, "state-service"); from pathlib import Path; from tests.helpers import repoint_directory; repoint_directory(Path(sys.argv[1]), Path(sys.argv[2]))', entry, target], { windowsHide: true, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
}
for (let i = 0; i < 150; i++) {
    if (!(await api('/status')).backupPending) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
}
await cp(original, moved, { recursive: true });
const browser = await playwright.chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage();
const title = `QA-linked-draft-${Date.now()}`;
try {
    await page.goto(base.href);
    await page.locator('#notes-open').click();
    await page.locator('#notes-new').click();
    await page.locator('#notes-title').fill(title);
    await page.locator('#notes-body').fill('链接切换期间保留本机草稿');
    repoint(moved);
    await page.locator('#notes-settings-inline').click();
    await page.waitForFunction(() => document.querySelector('#notes-backup-path').textContent.includes('→') && document.querySelector('#notes-backup-status').textContent.includes('目标已变化'));
    assert.match(await page.locator('#notes-backup-path').innerText(), /backup-ui-/);
    await page.locator('#notes-settings-dialog').screenshot({ path: path.join(output, 'settings-target-changed.png') });
    await page.locator('#notes-backup-check').click();
    const apply = page.getByRole('button', { name: '应用并重新绑定', exact: true });
    await apply.waitFor();
    await apply.click();
    await page.waitForFunction(() => document.querySelector('#notes-reconcile-dialog [role="status"]').textContent.includes('已重新绑定'));
    await page.locator('#notes-reconcile-dialog button[aria-label="关闭"]').click();
    await page.locator('#notes-settings-dialog button[aria-label="关闭"]').click();
    assert.equal(await page.locator('#notes-title').inputValue(), title);
    assert.equal(await page.locator('#notes-body').inputValue(), '链接切换期间保留本机草稿');
    await page.locator('#notes-save').click();
    await page.waitForFunction(() => document.querySelector('#notes-save-status').textContent.includes('已保存'));
    const note = (await api('')).notes.find((item) => item.title === title);
    assert.ok(note);
    await api('/' + note.id, 'DELETE', { expectedRevision: note.revision, operationId: crypto.randomUUID() });
    console.log('PASS link target display, no-difference rebind and dirty draft retention');
} finally {
    repoint(original);
    const checked = await job(await api('/reconcile/check', 'POST', {}));
    await job(await api('/reconcile/apply', 'POST', { checkId: checked.id, choices: checked.result.items.map((item) => ({ id: item.id, action: 'use-primary' })) }));
    await api('/backup/retry', 'POST', {});
    await browser.close();
    await writeFile(path.join(output, 'report.json'), JSON.stringify({ url: base.href, original, moved, screenshot: 'settings-target-changed.png' }, null, 2));
}
