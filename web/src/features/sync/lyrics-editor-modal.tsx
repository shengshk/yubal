import {
  firstTimestampOnLine,
  formatLrcTime,
  lineAtCursor,
  parseLyrics,
  replaceLineTimestamps,
  stripAllTimestamps,
  stripLineTimestamps,
} from "@/features/sync/lrc";
import { useLibraryAudio } from "@/features/sync/library-audio";
import { showErrorToast, showSuccessToast } from "@/lib/toast";
import {
  Button,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
} from "@heroui/react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";

function formatClock(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "00:00.00";
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  const whole = Math.floor(s);
  const cs = Math.floor((s - whole) * 100);
  return `${String(m).padStart(2, "0")}:${String(whole).padStart(2, "0")}.${String(cs).padStart(2, "0")}`;
}

function summarizeSave(r: {
  sidecar: boolean;
  embedded: boolean;
  catalog: boolean;
  errors: string[];
}): string {
  const ok: string[] = [];
  if (r.sidecar) ok.push(".lrc");
  if (r.embedded) ok.push("tags");
  if (r.catalog) ok.push("db");
  const parts = [];
  if (ok.length) parts.push(ok.join(" · "));
  if (r.errors.length) parts.push(r.errors.join("; "));
  return parts.join(" | ") || "—";
}

export function LyricsEditorModal() {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const open = audio.lyricsEditing;
  const taRef = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState("");
  const [baseline, setBaseline] = useState("");
  const [saving, setSaving] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);

  const dirty = text !== baseline;

  const targetKey = audio.lyricsEditorKey;

  useEffect(() => {
    if (!open) return;
    // Editing a non-playing track uses the provided initial content; otherwise
    // fall back to the currently playing track's lyrics.
    const initial =
      (targetKey ? audio.lyricsEditorInitial : audio.lyricsContent) ?? "";
    setText(initial);
    setBaseline(initial);
    setDiscardOpen(false);
  }, [open, targetKey, audio.lyricsEditorInitial, audio.lyricsContent]);

  const applyLineEdit = useCallback(
    (mapLine: (line: string) => string) => {
      const el = taRef.current;
      if (!el) return;
      const cursor = el.selectionStart;
      const info = lineAtCursor(text, cursor);
      const lines = text.split("\n");
      lines[info.index] = mapLine(info.text);
      const next = lines.join("\n");
      setText(next);
      requestAnimationFrame(() => {
        const start = info.start;
        const newLen = (lines[info.index] ?? "").length;
        el.focus();
        el.setSelectionRange(start, start + newLen);
      });
    },
    [text],
  );

  const jumpToCurrent = useCallback(() => {
    const el = taRef.current;
    if (!el) return;
    const parsed = parseLyrics(text);
    if (!parsed.timed || parsed.lines.length === 0) return;
    let best = 0;
    for (let i = 0; i < parsed.lines.length; i++) {
      if (parsed.lines[i]!.time <= audio.currentTime + 0.05) best = i;
      else break;
    }
    // Map timed line index → raw text line containing that timestamp text.
    const targetText = parsed.lines[best]!.text;
    const targetTime = parsed.lines[best]!.time;
    const tag = formatLrcTime(targetTime);
    const rawLines = text.split("\n");
    let rawIdx = rawLines.findIndex(
      (ln) => ln.includes(tag.slice(0, 8)) && ln.includes(targetText),
    );
    if (rawIdx < 0) {
      rawIdx = rawLines.findIndex((ln) => ln.includes(targetText));
    }
    if (rawIdx < 0) return;
    let start = 0;
    for (let i = 0; i < rawIdx; i++) start += (rawLines[i]?.length ?? 0) + 1;
    const end = start + (rawLines[rawIdx]?.length ?? 0);
    el.focus();
    el.setSelectionRange(start, end);
    // Scroll roughly into view.
    const ratio = rawIdx / Math.max(1, rawLines.length - 1);
    el.scrollTop = ratio * (el.scrollHeight - el.clientHeight);
  }, [audio.currentTime, text]);

  const tryClose = () => {
    if (dirty) {
      setDiscardOpen(true);
      return;
    }
    audio.closeLyricsEditor();
  };

  const handleSave = async () => {
    // Preview-only (opened from edit-tags): commit draft back to the parent,
    // never touch disk. Parent Save is the real write.
    if (audio.lyricsEditorPreviewOnly) {
      audio.commitLyricsEditorDraft(text);
      setBaseline(text);
      audio.closeLyricsEditor();
      return;
    }
    setSaving(true);
    const result = await audio.saveLyricsContent(text);
    setSaving(false);
    if (result.error && !result.ok) {
      showErrorToast(t("sync.lyricsEdit"), result.error);
      return;
    }
    if (result.ok) {
      showSuccessToast(t("sync.lyricsSaved"), summarizeSave(result));
      if (result.errors.length) {
        showErrorToast(t("sync.lyricsSavePartial"), result.errors.join("; "));
      }
      setBaseline(text);
      audio.closeLyricsEditor();
    } else {
      showErrorToast(t("sync.lyricsEdit"), summarizeSave(result));
    }
  };

  const handlePlayToggle = () => {
    // Editing a specific track (from edit-tags): play/pause that file so
    // timing tools work even when nothing was playing yet.
    if (targetKey && audio.lyricsEditorFolder) {
      audio.toggle(targetKey, targetKey, audio.lyricsEditorFolder);
      return;
    }
    if (audio.activeFolder) {
      audio.togglePlaylistFolder(audio.activeFolder);
    }
  };

  const onTextClick = (e: React.MouseEvent<HTMLTextAreaElement>) => {
    // Click timestamp area: if cursor lands on a timed line, seek.
    const el = e.currentTarget;
    const cursor = el.selectionStart;
    const info = lineAtCursor(text, cursor);
    const ts = firstTimestampOnLine(info.text);
    if (ts == null || !Number.isFinite(audio.duration) || audio.duration <= 0) {
      return;
    }
    // Only seek when click is in the leading tag region (~12 chars).
    const offsetInLine = cursor - info.start;
    if (offsetInLine <= 12) {
      audio.seek(Math.min(1, Math.max(0, ts / audio.duration)));
    }
  };

  return (
    <>
      <Modal
        isOpen={open}
        onClose={tryClose}
        size="3xl"
        scrollBehavior="inside"
        placement="center"
        classNames={
          audio.lyricsFullscreen || audio.lyricsEditorKey
            ? { wrapper: "z-[130]", backdrop: "z-[120]" }
            : undefined
        }
      >
        <ModalContent>
          <ModalHeader className="flex flex-col gap-1">
            <span>{t("sync.lyricsEdit")}</span>
            <span className="text-foreground-400 font-mono text-xs font-normal">
              {targetKey ?? audio.nowPlayingLabel ?? audio.key ?? ""}
            </span>
          </ModalHeader>
          <ModalBody className="gap-3">
            <div className="grid w-full grid-cols-5 gap-2">
              <Button
                size="sm"
                variant="flat"
                className="w-full min-w-0 px-1"
                isDisabled={
                  !(
                    (targetKey && audio.lyricsEditorFolder) ||
                    audio.activeFolder
                  )
                }
                onPress={handlePlayToggle}
              >
                <span className="truncate">
                  {audio.playing &&
                  (!targetKey || audio.key === targetKey)
                    ? t("sync.pauseTrack")
                    : t("sync.playTrack")}{" "}
                  <span className="font-mono text-xs opacity-70">
                    [{formatClock(audio.currentTime)}]
                  </span>
                </span>
              </Button>
              <Button
                size="sm"
                variant="flat"
                className="w-full min-w-0 px-1"
                onPress={jumpToCurrent}
              >
                <span className="truncate">
                  {t("sync.lyricsJumpLine")}
                </span>
              </Button>
              <Button
                size="sm"
                variant="flat"
                className="w-full min-w-0 px-1"
                onPress={() =>
                  applyLineEdit((line) =>
                    replaceLineTimestamps(line, audio.currentTime),
                  )
                }
              >
                <span className="truncate">
                  {t("sync.lyricsInsertTime")}
                </span>
              </Button>
              <Button
                size="sm"
                variant="flat"
                className="w-full min-w-0 px-1"
                onPress={() =>
                  applyLineEdit((line) => stripLineTimestamps(line))
                }
              >
                <span className="truncate">
                  {t("sync.lyricsRemoveTime")}
                </span>
              </Button>
              <Button
                size="sm"
                variant="flat"
                color="danger"
                className="w-full min-w-0 px-1"
                onPress={() => setText((v) => stripAllTimestamps(v))}
              >
                <span className="truncate">
                  {t("sync.lyricsRemoveAllTimes")}
                </span>
              </Button>
            </div>
            <textarea
              ref={taRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onClick={onTextClick}
              spellCheck={false}
              className="border-default-200 bg-content2 text-foreground focus:border-primary min-h-[22rem] w-full resize-y rounded-lg border px-3 py-2 font-mono text-sm leading-relaxed outline-none"
            />
          </ModalBody>
          <ModalFooter>
            <Button variant="light" onPress={tryClose} isDisabled={saving}>
              {t("sync.cancel")}
            </Button>
            <Button
              color="primary"
              isLoading={saving}
              onPress={() => {
                void handleSave();
              }}
            >
              {audio.lyricsEditorPreviewOnly
                ? t("sync.lyricsApplyDraft")
                : t("sync.save")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>

      <Modal
        isOpen={discardOpen}
        onClose={() => setDiscardOpen(false)}
        placement="center"
        size="sm"
        classNames={
          audio.lyricsFullscreen || audio.lyricsEditorKey
            ? { wrapper: "z-[150]", backdrop: "z-[140]" }
            : undefined
        }
      >
        <ModalContent>
          <ModalHeader>{t("sync.lyricsUnsavedTitle")}</ModalHeader>
          <ModalBody>
            <p className="text-foreground-500 text-sm">
              {audio.lyricsEditorPreviewOnly
                ? t("sync.lyricsUnsavedDraftBody")
                : t("sync.lyricsUnsavedBody")}
            </p>
          </ModalBody>
          <ModalFooter>
            <Button
              variant="light"
              onPress={() => {
                setDiscardOpen(false);
                audio.closeLyricsEditor();
              }}
            >
              {t("sync.lyricsDiscard")}
            </Button>
            <Button
              color="primary"
              onPress={() => {
                setDiscardOpen(false);
                void handleSave();
              }}
            >
              {audio.lyricsEditorPreviewOnly
                ? t("sync.lyricsApplyDraft")
                : t("sync.save")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </>
  );
}
