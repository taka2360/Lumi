/**
 * Which language model to fetch.
 *
 * Two screens in one: the recommended model, and the list of alternatives behind
 * [choose]. **Which one is showing is this screen's own business** — it was a piece of
 * state on the whole panel, which meant a question about something else could leave the
 * alternatives list open behind it, and every answer had to remember to close it.
 */

import { useState } from "react";

import type { SetupPrompt } from "../../core/store";
import { answerSetupPrompt } from "../../core/useCoreConnection";
import { type Locale, translate } from "../../i18n";
import { formatGigabytes } from "../format";
import { failureText } from "../status";

/** Shown when Core has not named a model — **the wording still has to say something.** */
const FALLBACK_MODEL = "Qwen 3.5 9B";
const FALLBACK_SIZE = "6.6 GB";

export function ModelPrompt({ prompt, locale }: { prompt: SetupPrompt; locale: Locale }) {
  const [showAlternatives, setShowAlternatives] = useState(false);
  const model = prompt.model;

  return (
    <div className="panel panel--model-prompt">
      <h1 className="panel__title">
        {translate(
          locale,
          showAlternatives ? "setup.prompt.model.chooseTitle" : "setup.prompt.model.title",
        )}
      </h1>
      {/* **A retry says so.** "Download" would read as though the first attempt never happened. */}
      {prompt.retry && (
        <p className="panel__status panel__status--bad">{failureText(prompt.reason, locale)}</p>
      )}
      <p className="panel__body">
        {showAlternatives ? (
          translate(locale, "setup.prompt.model.chooseBody")
        ) : (
          <>
            {translate(locale, "setup.prompt.model.body.before")}
            <strong>{model?.display_name ?? FALLBACK_MODEL}</strong>
            {translate(locale, "setup.prompt.model.body.after")}
          </>
        )}
      </p>
      {model && !showAlternatives && (
        <p className="panel__note">
          {translate(locale, "setup.prompt.model.downloadNote", {
            size: formatGigabytes(model.size_bytes, locale),
          })}
        </p>
      )}
      {showAlternatives ? (
        <div className="panel__model-options">
          {prompt.alternatives.map((option) => (
            <button
              type="button"
              className="panel__button"
              key={option.model}
              onClick={() =>
                answerSetupPrompt(option.installed ? "select" : "install", option.model)
              }
            >
              {translate(
                locale,
                option.installed
                  ? "setup.prompt.model.selectNamed"
                  : "setup.prompt.model.downloadNamed",
                {
                  model: option.display_name,
                  size: formatGigabytes(option.size_bytes, locale),
                },
              )}
            </button>
          ))}
          <button
            type="button"
            className="panel__button"
            onClick={() => setShowAlternatives(false)}
          >
            {translate(locale, "setup.prompt.model.back")}
          </button>
        </div>
      ) : (
        <div className="panel__model-options">
          <button
            type="button"
            className="panel__button"
            onClick={() => answerSetupPrompt("install", model?.model)}
          >
            {translate(locale, "setup.prompt.model.downloadNamed", {
              model: model?.display_name ?? FALLBACK_MODEL,
              size: model ? formatGigabytes(model.size_bytes, locale) : FALLBACK_SIZE,
            })}
          </button>
          <button type="button" className="panel__button" onClick={() => setShowAlternatives(true)}>
            {translate(locale, "setup.prompt.model.choose")}
          </button>
          <button type="button" className="panel__button" onClick={() => answerSetupPrompt("skip")}>
            {translate(locale, "setup.skip")}
          </button>
        </div>
      )}
    </div>
  );
}
