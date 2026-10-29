import { Network } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from 'react';
import { buildTree, layoutTree, tileAt, type Tile, type TreeFile } from '../../lib/treemap';
import { guarded } from '../../state/overlay';
import { loadMap, setMapActive, useProjectMap } from '../../state/projectMap';
import { useQuality, type HeatItem } from '../../state/quality';
import { useView } from '../../state/view';
import { openFile, openFolder } from '../../state/workspace';

const HUES = 8;

interface Palette { hues: string[]; neutral: string; text: string; muted: string; out: string; into: string; heat: string; font: string }

function palette(node: HTMLElement): Palette {
  const style = getComputedStyle(node);
  const read = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  return {
    hues: Array.from({ length: HUES }, (_, index) => read(`--map-hue-${index}`, '#5b9dff')),
    neutral: read('--map-neutral', '#8a8a8a'), text: read('--text', '#f5f5f5'), muted: read('--muted', '#a3a3a3'),
    out: read('--map-out', '#f5f5f5'), into: read('--map-in', '#b48cf2'), heat: read('--danger', '#ee7b83'), font: style.fontFamily || 'sans-serif',
  };
}

const plural = (n: number, one: string, few: string, many: string): string => {
  const tail = n % 100;
  const last = n % 10;
  return `${n.toLocaleString('ru-RU')} ${tail >= 11 && tail <= 14 ? many : last === 1 ? one : last >= 2 && last <= 4 ? few : many}`;
};

function roundRect(context: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  context.beginPath();
  const radius = Math.max(0, Math.min(r, w / 2, h / 2));
  if (typeof context.roundRect === 'function') context.roundRect(x, y, w, h, radius);
  else context.rect(x, y, w, h);
}

interface Hover { tile: Tile; x: number; y: number }

// файл с именем вроде «constructor» не должен находить свойство прототипа
const heatOf = (heat: Record<string, HeatItem> | null, path: string): HeatItem | undefined =>
  heat && Object.prototype.hasOwnProperty.call(heat, path) ? heat[path] : undefined;

/** Долг качества файла одной строкой для подсказки: из чего сложилась подсветка. */
function heatText(item: HeatItem): string {
  return [
    item.max_crap !== null ? `CRAP ${item.max_crap}` : item.max_complexity ? `сложность ${item.max_complexity}` : '',
    item.dry ? `дублей ${item.dry}` : '', item.survived ? `выжило мутантов ${item.survived}` : '', item.violations ? `нарушений слоёв ${item.violations}` : '',
  ].filter(Boolean).join(' · ');
}

function Tooltip({ hover, files, importers, heat, width, height }: {
  hover: Hover; files: TreeFile[]; importers: Map<number, number[]>; heat: Record<string, HeatItem> | null; width: number; height: number;
}) {
  const { tile } = hover;
  const left = hover.x + 250 > width ? Math.max(6, hover.x - 246) : hover.x + 14;
  const top = hover.y + 140 > height ? Math.max(6, hover.y - 130) : hover.y + 16;
  let body;
  if (tile.kind === 'file' && tile.file !== undefined) {
    const file = files[tile.file] as TreeFile;
    const cut = file.p.lastIndexOf('/');
    const facts = [file.lang, file.l ? plural(file.l, 'строка', 'строки', 'строк') : '', file.api ? `${file.api} API` : ''].filter(Boolean).join(' · ');
    const into = importers.get(tile.file)?.length ?? 0;
    body = (
      <>
        <strong>{file.p.slice(cut + 1)}</strong>
        {cut > 0 && <small className="map-tip-path">{file.p.slice(0, cut)}</small>}
        <small>{facts}</small>
        {file.s && <p>{file.s}</p>}
        {file.i?.length || into ? (
          <div className="map-tip-links">
            {file.i?.length ? <span><i className="out" />импортирует {file.i.length}</span> : null}
            {into ? <span><i className="into" />импортируют {into}</span> : null}
          </div>
        ) : null}
        {file.n?.map((note) => <p key={note} className="map-tip-note">{note}</p>)}
        {heatOf(heat, file.p) && <p className="map-tip-heat">{heatText(heatOf(heat, file.p) as HeatItem)}</p>}
      </>
    );
  } else if (tile.kind === 'dir') {
    const parent = tile.path.slice(0, Math.max(0, tile.path.length - tile.name.length - 1));
    body = (
      <>
        <strong>{tile.name}/</strong>
        {parent && <small className="map-tip-path">{parent}</small>}
        <small>{plural(tile.files ?? 0, 'файл', 'файла', 'файлов')}</small>
      </>
    );
  } else {
    body = <><strong>{plural(tile.count ?? 0, 'мелкий файл', 'мелких файла', 'мелких файлов')}</strong><small className="map-tip-path">{tile.path || 'корень проекта'}</small></>;
  }
  return <div className="map-tip" style={{ left, top }} aria-hidden="true">{body}</div>;
}

/** Карта проекта мозаикой: площадь плитки - размер файла, цвет - верхняя папка; щелчок открывает файл или папку. */
export function ProjectMapView({ active }: { active: boolean }) {
  const project = useView((s) => s.project);
  const tree = useProjectMap((s) => s.tree);
  const load = useProjectMap((s) => s.load);
  const error = useProjectMap((s) => s.error);
  const canvas = useRef<HTMLCanvasElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const colors = useRef<Palette | null>(null);
  const heatOn = useQuality((s) => s.heatOn);
  const heatFiles = useQuality((s) => s.heat);
  const heat = heatOn ? heatFiles : null;
  const widths = useRef(new Map<string, number>());
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [hover, setHover] = useState<Hover | null>(null);
  const [theme, setTheme] = useState(0);

  useEffect(() => {
    setMapActive(active);
    return () => setMapActive(false);
  }, [active]);

  useEffect(() => {
    const node = stage.current;
    if (!node || !active) return undefined;
    const observer = new ResizeObserver(() => {
      const box = node.getBoundingClientRect();
      setSize({ width: Math.round(box.width), height: Math.round(box.height) });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [active]);

  // смена темы окна: цвета холста перечитываются из CSS
  useEffect(() => {
    const observer = new MutationObserver(() => {
      colors.current = null;
      setTheme((value) => value + 1);
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-surface'] });
    return () => observer.disconnect();
  }, []);

  const files = useMemo(() => tree?.files ?? [], [tree]);
  const tiles = useMemo(
    () => (tree && size.width && size.height ? layoutTree(buildTree(tree.files, tree.rest), size.width, size.height) : []),
    [tree, size.width, size.height],
  );
  const importers = useMemo(() => {
    const result = new Map<number, number[]>();
    files.forEach((file, index) => file.i?.forEach((target) => result.set(target, [...(result.get(target) ?? []), index])));
    return result;
  }, [files]);
  const tileOfFile = useMemo(() => new Map(tiles.filter((tile) => tile.file !== undefined).map((tile) => [tile.file as number, tile])), [tiles]);
  useEffect(() => setHover(null), [tiles]);

  useEffect(() => {
    const element = canvas.current;
    if (!element || !size.width || !size.height) return;
    const ratio = window.devicePixelRatio || 1;
    if (element.width !== Math.round(size.width * ratio)) element.width = Math.round(size.width * ratio);
    if (element.height !== Math.round(size.height * ratio)) element.height = Math.round(size.height * ratio);
    const context = element.getContext('2d');
    if (!context) return;
    const c = colors.current ?? (colors.current = palette(element));
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, size.width, size.height);
    const focus = hover?.tile.kind === 'file' ? hover.tile.file : undefined;
    const out = new Set(focus !== undefined ? files[focus]?.i ?? [] : []);
    const into = new Set(focus !== undefined ? importers.get(focus) ?? [] : []);
    const hue = (tile: Tile) => (tile.group < 0 ? c.neutral : c.hues[tile.group % HUES] as string);
    const fit = (text: string, room: number): string => {
      const measure = (value: string) => {
        const key = `${context.font}|${value}`;
        let width = widths.current.get(key);
        if (width === undefined) {
          width = context.measureText(value).width;
          widths.current.set(key, width);
        }
        return width;
      };
      if (measure(text) <= room) return text;
      let end = text.length - 1;
      while (end > 1 && measure(`${text.slice(0, end)}…`) > room) end -= 1;
      return end > 1 ? `${text.slice(0, end)}…` : '';
    };
    for (const tile of tiles) {
      const { x, y, w, h } = tile.rect;
      const color = hue(tile);
      if (tile.kind === 'dir') {
        const lit = hover?.tile === tile;
        roundRect(context, x + 0.5, y + 0.5, w - 1, h - 1, 6);
        context.globalAlpha = lit ? 0.14 : 0.06;
        context.fillStyle = color;
        context.fill();
        context.globalAlpha = lit ? 0.75 : 0.3;
        context.strokeStyle = color;
        context.lineWidth = 1;
        context.stroke();
        if (tile.header) {
          context.globalAlpha = 1;
          context.font = `600 10.5px ${c.font}`;
          context.fillStyle = lit ? c.text : c.muted;
          const label = fit(tile.name, w - 12);
          if (label) context.fillText(label, x + 6, y + 12);
        }
        continue;
      }
      const index = tile.file;
      const related = index !== undefined && (index === focus || out.has(index) || into.has(index));
      const strength = focus === undefined ? 0.3 : index === focus ? 0.7 : related ? 0.48 : 0.12;
      roundRect(context, x + 1, y + 1, w - 2, h - 2, 4);
      context.globalAlpha = tile.kind === 'rest' ? strength * 0.5 : strength;
      context.fillStyle = color;
      context.fill();
      // тепловая карта качества: чем больше долг (CRAP, дубли, выжившие мутанты, слои), тем краснее плитка
      const debt = index !== undefined ? heatOf(heat, files[index]?.p ?? '')?.score ?? 0 : 0;
      if (debt > 0) {
        context.globalAlpha = 0.18 + 0.62 * Math.min(1, debt);
        context.fillStyle = c.heat;
        context.fill();
      }
      if (hover?.tile === tile) {
        context.globalAlpha = 1;
        context.strokeStyle = c.text;
        context.lineWidth = 1.5;
        context.stroke();
      }
      if (w >= 30 && h >= 16) {
        context.globalAlpha = focus === undefined || related ? 1 : 0.45;
        context.font = `500 11px ${c.font}`;
        context.fillStyle = c.text;
        const name = fit(tile.name, w - 10);
        if (name) context.fillText(name, x + 5, y + 13);
        const file = index !== undefined ? files[index] : undefined;
        if (h >= 30 && w >= 44 && file?.l) {
          context.font = `400 10px ${c.font}`;
          context.fillStyle = c.muted;
          const lines = fit(plural(file.l, 'строка', 'строки', 'строк'), w - 10);
          if (lines) context.fillText(lines, x + 5, y + 26);
        }
      }
    }
    // связи файла под курсором дугами поверх мозаики: что он импортирует и кто импортирует его
    const from = focus !== undefined ? tileOfFile.get(focus) : undefined;
    if (from) {
      const center = (tile: Tile) => ({ x: tile.rect.x + tile.rect.w / 2, y: tile.rect.y + tile.rect.h / 2 });
      const a = center(from);
      context.lineWidth = 1.5;
      context.globalAlpha = 0.95;
      for (const [set, color] of [[out, c.out], [into, c.into]] as const) {
        context.strokeStyle = color;
        context.fillStyle = color;
        for (const index of set) {
          const target = tileOfFile.get(index);
          if (!target || target === from) continue;
          const b = center(target);
          const lift = Math.min(70, Math.hypot(b.x - a.x, b.y - a.y) / 2);
          context.beginPath();
          context.moveTo(a.x, a.y);
          context.quadraticCurveTo((a.x + b.x) / 2, Math.max(4, Math.min(a.y, b.y) - lift), b.x, b.y);
          context.stroke();
          context.beginPath();
          context.arc(b.x, b.y, 2.6, 0, Math.PI * 2);
          context.fill();
        }
      }
    }
    context.globalAlpha = 1;
  }, [tiles, hover, files, importers, tileOfFile, heat, size.width, size.height, theme]);

  const point = (event: { clientX: number; clientY: number }) => {
    const box = canvas.current?.getBoundingClientRect();
    return { x: event.clientX - (box?.left ?? 0), y: event.clientY - (box?.top ?? 0) };
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const at = point(event);
    const tile = tileAt(tiles, at.x, at.y);
    event.currentTarget.style.cursor = tile ? 'pointer' : '';
    setHover(tile ? { tile, x: at.x, y: at.y } : null);
  };

  const onClick = (event: ReactMouseEvent<HTMLCanvasElement>) => {
    const at = point(event);
    const tile = tileAt(tiles, at.x, at.y);
    if (!tile) return;
    if (tile.kind === 'file') void guarded(() => openFile(tile.path))();
    else void guarded(() => openFolder(tile.path))();
  };

  if (!project) {
    return <div className="empty-list"><Network size={24} aria-hidden="true" /><h3>Проект не выбран</h3><p>Карта появится, когда откроете чат в проекте.</p></div>;
  }
  const building = tree?.state.status === 'building' || (load === 'loading' && !tree);
  const empty = Boolean(tree && !tree.files.length && !tree.rest.length);
  return (
    <div className="project-map">
      <div className="map-stage" ref={stage} onPointerLeave={() => setHover(null)}>
        <canvas
          ref={canvas}
          className="map-canvas"
          role="img"
          aria-label={tree ? `Карта проекта мозаикой: ${plural(tree.totals.files, 'файл', 'файла', 'файлов')}. Те же файлы есть в карточке «Файлы».` : 'Карта проекта'}
          style={{ width: size.width || '100%', height: size.height || '100%' }}
          onPointerMove={onPointerMove}
          onClick={onClick}
        />
        {load === 'failed' && !tree ? (
          <div className="map-empty">
            <strong>Не удалось загрузить карту</strong><p>{error}</p>
            <button type="button" className="map-text-button" onClick={guarded(loadMap)}>Повторить</button>
          </div>
        ) : building && (!tree || empty) ? (
          <div className="map-empty"><span className="spinner" aria-hidden="true" /><p>Карта строится…</p></div>
        ) : empty ? (
          <div className="map-empty"><strong>На карте пока пусто</strong><p>В папке нет файлов исходного кода или документации.</p></div>
        ) : null}
        {hover && tree ? <Tooltip hover={hover} files={files} importers={importers} heat={heat} width={size.width} height={size.height} /> : null}
      </div>
    </div>
  );
}
