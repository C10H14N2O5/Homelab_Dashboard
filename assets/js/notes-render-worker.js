import MarkdownIt from '../vendor/markdown-it/markdown-it.mjs';
import hljs from '../vendor/highlightjs/core.mjs';
import python from '../vendor/highlightjs/python.mjs';
import powershell from '../vendor/highlightjs/powershell.mjs';
import bash from '../vendor/highlightjs/bash.mjs';
import javascript from '../vendor/highlightjs/javascript.mjs';
import typescript from '../vendor/highlightjs/typescript.mjs';
import json from '../vendor/highlightjs/json.mjs';
import yaml from '../vendor/highlightjs/yaml.mjs';
import xml from '../vendor/highlightjs/xml.mjs';
import css from '../vendor/highlightjs/css.mjs';
import sql from '../vendor/highlightjs/sql.mjs';
import ini from '../vendor/highlightjs/ini.mjs';
import diff from '../vendor/highlightjs/diff.mjs';

const MAX_HIGHLIGHT_BYTES = 32 * 1024;
const internalImage = /^\/api\/notes\/images\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const encoder = new TextEncoder();
for (const [name, grammar] of Object.entries({ python, powershell, bash, javascript, typescript, json, yaml, xml, css, sql, ini, diff })) {
    hljs.registerLanguage(name, grammar);
}

export function isSafeNotesUrl(value, image = false) {
    if (typeof value !== 'string' || /[\u0000-\u0020\u007f\\]/.test(value)) return false;
    if (internalImage.test(value)) return true;
    if (!/^(?:https?:\/\/|mailto:)/i.test(value)) return false;
    try {
        const url = new URL(value);
        if (url.username || url.password) return false;
        return image ? url.protocol === 'https:' : ['http:', 'https:', 'mailto:'].includes(url.protocol);
    } catch { return false; }
}

const md = new MarkdownIt({
    html: false,
    linkify: false,
    typographer: false,
    breaks: false,
    highlight(code, language) {
        if (!language || encoder.encode(code).length > MAX_HIGHLIGHT_BYTES || !hljs.getLanguage(language)) return '';
        try { return hljs.highlight(code, { language, ignoreIllegals: true }).value; }
        catch { return ''; }
    },
});

// The parser's block maps exclude fenced/code text and identify the exact list
// paragraph. Match its first content line back to the unnormalized source so
// checkbox offsets remain valid with CRLF, block quotes, and nested lists.
md.core.ruler.after('inline', 'notes_tasks', (state) => {
    const env = state.env;
    const stack = [];
    for (let i = 0; i < state.tokens.length; i += 1) {
        const token = state.tokens[i];
        if (token.type === 'list_item_open') stack.push({ firstParagraph: true, level: token.level });
        else if (token.type === 'list_item_close') stack.pop();
        else if (token.type === 'paragraph_open' && stack.length) {
            const item = stack[stack.length - 1];
            if (token.level !== item.level + 1) continue;
            const firstParagraph = item.firstParagraph;
            item.firstParagraph = false;
            const inline = state.tokens[i + 1];
            if (!firstParagraph || inline?.type !== 'inline' || !inline.map) continue;
            const match = /^\[([ xX])\](?:[ \t]+|$)/.exec(inline.content);
            if (!match || inline.children?.[0]?.type !== 'text' || !inline.children[0].content.startsWith(`[${match[1]}]`)) continue;
            const lineNumber = inline.map[0];
            const rawLine = env.lines[lineNumber];
            const firstContentLine = inline.content.split('\n', 1)[0];
            const column = rawLine?.lastIndexOf(firstContentLine) ?? -1;
            if (column < 0) continue;
            const offset = env.lineOffsets[lineNumber] + column + 1;
            // Be conservative when normalization changed source text: a task
            // can remain ordinary Markdown rather than toggle the wrong byte.
            if (env.source.slice(offset - 1, offset + 2) !== `[${match[1]}]`) continue;
            const task = { offset, checked: match[1].toLowerCase() === 'x' };
            const marker = new state.Token('notes_task_marker', '', 0);
            marker.meta = { index: env.tasks.length };
            env.tasks.push(task);
            inline.children[0].content = inline.children[0].content.slice(match[0].length);
            inline.children.unshift(marker);
        }
    }
});
md.renderer.rules.notes_task_marker = (tokens, index) => `<span data-notes-task="${tokens[index].meta.index}"></span>`;

const defaultImage = md.renderer.rules.image;
md.renderer.rules.image = (tokens, index, options, env, renderer) => {
    const token = tokens[index];
    const src = token.attrGet('src') || '';
    if (!isSafeNotesUrl(src, true)) return `<span class="notes-image-missing">[图片地址不可用：${md.utils.escapeHtml(token.content || '图片')}]</span>`;
    token.attrSet('loading', 'lazy');
    token.attrSet('decoding', 'async');
    token.attrSet('referrerpolicy', 'no-referrer');
    return defaultImage(tokens, index, options, env, renderer);
};
const defaultLink = md.renderer.rules.link_open || ((tokens, index, options, env, renderer) => renderer.renderToken(tokens, index, options));
md.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
    const token = tokens[index];
    if (!isSafeNotesUrl(token.attrGet('href') || '')) {
        token.attrs = (token.attrs || []).filter(([name]) => name !== 'href');
    } else {
        token.attrSet('target', '_blank');
        token.attrSet('rel', 'noopener noreferrer');
    }
    return defaultLink(tokens, index, options, env, renderer);
};

export function parseNotesMarkdown(source) {
    const input = typeof source === 'string' ? source : '';
    const lines = input.split(/\r\n|\r|\n/);
    const lineOffsets = [0];
    const lineBreak = /\r\n|\r|\n/g;
    let match;
    while ((match = lineBreak.exec(input)) !== null) lineOffsets.push(match.index + match[0].length);
    const env = { source: input, lines, lineOffsets, tasks: [] };
    return { html: md.render(input, env), tasks: env.tasks };
}

if (typeof self !== 'undefined' && typeof document === 'undefined' && typeof self.addEventListener === 'function') {
    self.addEventListener('message', ({ data }) => {
        const { id, source } = data || {};
        try { self.postMessage({ id, ...parseNotesMarkdown(source) }); }
        catch { self.postMessage({ id, error: '笔记预览暂不可用' }); }
    });
}
