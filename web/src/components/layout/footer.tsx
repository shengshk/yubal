import { GithubIcon, KofiIcon } from "@/components/icons";
import { Trans, useTranslation } from "react-i18next";

export function Footer() {
  const { t } = useTranslation();

  return (
    <footer className="mx-auto max-w-5xl px-4 py-6">
      <div className="flex flex-col items-center gap-2 text-center">
        <p className="text-foreground-500 font-mono text-xs">
          Fork{" "}
          <a
            href="https://github.com/shengshk/yubal"
            target="_blank"
            rel="noopener noreferrer"
            className="group text-primary hover:text-foreground"
          >
            <GithubIcon className="-mt-px inline h-4 w-4" />{" "}
            <span className="group-hover:underline">shengshk</span>
          </a>
          {" · "}
          {t("footer.madeBy")}{" "}
          <a
            href="https://github.com/guillevc"
            target="_blank"
            rel="noopener noreferrer"
            className="group text-primary hover:text-foreground"
          >
            <GithubIcon className="-mt-px inline h-4 w-4" />{" "}
            <span className="group-hover:underline">guillevc</span>
          </a>
          {" · "}
          <Trans
            i18nKey="footer.supportVia"
            components={{
              bold: <strong className="font-semibold text-foreground" />,
            }}
          />{" "}
          <a
            href="https://ko-fi.com/guillevc"
            target="_blank"
            rel="noopener noreferrer"
            className="group text-primary hover:text-[#FF5E5B]"
          >
            <KofiIcon className="-mt-px inline h-4 w-4" />{" "}
            <span className="group-hover:underline">Ko-fi</span>
          </a>
        </p>
        <p className="text-foreground-400 font-mono text-xs">
          {t("footer.poweredBy")}{" "}
          <a
            href="https://github.com/yt-dlp/yt-dlp"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground hover:underline"
          >
            yt-dlp
          </a>
          {" & "}
          <a
            href="https://github.com/sigma67/ytmusicapi"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground hover:underline"
          >
            ytmusicapi
          </a>
          {" · "}
          <a
            href={`https://github.com/guillevc/yubal/${__IS_RELEASE__ ? `releases/tag/${__VERSION__}` : `commit/${__COMMIT_SHA__}`}`}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground hover:underline"
          >
            {__VERSION__}
          </a>
        </p>
      </div>
    </footer>
  );
}
