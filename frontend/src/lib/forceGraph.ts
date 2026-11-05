/* Граф связей карты проекта: узлы - файлы, рёбра - импорты. Силовая раскладка без зависимостей:
   пружины по рёбрам, отталкивание через сетку (считаются только близкие соседи), слабое тяготение к центру.
   Симуляция останавливается сама, когда узлы успокоились. */
import type { TreeFile } from './treemap';

export interface GraphNode {
  /** индекс файла в tree.files */
  file: number;
  path: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  /** сколько связей у файла: входящих и исходящих */
  degree: number;
  /** верхняя папка: по ней красится узел, -1 - файл в корне */
  group: number;
  /** узел зажат курсором и не двигается силами */
  pinned: boolean;
}

export interface GraphModel {
  nodes: GraphNode[];
  /** пары индексов в nodes: от импортирующего к импортируемому */
  edges: Array<[number, number]>;
  out: number[][];
  into: number[][];
}

export const MAX_GRAPH_NODES = 2500;
const CELL = 56;
const LINK = 34;
const ALPHA_DECAY = 0.0228;
const ALPHA_MIN = 0.003;
const VELOCITY_DECAY = 0.55;

const topFolder = (path: string): string => {
  const cut = path.indexOf('/');
  return cut < 0 ? '' : path.slice(0, cut);
};

/** Узлы и рёбра по файлам карты. Если файлов больше лимита, остаются самые связанные; позиции прежних узлов сохраняются. */
export function buildGraph(files: TreeFile[], previous?: Map<string, { x: number; y: number }>, limit = MAX_GRAPH_NODES): GraphModel {
  const degree = new Array<number>(files.length).fill(0);
  const seen = new Set<number>();
  const pairs: Array<[number, number]> = [];
  files.forEach((file, from) => {
    for (const to of file.i ?? []) {
      if (to === from || to < 0 || to >= files.length) continue;
      const key = from * files.length + to;
      if (seen.has(key)) continue;
      seen.add(key);
      degree[from] = (degree[from] as number) + 1;
      degree[to] = (degree[to] as number) + 1;
      pairs.push([from, to]);
    }
  });
  const kept = files.map((_, index) => index).filter((index) => (degree[index] as number) > 0);
  // файлы без связей показываются, пока есть место: они дают картину проекта целиком
  const alone = files.map((_, index) => index).filter((index) => !(degree[index] as number));
  const chosen = kept.length >= limit
    ? kept.sort((a, b) => (degree[b] as number) - (degree[a] as number) || a - b).slice(0, limit)
    : [...kept, ...alone.slice(0, limit - kept.length)];
  chosen.sort((a, b) => a - b);
  const position = new Map(chosen.map((file, index) => [file, index]));
  const groups = new Map<string, number>();
  for (const file of chosen) {
    const top = topFolder((files[file] as TreeFile).p);
    if (top && !groups.has(top)) groups.set(top, groups.size);
  }
  const nodes: GraphNode[] = chosen.map((file, index) => {
    const item = files[file] as TreeFile;
    const top = topFolder(item.p);
    const old = previous?.get(item.p);
    // новые узлы раскладываются спиралью, чтобы силы не начинали с одной точки
    const angle = index * 2.399963;
    const radius = 8 * Math.sqrt(index + 1);
    return {
      file, path: item.p, x: old?.x ?? Math.cos(angle) * radius, y: old?.y ?? Math.sin(angle) * radius, vx: 0, vy: 0,
      r: Math.min(18, 3 + Math.sqrt(degree[file] as number) * 1.7), degree: degree[file] as number,
      group: top ? (groups.get(top) as number) : -1, pinned: false,
    };
  });
  const edges: Array<[number, number]> = [];
  const out: number[][] = nodes.map(() => []);
  const into: number[][] = nodes.map(() => []);
  for (const [from, to] of pairs) {
    const a = position.get(from);
    const b = position.get(to);
    if (a === undefined || b === undefined) continue;
    edges.push([a, b]);
    (out[a] as number[]).push(b);
    (into[b] as number[]).push(a);
  }
  return { nodes, edges, out, into };
}

/** Силовая раскладка: `tick()` делает один шаг, `alpha` падает до нуля, `active` говорит, что рисовать дальше нужно. */
export class Simulation {
  alpha = 1;
  private chain = new Int32Array(0);

  constructor(readonly model: GraphModel) {}

  get active(): boolean {
    return this.alpha >= ALPHA_MIN || this.model.nodes.some((node) => node.pinned);
  }

  /** Разогреть: после перетаскивания или новых данных узлы снова двигаются. */
  reheat(alpha = 0.35): void {
    this.alpha = Math.max(this.alpha, alpha);
  }

  tick(): void {
    const { nodes, edges } = this.model;
    const count = nodes.length;
    if (!count) return;
    const alpha = this.alpha;
    // отталкивание: только узлы из соседних ячеек сетки, поэтому шаг почти линеен по числу узлов
    // списки узлов по ячейкам без выделений на каждый шаг: head - первый узел ячейки, chain - следующий в ней
    const head = new Map<number, number>();
    const chain = this.chain.length >= count ? this.chain : (this.chain = new Int32Array(count));
    const key = (cx: number, cy: number) => cx * 73856093 ^ cy * 19349663;
    for (let index = 0; index < count; index += 1) {
      const node = nodes[index] as GraphNode;
      const id = key(Math.floor(node.x / CELL), Math.floor(node.y / CELL));
      chain[index] = head.get(id) ?? -1;
      head.set(id, index);
    }
    const reach = CELL * CELL * 2.56;
    for (let index = 0; index < count; index += 1) {
      const a = nodes[index] as GraphNode;
      const cx = Math.floor(a.x / CELL);
      const cy = Math.floor(a.y / CELL);
      for (let dx = -1; dx <= 1; dx += 1) {
        for (let dy = -1; dy <= 1; dy += 1) {
          for (let other = head.get(key(cx + dx, cy + dy)) ?? -1; other >= 0; other = chain[other] as number) {
            if (other <= index) continue;
            const b = nodes[other] as GraphNode;
            let x = b.x - a.x;
            let y = b.y - a.y;
            let d2 = x * x + y * y;
            if (d2 > reach) continue;
            if (d2 < 0.01) {
              // совпавшие узлы расходятся в детерминированную сторону
              x = ((index + other) % 7) - 3 || 1;
              y = ((index * other) % 5) - 2 || 1;
              d2 = x * x + y * y;
            }
            const d = Math.sqrt(d2);
            const push = (alpha * (60 + (a.r + b.r) * 6)) / d2;
            const fx = (x / d) * push;
            const fy = (y / d) * push;
            b.vx += fx;
            b.vy += fy;
            a.vx -= fx;
            a.vy -= fy;
          }
        }
      }
    }
    // пружины по рёбрам; хабы с большой степенью тянутся слабее, иначе они сжимают всё вокруг
    for (const [from, to] of edges) {
      const a = nodes[from] as GraphNode;
      const b = nodes[to] as GraphNode;
      let x = b.x - a.x;
      let y = b.y - a.y;
      const d = Math.sqrt(x * x + y * y) || 0.01;
      const strength = (alpha * 0.5 * (d - LINK)) / d / Math.min(Math.max(a.degree, b.degree), 12) ** 0.5;
      x *= strength;
      y *= strength;
      b.vx -= x;
      b.vy -= y;
      a.vx += x;
      a.vy += y;
    }
    for (const node of nodes) {
      if (node.pinned) {
        node.vx = 0;
        node.vy = 0;
        continue;
      }
      // слабое тяготение к центру держит несвязанные части рядом
      node.vx -= node.x * 0.012 * alpha;
      node.vy -= node.y * 0.012 * alpha;
      node.vx *= 1 - VELOCITY_DECAY;
      node.vy *= 1 - VELOCITY_DECAY;
      node.x += node.vx;
      node.y += node.vy;
    }
    this.alpha *= 1 - ALPHA_DECAY;
    if (this.alpha < ALPHA_MIN) this.alpha = 0;
  }

  /** Шаги вперёд в пределах времени: первая раскладка готова до первого кадра. */
  warm(budgetMs: number, maxTicks = 120): void {
    const start = performance.now();
    for (let step = 0; step < maxTicks && this.alpha > 0.05; step += 1) {
      this.tick();
      if (performance.now() - start > budgetMs) break;
    }
  }
}

/** Узел под точкой мира (с запасом на мелкие узлы); при наложении выбирается ближайший к центру. */
export function nodeAt(nodes: GraphNode[], x: number, y: number, slack = 2): number {
  let best = -1;
  let bestDistance = Infinity;
  for (let index = 0; index < nodes.length; index += 1) {
    const node = nodes[index] as GraphNode;
    const d = Math.hypot(node.x - x, node.y - y);
    if (d <= node.r + slack && d < bestDistance) {
      best = index;
      bestDistance = d;
    }
  }
  return best;
}

export interface View { k: number; x: number; y: number }

/** Вид, в который целиком помещаются все узлы с полями. */
export function fitView(nodes: GraphNode[], width: number, height: number, pad = 28): View {
  if (!nodes.length || width <= 0 || height <= 0) return { k: 1, x: width / 2, y: height / 2 };
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    minX = Math.min(minX, node.x - node.r);
    minY = Math.min(minY, node.y - node.r);
    maxX = Math.max(maxX, node.x + node.r);
    maxY = Math.max(maxY, node.y + node.r);
  }
  const k = Math.min(2.5, Math.max(0.05, Math.min((width - pad * 2) / Math.max(1, maxX - minX), (height - pad * 2) / Math.max(1, maxY - minY))));
  return { k, x: width / 2 - ((minX + maxX) / 2) * k, y: height / 2 - ((minY + maxY) / 2) * k };
}

/** Зум вокруг точки экрана: точка под курсором остаётся на месте. */
export function zoomAt(view: View, factor: number, sx: number, sy: number, min = 0.05, max = 8): View {
  const k = Math.min(max, Math.max(min, view.k * factor));
  const ratio = k / view.k;
  return { k, x: sx - (sx - view.x) * ratio, y: sy - (sy - view.y) * ratio };
}
