export function formatTimeAgo(
  dateString: string | null | undefined,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  if (!dateString) return t("time.never");
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);

  if (diffMins < 1) return t("time.justNow");
  if (diffMins < 60) return t("time.minutesAgo", { count: diffMins });
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return t("time.hoursAgo", { count: diffHours });
  const diffDays = Math.floor(diffHours / 24);
  return t("time.daysAgo", { count: diffDays });
}

export function formatDateTime(dateString: string | null | undefined): string {
  if (!dateString) return "";
  return new Date(dateString).toLocaleString();
}

/** Same calendar day → time only; otherwise date + time. */
export function formatSmartDateTime(
  dateString: string | null | undefined,
): string {
  if (!dateString) return "—";
  const date = new Date(dateString);
  if (Number.isNaN(date.getTime())) return "—";
  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  if (sameDay) {
    return date.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function formatCountdown(
  targetDate: Date | null,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  if (!targetDate) return "—";

  const ms = targetDate.getTime() - Date.now();

  // Expired — callers should refresh for the next target; never show "0后".
  if (ms <= 0) return "—";

  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86400);

  if (days >= 1) {
    return t("time.day", { count: days });
  }

  const hours = Math.floor(totalSeconds / 3600);
  const mins = Math.floor((totalSeconds % 3600) / 60);
  const secs = totalSeconds % 60;

  const pad = (n: number) => n.toString().padStart(2, "0");

  if (hours > 0) {
    return `${hours}:${pad(mins)}:${pad(secs)}`;
  }
  return `${mins}:${pad(secs)}`;
}
