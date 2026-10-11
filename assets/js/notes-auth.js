import { requestNotes, configureNotesAuthorization, cancelNotesRequests } from './notes-api.js';

const HANDOFF_KEY = 'homelab-notes-reload-v1';
const HANDOFF_MS = 30000;

export function createNotesAuth() {
    const listeners = new Set();
    let initialization;
    let reloadPending = false;
    let waitUntil = 0;
    const auth = {
        token: null, allowed: false, required: false, enabled: true, epoch: 0, error: '',
        get retrySeconds() { return Math.max(0, Math.ceil((waitUntil - Date.now()) / 1000)); },
        subscribe(listener) { listeners.add(listener); },
        invalidate() {
            auth.epoch++; auth.token = null; auth.allowed = false;
            clearHandoff(); cancelNotesRequests(); announce();
        },
        async ready() {
            if (!initialization) initialization = initialize().catch((error) => {
                auth.error = error.status === 404 ? '当前后端不支持笔记验证，请升级后端。' : '无法确认笔记访问权限，请重试。';
                auth.allowed = false; announce();
            });
            await initialization;
            return auth.allowed;
        },
        async retry() { initialization = null; auth.error = ''; return auth.ready(); },
        async unlock(password) {
            const epoch = auth.epoch;
            try {
                const result = await requestNotes('/auth/unlock', 'POST', { password });
                if (epoch !== auth.epoch) return false;
                if (auth.required && (typeof result.token !== 'string' || !result.token)) throw new Error('笔记服务返回了无效的解锁凭据。');
                auth.token = result.token; auth.epoch++; auth.allowed = true; auth.error = ''; waitUntil = 0; announce();
                return true;
            } catch (error) {
                waitUntil = Date.now() + (error.payload?.retryAfterSeconds || 0) * 1000;
                throw error;
            }
        },
        async lock() {
            const epoch = auth.epoch;
            await requestNotes('/auth/lock', 'POST', {});
            if (epoch === auth.epoch) auth.invalidate();
        }
    };
    configureNotesAuthorization(auth);
    function clearHandoff() { try { sessionStorage.removeItem(HANDOFF_KEY); } catch { /* No persistence fallback. */ } reloadPending = false; }
    function announce() { for (const listener of listeners) listener(auth); }
    async function initialize() {
        const epoch = auth.epoch;
        let saved;
        try { saved = JSON.parse(sessionStorage.getItem(HANDOFF_KEY) || 'null'); } catch { /* Reverify on unavailable storage. */ }
        clearHandoff();
        const status = await requestNotes('/auth');
        if (epoch !== auth.epoch) return;
        if (typeof status.enabled !== 'boolean' || typeof status.passwordRequired !== 'boolean') throw new Error('Invalid authentication status');
        auth.enabled = status.enabled; auth.required = status.passwordRequired;
        waitUntil = Date.now() + (status.retryAfterSeconds || 0) * 1000;
        if (!status.enabled) { auth.error = '笔记尚未启用。'; announce(); return; }
        if (!status.passwordRequired) { auth.allowed = true; auth.epoch++; announce(); return; }
        const navigation = globalThis.navigation;
        const reloading = navigation?.activation?.navigationType === 'reload';
        if (reloading && saved?.token && saved.key === navigation.currentEntry?.key && Date.now() >= saved.createdAt && Date.now() - saved.createdAt <= HANDOFF_MS) {
            auth.token = saved.token;
            try {
                const resumed = await requestNotes('/auth/resume', 'POST', {});
                if (epoch !== auth.epoch) return;
                if (typeof resumed.token !== 'string' || !resumed.token) throw new Error('Invalid token');
                auth.token = resumed.token; auth.allowed = true; auth.epoch++;
            } catch { auth.token = null; auth.allowed = false; }
        }
        announce();
    }
    globalThis.navigation?.addEventListener('navigate', (event) => {
        clearHandoff();
        if (event.navigationType !== 'reload' || !auth.token || !auth.allowed || !globalThis.navigation.activation) return;
        try {
            sessionStorage.setItem(HANDOFF_KEY, JSON.stringify({ token: auth.token, key: globalThis.navigation.currentEntry.key, createdAt: Date.now() }));
            reloadPending = true;
        } catch { /* A refresh without a handoff requires the password. */ }
    });
    globalThis.navigation?.addEventListener('navigateerror', clearHandoff);
    window.addEventListener('pagehide', () => {
        if (!reloadPending && auth.token) {
            void fetch('/api/notes/auth/lock', { method: 'POST', keepalive: true, credentials: 'same-origin', headers: { 'X-Requested-With': 'homelab-dashboard', 'X-Notes-Token': auth.token, 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {});
        }
        // Clear private DOM before a page can enter the back/forward cache.
        auth.epoch++; auth.allowed = false; auth.token = null; cancelNotesRequests(); announce();
    });
    window.addEventListener('pageshow', (event) => { if (event.persisted) { clearHandoff(); initialization = null; void auth.ready(); } });
    // Consume refresh handoff during document startup, even if the panel stays collapsed.
    void auth.ready();
    return auth;
}
