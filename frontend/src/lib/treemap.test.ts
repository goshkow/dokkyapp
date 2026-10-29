import { describe, expect, it } from 'vitest';
import { buildTree, layoutTree, squarify, tileAt, type Rect } from './treemap';

const area = (rect: Rect) => rect.w * rect.h;

describe('мозаика карты проекта', () => {
  it('squarify делит прямоугольник пропорционально весам и не выходит за границы', () => {
    const bounds = { x: 10, y: 20, w: 300, h: 200 };
    const values = [50, 30, 10, 6, 3, 1];
    const rects = squarify(values, bounds);
    expect(rects).toHaveLength(values.length);
    const total = area(bounds);
    rects.forEach((rect, index) => {
      expect(area(rect)).toBeCloseTo((total * (values[index] as number)) / 100, 6);
      expect(rect.x).toBeGreaterThanOrEqual(bounds.x - 1e-6);
      expect(rect.y).toBeGreaterThanOrEqual(bounds.y - 1e-6);
      expect(rect.x + rect.w).toBeLessThanOrEqual(bounds.x + bounds.w + 1e-6);
      expect(rect.y + rect.h).toBeLessThanOrEqual(bounds.y + bounds.h + 1e-6);
    });
    // плитки близки к квадратам: у крупнейшей стороны отличаются не больше чем втрое
    const big = rects[0] as Rect;
    expect(Math.max(big.w / big.h, big.h / big.w)).toBeLessThan(3);
  });

  it('пустой ввод и нулевая площадь дают пустые плитки, а не NaN', () => {
    expect(squarify([], { x: 0, y: 0, w: 10, h: 10 })).toEqual([]);
    expect(squarify([1, 2], { x: 0, y: 0, w: 0, h: 10 })).toEqual([{ x: 0, y: 0, w: 0, h: 0 }, { x: 0, y: 0, w: 0, h: 0 }]);
  });

  it('дерево считает веса папок, мелкие файлы сверх лимита идут плиткой «ещё N»', () => {
    const root = buildTree(
      [{ p: 'app/a.py', w: 60 }, { p: 'app/ui/b.ts', w: 30 }, { p: 'README.md', w: 10 }],
      [{ p: 'app/ui', count: 12, w: 20 }],
    );
    expect(root.weight).toBe(120);
    expect(root.files).toBe(15);
    const app = root.folders.get('app');
    expect(app?.weight).toBe(110);
    expect(app?.folders.get('ui')?.rest?.count).toBe(12);
  });

  it('раскладка: папка раньше содержимого, цвет по папкам двух верхних уровней, щелчок попадает в самую глубокую плитку', () => {
    const files = [{ p: 'app/a.py', w: 60 }, { p: 'app/ui/b.ts', w: 30 }, { p: 'docs/c.md', w: 20 }, { p: 'README.md', w: 10 }];
    const tiles = layoutTree(buildTree(files, [{ p: 'app/ui', count: 4, w: 8 }]), 400, 300);
    const index = (path: string) => tiles.findIndex((tile) => tile.path === path);
    expect(index('app')).toBeLessThan(index('app/a.py'));
    expect(index('app/ui')).toBeLessThan(index('app/ui/b.ts'));
    const byPath = (path: string, kind = 'file') => tiles.find((tile) => tile.path === path && tile.kind === kind);
    const app = byPath('app/a.py')?.group;
    expect(byPath('app', 'dir')?.group).toBe(app);
    expect(byPath('app/ui', 'rest')?.group).toBe(byPath('app/ui/b.ts')?.group);
    expect(new Set([app, byPath('app/ui/b.ts')?.group, byPath('docs/c.md')?.group]).size).toBe(3);
    expect(byPath('README.md')?.group).toBe(-1);
    expect(byPath('app/ui', 'rest')?.name).toBe('ещё 4');
    expect(tiles.find((tile) => tile.path === 'app')?.header).toBe(true);
    const leaf = byPath('app/ui/b.ts');
    expect(leaf?.name).toBe('b.ts');
    const { x, y, w, h } = leaf?.rect as Rect;
    expect(tileAt(tiles, x + w / 2, y + h / 2)?.path).toBe('app/ui/b.ts');
    const folder = tiles.find((tile) => tile.path === 'app') as (typeof tiles)[number];
    expect(tileAt(tiles, folder.rect.x + 4, folder.rect.y + 4)?.path).toBe('app');
    expect(tileAt(tiles, -5, -5)).toBeNull();
  });

  it('цепочка одиночных папок склеивается в одну плитку', () => {
    const tiles = layoutTree(buildTree([{ p: 'src/components/inspector/A.tsx', w: 30 }, { p: 'src/components/inspector/B.tsx', w: 20 }, { p: 'lib/c.ts', w: 10 }]), 400, 300);
    const dirs = tiles.filter((tile) => tile.kind === 'dir');
    expect(dirs.map((tile) => [tile.name, tile.path])).toEqual([['src/components/inspector', 'src/components/inspector'], ['lib', 'lib']]);
  });

  it('плитки файлов в сумме не больше холста', () => {
    const files = Array.from({ length: 200 }, (_, i) => ({ p: `d${i % 7}/s${i % 3}/f${i}.ts`, w: 1 + (i * 37) % 90 }));
    const tiles = layoutTree(buildTree(files), 320, 240);
    const sum = tiles.filter((tile) => tile.kind === 'file').reduce((acc, tile) => acc + area(tile.rect), 0);
    expect(sum).toBeLessThanOrEqual(320 * 240 + 1e-6);
    expect(tiles.every((tile) => Number.isFinite(tile.rect.x + tile.rect.y + tile.rect.w + tile.rect.h))).toBe(true);
  });
});
