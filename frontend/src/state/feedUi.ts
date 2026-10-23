import { createStore, useStore } from '../lib/store';

/** Состояние ленты, нужное соседним компонентам: кнопка «К последнему сообщению» и выбранные варианты ответа. */
export const feedUi = createStore<{ jump: boolean }>({ jump: false });
export const useFeedUi = <S,>(selector: (state: { jump: boolean }) => S): S => useStore(feedUi, selector);
