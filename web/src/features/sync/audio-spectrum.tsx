import { useSpectrumLevels } from "@/features/sync/library-audio";
import { useEffect, useRef, useState } from "react";

const MIN_BARS = 12;
const MAX_BARS = 64;
const BAR_GAP_PX = 2;

function sampleLevel(
  spectrum: number[],
  barIndex: number,
  barCount: number,
): number {
  if (spectrum.length === 0 || barCount <= 0) return 0;
  const pos = ((barIndex + 0.5) / barCount) * spectrum.length;
  const idx = Math.floor(pos);
  const frac = pos - idx;
  const a = spectrum[idx] ?? 0;
  const b = spectrum[Math.min(idx + 1, spectrum.length - 1)] ?? 0;
  return a + (b - a) * frac;
}

export function AudioSpectrum() {
  const spectrum = useSpectrumLevels();
  const rootRef = useRef<HTMLDivElement>(null);
  const [barCount, setBarCount] = useState(MIN_BARS);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    const update = () => {
      const width = root.clientWidth;
      const count = Math.max(
        MIN_BARS,
        Math.min(MAX_BARS, Math.floor(width / (3 + BAR_GAP_PX))),
      );
      setBarCount(count);
    };

    update();
    const observer = new ResizeObserver(update);
    observer.observe(root);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={rootRef}
      className="pointer-events-none absolute inset-0 overflow-hidden"
      aria-hidden
    >
      <div className="flex h-full w-full items-end gap-[2px] opacity-[0.16]">
        {Array.from({ length: barCount }, (_, i) => {
          const level = sampleLevel(spectrum, i, barCount);
          const height = Math.max(3, level * 100);
          return (
            <span
              key={i}
              className="bg-primary flex-1 rounded-t-sm"
              style={{ height: `${height}%` }}
            />
          );
        })}
      </div>
    </div>
  );
}
