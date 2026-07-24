// Must match backend validation in packages/api/src/yubal_api/schemas/jobs.py
// and core validation in packages/yubal/src/yubal/utils/url.py
export const YOUTUBE_URL_PATTERN =
  /^https?:\/\/(music\.youtube\.com\/(playlist\?list=|browse\/|watch\?v=)|(?:www\.|m\.)?youtube\.com\/(playlist\?list=|watch\?v=|shorts\/|live\/|embed\/|e\/|v\/|vi\/)|youtu\.be\/|(?:www\.)?youtube-nocookie\.com\/embed\/)[\w-]+/;

export function isValidUrl(url: string): boolean {
  return YOUTUBE_URL_PATTERN.test(url);
}

export type UnifiedInputKind =
  | "empty"
  | "ytm_url"
  | "text"
  | "invalid_url"
  | "invalid_text";

export function classifyUnifiedInput(value: string): UnifiedInputKind {
  const normalized = value.trim();
  if (!normalized) return "empty";
  if (isValidUrl(normalized)) return "ytm_url";
  if (
    /^[a-z][a-z\d+.-]*:\/\//i.test(normalized) ||
    /^(?:www\.)?[\w-]+(?:\.[\w-]+)+(?:[/:?#]|$)/i.test(normalized)
  ) {
    return "invalid_url";
  }
  if (
    normalized.length > 200 ||
    Array.from(normalized).some((char) => char.charCodeAt(0) < 32)
  ) {
    return "invalid_text";
  }
  return "text";
}
