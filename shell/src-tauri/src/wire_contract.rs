//! Shell の wire 定数が `docs/contracts/wire.json` と一致する。
//!
//! 契約と規則 → docs/contracts/wire.md / ADR-022
//!
//! **Shell は知らない値を静かに拒否する。** `parse_command` はバージョン違いを捨て、
//! `validate` は allowlist 外を `unknown_method` にする。どちらも設計どおりの
//! fail-closed だが、**ずれたときも同じ見た目になる**。ここで落とす。
//!
//! 同じ突き合わせを Core（`core/tests/test_wire_contract.py`）と
//! Stage（`stage/src/core/wire.test.ts`）も行う。
//! **1言語でも欠けると、その言語だけが静かにずれる。**
//!
//! このモジュールは `#[cfg(test)]` でしか作られない。`include_str!` で埋め込むので、
//! **`wire.json` を変えると再ビルドされる**（読み込みは実行時ではなくコンパイル時）。

#![allow(clippy::unwrap_used)]

use serde_json::Value;

use crate::core_endpoint::EVENT_CORE_ENDPOINT;
use crate::hover::EVENT_HOVER_STATE;
use crate::os_command::ALLOWED_METHODS;
use crate::window::WindowKind;
use crate::ws_client::PROTOCOL_VERSION;

/// リポジトリの `docs/contracts/wire.json`。**配布物には入らない**（テストでしか読まない）。
const WIRE: &str = include_str!("../../../docs/contracts/wire.json");

fn wire() -> Value {
    serde_json::from_str(WIRE).unwrap()
}

fn strings(value: &Value) -> Vec<String> {
    value.as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_owned()).collect()
}

#[test]
fn protocol_version_matches() {
    // **3言語すべてが検査すること。** Shell だけずれると `parse_command` が
    // すべての command を捨て、Core は 10 秒待って timeout する。Stage には何も出ない。
    assert_eq!(PROTOCOL_VERSION, wire()["protocol_version"].as_u64().unwrap());
}

#[test]
fn tauri_event_names_match() {
    // ドットが使えないので `shell.hover.state` ではなく `shell:hover:state`。
    let contract = wire();
    assert_eq!(EVENT_HOVER_STATE, contract["tauri_events"]["hover_state"].as_str().unwrap());
    assert_eq!(EVENT_CORE_ENDPOINT, contract["tauri_events"]["core_endpoint"].as_str().unwrap());
}

#[test]
fn the_os_allowlist_matches() {
    // **allowlist を Core 側だけで増やせないこと**が B3 の観点でも望ましい
    // （docs/contracts/security-boundaries.md）。
    let expected = strings(&wire()["methods"]["os"]);
    let actual: Vec<String> = ALLOWED_METHODS.iter().map(|m| (*m).to_owned()).collect();
    assert_eq!(actual, expected);
}

#[test]
fn stage_methods_never_reach_the_shell_allowlist() {
    // **`stage.*` は絶対に OS 特権を要求しない**（docs/architecture/core.md §3）。
    for method in strings(&wire()["methods"]["stage"]) {
        assert!(
            !ALLOWED_METHODS.contains(&method.as_str()),
            "{method} が Shell の allowlist に載っている"
        );
    }
}

#[test]
fn window_labels_match_and_round_trip() {
    let expected = strings(&wire()["window_labels"]);
    let actual: Vec<String> = WindowKind::ALL.iter().map(|k| k.label().to_owned()).collect();
    assert_eq!(actual, expected);
    // label → 種別 が全件戻ること。戻らない種別は `os.*` から指定できない。
    for kind in WindowKind::ALL {
        assert_eq!(WindowKind::from_label(kind.label()), Some(kind));
    }
}
