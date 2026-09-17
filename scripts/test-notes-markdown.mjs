import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { parseNotesMarkdown, isSafeNotesUrl } from '../assets/js/notes-render-worker.js';

let checks = 0;
function check(name, run) {
    run();
    checks += 1;
    console.log(`PASS ${name}`);
}
const image = '/api/notes/images/40e6215d-b5c6-4896-987c-f30f3678f608';

check('task offsets identify exact original characters in nested, quoted, ordered and CRLF lists', () => {
    const source = '- [ ] outer\r\n  - [x] nested\r\n\r\n> 1. [X] quoted\r\n\r\n4. [ ] 最后 😀\r\n';
    const { html, tasks } = parseNotesMarkdown(source);
    assert.deepEqual(tasks, [...source.matchAll(/\[([ xX])\]/g)].map((m) => ({ offset: m.index + 1, checked: m[1].toLowerCase() === 'x' })));
    assert.equal((html.match(/data-notes-task=/g) || []).length, 4);
    assert.ok(!html.includes('<input'));
    const selected = tasks[3];
    const toggled = source.slice(0, selected.offset) + 'x' + source.slice(selected.offset + 1);
    assert.equal(toggled, source.replace('[ ] 最后', '[x] 最后'));
});

check('code, escaped markers, non-list paragraphs and later item paragraphs are not tasks', () => {
    const source = '```md\n- [ ] fence\n```\n\n    - [ ] code\n\n[ ] paragraph\n\n- \\[ ] escaped\n\n- ordinary\n\n  [ ] later paragraph\n\n- > [ ] quote nested in item\n\n- `[ ]` code span\n';
    assert.deepEqual(parseNotesMarkdown(source).tasks, []);
});

check('tasks map duplicate task text and preserve source formatting on toggle', () => {
    const source = '> - [ ] same [ ] text\n> - [ ] same [ ] text\n\n- [ ]\n';
    const { tasks } = parseNotesMarkdown(source);
    assert.deepEqual(tasks.map((x) => x.offset), [5, 27, 48]);
});

check('task-like HTML cannot create controlled task markers', () => {
    const { html, tasks } = parseNotesMarkdown('<span data-notes-task="0"><input checked></span>\n');
    assert.deepEqual(tasks, []);
    assert.ok(html.includes('&lt;span'));
    assert.ok(!html.includes('<input'));
});

check('tables, strikethrough, lists and headings render', () => {
    const { html } = parseNotesMarkdown('# Heading\n\n~~done~~\n\n| A | B |\n|---|---|\n| 1 | 2 |\n');
    assert.match(html, /<h1>Heading<\/h1>/);
    assert.match(html, /<s>done<\/s>/);
    assert.match(html, /<table>/);
});

for (const url of ['https://example.com/image.png', image]) {
    check(`allow image ${url}`, () => assert.equal(isSafeNotesUrl(url, true), true));
}
for (const url of ['http://example.com/a.png', '//example.com/a.png', 'data:image/svg+xml,evil', 'javascript:alert(1)', 'file:///c:/a.png', '../image.png', '/api/notes/images/../settings', '/api/notes/images/not-a-uuid', 'https://user:pass@example.com/x', 'https:\\evil.test', 'https://x.test/\u0000a']) {
    check(`reject image ${JSON.stringify(url)}`, () => assert.equal(isSafeNotesUrl(url, true), false));
}
check('mailto and HTTP allowed only as links', () => {
    assert.equal(isSafeNotesUrl('mailto:test@example.com'), true);
    assert.equal(isSafeNotesUrl('http://example.com'), true);
    assert.equal(isSafeNotesUrl('mailto:test@example.com', true), false);
});
check('raw HTML and unsafe Markdown cannot create executable markup', () => {
    const { html } = parseNotesMarkdown('<img src=x onerror=alert(1)>\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n[x](javascript&#x3a;alert(1))\n\n![bad](http://evil.test/a.png)\n\n[x](file:///private)');
    assert.ok(!/<(?:script|img)\b/i.test(html));
    assert.ok(!/href="(?:javascript|file):/i.test(html));
    assert.ok(html.includes('notes-image-missing'));
});
check('valid images preserve sources and safe link attributes', () => {
    const { html } = parseNotesMarkdown(`![managed](${image})\n\n![external](https://example.com/image.png)\n\n[link](https://example.com)`);
    assert.match(html, /referrerpolicy="no-referrer"/);
    assert.match(html, /target="_blank" rel="noopener noreferrer"/);
    assert.ok(html.includes(`src="${image}"`));
});
check('named language highlights but plain, unknown and oversized blocks do not', () => {
    assert.match(parseNotesMarkdown('```python\nprint("ok")\n```').html, /hljs-/);
    assert.match(parseNotesMarkdown('```powershell\nGet-ChildItem\n```').html, /hljs-/);
    assert.ok(!parseNotesMarkdown('```\nprint("ok")\n```').html.includes('hljs-'));
    assert.ok(!parseNotesMarkdown('```no-such-language\nprint("ok")\n```').html.includes('hljs-'));
    assert.ok(!parseNotesMarkdown('```python\n' + 'print("你好")\n'.repeat(3000) + '```').html.includes('hljs-'));
});

const manifest = JSON.parse(await readFile(new URL('../assets/vendor/notes-dependencies.json', import.meta.url), 'utf8'));
assert.deepEqual(manifest.dependencies.map((x) => [x.name, x.version]), [['markdown-it', '15.0.2'], ['highlight.js', '11.12.0'], ['DOMPurify', '3.4.15']]);
for (const dependency of manifest.dependencies) {
    for (const artifact of dependency.files) {
        const body = await readFile(new URL('../assets/vendor/' + artifact.path, import.meta.url));
        assert.equal(createHash('sha256').update(body).digest('hex'), artifact.sha256, `Pinned artifact differs: ${artifact.path}`);
    }
}
console.log(`PASS pinned dependency integrity (${manifest.dependencies.length} packages)`);
console.log(`Notes Markdown: ${checks + 1} checks passed.`);
