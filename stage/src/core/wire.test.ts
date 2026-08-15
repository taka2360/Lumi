/**
 * Stage の wire 定数が `docs/contracts/wire.json` と一致する。
 *
 * 契約と規則 → docs/contracts/wire.md / ADR-022
 *
 * **Stage は知らない値を静かに落とす。** `parseCoreMessage` は `null` を返し、
 * `toTtsSnapshot` は fail-closed 側の既定（`"starting"` / `"unknown"`）に丸め、
 * `parseTimeline` は口を閉じる。**どれもエラーにならない。**
 * 実装としては正しいが、そのせいでずれが見えない。ここで落とす。
 *
 * 同じ突き合わせを Core（`core/tests/test_wire_contract.py`）と
 * Shell（`shell/src-tauri/src/wire_contract.rs`）も行う。
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { VISEMES } from "../character/lipsync";
import {
  CMD_DRAG_START,
  CMD_SCALE,
  CMD_SET_HIT_REGION,
  EVENT_HOVER_STATE,
} from "../platform/tauri";
import { CMD_CORE_ENDPOINT, EVENT_CORE_ENDPOINT } from "./connection";
import { PROTOCOL_VERSION } from "./protocol";
import { BOOT_PHASES, ENGINE_RUNTIMES, TTS_SETUP_STATES } from "./store";
import {
  CHOICE_INSTALL,
  CHOICE_SKIP,
  METHOD_SETUP_PROMPT,
  METHOD_SETUP_STATE,
  METHOD_SPEECH_ENDED,
  METHOD_SPEECH_STARTED,
} from "./useCoreConnection";

interface Wire {
  protocol_version: number;
  methods: { stage: string[]; os: string[] };
  setup_prompt_choices: { install: string; skip: string };
  tauri_events: { hover_state: string; core_endpoint: string };
  tauri_commands: string[];
  enums: {
    tts_setup_state: string[];
    engine_runtime: string[];
    boot_phase: string[];
    viseme: string[];
  };
}

/** リポジトリの `docs/contracts/wire.json`。**実行時には読まない**（配布物に docs は入らない）。 */
const wire: Wire = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../../docs/contracts/wire.json"), "utf-8"),
) as Wire;

describe("wire contract", () => {
  it("protocol version", () => {
    // **3言語すべてが検査すること。** Stage だけずれると、Core からの command が
    // `parseCoreMessage` で捨てられ、Core は 10 秒待って timeout する。画面には何も出ない。
    expect(PROTOCOL_VERSION).toBe(wire.protocol_version);
  });

  it("stage の method 名", () => {
    expect(
      new Set([
        METHOD_SETUP_STATE,
        METHOD_SETUP_PROMPT,
        METHOD_SPEECH_STARTED,
        METHOD_SPEECH_ENDED,
      ]),
    ).toEqual(new Set(wire.methods.stage));
  });

  it("os.* の method 名は Stage に現れない", () => {
    // **`stage.*` は絶対に OS 特権を要求しない**（docs/architecture/core.md §3）。
    // 契約の os.* が Stage 側の定数に混ざっていないことを、値の側から確かめる。
    const stageConstants = [
      METHOD_SETUP_STATE,
      METHOD_SETUP_PROMPT,
      METHOD_SPEECH_STARTED,
      METHOD_SPEECH_ENDED,
    ];
    for (const method of wire.methods.os) {
      expect(stageConstants).not.toContain(method);
    }
  });

  it("取得するかの選択肢", () => {
    expect({ install: CHOICE_INSTALL, skip: CHOICE_SKIP }).toEqual(wire.setup_prompt_choices);
  });

  it("Tauri のイベント名", () => {
    // ドットが使えないので `shell.hover.state` ではなく `shell:hover:state`。
    // Shell 側の実体は hover.rs / core_endpoint.rs。
    expect(EVENT_HOVER_STATE).toBe(wire.tauri_events.hover_state);
    expect(EVENT_CORE_ENDPOINT).toBe(wire.tauri_events.core_endpoint);
  });

  it("Tauri のコマンド名", () => {
    // **片側検査。** Shell 側は `#[tauri::command]` の関数名なのでデータとして取れない
    // （docs/contracts/wire.md §4「保証しないこと」）。
    expect(new Set([CMD_SET_HIT_REGION, CMD_CORE_ENDPOINT, CMD_DRAG_START, CMD_SCALE])).toEqual(
      new Set(wire.tauri_commands),
    );
  });

  it("線に乗る enum の値", () => {
    // 並びも合わせる。Core の enum の宣言順（状態が進む順）に意味がある。
    expect(TTS_SETUP_STATES).toEqual(wire.enums.tts_setup_state);
    expect(ENGINE_RUNTIMES).toEqual(wire.enums.engine_runtime);
    expect(BOOT_PHASES).toEqual(wire.enums.boot_phase);
    expect(VISEMES).toEqual(wire.enums.viseme);
  });
});
