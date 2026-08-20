//! Shell's wire constants match `docs/contracts/wire.json`.
//!
//! Contract and rules → docs/contracts/wire.md / ADR-022
//!
//! **Shell silently rejects unknown values.** `parse_command` discards a version
//! mismatch, and `validate` turns anything outside the allowlist into
//! `unknown_method`. Both are fail-closed by design, but **drift looks the same
//! either way.** This is where it's caught.
//!
//! The same cross-check is also done by Core (`core/tests/test_wire_contract.py`)
//! and the Stage (`stage/src/core/wire.test.ts`).
//! **Missing it in even one language lets only that language quietly drift.**
//!
//! This module is only built under `#[cfg(test)]`. Since it's embedded via
//! `include_str!`, **changing `wire.json` triggers a rebuild** (it's read at
//! compile time, not runtime).

#![allow(clippy::unwrap_used)]

use serde_json::Value;

use crate::core_endpoint::EVENT_CORE_ENDPOINT;
use crate::hover::EVENT_HOVER_STATE;
use crate::os_command::ALLOWED_METHODS;
use crate::window::WindowKind;
use crate::ws_client::PROTOCOL_VERSION;

/// The repo's `docs/contracts/wire.json`. **Never included in the distributable** (only read by tests).
const WIRE: &str = include_str!("../../../docs/contracts/wire.json");

fn wire() -> Value {
    serde_json::from_str(WIRE).unwrap()
}

fn strings(value: &Value) -> Vec<String> {
    value.as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_owned()).collect()
}

#[test]
fn protocol_version_matches() {
    // **All 3 languages must check this.** If only Shell drifts, `parse_command`
    // discards every command, and Core waits 10 seconds and times out. Nothing shows on the Stage.
    assert_eq!(PROTOCOL_VERSION, wire()["protocol_version"].as_u64().unwrap());
}

#[test]
fn tauri_event_names_match() {
    // Dots aren't allowed, so `shell:hover:state` instead of `shell.hover.state`.
    let contract = wire();
    assert_eq!(EVENT_HOVER_STATE, contract["tauri_events"]["hover_state"].as_str().unwrap());
    assert_eq!(EVENT_CORE_ENDPOINT, contract["tauri_events"]["core_endpoint"].as_str().unwrap());
}

#[test]
fn the_os_allowlist_matches() {
    // **The allowlist must not be extendable from the Core side alone**, which is
    // also desirable from B3's standpoint (docs/contracts/security-boundaries.md).
    let expected = strings(&wire()["methods"]["os"]);
    let actual: Vec<String> = ALLOWED_METHODS.iter().map(|m| (*m).to_owned()).collect();
    assert_eq!(actual, expected);
}

#[test]
fn stage_methods_never_reach_the_shell_allowlist() {
    // **`stage.*` must never request OS privileges** (docs/architecture/core.md §3).
    for method in strings(&wire()["methods"]["stage"]) {
        assert!(
            !ALLOWED_METHODS.contains(&method.as_str()),
            "{method} is included in Shell allowlist"
        );
    }
}

#[test]
fn window_labels_match_and_round_trip() {
    let expected = strings(&wire()["window_labels"]);
    let actual: Vec<String> = WindowKind::ALL.iter().map(|k| k.label().to_owned()).collect();
    assert_eq!(actual, expected);
    // label → kind round-trips for every entry. A kind that doesn't round-trip can't be specified from `os.*`.
    for kind in WindowKind::ALL {
        assert_eq!(WindowKind::from_label(kind.label()), Some(kind));
    }
}
