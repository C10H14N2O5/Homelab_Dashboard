import { fetchWithTimeout } from './core-http.js';
import { createSafeSvgElement, sanitizeSvgMarkup } from './card-svg.js';
import { initializeCardEditor } from './card-editor.js';

const CARD_API_ENDPOINT = new URL('/api/cards', document.baseURI);
const CARD_INITIALIZE_ENDPOINT = new URL('/api/cards/initialize', document.baseURI);
const CARD_CACHE_KEY = 'homelab-dashboard-cards-cache-v1';
const MAX_CACHE_BYTES = 1024 * 1024;
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const MAX_CARDS_PER_CHANNEL = 32;
const CARD_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/;
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/;

function cloneValue(value) {
    return JSON.parse(JSON.stringify(value));
}

function utf8ByteLength(value) {
    return new TextEncoder().encode(value).byteLength;
}

function apiChannelKey(channel) {
    return channel === 'quick-links' || channel === 'quickLinks' ? 'quickLinks' : 'services';
}

function validateText(value, field, maximum, allowEmpty = false) {
    if (typeof value !== 'string') throw new Error(`${field}必须是文本。`);
    const normalized = value.trim();
    if ((!allowEmpty && normalized === '') || normalized.length > maximum || CONTROL_CHARACTER_PATTERN.test(normalized)) {
        throw new Error(`${field}不符合长度或字符限制。`);
    }
    return normalized;
}

function validateHttpsUrl(value, field, allowNull = false) {
    if (allowNull && (value === null || value === '')) return null;
    if (typeof value !== 'string' || value.length > 2048 || CONTROL_CHARACTER_PATTERN.test(value)) {
        throw new Error(`${field}格式无效。`);
    }
    let url;
    try {
        url = new URL(value);
    } catch (error) {
        throw new Error(`${field}不是有效网址。`);
    }
    if (url.protocol !== 'https:' || url.username !== '' || url.password !== '') {
        throw new Error(`${field}必须是不含账号信息的 HTTPS 链接。`);
    }
    return url.href;
}

function normalizeCard(card, channel) {
    if (!card || typeof card !== 'object' || Array.isArray(card)) throw new Error('卡片结构无效。');
    if (typeof card.id !== 'string' || !CARD_ID_PATTERN.test(card.id)) throw new Error('卡片 ID 格式无效。');
    const normalized = {
        id: card.id,
        iconSvg: sanitizeSvgMarkup(card.iconSvg),
        name: validateText(card.name, '显示名称', 80),
        url: validateHttpsUrl(card.url, '跳转链接')
    };
    if (channel === 'services') {
        normalized.description = validateText(card.description, '描述', 160);
        normalized.probeUrl = validateHttpsUrl(card.probeUrl, '健康探测链接', true);
    }
    return normalized;
}

function normalizeCards(cards, channel) {
    if (!Array.isArray(cards) || cards.length > MAX_CARDS_PER_CHANNEL) {
        throw new Error(`每个频道最多保存 ${MAX_CARDS_PER_CHANNEL} 张卡片。`);
    }
    const ids = new Set();
    return cards.map((card) => {
        const normalized = normalizeCard(card, channel);
        if (ids.has(normalized.id)) throw new Error(`卡片 ID 重复：${normalized.id}`);
        ids.add(normalized.id);
        return normalized;
    });
}

function normalizeChannel(value, channel) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('频道结构无效。');
    if (!Number.isInteger(value.revision) || value.revision < 1) throw new Error('频道修订号无效。');
    if (typeof value.updatedAt !== 'string' || !value.updatedAt.endsWith('Z') || !Number.isFinite(Date.parse(value.updatedAt))) {
        throw new Error('频道更新时间无效。');
    }
    return {
        revision: value.revision,
        updatedAt: value.updatedAt,
        cards: normalizeCards(value.cards, channel)
    };
}

function normalizeCardsResponse(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload) || payload.version !== 1) {
        throw new Error('卡片 API 版本或结构无效。');
    }
    if (payload.initialized === false && payload.channels === null) {
        return { version: 1, initialized: false, channels: null };
    }
    if (payload.initialized !== true || !payload.channels || typeof payload.channels !== 'object') {
        throw new Error('卡片 API 初始化状态无效。');
    }
    return {
        version: 1,
        initialized: true,
        channels: {
            services: normalizeChannel(payload.channels.services, 'services'),
            quickLinks: normalizeChannel(payload.channels.quickLinks, 'quickLinks')
        }
    };
}

function extractStaticCards() {
    const services = Array.from(document.querySelectorAll('#service-grid .service-card')).map((card) => ({
        id: card.dataset.cardId,
        iconSvg: card.querySelector('.service-icon svg')?.outerHTML || '',
        name: card.querySelector('.service-name')?.textContent || '',
        description: card.querySelector('.service-desc')?.textContent || '',
        url: card.href,
        probeUrl: card.dataset.probeUrl || null
    }));
    const quickLinks = Array.from(document.querySelectorAll('#quick-links-grid .quick-link-card')).map((card) => ({
        id: card.dataset.cardId,
        iconSvg: card.querySelector('.quick-link-icon svg')?.outerHTML || '',
        name: card.querySelector('.quick-link-name')?.textContent || '',
        url: card.href
    }));
    return {
        services: normalizeCards(services, 'services'),
        quickLinks: normalizeCards(quickLinks, 'quickLinks')
    };
}

async function readJsonResponse(response) {
    const contentType = response.headers?.get?.('Content-Type') || '';
    if (!contentType.toLowerCase().includes('application/json')) {
        try {
            await response.body?.cancel?.();
        } catch (error) {
            // Best-effort cancellation only.
        }
        throw new Error('卡片 API 未返回 JSON。');
    }

    const contentLength = response.headers?.get?.('Content-Length');
    if (typeof contentLength === 'string' && /^\d+$/.test(contentLength.trim())
        && Number(contentLength) > MAX_RESPONSE_BYTES) {
        try {
            await response.body?.cancel?.();
        } catch (error) {
            // Best-effort cancellation only.
        }
        throw new Error('卡片 API 响应过大。');
    }

    let text = '';
    if (response.body && typeof response.body.getReader === 'function') {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let receivedBytes = 0;
        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                receivedBytes += value.byteLength;
                if (receivedBytes > MAX_RESPONSE_BYTES) {
                    try {
                        await reader.cancel();
                    } catch (error) {
                        // Best-effort cancellation only.
                    }
                    throw new Error('卡片 API 响应过大。');
                }
                text += decoder.decode(value, { stream: true });
            }
            text += decoder.decode();
        } finally {
            reader.releaseLock?.();
        }
    } else {
        text = await response.text();
        if (utf8ByteLength(text) > MAX_RESPONSE_BYTES) throw new Error('卡片 API 响应过大。');
    }
    try {
        return JSON.parse(text);
    } catch (error) {
        throw new Error('卡片 API 返回了无效 JSON。');
    }
}

function requestOptions(method, body = null) {
    const options = {
        method,
        headers: { 'X-Requested-With': 'homelab-dashboard' },
        credentials: 'same-origin',
        cache: 'no-store'
    };
    if (body !== null) {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
    }
    return options;
}

async function requestCards(resource, options, expectedStatuses) {
    return fetchWithTimeout(resource.href, options, 7000, null, async (response) => {
        const payload = await readJsonResponse(response);
        if (!expectedStatuses.includes(response.status)) {
            const error = new Error(`卡片 API 返回 HTTP ${response.status}`);
            error.status = response.status;
            error.payload = payload;
            throw error;
        }
        return { status: response.status, payload };
    });
}

function createCardElement(card, channel) {
    const link = document.createElement('a');
    link.href = card.url;
    link.dataset.cardId = card.id;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.className = channel === 'services' ? 'service-card' : 'quick-link-card';
    if (channel === 'services' && card.probeUrl) link.dataset.probeUrl = card.probeUrl;
    link.setAttribute('aria-label', channel === 'services'
        ? `打开 ${card.name}，${card.description}`
        : `打开 ${card.name}`);

    const icon = document.createElement('span');
    icon.className = channel === 'services' ? 'service-icon' : 'quick-link-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.append(createSafeSvgElement(card.iconSvg));
    link.append(icon);

    if (channel === 'quickLinks') {
        const name = document.createElement('span');
        name.className = 'quick-link-name';
        name.textContent = card.name;
        link.append(name);
        return link;
    }

    const copy = document.createElement('span');
    copy.className = 'service-copy';
    const name = document.createElement('span');
    name.className = 'service-name';
    name.textContent = card.name;
    const meta = document.createElement('span');
    meta.className = 'service-meta';
    const description = document.createElement('span');
    description.className = 'service-desc';
    description.textContent = card.description;
    const state = document.createElement('span');
    state.className = 'service-state';
    state.dataset.state = 'checking';
    const dot = document.createElement('span');
    dot.className = 'state-dot';
    const stateLabel = document.createElement('span');
    stateLabel.className = 'state-label';
    stateLabel.textContent = '检测中';
    state.append(dot, stateLabel);
    meta.append(description, state);
    copy.append(name, meta);
    link.append(copy);
    return link;
}

function renderGrid(grid, cards, channel) {
    const fragment = document.createDocumentFragment();
    if (cards.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'card-empty-state';
        empty.textContent = channel === 'services' ? '暂无服务卡片' : '暂无快捷入口';
        fragment.append(empty);
    } else {
        cards.forEach((card) => fragment.append(createCardElement(card, channel)));
    }
    grid.replaceChildren(fragment);
}

export async function initializeCardManager(options = {}) {
    const serviceGrid = document.getElementById('service-grid');
    const quickLinksGrid = document.getElementById('quick-links-grid');
    const editorButton = document.getElementById('card-editor-button');
    if (!serviceGrid || !quickLinksGrid || !editorButton) throw new Error('卡片页面结构不完整。');

    // This immutable snapshot is captured before any server/cache rendering and is the only
    // source allowed for one-time initialization of a brand-new backend.
    const staticSnapshot = extractStaticCards();
    const listeners = new Set();
    let currentState = null;
    let currentSource = 'static';
    let serverAvailable = false;

    function updateEditorAvailability(available, reason = '') {
        serverAvailable = available;
        editorButton.disabled = false;
        editorButton.dataset.availability = available ? 'online' : 'retry';
        editorButton.setAttribute('aria-label', available
            ? '修改当前频道的卡片'
            : `修改卡片（${reason || '服务器不可用'}，点击重试）`);
        editorButton.title = available ? '' : `${reason || '卡片服务器不可用'}；点击后将重试连接。`;
    }

    function readCache() {
        try {
            const raw = localStorage.getItem(CARD_CACHE_KEY);
            if (raw === null) return null;
            if (raw === '') {
                removeCache();
                return null;
            }
            if (utf8ByteLength(raw) > MAX_CACHE_BYTES) {
                removeCache();
                return null;
            }
            const cached = normalizeCardsResponse(JSON.parse(raw));
            if (!cached.initialized) {
                removeCache();
                return null;
            }
            return cached;
        } catch (error) {
            removeCache();
            return null;
        }
    }

    function removeCache() {
        try {
            localStorage.removeItem(CARD_CACHE_KEY);
        } catch (error) {
            // Storage can be unavailable in strict privacy modes.
        }
    }

    function writeCache(state) {
        try {
            const serialized = JSON.stringify(state);
            if (utf8ByteLength(serialized) > MAX_CACHE_BYTES) {
                removeCache();
                return;
            }
            localStorage.setItem(CARD_CACHE_KEY, serialized);
        } catch (error) {
            removeCache();
        }
    }

    function notify() {
        listeners.forEach((listener) => {
            try {
                listener(cloneValue(currentState));
            } catch (error) {
                console.warn('卡片更新监听器执行失败。', error);
            }
        });
    }

    function applyState(state, source, cache = false) {
        const normalized = normalizeCardsResponse(state);
        if (!normalized.initialized) throw new Error('不能渲染未初始化的卡片状态。');
        currentState = normalized;
        currentSource = source;
        document.documentElement.dataset.cardSource = source;
        renderGrid(serviceGrid, normalized.channels.services.cards, 'services');
        renderGrid(quickLinksGrid, normalized.channels.quickLinks.cards, 'quickLinks');
        if (cache) writeCache(normalized);
        notify();
        return cloneValue(normalized);
    }

    async function getServerState() {
        const response = await requestCards(CARD_API_ENDPOINT, requestOptions('GET'), [200]);
        return normalizeCardsResponse(response.payload);
    }

    async function initializeServer() {
        let postError = null;
        try {
            const response = await requestCards(
                CARD_INITIALIZE_ENDPOINT,
                requestOptions('POST', {
                    version: 1,
                    services: staticSnapshot.services,
                    quickLinks: staticSnapshot.quickLinks
                }),
                [201, 409]
            );
            if (response.status === 201) return normalizeCardsResponse(response.payload);
            if (response.status === 409 && response.payload?.error !== 'already_initialized') {
                throw new Error('卡片初始化冲突响应无效。');
            }
        } catch (error) {
            postError = error;
        }

        // A race or a lost POST response is resolved only by reading server truth. Never
        // issue a blind second initialize request from this attempt.
        try {
            const confirmed = await getServerState();
            if (confirmed.initialized) return confirmed;
            throw new Error('卡片初始化结果尚未确认。');
        } catch (error) {
            throw postError || error;
        }
    }

    async function loadServerState() {
        const state = await getServerState();
        if (state.initialized) return state;
        return initializeServer();
    }

    async function reloadServerState() {
        const state = await loadServerState();
        const applied = applyState(state, 'server', true);
        updateEditorAvailability(true);
        return applied;
    }

    async function ensureServerState() {
        if (serverAvailable && currentSource === 'server' && currentState) return cloneValue(currentState);
        try {
            return await reloadServerState();
        } catch (error) {
            updateEditorAvailability(false, '无法连接卡片服务器');
            throw error;
        }
    }

    async function saveChannel(channel, expectedRevision, cards) {
        const key = apiChannelKey(channel);
        if (!Number.isInteger(expectedRevision) || expectedRevision < 1) throw new Error('频道修订号无效。');
        const normalizedCards = normalizeCards(cards, key);
        let response;
        try {
            response = await requestCards(
                CARD_API_ENDPOINT,
                requestOptions('PATCH', {
                    version: 1,
                    channel: key,
                    expectedRevision,
                    cards: normalizedCards
                }),
                [200, 409]
            );
        } catch (error) {
            if (error.status !== 400) updateEditorAvailability(false, '保存请求失败');
            throw error;
        }
        if (response.status === 409) {
            const conflict = new Error('服务器上的卡片已被其它页面修改。');
            conflict.name = 'CardRevisionConflictError';
            conflict.currentRevision = response.payload?.currentRevision;
            throw conflict;
        }
        const state = normalizeCardsResponse(response.payload);
        if (!state.initialized) throw new Error('保存响应缺少卡片状态。');
        const applied = applyState(state, 'server', true);
        updateEditorAvailability(true);
        return applied;
    }

    const controller = {
        getState() {
            return currentState ? cloneValue(currentState) : null;
        },
        getSource() {
            return currentSource;
        },
        ensureServerState,
        reloadServerState,
        saveChannel,
        subscribe(listener) {
            if (typeof listener !== 'function') throw new TypeError('卡片监听器必须是函数。');
            listeners.add(listener);
            return () => listeners.delete(listener);
        }
    };

    try {
        const state = await loadServerState();
        applyState(state, 'server', true);
        updateEditorAvailability(true);
    } catch (error) {
        console.warn('卡片服务器不可用，正在使用本地降级数据。', error);
        const cached = readCache();
        if (cached) applyState(cached, 'cache');
        document.documentElement.dataset.cardSource = cached ? 'cache' : 'static';
        updateEditorAvailability(false, cached ? '当前显示上次成功缓存' : '当前显示网页默认卡片');
    }

    try {
        initializeCardEditor(controller, { restoreFocus: options.editorRestoreFocus });
    } catch (error) {
        editorButton.disabled = true;
        editorButton.setAttribute('aria-label', '卡片编辑器初始化失败');
        console.warn('卡片编辑器初始化失败，导航卡片仍可使用。', error);
    }
    return controller;
}
