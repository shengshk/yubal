import type { SyncTrackItem } from "@/api/sync-ledger";
import {
  fetchTrackLyrics,
  resolveTrackMetadata,
  searchTrackMetadata,
  updateTrackTags,
  type MetadataCandidate,
  type MetadataSuggestion,
  type TrackTagUpdate,
} from "@/api/library";
import {
  useLibraryAudio,
  type LyricsChangedDetail,
} from "@/features/sync/library-audio";
import { parseLyrics } from "@/features/sync/lrc";
import {
  Button,
  Checkbox,
  Input,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Spinner,
} from "@heroui/react";
import { PencilIcon, SearchIcon, UploadIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

type Props = {
  track: SyncTrackItem | null;
  saveFolder: string;
  isOpen: boolean;
  busy?: boolean;
  /** Readonly External: hide text-tag editors; only lyrics + cover. */
  readOnlyTags?: boolean;
  /** Override library stream path (External Organized/… or Raw/…). */
  streamPath?: string;
  onClose: () => void;
  onSaved: (result: {
    video_id: string;
    locations: Array<{
      save_folder: string;
      old_relative_path: string;
      new_relative_path: string;
    }>;
  }) => void;
};

type FieldKey =
  | "title"
  | "artist"
  | "albumArtist"
  | "album"
  | "year"
  | "trackNumber";

type ApplyMode = "all" | "empty" | "custom";

const TEXT_FIELDS: readonly FieldKey[] = [
  "title",
  "artist",
  "albumArtist",
  "album",
  "year",
  "trackNumber",
];

function defaultQuery(track: SyncTrackItem): string {
  return [track.artist, track.title].filter(Boolean).join(" ").trim();
}

function isBlank(value: string): boolean {
  return !value.trim();
}

function buildTrackKey(saveFolder: string, relativePath: string): string {
  return `${saveFolder}/${relativePath}`.replace(/\/+/g, "/");
}

function coverSource(url: string | null): string {
  if (!url) return "—";
  if (url.startsWith("data:")) return "Local";
  try {
    const host = new URL(url).hostname.toLowerCase();
    if (host.includes("mzstatic") || host.includes("apple")) return "Apple";
    if (host.includes("googleusercontent") || host.includes("ytimg")) {
      return "YouTube Music";
    }
    return host.replace(/^www\./, "");
  } catch {
    return "—";
  }
}

/** Provenance of the *embedded* cover, from the catalog (not the URL guess). */
function coverSourceLabel(source: string | null | undefined): string | null {
  switch (source) {
    case "apple":
      return "Apple";
    case "ytm":
      return "YouTube Music";
    case "embedded":
      return "Embedded";
    default:
      return null;
  }
}

function plainLyrics(content: string): string {
  return parseLyrics(content)
    .lines.map((line) =>
      line.text.replace(/<\d{1,2}:\d{2}(?:\.\d{1,3})?>/g, "").trim(),
    )
    .filter(Boolean)
    .join("\n");
}

export function TrackEditModal({
  track,
  saveFolder,
  isOpen,
  busy = false,
  readOnlyTags = false,
  streamPath,
  onClose,
  onSaved,
}: Props) {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [title, setTitle] = useState("");
  const [artist, setArtist] = useState("");
  const [albumArtist, setAlbumArtist] = useState("");
  const [album, setAlbum] = useState("");
  const [year, setYear] = useState("");
  const [trackNumber, setTrackNumber] = useState("");

  // Cover module (independent of lyrics).
  const [coverUrl, setCoverUrl] = useState<string | null>(null);
  const [coverChanged, setCoverChanged] = useState(false);
  const [applyCover, setApplyCover] = useState(false);

  // Lyrics module (independent of cover).
  const [lyrics, setLyrics] = useState<string | null>(null);
  const [lyricsSource, setLyricsSource] = useState<string | null>(null);
  const [lyricsChanged, setLyricsChanged] = useState(false);
  const [applyLyrics, setApplyLyrics] = useState(false);

  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<MetadataCandidate[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState<MetadataSuggestion | null>(null);
  const [searching, setSearching] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [applyMode, setApplyMode] = useState<ApplyMode>("all");
  const [selectedFields, setSelectedFields] = useState<Set<FieldKey>>(
    () => new Set(),
  );

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trackKey =
    streamPath?.trim() ||
    (track && track.relative_path
      ? buildTrackKey(saveFolder, track.relative_path)
      : null);

  const baseline = useMemo(() => {
    if (!track) {
      return {
        title: "",
        artist: "",
        albumArtist: "",
        album: "",
        year: "",
        trackNumber: "",
        coverUrl: null as string | null,
      };
    }
    return {
      title: track.title ?? "",
      artist: track.artist ?? "",
      albumArtist: track.album_artist ?? track.artist ?? "",
      album: track.album ?? "",
      year: track.year ?? "",
      trackNumber: track.track_number != null ? String(track.track_number) : "",
      coverUrl: track.cover_url ?? null,
    };
  }, [track]);

  const labelWithChange = (label: string, changed: boolean) => (
    <span>
      {label}
      {changed ? (
        <span className="text-primary"> · {t("sync.trackAssetModified")}</span>
      ) : null}
    </span>
  );

  const sourceLabel = (source: string | null): string => {
    if (!source) return "—";
    const known: Record<string, string> = {
      manual: t("sync.trackSourceManual"),
      db: t("sync.trackSourceCatalog"),
      sidecar: ".lrc",
      embedded: t("sync.trackSourceEmbedded"),
      lrclib: "LRCLIB",
      ytm: "YouTube Music",
      qq: t("sync.trackSourceQQ"),
    };
    return known[source.toLowerCase()] ?? source;
  };

  // Reset the form when opening. No auto-search: the user searches manually.
  useEffect(() => {
    if (!track || !isOpen) return;
    setTitle(baseline.title);
    setArtist(baseline.artist);
    setAlbumArtist(baseline.albumArtist);
    setAlbum(baseline.album);
    setYear(baseline.year);
    setTrackNumber(baseline.trackNumber);
    setCoverUrl(baseline.coverUrl);
    setCoverChanged(false);
    setApplyCover(false);
    setLyrics(null);
    setLyricsSource(null);
    setLyricsChanged(false);
    setApplyLyrics(false);
    setQuery(defaultQuery(track));
    setCandidates([]);
    setSelectedId(null);
    setSuggestion(null);
    setApplyMode("all");
    setSelectedFields(new Set());
    setSearching(false);
    setResolving(false);
    setError(null);
  }, [track, isOpen, baseline]);

  // Load current lyrics for preview only. Reading never changes files.
  useEffect(() => {
    if (!isOpen || !trackKey) return;
    let cancelled = false;
    void fetchTrackLyrics(trackKey).then((result) => {
      if (cancelled) return;
      setLyrics(result.content);
      setLyricsSource(result.source ?? null);
      setLyricsChanged(false);
      setApplyLyrics(false);
    });
    return () => {
      cancelled = true;
    };
  }, [isOpen, trackKey]);

  // Global lyrics sync: draft from the nested editor (preview only — no disk).
  useEffect(() => {
    if (!isOpen || !trackKey) return;
    const onDraft = (event: Event) => {
      const detail = (event as CustomEvent<LyricsChangedDetail>).detail;
      if (!detail || detail.key !== trackKey) return;
      setLyrics(detail.content);
      setLyricsSource("manual");
      setLyricsChanged(true);
      setApplyLyrics(true);
    };
    window.addEventListener("yubal:lyrics-draft", onDraft);
    return () => window.removeEventListener("yubal:lyrics-draft", onDraft);
  }, [isOpen, trackKey]);

  const applyTextFields = (s: MetadataSuggestion, mode: ApplyMode) => {
    const pick = (key: FieldKey, suggested: string, current: string) => {
      if (mode === "all") return suggested || current;
      if (mode === "empty")
        return isBlank(current) ? suggested || current : current;
      return selectedFields.has(key) ? suggested || current : current;
    };
    setTitle(pick("title", s.title, baseline.title));
    setArtist(pick("artist", s.artist, baseline.artist));
    setAlbumArtist(pick("albumArtist", s.album_artist, baseline.albumArtist));
    setAlbum(pick("album", s.album, baseline.album));
    setYear(pick("year", s.year ?? "", baseline.year));
    setTrackNumber(
      pick(
        "trackNumber",
        s.track_number != null ? String(s.track_number) : "",
        baseline.trackNumber,
      ),
    );
  };

  useEffect(() => {
    if (!suggestion) return;
    applyTextFields(suggestion, applyMode);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- resync on mode/suggestion change
  }, [suggestion, applyMode, selectedFields]);

  const toggleField = (key: FieldKey) => {
    setApplyMode("custom");
    setSelectedFields((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const handleSearch = async () => {
    if (!track?.video_id || searching) return;
    setSearching(true);
    setError(null);
    setSuggestion(null);
    setSelectedId(null);
    const result = await searchTrackMetadata(track.video_id, query);
    setSearching(false);
    if ("error" in result) {
      setError(result.error || t("sync.trackScrapeSearchFailed"));
      setCandidates([]);
      return;
    }
    setQuery(result.query);
    setCandidates(result.candidates);
    if (result.candidates.length === 0) {
      setError(t("sync.trackScrapeNoResults"));
    }
  };

  const handleSelectCandidate = async (candidate: MetadataCandidate) => {
    if (!track?.video_id || resolving) return;
    setSelectedId(candidate.candidate_video_id);
    setResolving(true);
    setError(null);
    const result = await resolveTrackMetadata(
      track.video_id,
      candidate.candidate_video_id,
      true,
    );
    setResolving(false);
    if ("error" in result) {
      setError(result.error || t("sync.trackScrapeResolveFailed"));
      setSuggestion(null);
      return;
    }
    setSuggestion(result);
    if (applyMode === "custom" && selectedFields.size === 0) {
      setSelectedFields(new Set<FieldKey>(TEXT_FIELDS));
    }
    // Cover / lyrics are independent modules — seed their own apply toggles.
    if (result.cover_url) {
      setCoverUrl(result.cover_url);
      setCoverChanged(true);
      setApplyCover(true);
    }
    if (result.lyrics) {
      setLyrics(result.lyrics);
      setLyricsSource(result.lyrics_source);
      setLyricsChanged(true);
      setApplyLyrics(true);
    }
  };

  const handleCoverUpload = (file: File | null) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = reader.result;
      if (typeof dataUrl !== "string") return;
      setCoverUrl(dataUrl);
      setCoverChanged(true);
      setApplyCover(true);
    };
    reader.readAsDataURL(file);
  };

  const openLyricsEditor = () => {
    if (!trackKey) return;
    // Unmatched External has no video_id — write lyrics to the file path
    // directly. Matched tracks draft into this modal and flush on Save.
    const opts = {
      folder: saveFolder,
      previewOnly: Boolean(track?.video_id),
    };
    // Prefer scraped/edited lyrics; otherwise load whatever is on disk today.
    if (lyrics != null) {
      audio.openLyricsEditorFor(trackKey, lyrics, opts);
      return;
    }
    void fetchTrackLyrics(trackKey).then((res) => {
      audio.openLyricsEditorFor(trackKey, res.content ?? "", opts);
    });
  };

  const handleSave = async () => {
    if (saving) return;
    if (!track?.video_id) {
      // Cover still goes through catalog retag; lyrics for unmatched files
      // are saved directly from the lyrics editor.
      if (applyCover && coverChanged) {
        setError(t("sync.editAfterMatch"));
      } else {
        onClose();
      }
      return;
    }
    const payload: TrackTagUpdate = {};
    if (!readOnlyTags) {
      const trimmedTitle = title.trim();
      const trimmedArtist = artist.trim();
      const trimmedAlbumArtist = albumArtist.trim();
      const trimmedAlbum = album.trim();
      const trimmedYear = year.trim();
      if (
        !trimmedTitle ||
        !trimmedArtist ||
        !trimmedAlbumArtist ||
        !trimmedAlbum
      ) {
        setError(t("sync.trackEditRequired"));
        return;
      }
      if (trimmedTitle !== (track.title ?? "")) payload.title = trimmedTitle;
      if (trimmedArtist !== (track.artist ?? "")) payload.artist = trimmedArtist;
      if (trimmedAlbumArtist !== (track.album_artist ?? track.artist ?? "")) {
        payload.album_artist = trimmedAlbumArtist;
      }
      if (trimmedAlbum !== (track.album ?? "")) payload.album = trimmedAlbum;
      if (trimmedYear !== (track.year ?? "")) {
        payload.year = trimmedYear || null;
      }
      const parsedTrackNo = trackNumber.trim()
        ? parseInt(trackNumber.trim(), 10)
        : null;
      const currentNo = track.track_number ?? null;
      if (parsedTrackNo !== currentNo) {
        if (
          parsedTrackNo != null &&
          (!Number.isFinite(parsedTrackNo) || parsedTrackNo < 1)
        ) {
          setError(t("sync.trackEditInvalidTrackNumber"));
          return;
        }
        payload.track_number = parsedTrackNo;
      }
    }

    if (applyCover && coverUrl && coverChanged) {
      payload.cover_url = coverUrl;
      payload.refresh_cover = true;
    }
    if (applyLyrics && lyrics) {
      payload.lyrics = lyrics;
    }

    if (Object.keys(payload).length === 0) {
      onClose();
      return;
    }
    setSaving(true);
    setError(null);
    const result = await updateTrackTags(track.video_id, payload);
    setSaving(false);
    if (!result) {
      setError(t("sync.trackEditFailed"));
      return;
    }
    // Keep every lyrics view in sync (e.g. the currently-playing display).
    if (payload.lyrics && trackKey) {
      window.dispatchEvent(
        new CustomEvent<LyricsChangedDetail>("yubal:lyrics-changed", {
          detail: { key: trackKey, content: payload.lyrics },
        }),
      );
    }
    onSaved(result);
    onClose();
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      placement="center"
      size="2xl"
      scrollBehavior="inside"
      classNames={{
        closeButton:
          "border-none outline-none ring-0 shadow-none hover:bg-default-100 data-[focus-visible=true]:outline-none data-[focus-visible=true]:ring-0",
      }}
    >
      <ModalContent>
        <ModalHeader>
          {readOnlyTags ? t("sync.editTrackAssets") : t("sync.editTrackTags")}
        </ModalHeader>
        <ModalBody className="gap-4">
          {!readOnlyTags ? (
          <div className="flex flex-col gap-2">
            <p className="text-foreground-500 text-xs font-medium">
              {t("sync.trackScrapeSection")}
            </p>
            <div className="flex gap-2">
              <Input
                aria-label={t("sync.trackScrapeQuery")}
                placeholder={t("sync.trackScrapeQuery")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void handleSearch();
                  }
                }}
              />
              <Button
                color="primary"
                variant="flat"
                isIconOnly
                isLoading={searching}
                aria-label={t("sync.trackScrapeSearch")}
                onPress={() => {
                  void handleSearch();
                }}
              >
                <SearchIcon className="h-4 w-4" />
              </Button>
            </div>

            {candidates.length > 0 ? (
              <ul className="border-default-200 max-h-40 overflow-y-auto rounded-md border">
                {candidates.map((c) => {
                  const active = selectedId === c.candidate_video_id;
                  const score =
                    c.title_score != null && c.artist_score != null
                      ? Math.round((c.title_score + c.artist_score) / 2)
                      : null;
                  return (
                    <li key={c.candidate_video_id}>
                      <button
                        type="button"
                        className={`hover:bg-default-100 flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs ${
                          active ? "bg-primary/10" : ""
                        }`}
                        onClick={() => {
                          void handleSelectCandidate(c);
                        }}
                      >
                        {c.thumbnail_url ? (
                          <img
                            src={c.thumbnail_url}
                            alt=""
                            className="h-8 w-8 shrink-0 rounded object-cover"
                          />
                        ) : (
                          <div className="bg-default-100 h-8 w-8 shrink-0 rounded" />
                        )}
                        <span className="min-w-0 flex-1 truncate">
                          {c.artist} - {c.title}
                          {c.album ? (
                            <span className="text-foreground-400">
                              {" · "}
                              {c.album}
                            </span>
                          ) : null}
                        </span>
                        {score != null ? (
                          <span className="text-foreground-400 shrink-0 tabular-nums">
                            {score}%
                          </span>
                        ) : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : null}
            {resolving ? (
              <div className="text-foreground-400 flex items-center gap-2 text-xs">
                <Spinner size="sm" />
                {t("sync.trackScrapeResolving")}
              </div>
            ) : null}
          </div>
          ) : null}

          {!readOnlyTags && suggestion ? (
            <div className="flex flex-col gap-2">
              <p className="text-foreground-500 text-xs font-medium">
                {t("sync.trackScrapeApplyMode")}
              </p>
              <div className="flex flex-wrap gap-3 text-xs">
                {(
                  [
                    ["all", "trackScrapeApplyAll"],
                    ["empty", "trackScrapeApplyEmpty"],
                    ["custom", "trackScrapeApplyCustom"],
                  ] as const
                ).map(([mode, key]) => (
                  <label key={mode} className="flex items-center gap-1.5">
                    <input
                      type="radio"
                      name="apply-mode"
                      checked={applyMode === mode}
                      onChange={() => setApplyMode(mode)}
                    />
                    {t(`sync.${key}`)}
                  </label>
                ))}
              </div>
              {applyMode === "custom" ? (
                <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
                  {(
                    [
                      ["title", "trackFieldTitle"],
                      ["artist", "trackFieldArtist"],
                      ["albumArtist", "trackFieldAlbumArtist"],
                      ["album", "trackFieldAlbum"],
                      ["year", "trackFieldYear"],
                      ["trackNumber", "trackFieldTrackNumber"],
                    ] as const
                  ).map(([key, label]) => (
                    <Checkbox
                      key={key}
                      size="sm"
                      isSelected={selectedFields.has(key)}
                      onValueChange={() => toggleField(key)}
                    >
                      {t(`sync.${label}`)}
                    </Checkbox>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          {!readOnlyTags ? (
          <div className="grid gap-3">
            <Input
              label={labelWithChange(
                t("sync.trackFieldTitle"),
                title !== baseline.title,
              )}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
            <Input
              label={labelWithChange(
                t("sync.trackFieldArtist"),
                artist !== baseline.artist,
              )}
              value={artist}
              onChange={(e) => setArtist(e.target.value)}
            />
            <Input
              label={labelWithChange(
                t("sync.trackFieldAlbumArtist"),
                albumArtist !== baseline.albumArtist,
              )}
              value={albumArtist}
              onChange={(e) => setAlbumArtist(e.target.value)}
            />
            <Input
              label={labelWithChange(
                t("sync.trackFieldAlbum"),
                album !== baseline.album,
              )}
              value={album}
              onChange={(e) => setAlbum(e.target.value)}
            />
            <div className="grid grid-cols-2 gap-3">
              <Input
                label={labelWithChange(
                  t("sync.trackFieldYear"),
                  year !== baseline.year,
                )}
                value={year}
                onChange={(e) => setYear(e.target.value)}
              />
              <Input
                type="number"
                label={labelWithChange(
                  t("sync.trackFieldTrackNumber"),
                  trackNumber !== baseline.trackNumber,
                )}
                value={trackNumber}
                min={1}
                max={999}
                onChange={(e) => setTrackNumber(e.target.value)}
              />
            </div>
          </div>
          ) : (
            <p className="text-foreground-400 text-xs">
              {t("sync.editTrackAssetsHint")}
            </p>
          )}

          {/* Equal-height asset rows: preview left, source + action right. */}
          <div className="border-default-200 flex h-24 items-center justify-between gap-4 rounded-md border px-3">
            <div className="flex min-w-0 items-center">
              {coverUrl ? (
                <img
                  src={coverUrl}
                  alt=""
                  className="h-16 w-16 shrink-0 rounded object-cover"
                />
              ) : (
                <span className="text-foreground-400 text-xs">
                  {t("sync.trackCoverNone")}
                </span>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="text-foreground-400 text-xs">
                {coverChanged ? (
                  <span className="text-primary">
                    {t("sync.trackAssetModified")} ·{" "}
                  </span>
                ) : null}
                {coverUrl?.startsWith("data:")
                  ? t("sync.trackSourceLocal")
                  : (!coverChanged && coverSourceLabel(track?.cover_source)) ||
                    coverSource(coverUrl)}
              </span>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  handleCoverUpload(e.target.files?.[0] ?? null);
                  e.target.value = "";
                }}
              />
              <Button
                size="sm"
                variant="flat"
                startContent={<UploadIcon className="h-3.5 w-3.5" />}
                onPress={() => fileInputRef.current?.click()}
              >
                {t("sync.trackCoverUpload")}
              </Button>
            </div>
          </div>

          <div className="border-default-200 flex h-24 items-center justify-between gap-4 rounded-md border px-3">
            <div className="h-16 min-w-0 flex-1 overflow-y-auto pr-2 text-left">
              {lyrics ? (
                <p className="text-foreground-500 text-xs leading-relaxed whitespace-pre-wrap">
                  {plainLyrics(lyrics)}
                </p>
              ) : (
                <span className="text-foreground-400 text-xs">
                  {t("sync.trackLyricsNone")}
                </span>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="text-foreground-400 text-xs">
                {lyricsChanged ? (
                  <span className="text-primary">
                    {t("sync.trackAssetModified")} ·{" "}
                  </span>
                ) : null}
                {sourceLabel(lyricsSource)}
              </span>
              <Button
                size="sm"
                variant="flat"
                isDisabled={!trackKey}
                startContent={<PencilIcon className="h-3.5 w-3.5" />}
                onPress={openLyricsEditor}
              >
                {t("sync.lyricsEdit")}
              </Button>
            </div>
          </div>

          {track?.video_id ? (
            <p className="text-foreground-400 text-xs">
              {t("sync.trackFieldVideoId")}: {track.video_id}
            </p>
          ) : null}
          {error ? <p className="text-danger text-xs">{error}</p> : null}
        </ModalBody>
        <ModalFooter>
          <Button variant="light" onPress={onClose} isDisabled={saving || busy}>
            {t("sync.cancel")}
          </Button>
          <Button
            color="primary"
            isLoading={saving}
            isDisabled={busy || searching || resolving}
            onPress={() => {
              void handleSave();
            }}
          >
            {t("sync.save")}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
