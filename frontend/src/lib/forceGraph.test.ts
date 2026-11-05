import { describe, expect, it } from 'vitest';
import { buildGraph, fitView, nodeAt, Simulation, zoomAt } from './forceGraph';
import type { TreeFile } from './treemap';

const file = (p: string, i?: number[]): TreeFile => ({ p, w: 10, i });

describe('граф карты проекта', () => {
  const files = [file('src/a.ts', [1, 2, 1]), file('src/b.ts', [2]), file('lib/c.ts'), file('README.md'), file('x.ts', [0, 99, 4])];

  it('рёбра без повторов и петель, степень считает входящие и исходящие', () => {
    const graph = buildGraph(files);
    expect(graph.edges).toHaveLength(4);
    expect(graph.nodes[0]?.degree).toBe(3);
    expect(graph.out[0]).toEqual([1, 2]);
    expect(graph.into[2]).toEqual([0, 1]);
  });

  it('цвет по верхней папке, файл в корне без группы', () => {
    const graph = buildGraph(files);
    expect(graph.nodes[0]?.group).toBe(graph.nodes[1]?.group);
    expect(graph.nodes[0]?.group).not.toBe(graph.nodes[2]?.group);
    expect(graph.nodes[3]?.group).toBe(-1);
  });

  it('при превышении лимита остаются самые связанные, прежние позиции сохраняются', () => {
    const graph = buildGraph(files, new Map([['src/a.ts', { x: 5, y: 6 }]]), 3);
    expect(graph.nodes).toHaveLength(3);
    expect(graph.nodes.map((node) => node.path)).not.toContain('README.md');
    expect(graph.nodes.find((node) => node.path === 'src/a.ts')).toMatchObject({ x: 5, y: 6 });
  });

  it('раскладка останавливается и не даёт NaN даже для совпавших узлов', () => {
    const graph = buildGraph(files);
    graph.nodes.forEach((node) => Object.assign(node, { x: 0, y: 0 }));
    const sim = new Simulation(graph);
    for (let step = 0; step < 600 && sim.active; step += 1) sim.tick();
    expect(sim.active).toBe(false);
    expect(graph.nodes.every((node) => Number.isFinite(node.x) && Number.isFinite(node.y))).toBe(true);
  });

  it('2000 узлов раскладываются быстро: шаг до 40 мс', () => {
    const many = Array.from({ length: 2000 }, (_, index) => file(`d${index % 20}/f${index}.ts`, index ? [Math.floor(index / 3)] : undefined));
    const sim = new Simulation(buildGraph(many));
    const start = performance.now();
    for (let step = 0; step < 20; step += 1) sim.tick();
    expect((performance.now() - start) / 20).toBeLessThan(40);
  });

  it('зум держит точку под курсором, вид вписывает узлы', () => {
    const view = zoomAt({ k: 1, x: 10, y: 20 }, 2, 100, 100);
    expect((100 - view.x) / view.k).toBeCloseTo(90);
    expect(zoomAt({ k: 7.9, x: 0, y: 0 }, 5, 0, 0).k).toBe(8);
    const graph = buildGraph(files);
    const fit = fitView(graph.nodes, 400, 300);
    for (const node of graph.nodes) {
      expect(node.x * fit.k + fit.x).toBeGreaterThanOrEqual(0);
      expect(node.x * fit.k + fit.x).toBeLessThanOrEqual(400);
    }
    expect(nodeAt(graph.nodes, graph.nodes[0]!.x, graph.nodes[0]!.y)).toBe(0);
  });
});
