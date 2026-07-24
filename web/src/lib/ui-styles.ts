/** Shared surface styles — match HeroUI Card (bg-content1 + shadow-sm, no border). */

/**
 * Home page vertical rhythm (Tailwind spacing):
 *
 *   pageY        — main padding top/bottom
 *   blockGap     — between major modules (search / direct / subs / logs)
 *   blockMargin  — chrome blocks above the module stack (stats → search → stack)
 *   sectionInner — title → cards, and card → card inside one module
 *   sectionTitle — label above a module's cards
 *
 * Do not invent one-off mb/gap values for these roles.
 */
export const layout = {
  pageY: "py-6",
  blockGap: "gap-6",
  blockMargin: "mb-6",
  sectionInner: "gap-3",
  sectionTitle: "text-foreground-500 text-sm",
} as const;

/** Same elevation as top stats / ledger cards. */
export const cardShadow = "sm" as const;

/**
 * Input chrome that looks like a card, not a bordered field.
 * No visible border in idle, hover, focus, or invalid — only elevation + fill.
 */
export const cardInputWrapper = [
  "bg-content1",
  "shadow-sm",
  "!border-0",
  "!outline-none",
  "!ring-0",
  "border-transparent",
  "data-[hover=true]:!border-transparent",
  "data-[hover=true]:!bg-content1",
  "group-data-[focus=true]:!border-transparent",
  "group-data-[focus=true]:!bg-content1",
  "group-data-[focus=true]:!outline-none",
  "group-data-[focus=true]:!ring-0",
  "group-data-[focus=true]:data-[hover=true]:!border-transparent",
  "group-data-[invalid=true]:!border-transparent",
  "group-data-[invalid=true]:!bg-content1",
  "group-data-[invalid=true]:data-[hover=true]:!border-transparent",
  "group-data-[invalid=true]:group-data-[focus=true]:!border-transparent",
].join(" ");

/** Action card: same fill/text as stats cards; never use isDisabled (it adds opacity). */
export const cardActionClass = [
  "!opacity-100",
  "bg-content1",
  "text-foreground",
  "shadow-sm",
  "h-10",
  "min-w-fit",
  "data-[hover=true]:!opacity-100",
  "data-[hover=true]:text-primary",
  "data-[pressed=true]:text-primary",
  "data-[disabled=true]:!opacity-100",
].join(" ");
