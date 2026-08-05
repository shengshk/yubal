import { Tooltip } from "@heroui/react";
import { Fragment, type ReactNode } from "react";

export type PlaylistStatItem = {
  label: string;
  value: number | string;
  /** Optional tone for the whole segment (e.g. warning for readonly). */
  className?: string;
  /** Existing tooltip surface for compact pending/error breakdowns. */
  detail?: string;
};

type Props = {
  items: PlaylistStatItem[];
  /** Extra segments after the stats (e.g. missing hint). */
  trailing?: ReactNode;
  className?: string;
};

/** Shared compact stats row with uniform separator spacing. */
export function PlaylistStatsLine({ items, trailing, className }: Props) {
  return (
    <span
      className={`inline-flex max-w-full flex-nowrap items-baseline gap-x-0 overflow-hidden whitespace-nowrap align-bottom ${className ?? ""}`}
    >
      {items.map((item, i) => (
        <Fragment key={`${item.label}-${i}`}>
          {i > 0 ? (
            <span className="text-foreground-400 px-2" aria-hidden>
              ·
            </span>
          ) : null}
          {item.detail ? (
            <Tooltip content={item.detail}>
              <span
                className={`inline-flex shrink-0 items-baseline gap-0.5 whitespace-nowrap ${item.className ?? ""}`}
              >
                <span>{item.label}</span>
                <span className="tabular-nums">
                  {item.value}
                </span>
              </span>
            </Tooltip>
          ) : (
            <span
              className={`inline-flex shrink-0 items-baseline gap-0.5 whitespace-nowrap ${item.className ?? ""}`}
            >
              <span>{item.label}</span>
              <span className="tabular-nums">
                {item.value}
              </span>
            </span>
          )}
        </Fragment>
      ))}
      {trailing}
    </span>
  );
}
