import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const context = vm.createContext({ URL, TextEncoder, document: { baseURI: 'http://127.0.0.1:8875/' } });
const source = await readFile(new URL('../assets/js/favicon-manager.js', import.meta.url), 'utf8');
const module = new vm.SourceTextModule(source, { context });
await module.link((specifier) => {
    const exports = specifier.endsWith('card-svg.js')
        ? { createSafeSvgElement() {}, sanitizeSvgMarkup() {}, cardSvgLimits: {} }
        : specifier.endsWith('core-http.js') ? { fetchWithTimeout() {} } : { createDialogMotionController() {} };
    return new vm.SyntheticModule(Object.keys(exports), function () {
        for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
    }, { context });
});
await module.evaluate();
const normalize = module.namespace.normalizeFaviconState;
const old = { version: 1, configured: false, updatedAt: null, contentHash: null };
assert.equal(normalize(old).version, 1);
const empty = { ...old, version: 2, mimeType: null, width: null, height: null,
    animated: false, posterHash: null, displayBounds: null };
assert.equal(normalize(empty).version, 2);
const image = { ...empty, configured: true, updatedAt: '2026-10-11T00:00:00Z', contentHash: 'a'.repeat(64),
    mimeType: 'image/png', width: 80, height: 48, displayBounds: [3, 3, 77, 45] };
assert.equal(normalize(image).width, 80);
const animation = { ...image, mimeType: 'image/webp', animated: true, posterHash: 'b'.repeat(64) };
assert.equal(normalize(animation).animated, true);
for (const changes of [
    { mimeType: 'text/html' }, { width: true }, { height: 513 }, { displayBounds: [-1, 0, 80, 48] },
    { displayBounds: [3, 3, 3, 3] }, { displayBounds: [0, 0, 81, 48] }, { displayBounds: [0, 0, 2.5, 48] },
    { animated: true }, { posterHash: 'b'.repeat(64) }, { extra: 1 }, { updatedAt: null }
]) assert.throws(() => normalize({ ...image, ...changes }));
assert.throws(() => normalize({ ...animation, posterHash: 'not-a-hash' }));
assert.throws(() => normalize({ ...empty, mimeType: 'image/png' }));
assert.equal(module.namespace.faviconAssetUrl(image.contentHash).searchParams.get('v'), image.contentHash);
assert.throws(() => module.namespace.faviconAssetUrl('invalid'));
console.log('Icon metadata: legacy/v2, MIME, dimensions, bounds, animation poster and hash checks passed.');
