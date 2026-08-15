/**
 * Stage のストア。**Core が `stage.*` で配ったものだけ**を持つ。
 *
 * > 判定基準: Stage のストアから読める値は、すべて Core が配信したものであるべき。
 * > Stage が自分で計算して状態を作っていたら、それはロジックが漏れている。
 * > → docs/architecture/ui.md §2
 *
 * 例外は `connected`（WS が繋がっているか）と `prompt`（Core に聞かれている最中か）。
 * どちらも**接続そのものの状態**であり、Lumi の判断ではない。
 */

import { create } from "zustand";

export type TtsSetupState =
  | "unknown"
  | "not_configured"
  | "detected"
  | "installing"
  | "installed"
  | "failed";

/** Core の `TtsSetup.to_payload()` と同じ形（core/lumi/setup/state.py）。 */
export interface TtsSetupSnapshot {
  state: TtsSetupState;
  engine_name: string | null;
  version: string | null;
  port: number | null;
  executable: string | null;
  reason: string | null;
  progress: number | null;
}

export interface SetupPrompt {
  /** 失敗のあとの再提案か。 */
  retry: boolean;
  /** 直前の失敗理由（`retry` のときだけ入る）。 */
  reason: string | null;
}

interface StageState {
  connected: boolean;
  tts: TtsSetupSnapshot;
  prompt: SetupPrompt | null;
  setConnected(connected: boolean): void;
  setTts(snapshot: TtsSetupSnapshot): void;
  setPrompt(prompt: SetupPrompt | null): void;
}

const UNKNOWN_TTS: TtsSetupSnapshot = {
  state: "unknown",
  engine_name: null,
  version: null,
  port: null,
  executable: null,
  reason: null,
  progress: null,
};

export const useStageStore = create<StageState>((set) => ({
  connected: false,
  tts: UNKNOWN_TTS,
  prompt: null,
  setConnected: (connected) => set({ connected }),
  setTts: (tts) => set({ tts }),
  setPrompt: (prompt) => set({ prompt }),
}));

/** Core から来た payload を型に落とす。**知らない値は unknown 扱い**（勝手に補完しない）。 */
export function toTtsSnapshot(payload: Record<string, unknown>): TtsSetupSnapshot {
  const state = payload.state;
  const known: TtsSetupState[] = [
    "unknown",
    "not_configured",
    "detected",
    "installing",
    "installed",
    "failed",
  ];
  return {
    state: known.find((candidate) => candidate === state) ?? "unknown",
    engine_name: typeof payload.engine_name === "string" ? payload.engine_name : null,
    version: typeof payload.version === "string" ? payload.version : null,
    port: typeof payload.port === "number" ? payload.port : null,
    executable: typeof payload.executable === "string" ? payload.executable : null,
    reason: typeof payload.reason === "string" ? payload.reason : null,
    progress: typeof payload.progress === "number" ? payload.progress : null,
  };
}
