import { fetchWithTimeout } from './core-http.js';

// One-click Bing China + international daily-image download controller.
export function createBingDailyController(context) {
    const {
        clearBackgroundResultPresentation,
        isManagerBusy,
        isManagerOnline,
        normalizeBackgroundName,
        presentBackgroundResults,
        scanBackgroundDirectory,
        setManagerStatus,
        syncManagerControls
    } = context;
    const bingDailyEndpoint = new URL('/api/background-files/bing-daily', document.baseURI);
    const bingDailyButton = document.getElementById('background-bing-daily-button');
    let bingDailyPending = false;

    function bingDailyErrorMessage(payload, status) {
        const error = payload && typeof payload.error === 'string' ? payload.error : '';
        const knownMessages = {
            bing_operation_in_progress: '另一项 Bing 每日一图操作正在进行，请稍后再试。',
            background_file_quota_exceeded: '背景目录已达到 512 张上限，请先手动删除不再需要的图片。'
        };
        if (Object.prototype.hasOwnProperty.call(knownMessages, error)) return knownMessages[error];
        const oldBackendRejectedBody = status === 400 && error === 'request body is not allowed for this route';
        if (status === 404 || status === 405 || oldBackendRejectedBody) {
            return '当前背景管理服务不支持“每日一图”，请先升级后端。';
        }
        return `Bing 每日一图接口返回 ${status}`;
    }

    function normalizeBingDailyResult(value) {
        if (!value || typeof value !== 'object') throw new Error('Bing 每日一图接口返回了无效项目');
        const slot = value.slot;
        const status = value.status;
        if (!['cn', 'international'].includes(slot) || !['created', 'existing', 'failed'].includes(status)) {
            throw new Error('Bing 每日一图接口返回了无效项目');
        }
        const result = { slot, status };
        if (status === 'failed') {
            const knownErrors = ['bing_unavailable', 'bing_invalid_image', 'background_file_quota_exceeded'];
            if (!knownErrors.includes(value.error)) throw new Error('Bing 每日一图接口返回了无效错误');
            result.error = value.error;
            result.market = typeof value.market === 'string' ? value.market : '';
            result.fallback = slot === 'international' && value.fallback === true;
            return result;
        }

        const background = normalizeBackgroundName(value.background);
        const allowedMarkets = slot === 'cn' ? ['zh-CN'] : ['ROW', 'en-US'];
        if (!background || !allowedMarkets.includes(value.market)) {
            throw new Error('Bing 每日一图接口返回了无效文件信息');
        }
        result.background = background;
        result.market = value.market;
        result.fallback = slot === 'international' && value.fallback === true;
        return result;
    }

    function normalizeBingDailyPayload(payload) {
        if (!payload || payload.version !== 1 || !Array.isArray(payload.results) || payload.results.length !== 2) {
            throw new Error('Bing 每日一图接口返回了无效数据');
        }
        const results = payload.results.map(normalizeBingDailyResult);
        if (new Set(results.map((result) => result.slot)).size !== 2
            || !results.some((result) => result.slot === 'cn')
            || !results.some((result) => result.slot === 'international')) {
            throw new Error('Bing 每日一图接口返回了重复或缺失的市场');
        }
        const slotOrder = { cn: 0, international: 1 };
        return results.sort((left, right) => slotOrder[left.slot] - slotOrder[right.slot]);
    }

    async function requestBingDaily() {
        return fetchWithTimeout(bingDailyEndpoint.href, {
            method: 'POST',
            cache: 'no-store',
            credentials: 'same-origin',
            headers: {
                Accept: 'application/json',
                'Content-Type': 'application/json',
                'X-Requested-With': 'homelab-dashboard'
            },
            body: '{}'
        }, 120000, null, async (response) => {
            let payload = null;
            try {
                payload = await response.json();
            } catch (error) {
                // Keep the HTTP status useful when an intermediary returns non-JSON.
            }
            if (payload && Array.isArray(payload.results)) return normalizeBingDailyPayload(payload);
            if (!response.ok) throw new Error(bingDailyErrorMessage(payload, response.status));
            throw new Error('Bing 每日一图接口返回了无效数据');
        });
    }

    function resultLabel(result) {
        if (result.slot === 'cn') return '中国版';
        return result.market === 'en-US' || result.fallback ? '国际版（ROW 不可用，已回退 US）' : '国际版 ROW';
    }

    function resultMessage(result) {
        const label = resultLabel(result);
        if (result.status === 'created') return `${label}已保存为 ${result.background}`;
        if (result.status === 'existing') return `${label}已存在：${result.background}`;
        if (result.error === 'background_file_quota_exceeded') return `${label}未保存：背景目录已达到 512 张上限`;
        if (result.error === 'bing_invalid_image') return `${label}未保存：Bing 返回的图片无效或不受支持`;
        if (result.slot === 'international') {
            return `${label}获取失败：请检查状态服务所在主机的国际版 HTTP 代理及其外网连通性`;
        }
        return `${label}获取失败：暂时无法连接或识别 Bing 图片`;
    }

    async function fetchBingDaily() {
        if (isManagerBusy() || !isManagerOnline()) {
            setManagerStatus('背景管理服务正忙或暂不可用，请稍后再试。', 'error');
            return false;
        }

        bingDailyPending = true;
        clearBackgroundResultPresentation();
        syncManagerControls();
        syncAvailability(true, true);
        setManagerStatus('正在获取 Bing 中国版与国际版每日一图，请稍候…');
        let createdNames = [];
        let existingNames = [];
        try {
            const results = await requestBingDaily();
            const fulfilled = results.filter((result) => result.status === 'created' || result.status === 'existing');
            const messages = results.map(resultMessage);
            if (fulfilled.length === 0) {
                setManagerStatus(`每日一图获取失败：${messages.join('；')}。`, 'error');
                return false;
            }
            createdNames = fulfilled
                .filter((result) => result.status === 'created')
                .map((result) => result.background);
            existingNames = fulfilled
                .filter((result) => result.status === 'existing')
                .map((result) => result.background);
            const prefix = fulfilled.length === results.length ? '每日一图处理完成' : '每日一图部分完成';
            await scanBackgroundDirectory({
                successMessage: `${prefix}：${messages.join('；')}；当前背景未更改。`
            });
            return true;
        } catch (error) {
            console.warn('Bing 每日一图获取失败。', error);
            setManagerStatus(`每日一图获取失败：${error.message || '未知错误'}`, 'error');
            return false;
        } finally {
            bingDailyPending = false;
            syncManagerControls();
            syncAvailability(false, isManagerOnline());
            if (createdNames.length > 0 || existingNames.length > 0) {
                presentBackgroundResults({
                    createdNames,
                    locateNames: existingNames
                });
            }
        }
    }

    function syncAvailability(managerBusy = isManagerBusy(), managerOnline = isManagerOnline()) {
        const disabled = bingDailyPending || managerBusy || !managerOnline;
        bingDailyButton.disabled = disabled;
        bingDailyButton.setAttribute('aria-busy', String(bingDailyPending));
        bingDailyButton.setAttribute(
            'aria-label',
            managerOnline ? '获取 Bing 中国版与国际版每日一图' : '每日一图需要背景管理服务在线'
        );
    }

    bingDailyButton.addEventListener('click', fetchBingDaily);
    syncAvailability(false, isManagerOnline());

    return {
        isBusy: () => bingDailyPending,
        syncAvailability
    };
}
