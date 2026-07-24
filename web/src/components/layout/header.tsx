import { SettingsDrawer } from "@/features/settings/settings-drawer";
import { LyricsAdjustLeaveModal } from "@/features/sync/lyrics-adjust";
import { LyricsEditorModal } from "@/features/sync/lyrics-editor-modal";
import {
  CompactLyricsDisplay,
  FullscreenLyrics,
} from "@/features/sync/lyrics-panel";
import { useLibraryAudio } from "@/features/sync/library-audio";
import { useJobs } from "@/features/jobs/jobs-context";
import { useVersionCheck } from "@/hooks/use-version-check";
import { Button, Navbar, NavbarBrand, NavbarContent, NavbarItem } from "@heroui/react";
import { Link } from "@tanstack/react-router";
import { Disc3Icon, RocketIcon, SettingsIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

export function Header() {
  const { t } = useTranslation();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const audio = useLibraryAudio();
  const { data: versionInfo } = useVersionCheck();
  const { hasActiveJobs } = useJobs();

  return (
    <>
      <Navbar
        classNames={{
          wrapper: "max-w-5xl",
          brand: "grow-0",
        }}
      >
        <NavbarBrand>
          <Link to="/" className="flex items-center">
            <Disc3Icon
              className={`text-primary h-7 w-7 ${hasActiveJobs ? "animate-[spin_4s_linear_infinite] motion-reduce:animate-none" : ""}`}
            />
            <p className="text-foreground ml-2 text-xl font-bold">yubal</p>
          </Link>
        </NavbarBrand>

        <NavbarContent
          justify="end"
          className="min-w-0 max-w-[min(28rem,52vw)] grow-0 basis-auto items-center gap-0.5"
        >
          {audio.lyricsHeaderVisible ? (
            <NavbarItem className="min-w-0">
              <CompactLyricsDisplay
                onOpenFullscreen={() => audio.openLyricsFullscreen()}
              />
            </NavbarItem>
          ) : null}
          {versionInfo?.updateAvailable && (
            <NavbarItem className="shrink-0">
              <Button
                as="a"
                disableAnimation
                size="sm"
                href={versionInfo.releaseUrl}
                target="_blank"
                rel="noopener noreferrer"
                variant="flat"
                color="success"
                radius="lg"
                startContent={<RocketIcon className="h-4 w-4" />}
                className="text-small font-mono"
              >
                {versionInfo.latestVersion}
              </Button>
            </NavbarItem>
          )}
          <NavbarItem className="shrink-0">
            <Button
              isIconOnly
              size="sm"
              variant="light"
              radius="lg"
              aria-label={t("settings.title")}
              title={t("settings.title")}
              onPress={() => setSettingsOpen(true)}
              className="h-8 min-w-8 w-8"
            >
              <SettingsIcon className="h-5 w-5" />
            </Button>
          </NavbarItem>
        </NavbarContent>
      </Navbar>

      <SettingsDrawer isOpen={settingsOpen} onOpenChange={setSettingsOpen} />
      <FullscreenLyrics
        open={audio.lyricsFullscreen}
        onClose={() => audio.closeLyricsFullscreen()}
      />
      <LyricsEditorModal />
      <LyricsAdjustLeaveModal />
    </>
  );
}
