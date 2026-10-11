const BASE = '/api/notes';
let authorization = null;
const pending = new Set();
export function configureNotesAuthorization(controller) { authorization = controller; }
export function cancelNotesRequests() { for (const abort of [...pending]) abort(); }
function context(path) {
    const authRoute = path === '/auth' || path.startsWith('/auth/');
    if (!authRoute && authorization && !authorization.allowed) throw new NotesError('请先解锁笔记。', 401, { error: 'notes_auth_required' });
    return { authRoute, epoch: authorization?.epoch, token: authorization?.token };
}
function checkContext(captured) {
    if (!captured.authRoute && authorization && captured.epoch !== authorization.epoch) throw new NotesError('笔记请求已取消，请重新解锁。', 499);
}
function rejectAuthorization(payload) {
    if (payload.error === 'notes_auth_required') authorization?.invalidate();
}
const ERROR_MESSAGES = {
    notes_auth_required: '请重新输入密码解锁笔记，未同步草稿已保留。',
    notes_password_incorrect: '密码错误，请重试。',
    notes_auth_wait: '密码连续输错，请稍后重试。',
    notes_auth_busy: '笔记解锁会话已满，请重启后端后重试。',
    backup_target_changed: '备份目录目标已变化，请检查／恢复备份后应用重新绑定。',
    primary_target_changed: '主目录目标不是完整的同一笔记库，当前草稿已保留。',
    storage_target_missing: '存储入口或链接目标不存在，请先创建目录或链接。',
    storage_binding_invalid: '笔记目标绑定记录不可用，已停止写入。',
    storage_boundary: '笔记目录与公开目录或状态目录重叠。',
    storage_overlap: '两个笔记目录的真实目标重叠。',
    primary_not_local: '笔记主目录的真实目标必须位于本机磁盘。',
    unsupported_link_type: '存储入口使用了不支持的链接类型。',
    storage_link_loop: '存储入口存在链接循环或跳数过多。',
    check_stale: '目录或内容已变化，请重新检查备份。',
    primary_unavailable: '笔记主存储不可用，当前内容已保留。',
    backup_unavailable: '图片与备份目录不可用，文字仍可保存到主存储。',
    revision_conflict: '服务器内容已变化，请处理版本冲突。',
    order_conflict: '笔记列表已变化，请重新排序。',
    note_missing: '这篇笔记已不存在。', note_deleted: '这篇笔记已被删除。',
    note_limit: '标题最多 200 个字符，正文最多 256 KiB，请精简后保存。',
    notes_limit: '最多保存 500 篇笔记，请先删除不再需要的笔记。', text_limit: '笔记文字总容量已满，请精简后重试。',
    image_limit: '单张图片不能超过 20 MiB。', images_limit: '图片存储容量已满，请清理不再引用的图片。',
    invalid_image: '图片格式、尺寸或动画帧数不符合要求。', image_not_ready: '图片正在处理或删除，请检查引用后重试。',
    upload_conflict: '这次上传标识已被其他图片使用，请重新选择图片。', upload_removed: '图片已被清理，请重新选择图片。',
    upload_active: '这张图片仍在上传，请稍后重试。',
    worker_busy: '图片与备份服务繁忙，请稍后重试。', worker_timeout: '图片与备份目录响应超时，请检查连接。',
    invalid_settings: '请输入 10 至 3600 之间的整数秒数。',
    invalid_store: '笔记目录状态异常，请通过配置向导检查。', store_locked: '笔记目录正被另一个后端使用。'
};
export function notesErrorMessage(payload, status) {
    return ERROR_MESSAGES[payload.error] || (/[^\x00-\x7F]/.test(payload.message || '') ? payload.message : `笔记操作失败（${status || '连接异常'}），请重试。`);
}

export class NotesError extends Error {
    constructor(message, status = 0, payload = {}) {
        super(message);
        this.status = status;
        this.payload = payload;
    }
}

export async function requestNotes(path = '', method = 'GET', body = null, timeout = 15000) {
    const captured = context(path);
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (!captured.authRoute) pending.add(abort);
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
        const response = await fetch(`${BASE}${path}`, {
            method, signal: controller.signal, credentials: 'same-origin', cache: 'no-store',
            headers: { 'X-Requested-With': 'homelab-dashboard', ...(captured.token ? { 'X-Notes-Token': captured.token } : {}), ...(body === null ? {} : { 'Content-Type': 'application/json' }) },
            ...(body === null ? {} : { body: JSON.stringify(body) })
        });
        let payload;
        try { payload = await response.json(); } catch { throw new NotesError('笔记服务返回了无法识别的响应。', response.status); }
        checkContext(captured);
        if (!response.ok) { rejectAuthorization(payload); throw new NotesError(notesErrorMessage(payload, response.status), response.status, payload); }
        return payload;
    } catch (error) {
        if (error instanceof NotesError) throw error;
        checkContext(captured);
        throw new NotesError(error.name === 'AbortError' ? '笔记请求超时，请检查连接后重试。' : '无法连接笔记服务，内容已保留。');
    } finally { clearTimeout(timer); pending.delete(abort); }
}

export async function fetchNoteImage(path, signal) {
    if (!/^\/api\/notes\/images\/[0-9a-f-]{36}$/.test(path)) throw new NotesError('无效的笔记图片。');
    const captured = context(path.slice(BASE.length));
    const controller = new AbortController();
    const abort = () => controller.abort();
    pending.add(abort);
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) controller.abort();
    const timer = setTimeout(abort, 30000);
    try {
        const response = await fetch(path, { signal: controller.signal, credentials: 'same-origin', cache: 'no-store', headers: { 'X-Requested-With': 'homelab-dashboard', ...(captured.token ? { 'X-Notes-Token': captured.token } : {}) } });
        checkContext(captured);
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            checkContext(captured); rejectAuthorization(payload);
            throw new NotesError(notesErrorMessage(payload, response.status), response.status, payload);
        }
        const mime = response.headers.get('content-type')?.split(';')[0];
        if (!['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif'].includes(mime)) throw new NotesError('图片响应类型无效。');
        const blob = await response.blob(); checkContext(captured);
        if (blob.size > 20 * 1024 * 1024) throw new NotesError('图片响应过大。');
        return blob;
    } finally { clearTimeout(timer); pending.delete(abort); signal?.removeEventListener('abort', abort); }
}

export function uploadNoteImage(task, onProgress) {
    const captured = context('/uploads');
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        task.abort = () => xhr.abort();
        pending.add(task.abort);
        xhr.addEventListener('loadend', () => pending.delete(task.abort), { once: true });
        xhr.open('POST', `${BASE}/uploads?id=${encodeURIComponent(task.id)}&name=${encodeURIComponent(task.file.name || 'image')}`);
        xhr.setRequestHeader('X-Requested-With', 'homelab-dashboard');
        if (captured.token) xhr.setRequestHeader('X-Notes-Token', captured.token);
        xhr.setRequestHeader('Content-Type', 'application/octet-stream');
        xhr.timeout = 300000;
        xhr.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100)); };
        xhr.onload = () => {
            try { checkContext(captured); } catch (error) { reject(error); return; }
            let payload;
            try { payload = JSON.parse(xhr.responseText); } catch { reject(new NotesError('图片上传响应无效。', xhr.status)); return; }
            if (xhr.status >= 200 && xhr.status < 300) resolve(payload);
            else { rejectAuthorization(payload); reject(new NotesError(notesErrorMessage(payload, xhr.status), xhr.status, payload)); }
        };
        xhr.onerror = () => reject(new NotesError('图片上传连接中断。'));
        xhr.ontimeout = () => reject(new NotesError('图片上传超时。'));
        xhr.onabort = () => reject(new NotesError('图片上传已取消。', 499));
        xhr.send(task.file);
    });
}

export function newOperationId() {
    if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function moveInsertionOffset(previous, next, offset) {
    let start = 0;
    while (start < previous.length && start < next.length && previous[start] === next[start]) start++;
    let end = previous.length;
    let nextEnd = next.length;
    while (end > start && nextEnd > start && previous[end - 1] === next[nextEnd - 1]) { end--; nextEnd--; }
    if (offset < start) return offset;
    if (offset >= end) return Math.max(0, offset + nextEnd - end);
    return next.length;
}

export function formatSaveCountdown(milliseconds) {
    const seconds = Math.max(0, Math.ceil(milliseconds / 1000));
    return `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
}
