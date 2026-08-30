import { App } from "./App";
import { cachedLocale, setDocumentLocale } from "./i18n";
import { LocaleProvider } from "./i18n/provider";
import { mountRoot } from "./mount";
import "./styles/tokens.css";
import "./styles/stage.css";

setDocumentLocale(cachedLocale());

mountRoot(
  <LocaleProvider>
    <App />
  </LocaleProvider>,
);
