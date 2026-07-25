export const LIKED_MUSIC_PLAYLIST_ID = "LM";
export const LIKED_MUSIC_SAVE_FOLDER = "liked";

export function isLikedMusicUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    return new URL(url).searchParams.get("list") === LIKED_MUSIC_PLAYLIST_ID;
  } catch {
    return /(?:[?&])list=LM(?:&|$)/.test(url);
  }
}
