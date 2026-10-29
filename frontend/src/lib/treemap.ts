/* Мозаика карты проекта: дерево папок из путей и раскладка squarified (Bruls и др.) - плитки близки к квадратам,
   поэтому имена файлов читаются. Без зависимостей, на 6000 файлов хватает одного прохода. */

export interface Rect { x: number; y: number; w: number; h: number }

export interface TreeFile {
  p: string;
  w: number;
  l?: number;
  lang?: string;
  s?: string;
  i?: number[];
  api?: number;
  n?: string[];
}

export interface TreeRest { p: string; count: number; w: number }

interface Folder {
  path: string;
  name: string;
  weight: number;
  files: number;
  folders: Map<string, Folder>;
  items: { file: number; weight: number; path: string }[];
  rest: TreeRest | null;
}

export type TileKind = 'dir' | 'file' | 'rest';

export interface Tile {
  kind: TileKind;
  path: string;
  name: string;
  depth: number;
  // папка первого или второго уровня: по ней красится плитка, чтобы части проекта различались цветом
  group: number;
  rect: Rect;
  // полоса с именем папки сверху; у мелкой папки её нет
  header: boolean;
  file?: number;
  files?: number;
  count?: number;
}

const HEADER = 16;
const PAD = 2;

export function buildTree(files: TreeFile[], rest: TreeRest[] = []): Folder {
  const root: Folder = { path: '', name: '', weight: 0, files: 0, folders: new Map(), items: [], rest: null };
  const folderOf = (path: string): Folder => {
    let current = root;
    if (!path) return current;
    let walked = '';
    for (const part of path.split('/')) {
      walked = walked ? `${walked}/${part}` : part;
      let next = current.folders.get(part);
      if (!next) {
        next = { path: walked, name: part, weight: 0, files: 0, folders: new Map(), items: [], rest: null };
        current.folders.set(part, next);
      }
      current = next;
    }
    return current;
  };
  const add = (path: string, weight: number, count: number) => {
    let current = root;
    current.weight += weight;
    current.files += count;
    for (const part of path ? path.split('/') : []) {
      current = current.folders.get(part) as Folder;
      current.weight += weight;
      current.files += count;
    }
  };
  files.forEach((file, index) => {
    const cut = file.p.lastIndexOf('/');
    const parent = cut < 0 ? '' : file.p.slice(0, cut);
    const weight = Math.max(1, file.w);
    folderOf(parent).items.push({ file: index, weight, path: file.p });
    add(parent, weight, 1);
  });
  for (const item of rest) {
    folderOf(item.p).rest = item;
    add(item.p, Math.max(1, item.w), item.count);
  }
  return root;
}

const worst = (row: number[], side: number): number => {
  let sum = 0;
  let max = 0;
  let min = Infinity;
  for (const value of row) {
    sum += value;
    max = Math.max(max, value);
    min = Math.min(min, value);
  }
  const s2 = sum * sum;
  const side2 = side * side;
  return Math.max((side2 * max) / s2, s2 / (side2 * min));
};

/** Squarified: значения (уже отсортированные по убыванию) раскладываются в прямоугольник с площадью пропорционально весу. */
export function squarify(values: number[], rect: Rect): Rect[] {
  const total = values.reduce((sum, value) => sum + value, 0);
  const out: Rect[] = [];
  if (!values.length || total <= 0 || rect.w <= 0 || rect.h <= 0) return values.map(() => ({ x: rect.x, y: rect.y, w: 0, h: 0 }));
  const scale = (rect.w * rect.h) / total;
  const areas = values.map((value) => value * scale);
  let { x, y, w, h } = rect;
  let row: number[] = [];
  const flush = () => {
    const sum = row.reduce((acc, value) => acc + value, 0);
    if (w >= h) {
      const width = sum / h;
      let top = y;
      for (const area of row) {
        const height = area / width;
        out.push({ x, y: top, w: width, h: height });
        top += height;
      }
      x += width;
      w -= width;
    } else {
      const height = sum / w;
      let left = x;
      for (const area of row) {
        const width = area / height;
        out.push({ x: left, y, w: width, h: height });
        left += width;
      }
      y += height;
      h -= height;
    }
    row = [];
  };
  for (const area of areas) {
    const side = Math.min(w, h);
    if (!row.length || worst([...row, area], side) <= worst(row, side)) row.push(area);
    else {
      flush();
      row.push(area);
    }
  }
  if (row.length) flush();
  return out;
}

/** Плитки всех уровней в порядке отрисовки: папка раньше своего содержимого. */
export function layoutTree(root: Folder, width: number, height: number): Tile[] {
  const tiles: Tile[] = [];
  let groups = 0;
  const visit = (start: Folder, rect: Rect, depth: number, group: number) => {
    let inner = rect;
    let folder = start;
    let name = start.name;
    // цепочка папок с единственной вложенной - одна плитка «src/components», иначе заголовки съедают высоту
    while (depth > 0 && folder.folders.size === 1 && !folder.items.length && !folder.rest) {
      folder = folder.folders.values().next().value as Folder;
      name = `${name}/${folder.name}`;
    }
    if (depth > 0) {
      const header = rect.w >= 44 && rect.h >= HEADER + 14;
      tiles.push({ kind: 'dir', path: folder.path, name, depth, group, rect, header, files: folder.files });
      // в слишком мелкой папке файлы не различить: она остаётся одной плиткой
      if (rect.w < 10 || rect.h < 10) return;
      inner = header
        ? { x: rect.x + PAD, y: rect.y + HEADER, w: rect.w - PAD * 2, h: rect.h - HEADER - PAD }
        : { x: rect.x + PAD, y: rect.y + PAD, w: rect.w - PAD * 2, h: rect.h - PAD * 2 };
      if (inner.w <= 1 || inner.h <= 1) return;
    }
    const children: ({ folder: Folder } | { file: number; path: string } | { rest: TreeRest })[] = [];
    const weights: number[] = [];
    const entries = [
      ...[...folder.folders.values()].map((item) => ({ weight: item.weight, entry: { folder: item } })),
      ...folder.items.map((item) => ({ weight: item.weight, entry: { file: item.file, path: item.path } })),
      ...(folder.rest ? [{ weight: Math.max(1, folder.rest.w), entry: { rest: folder.rest } }] : []),
    ].sort((a, b) => b.weight - a.weight);
    for (const { weight, entry } of entries) {
      weights.push(weight);
      children.push(entry);
    }
    const rects = squarify(weights, inner);
    children.forEach((child, index) => {
      const box = rects[index] as Rect;
      // свой цвет у папок двух верхних уровней: иначе проект с одной корневой папкой весь одного цвета
      if ('folder' in child) visit(child.folder, box, depth + 1, depth < 2 ? groups++ : group);
      else if ('file' in child) {
        tiles.push({ kind: 'file', path: child.path, name: child.path.slice(child.path.lastIndexOf('/') + 1), depth: depth + 1, group: depth === 0 ? -1 : group, rect: box, header: false, file: child.file });
      } else tiles.push({ kind: 'rest', path: child.rest.p, name: `ещё ${child.rest.count}`, depth: depth + 1, group: depth === 0 ? -1 : group, rect: box, header: false, count: child.rest.count });
    });
  };
  visit(root, { x: 0, y: 0, w: width, h: height }, 0, -1);
  return tiles;
}

/** Самая глубокая плитка под точкой: файл внутри папки важнее самой папки. */
export function tileAt(tiles: Tile[], x: number, y: number): Tile | null {
  let best: Tile | null = null;
  for (const tile of tiles) {
    const { rect } = tile;
    if (x >= rect.x && x < rect.x + rect.w && y >= rect.y && y < rect.y + rect.h && (!best || tile.depth >= best.depth)) best = tile;
  }
  return best;
}
