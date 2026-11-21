import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Shell } from "./shell/Shell";
import { useSettings } from "./state/settings";
import { applyTheme, fonts } from "./theme";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      // Engine output is files on disk; refetch on demand (r) rather than on focus.
      queries: { refetchOnWindowFocus: false, retry: 1, staleTime: 30_000 },
    },
  });
}

/** Keeps the CSS custom properties and the pixel-accent switch in sync with settings. */
function useThemeSync(): void {
  const fontSize = useSettings((s) => s.fontSize);
  const pixel = useSettings((s) => s.pixelAccents);
  useEffect(() => {
    const root = document.documentElement;
    applyTheme(root.style, fontSize);
    root.dataset["pixel"] = pixel ? "on" : "off";
    // Accents off: the pixel font token itself becomes mono, so nothing can render
    // Silkscreen even outside `.pixel-font`.
    root.style.setProperty("--font-pixel", pixel ? fonts.pixel : fonts.mono);
  }, [fontSize, pixel]);
}

export function App({ client }: { client?: QueryClient }) {
  const [queryClient] = useState(() => client ?? makeQueryClient());
  useThemeSync();
  return (
    <QueryClientProvider client={queryClient}>
      <Shell />
    </QueryClientProvider>
  );
}
