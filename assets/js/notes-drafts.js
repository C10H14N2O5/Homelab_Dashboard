// Drafts are emergency browser recovery records, never server-save acknowledgements.
export function createNotesDraftStore(tabId) {
    let database;
    let openPromise;
    function open() {
        if (openPromise) return openPromise;
        openPromise = new Promise((resolve, reject) => {
            if (!globalThis.indexedDB) { reject(new Error('浏览器不支持本机草稿。')); return; }
            const request = indexedDB.open('homelab-notes-recovery-v1', 1);
            request.onupgradeneeded = () => request.result.createObjectStore('drafts', { keyPath: 'key' });
            request.onsuccess = () => { database = request.result; resolve(database); };
            request.onerror = () => reject(request.error);
            request.onblocked = () => reject(new Error('本机草稿存储被其他页面阻塞。'));
        });
        return openPromise;
    }
    async function transaction(mode, operation) {
        const db = await open();
        return new Promise((resolve, reject) => {
            const tx = db.transaction('drafts', mode);
            let result;
            const request = operation(tx.objectStore('drafts'));
            if (request) request.onsuccess = () => { result = request.result; };
            tx.oncomplete = () => resolve(result);
            tx.onerror = () => reject(tx.error);
            tx.onabort = () => reject(tx.error || new Error('本机草稿暂存失败。'));
        });
    }
    return {
        async write(scope, note) {
            return transaction('readwrite', (store) => store.put({ ...note, key: `${scope}:${tabId}:${note.id}`, scope, tabId, savedAt: Date.now() }));
        },
        async remove(scope, id) { return transaction('readwrite', (store) => store.delete(`${scope}:${tabId}:${id}`)); },
        async removeKey(key) { return transaction('readwrite', (store) => store.delete(key)); },
        async list(scope) { return (await transaction('readonly', (store) => store.getAll())).filter((draft) => draft.scope === scope); }
    };
}
