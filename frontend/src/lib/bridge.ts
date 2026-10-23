/** Мост окна Qt. Его создаёт desktop-window.js, внедрённый хостом; в браузере моста нет. */
interface WindowBridge {
  adjustWidth(side: string, delta: number, ms: number, callback: (result: string) => void): void;
  command(action: string): void;
  /** Liquid Glass: включение и тема (JSON). */
  setGlass?(payload: string): void;
  /** Рамки блоков вида плитки (карточки правой панели): под каждым хост кладёт своё нативное стекло (JSON). */
  regions?(payload: string): void;
  /** Тема и цвет фона страницы: хост запоминает их для заставки следующего запуска (JSON). */
  appearance?(payload: string): void;
  /** Связь видов приложения через хост (lib/mirror.ts). */
  relay?(payload: string): void;
  /** Поверх страницы открыто модальное окно: плитки стекла уходят под страницу, чтобы окно и его фон лежали над ними (JSON). */
  overlay?(payload: string): void;
  /** Состояние панелей для нативных плиток: открыты ли, ширины, время и кривая движения (JSON). */
  panels?(payload: string): void;
  /** Клавиатуру нужно вернуть в поле ввода: у него свой веб-вид над лентой. */
  focusComposer?(payload: string): void;
}

declare global {
  interface Window {
    koddiWindowBridge?: WindowBridge;
    koddiDesktop?: boolean;
  }
}

export const hasWindowBridge = () => Boolean(window.koddiWindowBridge);

let announced = '';
let announcements = 0;
/** Окно приложения называется по режиму («Докки» или «Докки Code»); сообщаем хосту только при смене режима. */
export function announceProduct(scope: string): void {
  if (!window.koddiDesktop || announced === scope) return;
  announced = scope;
  // одинаковый адрес подряд Chromium может не передать хосту: добавляем счётчик
  window.location.href = `koddi://product?mode=${scope}&n=${++announcements}`;
}

/** Просит окно вырасти у нужного края (при нехватке места - у противоположного) плавно, с той же кривой, что и панель. */
export function growWindow(delta: number, ms: number, side: 'left' | 'right' = 'right'): boolean {
  const bridge = window.koddiWindowBridge;
  if (!bridge?.adjustWidth) return false;
  try {
    bridge.adjustWidth(side, Math.round(delta), Math.round(ms), () => undefined);
    return true;
  } catch {
    return false; /* окно без моста */
  }
}

export interface PanelsAnnouncement {
  left: boolean;
  right: boolean;
  lw: number;
  rw: number;
  animate: boolean;
  ms: number;
  c: number[];
}

/** Сообщает хосту, какие панели открыты: нативные плитки стекла двигает AppKit в то же время и с той же кривой. */
export function announcePanels(state: PanelsAnnouncement): void {
  try {
    window.koddiWindowBridge?.panels?.(JSON.stringify(state));
  } catch {
    /* окно без моста */
  }
}

/** Сообщает хосту, открыто ли модальное окно: нативные плитки лежат над страницей и иначе закрывали бы его. */
export function announceOverlay(active: boolean): void {
  try {
    window.koddiWindowBridge?.overlay?.(JSON.stringify({ active }));
  } catch {
    /* окно без моста */
  }
}

/** Сообщает хосту тему и цвет фона: при следующем запуске заставка и окно сразу выглядят так же, как страница. */
export function announceAppearance(theme: string, color: number[] | null): void {
  if (!color) return;
  try {
    window.koddiWindowBridge?.appearance?.(JSON.stringify({ theme: theme === 'light' ? 'light' : 'dark', color }));
  } catch {
    /* окно без моста */
  }
}

/** Хост передаёт клавиатуру виду поля ввода (нативное стекло над лентой). */
export function announceComposerFocus(): void {
  try {
    window.koddiWindowBridge?.focusComposer?.('1');
  } catch {
    /* окно без моста */
  }
}
