/**
 * The one question that covers everything missing, with the total (ADR-034 / setup.md).
 *
 * **Asked before the per-component questions.** Four prompts with four numbers make the
 * number that actually decides it — the total — the one nobody is ever shown.
 *
 * The itemisation is not decoration: **the sizes line up in a column** so the total above
 * them can be checked at a glance.
 */

import type { SetupPrompt } from "../../core/store";
import { answerSetupPrompt } from "../../core/useCoreConnection";
import { type Locale, translate } from "../../i18n";
import { formatSize } from "../format";

export function BulkFetchPrompt({ prompt, locale }: { prompt: SetupPrompt; locale: Locale }) {
  const total = formatSize(prompt.totalBytes, locale);
  return (
    <div className="panel panel--model-prompt">
      <h1 className="panel__title">{translate(locale, "setup.prompt.all.title")}</h1>
      <p className="panel__body">{translate(locale, "setup.prompt.all.body", { size: total })}</p>
      <ul className="panel__fetch-list">
        {prompt.items.map((item) => (
          <li key={item.component}>
            {/* A product name. **Never translated** (docs/architecture/ui.md §6b). */}
            <span className="panel__fetch-name">{item.name}</span>
            <span className="panel__fetch-size">{formatSize(item.sizeBytes, locale)}</span>
          </li>
        ))}
      </ul>
      <p className="panel__note">{translate(locale, "setup.prompt.all.note")}</p>
      <div className="panel__model-options">
        <button
          type="button"
          className="panel__button"
          onClick={() => answerSetupPrompt("install")}
        >
          {translate(locale, "setup.prompt.all.install", { size: total })}
        </button>
        <button
          type="button"
          className="panel__button"
          onClick={() => answerSetupPrompt("individually")}
        >
          {translate(locale, "setup.prompt.all.individually")}
        </button>
        <button type="button" className="panel__button" onClick={() => answerSetupPrompt("skip")}>
          {translate(locale, "setup.skip")}
        </button>
      </div>
    </div>
  );
}
