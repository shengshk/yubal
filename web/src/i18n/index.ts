import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import zh from "./locales/zh.json";
import zhTW from "./locales/zh-TW.json";

export const LOCALE_STORAGE_KEY = "yubal-locale";
export type AppLocale = "en" | "zh" | "zh-TW";

export const APP_LOCALES: readonly AppLocale[] = ["en", "zh", "zh-TW"];

export function resolveAppLocale(lng: string | undefined | null): AppLocale {
  const raw = (lng ?? "").trim();
  if (raw === "zh-TW" || raw.toLowerCase() === "zh-tw" || raw === "zh-Hant") {
    return "zh-TW";
  }
  if (raw === "zh" || raw.startsWith("zh-CN") || raw.startsWith("zh-Hans")) {
    return "zh";
  }
  if (raw.startsWith("zh")) {
    // zh-HK / other Chinese → Traditional UI
    return "zh-TW";
  }
  return "en";
}

export function localeHtmlLang(locale: AppLocale): string {
  if (locale === "zh") return "zh-CN";
  if (locale === "zh-TW") return "zh-TW";
  return "en";
}

/** Short badge shown on the language toggle button. */
export function localeBadge(locale: AppLocale): string {
  if (locale === "zh") return "简";
  if (locale === "zh-TW") return "繁";
  return "En";
}

export function localeTitle(locale: AppLocale): string {
  if (locale === "zh") return "简体中文";
  if (locale === "zh-TW") return "繁體中文";
  return "English";
}

function detectLocale(): AppLocale {
  try {
    const saved = localStorage.getItem(LOCALE_STORAGE_KEY);
    if (saved === "en" || saved === "zh" || saved === "zh-TW") return saved;
    // Legacy value from older builds
    if (saved === "zh-CN") return "zh";
  } catch {
    // ignore
  }
  return "en";
}

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    zh: { translation: zh },
    "zh-TW": { translation: zhTW },
  },
  lng: detectLocale(),
  fallbackLng: "en",
  interpolation: {
    escapeValue: false,
  },
});

i18n.on("languageChanged", (lng) => {
  const locale = resolveAppLocale(lng);
  document.documentElement.lang = localeHtmlLang(locale);
  document.title = i18n.t("meta.title");
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // ignore
  }
});

const initialLocale = resolveAppLocale(i18n.language);
document.documentElement.lang = localeHtmlLang(initialLocale);
document.title = i18n.t("meta.title");

export default i18n;

export function setAppLocale(locale: AppLocale): void {
  void i18n.changeLanguage(locale);
}

export function toggleAppLocale(): AppLocale {
  const current = resolveAppLocale(i18n.language);
  const idx = APP_LOCALES.indexOf(current);
  const next = APP_LOCALES[(idx + 1) % APP_LOCALES.length]!;
  setAppLocale(next);
  return next;
}
