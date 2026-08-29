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

/**
 * The settings shown as a slider, with the range Core validates against.
 *
 * **`tts_volume` is a multiplier over the Content Pack's own volume** (ADR-046), which is
 * why it is shown as a percentage and why 100% is the default: it is exactly the volume
 * the character was authored with.
 */
const SLIDERS: Partial<
  Record<string, { min: number; max: number; step: number; format: (v: number) => string }>
> = {
  tts_speed: { min: 0.5, max: 2.0, step: 0.1, format: (v) => `${v.toFixed(1)}x` },
  tts_volume: { min: 0.0, max: 2.0, step: 0.05, format: (v) => `${Math.round(v * 100)}%` },
};

/**
 * Settings that have a translated label. **Kept as literals**, so the message key stays
 * type-checked — a setting added here without a string in `i18n` fails to compile rather
 * than showing its raw key to the user.
 */
const LABELLED = [
  "inference_device",
  "llm_model",
  "stt_model",
  "locale",
  "tts_speed",
  "tts_volume",
] as const;

type Labelled = (typeof LABELLED)[number];

function isLabelled(name: string): name is Labelled {
  return (LABELLED as readonly string[]).includes(name);
}

/** Changes a running Lumi picks up without a restart (Core decides; this only words it). */
const APPLIED_NOW: ReadonlySet<string> = new Set(["locale", "tts_speed", "tts_volume"]);

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
  const slider = SLIDERS[name];

  const label = isLabelled(name) ? translate(locale, `settings.label.${name}`) : null;

  /**
   * The value as the user reads it. **One formatting for both branches** — an overridden
   * setting printing its raw number would read as a different quantity than the same
   * setting unlocked ("1.5" against "150%"), which is exactly the multiplier-for-level
   * confusion ADR-046 exists to prevent. A value Core sent that is not a number is shown
   * as-is rather than rendered as `NaN%`.
   */
  const display = (raw: string) => {
    if (!slider || raw.trim() === "") {
      return raw;
    }
    const numeric = Number(raw);
    return Number.isFinite(numeric) ? slider.format(numeric) : raw;
  };

  return (
    <tr>
      <th>{label ?? name}</th>
      <td className="settings__value">
        {locked ? (
          display(value)
        ) : slider ? (
          <div className="settings__slider">
            <input
              className="settings__input settings__slider-input"
              type="range"
              min={slider.min}
              max={slider.max}
              step={slider.step}
              value={draft}
              aria-label={label ?? name}
              onChange={(event) => setDraft(event.target.value)}
              onPointerUp={(event) => void commit(event.currentTarget.value)}
              onKeyUp={(event) => void commit(event.currentTarget.value)}
              onBlur={() => void commit(draft)}
            />
            <output>{display(draft)}</output>
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
            ? translate(locale, APPLIED_NOW.has(name) ? "settings.applied" : "settings.saved")
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
