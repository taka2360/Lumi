/**
 * Settings — **the skeleton** (roadmap Phase 1 / open item #9).
 *
 * Design → docs/architecture/core.md "設定" / docs/architecture/ui.md §2
 *
 * **Read-only for now, and deliberately so.** Core owns the values and the Stage only
 * shows them; changing one needs a Stage → Core request, and that direction is not built
 * yet (the protocol currently accepts only `hello` and `result` from a client). Shipping
 * a control that silently does nothing would be worse than showing the values plainly.
 *
 * **Where each value came from is shown.** A setting that an environment variable is
 * overriding, without saying so, turns "I changed it and nothing happened" into something
 * nobody can explain.
 */

import { useState } from "react";

import type { SettingsSource } from "../core/store";
import { useStageStore } from "../core/store";

const SOURCE_TEXT: Record<SettingsSource, string> = {
  default: "既定",
  file: "設定ファイル",
  env: "環境変数で上書き中",
};

const LABEL: Record<string, string> = {
  inference_device: "推論デバイス",
  llm_model: "LLM モデル",
  stt_model: "音声認識モデル",
};

export function Settings({ onOpenChange }: { onOpenChange?: (open: boolean) => void } = {}) {
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
        {open ? "▾" : "▸"} 設定
      </button>
      {open && (
        <div className="inspect__body">
          {settings.unreadable && (
            // **Core will refuse to save over it**, and says so rather than quietly
            // running on defaults.
            <p className="panel__status panel__status--bad">
              設定ファイルを読めませんでした。既定値で動いています（上書きはしません）
            </p>
          )}
          <table className="inspect__lat">
            <tbody>
              {Object.entries(settings.values).map(([key, setting]) => (
                <tr key={key}>
                  <th>{LABEL[key] ?? key}</th>
                  <td className="settings__value">{setting.value}</td>
                  <td
                    className={
                      setting.source === "env"
                        ? "settings__src settings__src--env"
                        : "settings__src"
                    }
                  >
                    {SOURCE_TEXT[setting.source]}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="inspect__empty">
            変更はまだできません（設定ファイルを直接編集して再起動してください）
          </p>
        </div>
      )}
    </div>
  );
}
