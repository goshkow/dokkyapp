/* Локальный кэш облачных чатов (IndexedDB): список и последние сообщения показываются сразу, а свежие данные подтягиваются следом.
   Ключи привязаны к аккаунту; при выходе или отзыве сессии кэш очищается. Любая ошибка хранилища молча игнорируется: кэш лишь ускорение. */

const NAME = 'koddi-cache';
const STORE = 'kv';
const LIMIT = 80;

let opened: Promise<IDBDatabase | null> | null = null;
let owner = '';

function open(): Promise<IDBDatabase | null> {
  opened ??= new Promise((resolve) => {
    try {
      const request = indexedDB.open(NAME, 1);
      request.onupgradeneeded = () => request.result.createObjectStore(STORE);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return opened;
}

/** Кому принадлежит кэш: у другого аккаунта чужие данные не показываются. */
export const setCacheOwner = (id: string): void => { owner = id; };
const keyOf = (name: string): string => `${owner}:${name}`;

export async function cacheGet<T>(name: string): Promise<T | undefined> {
  if (!owner) return undefined;
  const db = await open();
  if (!db) return undefined;
  return new Promise((resolve) => {
    try {
      const request = db.transaction(STORE, 'readonly').objectStore(STORE).get(keyOf(name));
      request.onsuccess = () => resolve((request.result as { value: T } | undefined)?.value);
      request.onerror = () => resolve(undefined);
    } catch {
      resolve(undefined);
    }
  });
}

export function cacheSet(name: string, value: unknown): void {
  if (!owner) return;
  void open().then((db) => {
    if (!db) return;
    try {
      const store = db.transaction(STORE, 'readwrite').objectStore(STORE);
      store.put({ value, at: Date.now() }, keyOf(name));
      prune(db);
    } catch {
      /* хранилище недоступно */
    }
  });
}

let pruning = false;
/** Хранит не больше LIMIT самых свежих записей. */
function prune(db: IDBDatabase): void {
  if (pruning) return;
  pruning = true;
  window.setTimeout(() => {
    try {
      const store = db.transaction(STORE, 'readwrite').objectStore(STORE);
      const rows: Array<[IDBValidKey, number]> = [];
      const cursor = store.openCursor();
      cursor.onsuccess = () => {
        const item = cursor.result;
        if (item) {
          rows.push([item.key, (item.value as { at?: number }).at ?? 0]);
          item.continue();
          return;
        }
        rows.sort((a, b) => b[1] - a[1]);
        for (const [key] of rows.slice(LIMIT)) store.delete(key);
        pruning = false;
      };
      cursor.onerror = () => { pruning = false; };
    } catch {
      pruning = false;
    }
  }, 2000);
}

export function cacheClear(): void {
  void open().then((db) => {
    try {
      db?.transaction(STORE, 'readwrite').objectStore(STORE).clear();
    } catch {
      /* ничего не делаем */
    }
  });
}
