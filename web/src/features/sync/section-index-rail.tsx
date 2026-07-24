import {
  INDEX_LETTERS,
  type IndexLetter,
} from "@/features/sync/track-index";
import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";

type Props = {
  letters: IndexLetter[];
  /** Letters whose sections currently intersect the list viewport. */
  inViewLetters?: IndexLetter[];
  onJump: (letter: IndexLetter) => void;
};

const LETTER_SLOT_PCT = 3.2;

/** Contacts-style vertical A–Z scrubber for the track list viewport. */
export function SectionIndexRail({
  letters,
  inViewLetters = [],
  onJump,
}: Props) {
  const active = useRef(new Set(letters));
  active.current = new Set(letters);
  const [hint, setHint] = useState<IndexLetter | null>(null);
  const inView = useMemo(() => new Set(inViewLetters), [inViewLetters]);

  /** One continuous band covering first…last in-view letter (inclusive). */
  const inViewBand = useMemo(() => {
    if (inViewLetters.length === 0) return null;
    const indexes = inViewLetters
      .map((letter) => INDEX_LETTERS.indexOf(letter))
      .filter((index) => index >= 0);
    if (indexes.length === 0) return null;
    const first = Math.min(...indexes);
    const last = Math.max(...indexes);
    const stackPct = INDEX_LETTERS.length * LETTER_SLOT_PCT;
    const padPct = (100 - stackPct) / 2;
    return {
      top: `${padPct + first * LETTER_SLOT_PCT}%`,
      height: `${(last - first + 1) * LETTER_SLOT_PCT}%`,
    };
  }, [inViewLetters]);

  const letterFromPoint = useCallback(
    (clientX: number, clientY: number): IndexLetter | null => {
      const el = document.elementFromPoint(clientX, clientY);
      const btn = el?.closest<HTMLElement>("[data-index-letter]");
      const raw = btn?.dataset.indexLetter;
      if (!raw) return null;
      const letter = raw as IndexLetter;
      if (active.current.has(letter)) return letter;
      const wantIdx = INDEX_LETTERS.indexOf(letter);
      let best: IndexLetter | null = null;
      let bestDist = Infinity;
      for (const item of active.current) {
        const dist = Math.abs(INDEX_LETTERS.indexOf(item) - wantIdx);
        if (dist < bestDist) {
          bestDist = dist;
          best = item;
        }
      }
      return best;
    },
    [],
  );

  const onPointer = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.type === "pointermove" && event.buttons === 0) {
      setHint(letterFromPoint(event.clientX, event.clientY));
      return;
    }
    if (event.type === "pointerdown") {
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
    }
    const letter = letterFromPoint(event.clientX, event.clientY);
    setHint(letter);
    if (letter) onJump(letter);
  };

  if (letters.length === 0) return null;

  return (
    <div
      className="pointer-events-auto absolute top-1 right-0 bottom-1 z-30 flex w-5 select-none flex-col items-center justify-center"
      role="navigation"
      aria-label="A–Z"
      onPointerDown={onPointer}
      onPointerMove={onPointer}
      onPointerLeave={() => setHint(null)}
      onPointerUp={() => setHint(null)}
      onPointerCancel={() => setHint(null)}
    >
      {inViewBand ? (
        <div
          className="bg-default-300/55 pointer-events-none absolute inset-x-0 rounded-full transition-[top,height] duration-150"
          style={{ top: inViewBand.top, height: inViewBand.height }}
          aria-hidden
        />
      ) : null}
      {INDEX_LETTERS.map((letter) => {
        const enabled = active.current.has(letter);
        const activeHint = hint === letter;
        const visible = inView.has(letter);
        return (
          <button
            key={letter}
            type="button"
            tabIndex={-1}
            disabled={!enabled}
            data-index-letter={letter}
            title={enabled ? letter : undefined}
            aria-label={enabled ? letter : undefined}
            aria-current={visible ? "true" : undefined}
            className={`relative z-10 flex h-[3.2%] min-h-0 w-full items-center justify-center text-[9px] leading-none transition-colors ${
              !enabled
                ? "text-foreground-300 cursor-default opacity-35"
                : activeHint
                  ? "bg-primary/20 text-primary cursor-pointer rounded-sm font-semibold"
                  : "text-primary cursor-pointer font-medium"
            }`}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              if (enabled) onJump(letter);
            }}
          >
            {letter}
          </button>
        );
      })}
    </div>
  );
}
