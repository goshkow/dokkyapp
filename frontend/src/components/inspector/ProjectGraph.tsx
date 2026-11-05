import { Minus, Plus, Scan } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { buildGraph, fitView, nodeAt, Simulation, zoomAt, type GraphModel, type View } from '../../lib/forceGraph';
import { numberText } from '../../lib/format';
import type { TreeFile } from '../../lib/treemap';
import { guarded } from '../../state/overlay';
import { openFile } from '../../state/workspace';

export interface GraphColors { hues: string[]; neutral: string; text: string; muted: string; out: string; into: string; font: string }

interface Props {
  files: TreeFile[];
  width: number;
  height: number;
  colors: GraphColors;
  /** меняется со сменой темы: холст перерисовывается */
  theme: number;
}

const CLICK_SLOP = 4;
const MAX_LABELS = 140;

/** Граф связей: файлы - узлы, импорты - линии. Колесо и щипок - масштаб, перетаскивание фона - сдвиг, узла - перемещение. */
export function ProjectGraph({ files, width, height, colors, theme }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const model = useRef<GraphModel | null>(null);
  const simulation = useRef<Simulation | null>(null);
  const view = useRef<View>({ k: 1, x: 0, y: 0 });
  const hoverNode = useRef(-1);
  const frame = useRef(0);
  const fitted = useRef(false);
  const latest = useRef({ colors, width, height });
  latest.current = { colors, width, height };
  const [hover, setHover] = useState<{ node: number; x: number; y: number } | null>(null);
  const graph = useMemo(() => {
    const previous = new Map<string, { x: number; y: number }>();
    for (const node of model.current?.nodes ?? []) previous.set(node.path, { x: node.x, y: node.y });
    return buildGraph(files, previous);
    // позиции прежнего графа читаются один раз при смене файлов
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files]);

  const draw = useRef<() => void>(() => undefined);
  const request = () => {
    if (!frame.current) frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      draw.current();
    });
  };

  draw.current = () => {
    const element = canvas.current;
    const current = model.current;
    const sim = simulation.current;
    const { colors: c, width: w, height: h } = latest.current;
    if (!element || !current || !sim || !w || !h) return;
    const ratio = window.devicePixelRatio || 1;
    if (element.width !== Math.round(w * ratio)) element.width = Math.round(w * ratio);
    if (element.height !== Math.round(h * ratio)) element.height = Math.round(h * ratio);
    const context = element.getContext('2d');
    if (!context) return;
    if (sim.active) sim.tick();
    const { k, x: ox, y: oy } = view.current;
    const { nodes, edges, out, into } = current;
    const focus = hoverNode.current;
    const near = new Set<number>();
    if (focus >= 0) {
      near.add(focus);
      for (const index of [...(out[focus] ?? []), ...(into[focus] ?? [])]) near.add(index);
    }
    const hue = (group: number) => (group < 0 ? c.neutral : c.hues[group % c.hues.length] as string);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, w, h);
    context.translate(ox, oy);
    context.scale(k, k);
    // линии: при наведении остальные едва видны, связи узла яркие
    const line = Math.max(0.6, 1 / k);
    context.lineWidth = line;
    context.strokeStyle = c.muted;
    context.globalAlpha = focus >= 0 ? 0.05 : Math.min(0.4, 0.14 + 0.04 * k);
    context.beginPath();
    for (const [a, b] of edges) {
      const from = nodes[a]!;
      const to = nodes[b]!;
      context.moveTo(from.x, from.y);
      context.lineTo(to.x, to.y);
    }
    context.stroke();
    if (focus >= 0) {
      const center = nodes[focus]!;
      context.lineWidth = line * 1.6;
      context.globalAlpha = 0.95;
      for (const [set, color] of [[out[focus] ?? [], c.out], [into[focus] ?? [], c.into]] as const) {
        context.strokeStyle = color;
        context.beginPath();
        for (const index of set) {
          context.moveTo(center.x, center.y);
          context.lineTo(nodes[index]!.x, nodes[index]!.y);
        }
        context.stroke();
      }
    }
    for (let index = 0; index < nodes.length; index += 1) {
      const node = nodes[index]!;
      const sx = node.x * k + ox;
      const sy = node.y * k + oy;
      if (sx < -20 || sy < -20 || sx > w + 20 || sy > h + 20) continue;
      context.globalAlpha = focus < 0 || near.has(index) ? 1 : 0.14;
      context.fillStyle = hue(node.group);
      context.beginPath();
      context.arc(node.x, node.y, node.r, 0, Math.PI * 2);
      context.fill();
      if (index === focus) {
        context.strokeStyle = c.text;
        context.lineWidth = 1.5 / k;
        context.stroke();
      }
    }
    // подписи: у крупных узлов, у соседей наведённого и у всех при увеличении
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.font = `500 11px ${c.font}`;
    context.textAlign = 'center';
    context.fillStyle = c.text;
    let labels = 0;
    const byBigness = nodes.map((_, index) => index).filter((index) => {
      const node = nodes[index]!;
      return focus >= 0 ? near.has(index) : node.r * k >= 9 || (k >= 1.8 && node.r * k >= 4) || k >= 4;
    }).sort((a, b) => nodes[b]!.degree - nodes[a]!.degree);
    for (const index of byBigness) {
      if (labels >= MAX_LABELS) break;
      const node = nodes[index]!;
      const sx = node.x * k + ox;
      const sy = node.y * k + oy;
      if (sx < 0 || sy < 0 || sx > w || sy > h) continue;
      const path = files[node.file]?.p ?? '';
      context.globalAlpha = focus >= 0 && index !== focus ? 0.85 : 1;
      context.fillText(path.slice(path.lastIndexOf('/') + 1), sx, sy + node.r * k + 12);
      labels += 1;
    }
    context.globalAlpha = 1;
    if (sim.active) request();
  };

  // новая модель: первая раскладка считается заранее, вид подгоняется под неё один раз
  useEffect(() => {
    model.current = graph;
    const sim = new Simulation(graph);
    simulation.current = sim;
    const first = !fitted.current;
    if (first) sim.warm(60);
    else sim.reheat(0.3);
    if (first && width && height) {
      view.current = fitView(graph.nodes, width, height);
      fitted.current = graph.nodes.length > 0;
    }
    hoverNode.current = -1;
    setHover(null);
    request();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph]);

  useEffect(() => {
    if (!fitted.current && model.current && width && height) {
      view.current = fitView(model.current.nodes, width, height);
      fitted.current = model.current.nodes.length > 0;
    }
    request();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [width, height, colors, theme]);

  useEffect(() => () => {
    if (frame.current) cancelAnimationFrame(frame.current);
    frame.current = 0;
  }, []);

  const world = (sx: number, sy: number) => ({ x: (sx - view.current.x) / view.current.k, y: (sy - view.current.y) / view.current.k });
  const local = (event: { clientX: number; clientY: number }) => {
    const box = canvas.current?.getBoundingClientRect();
    return { x: event.clientX - (box?.left ?? 0), y: event.clientY - (box?.top ?? 0) };
  };
  const zoomBy = (factor: number, sx = latest.current.width / 2, sy = latest.current.height / 2) => {
    view.current = zoomAt(view.current, factor, sx, sy);
    request();
  };

  // колесо: масштаб вокруг курсора; щипок на трекпаде приходит колесом с ctrlKey
  useEffect(() => {
    const element = canvas.current;
    if (!element) return undefined;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const at = local(event);
      const unit = event.deltaMode === 1 ? 16 : 1;
      // два пальца на трекпаде двигают граф, щипок и колесо мыши масштабируют
      const wheel = event.deltaMode !== 0 || (event.deltaX === 0 && Math.abs(event.deltaY) >= 50 && Number.isInteger(event.deltaY));
      if (!event.ctrlKey && !wheel) {
        view.current = { ...view.current, x: view.current.x - event.deltaX, y: view.current.y - event.deltaY };
        request();
        return;
      }
      zoomBy(Math.exp((-event.deltaY * unit) * (event.ctrlKey ? 0.01 : 0.0018)), at.x, at.y);
    };
    element.addEventListener('wheel', onWheel, { passive: false });
    return () => element.removeEventListener('wheel', onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const drag = useRef<{ mode: 'pan' | 'node' | 'pinch'; node: number; startX: number; startY: number; moved: number; distance: number } | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    const at = local(event);
    pointers.current.set(event.pointerId, at);
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()] as [{ x: number; y: number }, { x: number; y: number }];
      const previous = drag.current;
      if (previous?.mode === 'node' && model.current) (model.current.nodes[previous.node] as { pinned: boolean }).pinned = false;
      drag.current = { mode: 'pinch', node: -1, startX: at.x, startY: at.y, moved: CLICK_SLOP + 1, distance: Math.hypot(a.x - b.x, a.y - b.y) };
      return;
    }
    const point = world(at.x, at.y);
    const node = model.current ? nodeAt(model.current.nodes, point.x, point.y, 3 / view.current.k) : -1;
    drag.current = { mode: node >= 0 ? 'node' : 'pan', node, startX: at.x, startY: at.y, moved: 0, distance: 0 };
    if (node >= 0 && model.current) {
      (model.current.nodes[node] as { pinned: boolean }).pinned = true;
      simulation.current?.reheat(0.3);
      request();
    }
  };

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const at = local(event);
    const state = drag.current;
    if (!state) {
      const point = world(at.x, at.y);
      const node = model.current ? nodeAt(model.current.nodes, point.x, point.y, 3 / view.current.k) : -1;
      event.currentTarget.style.cursor = node >= 0 ? 'pointer' : 'grab';
      if (node !== hoverNode.current) {
        hoverNode.current = node;
        request();
      }
      setHover(node >= 0 ? { node, x: at.x, y: at.y } : null);
      return;
    }
    const previous = pointers.current.get(event.pointerId);
    pointers.current.set(event.pointerId, at);
    if (state.mode === 'pinch' && pointers.current.size >= 2) {
      const [a, b] = [...pointers.current.values()] as [{ x: number; y: number }, { x: number; y: number }];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      if (state.distance > 0 && distance > 0) zoomBy(distance / state.distance, (a.x + b.x) / 2, (a.y + b.y) / 2);
      state.distance = distance;
      return;
    }
    state.moved = Math.max(state.moved, Math.hypot(at.x - state.startX, at.y - state.startY));
    if (state.mode === 'node' && model.current) {
      const node = model.current.nodes[state.node]!;
      const point = world(at.x, at.y);
      node.x = point.x;
      node.y = point.y;
      simulation.current?.reheat(0.3);
    } else if (state.mode === 'pan' && previous) {
      view.current = { ...view.current, x: view.current.x + at.x - previous.x, y: view.current.y + at.y - previous.y };
      event.currentTarget.style.cursor = 'grabbing';
    }
    setHover(null);
    request();
  };

  const onPointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    pointers.current.delete(event.pointerId);
    const state = drag.current;
    if (pointers.current.size === 0) drag.current = null;
    if (!state) return;
    if (state.mode === 'node' && model.current) {
      (model.current.nodes[state.node] as { pinned: boolean }).pinned = false;
      const file = files[model.current.nodes[state.node]!.file];
      // щелчок без сдвига открывает файл так же, как щелчок по плитке мозаики
      if (state.moved <= CLICK_SLOP && file && event.type === 'pointerup') void guarded(() => openFile(file.p))();
    }
    request();
  };

  const onLeave = () => {
    if (drag.current) return;
    hoverNode.current = -1;
    setHover(null);
    request();
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLCanvasElement>) => {
    const step = 40;
    const moves: Record<string, [number, number]> = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
    const move = moves[event.key];
    if (move) {
      view.current = { ...view.current, x: view.current.x + move[0], y: view.current.y + move[1] };
    } else if (event.key === '+' || event.key === '=') zoomBy(1.25);
    else if (event.key === '-' || event.key === '_') zoomBy(0.8);
    else if (event.key === '0') view.current = fitView(model.current?.nodes ?? [], width, height);
    else return;
    event.preventDefault();
    request();
  };

  const reset = () => {
    view.current = fitView(model.current?.nodes ?? [], width, height);
    request();
  };

  const nodes = graph.nodes;
  const shown = hover ? files[nodes[hover.node]?.file ?? -1] : undefined;
  const hovered = hover ? nodes[hover.node] : undefined;
  return (
    <>
      <canvas
        ref={canvas}
        className="map-canvas map-graph"
        role="application"
        tabIndex={0}
        aria-label={`Граф связей: ${numberText(nodes.length)} файлов, ${numberText(graph.edges.length)} связей. Колесо или клавиши плюс и минус - масштаб, стрелки - сдвиг, ноль - вписать целиком.`}
        style={{ width: width || '100%', height: height || '100%' }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={onLeave}
        onKeyDown={onKeyDown}
      />
      <div className="map-zoom" role="group" aria-label="Масштаб графа">
        <button type="button" aria-label="Приблизить" title="Приблизить" onClick={() => zoomBy(1.4)}><Plus size={14} aria-hidden="true" /></button>
        <button type="button" aria-label="Отдалить" title="Отдалить" onClick={() => zoomBy(1 / 1.4)}><Minus size={14} aria-hidden="true" /></button>
        <button type="button" aria-label="Вписать граф целиком" title="Вписать целиком" onClick={reset}><Scan size={14} aria-hidden="true" /></button>
      </div>
      {shown && hovered && hover ? (
        <div className="map-tip" style={{ left: hover.x + 250 > width ? Math.max(6, hover.x - 246) : hover.x + 14, top: hover.y + 120 > height ? Math.max(6, hover.y - 110) : hover.y + 16 }} aria-hidden="true">
          <strong>{shown.p.slice(shown.p.lastIndexOf('/') + 1)}</strong>
          {shown.p.includes('/') && <small className="map-tip-path">{shown.p.slice(0, shown.p.lastIndexOf('/'))}</small>}
          <small>{[shown.lang, shown.l ? `${numberText(shown.l)} строк` : ''].filter(Boolean).join(' · ')}</small>
          {shown.s && <p>{shown.s}</p>}
          <div className="map-tip-links">
            {graph.out[hover.node]?.length ? <span><i className="out" />импортирует {graph.out[hover.node]!.length}</span> : null}
            {graph.into[hover.node]?.length ? <span><i className="into" />импортируют {graph.into[hover.node]!.length}</span> : null}
          </div>
        </div>
      ) : null}
    </>
  );
}
