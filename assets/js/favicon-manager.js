import { createSafeSvgElement, sanitizeSvgMarkup, cardSvgLimits } from './card-svg.js';
import { fetchWithTimeout } from './core-http.js';
import { createDialogMotionController } from './dialog-motion.js';

const FAVICON_STATE_ENDPOINT = new URL('/api/favicon', document.baseURI);
const FAVICON_ASSET_ENDPOINT = new URL('/api/favicon.svg', document.baseURI);
const MAX_FAVICON_RESPONSE_BYTES = 16 * 1024;
const FAVICON_HASH_PATTERN = /^[0-9a-f]{64}$/;

function utf8ByteLength(value) {
    if (typeof TextEncoder === 'function') return new TextEncoder().encode(value).byteLength;
    return unescape(encodeURIComponent(value)).length;
}

function isUtcTimestamp(value) {
    return typeof value === 'string'
        && value.endsWith('Z')
        && Number.isFinite(Date.parse(value));
}

export function normalizeFaviconState(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error('网站图标 API 返回了无效状态。');
    }
    const keys = Object.keys(value).sort();
    const expectedKeys = ['configured', 'contentHash', 'updatedAt', 'version'];
    if (keys.length !== expectedKeys.length || keys.some((key, index) => key !== expectedKeys[index])) {
        throw new Error('网站图标 API 状态字段无效。');
    }
    if (value.version !== 1 || typeof value.configured !== 'boolean') {
        throw new Error('网站图标 API 状态版本无效。');
    }
    if (value.configured) {
        if (!isUtcTimestamp(value.updatedAt) || !FAVICON_HASH_PATTERN.test(value.contentHash)) {
            throw new Error('网站图标 API 已配置状态无效。');
        }
    } else if (
        value.contentHash !== null
        || (value.updatedAt !== null && !isUtcTimestamp(value.updatedAt))
    ) {
        throw new Error('网站图标 API 未配置状态无效。');
    }
    return {
        version: 1,
        configured: value.configured,
        updatedAt: value.updatedAt,
        contentHash: value.contentHash
    };
}

export function faviconAssetUrl(contentHash) {
    if (!FAVICON_HASH_PATTERN.test(contentHash)) throw new Error('网站图标内容哈希无效。');
    const url = new URL(FAVICON_ASSET_ENDPOINT.href);
    url.searchParams.set('v', contentHash);
    return url;
}

async function readJsonResponse(response) {
    const contentType = response.headers?.get?.('Content-Type') || '';
    if (!contentType.toLowerCase().includes('application/json')) {
        throw new Error('网站图标 API 返回了非 JSON 响应。');
    }
    const declaredLength = Number(response.headers?.get?.('Content-Length'));
    if (Number.isFinite(declaredLength) && declaredLength > MAX_FAVICON_RESPONSE_BYTES) {
        throw new Error('网站图标 API 响应过大。');
    }
    const text = await response.text();
    if (utf8ByteLength(text) > MAX_FAVICON_RESPONSE_BYTES) {
        throw new Error('网站图标 API 响应过大。');
    }
    try {
        return JSON.parse(text);
    } catch (error) {
        throw new Error('网站图标 API 返回了无效 JSON。');
    }
}

async function requestFavicon(method, iconSvg) {
    const options = {
        method,
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {
            Accept: 'application/json',
            'X-Requested-With': 'homelab-dashboard'
        }
    };
    if (method === 'PATCH') {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify({ version: 1, iconSvg });
    }
    return fetchWithTimeout(FAVICON_STATE_ENDPOINT.href, options, 7000, null, async (response) => {
        let payload = null;
        try {
            payload = await readJsonResponse(response);
        } catch (error) {
            if (response.status >= 200 && response.status < 300) throw error;
        }
        if (response.status !== 200) {
            const error = new Error(`网站图标 API 返回 HTTP ${response.status}`);
            error.status = response.status;
            error.payload = payload;
            throw error;
        }
        return normalizeFaviconState(payload);
    });
}

export function initializeFaviconManager(options = {}) {
    const launchButton = document.getElementById('favicon-editor-button');
    const dialog = document.getElementById('favicon-dialog');
    const closeButton = document.getElementById('favicon-close');
    const preview = document.getElementById('favicon-preview');
    const stateLabel = document.getElementById('favicon-state-label');
    const selectButton = document.getElementById('favicon-select-button');
    const fileInput = document.getElementById('favicon-file-input');
    const removeButton = document.getElementById('favicon-remove-button');
    const saveButton = document.getElementById('favicon-save-button');
    const status = document.getElementById('favicon-status');
    const confirmDialog = document.getElementById('favicon-remove-confirm-dialog');
    const confirmCancel = document.getElementById('favicon-remove-cancel');
    const confirmAccept = document.getElementById('favicon-remove-confirm');
    const confirmStatus = document.getElementById('favicon-remove-status');
    const required = [
        launchButton, dialog, closeButton, preview, stateLabel, selectButton, fileInput,
        removeButton, saveButton, status, confirmDialog, confirmCancel, confirmAccept,
        confirmStatus
    ];
    if (required.some((element) => !element)) throw new Error('网站图标编辑器页面结构不完整。');

    const restoreFocus = typeof options.restoreFocus === 'function'
        ? options.restoreFocus
        : () => launchButton.focus({ preventScroll: true });
    const dialogMotion = createDialogMotionController(dialog, 280);
    const confirmMotion = createDialogMotionController(confirmDialog, 220);
    let serverState = null;
    let availability = 'loading';
    let draftSvg = null;
    let draftFileName = '';
    let busy = false;
    let readToken = 0;
    let requestToken = 0;
    let confirmResolver = null;

    function managedLink() {
        return document.head.querySelector('link[data-managed-favicon="true"]');
    }

    function applyManagedLink(state) {
        if (!state?.configured) {
            managedLink()?.remove();
            return;
        }
        let link = managedLink();
        if (!link) {
            link = document.createElement('link');
            link.dataset.managedFavicon = 'true';
            document.head.append(link);
        }
        link.rel = 'icon';
        link.type = 'image/svg+xml';
        link.href = faviconAssetUrl(state.contentHash).href;
    }

    function setStatus(message, kind = '') {
        status.textContent = message;
        if (kind) status.dataset.kind = kind;
        else delete status.dataset.kind;
    }

    function availabilityMessage() {
        if (availability === 'unsupported') return '当前状态服务版本不支持网站图标设置，请先升级后端。';
        if (availability === 'unavailable') return '网站图标服务当前不可用；现有页面功能不受影响。';
        if (availability === 'loading') return '正在读取服务器上的网站图标设置…';
        return '';
    }

    function renderPreview() {
        preview.replaceChildren();
        if (draftSvg) {
            preview.append(createSafeSvgElement(draftSvg));
            stateLabel.textContent = `待保存：${draftFileName}`;
            return;
        }
        if (serverState?.configured) {
            const image = document.createElement('img');
            image.src = faviconAssetUrl(serverState.contentHash).href;
            image.alt = '';
            image.decoding = 'async';
            preview.append(image);
            stateLabel.textContent = '当前服务器图标';
            return;
        }
        const empty = document.createElement('span');
        empty.className = 'favicon-preview-empty';
        empty.textContent = '无';
        preview.append(empty);
        stateLabel.textContent = '当前未设置网站图标';
    }

    function syncControls() {
        const online = availability === 'online';
        closeButton.disabled = busy;
        selectButton.disabled = busy || !online;
        fileInput.disabled = busy || !online;
        saveButton.disabled = busy || !online || !draftSvg;
        removeButton.disabled = busy || !online || !serverState?.configured;
        dialog.setAttribute('aria-busy', String(busy));
        confirmCancel.disabled = busy;
        confirmAccept.disabled = busy;
    }

    function render() {
        renderPreview();
        syncControls();
    }

    async function refreshState(options = {}) {
        const token = ++requestToken;
        if (!serverState) availability = 'loading';
        if (dialogMotion.isOpen() && options.announce !== false) {
            setStatus(availabilityMessage());
            syncControls();
        }
        try {
            const state = await requestFavicon('GET');
            if (token !== requestToken) return null;
            serverState = state;
            availability = 'online';
            applyManagedLink(state);
            render();
            if (dialogMotion.isOpen() && options.announce !== false) {
                setStatus(state.configured ? '已载入当前服务器图标。' : '服务器当前未设置网站图标。');
            }
            return state;
        } catch (error) {
            if (token !== requestToken) return null;
            availability = error.status === 404 ? 'unsupported' : 'unavailable';
            render();
            if (dialogMotion.isOpen() && options.announce !== false) {
                setStatus(availabilityMessage(), 'error');
            }
            return null;
        }
    }

    function clearDraft() {
        readToken += 1;
        draftSvg = null;
        draftFileName = '';
        fileInput.value = '';
    }

    function openEditor() {
        if (dialogMotion.isOpen() || dialogMotion.isClosing()) return;
        setStatus(availabilityMessage());
        render();
        dialogMotion.show(() => {
            if (availability === 'online') selectButton.focus({ preventScroll: true });
            else closeButton.focus({ preventScroll: true });
        });
        void refreshState();
    }

    async function closeEditor() {
        if (busy || dialogMotion.isClosing()) return;
        clearDraft();
        render();
        await dialogMotion.hide('', restoreFocus);
    }

    async function importSvgFile() {
        const file = fileInput.files?.[0];
        fileInput.value = '';
        if (!file || busy) return;
        if (!/\.svg$/i.test(file.name) || file.size <= 0 || file.size > cardSvgLimits.rawBytes) {
            setStatus('请选择大于 0 字节且不超过 64 KiB 的 .svg 文件。', 'error');
            return;
        }
        const token = ++readToken;
        try {
            const cleanSvg = sanitizeSvgMarkup(await file.text());
            if (token !== readToken || busy || !dialogMotion.isOpen()) return;
            draftSvg = cleanSvg;
            draftFileName = file.name;
            render();
            setStatus('SVG 已通过本地安全检查；确认保存前不会修改服务器。', 'success');
            saveButton.focus({ preventScroll: true });
        } catch (error) {
            if (token !== readToken) return;
            setStatus(error.message || '无法读取 SVG 文件。', 'error');
        }
    }

    async function saveDraft() {
        if (busy || availability !== 'online' || !draftSvg) return;
        requestToken += 1;
        busy = true;
        syncControls();
        setStatus('正在保存网站图标并等待服务器确认…');
        let focusAfterWrite = null;
        try {
            const state = await requestFavicon('PATCH', draftSvg);
            requestToken += 1;
            serverState = state;
            availability = 'online';
            clearDraft();
            applyManagedLink(state);
            render();
            setStatus('网站图标已保存，当前页面已立即更新。', 'success');
            focusAfterWrite = selectButton;
        } catch (error) {
            if (error.status === 404) availability = 'unsupported';
            else availability = 'online';
            render();
            setStatus(
                error.status === 400
                    ? '服务器拒绝了该 SVG；请检查图标内容后重试。'
                    : error.status === 404
                        ? availabilityMessage()
                        : '保存失败；本页草稿仍在，可直接重试。',
                'error'
            );
            focusAfterWrite = availability === 'unsupported' ? closeButton : saveButton;
        } finally {
            busy = false;
            syncControls();
            focusAfterWrite?.focus({ preventScroll: true });
        }
    }

    function askRemoveConfirmation() {
        if (busy || confirmResolver || confirmMotion.isClosing()) return Promise.resolve(false);
        confirmStatus.textContent = '';
        return new Promise((resolve) => {
            confirmResolver = resolve;
            confirmMotion.show(() => confirmCancel.focus({ preventScroll: true }));
        });
    }

    async function settleRemoveConfirmation(result) {
        if (!confirmResolver || confirmMotion.isClosing()) return;
        const resolve = confirmResolver;
        confirmResolver = null;
        await confirmMotion.hide(result ? 'confirm' : 'cancel', () => {
            if (removeButton.isConnected && !removeButton.disabled) {
                removeButton.focus({ preventScroll: true });
            }
        });
        resolve(result);
    }

    async function removeFavicon() {
        if (busy || availability !== 'online' || !serverState?.configured) return;
        if (!await askRemoveConfirmation()) return;
        requestToken += 1;
        busy = true;
        syncControls();
        setStatus('正在移除服务器网站图标…');
        let focusAfterWrite = null;
        try {
            const state = await requestFavicon('PATCH', null);
            requestToken += 1;
            serverState = state;
            availability = 'online';
            clearDraft();
            applyManagedLink(state);
            render();
            setStatus('网站图标已移除；其他页面刷新后会同步。', 'success');
            focusAfterWrite = selectButton;
        } catch (error) {
            if (error.status === 404) availability = 'unsupported';
            else availability = 'online';
            render();
            setStatus(
                error.status === 404
                    ? availabilityMessage()
                    : '移除失败；服务器上的网站图标未在本页更改，可再次尝试。',
                'error'
            );
            focusAfterWrite = availability === 'unsupported' ? closeButton : removeButton;
        } finally {
            busy = false;
            syncControls();
            focusAfterWrite?.focus({ preventScroll: true });
        }
    }

    launchButton.addEventListener('click', openEditor);
    closeButton.addEventListener('click', () => { void closeEditor(); });
    selectButton.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => { void importSvgFile(); });
    saveButton.addEventListener('click', () => { void saveDraft(); });
    removeButton.addEventListener('click', () => { void removeFavicon(); });
    dialog.addEventListener('cancel', (event) => {
        event.preventDefault();
        void closeEditor();
    });
    dialog.addEventListener('click', (event) => {
        if (event.target === dialog) void closeEditor();
    });
    confirmCancel.addEventListener('click', () => { void settleRemoveConfirmation(false); });
    confirmAccept.addEventListener('click', () => { void settleRemoveConfirmation(true); });
    confirmDialog.addEventListener('cancel', (event) => {
        event.preventDefault();
        void settleRemoveConfirmation(false);
    });
    confirmDialog.addEventListener('close', () => {
        if (!confirmResolver) return;
        const resolve = confirmResolver;
        confirmResolver = null;
        resolve(false);
    });

    render();
    void refreshState({ announce: false });
    return { applyManagedLink, closeEditor, openEditor, refreshState };
}
