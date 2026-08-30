import { cachedLocale, setDocumentLocale, translate } from "../i18n";
import { mountRoot } from "../mount";
import { Help } from "./Help";
import "../styles/tokens.css";
import "../styles/document.css";
import "./help.css";

const locale = cachedLocale();
setDocumentLocale(locale);
document.title = `Lumi — ${translate(locale, "help.title")}`;

mountRoot(<Help />);
