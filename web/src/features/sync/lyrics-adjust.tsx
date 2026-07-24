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
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

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

/** Confirm save/discard when leaving lyrics-adjust mode with a non-zero offset. */
export function LyricsAdjustLeaveModal() {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!audio.lyricsAdjusting) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        audio.requestLeaveLyricsAdjust();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [audio]);

  useEffect(() => {
    if (!audio.lyricsAdjusting) return;
    const onPointerDown = (e: PointerEvent) => {
      const t = e.target;
      if (!(t instanceof Element)) return;
      if (t.closest("[data-lyrics-adjust-zone]")) return;
      if (t.closest("[data-lyrics-adjust-dialog]")) return;
      audio.requestLeaveLyricsAdjust();
    };
    // Capture so we run before other handlers.
    window.addEventListener("pointerdown", onPointerDown, true);
    return () => window.removeEventListener("pointerdown", onPointerDown, true);
  }, [audio]);

  const onSave = async () => {
    setSaving(true);
    const result = await audio.confirmLeaveLyricsAdjust("save");
    setSaving(false);
    if (!result) return;
    if (result.ok) {
      showSuccessToast(t("sync.lyricsSaved"), summarizeSave(result));
      if (result.errors.length) {
        showErrorToast(t("sync.lyricsSavePartial"), result.errors.join("; "));
      }
    } else {
      showErrorToast(t("sync.lyricsAdjust"), summarizeSave(result));
    }
  };

  return (
    <Modal
      isOpen={audio.lyricsAdjustLeaveOpen}
      onClose={() => audio.cancelLeaveLyricsAdjust()}
      placement="center"
      size="sm"
      classNames={
        audio.lyricsFullscreen
          ? { wrapper: "z-[130]", backdrop: "z-[120]" }
          : undefined
      }
    >
      <ModalContent data-lyrics-adjust-dialog="">
        <ModalHeader>{t("sync.lyricsUnsavedTitle")}</ModalHeader>
        <ModalBody>
          <p className="text-foreground-500 text-sm">
            {t("sync.lyricsAdjustUnsavedBody", {
              offset:
                (audio.lyricsOffsetSec >= 0 ? "+" : "") +
                audio.lyricsOffsetSec.toFixed(2) +
                "s",
            })}
          </p>
        </ModalBody>
        <ModalFooter>
          <Button
            variant="light"
            isDisabled={saving}
            onPress={() => {
              void audio.confirmLeaveLyricsAdjust("discard");
            }}
          >
            {t("sync.lyricsDiscard")}
          </Button>
          <Button
            color="primary"
            isLoading={saving}
            onPress={() => {
              void onSave().catch((err: unknown) => {
                showErrorToast(
                  t("sync.lyricsAdjust"),
                  err instanceof Error ? err.message : String(err),
                );
              });
            }}
          >
            {t("sync.save")}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

export function formatOffsetLabel(offset: number): string {
  const sign = offset > 0 ? "+" : "";
  return `${sign}${offset.toFixed(2)}s`;
}

/** Tiny offset chip for header / fullscreen while adjusting. */
export function LyricsOffsetBadge({ className = "" }: { className?: string }) {
  const audio = useLibraryAudio();
  if (!audio.lyricsAdjusting) return null;
  return (
    <span
      data-lyrics-adjust-zone=""
      className={`text-foreground-400 font-mono text-[0.65rem] tracking-wide tabular-nums ${className}`}
      title="Shift = 0.05s"
    >
      {formatOffsetLabel(audio.lyricsOffsetSec)}
    </span>
  );
}
