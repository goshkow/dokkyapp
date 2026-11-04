/** Край капсулы на пружине: положение и скорость, шаг считается сам, без кривых анимации. */
export interface SpringEdge { x: number; v: number }

/* передний край быстрее заднего: на ходу капсула вытягивается и догоняет себя. Затухание ниже критического даёт лёгкий перелёт */
export const LEAD = { stiffness: 620, damping: 34 };
export const TRAIL = { stiffness: 340, damping: 27 };
const STEP = 1 / 240;
const REST = 0.05;

/** Полушаговый метод Эйлера мелкими шагами: не разносит при рваных кадрах. */
export function stepEdge(edge: SpringEdge, target: number, { stiffness, damping }: { stiffness: number; damping: number }, seconds: number): SpringEdge {
  let { x, v } = edge;
  let left = Math.min(seconds, 0.05);
  while (left > 0) {
    const dt = Math.min(STEP, left);
    v += (stiffness * (target - x) - damping * v) * dt;
    x += v * dt;
    left -= dt;
  }
  return { x, v };
}

export const atRest = (edge: SpringEdge, target: number): boolean => Math.abs(edge.x - target) < REST && Math.abs(edge.v) < REST * 20;
