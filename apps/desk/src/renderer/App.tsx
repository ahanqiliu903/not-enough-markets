import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Shell } from "./shell/Shell";
import { useSettings } from "./state/settings";
import { applyTheme } from "./theme";

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
    applyTheme(document.documentElement.style, fontSize);
  }, [fontSize]);
  useEffect(() => {
    document.documentElement.dataset["pixel"] = pixel ? "on" : "off";
  }, [pixel]);
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
