import { fetchWithTimeout } from './core-http.js';
import { createBackgroundUploadController } from './background-upload.js';
import { createBingDailyController } from './bing-daily.js';
import { createBestdoriController } from './bestdori.js';

// Background inventory, preferences, selection, deletion, and shared coordination.
export function initializeBackgroundManager() {
    const root = document.documentElement;
    let backgroundUploadController = { isBusy: () => false, syncAvailability() {} };
    let bingDailyController = { isBusy: () => false, syncAvailability() {} };
    let bestdoriController = { isBusy: () => false, syncAvailability() {} };
            const pageBase = new URL('.', document.baseURI);
            const backgroundDirectory = new URL('backgrounds/', pageBase);
            const backgroundThumbnailDirectory = new URL('thumbnails/', backgroundDirectory);
            const defaultBackground = new URL('bg.jpg', backgroundDirectory);
            const defaultBackgroundName = 'bg.jpg';
            const backgroundPreferencesEndpoint = new URL('/api/background-preferences', document.baseURI);
            const backgroundInventoryEndpoint = new URL('/api/background-files', document.baseURI);
            const backgroundRemoveEndpoint = new URL('/api/background-files/remove', document.baseURI);
            const bestdoriResetEndpoint = new URL('/api/bestdori/history/reset', document.baseURI);
            const backgroundPreferencesCacheKey = 'homelab-background-state-cache-v1';
            const legacyBackgroundStorageKey = 'homelab-background';
            const maximumBackgroundResponseBytes = 512 * 1024;
            const maximumBackgroundInventoryItems = 512;
            const backgroundExtensionPattern = /\.(?:jpe?g|png|webp|avif)$/i;
            const backgroundModes = ['landscape', 'portrait'];
            const backgroundModeLabels = {
                landscape: '横屏',
                portrait: '竖屏'
            };
            const backgroundLibrarySources = ['ordinary', 'bing', 'bestdori'];
            const backgroundLibraryLabels = {
                ordinary: '普通壁纸',
                bing: 'Bing 每日一图',
                bestdori: 'Bestdori 抽卡'
            };
            const backgroundLibraryExpanded = {
                ordinary: true,
                bing: true,
                bestdori: true
            };
            const backgroundSectionTransitionFallbackMs = 400;
            const backgroundSectionTransitionCleanups = new Map();
            const orientationQuery = window.matchMedia('(orientation: portrait)');
            const backgroundLayer = document.querySelector('.background-layer');
            const backgroundPickerButton = document.getElementById('background-picker-button');
            const backgroundDialog = document.getElementById('background-dialog');
            const backgroundDialogCloseForm = backgroundDialog.querySelector('form[method="dialog"]');
            const backgroundGrid = document.getElementById('background-grid');
            const backgroundPickerStatus = document.getElementById('background-picker-status');
            const backgroundModeButtons = Array.from(backgroundDialog.querySelectorAll('[data-background-mode]'));
            const backgroundDeleteButton = document.getElementById('background-delete-button');
            const backgroundOriginalLink = document.getElementById('background-original-link');
            const backgroundDeleteDialog = document.getElementById('background-delete-dialog');
            const backgroundDeleteTitle = document.getElementById('background-delete-title');
            const backgroundDeleteDescription = document.getElementById('background-delete-description');
            const backgroundDeleteStatus = document.getElementById('background-delete-status');
            const backgroundDeleteCancel = document.getElementById('background-delete-cancel');
            const backgroundDeleteConfirm = document.getElementById('background-delete-confirm');
            let backgroundScanSequence = 0;
            let backgroundScanController = null;
            let backgroundScanPending = false;
            let backgroundDialogAnimationToken = 0;
            let backgroundDialogCloseTimer = 0;
            let backgroundDisplaySequence = 0;
            let backgroundPreferencesReadSequence = 0;
            let backgroundPreferencesWriteSequence = 0;
            let backgroundWritePending = false;
            let backgroundDeletePending = false;
            let backgroundDeleteRequest = null;
            let backgroundDeleteReturnFocus = null;
            let backgroundPreferencesConnection = 'loading';
            let editingBackgroundModeTouched = false;

            function normalizeBackgroundUrl(value) {
                if (!value) return null;

                try {
                    const url = new URL(value, pageBase);
                    url.search = '';
                    url.hash = '';

                    if (url.origin !== location.origin || !url.pathname.startsWith(backgroundDirectory.pathname)) return null;
                    const relativePath = decodeURIComponent(url.pathname.slice(backgroundDirectory.pathname.length));
                    if (!relativePath || relativePath.includes('/') || !backgroundExtensionPattern.test(relativePath)) return null;
                    return url;
                } catch (error) {
                    return null;
                }
            }

            function backgroundFileName(url) {
                try {
                    return decodeURIComponent(url.pathname.split('/').pop());
                } catch (error) {
                    return url.pathname.split('/').pop();
                }
            }

            function normalizeBackgroundName(value) {
                if (
                    typeof value !== 'string'
                    || value.length === 0
                    || value.length > 255
                    || value.includes('/')
                    || value.includes('\\')
                    || /[\u0000-\u001f\u007f]/.test(value)
                    || !backgroundExtensionPattern.test(value)
                ) return null;

                return value;
            }

            function normalizeBackgroundThumbnailUrl(value) {
                if (typeof value !== 'string' || value.length === 0 || value.length > 1024) return null;

                try {
                    const url = new URL(value, backgroundDirectory);
                    if (
                        url.origin !== location.origin
                        || url.username
                        || url.password
                        || url.hash
                        || !url.pathname.startsWith(backgroundThumbnailDirectory.pathname)
                    ) return null;

                    const relativePath = decodeURIComponent(
                        url.pathname.slice(backgroundThumbnailDirectory.pathname.length)
                    );
                    if (
                        !relativePath
                        || relativePath.includes('/')
                        || relativePath.includes('\\')
                        || !/^[a-z0-9][a-z0-9._-]*\.webp$/i.test(relativePath)
                    ) return null;

                    const versionValues = url.searchParams.getAll('v');
                    const queryKeys = Array.from(url.searchParams.keys());
                    if (
                        queryKeys.length !== 1
                        || queryKeys[0] !== 'v'
                        || versionValues.length !== 1
                        || !/^[a-z0-9][a-z0-9._-]{0,127}$/i.test(versionValues[0])
                    ) return null;

                    return url;
                } catch (error) {
                    return null;
                }
            }

            async function readBoundedResponseText(response, maximumBytes = maximumBackgroundResponseBytes) {
                const declaredValue = response.headers && response.headers.get
                    ? response.headers.get('Content-Length')
                    : null;
                let declaredLength = null;
                if (declaredValue !== null) {
                    if (!/^\d+$/.test(declaredValue)) {
                        throw new Error('响应包含无效的 Content-Length');
                    }
                    declaredLength = Number(declaredValue);
                    if (!Number.isSafeInteger(declaredLength) || declaredLength > maximumBytes) {
                        throw new Error('响应内容超过安全上限');
                    }
                }

                if (response.body && typeof response.body.getReader === 'function') {
                    const reader = response.body.getReader();
                    const decoder = new TextDecoder('utf-8', { fatal: true });
                    let received = 0;
                    let text = '';
                    try {
                        while (true) {
                            const { done, value } = await reader.read();
                            if (done) break;
                            if (!(value instanceof Uint8Array)) {
                                throw new Error('响应流包含无效数据');
                            }
                            received += value.byteLength;
                            if (received > maximumBytes) {
                                await reader.cancel();
                                throw new Error('响应内容超过安全上限');
                            }
                            text += decoder.decode(value, { stream: true });
                        }
                        text += decoder.decode();
                        if (declaredLength !== null && received !== declaredLength) {
                            throw new Error('响应长度与 Content-Length 不一致');
                        }
                    } catch (error) {
                        try {
                            await reader.cancel();
                        } catch (cancelError) {
                            // The response may already be closed.
                        }
                        throw error;
                    } finally {
                        if (reader.releaseLock) reader.releaseLock();
                    }
                    return text;
                }

                if (declaredLength === null || typeof response.text !== 'function') {
                    throw new Error('当前浏览器无法安全读取无长度响应');
                }
                return response.text();
            }

            function normalizeBackgroundInventory(value) {
                if (
                    !value
                    || typeof value !== 'object'
                    || value.version !== 1
                    || !Array.isArray(value.backgrounds)
                    || value.backgrounds.length === 0
                    || value.backgrounds.length > maximumBackgroundInventoryItems
                    || !value.thumbnails
                    || typeof value.thumbnails !== 'object'
                ) return null;

                const thumbnailStats = {};
                for (const key of ['generated', 'reused', 'failed', 'deferred']) {
                    const count = key === 'deferred' && value.thumbnails[key] === undefined
                        ? 0
                        : value.thumbnails[key];
                    if (!Number.isSafeInteger(count) || count < 0) return null;
                    thumbnailStats[key] = count;
                }
                const total = value.total === undefined ? value.backgrounds.length : value.total;
                const truncated = value.truncated === undefined ? false : value.truncated;
                if (
                    (total !== null && (!Number.isSafeInteger(total) || total < value.backgrounds.length))
                    || typeof truncated !== 'boolean'
                ) return null;

                const seenNames = new Set();
                const backgrounds = [];
                for (const item of value.backgrounds) {
                    if (!item || typeof item !== 'object') return null;
                    const name = normalizeBackgroundName(item.name);
                    const nameKey = name && name.toLocaleLowerCase('en-US');
                    if (!name || seenNames.has(nameKey)) return null;

                    const url = backgroundUrlFromName(name);
                    if (!url) return null;

                    let thumbnailUrl = null;
                    if (item.thumbnail !== null) {
                        thumbnailUrl = normalizeBackgroundThumbnailUrl(item.thumbnail);
                        if (!thumbnailUrl) return null;
                    }

                    seenNames.add(nameKey);
                    backgrounds.push({ url, thumbnailUrl });
                }
                if (
                    Object.values(thumbnailStats).reduce((sum, count) => sum + count, 0)
                    !== backgrounds.length
                ) return null;

                return {
                    backgrounds,
                    thumbnails: thumbnailStats,
                    total,
                    truncated
                };
            }

            function validateBackgroundUploadFile(file) {
                const maximumBytes = 50 * 1024 * 1024;
                const supportedExtension = /\.(?:jpe?g|png|webp|avif)$/i;

                if (!file || typeof file.name !== 'string' || typeof file.size !== 'number') {
                    return '请选择一张图片。';
                }
                if (!supportedExtension.test(file.name)) {
                    return '仅支持 JPEG、PNG、WebP 或 AVIF 图片。';
                }
                if (file.size <= 0) {
                    return '不能上传空文件。';
                }
                if (file.size > maximumBytes) {
                    return '图片不能超过 50 MiB。';
                }
                return '';
            }

            function backgroundModesUsingFile(fileName, preferences) {
                if (typeof fileName !== 'string' || !preferences || typeof preferences !== 'object') return [];
                return ['landscape', 'portrait'].filter((mode) => preferences[mode] === fileName);
            }

            function isBestdoriBackgroundName(value) {
                const fileName = normalizeBackgroundName(value);
                return Boolean(
                    fileName
                    && /^.+_(?:三星|四星|五星)_.+_[1-9]\d*_(?:after_training|normal)(?: \((?:[2-9]|[1-9]\d+)\))?\.png$/u.test(fileName)
                );
            }

            function isBingBackgroundName(value) {
                const fileName = normalizeBackgroundName(value);
                if (!fileName) return false;
                const match = /^bing_(cn|row|us)_\d{8}_OHR\.[A-Za-z0-9][A-Za-z0-9._-]{0,179}_(ZH-CN|ROW|EN-US)\d+(?: \((?:[2-9]|[1-9]\d+)\))?\.jpg$/i.exec(fileName);
                if (!match) return false;
                return { cn: 'zh-cn', row: 'row', us: 'en-us' }[match[1].toLowerCase()] === match[2].toLowerCase();
            }

            function backgroundSourceForName(value) {
                if (isBestdoriBackgroundName(value)) return 'bestdori';
                if (isBingBackgroundName(value)) return 'bing';
                return 'ordinary';
            }

            function backgroundUrlFromName(value) {
                const fileName = normalizeBackgroundName(value);
                if (!fileName) return null;
                return normalizeBackgroundUrl(new URL(encodeURIComponent(fileName), backgroundDirectory));
            }

            function normalizeBackgroundPreferences(value) {
                if (!value || typeof value !== 'object' || value.version !== 1) return null;
                const landscape = normalizeBackgroundName(value.landscape);
                const portrait = normalizeBackgroundName(value.portrait);
                if (!landscape || !portrait) return null;

                return {
                    version: 1,
                    landscape,
                    portrait,
                    updatedAt: typeof value.updatedAt === 'string' ? value.updatedAt : ''
                };
            }

            function readBackgroundPreferencesCache() {
                try {
                    return normalizeBackgroundPreferences(JSON.parse(localStorage.getItem(backgroundPreferencesCacheKey) || 'null'));
                } catch (error) {
                    return null;
                }
            }

            function saveBackgroundPreferencesCache(preferences) {
                try {
                    localStorage.setItem(backgroundPreferencesCacheKey, JSON.stringify(preferences));
                } catch (error) {
                    console.warn('无法缓存共享背景状态。', error);
                }
            }

            function clearLegacyBackgroundPreference() {
                try {
                    localStorage.removeItem(legacyBackgroundStorageKey);
                } catch (error) {
                    // Storage failure does not affect the current page.
                }
            }

            function currentOrientationMode() {
                return orientationQuery.matches ? 'portrait' : 'landscape';
            }

            const cachedBackgroundPreferences = readBackgroundPreferencesCache();
            let backgroundPreferences = cachedBackgroundPreferences || {
                version: 1,
                landscape: defaultBackgroundName,
                portrait: defaultBackgroundName,
                updatedAt: ''
            };
            let activeBackgroundMode = currentOrientationMode();
            let editingBackgroundMode = activeBackgroundMode;

            function backgroundUrlForMode(mode) {
                return backgroundUrlFromName(backgroundPreferences[mode]) || defaultBackground;
            }

            let currentBackground = normalizeBackgroundUrl(root.dataset.background) || backgroundUrlForMode(activeBackgroundMode);

            function setBackgroundStyle(url) {
                root.style.setProperty('--background-image', `url("${url.href}")`);
                root.dataset.background = url.href;
                currentBackground = url;
            }

            function setBackgroundStatus(message, kind = '') {
                backgroundPickerStatus.textContent = message;
                if (kind) {
                    backgroundPickerStatus.dataset.kind = kind;
                } else {
                    delete backgroundPickerStatus.dataset.kind;
                }
            }

            function focusWithoutScroll(element) {
                if (!element || typeof element.focus !== 'function') return;
                try {
                    element.focus({ preventScroll: true });
                } catch (error) {
                    element.focus();
                }
            }

            function preloadBackground(url, timeoutMs = 8000) {
                return new Promise((resolve, reject) => {
                    const image = new Image();
                    let settled = false;
                    let timeout = 0;

                    const finish = (callback, value) => {
                        if (settled) return;
                        settled = true;
                        window.clearTimeout(timeout);
                        image.onload = null;
                        image.onerror = null;
                        callback(value);
                    };

                    image.decoding = 'async';
                    image.onload = () => finish(resolve, url);
                    image.onerror = () => finish(reject, new Error(`图片无法加载：${url.pathname}`));
                    timeout = window.setTimeout(() => {
                        finish(reject, new Error(`图片加载超时：${url.pathname}`));
                        image.src = 'data:,';
                    }, timeoutMs);
                    image.src = url.href;
                });
            }

            function backgroundManagerIsBusy() {
                return backgroundScanPending
                    || backgroundWritePending
                    || backgroundUploadController.isBusy()
                    || backgroundDeletePending
                    || bingDailyController.isBusy()
                    || bestdoriController.isBusy();
            }

            function syncBackgroundOriginalLink() {
                const selectedName = normalizeBackgroundName(backgroundPreferences[editingBackgroundMode]);
                const selectedUrl = selectedName ? backgroundUrlFromName(selectedName) : null;
                if (!selectedUrl) {
                    backgroundOriginalLink.removeAttribute('href');
                    backgroundOriginalLink.setAttribute('aria-disabled', 'true');
                    backgroundOriginalLink.setAttribute('aria-label', '查看当前背景原图');
                    return;
                }

                backgroundOriginalLink.setAttribute('href', selectedUrl.href);
                backgroundOriginalLink.setAttribute('aria-disabled', 'false');
                backgroundOriginalLink.setAttribute(
                    'aria-label',
                    `在新标签页查看当前${backgroundModeLabels[editingBackgroundMode]}背景原图 ${selectedName}`
                );
            }

            function syncBackgroundControlsBusy() {
                const isBusy = backgroundManagerIsBusy();
                const managerOnline = backgroundPreferencesConnection === 'online';
                const selectedName = normalizeBackgroundName(backgroundPreferences[editingBackgroundMode]);
                const canDelete = managerOnline
                    && !isBusy
                    && Boolean(selectedName)
                    && selectedName.toLowerCase() !== defaultBackgroundName;

                backgroundGrid.setAttribute('aria-busy', String(isBusy));
                backgroundModeButtons.forEach((button) => {
                    button.disabled = isBusy;
                });
                backgroundGrid.querySelectorAll('.background-option').forEach((option) => {
                    option.disabled = isBusy;
                });
                backgroundUploadController.syncAvailability(isBusy, managerOnline);
                bingDailyController.syncAvailability(isBusy, managerOnline);
                bestdoriController.syncAvailability(isBusy, managerOnline);
                backgroundDeleteButton.disabled = !canDelete;

                syncBackgroundOriginalLink();
                backgroundDeleteButton.setAttribute(
                    'aria-label',
                    canDelete ? `删除当前${backgroundModeLabels[editingBackgroundMode]}背景 ${selectedName}` : '删除当前背景'
                );
            }

            function updateBackgroundModeControls() {
                backgroundModeButtons.forEach((button) => {
                    button.setAttribute('aria-pressed', String(button.dataset.backgroundMode === editingBackgroundMode));
                });
            }

            function updateBackgroundSelection() {
                const selectedUrl = backgroundUrlForMode(editingBackgroundMode);
                backgroundGrid.querySelectorAll('.background-option').forEach((option) => {
                    const isSelected = option.dataset.url === selectedUrl.href;
                    option.setAttribute('aria-pressed', String(isSelected));
                    option.setAttribute(
                        'aria-label',
                        `将 ${backgroundFileName(new URL(option.dataset.url))} 设置为${backgroundModeLabels[editingBackgroundMode]}背景${isSelected ? '，当前已选' : ''}`
                    );
                });
                syncBackgroundControlsBusy();
            }

            async function applyDisplayedBackground(url, options = {}) {
                const candidate = normalizeBackgroundUrl(url);
                if (!candidate) {
                    if (options.announce) setBackgroundStatus('该图片地址无效，已保留当前背景。', 'error');
                    return false;
                }

                const displayId = ++backgroundDisplaySequence;
                backgroundLayer.classList.remove('is-changing');

                try {
                    if (options.announce) setBackgroundStatus(`正在加载 ${backgroundFileName(candidate)}…`);
                    await preloadBackground(candidate);
                    if (displayId !== backgroundDisplaySequence) return false;

                    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                    const shouldAnimate = options.animate !== false && !reduceMotion && candidate.href !== currentBackground.href;
                    if (shouldAnimate) {
                        backgroundLayer.classList.add('is-changing');
                        await new Promise((resolve) => window.setTimeout(resolve, 150));
                        if (displayId !== backgroundDisplaySequence) return false;
                    }

                    setBackgroundStyle(candidate);
                    updateBackgroundSelection();

                    if (shouldAnimate) {
                        window.requestAnimationFrame(() => {
                            window.requestAnimationFrame(() => {
                                if (displayId === backgroundDisplaySequence) backgroundLayer.classList.remove('is-changing');
                            });
                        });
                    }
                    if (options.announce) {
                        setBackgroundStatus(options.successMessage || `已切换为 ${backgroundFileName(candidate)}`, 'success');
                    }
                    return true;
                } catch (error) {
                    if (displayId !== backgroundDisplaySequence) return false;
                    backgroundLayer.classList.remove('is-changing');
                    console.warn('背景切换失败。', error);
                    if (options.fallbackToDefault) {
                        setBackgroundStyle(defaultBackground);
                        updateBackgroundSelection();
                    }
                    if (options.announce) setBackgroundStatus('图片加载失败，已保留当前背景。', 'error');
                    return false;
                }
            }

            async function requestBackgroundPreferences(method = 'GET', body = null) {
                const headers = {
                    Accept: 'application/json',
                    'X-Requested-With': 'homelab-dashboard'
                };

                if (body) headers['Content-Type'] = 'application/json';

                const result = await fetchWithTimeout(backgroundPreferencesEndpoint.href, {
                    method,
                    cache: 'no-store',
                    credentials: 'same-origin',
                    headers,
                    body: body ? JSON.stringify(body) : null
                }, 5000, null, async (response) => {
                    if (!response.ok) throw new Error(`背景状态接口返回 ${response.status}`);
                    return {
                        response,
                        payload: await response.json()
                    };
                });

                const preferences = normalizeBackgroundPreferences(result.payload);
                if (!preferences) throw new Error('背景状态接口返回了无效数据');
                return preferences;
            }

            function acceptSharedBackgroundPreferences(preferences) {
                backgroundPreferences = preferences;
                backgroundPreferencesConnection = 'online';
                saveBackgroundPreferencesCache(preferences);
                clearLegacyBackgroundPreference();
                activeBackgroundMode = currentOrientationMode();
                updateBackgroundModeControls();
                updateBackgroundSelection();
            }

            async function loadSharedBackgroundPreferences() {
                const requestId = ++backgroundPreferencesReadSequence;
                try {
                    const preferences = await requestBackgroundPreferences();
                    if (requestId !== backgroundPreferencesReadSequence) return;
                    acceptSharedBackgroundPreferences(preferences);
                    await applyDisplayedBackground(backgroundUrlForMode(activeBackgroundMode), {
                        fallbackToDefault: true
                    });
                    if (requestId !== backgroundPreferencesReadSequence) return;
                    if (backgroundDialogIsOpen()) {
                        setBackgroundStatus(`已读取共享设置，正在配置${backgroundModeLabels[editingBackgroundMode]}背景。`, 'success');
                    }
                } catch (error) {
                    if (requestId !== backgroundPreferencesReadSequence) return;
                    backgroundPreferencesConnection = 'offline';
                    syncBackgroundControlsBusy();
                    console.warn('无法读取共享背景状态，继续使用本地缓存或默认背景。', error);
                    if (backgroundDialogIsOpen()) {
                        setBackgroundStatus('跨设备同步服务暂不可用，已继续使用最近缓存或默认背景。', 'error');
                    }
                }
            }

            async function reconcileSharedBackgroundPreference(mode, fileName, writeId, patchError) {
                const requestId = ++backgroundPreferencesReadSequence;
                try {
                    const preferences = await requestBackgroundPreferences();
                    if (writeId !== backgroundPreferencesWriteSequence || requestId !== backgroundPreferencesReadSequence) {
                        return false;
                    }

                    acceptSharedBackgroundPreferences(preferences);
                    await applyDisplayedBackground(backgroundUrlForMode(currentOrientationMode()), {
                        fallbackToDefault: true
                    });
                    if (writeId !== backgroundPreferencesWriteSequence || requestId !== backgroundPreferencesReadSequence) {
                        return false;
                    }

                    if (preferences[mode] === fileName) {
                        setBackgroundStatus(`连接中断后已核对：${backgroundModeLabels[mode]}背景已同步为 ${fileName}`, 'success');
                        return true;
                    }

                    setBackgroundStatus('当前读取仍为服务器原设置；原同步请求结果可能尚未确认，请稍后刷新核对。', 'error');
                    return false;
                } catch (reconcileError) {
                    if (writeId !== backgroundPreferencesWriteSequence || requestId !== backgroundPreferencesReadSequence) {
                        return false;
                    }
                    backgroundPreferencesConnection = 'offline';
                    syncBackgroundControlsBusy();
                    console.warn('共享背景设置失败，且无法向服务器核对最终状态。', patchError, reconcileError);
                    setBackgroundStatus('同步请求结果暂时无法确认；请恢复连接后刷新页面核对。', 'error');
                    return false;
                }
            }

            async function saveSharedBackgroundPreference(mode, url) {
                if (backgroundManagerIsBusy() || !backgroundModes.includes(mode)) return false;
                const candidate = normalizeBackgroundUrl(url);
                if (!candidate) {
                    setBackgroundStatus('该图片地址无效，未修改共享设置。', 'error');
                    return false;
                }

                const fileName = normalizeBackgroundName(backgroundFileName(candidate));
                if (!fileName) {
                    setBackgroundStatus('该图片文件名无效，未修改共享设置。', 'error');
                    return false;
                }

                backgroundWritePending = true;
                const writeId = ++backgroundPreferencesWriteSequence;
                syncBackgroundControlsBusy();
                setBackgroundStatus(`正在将 ${fileName} 同步为${backgroundModeLabels[mode]}背景…`);

                try {
                    try {
                        await preloadBackground(candidate);
                    } catch (imageError) {
                        console.warn('所选背景图片无法加载，未发送同步请求。', imageError);
                        setBackgroundStatus('所选图片无法加载，未修改共享设置。', 'error');
                        return false;
                    }

                    ++backgroundPreferencesReadSequence;
                    ++backgroundDisplaySequence;
                    backgroundLayer.classList.remove('is-changing');

                    let preferences;
                    try {
                        preferences = await requestBackgroundPreferences('PATCH', {
                            mode,
                            background: fileName
                        });
                    } catch (patchError) {
                        console.warn('共享背景写入请求失败，正在读取服务器状态进行核对。', patchError);
                        setBackgroundStatus('同步连接中断，正在核对服务器最终状态…');
                        return await reconcileSharedBackgroundPreference(mode, fileName, writeId, patchError);
                    }
                    if (writeId !== backgroundPreferencesWriteSequence) return false;
                    acceptSharedBackgroundPreferences(preferences);

                    activeBackgroundMode = currentOrientationMode();
                    const displayed = await applyDisplayedBackground(backgroundUrlForMode(activeBackgroundMode), {
                        fallbackToDefault: true
                    });
                    const storedName = preferences[mode];
                    if (!displayed && activeBackgroundMode === mode) {
                        setBackgroundStatus(`${backgroundModeLabels[mode]}设置已同步，但图片暂时无法加载。`, 'error');
                    } else if (storedName === fileName) {
                        setBackgroundStatus(`${backgroundModeLabels[mode]}背景已同步为 ${fileName}`, 'success');
                    } else {
                        setBackgroundStatus('服务器状态已更新，当前选择已按服务器结果刷新。', 'success');
                    }
                    return true;
                } finally {
                    backgroundWritePending = false;
                    if (backgroundDialogIsOpen() && !editingBackgroundModeTouched) {
                        setEditingBackgroundMode(currentOrientationMode(), false);
                    }
                    syncBackgroundControlsBusy();
                    updateBackgroundModeControls();
                    updateBackgroundSelection();
                    if (backgroundDialogIsOpen()) {
                        const focusTarget = Array.from(backgroundGrid.querySelectorAll('.background-option'))
                            .find((option) => option.dataset.url === candidate.href && !option.disabled);
                        focusWithoutScroll(focusTarget);
                    }
                }
            }

            function renderBackgroundOptions(backgrounds) {
                const fragment = document.createDocumentFragment();
                const groupedBackgrounds = {
                    ordinary: [],
                    bing: [],
                    bestdori: []
                };

                backgroundLibrarySources.forEach(cancelBackgroundSectionTransition);
                backgrounds.forEach((background) => {
                    groupedBackgrounds[
                        backgroundSourceForName(backgroundFileName(background.url))
                    ].push(background);
                });

                backgroundLibrarySources.forEach((source) => {
                    const section = document.createElement('section');
                    section.className = 'background-library-section';
                    section.dataset.backgroundSection = source;

                    const sectionContentId = `background-section-${source}`;
                    const toggle = document.createElement('button');
                    toggle.type = 'button';
                    toggle.className = 'background-library-toggle';
                    toggle.setAttribute('aria-controls', sectionContentId);
                    toggle.setAttribute('aria-expanded', String(backgroundLibraryExpanded[source]));

                    const title = document.createElement('span');
                    title.className = 'background-library-title';
                    title.textContent = backgroundLibraryLabels[source];

                    const count = document.createElement('span');
                    count.className = 'background-library-count';
                    count.textContent = `${groupedBackgrounds[source].length} 张`;

                    const chevron = document.createElement('span');
                    chevron.className = 'background-library-chevron';
                    chevron.setAttribute('aria-hidden', 'true');
                    chevron.textContent = '▼';
                    toggle.append(title, count, chevron);

                    const sectionContent = document.createElement('div');
                    sectionContent.id = sectionContentId;
                    sectionContent.className = 'background-section-collapse';
                    sectionContent.dataset.expanded = String(backgroundLibraryExpanded[source]);
                    sectionContent.setAttribute('aria-hidden', String(!backgroundLibraryExpanded[source]));
                    sectionContent.toggleAttribute('inert', !backgroundLibraryExpanded[source]);

                    const sectionClip = document.createElement('div');
                    sectionClip.className = 'background-section-clip';

                    const sectionGrid = document.createElement('div');
                    sectionGrid.className = 'background-section-grid';

                    groupedBackgrounds[source].forEach(({ url, thumbnailUrl }) => {
                        const option = document.createElement('button');
                        option.type = 'button';
                        option.className = 'background-option';
                        option.dataset.url = url.href;
                        option.dataset.backgroundSource = source;
                        option.setAttribute('aria-pressed', 'false');

                        const thumbnail = document.createElement('img');
                        const thumbnailSource = thumbnailUrl || url;
                        thumbnail.src = thumbnailSource.href;
                        thumbnail.alt = '';
                        thumbnail.loading = 'lazy';
                        thumbnail.decoding = 'async';
                        if (thumbnailSource.href !== url.href) {
                            thumbnail.addEventListener('error', () => {
                                thumbnail.src = url.href;
                            }, { once: true });
                        }

                        const label = document.createElement('span');
                        label.className = 'background-option-label';
                        label.textContent = backgroundFileName(url);

                        const check = document.createElement('span');
                        check.className = 'background-option-check';
                        check.setAttribute('aria-hidden', 'true');
                        check.textContent = '✓';

                        option.append(thumbnail, label);
                        if (source === 'bestdori') {
                            const sourceBadge = document.createElement('span');
                            sourceBadge.className = 'background-option-source';
                            sourceBadge.textContent = 'Bestdori';
                            option.append(sourceBadge);
                        }
                        option.append(check);
                        option.addEventListener('click', () => saveSharedBackgroundPreference(editingBackgroundMode, url));
                        sectionGrid.append(option);
                    });

                    if (groupedBackgrounds[source].length === 0) {
                        const empty = document.createElement('p');
                        empty.className = 'background-section-empty';
                        empty.textContent = source === 'bestdori'
                            ? '还没有通过 Bestdori 抽取卡面。'
                            : source === 'bing'
                                ? '还没有获取 Bing 每日一图。'
                                : '还没有普通壁纸。';
                        sectionGrid.append(empty);
                    }

                    sectionClip.append(sectionGrid);
                    sectionContent.append(sectionClip);
                    toggle.addEventListener('click', () => {
                        setBackgroundSectionExpanded(source, !backgroundLibraryExpanded[source]);
                    });
                    section.append(toggle, sectionContent);
                    fragment.append(section);
                });

                backgroundGrid.replaceChildren(fragment);
                updateBackgroundSelection();
                syncBackgroundControlsBusy();
                return {
                    total: backgrounds.length,
                    ordinary: groupedBackgrounds.ordinary.length,
                    bing: groupedBackgrounds.bing.length,
                    bestdori: groupedBackgrounds.bestdori.length
                };
            }

            function cancelBackgroundSectionTransition(source) {
                const cleanup = backgroundSectionTransitionCleanups.get(source);
                if (cleanup) cleanup();
            }

            function runAfterBackgroundSectionTransition(source, sectionContent, callback) {
                if (typeof callback !== 'function') return;
                if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                    callback();
                    return;
                }

                let active = true;
                let timeoutId = 0;
                const onTransitionEnd = (event) => {
                    if (event.target === sectionContent && event.propertyName === 'grid-template-rows') {
                        finish();
                    }
                };
                const cleanup = () => {
                    if (!active) return;
                    active = false;
                    sectionContent.removeEventListener('transitionend', onTransitionEnd);
                    if (timeoutId) window.clearTimeout(timeoutId);
                    if (backgroundSectionTransitionCleanups.get(source) === cleanup) {
                        backgroundSectionTransitionCleanups.delete(source);
                    }
                };
                const finish = () => {
                    if (!active) return;
                    cleanup();
                    callback();
                };

                sectionContent.addEventListener('transitionend', onTransitionEnd);
                timeoutId = window.setTimeout(finish, backgroundSectionTransitionFallbackMs);
                backgroundSectionTransitionCleanups.set(source, cleanup);
            }

            function setBackgroundSectionExpanded(source, expanded, options = {}) {
                if (!backgroundLibrarySources.includes(source)) return false;
                const section = Array.from(backgroundGrid.querySelectorAll('[data-background-section]'))
                    .find((item) => item.dataset.backgroundSection === source);
                if (!section) return false;

                const toggle = section.querySelector('.background-library-toggle');
                const sectionContent = section.querySelector('.background-section-collapse');
                if (!toggle || !sectionContent) return false;

                const nextExpanded = Boolean(expanded);
                const changed = backgroundLibraryExpanded[source] !== nextExpanded;
                cancelBackgroundSectionTransition(source);
                if (changed) void sectionContent.offsetHeight;
                backgroundLibraryExpanded[source] = nextExpanded;
                toggle.setAttribute('aria-expanded', String(nextExpanded));
                sectionContent.dataset.expanded = String(nextExpanded);
                sectionContent.setAttribute('aria-hidden', String(!nextExpanded));
                sectionContent.toggleAttribute('inert', !nextExpanded);

                if (typeof options.afterTransition === 'function') {
                    if (changed) {
                        runAfterBackgroundSectionTransition(source, sectionContent, options.afterTransition);
                    } else {
                        options.afterTransition();
                    }
                }
                return true;
            }

            function focusBackgroundOption(fileName) {
                const candidate = backgroundUrlFromName(fileName);
                if (!candidate) return false;
                const option = Array.from(backgroundGrid.querySelectorAll('.background-option'))
                    .find((item) => item.dataset.url === candidate.href && !item.disabled);
                if (!option) return false;

                const focusOption = () => {
                    if (!option.isConnected || option.closest('[inert]')) return;
                    if (typeof option.scrollIntoView === 'function') {
                        option.scrollIntoView({ block: 'nearest', inline: 'nearest' });
                    }
                    focusWithoutScroll(option);
                };
                return setBackgroundSectionExpanded(backgroundSourceForName(fileName), true, {
                    afterTransition: focusOption
                });
            }

            function backgroundDeleteDialogIsOpen() {
                return backgroundDeleteDialog.open || backgroundDeleteDialog.hasAttribute('open');
            }

            function syncBackgroundDeleteDialogControls() {
                backgroundDeleteCancel.disabled = backgroundDeletePending;
                backgroundDeleteConfirm.disabled = backgroundDeletePending;
                backgroundDeleteConfirm.setAttribute('aria-busy', String(backgroundDeletePending));
            }

            function deletionImpactText(fileName, modes) {
                const modeText = modes.length
                    ? `${modes.map((mode) => backgroundModeLabels[mode]).join('、')}设置会回退到 ${defaultBackgroundName}。`
                    : '当前共享设置不会改变。';
                return `目标文件：“${fileName}”。${modeText}`;
            }

            function showBackgroundDeleteStage(stage, confirmationToken = '', expiresIn = 0) {
                if (!backgroundDeleteRequest) return;
                backgroundDeleteRequest.stage = stage;
                backgroundDeleteRequest.confirmationToken = confirmationToken;
                backgroundDeleteTitle.textContent = stage === 'permanent' ? '永久删除背景图片？' : '删除背景图片？';
                backgroundDeleteDescription.textContent = stage === 'permanent'
                    ? `无法将图片移入系统回收站。永久删除后无法恢复。${deletionImpactText(backgroundDeleteRequest.background, backgroundDeleteRequest.modes)}`
                    : `图片会先移入系统回收站。${deletionImpactText(backgroundDeleteRequest.background, backgroundDeleteRequest.modes)}`;
                backgroundDeleteConfirm.textContent = stage === 'permanent' ? '永久删除' : '移入回收站';
                backgroundDeleteStatus.textContent = stage === 'permanent'
                    ? `请再次确认永久删除${expiresIn > 0 ? `；确认将在 ${expiresIn} 秒后失效` : ''}。`
                    : '';
            }

            function closeBackgroundDeleteDialog(returnValue = '', restoreFocus = true) {
                if (backgroundDeletePending) return;
                const returnFocus = backgroundDeleteReturnFocus;
                backgroundDeleteDialog.classList.remove('is-visible');
                if (typeof backgroundDeleteDialog.close === 'function' && backgroundDeleteDialog.open) {
                    backgroundDeleteDialog.close(returnValue);
                } else {
                    backgroundDeleteDialog.removeAttribute('open');
                }
                backgroundDeleteRequest = null;
                backgroundDeleteReturnFocus = null;
                backgroundDeleteStatus.textContent = '';
                if (restoreFocus) {
                    window.requestAnimationFrame(() => focusWithoutScroll(returnFocus));
                }
            }

            function openBackgroundDeleteDialog() {
                const fileName = normalizeBackgroundName(backgroundPreferences[editingBackgroundMode]);
                const isBusy = backgroundManagerIsBusy();
                if (
                    !fileName
                    || fileName.toLowerCase() === defaultBackgroundName
                    || backgroundPreferencesConnection !== 'online'
                    || isBusy
                ) return false;

                backgroundDeleteReturnFocus = document.activeElement || backgroundDeleteButton;
                backgroundDeleteRequest = {
                    background: fileName,
                    modes: backgroundModesUsingFile(fileName, backgroundPreferences),
                    stage: 'trash',
                    confirmationToken: ''
                };
                showBackgroundDeleteStage('trash');
                syncBackgroundDeleteDialogControls();

                if (typeof backgroundDeleteDialog.showModal === 'function') {
                    if (!backgroundDeleteDialog.open) backgroundDeleteDialog.showModal();
                } else {
                    backgroundDeleteDialog.setAttribute('open', '');
                }

                const revealAndFocus = () => {
                    if (!backgroundDeleteDialogIsOpen()) return;
                    backgroundDeleteDialog.classList.add('is-visible');
                    focusWithoutScroll(backgroundDeleteConfirm);
                };
                if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                    revealAndFocus();
                } else {
                    window.requestAnimationFrame(() => window.requestAnimationFrame(revealAndFocus));
                }
                return true;
            }

            async function requestBackgroundRemoval(request) {
                return fetchWithTimeout(backgroundRemoveEndpoint.href, {
                    method: 'POST',
                    cache: 'no-store',
                    credentials: 'same-origin',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'homelab-dashboard'
                    },
                    body: JSON.stringify(request)
                }, 10000, null, async (response) => {
                    let payload = null;
                    try {
                        payload = await response.json();
                    } catch (error) {
                        // A non-JSON intermediary error is reported using its HTTP status below.
                    }

                    const errorCode = payload && (
                        typeof payload.error === 'string'
                            ? payload.error
                            : payload.error && payload.error.code
                    );
                    if (response.status === 409 && errorCode === 'trash_unavailable') {
                        const confirmationToken = payload && payload.confirmationToken;
                        if (typeof confirmationToken !== 'string' || !confirmationToken) {
                            throw new Error('删除接口未返回有效的永久删除确认令牌');
                        }
                        return {
                            requiresPermanentConfirmation: true,
                            confirmationToken,
                            expiresIn: Number.isFinite(payload.expiresIn) ? payload.expiresIn : 0
                        };
                    }
                    if (!response.ok) {
                        const detail = payload && (
                            payload.message
                            || (typeof payload.error === 'string' ? payload.error : null)
                            || payload.code
                        );
                        throw new Error(typeof detail === 'string' ? detail : `删除接口返回 ${response.status}`);
                    }
                    return {
                        requiresPermanentConfirmation: false,
                        payload
                    };
                });
            }

            async function confirmBackgroundDeletion() {
                if (!backgroundDeleteRequest || backgroundDeletePending) return false;
                const request = backgroundDeleteRequest;
                const requestBody = { background: request.background };
                if (request.stage === 'permanent') {
                    if (!request.confirmationToken) {
                        backgroundDeleteStatus.textContent = '永久删除确认已失效，请取消后重试。';
                        return false;
                    }
                    requestBody.confirmationToken = request.confirmationToken;
                }

                backgroundDeletePending = true;
                syncBackgroundControlsBusy();
                syncBackgroundDeleteDialogControls();
                backgroundDeleteStatus.textContent = request.stage === 'permanent'
                    ? '正在永久删除图片…'
                    : '正在将图片移入回收站…';

                try {
                    const result = await requestBackgroundRemoval(requestBody);
                    if (result.requiresPermanentConfirmation) {
                        showBackgroundDeleteStage(
                            'permanent',
                            result.confirmationToken,
                            result.expiresIn
                        );
                        setBackgroundStatus('系统回收站不可用；如需继续，请在确认窗口中再次确认永久删除。', 'error');
                        return false;
                    }

                    const preferences = normalizeBackgroundPreferences(result.payload && result.payload.preferences);
                    if (!preferences) throw new Error('删除接口返回了无效背景设置');

                    ++backgroundPreferencesReadSequence;
                    acceptSharedBackgroundPreferences(preferences);
                    activeBackgroundMode = currentOrientationMode();
                    await applyDisplayedBackground(backgroundUrlForMode(activeBackgroundMode), {
                        fallbackToDefault: true
                    });

                    const deletedName = request.background;
                    const deletionLabel = request.stage === 'permanent' ? '已永久删除' : '已移入回收站';
                    backgroundDeletePending = false;
                    syncBackgroundControlsBusy();
                    syncBackgroundDeleteDialogControls();
                    closeBackgroundDeleteDialog('deleted', false);
                    await scanBackgroundDirectory({
                        successMessage: `${deletedName} ${deletionLabel}；相关设置已回退到 ${defaultBackgroundName}。`
                    });
                    focusBackgroundOption(backgroundPreferences[editingBackgroundMode]);
                    return true;
                } catch (error) {
                    console.warn('背景图片删除失败。', error);
                    const message = `删除失败：${error.message || '未知错误'}`;
                    backgroundDeleteStatus.textContent = message;
                    setBackgroundStatus(message, 'error');
                    return false;
                } finally {
                    if (backgroundDeletePending) {
                        backgroundDeletePending = false;
                        syncBackgroundControlsBusy();
                        syncBackgroundDeleteDialogControls();
                        focusWithoutScroll(backgroundDeleteConfirm);
                    }
                }
            }

            function mergeAndSortBackgrounds(backgrounds) {
                const merged = new Map();
                backgrounds.forEach((background) => {
                    merged.set(background.url.href, background);
                });
                const requiredBackgroundUrls = [
                    defaultBackground,
                    currentBackground,
                    ...backgroundModes.map((mode) => backgroundUrlForMode(mode))
                ];
                requiredBackgroundUrls.forEach((requiredUrl) => {
                    if (!merged.has(requiredUrl.href)) {
                        merged.set(requiredUrl.href, {
                            url: requiredUrl,
                            thumbnailUrl: null
                        });
                    }
                });

                const sorted = Array.from(merged.values()).sort((left, right) => {
                    if (left.url.href === defaultBackground.href) return -1;
                    if (right.url.href === defaultBackground.href) return 1;
                    return backgroundFileName(left.url).localeCompare(
                        backgroundFileName(right.url),
                        'zh-CN',
                        { numeric: true }
                    );
                });
                const requiredHrefs = new Set(requiredBackgroundUrls.map((url) => url.href));
                const requiredCount = sorted.filter((background) => requiredHrefs.has(background.url.href)).length;
                let remainingUnselectedSlots = Math.max(0, maximumBackgroundInventoryItems - requiredCount);
                return sorted.filter((background) => {
                    if (requiredHrefs.has(background.url.href)) return true;
                    if (remainingUnselectedSlots === 0) return false;
                    remainingUnselectedSlots -= 1;
                    return true;
                });
            }

            async function requestBackgroundInventory(controller) {
                return fetchWithTimeout(backgroundInventoryEndpoint.href, {
                    cache: 'no-store',
                    credentials: 'same-origin',
                    headers: {
                        Accept: 'application/json'
                    }
                }, 120000, controller, async (response) => {
                    if (!response.ok) throw new Error(`背景清单接口返回 ${response.status}`);
                    let payload;
                    try {
                        payload = JSON.parse(await readBoundedResponseText(response));
                    } catch (error) {
                        throw new Error('背景清单接口返回了非 JSON 响应');
                    }
                    const inventory = normalizeBackgroundInventory(payload);
                    if (!inventory) throw new Error('背景清单接口返回了无效数据');
                    return inventory;
                });
            }

            async function requestLegacyBackgroundListing(controller) {
                const result = await fetchWithTimeout(backgroundDirectory.href, {
                    cache: 'no-store',
                    credentials: 'same-origin'
                }, 5000, controller, async (response) => {
                    if (!response.ok) throw new Error(`目录请求返回 ${response.status}`);
                    return {
                        response,
                        listingText: await readBoundedResponseText(response)
                    };
                });

                const listing = new DOMParser().parseFromString(result.listingText, 'text/html');
                const discovered = new Map();
                listing.querySelectorAll('a[href]').forEach((anchor) => {
                    if (discovered.size >= maximumBackgroundInventoryItems) return;
                    try {
                        const url = normalizeBackgroundUrl(
                            new URL(anchor.getAttribute('href'), result.response.url)
                        );
                        if (url) {
                            discovered.set(url.href, {
                                url,
                                thumbnailUrl: null
                            });
                        }
                    } catch (error) {
                        // Ignore malformed links in the server-generated directory listing.
                    }
                });

                if (discovered.size === 0) throw new Error('目录列表中没有支持的图片链接');
                return Array.from(discovered.values());
            }

            function renderKnownBackgroundFallback() {
                const fallbackUrls = new Map([[defaultBackground.href, defaultBackground]]);
                if (currentBackground.href !== defaultBackground.href) {
                    fallbackUrls.set(currentBackground.href, currentBackground);
                }
                backgroundModes.forEach((mode) => {
                    const configuredUrl = backgroundUrlForMode(mode);
                    fallbackUrls.set(configuredUrl.href, configuredUrl);
                });
                return renderBackgroundOptions(
                    Array.from(fallbackUrls.values()).map((url) => ({
                        url,
                        thumbnailUrl: null
                    }))
                );
            }

            async function scanBackgroundDirectory(options = {}) {
                if (backgroundScanController) backgroundScanController.abort();
                const scanId = ++backgroundScanSequence;
                let controller = typeof AbortController === 'function' ? new AbortController() : null;
                backgroundScanController = controller;
                backgroundScanPending = true;
                syncBackgroundControlsBusy();
                setBackgroundStatus('正在核对背景清单与缩略图…');

                try {
                    let inventory;
                    try {
                        inventory = await requestBackgroundInventory(controller);
                    } catch (inventoryError) {
                        if (scanId !== backgroundScanSequence) return;
                        console.warn('背景清单接口不可用，正在使用原图目录列表降级。', inventoryError);

                        controller = typeof AbortController === 'function' ? new AbortController() : null;
                        backgroundScanController = controller;
                        try {
                            const legacyBackgrounds = await requestLegacyBackgroundListing(controller);
                            if (scanId !== backgroundScanSequence) return;
                            const backgrounds = mergeAndSortBackgrounds(legacyBackgrounds);
                            const counts = renderBackgroundOptions(backgrounds);
                            const inventorySummary = `共 ${counts.total} 张：普通壁纸 ${counts.ordinary} 张，Bing 每日一图 ${counts.bing} 张，Bestdori 抽卡 ${counts.bestdori} 张`;
                            const prefix = options.successMessage ? `${options.successMessage} ` : '';
                            setBackgroundStatus(
                                `${prefix}已进入原图加载降级模式；${inventorySummary}。`,
                                'error'
                            );
                            return;
                        } catch (listingError) {
                            if (scanId !== backgroundScanSequence) return;
                            console.warn('原图目录列表降级也失败。', listingError);
                            const counts = renderKnownBackgroundFallback();
                            setBackgroundStatus(
                                `已进入原图加载降级模式，但无法读取目录列表；仅保留 ${counts.total} 张已知背景（普通壁纸 ${counts.ordinary} 张，Bing 每日一图 ${counts.bing} 张，Bestdori 抽卡 ${counts.bestdori} 张）。请确认 Zoraxy 已开启 directory listing。`,
                                'error'
                            );
                            return;
                        }
                    }

                    if (scanId !== backgroundScanSequence) return;
                    const backgrounds = mergeAndSortBackgrounds(inventory.backgrounds);
                    const counts = renderBackgroundOptions(backgrounds);
                    const inventoryScope = inventory.truncated
                        ? (
                            inventory.total === null
                                ? `显示前 ${counts.total} 张`
                                : `显示 ${counts.total}/${inventory.total} 张`
                        )
                        : `共 ${counts.total} 张`;
                    const inventorySummary = `${inventoryScope}：普通壁纸 ${counts.ordinary} 张，Bing 每日一图 ${counts.bing} 张，Bestdori 抽卡 ${counts.bestdori} 张`;
                    const originalFallbackCount = backgrounds.filter(
                        (background) => !background.thumbnailUrl
                    ).length;
                    const thumbnailMessage = originalFallbackCount > 0
                        ? `，其中 ${originalFallbackCount} 张缩略图不可用，已单张使用原图`
                        : '';

                    if (options.successMessage) {
                        setBackgroundStatus(
                            `${options.successMessage} ${inventorySummary}${thumbnailMessage}。`,
                            originalFallbackCount > 0 ? 'error' : 'success'
                        );
                    } else if (backgroundPreferencesConnection === 'offline') {
                        setBackgroundStatus(
                            `${inventorySummary}${thumbnailMessage}，但跨设备同步服务暂不可用。`,
                            'error'
                        );
                    } else {
                        setBackgroundStatus(
                            `${inventorySummary}${thumbnailMessage}，正在设置${backgroundModeLabels[editingBackgroundMode]}背景。`,
                            originalFallbackCount > 0 ? 'error' : 'success'
                        );
                    }
                } finally {
                    if (scanId === backgroundScanSequence) {
                        backgroundScanController = null;
                        backgroundScanPending = false;
                        syncBackgroundControlsBusy();
                    }
                }
            }

            function setEditingBackgroundMode(mode, announce = true) {
                if (
                    !backgroundModes.includes(mode)
                    || backgroundManagerIsBusy()
                ) return false;
                editingBackgroundMode = mode;
                updateBackgroundModeControls();
                updateBackgroundSelection();
                if (announce) setBackgroundStatus(`请选择一张图片作为${backgroundModeLabels[mode]}背景。`);
                return true;
            }

            backgroundModeButtons.forEach((button) => {
                button.addEventListener('click', () => {
                    if (setEditingBackgroundMode(button.dataset.backgroundMode)) {
                        editingBackgroundModeTouched = true;
                    }
                });
            });

            backgroundDeleteButton.addEventListener('click', openBackgroundDeleteDialog);
            backgroundDeleteCancel.addEventListener('click', () => closeBackgroundDeleteDialog('cancel'));
            backgroundDeleteConfirm.addEventListener('click', confirmBackgroundDeletion);

            backgroundDeleteDialog.addEventListener('cancel', (event) => {
                event.preventDefault();
                if (!backgroundDeletePending) closeBackgroundDeleteDialog('cancel');
            });

            backgroundDeleteDialog.addEventListener('click', (event) => {
                if (event.target === backgroundDeleteDialog && !backgroundDeletePending) {
                    closeBackgroundDeleteDialog('backdrop');
                }
            });

            function backgroundDialogIsOpen() {
                return backgroundDialog.open || backgroundDialog.hasAttribute('open');
            }

            function finishBackgroundDialogClose(returnValue = '') {
                window.clearTimeout(backgroundDialogCloseTimer);
                backgroundDialogCloseTimer = 0;
                backgroundDialog.classList.remove('is-visible', 'is-closing');

                if (typeof backgroundDialog.close === 'function' && backgroundDialog.open) {
                    backgroundDialog.close(returnValue);
                } else {
                    backgroundDialog.removeAttribute('open');
                    focusWithoutScroll(backgroundPickerButton);
                }
            }

            function closeBackgroundDialog(returnValue = '') {
                if (!backgroundDialogIsOpen() || backgroundDialog.classList.contains('is-closing')) return;

                const animationToken = ++backgroundDialogAnimationToken;
                const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                if (reduceMotion) {
                    finishBackgroundDialogClose(returnValue);
                    return;
                }

                backgroundDialog.classList.add('is-closing');
                backgroundDialog.classList.remove('is-visible');

                const finishAfterTransition = (event) => {
                    if (event && (event.target !== backgroundDialog || event.propertyName !== 'opacity')) return;
                    if (animationToken !== backgroundDialogAnimationToken) return;
                    backgroundDialog.removeEventListener('transitionend', finishAfterTransition);
                    finishBackgroundDialogClose(returnValue);
                };

                backgroundDialog.addEventListener('transitionend', finishAfterTransition);
                backgroundDialogCloseTimer = window.setTimeout(finishAfterTransition, 300);
            }

            function openBackgroundDialog() {
                const animationToken = ++backgroundDialogAnimationToken;
                window.clearTimeout(backgroundDialogCloseTimer);
                backgroundDialogCloseTimer = 0;
                backgroundDialog.classList.remove('is-visible', 'is-closing');
                editingBackgroundModeTouched = false;
                setEditingBackgroundMode(currentOrientationMode(), false);

                if (typeof backgroundDialog.showModal === 'function') {
                    if (!backgroundDialog.open) backgroundDialog.showModal();
                } else {
                    backgroundDialog.setAttribute('open', '');
                }

                if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                    backgroundDialog.classList.add('is-visible');
                } else {
                    window.requestAnimationFrame(() => {
                        window.requestAnimationFrame(() => {
                            if (animationToken === backgroundDialogAnimationToken && backgroundDialogIsOpen()) {
                                backgroundDialog.classList.add('is-visible');
                            }
                        });
                    });
                }

                scanBackgroundDirectory();
            }

            backgroundPickerButton.addEventListener('click', openBackgroundDialog);

            backgroundDialogCloseForm.addEventListener('submit', (event) => {
                event.preventDefault();
                closeBackgroundDialog(event.submitter?.value || '');
            });

            backgroundDialog.addEventListener('cancel', (event) => {
                event.preventDefault();
                closeBackgroundDialog('cancel');
            });

            backgroundDialog.addEventListener('click', (event) => {
                if (event.target === backgroundDialog) closeBackgroundDialog('backdrop');
            });

            backgroundDialog.addEventListener('close', () => {
                ++backgroundDialogAnimationToken;
                window.clearTimeout(backgroundDialogCloseTimer);
                backgroundDialogCloseTimer = 0;
                backgroundDialog.classList.remove('is-visible', 'is-closing');
            });

            function handleOrientationChange() {
                activeBackgroundMode = currentOrientationMode();
                if (
                    backgroundDialogIsOpen()
                    && !editingBackgroundModeTouched
                    && !backgroundManagerIsBusy()
                ) {
                    setEditingBackgroundMode(activeBackgroundMode, false);
                }
                applyDisplayedBackground(backgroundUrlForMode(activeBackgroundMode), {
                    fallbackToDefault: true
                });
            }

            if (typeof orientationQuery.addEventListener === 'function') {
                orientationQuery.addEventListener('change', handleOrientationChange);
            } else if (typeof orientationQuery.addListener === 'function') {
                orientationQuery.addListener(handleOrientationChange);
            }


    const featureContext = {
        focusBackgroundOption,
        focusWithoutScroll,
        isManagerBusy: backgroundManagerIsBusy,
        isManagerOnline: () => backgroundPreferencesConnection === 'online',
        normalizeBackgroundName,
        scanBackgroundDirectory,
        setManagerStatus: setBackgroundStatus,
        syncManagerControls: syncBackgroundControlsBusy,
        validateBackgroundUploadFile
    };
    try {
        backgroundUploadController = createBackgroundUploadController(featureContext);
    } catch (error) {
        console.warn('批量壁纸上传初始化失败，背景选择与删除仍可使用。', error);
    }
    try {
        bingDailyController = createBingDailyController(featureContext);
    } catch (error) {
        console.warn('Bing 每日一图初始化失败，普通壁纸管理仍可使用。', error);
    }
    try {
        bestdoriController = createBestdoriController(featureContext);
    } catch (error) {
        console.warn('Bestdori 初始化失败，普通壁纸管理仍可使用。', error);
    }
            applyDisplayedBackground(backgroundUrlForMode(activeBackgroundMode), {
                animate: false,
                fallbackToDefault: true
            });
            syncBackgroundControlsBusy();
            loadSharedBackgroundPreferences();
}
