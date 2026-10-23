/** Система, в которой запущено окно: подписи и подсказки называют её клавиши и настройки, а не чужие. */
const root = document.documentElement;

export const isMac = (): boolean => root.classList.contains('platform-mac');
export const isWindows = (): boolean => root.classList.contains('platform-win');

/** Название ОС для подсказок; в браузере без окна Докки - нейтральное «системы». */
export const osName = (): string => (isMac() ? 'macOS' : isWindows() ? 'Windows' : 'системы');

/** Главный модификатор в подписях: ⌘ на Mac, Ctrl в остальных. */
export const mod = (): string => (isMac() ? '⌘' : 'Ctrl+');
