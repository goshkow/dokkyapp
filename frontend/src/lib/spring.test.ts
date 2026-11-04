import { describe, expect, it } from 'vitest';
import { atRest, LEAD, stepEdge, TRAIL } from './spring';

const run = (spring: typeof LEAD, target: number, ms: number) => {
  let edge = { x: 0, v: 0 };
  let peak = 0;
  for (let t = 0; t < ms; t += 16) {
    edge = stepEdge(edge, target, spring, 0.016);
    peak = Math.max(peak, edge.x);
  }
  return { edge, peak };
};

describe('stepEdge', () => {
  it('приходит к цели и останавливается', () => {
    const { edge } = run(LEAD, 100, 1000);
    expect(atRest(edge, 100)).toBe(true);
  });

  it('лёгкий перелёт есть, но небольшой', () => {
    const { peak } = run(LEAD, 100, 1000);
    expect(peak).toBeGreaterThan(100);
    expect(peak).toBeLessThan(112);
  });

  it('передний край уходит вперёд заднего: капсула вытягивается', () => {
    expect(run(LEAD, 100, 64).edge.x).toBeGreaterThan(run(TRAIL, 100, 64).edge.x);
  });

  it('долгий кадр не разносит пружину', () => {
    const edge = stepEdge({ x: 0, v: 0 }, 100, LEAD, 5);
    expect(Math.abs(edge.x)).toBeLessThan(200);
  });
});
