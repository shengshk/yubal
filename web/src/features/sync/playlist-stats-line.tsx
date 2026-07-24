import { Fragment, type ReactNode } from "react";

export type PlaylistStatItem = {
  label: string;
  value: number | string;
  /** Optional tone for the whole segment (e.g. warning for readonly). */
  className?: string;
};

type Props = {
  items: PlaylistStatItem[];
  /** Extra segments after the stats (e.g. missing hint). */
  trailing?: ReactNode;
  className?: string;
};

/**
 * Stats row with tabular numbers so 1 vs 10 does not shift following labels.
 * Each value gets a fixed digit slot (up to 3 digits) for column-like alignment.
 */
export function PlaylistStatsLine({ items, trailing, className }: Props) {
  return (
    <span
      className={`inline-flex flex-wrap items-baseline gap-x-0 ${className ?? ""}`}
    >
      {items.map((item, i) => (
        <Fragment key={`${item.label}-${i}`}>
          {i > 0 ? (
            <span className="text-foreground-400 px-1" aria-hidden>
              ·
            </span>
          ) : null}
          <span
            className={`inline-flex items-baseline gap-0.5 whitespace-nowrap ${item.className ?? ""}`}
          >
            <span>{item.label}</span>
            {/* Width reserved on the right so digits sit next to the label, not the · */}
            <span className="inline-block min-w-[3ch] text-left tabular-nums">
              {item.value}
            </span>
          </span>
        </Fragment>
      ))}
      {trailing}
    </span>
  );
}
