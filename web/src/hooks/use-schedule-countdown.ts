import { formatCountdown } from "@/lib/format";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

/**
 * Countdown to an absolute ISO datetime from the scheduler API
 * (already includes per-subscription jitter).
 *
 * When the target is reached, ``onExpire`` fires once so the caller can
 * fetch the next ``next_run_at`` immediately — never linger on "0后".
 */
export function useScheduleCountdown(
  targetIso: string | null | undefined,
  onExpire?: () => void,
): string {
  const { t, i18n } = useTranslation();
  const nextRun = useMemo(() => {
    if (!targetIso) return null;
    const d = new Date(targetIso);
    return Number.isNaN(d.getTime()) ? null : d;
  }, [targetIso]);
  const [tick, setTick] = useState(0);
  const onExpireRef = useRef(onExpire);
  onExpireRef.current = onExpire;
  const expiredKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!nextRun) return;
    const interval = setInterval(() => {
      setTick((current) => current + 1);
    }, 1000);
    return () => clearInterval(interval);
  }, [nextRun]);

  useEffect(() => {
    if (!nextRun || !targetIso) {
      expiredKeyRef.current = null;
      return;
    }

    const fireExpire = () => {
      if (expiredKeyRef.current === targetIso) return;
      expiredKeyRef.current = targetIso;
      onExpireRef.current?.();
    };

    const ms = nextRun.getTime() - Date.now();
    if (ms <= 0) {
      fireExpire();
      return;
    }

    expiredKeyRef.current = null;
    const timer = window.setTimeout(fireExpire, ms + 50);
    return () => window.clearTimeout(timer);
  }, [nextRun, targetIso]);

  void tick;
  void i18n.language;

  if (!nextRun) return "—";
  const ms = nextRun.getTime() - Date.now();
  if (ms <= 0) return "—";
  return formatCountdown(nextRun, t);
}
