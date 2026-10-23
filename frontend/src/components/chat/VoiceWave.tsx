import { useEffect, useRef } from 'react';
import { voiceLevels } from '../../state/voice';

const BARS = 36;

/** Волна записи: полоски следят за уровнем звука с микрофона, обновляются напрямую без перерисовки React. */
export function VoiceWave({ levels: source = voiceLevels }: { levels?: (count: number) => number[] }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let frame = 0;
    const smooth = new Array<number>(BARS).fill(0);
    const tick = () => {
      const levels = source(BARS);
      const bars = box.current?.children;
      if (bars) {
        for (let i = 0; i < BARS; i += 1) {
          smooth[i] = Math.max(levels[i] ?? 0, (smooth[i] ?? 0) * 0.82);
          (bars[i] as HTMLElement).style.transform = `scaleY(${(0.12 + smooth[i]! * 0.88).toFixed(3)})`;
        }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [source]);
  return (
    <div className="voice-wave" ref={box} role="img" aria-label="Идёт запись">
      {Array.from({ length: BARS }, (_value, index) => <i key={index} />)}
    </div>
  );
}
