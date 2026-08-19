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

import { useEffect, useMemo, useState } from "react";

import type { SettingsSource } from "../core/store";
import { useStageStore } from "../core/store";
import { updateSettings } from "../core/useCoreConnection";
import { translate } from "../i18n";
import { useLocale } from "../i18n/provider";

/** Values with a fixed set of choices. Anything else is free text (model names vary). */
const CHOICES: Record<string, string[]> = {
  inference_device: ["auto", "cuda", "cpu"],
  locale: ["auto", "ja", "en"],
};

function Row({ name, value, source }: { name: string; value: string; source: SettingsSource }) {
  const locale = useLocale();
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const snapshot = useMemo(() => ({ value, source }), [value, source]);
  useEffect(() => {
    // Core owns the value. A new snapshot supersedes any local draft or status.
    setDraft(snapshot.value);
    setError(null);
    setSaved(false);
  }, [snapshot]);
  // **Never editable while overridden.** Saving would make the escape hatch permanent
  const locked = source === "env";

  const commit = async (next: string) => {
    setDraft(next);
    setError(null);
    setSaved(false);
    try {
      await updateSettings({ [name]: next });
      setSaved(true);
    } catch (failure: unknown) {
      // **The reason Core gave, shown as-is.** Never "failed to save" with nothing else
      setError(failure instanceof Error ? failure.message : translate(locale, "settings.refused"));
      setDraft(value);
    }
  };

  const choices = CHOICES[name];

  return (
    <tr>
      <th>
        {name === "inference_device" ||
        name === "llm_model" ||
        name === "stt_model" ||
        name === "locale"
          ? translate(locale, `settings.label.${name}`)
          : name}
      </th>
      <td className="settings__value">
        {locked ? (
          value
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
            ? translate(locale, name === "locale" ? "settings.applied" : "settings.saved")
            : translate(locale, `settings.source.${source}`))}
      </td>
    </tr>
  );
}

export function Settings({ onOpenChange }: { onOpenChange?: (open: boolean) => void } = {}) {
  const locale = useLocale();
  const settings = useStageStore((state) => state.settings);
  const [open, setOpen] = useState(false);

  if (!settings) {
    return null;
  }

  const handleToggle = () => {
    const next = !open;
    setOpen(next);
    onOpenChange?.(next);
  };

  return (
    <div className="settings">
      <button type="button" className="inspect__toggle" onClick={handleToggle}>
        {open ? "▾" : "▸"} {translate(locale, "settings.title")}
      </button>
      {open && (
        <div className="inspect__body">
          {settings.unreadable && (
            // **Core refuses to save over it**, and says so rather than quietly running
            // on defaults and destroying what the user meant.
            <p className="panel__status panel__status--bad">
              {translate(locale, "settings.unreadable")}
            </p>
          )}
          <table className="inspect__lat">
            <tbody>
              {Object.entries(settings.values).map(([name, setting]) => (
                <Row key={name} name={name} value={setting.value} source={setting.source} />
              ))}
            </tbody>
          </table>
          <p className="inspect__empty">{translate(locale, "settings.restart")}</p>
        </div>
      )}
    </div>
  );
}
