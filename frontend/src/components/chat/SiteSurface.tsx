import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { appColor, glassActive, useNativeTiles } from '../../lib/glass';
import { useFieldHost } from '../../state/field';
import { siteFieldHeight } from '../../state/siteField';
import { bindFrame, command, reloadSurface, sendLayout, setLayoutProvider, setSurfaceProduct, surface, useSurface } from '../../state/surface';

/** Цвет фона приложения для страницы сайта: у чата приложения свой фон, а не фон сайта.
 * Под стеклом страница прозрачная, а цвет нужен её затуханиям у краёв: это тот же тон, что даёт стекло под окном. */
function background(glass: boolean): string {
  const color = appColor(glass ? '--glass-base' : '--bg');
  if (!color) return '';
  return `#${color.map((part) => Math.round(part * 255).toString(16).padStart(2, '0')).join('')}`;
}

/** Сколько места сверху слева и справа занимают кнопки окна поверх области чата. */
function insets(main: HTMLElement): [number, number] {
  const box = main.getBoundingClientRect();
  let left = 0;
  let right = 0;
  for (const node of document.querySelectorAll<HTMLElement>('.shell-controls-left, .shell-controls-right, .desktop-window-controls')) {
    if (!node.checkVisibility()) continue;
    for (const child of node.children) {
      const rect = child.getBoundingClientRect();
      if (rect.width < 1 || rect.bottom <= box.top || rect.top >= box.top + 46 || (child as HTMLElement).hidden) continue;
      if (rect.right > box.left && rect.left < box.left + box.width / 2) left = Math.max(left, rect.right - box.left);
      if (rect.left < box.right && rect.right > box.left + box.width / 2) right = Math.max(right, box.right - rect.left);
    }
  }
  return [Math.ceil(left && left + 6), Math.ceil(right && right + 6)];
}

/** Затемнение окна вокруг фрейма, пока на странице сайта открыта новость: фрейм затемняет себя сам тем же тоном,
 * а здесь в затемнении дыра по рамке фрейма. Нажатие или Escape закрывают новость, как на сайте. */
function OverlayScrim({ host, closing }: { host: HTMLElement; closing: boolean }) {
  const [hole, setHole] = useState(() => host.getBoundingClientRect());
  useEffect(() => {
    const update = () => setHole(host.getBoundingClientRect());
    const resize = new ResizeObserver(update);
    resize.observe(host);
    window.addEventListener('resize', update);
    const keys = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); command('close-overlay'); } };
    document.addEventListener('keydown', keys, true);
    return () => {
      resize.disconnect();
      window.removeEventListener('resize', update);
      document.removeEventListener('keydown', keys, true);
    };
  }, [host]);
  const { left, top, right, bottom } = hole;
  const clip = `polygon(evenodd, 0 0, 100% 0, 100% 100%, 0 100%, 0 0, ${left}px ${top}px, ${right}px ${top}px, ${right}px ${bottom}px, ${left}px ${bottom}px, ${left}px ${top}px)`;
  return createPortal(
    <div className={`surface-scrim${closing ? ' is-closing' : ''}`} aria-hidden="true" style={{ clipPath: clip }} onClick={() => command('close-overlay')} />,
    document.body,
  );
}

/** Подложки поля ввода и «Чат / Поиск» на месте невидимых блоков страницы сайта: по их рамкам хост ставит нативное
 * стекло над лентой, а в стекло - вид поля и вид переключателя приложения (как поле режима кода). Высоту поля задаёт
 * приложение, страница подстраивает под неё отступы ленты. */
function SiteFieldSlots() {
  const site = useSurface((state) => state.siteField);
  const height = useFieldHost((state) => state.height);
  const expanded = useFieldHost((state) => state.expanded);
  useEffect(() => siteFieldHeight(height), [height]);
  const field = site?.composer && site.composer.o > 0.5 ? site.composer : null;
  const product = site?.product && site.product.o > 0.5 ? site.product : null;
  const fieldHeight = height || field?.h || 0;
  return (
    <>
      {field && (
        <div
          className="site-field-slot"
          data-glass="composer"
          aria-hidden="true"
          // низ поля держится за низ блока страницы: пока страница не подхватила новую высоту, поле растёт вверх
          style={{ left: field.x, top: field.y + field.h - fieldHeight, width: field.w, height: fieldHeight, borderRadius: expanded ? 23 : field.r }}
        />
      )}
      {product && <div className="site-field-slot" data-glass="product" aria-hidden="true" style={{ left: product.x, top: product.y, width: product.w, height: product.h, borderRadius: product.r }} />}
    </>
  );
}

/** Вид плитки «Чат / Поиск» (роль product): переключатель страницы сайта внутри нативного стекла. */
export function TileProduct() {
  const choice = useSurface((state) => state.siteField?.choice ?? state.product);
  const items = [['chat', 'Чат'], ['search', 'Поиск']] as const;
  return (
    <div className="ai-product-switch tile-product" data-product={choice} role="tablist" aria-label="Чат или поиск">
      <span className="ai-product-indicator" aria-hidden="true" />
      {items.map(([id, label]) => (
        <button key={id} type="button" role="tab" aria-selected={choice === id} tabIndex={choice === id ? 0 : -1} onClick={() => void setSurfaceProduct(id)}>
          {label}
        </button>
      ))}
    </div>
  );
}

/**
 * Режим чата: страница сайта Докки во фрейме. Фрейм живёт всё время входа (и в режиме кода, скрытым):
 * так работают ответы в фоне, список чатов в меню и чаты других компьютеров.
 */
export function SiteSurface({ shown }: { shown: boolean }) {
  const status = useSurface((state) => state.status);
  const url = useSurface((state) => state.url);
  const epoch = useSurface((state) => state.epoch);
  const overlay = useSurface((state) => state.overlay);
  const capable = useSurface((state) => state.features.includes('field'));
  const tiles = useNativeTiles();
  // поле и переключатель - нативные, когда есть плитки стекла и страница умеет отдавать своё поле
  const field = tiles && capable && shown && status === 'ready';
  const frame = useRef<HTMLIFrameElement>(null);
  const relayout = useRef<() => void>(() => undefined);
  const host = useRef<HTMLDivElement>(null);
  // после закрытия затемнение гаснет вместе со слоем новости на сайте (0.18 с), а не пропадает рывком
  const [scrim, setScrim] = useState(false);
  const dimmed = overlay && shown;
  useEffect(() => {
    if (dimmed) { setScrim(true); return undefined; }
    const timer = window.setTimeout(() => setScrim(false), 180);
    return () => window.clearTimeout(timer);
  }, [dimmed]);

  useEffect(() => {
    bindFrame(frame.current);
    return () => bindFrame(null);
  }, [epoch, url]);

  useEffect(() => {
    surface.set({ field });
    relayout.current();
  }, [field]);

  useEffect(() => {
    const main = document.getElementById('main');
    const shell = document.getElementById('shell');
    if (!main) return undefined;
    let frameId = 0;
    let last = '';
    const measure = (force = false) => {
      frameId = 0;
      const [left, right] = insets(main);
      const glass = glassActive();
      const color = background(glass);
      const { field: own } = surface.get();
      const key = `${left}:${right}:${color}:${glass}:${own}`;
      if (!force && key === last) return;
      last = key;
      sendLayout(left, right, color, glass, own);
    };
    const schedule = () => { if (!frameId) frameId = requestAnimationFrame(() => measure()); };
    relayout.current = schedule;
    setLayoutProvider(() => measure(true));
    const resize = new ResizeObserver(schedule);
    resize.observe(main);
    const classes = new MutationObserver(schedule);
    if (shell) classes.observe(shell, { attributes: true, attributeFilter: ['class'] });
    classes.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme', 'data-surface', 'data-caption-mode'] });
    // панели едут анимацией: после неё кнопки окна стоят на новом месте
    const settle = window.setInterval(schedule, 1000);
    window.addEventListener('resize', schedule);
    return () => {
      setLayoutProvider(() => undefined);
      relayout.current = () => undefined;
      resize.disconnect();
      classes.disconnect();
      window.clearInterval(settle);
      window.removeEventListener('resize', schedule);
      cancelAnimationFrame(frameId);
    };
  }, []);

  return (
    <div ref={host} className="site-surface" hidden={!shown} data-status={status}>
      {scrim && host.current && <OverlayScrim host={host.current} closing={!dimmed} />}
      {field && <SiteFieldSlots />}
      {url && (
        <iframe
          key={epoch}
          ref={frame}
          src={url}
          title="Докки"
          // страница сайта не может увести окно приложения: без allow-top-navigation
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads allow-modals"
          allow="microphone; clipboard-write; fullscreen"
          referrerPolicy="strict-origin"
        />
      )}
      {status === 'loading' && <div className="site-surface-state" role="status"><span className="spinner" aria-label="Загрузка" /></div>}
      {status === 'failed' && (
        <div className="site-surface-state" role="alert">
          <b>Не удалось открыть Докки</b>
          <p>Проверьте подключение к интернету и попробуйте ещё раз.</p>
          <button type="button" className="btn" onClick={reloadSurface}>Повторить</button>
        </div>
      )}
    </div>
  );
}
