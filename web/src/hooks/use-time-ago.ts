import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { formatTimeAgo } from "@/lib/format";

const INTERVAL_MS = 1000;

export function useTimeAgo(dateString: string | null | undefined): string {
  const { t, i18n } = useTranslation();
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!dateString) return;

    const interval = setInterval(() => {
      setTick((tick) => tick + 1);
    }, INTERVAL_MS);

    return () => clearInterval(interval);
  }, [dateString]);

  // Recompute when language changes
  void i18n.language;

  return formatTimeAgo(dateString, t);
}
