import { updateSettings } from "@/api/settings";
import { HoverHint } from "@/components/common/hover-hint";
import {
  Button,
  Input,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Switch,
} from "@heroui/react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

export type WantedEditValues = {
  auto_match_enabled: boolean;
  max_items: number;
  sync_jitter_seconds: number;
};

type Props = {
  isOpen: boolean;
  initial?: Partial<WantedEditValues> | null;
  isSchedulerEnabled?: boolean;
  onClose: () => void;
  onSaved: () => void;
};

export function WantedEditModal({
  isOpen,
  initial,
  isSchedulerEnabled = true,
  onClose,
  onSaved,
}: Props) {
  const { t } = useTranslation();
  const [autoMatch, setAutoMatch] = useState(true);
  const [maxItems, setMaxItems] = useState(50);
  const [jitterSeconds, setJitterSeconds] = useState(600);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setAutoMatch(initial?.auto_match_enabled ?? true);
    setMaxItems(initial?.max_items ?? 50);
    setJitterSeconds(initial?.sync_jitter_seconds ?? 600);
  }, [isOpen, initial]);

  const handleSave = async () => {
    setSaving(true);
    const result = await updateSettings({
      wanted_auto_match_enabled: autoMatch,
      wanted_max_items: maxItems >= 1 ? maxItems : 50,
      wanted_sync_jitter_seconds: Math.min(600, Math.max(0, jitterSeconds)),
    });
    setSaving(false);
    if ("error" in result) return;
    window.dispatchEvent(new Event("yubal:settings-changed"));
    onSaved();
    onClose();
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      placement="center"
      size="lg"
      scrollBehavior="inside"
      classNames={{
        closeButton:
          "border-none outline-none ring-0 shadow-none hover:bg-default-100 data-[focus-visible=true]:outline-none data-[focus-visible=true]:ring-0",
      }}
    >
      <ModalContent>
        <ModalHeader>{t("sync.editWantedTitle")}</ModalHeader>
        <ModalBody className="gap-4">
          <HoverHint content={t("sync.editWantedAutoMatchHint")}>
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm">{t("settings.wantedAutoMatch")}</p>
              <Switch
                size="sm"
                isSelected={autoMatch}
                isDisabled={!isSchedulerEnabled || saving}
                onValueChange={setAutoMatch}
              />
            </div>
          </HoverHint>
          {!isSchedulerEnabled ? (
            <p className="text-warning text-xs">
              {t("sync.statusGlobalStopped")}
            </p>
          ) : null}
          <div className="grid grid-cols-2 gap-2">
            <HoverHint content={t("settings.wantedMaxItemsHint")}>
              <Input
                size="sm"
                type="number"
                min={1}
                max={10000}
                step={1}
                label={t("settings.wantedMaxItems")}
                value={String(maxItems)}
                isDisabled={saving}
                onValueChange={(v) => {
                  const n = Number.parseInt(v, 10);
                  if (!Number.isNaN(n)) setMaxItems(n);
                }}
                classNames={{ input: "font-mono" }}
              />
            </HoverHint>
            <HoverHint content={t("settings.wantedSyncJitterHint")}>
              <Input
                size="sm"
                type="number"
                min={0}
                max={600}
                step={1}
                label={t("settings.wantedSyncJitter")}
                value={String(jitterSeconds)}
                isDisabled={saving}
                onValueChange={(v) => {
                  const n = Number.parseInt(v, 10);
                  if (!Number.isNaN(n)) setJitterSeconds(n);
                }}
                endContent={
                  <span className="text-foreground-400 text-[10px]">
                    {t("settings.seconds")}
                  </span>
                }
                classNames={{ input: "font-mono" }}
              />
            </HoverHint>
          </div>
        </ModalBody>
        <ModalFooter>
          <Button variant="light" isDisabled={saving} onPress={onClose}>
            {t("sync.cancel")}
          </Button>
          <Button
            color="primary"
            isLoading={saving}
            onPress={() => {
              void handleSave();
            }}
          >
            {t("common.save")}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
