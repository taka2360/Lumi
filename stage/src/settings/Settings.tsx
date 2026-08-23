/**
 * Settings (roadmap open item #9 / ADR-028).
 *
 * Design → docs/architecture/core.md §6b / docs/architecture/ui.md §2
 *
 * **Core owns the values; the Stage shows them and asks.** Every value here was broadcast
 * by Core, and a change is a `request` Core is free to refuse — the Stage decides nothing.
 *
 * **Where each value came from is shown.** A setting an environment variable is overriding,
 * without saying so, turns "I changed it and nothing happened" into something nobody can
 * explain. A value being overridden is **not editable**, because saving it would write a
 * temporary escape hatch into the file permanently.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { updateSettings } from "../core/request";
import type { SettingsSource } from "../core/store";
import { useStageStore } from "../core/store";
import { translate } from "../i18n";
import { useLocale } from "../i18n/provider";

/** Values with a fixed set of choices. Anything else is free text (model names vary). */
const CHOICES: Record<string, string[]> = {
  inference_device: ["auto", "cuda", "cpu"],
  locale: ["auto", "ja", "en"],
};

const TTS_SPEED_MIN = 0.5;
const TTS_SPEED_MAX = 2.0;
const TTS_SPEED_STEP = 0.1;

function formatSpeed(value: string): string {
  const speed = Number(value);
  return Number.isFinite(speed) ? `${speed.toFixed(1)}x` : value;
}

function Row({ name, value, source }: { name: string; value: string; source: SettingsSource }) {
  const locale = useLocale();
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const snapshot = useMemo(() => ({ value, source }), [value, source]);
  const lastCommitted = useRef(value);
  const currentSnapshot = useRef(snapshot);
  const latestRequestId = useRef(0);
  useEffect(() => {
    // Core owns the value. A new snapshot supersedes any local draft or status.
    currentSnapshot.current = snapshot;
    setDraft(snapshot.value);
    lastCommitted.current = snapshot.value;
    setError(null);
    setSaved(false);
  }, [snapshot]);
  // **Never editable while overridden.** Saving would make the escape hatch permanent
  const locked = source === "env";

  const commit = async (next: string) => {
    if (next === lastCommitted.current) {
      return;
    }
    const requestId = ++latestRequestId.current;
    const requestSnapshot = currentSnapshot.current;
    lastCommitted.current = next;
    setDraft(next);
    setError(null);
    setSaved(false);
    try {
      await updateSettings({ [name]: next });
      if (
        requestId !== latestRequestId.current ||
        requestSnapshot !== currentSnapshot.current ||
        lastCommitted.current !== next
      ) {
        return;
      }
      setSaved(true);
    } catch (failure: unknown) {
      // A newer request or Core snapshot supersedes this result. Do not let an older
      // rejection restore a value that is no longer current.
      if (
        requestId !== latestRequestId.current ||
        requestSnapshot !== currentSnapshot.current ||
        lastCommitted.current !== next
      ) {
        return;
      }
      // **The reason Core gave, shown as-is.** Never "failed to save" with nothing else
      setError(failure instanceof Error ? failure.message : translate(locale, "settings.refused"));
      lastCommitted.current = value;
      setDraft(value);
    }
  };

  const choices = CHOICES[name];
  const isSpeed = name === "tts_speed";

  return (
    <tr>
      <th>
        {name === "inference_device" ||
        name === "llm_model" ||
        name === "stt_model" ||
        name === "locale" ||
        name === "tts_speed"
          ? translate(locale, `settings.label.${name}`)
          : name}
      </th>
      <td className="settings__value">
        {locked ? (
          value
        ) : isSpeed ? (
          <div className="settings__speed">
            <input
              className="settings__input settings__speed-input"
              type="range"
              min={TTS_SPEED_MIN}
              max={TTS_SPEED_MAX}
              step={TTS_SPEED_STEP}
              value={draft}
              aria-label={translate(locale, "settings.label.tts_speed")}
              onChange={(event) => setDraft(event.target.value)}
              onPointerUp={(event) => void commit(event.currentTarget.value)}
              onKeyUp={(event) => void commit(event.currentTarget.value)}
              onBlur={() => void commit(draft)}
            />
            <output>{formatSpeed(draft)}</output>
          </div>
        ) : choices ? (
          <select
            className="settings__input"
            value={draft}
            onChange={(event) => void commit(event.target.value)}
          >
            {choices.map((choice) => (
              <option key={choice} value={choice}>
                {name === "locale" && (choice === "auto" || choice === "ja" || choice === "en")
                  ? translate(locale, `settings.choice.${choice}`)
                  : choice}
              </option>
            ))}
          </select>
        ) : (
          <input
            className="settings__input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => draft !== value && void commit(draft)}
          />
        )}
      </td>
      <td className={locked ? "settings__src settings__src--env" : "settings__src"}>
        {error ??
          (saved
            ? translate(
                locale,
                name === "locale" || name === "tts_speed" ? "settings.applied" : "settings.saved",
              )
            : translate(locale, `settings.source.${source}`))}
      </td>
    </tr>
  );
}

/**
 * The settings window's content (ADR-042).
 *
 * **Nothing is shown until Core has said what the values are.** Rendering an empty table
 * first and filling it in would make "not connected yet" look like "no settings", and
 * every field would appear to have been cleared.
 */
export function Settings() {
  const locale = useLocale();
  const settings = useStageStore((state) => state.settings);

  if (!settings) {
    return <p className="inspect__empty">{translate(locale, "settings.waiting")}</p>;
  }

  return (
    <div className="settings">
      {settings.unreadable && (
        // **Core refuses to save over it**, and says so rather than quietly running
        // on defaults and destroying what the user meant.
        <p className="panel__status panel__status--bad">
          {translate(locale, "settings.unreadable")}
        </p>
      )}
      <table className="settings__table">
        <tbody>
          {Object.entries(settings.values).map(([name, setting]) => (
            <Row key={name} name={name} value={setting.value} source={setting.source} />
          ))}
        </tbody>
      </table>
      <p className="inspect__empty">{translate(locale, "settings.restart")}</p>
    </div>
  );
}
