/* eslint-disable react-refresh/only-export-components */

import { Footer } from "@/components/layout/footer";
import { Header } from "@/components/layout/header";
import { LibraryHealthModal } from "@/features/library/library-health-modal";
import { LibraryAudioProvider } from "@/features/sync/library-audio";
import { basePath } from "@/lib/base-path";
import { layout } from "@/lib/ui-styles";
import { JobsPage } from "@/pages/jobs";
import { HeroUIProvider, ToastProvider } from "@heroui/react";
import {
  createRootRoute,
  createRoute,
  createRouter,
  Navigate,
  NavigateOptions,
  Outlet,
  ToOptions,
  useNavigate,
  useRouter,
} from "@tanstack/react-router";
import { useEffect } from "react";

function RootLayout() {
  const router = useRouter();

  return (
    <HeroUIProvider
      navigate={(to, options) => router.navigate({ to, ...options })}
      useHref={(to) => router.buildLocation({ to }).href}
    >
      <ToastProvider />
      <LibraryHealthModal />
      <LibraryAudioProvider>
        <div className="flex min-h-screen flex-col">
          <Header />
          <main className={`m-auto w-full max-w-5xl flex-1 px-4 ${layout.pageY}`}>
            <Outlet />
          </main>
          <Footer />
        </div>
      </LibraryAudioProvider>
    </HeroUIProvider>
  );
}

function NotFoundRedirect() {
  const navigate = useNavigate();
  useEffect(() => {
    navigate({ to: "/" });
  }, [navigate]);
  return null;
}

const rootRoute = createRootRoute({
  component: RootLayout,
  notFoundComponent: NotFoundRedirect,
});

const jobsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: JobsPage,
});

const playlistsRedirectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/playlists",
  component: () => <Navigate to="/" replace />,
});

const routeTree = rootRoute.addChildren([jobsRoute, playlistsRedirectRoute]);

export const router = createRouter({ routeTree, basepath: basePath || "/" });

declare module "@react-types/shared" {
  interface RouterConfig {
    href: ToOptions["to"];
    routerOptions: Omit<NavigateOptions, keyof ToOptions>;
  }
}
