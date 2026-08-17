// Batch wallpaper upload controller. Its queue state is private to this module.
export function createBackgroundUploadController(context) {
    const {
        focusBackgroundOption,
        focusWithoutScroll,
        isManagerBusy,
        isManagerOnline,
        normalizeBackgroundName,
        scanBackgroundDirectory,
        setManagerStatus,
        syncManagerControls,
        validateBackgroundUploadFile
    } = context;
    const backgroundUploadEndpoint = new URL('/api/background-files/upload', document.baseURI);
    const backgroundUploadButton = document.getElementById('background-upload-button');
    const backgroundUploadInput = document.getElementById('background-upload-input');
    const backgroundUploadDialog = document.getElementById('background-upload-dialog');
    const backgroundUploadSummary = document.getElementById('background-upload-summary');
    const backgroundUploadCount = document.getElementById('background-upload-count');
    const backgroundUploadProgress = document.getElementById('background-upload-progress');
    const backgroundUploadList = document.getElementById('background-upload-list');
    const backgroundUploadStatus = document.getElementById('background-upload-status');
    const backgroundUploadStop = document.getElementById('background-upload-stop');
    const backgroundUploadRetry = document.getElementById('background-upload-retry');
    const backgroundUploadDone = document.getElementById('background-upload-done');
    let backgroundUploadPending = false;
    let backgroundUploadSession = null;
    let backgroundUploadRequest = null;

            function formatBackgroundUploadBytes(value) {
                if (!Number.isFinite(value) || value < 0) return '未知大小';
                if (value < 1024) return `${value} B`;
                if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
                return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
            }

            function createBackgroundUploadError(message, options = {}) {
                const error = new Error(message);
                error.isGlobal = options.isGlobal === true;
                error.isRetryable = options.isRetryable === true;
                error.isCancelled = options.isCancelled === true;
                return error;
            }

            function createBackgroundUploadItem(file, index) {
                const validationError = validateBackgroundUploadFile(file);
                return {
                    id: index + 1,
                    file,
                    name: file && typeof file.name === 'string' ? file.name : `文件 ${index + 1}`,
                    size: file && typeof file.size === 'number' ? file.size : 0,
                    uploadedBytes: 0,
                    progress: 0,
                    status: validationError ? 'skipped' : 'waiting',
                    message: validationError || '等待上传',
                    uploadedName: '',
                    retryable: false
                };
            }

            function backgroundUploadItemIsTerminal(item) {
                return ['success', 'skipped', 'failed', 'cancelled'].includes(item.status);
            }

            function backgroundUploadStatusLabel(status) {
                const labels = {
                    waiting: '等待上传',
                    uploading: '正在上传',
                    verifying: '服务端验证中',
                    success: '上传成功',
                    skipped: '已跳过',
                    failed: '上传失败',
                    cancelled: '已取消'
                };
                return labels[status] || '等待上传';
            }

            function renderBackgroundUploadItems(items) {
                backgroundUploadList.replaceChildren();
                items.forEach((item) => {
                    const row = document.createElement('li');
                    row.className = 'background-upload-item';
                    row.dataset.uploadId = String(item.id);

                    const head = document.createElement('div');
                    head.className = 'background-upload-item-head';
                    const name = document.createElement('span');
                    name.className = 'background-upload-file-name';
                    name.textContent = item.name;
                    name.title = item.name;
                    const size = document.createElement('span');
                    size.className = 'background-upload-file-size';
                    size.textContent = formatBackgroundUploadBytes(item.size);
                    head.append(name, size);

                    const progress = document.createElement('progress');
                    progress.className = 'background-upload-progress background-upload-item-progress';
                    progress.max = 100;
                    progress.value = item.progress;
                    progress.setAttribute('aria-label', `${item.name} 上传进度`);

                    const status = document.createElement('p');
                    status.className = 'background-upload-item-status';
                    row.append(head, progress, status);
                    backgroundUploadList.append(row);
                    updateBackgroundUploadItem(item);
                });
            }

            function updateBackgroundUploadItem(item) {
                const row = backgroundUploadList.querySelector(`[data-upload-id="${item.id}"]`);
                if (!row) return;
                row.dataset.status = item.status;
                const progress = row.querySelector('.background-upload-item-progress');
                const status = row.querySelector('.background-upload-item-status');
                if (progress) progress.value = item.progress;
                if (status) {
                    const prefix = backgroundUploadStatusLabel(item.status);
                    status.textContent = item.message ? `${prefix} · ${item.message}` : prefix;
                }
            }

            function backgroundUploadCounts(items) {
                return items.reduce((counts, item) => {
                    counts[item.status] = (counts[item.status] || 0) + 1;
                    return counts;
                }, {});
            }

            function updateBackgroundUploadSummary(kind = '') {
                const session = backgroundUploadSession;
                if (!session) return;
                const counts = backgroundUploadCounts(session.items);
                const processed = session.items.filter(backgroundUploadItemIsTerminal).length;
                const validItems = session.items.filter((item) => item.status !== 'skipped');
                const totalBytes = validItems.reduce((total, item) => total + item.size, 0);
                const completedBytes = validItems.reduce((total, item) => {
                    if (backgroundUploadItemIsTerminal(item)) return total + item.size;
                    return total + Math.min(item.size, item.uploadedBytes);
                }, 0);
                let percent = totalBytes > 0 ? Math.round((completedBytes / totalBytes) * 100) : 100;
                if (session.running && percent >= 100) percent = 99;
                if (!session.running && processed === session.items.length) percent = 100;

                backgroundUploadProgress.value = Math.max(0, Math.min(100, percent));
                backgroundUploadCount.textContent = `${processed} / ${session.items.length}`;
                backgroundUploadList.setAttribute('aria-busy', String(session.running));
                if (session.refreshing) {
                    backgroundUploadSummary.textContent = '正在刷新背景清单…';
                } else if (session.running) {
                    backgroundUploadSummary.textContent = '正在上传壁纸…';
                } else {
                    backgroundUploadSummary.textContent = `已上传 ${counts.success || 0}，跳过 ${counts.skipped || 0}，失败 ${counts.failed || 0}，取消 ${counts.cancelled || 0}`;
                }

                const retryable = session.items.filter(
                    (item) => item.retryable && ['failed', 'cancelled'].includes(item.status)
                ).length;
                const hasPendingUpload = session.items.some(
                    (item) => ['waiting', 'uploading', 'verifying'].includes(item.status)
                );
                backgroundUploadStop.disabled = !session.running || session.refreshing || !hasPendingUpload;
                backgroundUploadRetry.hidden = session.running || retryable === 0;
                backgroundUploadRetry.textContent = retryable > 0 ? `重试失败项（${retryable}）` : '重试失败项';
                backgroundUploadDone.disabled = session.running;

                if (kind) {
                    backgroundUploadStatus.dataset.kind = kind;
                } else {
                    delete backgroundUploadStatus.dataset.kind;
                }
            }

            function setBackgroundUploadStatus(message, kind = '') {
                backgroundUploadStatus.textContent = message;
                updateBackgroundUploadSummary(kind);
            }

            function backgroundUploadDialogIsOpen() {
                return backgroundUploadDialog.open || backgroundUploadDialog.hasAttribute('open');
            }

            function openBackgroundUploadDialog() {
                if (typeof backgroundUploadDialog.showModal === 'function') {
                    if (!backgroundUploadDialog.open) backgroundUploadDialog.showModal();
                } else {
                    backgroundUploadDialog.setAttribute('open', '');
                }
                window.requestAnimationFrame(() => {
                    window.requestAnimationFrame(() => {
                        if (!backgroundUploadDialogIsOpen()) return;
                        backgroundUploadDialog.classList.add('is-visible');
                        focusWithoutScroll(backgroundUploadSession?.running ? backgroundUploadStop : backgroundUploadDone);
                    });
                });
            }

            function closeBackgroundUploadDialog(returnValue = '') {
                if (!backgroundUploadDialogIsOpen() || backgroundUploadSession?.running) return false;
                backgroundUploadDialog.classList.remove('is-visible');
                if (typeof backgroundUploadDialog.close === 'function' && backgroundUploadDialog.open) {
                    backgroundUploadDialog.close(returnValue);
                } else {
                    backgroundUploadDialog.removeAttribute('open');
                }
                backgroundUploadSession = null;
                focusWithoutScroll(backgroundUploadButton);
                return true;
            }

            function requestBackgroundUpload(file, onProgress) {
                return new Promise((resolve, reject) => {
                    const endpoint = new URL(backgroundUploadEndpoint.href);
                    endpoint.searchParams.set('name', file.name);
                    const request = new XMLHttpRequest();
                    let settled = false;

                    const finish = (callback, value) => {
                        if (settled) return;
                        settled = true;
                        if (backgroundUploadRequest === request) backgroundUploadRequest = null;
                        callback(value);
                    };
                    const rejectWith = (message, options = {}) => {
                        finish(reject, createBackgroundUploadError(message, options));
                    };

                    backgroundUploadRequest = request;
                    request.open('POST', endpoint.href, true);
                    request.timeout = 60000;
                    request.withCredentials = true;
                    request.setRequestHeader('Accept', 'application/json');
                    request.setRequestHeader('Content-Type', 'application/octet-stream');
                    request.setRequestHeader('X-Requested-With', 'homelab-dashboard');
                    request.upload.addEventListener('progress', (event) => {
                        if (event.lengthComputable) onProgress(event.loaded, event.total, false);
                    });
                    request.upload.addEventListener('load', () => {
                        onProgress(file.size, file.size, true);
                    });
                    request.addEventListener('load', () => {
                        let payload = null;
                        try {
                            payload = request.responseText ? JSON.parse(request.responseText) : null;
                        } catch (error) {
                            // Status and response text still determine how the queue proceeds.
                        }

                        if (request.status >= 200 && request.status < 300) {
                            const uploadedName = normalizeBackgroundName(payload && payload.background);
                            if (!uploadedName) {
                                rejectWith('上传接口返回了无效文件名', { isGlobal: true });
                                return;
                            }
                            finish(resolve, { payload, uploadedName });
                            return;
                        }

                        const responseError = payload && payload.error;
                        const errorCode = typeof responseError === 'string'
                            ? responseError
                            : responseError && typeof responseError.code === 'string'
                                ? responseError.code
                                : payload && typeof payload.code === 'string' ? payload.code : '';
                        const detail = payload && (
                            payload.message
                            || (typeof responseError === 'string' ? responseError : '')
                            || errorCode
                        );
                        const quotaFull = request.status === 409 && errorCode === 'background_file_quota_exceeded';
                        const globalStatus = [401, 403, 404, 405, 502, 503, 504].includes(request.status);
                        const retryable = request.status === 500 || [502, 503, 504].includes(request.status);
                        const message = quotaFull
                            ? '背景目录已达到 512 张配额，剩余文件未上传。'
                            : typeof detail === 'string' && detail
                                ? detail
                                : `上传接口返回 ${request.status || '未知状态'}`;
                        rejectWith(message, {
                            isGlobal: quotaFull || globalStatus,
                            isRetryable: !quotaFull && retryable
                        });
                    });
                    request.addEventListener('error', () => {
                        rejectWith('无法连接背景管理服务，剩余文件未上传。', {
                            isGlobal: true,
                            isRetryable: true
                        });
                    });
                    request.addEventListener('timeout', () => {
                        rejectWith('上传请求超时，剩余文件未上传。', {
                            isGlobal: true,
                            isRetryable: true
                        });
                    });
                    request.addEventListener('abort', () => {
                        rejectWith('用户已停止上传。', {
                            isGlobal: true,
                            isRetryable: true,
                            isCancelled: true
                        });
                    });
                    request.send(file);
                });
            }

            async function executeBackgroundUploadQueue(items, uploadOne, onSettled, shouldStop) {
                let processed = 0;
                for (const item of items) {
                    if (shouldStop()) break;
                    let result = null;
                    let error = null;
                    try {
                        result = await uploadOne(item);
                    } catch (uploadError) {
                        error = uploadError;
                    }
                    await onSettled(item, result, error);
                    processed += 1;
                    if (error?.isGlobal) break;
                }
                return {
                    processed,
                    remaining: items.slice(processed)
                };
            }

            async function runBackgroundUploadQueue(items) {
                const session = backgroundUploadSession;
                if (!session || session.running || items.length === 0) return false;

                session.running = true;
                session.refreshing = false;
                session.stopRequested = false;
                session.stopReason = '';
                session.stopRetryable = false;
                backgroundUploadPending = true;
                syncManagerControls();
                setBackgroundUploadStatus('队列已开始，上传期间请保持此页面打开。');
                let requestStarted = false;

                try {
                    await executeBackgroundUploadQueue(
                        items.filter((item) => item.status === 'waiting'),
                        async (item) => {
                            item.status = 'uploading';
                            item.message = '0%';
                            item.uploadedBytes = 0;
                            item.progress = 0;
                            item.retryable = false;
                            updateBackgroundUploadItem(item);
                            updateBackgroundUploadSummary();
                            requestStarted = true;
                            return requestBackgroundUpload(item.file, (loaded, total, verifying) => {
                                item.uploadedBytes = Math.max(0, Math.min(item.size, loaded));
                                item.progress = total > 0
                                    ? Math.max(0, Math.min(100, Math.round((loaded / total) * 100)))
                                    : 0;
                                item.status = verifying ? 'verifying' : 'uploading';
                                item.message = verifying ? '已传输，正在校验图片并生成缩略图' : `${item.progress}%`;
                                updateBackgroundUploadItem(item);
                                updateBackgroundUploadSummary();
                            });
                        },
                        async (item, result, error) => {
                            if (!error) {
                            item.status = 'success';
                            item.progress = 100;
                            item.uploadedBytes = item.size;
                            item.uploadedName = result.uploadedName;
                            item.message = result.uploadedName === item.name
                                ? result.uploadedName
                                : `${item.name} → ${result.uploadedName}`;
                            session.lastUploadedName = result.uploadedName;
                            } else {
                                console.warn(`背景图片上传失败：${item.name}`, error);
                                item.status = error.isCancelled ? 'cancelled' : 'failed';
                                item.message = error.message || '未知错误';
                                item.retryable = error.isRetryable === true;
                                if (error.isGlobal) {
                                    session.stopRequested = true;
                                    session.stopReason = item.message;
                                    session.stopRetryable = error.isRetryable === true;
                                }
                            }
                            updateBackgroundUploadItem(item);
                            updateBackgroundUploadSummary(session.stopRequested ? 'error' : '');
                        },
                        () => session.stopRequested
                    );

                    if (session.stopRequested) {
                        session.items.forEach((item) => {
                            if (item.status !== 'waiting') return;
                            item.status = 'cancelled';
                            item.message = session.stopReason || '队列已停止';
                            item.retryable = session.stopRetryable;
                            updateBackgroundUploadItem(item);
                        });
                    }

                    if (requestStarted) {
                        session.refreshing = true;
                        updateBackgroundUploadSummary(session.stopRequested ? 'error' : '');
                        const counts = backgroundUploadCounts(session.items);
                        await scanBackgroundDirectory({
                            successMessage: `批量上传完成：成功 ${counts.success || 0}，跳过 ${counts.skipped || 0}，失败 ${counts.failed || 0}，取消 ${counts.cancelled || 0}；未更改当前背景。`
                        });
                    }
                } finally {
                    session.refreshing = false;
                    session.running = false;
                    backgroundUploadRequest = null;
                    backgroundUploadPending = false;
                    syncManagerControls();
                    const counts = backgroundUploadCounts(session.items);
                    const kind = (counts.failed || counts.skipped || counts.cancelled) ? 'error' : 'success';
                    setBackgroundUploadStatus(
                        `已上传 ${counts.success || 0} 张，跳过 ${counts.skipped || 0} 张，失败 ${counts.failed || 0} 张，取消 ${counts.cancelled || 0} 张。`,
                        kind
                    );
                    if (session.lastUploadedName) focusBackgroundOption(session.lastUploadedName);
                    focusWithoutScroll(backgroundUploadRetry.hidden ? backgroundUploadDone : backgroundUploadRetry);
                }
                return true;
            }

            async function uploadBackgroundFiles(files) {
                if (!Array.isArray(files) || files.length === 0) return false;
                if (!isManagerOnline() || isManagerBusy()) {
                    setManagerStatus('背景管理服务正忙或暂不可用，请稍后再试。', 'error');
                    return false;
                }

                const items = files.map(createBackgroundUploadItem);
                backgroundUploadSession = {
                    items,
                    running: false,
                    refreshing: false,
                    stopRequested: false,
                    stopReason: '',
                    stopRetryable: false,
                    lastUploadedName: ''
                };
                renderBackgroundUploadItems(items);
                setBackgroundUploadStatus('正在准备上传队列…');
                openBackgroundUploadDialog();

                const queued = items.filter((item) => item.status === 'waiting');
                if (queued.length === 0) {
                    setBackgroundUploadStatus('所选文件均未通过上传预检，请查看逐项原因。', 'error');
                    return false;
                }
                return runBackgroundUploadQueue(queued);
            }

            function stopBackgroundUploadQueue() {
                const session = backgroundUploadSession;
                if (!session?.running || session.refreshing) return;
                if (!window.confirm('停止当前上传并取消剩余队列？已经上传成功的壁纸会保留。')) return;
                session.stopRequested = true;
                session.stopReason = '用户已停止上传。';
                session.stopRetryable = true;
                backgroundUploadStop.disabled = true;
                setBackgroundUploadStatus('正在停止当前上传…', 'error');
                if (backgroundUploadRequest) backgroundUploadRequest.abort();
            }

            function retryBackgroundUploadFailures() {
                const session = backgroundUploadSession;
                if (!session || session.running) return;
                const retryItems = session.items.filter(
                    (item) => item.retryable && ['failed', 'cancelled'].includes(item.status)
                );
                retryItems.forEach((item) => {
                    item.status = 'waiting';
                    item.message = '等待重试';
                    item.progress = 0;
                    item.uploadedBytes = 0;
                    item.uploadedName = '';
                    item.retryable = false;
                    updateBackgroundUploadItem(item);
                });
                if (retryItems.length > 0) runBackgroundUploadQueue(retryItems);
            }
            backgroundUploadButton.addEventListener('click', () => {
                if (!backgroundUploadButton.disabled) backgroundUploadInput.click();
            });

            backgroundUploadInput.addEventListener('change', () => {
                const files = Array.from(backgroundUploadInput.files || []);
                backgroundUploadInput.value = '';
                if (files.length > 0) uploadBackgroundFiles(files);
            });

            backgroundUploadStop.addEventListener('click', stopBackgroundUploadQueue);
            backgroundUploadRetry.addEventListener('click', retryBackgroundUploadFailures);
            backgroundUploadDone.addEventListener('click', () => closeBackgroundUploadDialog('done'));
            backgroundUploadDialog.addEventListener('cancel', (event) => {
                event.preventDefault();
                if (backgroundUploadSession?.running) {
                    setBackgroundUploadStatus('上传期间请使用“停止上传”安全结束队列。', 'error');
                } else {
                    closeBackgroundUploadDialog('cancel');
                }
            });
            backgroundUploadDialog.addEventListener('click', (event) => {
                if (event.target !== backgroundUploadDialog) return;
                if (backgroundUploadSession?.running) {
                    setBackgroundUploadStatus('上传期间不能关闭进度窗口。', 'error');
                } else {
                    closeBackgroundUploadDialog('backdrop');
                }
            });
            backgroundUploadDialog.addEventListener('close', () => {
                backgroundUploadDialog.classList.remove('is-visible');
            });


    return {
        isBusy: () => backgroundUploadPending,
        syncAvailability(isBusy, managerOnline) {
            backgroundUploadButton.disabled = isBusy || !managerOnline;
            backgroundUploadInput.disabled = isBusy || !managerOnline;
        }
    };
}
