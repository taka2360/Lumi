import { useEffect, useState } from "react";

import { cachedLocale, LOCALE_CACHE_KEY, type Locale, setDocumentLocale } from "./index";

/** Locale for the standalone credits window, which deliberately never connects to Core or Shell. */
export function useStandaloneLocale(): Locale {
  const [locale, setLocale] = useState(cachedLocale);

  useEffect(() => {
    setDocumentLocale(locale);
  }, [locale]);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === LOCALE_CACHE_KEY) setLocale(cachedLocale());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  return locale;
}
