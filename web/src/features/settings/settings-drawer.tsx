import { scanExternal } from "@/api/external";
import {
  auditLibrary,
  getLibraryHealth,
  type LibraryAudit,
  type LibraryHealth,
} from "@/api/library-health";
import {
  clearMatchCooldowns,
  clearScrapeCooldowns,
  executeFactoryReset,
  getSettings,
  previewFactoryReset,
  reclaimPits,
  updateSettings,
  type AppSettings,
  type FactoryResetMode,
  type FactoryResetPreview,
  type ReclaimPitTarget,
  type SettingsUpdate,
} from "@/api/settings";
import { HoverHint } from "@/components/common/hover-hint";
import { LanguageToggler } from "@/components/layout/language-toggler";
import { AnimatedThemeToggler } from "@/components/magicui/animated-theme-toggler";
import { useAuth } from "@/features/auth/auth-context";
import { useCookies } from "@/features/cookies/use-cookies";
import { showErrorToast, showSuccessToast } from "@/lib/toast";
import {
  Button,
  Divider,
  Drawer,
  DrawerBody,
  DrawerContent,
  DrawerHeader,
  Input,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Select,
  SelectItem,
  Switch,
} from "@heroui/react";
import {
  CookieIcon,
  HeartIcon,
  LibraryIcon,
  LogOutIcon,
  Mic2Icon,
  PaletteIcon,
  RotateCcwIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const EXTERNAL_RAW_PATH = "/data/external/raw";
const EXTERNAL_ORGANIZED_PATH = "/data/external/organized";

type Props = {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
};

type ResetStage = "select" | "warning" | "confirm";

function SettingsDisclosure({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <details className="border-default-200 group rounded-xl border">
      <summary className="text-foreground-600 cursor-pointer list-none px-3 py-2.5 text-sm font-medium marker:hidden">
        <div className="flex items-center justify-between gap-3">
          <span>{title}</span>
          <span className="text-foreground-400 transition-transform group-open:rotate-180">
            ▾
          </span>
        </div>
        {hint ? (
          <span className="text-foreground-400 mt-0.5 block text-xs font-normal">
            {hint}
          </span>
        ) : null}
      </summary>
      <div className="border-default-200 flex flex-col gap-3 border-t px-3 py-3">
        {children}
      </div>
    </details>
  );
}

type FormState = {
  min_free_gb: string;
  direct_download_limit: string;
  index_threshold: string;
  track_sort_key: AppSettings["track_sort_key"];
  search_result_ttl_hours: string;
  audio_format: AppSettings["audio_format"];
  audio_quality: string;
  fetch_lyrics: boolean;
  ytmusic_lyrics_fallback: boolean;
  qq_lyrics_fallback: boolean;
  scrape_cooldown_hours: string;
  scheduler_enabled: boolean;
  scheduler_cron: string;
  external_inventory_schedule_enabled: boolean;
  external_inventory_schedule_time: string;
  job_timeout_seconds: string;
  external_library_enabled: boolean;
  external_new_playlist_mode: AppSettings["external_new_playlist_mode"];
  match_backoff_cap_days: string;
  match_strictness: AppSettings["match_strictness"];
  cover_excellence_px: string;
  cover_probe_fresh_days: string;
  cover_download_fresh_days: string;
  download_cache_enabled: boolean;
  cache_min_free_gb: string;
  telegram_bot_token: string;
  telegram_admin_ids: string;
  telegram_user_ids: string;
  telegram_daily_limit: string;
  wanted_enabled: boolean;
  wanted_auto_match_enabled: boolean;
  wanted_max_items: string;
  wanted_sync_jitter_seconds: string;
  wanted_source_musicbrainz: boolean;
  wanted_source_qq: boolean;
  wanted_source_discogs: boolean;
  wanted_source_lastfm: boolean;
  lastfm_api_key: string;
};

function formFromSettings(data: AppSettings): FormState {
  return {
    min_free_gb: String(data.min_free_gb),
    direct_download_limit: String(data.direct_download_limit ?? 50),
    index_threshold: String(data.index_threshold ?? 50),
    track_sort_key: data.track_sort_key ?? "title",
    search_result_ttl_hours: String(data.search_result_ttl_hours ?? 24),
    audio_format: data.audio_format,
    audio_quality: String(data.audio_quality),
    fetch_lyrics: data.fetch_lyrics,
    ytmusic_lyrics_fallback: data.ytmusic_lyrics_fallback,
    qq_lyrics_fallback: data.qq_lyrics_fallback,
    scrape_cooldown_hours: String(data.scrape_cooldown_hours ?? 24),
    scheduler_enabled: data.scheduler_enabled,
    scheduler_cron: data.scheduler_cron,
    external_inventory_schedule_enabled:
      data.external_inventory_schedule_enabled ?? true,
    external_inventory_schedule_time:
      data.external_inventory_schedule_time ?? "03:00",
    job_timeout_seconds: String(data.job_timeout_seconds),
    external_library_enabled: data.external_library_enabled ?? false,
    external_new_playlist_mode: data.external_new_playlist_mode ?? "pending",
    match_backoff_cap_days: String(data.match_backoff_cap_days ?? 7),
    match_strictness: data.match_strictness ?? "strict",
    cover_excellence_px: String(data.cover_excellence_px ?? 0),
    cover_probe_fresh_days: String(data.cover_probe_fresh_days ?? 7),
    cover_download_fresh_days: String(data.cover_download_fresh_days ?? 30),
    download_cache_enabled: data.download_cache_enabled ?? false,
    cache_min_free_gb: String(data.cache_min_free_gb ?? 2),
    telegram_bot_token: data.telegram_bot_token ?? "",
    telegram_admin_ids: data.telegram_admin_ids ?? "",
    telegram_user_ids: data.telegram_user_ids ?? "",
    telegram_daily_limit: String(data.telegram_daily_limit ?? 5),
    wanted_enabled: data.wanted_enabled ?? true,
    wanted_auto_match_enabled: data.wanted_auto_match_enabled ?? true,
    wanted_max_items: String(data.wanted_max_items ?? 50),
    wanted_sync_jitter_seconds: String(data.wanted_sync_jitter_seconds ?? 600),
    wanted_source_musicbrainz: data.wanted_source_musicbrainz ?? true,
    wanted_source_qq: data.wanted_source_qq ?? true,
    wanted_source_discogs: data.wanted_source_discogs ?? false,
    wanted_source_lastfm: data.wanted_source_lastfm ?? false,
    lastfm_api_key: data.lastfm_api_key ?? "",
  };
}

export function SettingsDrawer({ isOpen, onOpenChange }: Props) {
  const { t } = useTranslation();
  const { status: authStatus, logout } = useAuth();
  const {
    cookiesConfigured,
    cookiesStatus,
    isUploading,
    isDeleting,
    fileInputRef,
    handleFileSelect,
    handleDropdownAction,
    triggerFileUpload,
    refreshCookiesStatus,
  } = useCookies();

  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetPreviewing, setResetPreviewing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [confirmResetOpen, setConfirmResetOpen] = useState(false);
  const [resetStage, setResetStage] = useState<ResetStage>("select");
  const [resetPreview, setResetPreview] = useState<FactoryResetPreview | null>(
    null,
  );
  const [resetPassword, setResetPassword] = useState("");
  const [libraryHealth, setLibraryHealth] = useState<LibraryHealth | null>(
    null,
  );
  const [externalScanning, setExternalScanning] = useState(false);
  const [auditingLibrary, setAuditingLibrary] = useState(false);
  const [libraryAudit, setLibraryAudit] = useState<LibraryAudit | null>(null);
  const [clearingCooldowns, setClearingCooldowns] = useState(false);
  const [reclaiming, setReclaiming] = useState(false);
  const [reclaimTarget, setReclaimTarget] = useState<ReclaimPitTarget | null>(
    null,
  );
  const debounceTimers = useRef<Record<string, number>>({});

  useEffect(() => {
    if (!isOpen) return;
    let mounted = true;
    setLoading(true);
    void refreshCookiesStatus();
    void getSettings().then((data) => {
      if (!mounted) return;
      setSettings(data);
      if (data) setForm(formFromSettings(data));
      setLoading(false);
    });
    void getLibraryHealth().then((health) => {
      if (mounted) setLibraryHealth(health);
    });
    return () => {
      mounted = false;
    };
  }, [isOpen, refreshCookiesStatus]);

  useEffect(() => {
    return () => {
      for (const timer of Object.values(debounceTimers.current)) {
        window.clearTimeout(timer);
      }
    };
  }, []);

  const applyResult = (result: AppSettings) => {
    setSettings(result);
    setForm(formFromSettings(result));
    window.dispatchEvent(new Event("yubal:settings-changed"));
  };

  const reloadFromServer = async () => {
    const data = await getSettings();
    if (data) applyResult(data);
  };

  const patch = async (
    updates: SettingsUpdate,
    opts?: { quiet?: boolean; toastDesc?: string },
  ): Promise<AppSettings | null> => {
    setSaving(true);
    const result = await updateSettings(updates);
    setSaving(false);
    if ("error" in result) {
      showErrorToast(t("settings.saveFailedTitle"), result.error);
      await reloadFromServer();
      return null;
    }
    applyResult(result);
    if (!opts?.quiet) {
      showSuccessToast(
        t("settings.savedTitle"),
        opts?.toastDesc ?? t("settings.savedDesc"),
      );
    }
    return result;
  };

  const patchField = (updates: SettingsUpdate) => {
    void patch(updates);
  };

  const patchFieldDebounced = (
    key: string,
    updates: SettingsUpdate,
    delayMs = 600,
  ) => {
    const existing = debounceTimers.current[key];
    if (existing) window.clearTimeout(existing);
    debounceTimers.current[key] = window.setTimeout(() => {
      void patch(updates, { quiet: true }).then((result) => {
        if (result) {
          showSuccessToast(t("settings.savedTitle"), t("settings.savedDesc"));
        }
      });
    }, delayMs);
  };

  const handleExternalScan = async () => {
    setExternalScanning(true);
    const result = await scanExternal();
    setExternalScanning(false);
    if ("error" in result) {
      showErrorToast(t("settings.saveFailedTitle"), result.error);
      return;
    }
    showSuccessToast(
      t("settings.savedTitle"),
      t("settings.externalScanDone", {
        scanned: result.scanned,
        added: result.added,
        updated: result.updated,
      }),
    );
    window.dispatchEvent(new Event("yubal:ledger-changed"));
  };

  const handleLibraryAudit = async () => {
    setAuditingLibrary(true);
    const result = await auditLibrary(true);
    setAuditingLibrary(false);
    if ("error" in result) {
      showErrorToast(t("settings.libraryAuditFailed"), result.error);
      return;
    }
    setLibraryAudit(result);
    showSuccessToast(
      t("settings.libraryAuditDoneTitle"),
      t("settings.libraryAuditDone", {
        physical: result.physical_count,
        catalog: result.catalog_location_count,
        repaired:
          result.repaired_catalog_locations + result.repaired_index_entries,
        untracked: result.untracked_physical_count,
      }),
    );
    window.dispatchEvent(new Event("yubal:ledger-changed"));
  };

  const handleClearScrapeCooldowns = async () => {
    setClearingCooldowns(true);
    const result = await clearScrapeCooldowns();
    setClearingCooldowns(false);
    if ("error" in result) {
      showErrorToast(t("settings.saveFailedTitle"), result.error);
      return;
    }
    showSuccessToast(
      t("settings.savedTitle"),
      t("settings.clearScrapeCooldownsDone", { count: result.cleared }),
    );
  };

  const handleClearMatchCooldowns = async (includeRejected: boolean) => {
    setClearingCooldowns(true);
    const result = await clearMatchCooldowns(includeRejected);
    setClearingCooldowns(false);
    if ("error" in result) {
      showErrorToast(t("settings.saveFailedTitle"), result.error);
      return;
    }
    showSuccessToast(
      t("settings.savedTitle"),
      t("settings.clearMatchCooldownsDone", { count: result.cleared }),
    );
    window.dispatchEvent(new Event("yubal:ledger-changed"));
  };

  const handleReclaimPits = async () => {
    if (!reclaimTarget) return;
    const target = reclaimTarget;
    setReclaimTarget(null);
    setReclaiming(true);
    const result = await reclaimPits(target);
    setReclaiming(false);
    if ("error" in result) {
      showErrorToast(t("settings.saveFailedTitle"), result.error);
      return;
    }
    showSuccessToast(
      t("settings.savedTitle"),
      t("settings.reclaimDone", {
        files: result.deleted_files,
        raw: result.deleted_raw,
        locations: result.deleted_locations,
      }),
    );
    window.dispatchEvent(new Event("yubal:ledger-changed"));
  };

  const openReset = () => {
    setResetStage("select");
    setResetPreview(null);
    setResetPassword("");
    setConfirmResetOpen(true);
  };

  const selectResetMode = async (mode: FactoryResetMode) => {
    setResetPreviewing(true);
    const result = await previewFactoryReset(mode);
    setResetPreviewing(false);
    if ("error" in result) {
      showErrorToast(t("settings.resetFailedTitle"), result.error);
      return;
    }
    setResetPreview(result);
    setResetStage(mode === "preferences" ? "confirm" : "warning");
  };

  const clearBrowserPreferences = () => {
    for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
      const key = window.localStorage.key(index);
      if (key?.startsWith("yubal")) window.localStorage.removeItem(key);
    }
  };

  const handleReset = async () => {
    if (!resetPreview) return;
    setResetting(true);
    const result = await executeFactoryReset(resetPreview, resetPassword);
    setResetting(false);
    if ("error" in result) {
      showErrorToast(
        t("settings.resetFailedTitle"),
        result.error === "invalid password"
          ? t("settings.factoryPasswordInvalid")
          : result.error,
      );
      return;
    }
    clearBrowserPreferences();
    setConfirmResetOpen(false);
    if (result.requires_setup) {
      window.location.reload();
      return;
    }
    const next = await getSettings();
    if (next) applyResult(next);
    showSuccessToast(
      t("settings.factoryDoneTitle"),
      t(`settings.factoryDone.${result.mode}`),
    );
    window.setTimeout(() => window.location.reload(), 500);
  };

  const busy = loading || !form || saving;
  const resetStats = resetPreview
    ? t("settings.factoryStats", {
        entries: resetPreview.list_entries,
        files: resetPreview.files,
        paths: resetPreview.paths,
        size: (resetPreview.bytes / (1024 * 1024)).toFixed(1),
      })
    : "";

  return (
    <>
      <Drawer
        isOpen={isOpen}
        onOpenChange={onOpenChange}
        placement="right"
        size="sm"
        classNames={{
          base: "rounded-none",
        }}
      >
        <DrawerContent className="rounded-none">
          <DrawerHeader className="flex flex-col gap-1">
            <span>{t("settings.title")}</span>
            <span className="text-foreground-400 text-xs font-normal">
              {saving ? t("settings.saving") : t("settings.autoSaveHint")}
            </span>
          </DrawerHeader>
          <DrawerBody className="gap-8 pb-8">
            {settings?.maintenance_locked && (
              <p className="text-warning text-xs">
                {t("settings.maintenanceLocked")}
              </p>
            )}

            <section className="flex flex-col gap-4">
              <div className="flex items-center gap-2">
                <LibraryIcon className="text-foreground-500 h-4 w-4" />
                <h3 className="text-sm font-semibold">
                  {t("settings.sectionSyncLibrary")}
                </h3>
              </div>

              <div className="flex flex-col gap-3">
                <HoverHint content={t("settings.schedulerScopeSummary")}>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-foreground-500 text-sm font-medium">
                      {t("settings.schedulerTitle")}
                    </span>
                    <Switch
                      size="sm"
                      isSelected={form?.scheduler_enabled ?? true}
                      isDisabled={busy}
                      onValueChange={(v) => {
                        if (!form) return;
                        setForm({ ...form, scheduler_enabled: v });
                        patchField({ scheduler_enabled: v });
                      }}
                    />
                  </div>
                </HoverHint>
                <HoverHint content={t("settings.schedulerCronHint")}>
                  <Input
                    size="sm"
                    label={t("settings.schedulerCron")}
                    value={form?.scheduler_cron ?? ""}
                    isDisabled={busy || !(form?.scheduler_enabled ?? true)}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, scheduler_cron: v });
                      patchFieldDebounced("scheduler_cron", {
                        scheduler_cron: v.trim(),
                      });
                    }}
                    classNames={{ input: "font-mono" }}
                  />
                </HoverHint>

              </div>

              <div className="flex flex-col gap-3">
                <p className="text-foreground-500 text-sm font-medium">
                  {t("settings.sectionDownload")}
                </p>
                <div className="grid grid-cols-3 gap-2">
                  <Select
                    size="sm"
                    label={t("settings.audioFormat")}
                    selectedKeys={form ? [form.audio_format] : []}
                    isDisabled={busy}
                    onSelectionChange={(keys) => {
                      const value = Array.from(keys)[0] as
                        | AppSettings["audio_format"]
                        | undefined;
                      if (!value || !form) return;
                      setForm({ ...form, audio_format: value });
                      patchField({ audio_format: value });
                    }}
                  >
                    <SelectItem key="opus">opus</SelectItem>
                    <SelectItem key="mp3">mp3</SelectItem>
                    <SelectItem key="m4a">m4a</SelectItem>
                  </Select>
                  <Input
                    size="sm"
                    type="number"
                    min={0}
                    max={10}
                    step={1}
                    label={t("settings.audioQuality")}
                    title={t("settings.audioQualityHint")}
                    value={form?.audio_quality ?? "0"}
                    isDisabled={busy}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, audio_quality: v });
                      const quality = Number.parseInt(v, 10);
                      if (
                        Number.isNaN(quality) ||
                        quality < 0 ||
                        quality > 10
                      ) {
                        return;
                      }
                      patchFieldDebounced("audio_quality", {
                        audio_quality: quality,
                      });
                    }}
                    classNames={{ input: "font-mono" }}
                  />
                  <Input
                    size="sm"
                    type="number"
                    min={60}
                    max={86400}
                    step={60}
                    label={t("settings.jobTimeout")}
                    title={t("settings.jobTimeoutHint")}
                    value={form?.job_timeout_seconds ?? "1800"}
                    isDisabled={busy}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, job_timeout_seconds: v });
                      const timeout = Number.parseInt(v, 10);
                      if (Number.isNaN(timeout) || timeout < 60) return;
                      patchFieldDebounced("job_timeout_seconds", {
                        job_timeout_seconds: timeout,
                      });
                    }}
                    endContent={
                      <span className="text-foreground-400 text-[10px]">
                        {t("settings.seconds")}
                      </span>
                    }
                    classNames={{ input: "font-mono" }}
                  />
                </div>
              </div>

              <SettingsDisclosure
                title={t("settings.advancedSettings")}
                hint={t("settings.advancedSettingsHint")}
              >
                <div className="grid grid-cols-2 gap-2">
                  <Input
                    size="sm"
                    type="number"
                    min={1}
                    max={100}
                    step={1}
                    label={t("settings.directDownloadLimit")}
                    title={t("settings.directDownloadLimitHint")}
                    value={form?.direct_download_limit ?? "50"}
                    isDisabled={busy}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, direct_download_limit: v });
                      const limit = Number.parseInt(v, 10);
                      if (Number.isNaN(limit) || limit < 1 || limit > 100) {
                        return;
                      }
                      patchFieldDebounced("direct_download_limit", {
                        direct_download_limit: limit,
                      });
                    }}
                    classNames={{ input: "font-mono" }}
                  />
                  <Input
                    size="sm"
                    type="number"
                    min={1}
                    max={10000}
                    step={1}
                    label={t("settings.indexThreshold")}
                    title={t("settings.indexThresholdHint")}
                    value={form?.index_threshold ?? "50"}
                    isDisabled={busy}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, index_threshold: v });
                      const threshold = Number.parseInt(v, 10);
                      if (
                        Number.isNaN(threshold) ||
                        threshold < 1 ||
                        threshold > 10000
                      ) {
                        return;
                      }
                      patchFieldDebounced("index_threshold", {
                        index_threshold: threshold,
                      });
                    }}
                    classNames={{ input: "font-mono" }}
                  />
                  <Select
                    size="sm"
                    label={t("settings.trackSortKey")}
                    title={t("settings.trackSortKeyHint")}
                    selectedKeys={
                      form?.track_sort_key
                        ? new Set([form.track_sort_key])
                        : new Set(["title"])
                    }
                    isDisabled={busy}
                    onSelectionChange={(keys) => {
                      if (!form) return;
                      const key = Array.from(keys)[0] as
                        | AppSettings["track_sort_key"]
                        | undefined;
                      if (!key || key === form.track_sort_key) return;
                      setForm({ ...form, track_sort_key: key });
                      patchField({ track_sort_key: key });
                    }}
                  >
                    <SelectItem key="title">
                      {t("settings.trackSortTitle")}
                    </SelectItem>
                    <SelectItem key="artist">
                      {t("settings.trackSortArtist")}
                    </SelectItem>
                    <SelectItem key="album">
                      {t("settings.trackSortAlbum")}
                    </SelectItem>
                  </Select>
                  <Input
                    size="sm"
                    type="number"
                    min={1}
                    max={720}
                    step={1}
                    label={t("settings.searchResultTtl")}
                    title={t("settings.searchResultTtlHint")}
                    value={form?.search_result_ttl_hours ?? "24"}
                    isDisabled={busy}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, search_result_ttl_hours: v });
                      const hours = Number.parseInt(v, 10);
                      if (Number.isNaN(hours) || hours < 1 || hours > 720) {
                        return;
                      }
                      patchFieldDebounced("search_result_ttl_hours", {
                        search_result_ttl_hours: hours,
                      });
                    }}
                    endContent={
                      <span className="text-foreground-400 text-[10px]">
                        {t("settings.hours")}
                      </span>
                    }
                    classNames={{ input: "font-mono" }}
                  />
                </div>
              </SettingsDisclosure>

              <div className="flex flex-col gap-3">
                <HoverHint
                  content={
                    <div className="space-y-1">
                      <p>{t("settings.externalEnabledHint")}</p>
                      <p>
                        {t("settings.externalRawPath", {
                          path: EXTERNAL_RAW_PATH,
                        })}
                      </p>
                      <p>
                        {t("settings.externalOrganizedPath", {
                          path: EXTERNAL_ORGANIZED_PATH,
                        })}
                      </p>
                    </div>
                  }
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-foreground-500 text-sm font-medium">
                      {t("settings.externalTitle")}
                    </span>
                    <Switch
                      size="sm"
                      isSelected={form?.external_library_enabled ?? false}
                      isDisabled={busy}
                      onValueChange={(v) => {
                        if (!form) return;
                        setForm({ ...form, external_library_enabled: v });
                        void patch({ external_library_enabled: v }).then(
                          (result) => {
                            if (result && v) {
                              void getLibraryHealth().then(setLibraryHealth);
                            }
                          },
                        );
                      }}
                    />
                  </div>
                </HoverHint>
                {form?.external_library_enabled ? (
                  <SettingsDisclosure
                    title={t("settings.externalAdvanced")}
                    hint={t("settings.externalAdvancedHint")}
                  >
                    {libraryHealth ? (
                      <p
                        className={
                          libraryHealth.status === "healthy"
                            ? "text-success text-xs"
                            : "text-danger text-xs"
                        }
                      >
                        {t(`libraryHealth.status.${libraryHealth.status}`)}
                      </p>
                    ) : null}
                    <HoverHint
                      content={t("settings.externalInventoryScheduleHint")}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-foreground-500 text-sm">
                          {t("settings.externalInventorySchedule")}
                        </span>
                        <Switch
                          size="sm"
                          isSelected={
                            form?.external_inventory_schedule_enabled ?? true
                          }
                          isDisabled={busy}
                          onValueChange={(v) => {
                            if (!form) return;
                            setForm({
                              ...form,
                              external_inventory_schedule_enabled: v,
                            });
                            patchField({
                              external_inventory_schedule_enabled: v,
                            });
                          }}
                        />
                      </div>
                    </HoverHint>
                    <Input
                      size="sm"
                      type="time"
                      label={t("settings.externalInventoryScheduleTime")}
                      value={form?.external_inventory_schedule_time ?? "03:00"}
                      isDisabled={
                        busy ||
                        !(form?.external_inventory_schedule_enabled ?? true)
                      }
                      onValueChange={(v) => {
                        if (!form) return;
                        setForm({
                          ...form,
                          external_inventory_schedule_time: v,
                        });
                        if (/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(v)) {
                          patchField({
                            external_inventory_schedule_time: v,
                          });
                        }
                      }}
                    />
                    <Select
                      size="sm"
                      label={t("settings.externalNewPlaylistMode")}
                      title={t("settings.externalNewPlaylistModeHint")}
                      selectedKeys={
                        form ? [form.external_new_playlist_mode] : ["pending"]
                      }
                      isDisabled={busy}
                      onSelectionChange={(keys) => {
                        const value = Array.from(keys)[0] as
                          | AppSettings["external_new_playlist_mode"]
                          | undefined;
                        if (!value || !form) return;
                        setForm({ ...form, external_new_playlist_mode: value });
                        patchField({ external_new_playlist_mode: value });
                      }}
                    >
                      <SelectItem key="pending">
                        {t("settings.externalNewPlaylistPending")}
                      </SelectItem>
                      <SelectItem key="readonly">
                        {t("settings.externalNewPlaylistReadonly")}
                      </SelectItem>
                      <SelectItem key="managed">
                        {t("settings.externalNewPlaylistManaged")}
                      </SelectItem>
                    </Select>
                    <div className="grid grid-cols-2 gap-2">
                      <Input
                        size="sm"
                        type="number"
                        min={1}
                        max={30}
                        step={1}
                        label={t("settings.matchBackoffCapDays")}
                        title={t("settings.matchBackoffCapDaysHint")}
                        value={form?.match_backoff_cap_days ?? "7"}
                        isDisabled={busy}
                        onValueChange={(v) => {
                          if (!form) return;
                          setForm({ ...form, match_backoff_cap_days: v });
                          const days = Number.parseInt(v, 10);
                          if (Number.isNaN(days) || days < 1 || days > 30)
                            return;
                          patchFieldDebounced("match_backoff_cap_days", {
                            match_backoff_cap_days: days,
                          });
                        }}
                        classNames={{ input: "font-mono" }}
                      />
                      <Select
                        size="sm"
                        label={t("settings.matchStrictness")}
                        title={t("settings.matchStrictnessHint")}
                        selectedKeys={
                          form ? [form.match_strictness] : ["strict"]
                        }
                        isDisabled={busy}
                        onSelectionChange={(keys) => {
                          const value = Array.from(keys)[0] as
                            | AppSettings["match_strictness"]
                            | undefined;
                          if (!value || !form) return;
                          setForm({ ...form, match_strictness: value });
                          patchField({ match_strictness: value });
                        }}
                      >
                        <SelectItem key="strict">
                          {t("settings.matchStrictnessStrict")}
                        </SelectItem>
                        <SelectItem key="relaxed">
                          {t("settings.matchStrictnessRelaxed")}
                        </SelectItem>
                      </Select>
                    </div>
                    <div className="grid w-full grid-cols-3 gap-2">
                      <Button
                        size="sm"
                        variant="flat"
                        className="h-auto min-h-8 w-full px-1 text-center text-[11px] leading-tight whitespace-normal"
                        isDisabled={busy || externalScanning}
                        isLoading={externalScanning}
                        onPress={() => {
                          void handleExternalScan();
                        }}
                      >
                        {externalScanning
                          ? t("settings.externalScanning")
                          : t("settings.externalScan")}
                      </Button>
                      <Button
                        size="sm"
                        variant="flat"
                        className="h-auto min-h-8 w-full px-1 text-center text-[11px] leading-tight whitespace-normal"
                        title={t("settings.clearMatchCooldownsHint")}
                        isDisabled={busy || clearingCooldowns}
                        isLoading={clearingCooldowns}
                        onPress={() => {
                          void handleClearMatchCooldowns(false);
                        }}
                      >
                        {t("settings.clearMatchCooldowns")}
                      </Button>
                      <Button
                        size="sm"
                        variant="flat"
                        className="h-auto min-h-8 w-full px-1 text-center text-[11px] leading-tight whitespace-normal"
                        title={t("settings.clearMatchCooldownsRejectedHint")}
                        isDisabled={busy || clearingCooldowns}
                        isLoading={clearingCooldowns}
                        onPress={() => {
                          void handleClearMatchCooldowns(true);
                        }}
                      >
                        {t("settings.clearMatchCooldownsRejected")}
                      </Button>
                    </div>
                  </SettingsDisclosure>
                ) : null}
              </div>

              <SettingsDisclosure
                title={t("settings.sectionStorage")}
                hint={t("settings.storageAdvancedHint")}
              >
                <HoverHint content={t("settings.cacheMinFreeHint")}>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-foreground-500 text-sm">
                      {t("settings.downloadCacheEnabled")}
                    </span>
                    <Switch
                      size="sm"
                      isSelected={form?.download_cache_enabled ?? false}
                      isDisabled={busy}
                      onValueChange={(v) => {
                        if (!form) return;
                        setForm({ ...form, download_cache_enabled: v });
                        patchField({ download_cache_enabled: v });
                      }}
                    />
                  </div>
                </HoverHint>
                <div className="grid grid-cols-2 gap-2">
                  <HoverHint
                    content={[
                      t("settings.cacheMinFreeHint"),
                      settings?.cache_path ?? "",
                      loading || !settings
                        ? t("common.loading")
                        : settings.cache_available
                          ? t("settings.cacheFreeNow", {
                              free: settings.cache_free_gb,
                            })
                          : t("settings.cacheUnavailable"),
                    ]
                      .filter(Boolean)
                      .join("\n")}
                  >
                    <Input
                      size="sm"
                      type="number"
                      min={0}
                      step={0.5}
                      label={t("settings.cacheMinFreeGb")}
                      value={form?.cache_min_free_gb ?? "2"}
                      isDisabled={busy}
                      onValueChange={(v) => {
                        if (!form) return;
                        setForm({ ...form, cache_min_free_gb: v });
                        const minimum = Number.parseFloat(v);
                        if (Number.isNaN(minimum) || minimum < 0) return;
                        patchFieldDebounced("cache_min_free_gb", {
                          cache_min_free_gb: minimum,
                        });
                      }}
                      endContent={
                        <span className="text-foreground-400 text-xs">GiB</span>
                      }
                      classNames={{ input: "font-mono" }}
                    />
                  </HoverHint>
                  <HoverHint
                    content={[
                      t("settings.diskHint"),
                      settings?.data_path ?? "",
                      loading || !settings
                        ? t("common.loading")
                        : t("settings.freeNow", {
                            free: settings.free_gb,
                          }),
                    ]
                      .filter(Boolean)
                      .join("\n")}
                  >
                    <Input
                      size="sm"
                      type="number"
                      min={0}
                      step={0.5}
                      label={t("settings.minFreeGb")}
                      value={form?.min_free_gb ?? "2"}
                      isDisabled={busy}
                      onValueChange={(v) => {
                        if (!form) return;
                        setForm({ ...form, min_free_gb: v });
                        const minFree = Number.parseFloat(v);
                        if (Number.isNaN(minFree) || minFree < 0) return;
                        patchFieldDebounced("min_free_gb", {
                          min_free_gb: minFree,
                        });
                      }}
                      endContent={
                        <span className="text-foreground-400 text-xs">GiB</span>
                      }
                      classNames={{ input: "font-mono" }}
                    />
                  </HoverHint>
                </div>
                <div className="flex flex-col gap-2">
                  <HoverHint
                    content={
                      <div className="space-y-1">
                        <p>
                          {libraryAudit
                            ? t("settings.libraryAuditResult", {
                                physical: libraryAudit.physical_count,
                                hardlinks:
                                  libraryAudit.hardlink_duplicate_count,
                                missing: libraryAudit.missing_catalog_locations,
                                untracked:
                                  libraryAudit.untracked_physical_count,
                              })
                            : t("settings.libraryAuditHint")}
                        </p>
                        <p>{t("settings.databaseBackupHint")}</p>
                      </div>
                    }
                  >
                    <Button
                      size="sm"
                      variant="flat"
                      className="w-full"
                      isDisabled={busy || auditingLibrary}
                      isLoading={auditingLibrary}
                      onPress={() => {
                        void handleLibraryAudit();
                      }}
                    >
                      {auditingLibrary
                        ? t("settings.libraryAuditing")
                        : t("settings.libraryAudit")}
                    </Button>
                  </HoverHint>
                </div>
                {form?.external_library_enabled ? (
                  <div className="flex flex-col gap-2">
                    <HoverHint content={t("settings.reclaimPitsHint")}>
                      <p className="text-foreground-500 text-sm">
                        {t("settings.reclaimPitsTitle")}
                      </p>
                    </HoverHint>
                    <div className="grid w-full grid-cols-3 gap-2">
                      <Button
                        size="sm"
                        color="danger"
                        variant="flat"
                        className="h-auto min-h-8 w-full px-1 text-center text-[11px] leading-tight whitespace-normal"
                        isDisabled={busy || reclaiming}
                        onPress={() => setReclaimTarget("delete")}
                      >
                        {t("settings.reclaimDelete")}
                      </Button>
                      <Button
                        size="sm"
                        color="danger"
                        variant="flat"
                        className="h-auto min-h-8 w-full px-1 text-center text-[11px] leading-tight whitespace-normal"
                        isDisabled={busy || reclaiming}
                        onPress={() => setReclaimTarget("default")}
                      >
                        {t("settings.reclaimDefault")}
                      </Button>
                      <Button
                        size="sm"
                        color="danger"
                        variant="flat"
                        className="h-auto min-h-8 w-full px-1 text-center text-[11px] leading-tight whitespace-normal"
                        isDisabled={busy || reclaiming}
                        onPress={() => setReclaimTarget("both")}
                      >
                        {t("settings.reclaimBoth")}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <p className="text-foreground-400 text-xs text-pretty">
                    {t("settings.reclaimNeedExternal")}
                  </p>
                )}
              </SettingsDisclosure>
            </section>

            <Divider />

            <section className="flex flex-col gap-3">
              <HoverHint content={t("settings.wantedHint")}>
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <HeartIcon className="text-foreground-500 h-4 w-4" />
                    <h3 className="text-sm font-semibold">
                      {t("settings.wantedTitle")}
                    </h3>
                  </div>
                  <Switch
                    size="sm"
                    isSelected={form?.wanted_enabled ?? true}
                    isDisabled={busy}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, wanted_enabled: v });
                      void patch({ wanted_enabled: v });
                    }}
                  />
                </div>
              </HoverHint>
              {form?.wanted_enabled ? (
                <SettingsDisclosure
                  title={t("settings.wantedSources")}
                  hint={t("settings.metadataSourcesHint")}
                >
                  <p className="text-foreground-500 text-xs font-medium">
                    {t("settings.wantedSources")}
                  </p>
                  <div className="flex flex-col gap-2">
                    {(
                      [
                        [
                          "wanted_source_musicbrainz",
                          "settings.wantedSourceMusicbrainz",
                        ],
                        ["wanted_source_qq", "settings.wantedSourceQq"],
                        [
                          "wanted_source_discogs",
                          "settings.wantedSourceDiscogs",
                        ],
                        ["wanted_source_lastfm", "settings.wantedSourceLastfm"],
                      ] as const
                    ).map(([key, labelKey]) => (
                      <div
                        key={key}
                        className="flex items-center justify-between gap-3"
                      >
                        <span className="text-foreground-500 text-sm">
                          {t(labelKey)}
                        </span>
                        <Switch
                          size="sm"
                          isSelected={form[key]}
                          isDisabled={busy}
                          onValueChange={(v) => {
                            setForm({ ...form, [key]: v });
                            patchField({ [key]: v });
                          }}
                        />
                      </div>
                    ))}
                  </div>
                  {form.wanted_source_lastfm ? (
                    <Input
                      size="sm"
                      type="text"
                      label={t("settings.lastfmApiKey")}
                      title={t("settings.lastfmApiKeyHint")}
                      value={form.lastfm_api_key}
                      isDisabled={busy}
                      onValueChange={(v) => {
                        setForm({ ...form, lastfm_api_key: v });
                        patchFieldDebounced("lastfm_api_key", {
                          lastfm_api_key: v,
                        });
                      }}
                      classNames={{ input: "font-mono" }}
                    />
                  ) : null}
                </SettingsDisclosure>
              ) : null}
            </section>

            <Divider />

            <section className="flex flex-col gap-3">
              <div className="flex items-center gap-2">
                <Mic2Icon className="text-foreground-500 h-4 w-4" />
                <h3 className="text-sm font-semibold">
                  {t("settings.sectionLyrics")}
                </h3>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-foreground-500 text-sm">
                  {t("settings.fetchLyrics")}
                </span>
                <Switch
                  size="sm"
                  isSelected={form?.fetch_lyrics ?? true}
                  isDisabled={busy}
                  onValueChange={(v) => {
                    if (!form) return;
                    setForm({ ...form, fetch_lyrics: v });
                    patchField({ fetch_lyrics: v });
                  }}
                />
              </div>
              <SettingsDisclosure
                title={t("settings.lyricsAdvanced")}
                hint={t("settings.lyricsAdvancedHint")}
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="text-foreground-500 text-sm">
                    {t("settings.ytmusicLyricsFallback")}
                  </span>
                  <Switch
                    size="sm"
                    isSelected={form?.ytmusic_lyrics_fallback ?? true}
                    isDisabled={busy || !form?.fetch_lyrics}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, ytmusic_lyrics_fallback: v });
                      patchField({ ytmusic_lyrics_fallback: v });
                    }}
                  />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-foreground-500 text-sm">
                    {t("settings.qqLyricsFallback")}
                  </span>
                  <Switch
                    size="sm"
                    isSelected={form?.qq_lyrics_fallback ?? true}
                    isDisabled={busy || !form?.fetch_lyrics}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, qq_lyrics_fallback: v });
                      patchField({ qq_lyrics_fallback: v });
                    }}
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Input
                    size="sm"
                    type="number"
                    min={0}
                    max={8760}
                    step={1}
                    label={t("settings.scrapeCooldown")}
                    title={t("settings.scrapeCooldownHint")}
                    value={form?.scrape_cooldown_hours ?? "24"}
                    isDisabled={busy}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, scrape_cooldown_hours: v });
                      const hours = Number.parseInt(v, 10);
                      if (Number.isNaN(hours) || hours < 0) return;
                      patchFieldDebounced("scrape_cooldown_hours", {
                        scrape_cooldown_hours: hours,
                      });
                    }}
                    classNames={{ input: "font-mono", label: "truncate" }}
                  />
                  <Input
                    size="sm"
                    type="number"
                    min={0}
                    max={10000}
                    step={100}
                    label={t("settings.coverExcellencePx")}
                    title={t("settings.coverExcellencePxHint")}
                    value={form?.cover_excellence_px ?? "0"}
                    isDisabled={busy}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, cover_excellence_px: v });
                      const px = Number.parseInt(v, 10);
                      if (Number.isNaN(px) || px < 0 || px > 10000) return;
                      patchFieldDebounced("cover_excellence_px", {
                        cover_excellence_px: px,
                      });
                    }}
                    classNames={{ input: "font-mono", label: "truncate" }}
                  />
                  <Input
                    size="sm"
                    type="number"
                    min={1}
                    max={365}
                    step={1}
                    label={t("settings.coverProbeFreshDays")}
                    title={t("settings.coverProbeFreshDaysHint")}
                    value={form?.cover_probe_fresh_days ?? "7"}
                    isDisabled={busy}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, cover_probe_fresh_days: v });
                      const days = Number.parseInt(v, 10);
                      if (Number.isNaN(days) || days < 1 || days > 365) return;
                      patchFieldDebounced("cover_probe_fresh_days", {
                        cover_probe_fresh_days: days,
                      });
                    }}
                    classNames={{ input: "font-mono", label: "truncate" }}
                  />
                <Input
                  size="sm"
                  type="number"
                  min={1}
                  max={365}
                  step={1}
                  label={t("settings.coverDownloadFreshDays")}
                  title={t("settings.coverDownloadFreshDaysHint")}
                  value={form?.cover_download_fresh_days ?? "30"}
                  isDisabled={busy}
                  onValueChange={(v) => {
                    if (!form) return;
                    setForm({ ...form, cover_download_fresh_days: v });
                    const days = Number.parseInt(v, 10);
                    if (Number.isNaN(days) || days < 1 || days > 365) return;
                    patchFieldDebounced("cover_download_fresh_days", {
                      cover_download_fresh_days: days,
                    });
                  }}
                  classNames={{ input: "font-mono", label: "truncate" }}
                />
                </div>
                <Button
                  size="sm"
                  variant="flat"
                  className="w-full"
                  title={t("settings.clearScrapeCooldownsHint")}
                  isDisabled={busy || clearingCooldowns}
                  isLoading={clearingCooldowns}
                  onPress={() => {
                    void handleClearScrapeCooldowns();
                  }}
                >
                  {t("settings.clearScrapeCooldowns")}
                </Button>
              </SettingsDisclosure>
            </section>

            <Divider />

            <section className="flex flex-col gap-4">
              <div className="flex items-center gap-2">
                <PaletteIcon className="text-foreground-500 h-4 w-4" />
                <h3 className="text-sm font-semibold">
                  {t("settings.sectionAccess")}
                </h3>
              </div>

              <div className="flex flex-col gap-3">
                <p className="text-foreground-500 text-sm font-medium">
                  {t("settings.sectionTelegram")}
                </p>
                <p className="text-foreground-400 text-xs text-pretty">
                  {settings?.telegram_bot_running
                    ? t("settings.telegramRunning", {
                        api:
                          settings.telegram_api_url ||
                          t("settings.telegramOfficialApi"),
                      })
                    : t("settings.telegramStopped")}
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <Input
                    size="sm"
                    type="password"
                    label={t("settings.telegramBotToken")}
                    title={t("settings.telegramBotTokenHint")}
                    value={form?.telegram_bot_token ?? ""}
                    isDisabled={busy}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, telegram_bot_token: v });
                      patchFieldDebounced("telegram_bot_token", {
                        telegram_bot_token: v.trim(),
                      });
                    }}
                  />
                  <Input
                    size="sm"
                    label={t("settings.telegramAdminIds")}
                    title={t("settings.telegramAdminIdsHint")}
                    value={form?.telegram_admin_ids ?? ""}
                    isDisabled={busy}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, telegram_admin_ids: v });
                      patchFieldDebounced("telegram_admin_ids", {
                        telegram_admin_ids: v.trim(),
                      });
                    }}
                    classNames={{ input: "font-mono" }}
                  />
                  <Input
                    size="sm"
                    label={t("settings.telegramUserIds")}
                    title={t("settings.telegramUserIdsHint")}
                    value={form?.telegram_user_ids ?? ""}
                    isDisabled={busy}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, telegram_user_ids: v });
                      patchFieldDebounced("telegram_user_ids", {
                        telegram_user_ids: v.trim(),
                      });
                    }}
                    classNames={{ input: "font-mono" }}
                  />
                  <Input
                    size="sm"
                    type="number"
                    min={1}
                    max={1000}
                    label={t("settings.telegramDailyLimit")}
                    title={t("settings.telegramDailyLimitHint")}
                    value={form?.telegram_daily_limit ?? "5"}
                    isDisabled={busy}
                    onValueChange={(v) => {
                      if (!form) return;
                      setForm({ ...form, telegram_daily_limit: v });
                      const n = Number.parseInt(v, 10);
                      if (!Number.isFinite(n) || n < 1) return;
                      patchFieldDebounced("telegram_daily_limit", {
                        telegram_daily_limit: n,
                      });
                    }}
                  />
                </div>
              </div>

              <div className="flex flex-col gap-3">
                <div className="flex items-center gap-2">
                  <CookieIcon className="text-foreground-500 h-4 w-4" />
                  <p className="text-foreground-500 text-sm font-medium">
                    {t("cookies.options")}
                  </p>
                </div>
                <p className="text-foreground-500 text-xs text-pretty">
                  {!cookiesConfigured
                    ? t("cookies.uploadTooltip")
                    : cookiesStatus.status === "expired"
                      ? t("cookies.statusExpired")
                      : cookiesStatus.status === "incomplete"
                        ? t("cookies.statusIncomplete")
                        : cookiesStatus.status === "expiring_soon"
                          ? t("cookies.statusExpiringSoon", {
                              days: cookiesStatus.days_remaining ?? 0,
                            })
                          : cookiesStatus.days_remaining != null
                            ? t("cookies.statusOk", {
                                days: cookiesStatus.days_remaining,
                              })
                            : t("cookies.statusOkNoExpiry")}
                </p>
                {cookiesConfigured &&
                (cookiesStatus.status === "expired" ||
                  cookiesStatus.status === "incomplete" ||
                  cookiesStatus.status === "expiring_soon") ? (
                  <p className="text-warning text-xs">
                    {t("cookies.staleHint")}
                  </p>
                ) : null}
                <div
                  className={`grid w-full gap-2 ${cookiesConfigured ? "grid-cols-2" : "grid-cols-1"}`}
                >
                  <Button
                    size="sm"
                    variant="flat"
                    className="w-full"
                    isLoading={isUploading}
                    startContent={<UploadIcon className="h-4 w-4" />}
                    onPress={triggerFileUpload}
                  >
                    {cookiesConfigured
                      ? t("cookies.uploadNew")
                      : t("cookies.upload")}
                  </Button>
                  {cookiesConfigured ? (
                    <Button
                      size="sm"
                      variant="flat"
                      color="danger"
                      className="w-full"
                      isLoading={isDeleting}
                      startContent={<Trash2Icon className="h-4 w-4" />}
                      onPress={() => handleDropdownAction("delete")}
                    >
                      {t("cookies.delete")}
                    </Button>
                  ) : null}
                </div>
              </div>

              <div className="flex flex-col gap-3">
                <p className="text-foreground-500 text-sm font-medium">
                  {t("settings.appearance")}
                </p>
                <div className="flex items-center justify-between">
                  <span className="text-foreground-500 text-sm">
                    {t("nav.switchLanguage")}
                  </span>
                  <LanguageToggler />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-foreground-500 text-sm">
                    {t("nav.toggleTheme")}
                  </span>
                  <AnimatedThemeToggler />
                </div>
              </div>

              <div
                className={`grid w-full gap-2 ${authStatus.enabled && authStatus.authenticated ? "grid-cols-2" : "grid-cols-1"}`}
              >
                <Button
                  variant="flat"
                  className="w-full"
                  isLoading={resetting}
                  isDisabled={busy}
                  startContent={<RotateCcwIcon className="h-4 w-4" />}
                  onPress={openReset}
                >
                  {t("settings.factoryCenter")}
                </Button>
                {authStatus.enabled && authStatus.authenticated ? (
                  <Button
                    color="danger"
                    variant="flat"
                    className="w-full"
                    startContent={<LogOutIcon className="h-4 w-4" />}
                    onPress={() => {
                      void logout();
                      onOpenChange(false);
                    }}
                  >
                    {t("auth.logout")}
                  </Button>
                ) : null}
              </div>
            </section>
          </DrawerBody>
        </DrawerContent>
      </Drawer>

      <Modal
        isOpen={confirmResetOpen}
        onOpenChange={(open) => {
          if (!resetting) setConfirmResetOpen(open);
        }}
        placement="center"
        isDismissable={!resetting}
      >
        <ModalContent>
          {(onClose) => (
            <>
              <ModalHeader>
                {resetStage === "select"
                  ? t("settings.factoryCenter")
                  : resetStage === "warning"
                    ? t("settings.factoryFirstConfirm")
                    : t("settings.factoryFinalConfirm")}
              </ModalHeader>
              <ModalBody className="gap-3 text-sm">
                {resetStage === "select" ? (
                  <div className="flex flex-col gap-2">
                    {(
                      ["preferences", "invalid", "full"] as FactoryResetMode[]
                    ).map((mode, index) => (
                      <Button
                        key={mode}
                        variant="flat"
                        color={mode === "full" ? "danger" : "default"}
                        className="h-auto justify-start px-4 py-3 text-left"
                        isLoading={resetPreviewing}
                        isDisabled={resetPreviewing}
                        onPress={() => {
                          void selectResetMode(mode);
                        }}
                      >
                        <span className="flex flex-col items-start gap-1">
                          <span className="font-medium">
                            {index + 1}.{" "}
                            {t(`settings.factoryMode.${mode}.title`)}
                          </span>
                          <span className="text-foreground-500 text-xs whitespace-normal">
                            {t(`settings.factoryMode.${mode}.summary`)}
                          </span>
                        </span>
                      </Button>
                    ))}
                  </div>
                ) : resetPreview ? (
                  <>
                    <p className="font-medium">
                      {t(
                        `settings.factoryMode.${resetPreview.mode}.${
                          resetStage === "warning" ? "warning" : "confirm"
                        }`,
                      )}
                    </p>
                    {resetPreview.mode !== "preferences" ? (
                      <div className="bg-default-100 rounded-medium px-3 py-2 text-xs">
                        <p>{resetStats}</p>
                        {resetPreview.mode === "full" ? (
                          <p className="text-danger mt-1">
                            {t("settings.factoryBackups", {
                              count: resetPreview.backups,
                            })}
                          </p>
                        ) : null}
                      </div>
                    ) : null}
                    {resetStage === "confirm" &&
                    resetPreview.mode === "full" &&
                    authStatus.enabled ? (
                      <Input
                        type="password"
                        autoFocus
                        label={t("settings.factoryPassword")}
                        value={resetPassword}
                        onValueChange={setResetPassword}
                        isDisabled={resetting}
                      />
                    ) : null}
                  </>
                ) : null}
              </ModalBody>
              <ModalFooter>
                <Button
                  variant="light"
                  onPress={() => {
                    if (resetStage === "select") {
                      onClose();
                    } else {
                      setResetStage(
                        resetStage === "confirm" &&
                          resetPreview?.mode !== "preferences"
                          ? "warning"
                          : "select",
                      );
                    }
                  }}
                  isDisabled={resetting}
                >
                  {resetStage === "select"
                    ? t("sync.cancel")
                    : t("settings.factoryBack")}
                </Button>
                {resetStage === "warning" ? (
                  <Button
                    color="danger"
                    onPress={() => setResetStage("confirm")}
                  >
                    {t("settings.factoryContinue")}
                  </Button>
                ) : resetStage === "confirm" ? (
                  <Button
                    color={
                      resetPreview?.mode === "preferences"
                        ? "primary"
                        : "danger"
                    }
                    isLoading={resetting}
                    isDisabled={
                      resetPreview?.mode === "full" &&
                      authStatus.enabled &&
                      !resetPassword
                    }
                    onPress={() => {
                      void handleReset();
                    }}
                  >
                    {t(
                      resetPreview?.mode === "full"
                        ? "settings.factoryDeleteAll"
                        : "settings.factoryExecute",
                    )}
                  </Button>
                ) : null}
              </ModalFooter>
            </>
          )}
        </ModalContent>
      </Modal>

      <Modal
        isOpen={reclaimTarget !== null}
        onOpenChange={(open) => {
          if (!open && !reclaiming) setReclaimTarget(null);
        }}
        placement="center"
      >
        <ModalContent>
          {(onClose) => (
            <>
              <ModalHeader>{t("settings.reclaimPitsTitle")}</ModalHeader>
              <ModalBody className="gap-2 text-sm">
                <p>
                  {reclaimTarget === "delete"
                    ? t("settings.reclaimConfirmDelete")
                    : reclaimTarget === "default"
                      ? t("settings.reclaimConfirmDefault")
                      : t("settings.reclaimConfirmBoth")}
                </p>
              </ModalBody>
              <ModalFooter>
                <Button
                  variant="light"
                  onPress={onClose}
                  isDisabled={reclaiming}
                >
                  {t("sync.cancel")}
                </Button>
                <Button
                  color="danger"
                  isLoading={reclaiming}
                  onPress={() => {
                    void handleReclaimPits();
                  }}
                >
                  {t("settings.reclaimPitsTitle")}
                </Button>
              </ModalFooter>
            </>
          )}
        </ModalContent>
      </Modal>

      <input
        ref={fileInputRef}
        type="file"
        accept=".txt"
        onChange={handleFileSelect}
        className="hidden"
      />
    </>
  );
}
