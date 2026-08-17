import { fetchWithTimeout } from './core-http.js';

// Same-origin service reachability checks and card status rendering.
export function initializeServiceStatus() {
    const serviceStatusEndpoint = new URL('/api/service-status', document.baseURI);
    const cards = Array.from(document.querySelectorAll('.service-card'));
    const serviceCountLabel = document.getElementById('service-count-label');
    const refreshButton = document.getElementById('refresh-status');
    const lastChecked = document.getElementById('last-checked');
    const statusSummary = document.getElementById('status-summary');
            function setCardState(card, state, label) {
                const stateElement = card.querySelector('.service-state');
                stateElement.dataset.state = state;
                stateElement.querySelector('.state-label').textContent = label;
            }

            function initializeServiceCards() {
                serviceCountLabel.textContent = `${cards.length} 个家庭服务入口`;
                cards.forEach((card, index) => {
                    card.style.setProperty('--card-index', String(index));
                });
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

            async function refreshStatuses() {
                refreshButton.disabled = true;
                refreshButton.setAttribute('aria-busy', 'true');
                lastChecked.textContent = `正在检测 ${cards.length} 个入口…`;
                cards.forEach((card) => setCardState(card, 'checking', '检测中'));
                const targets = cards.map((card) => card.dataset.probeUrl || card.href);

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
                    }, 8000, null, async (response) => {
                        if (!response.ok) throw new Error(`状态 API 返回 HTTP ${response.status}`);
                        return response.json();
                    });
                    if (!payload || typeof payload !== 'object' || !Array.isArray(payload.results)
                        || payload.results.length !== targets.length) {
                        throw new Error('状态 API 返回结构不符合契约');
                    }
                    const results = payload.results.map((value, index) => {
                        const result = normalizeServiceStatusResult(value, targets[index]);
                        if (!result) throw new Error(`状态 API 第 ${index + 1} 项不符合契约`);
                        const state = result.error === null
                            ? classifyServiceStatus(result.status)
                            : 'offline';
                        setCardState(cards[index], state, serviceStatusLabel(state, result.status, result.error));
                        return { ...result, state };
                    });
                    const counts = results.reduce((summary, result) => {
                        summary[result.state] += 1;
                        return summary;
                    }, { online: 0, warning: 0, offline: 0 });

                    lastChecked.textContent = `刚刚更新 · ${counts.online}/${cards.length} 服务端可达 · ${counts.warning} 响应异常 · ${counts.offline} 不可用`;
                    statusSummary.textContent = `入口检测完成，${counts.online} 个服务端可达，${counts.warning} 个响应异常，${counts.offline} 个不可用。`;
                } catch (error) {
                    console.warn('状态检测服务不可用。', error);
                    cards.forEach((card) => setCardState(card, 'unknown', '状态未知'));
                    lastChecked.textContent = '状态检测服务不可用';
                    statusSummary.textContent = '状态检测服务不可用，所有入口状态未知；服务链接仍可正常使用。';
                } finally {
                    refreshButton.disabled = false;
                    refreshButton.setAttribute('aria-busy', 'false');
                }
            }

    initializeServiceCards();
    refreshButton.addEventListener('click', refreshStatuses);
    refreshStatuses();
}
