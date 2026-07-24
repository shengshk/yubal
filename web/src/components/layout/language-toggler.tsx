import { Button } from "@heroui/react";
import { useTranslation } from "react-i18next";
import {
  localeBadge,
  localeTitle,
  resolveAppLocale,
  toggleAppLocale,
} from "@/i18n";

export function LanguageToggler() {
  const { t, i18n } = useTranslation();
  const locale = resolveAppLocale(i18n.language);

  return (
    <Button
      isIconOnly
      size="sm"
      variant="light"
      radius="lg"
      aria-label={t("nav.switchLanguage")}
      title={localeTitle(locale)}
      onPress={() => toggleAppLocale()}
      className="text-foreground-500 h-8 min-w-8 w-8 text-xs font-semibold"
    >
      {localeBadge(locale)}
    </Button>
  );
}
