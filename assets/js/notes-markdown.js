// Parser and syntax grammars run outside the UI thread. Each container owns
// its current request; navigation cancels obsolete work without stale writes.
import { fetchNoteImage } from './notes-api.js';
const renderStates = new WeakMap();
const IMAGE_PATH = /^\/api\/notes\/images\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
let purifyPromise;
let sequence = 0;

function safeUrl(value, image) {
    if (!value || /[\u0000-\u0020\u007f\\]/.test(value)) return false;
    if (IMAGE_PATH.test(value)) return true;
    try {
        if (!/^(?:https?:\/\/|mailto:)/i.test(value)) return false;
        const parsed = new URL(value);
        if (parsed.username || parsed.password) return false;
        return image ? parsed.protocol === 'https:' : ['http:', 'https:', 'mailto:'].includes(parsed.protocol);
    } catch { return false; }
}

function showPlaintext(source, container, reason) {
    const wrapper = document.createElement('div');
    wrapper.className = 'notes-markdown notes-markdown-fallback';
    const status = document.createElement('p');
    status.className = 'notes-render-status';
    status.setAttribute('role', 'status');
    status.textContent = reason;
    const pre = document.createElement('pre');
    pre.textContent = source;
    wrapper.append(status, pre);
    container.replaceChildren(wrapper);
}

export function cancelNotesMarkdown(container) {
    renderStates.get(container)?.cancel();
    renderStates.delete(container);
}

export async function renderNotesMarkdown(source, container, { onTaskToggle, readOnly = false } = {}) {
    cancelNotesMarkdown(container);
    const input = typeof source === 'string' ? source : '';
    const requestId = ++sequence;
    let resolveDone;
    const done = new Promise((resolve) => { resolveDone = resolve; });
    let worker;
    let timer;
    let completed = false;
    const resources = new Set();
    const images = new Map();
    const abort = new AbortController();
    let observer;
    const state = {
        cancel() {
            abort.abort(); observer?.disconnect();
            for (const url of resources) URL.revokeObjectURL(url);
            resources.clear();
            if (completed) return;
            completed = true;
            clearTimeout(timer);
            worker?.terminate();
            resolveDone({ cancelled: true });
        },
    };
    renderStates.set(container, state);
    const active = () => !completed && renderStates.get(container) === state;
    const owned = () => !abort.signal.aborted && renderStates.get(container) === state;
    function imageUrl(path) {
        if (!images.has(path)) images.set(path, fetchNoteImage(path, abort.signal).then((blob) => {
            if (!owned()) throw new Error('Obsolete image');
            const url = URL.createObjectURL(blob); resources.add(url); return url;
        }));
        return images.get(path);
    }
    function protectLink(link, path) {
        link.href = '#';
        link.addEventListener('click', async (event) => {
            if (!owned()) { event.preventDefault(); return; }
            if (link.getAttribute('href').startsWith('blob:')) return;
            event.preventDefault();
            try { const url = await imageUrl(path); if (owned()) { link.href = url; link.click(); } }
            catch { /* Keep the original Markdown attachment unchanged. */ }
        });
    }
    const finish = (result) => {
        if (!active()) return;
        completed = true;
        clearTimeout(timer);
        worker?.terminate();
        resolveDone(result);
    };
    const fallback = (reason) => {
        if (!active()) return;
        showPlaintext(input, container, reason);
        finish({ fallback: true });
    };
    timer = setTimeout(() => fallback('预览处理超时，暂时显示 Markdown 原文。'), 2000);
    try {
        worker = new Worker(new URL('./notes-render-worker.js', import.meta.url), { type: 'module' });
        worker.addEventListener('error', () => fallback('预览暂不可用，暂时显示 Markdown 原文。'));
        worker.addEventListener('message', async ({ data }) => {
            if (!active() || data?.id !== requestId) return;
            if (data.error || typeof data.html !== 'string' || !Array.isArray(data.tasks)) {
                fallback('预览暂不可用，暂时显示 Markdown 原文。');
                return;
            }
            try {
                purifyPromise ||= import('../vendor/dompurify/purify.mjs').catch((error) => { purifyPromise = null; throw error; });
                const { default: purify } = await purifyPromise;
                if (!active()) return;
                const fragment = purify.sanitize(data.html, {
                    RETURN_DOM_FRAGMENT: true,
                    ALLOWED_TAGS: ['p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'em', 's', 'blockquote', 'ul', 'ol', 'li', 'pre', 'code', 'a', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span'],
                    ALLOWED_ATTR: ['href', 'src', 'alt', 'title', 'class', 'start', 'data-notes-task'],
                    ALLOW_DATA_ATTR: false,
                    ALLOW_ARIA_ATTR: false,
                });
                for (const link of fragment.querySelectorAll('a')) {
                    if (safeUrl(link.getAttribute('href'), false)) {
                        link.target = '_blank';
                        link.rel = 'noopener noreferrer';
                        if (IMAGE_PATH.test(link.getAttribute('href'))) protectLink(link, link.getAttribute('href'));
                    } else link.removeAttribute('href');
                }
                for (const image of fragment.querySelectorAll('img')) {
                    const src = image.getAttribute('src');
                    if (!safeUrl(src, true)) { image.remove(); continue; }
                    image.loading = 'lazy';
                    image.decoding = 'async';
                    image.referrerPolicy = 'no-referrer';
                    const link = document.createElement('a');
                    link.href = src;
                    link.target = '_blank';
                    link.rel = 'noopener noreferrer';
                    link.className = 'notes-image-link';
                    image.replaceWith(link);
                    link.append(image);
                    if (IMAGE_PATH.test(src)) {
                        image.removeAttribute('src');
                        protectLink(link, src);
                        const load = () => imageUrl(src).then((url) => { if (owned()) { image.src = url; link.href = url; } }).catch(() => { if (owned()) image.dispatchEvent(new Event('error')); });
                        image._notesLoad = load;
                    }
                    image.addEventListener('error', () => {
                        if (!owned()) return;
                        const missing = document.createElement('span');
                        missing.className = 'notes-image-missing';
                        missing.textContent = `[图片不可用：${image.alt || '图片'}]`;
                        link.replaceWith(missing);
                    }, { once: true });
                }
                for (const marker of fragment.querySelectorAll('[data-notes-task]')) {
                    const task = data.tasks[Number(marker.getAttribute('data-notes-task'))];
                    if (!task || !Number.isInteger(task.offset) || !/^\[[ xX]\]$/.test(input.slice(task.offset - 1, task.offset + 2))) { marker.remove(); continue; }
                    const checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.className = 'notes-task-checkbox';
                    checkbox.checked = task.checked;
                    checkbox.disabled = readOnly || typeof onTaskToggle !== 'function';
                    checkbox.setAttribute('aria-label', marker.parentElement?.textContent?.trim().slice(0, 150) || '切换任务完成状态');
                    checkbox.addEventListener('change', () => {
                        // Offsets belong to this render. The owner should render
                        // updated content; an obsolete DOM must not edit a note.
                        if (renderStates.get(container) !== state) return;
                        onTaskToggle?.({ offset: task.offset, checked: checkbox.checked });
                    });
                    marker.replaceWith(checkbox);
                }
                const wrapper = document.createElement('div');
                wrapper.className = 'notes-markdown';
                wrapper.append(fragment);
                if (!active()) return;
                container.replaceChildren(wrapper);
                const protectedImages = wrapper.querySelectorAll('img');
                if (typeof IntersectionObserver === 'function') {
                    observer = new IntersectionObserver((entries) => { for (const entry of entries) if (entry.isIntersecting) { observer.unobserve(entry.target); entry.target._notesLoad?.(); } }, { rootMargin: '200px' });
                    for (const image of protectedImages) if (image._notesLoad) observer.observe(image);
                } else for (const image of protectedImages) image._notesLoad?.();
                finish({ fallback: false, tasks: data.tasks });
            } catch { fallback('预览暂不可用，暂时显示 Markdown 原文。'); }
        });
        worker.postMessage({ id: requestId, source: input });
    } catch { fallback('浏览器暂不支持笔记预览，显示 Markdown 原文。'); }
    return done;
}
