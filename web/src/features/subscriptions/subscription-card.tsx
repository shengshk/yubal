import { cardShadow } from "@/lib/ui-styles";
import { Card, CardBody, cn } from "@heroui/react";
import { ComponentProps, ReactNode } from "react";

type RootProps = ComponentProps<typeof Card>;

function _Root({ children, className, shadow = cardShadow, ...props }: RootProps) {
  return (
    <Card className={className} shadow={shadow} {...props}>
      <CardBody className="flex flex-row items-center justify-between gap-2 p-3 md:p-5">
        {children}
      </CardBody>
    </Card>
  );
}

type HeaderProps = ComponentProps<"div"> & {
  title: string;
};

/** Line 1 + line 2 share the same size; only color differs (grey / black). */
const LINE_SIZE = "text-sm font-medium leading-none";

function _Header({ title, children, className, ...props }: HeaderProps) {
  return (
    <div className={cn("min-w-0", className)} {...props}>
      <p
        className={cn(
          "text-foreground-500 mb-1 truncate",
          LINE_SIZE,
        )}
      >
        {title}
      </p>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

type ValueProps = {
  children: ReactNode;
  suffix?: string;
  className?: string;
};

function _Value({ children, suffix, className }: ValueProps) {
  // One node for the whole second line so digits and labels never diverge.
  return (
    <p
      className={cn(
        "text-foreground truncate",
        LINE_SIZE,
        className,
      )}
    >
      {children}
      {suffix != null && suffix !== "" ? suffix : null}
    </p>
  );
}

type IconProps = ComponentProps<"div">;

function _Icon({ children, className, ...props }: IconProps) {
  return (
    <div
      className={cn(
        "bg-secondary/10 text-secondary hidden h-8 w-8 shrink-0 items-center justify-center rounded-full sm:flex md:h-10 md:w-10 [&>svg]:size-4 md:[&>svg]:size-5",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

type SubscriptionCardComponent = typeof _Root & {
  Header: typeof _Header;
  Value: typeof _Value;
  Icon: typeof _Icon;
};

export const SubscriptionCard: SubscriptionCardComponent = Object.assign(
  _Root,
  {
    Header: _Header,
    Value: _Value,
    Icon: _Icon,
  },
);
