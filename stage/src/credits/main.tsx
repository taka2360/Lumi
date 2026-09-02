import { cachedLocale, setDocumentLocale, translate } from "../i18n";
import { mountRoot } from "../mount";
import { Credits } from "./Credits";
import "../styles/tokens.css";
import "../styles/document.css";
import "./credits.css";

const locale = cachedLocale();
setDocumentLocale(locale);
// **The title is wording too.** It was fixed English in `credits.html`, so the one part of
// this window the user saw before anything rendered ignored their language setting.
document.title = `Lumi — ${translate(locale, "credits.title")}`;

mountRoot(<Credits />);
