import { useAuth } from "@/features/auth/auth-context";
import { localeBadge, localeTitle, resolveAppLocale, toggleAppLocale } from "@/i18n";
import { Button, Checkbox, Input } from "@heroui/react";
import { FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

export function LoginOverlay() {
  const { t, i18n } = useTranslation();
  const { status, login, setup } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const isSetup = status.needsSetup;
  const locale = resolveAppLocale(i18n.language);

  if (!status.enabled || status.authenticated) return null;
  if (status.setupLocked) {
    return (
      <div className="bg-background fixed inset-0 z-[100] grid place-items-center p-6">
        <div className="border-default-200 bg-content1 w-full max-w-[420px] rounded-xl border p-6">
          <h1 className="text-foreground text-xl font-bold">
            {t("auth.setupLockedTitle")}
          </h1>
          <p className="text-foreground-500 mt-2 text-sm">
            {t("auth.setupLockedDesc")}
          </p>
        </div>
      </div>
    );
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const err = isSetup
        ? await setup(username, password, confirm)
        : await login(username, password, remember);
      if (err) setError(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-background fixed inset-0 z-[100] grid place-items-center p-6">
      <form
        onSubmit={onSubmit}
        className="border-default-200 bg-content1 relative flex w-full max-w-[420px] flex-col gap-3 rounded-xl border p-6"
      >
        <button
          type="button"
          className="text-foreground-500 hover:bg-default-100 absolute top-3 right-3 grid h-7 w-7 place-items-center rounded-md text-xs font-bold"
          aria-label={t("nav.switchLanguage")}
          title={localeTitle(locale)}
          onClick={() => toggleAppLocale()}
        >
          {localeBadge(locale)}
        </button>

        <h1 className="text-foreground text-xl font-bold">
          {isSetup ? t("auth.setupTitle") : t("auth.loginTitle")}
        </h1>
        <p className="text-foreground-500 text-sm">
          {isSetup ? t("auth.setupSubtitle") : t("auth.loginSubtitle")}
        </p>

        {isSetup && (
          <div className="rounded-lg border border-warning-300 bg-warning-50 px-3 py-2 text-sm font-medium whitespace-pre-line text-warning-700 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-100">
            {t("auth.setupNote")}
          </div>
        )}

        <Input
          radius="md"
          autoComplete="username"
          placeholder={t("auth.username")}
          value={username}
          onValueChange={setUsername}
          isDisabled={loading}
        />
        <Input
          radius="md"
          type="password"
          autoComplete={isSetup ? "new-password" : "current-password"}
          placeholder={t("auth.password")}
          value={password}
          onValueChange={setPassword}
          isDisabled={loading}
        />
        {isSetup && (
          <Input
            radius="md"
            type="password"
            autoComplete="new-password"
            placeholder={t("auth.confirmPassword")}
            value={confirm}
            onValueChange={setConfirm}
            isDisabled={loading}
          />
        )}

        {!isSetup && (
          <Checkbox
            size="sm"
            isSelected={remember}
            onValueChange={setRemember}
            isDisabled={loading}
          >
            {t("auth.remember")}
          </Checkbox>
        )}

        {error && <p className="text-danger text-sm">{error}</p>}

        <Button
          type="submit"
          color="primary"
          radius="md"
          className="w-full"
          isLoading={loading}
        >
          {isSetup ? t("auth.setupSubmit") : t("auth.loginSubmit")}
        </Button>
      </form>
    </div>
  );
}
