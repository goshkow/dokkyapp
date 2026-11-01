import { createStore, useStore } from '../lib/store';

/** Есть ли у компьютера сеть. Сервер Докки при этом может быть недоступен сам по себе. */
export const connection = createStore({ online: navigator.onLine !== false });
export const useConnection = (): boolean => useStore(connection, (state) => state.online);

window.addEventListener('online', () => connection.set({ online: true }));
window.addEventListener('offline', () => connection.set({ online: false }));
