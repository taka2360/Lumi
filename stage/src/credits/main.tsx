import { cachedLocale, setDocumentLocale } from "../i18n";
import { mountRoot } from "../mount";
import { Credits } from "./Credits";
import "../styles/tokens.css";
import "../styles/document.css";
import "./credits.css";

setDocumentLocale(cachedLocale());

mountRoot(<Credits />);
