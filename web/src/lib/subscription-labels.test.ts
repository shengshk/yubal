import { describe, expect, it } from "bun:test";
import { isLikedMusicUrl } from "./subscription-labels";

describe("isLikedMusicUrl", () => {
  it("recognizes the account Liked Music playlist", () => {
    expect(isLikedMusicUrl("https://music.youtube.com/playlist?list=LM")).toBe(
      true,
    );
  });

  it("does not classify normal playlists as Liked Music", () => {
    expect(
      isLikedMusicUrl("https://music.youtube.com/playlist?list=PL123"),
    ).toBe(false);
  });
});
