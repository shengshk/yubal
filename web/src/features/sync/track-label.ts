/** Fixed playing / playlist label: `Artist - Title` (never album-artist prefix). */
export function formatArtistTitle(
  artist: string | null | undefined,
  title: string,
): string {
  const trimmed = title.trim();
  const name = artist?.trim();
  return name ? `${name} - ${trimmed}` : trimmed;
}
