import { Tooltip } from "@heroui/react";
import type { ReactNode } from "react";

type Props = {
  children: ReactNode;
  content: ReactNode;
  className?: string;
  placement?:
    | "top"
    | "top-start"
    | "top-end"
    | "bottom"
    | "bottom-start"
    | "bottom-end";
};

export function HoverHint({
  children,
  content,
  className = "w-full",
  placement = "top",
}: Props) {
  return (
    <Tooltip
      content={
        <div className="max-w-[20rem] text-xs leading-relaxed whitespace-pre-line">
          {content}
        </div>
      }
      placement={placement}
      delay={300}
      closeDelay={0}
      classNames={{ content: "px-3 py-2" }}
    >
      <div className={className}>{children}</div>
    </Tooltip>
  );
}
