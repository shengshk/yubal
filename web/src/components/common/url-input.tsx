import { cardInputWrapper } from "@/lib/ui-styles";
import { YOUTUBE_URL_PATTERN } from "@/lib/url";
import { Input } from "@heroui/react";
import { LinkIcon, SearchIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

type Props = {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: () => void;
  disabled?: boolean;
  placeholder?: string;
  mode?: "url" | "mixed";
};

export function UrlInput({
  value,
  onChange,
  onSubmit,
  disabled,
  placeholder,
  mode = "url",
}: Props) {
  const { t } = useTranslation();
  const isValid =
    mode === "mixed" || value === "" || YOUTUBE_URL_PATTERN.test(value);
  const resolvedPlaceholder = placeholder ?? t("downloads.urlPlaceholder");

  return (
    <Input
      isClearable
      type={mode === "mixed" ? "text" : "url"}
      variant="flat"
      placeholder={resolvedPlaceholder}
      value={value}
      onValueChange={(v) => onChange(mode === "mixed" ? v : v.trim())}
      onKeyDown={(event) => {
        if (
          event.key !== "Enter" ||
          event.nativeEvent.isComposing ||
          !onSubmit
        ) {
          return;
        }
        event.preventDefault();
        onSubmit();
      }}
      isDisabled={disabled}
      isInvalid={!isValid}
      radius="lg"
      errorMessage={!isValid ? t("downloads.invalidUrl") : undefined}
      startContent={
        mode === "mixed" ? (
          <SearchIcon className="text-foreground-400 h-4 w-4" />
        ) : (
          <LinkIcon className="text-foreground-400 h-4 w-4" />
        )
      }
      classNames={{
        inputWrapper: cardInputWrapper,
      }}
    />
  );
}
