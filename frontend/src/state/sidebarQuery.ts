import { createStore, useStore } from '../lib/store';

const store = createStore({ query: '' });
export const useSidebarQuery = () => useStore(store, (state) => state.query);
export const getSidebarQuery = () => store.get().query;
export const setSidebarQuery = (query: string) => store.set({ query });
