import { createContext, type ReactNode, useContext, useEffect } from "react";

import { useStageStore } from "../core/store";
import { getPlatformShell } from "../platform/useStageShell";
import {
  browserLocale,
  cachedLocale,
  cacheLocale,
  type Locale,
  resolveConfiguredLocale,
  setDocumentLocale,
} from "./index";

const LocaleContext = createContext<Locale | null>(null);

export function LocaleProvider({ children }: { children: ReactNode }) {
  const setting = useStageStore((state) => state.settings?.values.locale?.value ?? null);
  const locale =
    setting === null ? cachedLocale() : resolveConfiguredLocale(setting, browserLocale());
  const shellLocale = setting === null ? null : locale;

  useEffect(() => {
    cacheLocale(locale);
    setDocumentLocale(locale);
  }, [locale]);

  useEffect(() => {
    // Wait for Core's state. This also avoids racing Shell's tray creation during boot.
    if (shellLocale !== null) void getPlatformShell().setLocale(shellLocale);
  }, [shellLocale]);

  return <LocaleContext.Provider value={locale}>{children}</LocaleContext.Provider>;
}

export function useLocale(): Locale {
  return useContext(LocaleContext) ?? cachedLocale();
}
