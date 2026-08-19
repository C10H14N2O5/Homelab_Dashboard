import { fetchWithTimeout } from './core-http.js';

// Same-origin service reachability checks and card status rendering.
export function initializeServiceStatus() {
    const serviceStatusEndpoint = new URL('/api/service-status', document.baseURI);
    const serviceCountLabel = document.getElementById('service-count-label');
    const refreshButton = document.getElementById('refresh-status');
    const lastChecked = document.getElementById('last-checked');
    const statusSummary = document.getElementById('status-summary');
    const dashboard = document.getElementById('dashboard');
    if (!serviceCountLabel || !refreshButton || !lastChecked || !statusSummary || !dashboard) {
        throw new Error('服务状态页面结构不完整。');
    }

    let cards = [];
    let requestGeneration = 0;
    let activeController = null;

    function setCardState(card, state, label) {
        const stateElement = card.querySelector('.service-state');
        const stateLabel = stateElement?.querySelector('.state-label');
        if (!stateElement || !stateLabel) return;
        stateElement.dataset.state = state;
        stateLabel.textContent = label;
    }

    function classifyServiceStatus(status) {
        if (Number.isInteger(status) && status >= 200 && status < 400) return 'online';
        if (Number.isInteger(status) && status >= 400 && status < 500) return 'warning';
        return 'offline';
    }

    function serviceStatusLabel(state, status = null, error = null) {
        if (error === 'redirect_rejected') return `重定向被拒绝 · HTTP ${status}`;
        if (error !== null && Number.isInteger(status)) return `探测失败 · HTTP ${status}`;
        if (state === 'online') return '服务端可达';
        if (state === 'warning') return `异常 · HTTP ${status}`;
        if (Number.isInteger(status)) return `不可用 · HTTP ${status}`;
        return '不可连接';
    }

    function normalizeServiceStatusResult(value, target) {
        if (!value || typeof value !== 'object' || value.target !== target) return null;
        const status = value.status;
        const latencyMs = value.latencyMs;
        const error = value.error;
        const validStatus = status === null || (Number.isInteger(status) && status >= 100 && status <= 599);
        const validLatency = typeof latencyMs === 'number' && Number.isFinite(latencyMs) && latencyMs >= 0;
        const validErrors = new Set([
            'invalid_target',
            'timeout',
            'tls_error',
            'redirect_rejected',
            'connection_error',
            'request_error'
        ]);
        if (!validStatus || !validLatency || (error !== null && !validErrors.has(error))) return null;
        if (status === null && error === null) return null;
        return { target, status, latencyMs, error };
    }

    function initializeServiceCards() {
        cards = Array.from(document.querySelectorAll('#service-grid .service-card'));
        cards.forEach((card, index) => {
            card.style.setProperty('--card-index', String(index));
            setCardState(card, 'checking', '检测中');
        });
        if (dashboard.dataset.activeChannel !== 'quick-links') {
            serviceCountLabel.textContent = `${cards.length} 个家庭服务入口`;
        }
    }

    async function refreshStatuses() {
        requestGeneration += 1;
        const generation = requestGeneration;
        if (activeController) activeController.abort();
        activeController = typeof AbortController === 'function' ? new AbortController() : null;
        const requestCards = cards.slice();

        if (requestCards.length === 0) {
            refreshButton.disabled = true;
            refreshButton.setAttribute('aria-busy', 'false');
            lastChecked.textContent = '暂无服务入口';
            statusSummary.textContent = '当前 Services 频道没有卡片，因此未发送状态检测请求。';
            activeController = null;
            return;
        }

        refreshButton.disabled = true;
        refreshButton.setAttribute('aria-busy', 'true');
        lastChecked.textContent = `正在检测 ${requestCards.length} 个入口…`;
        requestCards.forEach((card) => setCardState(card, 'checking', '检测中'));
        const targets = requestCards.map((card) => card.dataset.probeUrl || card.href);

        try {
            const payload = await fetchWithTimeout(serviceStatusEndpoint.href, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Requested-With': 'homelab-dashboard'
                },
                credentials: 'same-origin',
                cache: 'no-store',
                body: JSON.stringify({ targets })
            }, 8000, activeController, async (response) => {
                if (!response.ok) throw new Error(`状态 API 返回 HTTP ${response.status}`);
                return response.json();
            });
            if (generation !== requestGeneration) return;
            if (!payload || typeof payload !== 'object' || !Array.isArray(payload.results)
                || payload.results.length !== targets.length) {
                throw new Error('状态 API 返回结构不符合契约');
            }
            const results = payload.results.map((value, index) => {
                const result = normalizeServiceStatusResult(value, targets[index]);
                if (!result) throw new Error(`状态 API 第 ${index + 1} 项不符合契约`);
                const state = result.error === null ? classifyServiceStatus(result.status) : 'offline';
                const card = requestCards[index];
                if (card.isConnected) {
                    setCardState(card, state, serviceStatusLabel(state, result.status, result.error));
                }
                return { ...result, state };
            });
            if (generation !== requestGeneration) return;
            const counts = results.reduce((summary, result) => {
                summary[result.state] += 1;
                return summary;
            }, { online: 0, warning: 0, offline: 0 });
            lastChecked.textContent = `刚刚更新 · ${counts.online}/${requestCards.length} 服务端可达 · ${counts.warning} 响应异常 · ${counts.offline} 不可用`;
            statusSummary.textContent = `入口检测完成，${counts.online} 个服务端可达，${counts.warning} 个响应异常，${counts.offline} 个不可用。`;
        } catch (error) {
            if (generation !== requestGeneration) return;
            console.warn('状态检测服务不可用。', error);
            requestCards.forEach((card) => {
                if (card.isConnected) setCardState(card, 'unknown', '状态未知');
            });
            lastChecked.textContent = '状态检测服务不可用';
            statusSummary.textContent = '状态检测服务不可用，所有入口状态未知；服务链接仍可正常使用。';
        } finally {
            if (generation === requestGeneration) {
                activeController = null;
                refreshButton.disabled = cards.length === 0;
                refreshButton.setAttribute('aria-busy', 'false');
            }
        }
    }

    function resync(options = {}) {
        requestGeneration += 1;
        if (activeController) activeController.abort();
        activeController = null;
        initializeServiceCards();
        if (options.refresh === false) {
            refreshButton.disabled = cards.length === 0;
            return;
        }
        refreshStatuses();
    }

    refreshButton.addEventListener('click', refreshStatuses);
    resync();
    return { resync, refresh: refreshStatuses };
}
