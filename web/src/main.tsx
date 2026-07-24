import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ErrorBoundary } from "./components/common/error-boundary";
import { AuthProvider, useAuth } from "./features/auth/auth-context";
import { LoginOverlay } from "./features/auth/login-overlay";
import { JobsProvider } from "./features/jobs/jobs-context";
import { ThemeProvider } from "./hooks/use-theme";
import "./i18n";
import "./index.css";
import { router } from "./router";

function AppShell() {
  const { ready, status } = useAuth();

  if (!ready) {
    return (
      <div className="bg-background text-foreground-500 grid min-h-screen place-items-center font-mono text-sm">
        …
      </div>
    );
  }

  const showApp = !status.enabled || status.authenticated;

  return (
    <>
      <LoginOverlay />
      {showApp && (
        <JobsProvider>
          <RouterProvider router={router} />
        </JobsProvider>
      )}
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <ThemeProvider>
        <AuthProvider>
          <main className="text-foreground">
            <AppShell />
          </main>
        </AuthProvider>
      </ThemeProvider>
    </ErrorBoundary>
  </StrictMode>,
);

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker
      .register("./sw.js", { scope: "./" })
      .catch(() => {
        // ignore SW registration failures
      });
  });
}
