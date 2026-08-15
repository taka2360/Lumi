//! `os.*` の検証層（B3）— **純粋関数**。ソケットもウィンドウも要らずにテストできる。
//!
//! > **Shell は Core を信頼しない。** Core が侵害されただけで OS 乗っ取りになってはならない。
//! > → docs/contracts/security-boundaries.md B3
//!
//! Core の指示内容にかかわらず、次を適用する。
//!
//! 1. WS token による認証（`ws_client.rs`）
//! 2. **allowlist** — 未知のコマンドは拒否してログ
//! 3. **schema** — 型・範囲・列挙値
//! 4. **保護対象への無条件拒否**（Invariant 8）
//!
//! ここに置くのは**拒否だけ**。「許可」を置いたら Invariant 1 の実質的な破壊になる。
//!
//! ## この層が保証しないこと
//!
//! B3 が保証するのは (a) allowlist 外の操作ができない (b) 保護対象への入力・キャプチャが
//! できない (c) 権限昇格が自己承認で行われない、の3点**だけ**である。
//! **被害の防止ではなく、権限の上限固定。**

use serde_json::Value;

use crate::window::WindowKind;

/// 検証を通った `os.*` 要求。**この型を作れるのは `validate` だけ**にしておく。
#[derive(Debug, Clone, PartialEq)]
pub enum OsCommand {
    WindowGetPosition { window: WindowKind },
    WindowSetPosition { window: WindowKind, x: f64, y: f64 },
}

/// 拒否の理由。**必ずログに残す。** 黙って落とさない。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Rejection {
    /// allowlist に載っていない。
    UnknownMethod,
    /// Phase 0 では実装しない lane（`os.input.*` / `os.capture.*`）。
    NotImplemented,
    /// payload が schema に合わない。
    InvalidPayload(String),
    /// Lumi のウィンドウではない対象を指定された。
    UnknownWindow,
}

impl Rejection {
    pub fn reason(&self) -> &'static str {
        match self {
            Rejection::UnknownMethod => "unknown_method",
            Rejection::NotImplemented => "not_implemented",
            Rejection::InvalidPayload(_) => "invalid_payload",
            Rejection::UnknownWindow => "unknown_window",
        }
    }
}

/// **Phase 0 の allowlist。** ここに無いものは通らない。
///
/// 増やすときは docs/contracts/security-boundaries.md の B3 を読み直すこと。
pub(crate) const ALLOWED_METHODS: &[&str] = &["os.window.get_position", "os.window.set_position"];

/// Phase 4c まで実装しない namespace。**存在しないのではなく、明示的に拒否する。**
///
/// Invariant 8 の穴（全画面キャプチャ / 座標指定の入力注入）の決着が済んでいないため
/// （docs/roadmap.md Phase 4c「🔴 着手前に決めること」）。
/// ここを実装するときは、**`WindowKind::is_protected` による拒否を必ず通す**。
const DEFERRED_PREFIXES: &[&str] = &["os.input.", "os.capture."];

pub fn validate(method: &str, payload: &Value) -> Result<OsCommand, Rejection> {
    if DEFERRED_PREFIXES.iter().any(|p| method.starts_with(p)) {
        return Err(Rejection::NotImplemented);
    }
    if !ALLOWED_METHODS.contains(&method) {
        return Err(Rejection::UnknownMethod);
    }

    match method {
        "os.window.get_position" => {
            Ok(OsCommand::WindowGetPosition { window: window_of(payload)? })
        }
        "os.window.set_position" => Ok(OsCommand::WindowSetPosition {
            window: window_of(payload)?,
            x: finite_number(payload, "x")?,
            y: finite_number(payload, "y")?,
        }),
        // ALLOWED_METHODS に足して match を足し忘れたときに**通してしまわない**ため、
        // ここは到達しても拒否する。
        _ => Err(Rejection::UnknownMethod),
    }
}

/// 対象ウィンドウは **Lumi 自身のウィンドウに限る**。
/// 任意の HWND を Core から指定させない（B3 の「権限の上限固定」）。
fn window_of(payload: &Value) -> Result<WindowKind, Rejection> {
    let label = payload
        .get("window")
        .and_then(Value::as_str)
        .ok_or_else(|| Rejection::InvalidPayload("window が無い".into()))?;
    WindowKind::from_label(label).ok_or(Rejection::UnknownWindow)
}

fn finite_number(payload: &Value, key: &str) -> Result<f64, Rejection> {
    let value = payload
        .get(key)
        .and_then(Value::as_f64)
        .ok_or_else(|| Rejection::InvalidPayload(format!("{key} が数値ではない")))?;
    if !value.is_finite() {
        return Err(Rejection::InvalidPayload(format!("{key} が有限ではない")));
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    // テストは panic してよい場所なので unwrap を許す。
    #![allow(clippy::unwrap_used)]

    use serde_json::json;

    use super::*;

    #[test]
    fn accepts_allowlisted_commands() {
        assert_eq!(
            validate("os.window.get_position", &json!({"window": "stage"})),
            Ok(OsCommand::WindowGetPosition { window: WindowKind::Stage })
        );
        assert_eq!(
            validate("os.window.set_position", &json!({"window": "stage", "x": 1.0, "y": 2.0})),
            Ok(OsCommand::WindowSetPosition { window: WindowKind::Stage, x: 1.0, y: 2.0 })
        );
    }

    #[test]
    fn rejects_unknown_method() {
        // roadmap Phase 0 検証手順 9: 未知の os.* コマンドは拒否されてログに残る
        assert_eq!(validate("os.window.destroy", &json!({})), Err(Rejection::UnknownMethod));
        assert_eq!(validate("os.evil", &json!({})), Err(Rejection::UnknownMethod));
        assert_eq!(validate("stage.character.speak", &json!({})), Err(Rejection::UnknownMethod));
    }

    #[test]
    fn rejects_deferred_lanes_even_if_core_asks_nicely() {
        // Invariant 8 の決着前に input / capture を通さない（fail-closed）
        for method in ["os.input.click", "os.input.key", "os.capture.screenshot"] {
            assert_eq!(
                validate(method, &json!({"window": "stage"})),
                Err(Rejection::NotImplemented)
            );
        }
    }

    #[test]
    fn rejects_windows_that_are_not_ours() {
        assert_eq!(
            validate("os.window.get_position", &json!({"window": "explorer"})),
            Err(Rejection::UnknownWindow)
        );
    }

    #[test]
    fn rejects_malformed_payload() {
        assert!(matches!(
            validate("os.window.get_position", &json!({})),
            Err(Rejection::InvalidPayload(_))
        ));
        assert!(matches!(
            validate("os.window.set_position", &json!({"window": "stage", "x": "1", "y": 2})),
            Err(Rejection::InvalidPayload(_))
        ));
        assert!(matches!(
            validate("os.window.set_position", &json!({"window": "stage", "y": 2})),
            Err(Rejection::InvalidPayload(_))
        ));
    }

    #[test]
    fn every_allowlisted_method_is_handled() {
        // allowlist に足して match を足し忘れると、ここで気づく。
        for method in ALLOWED_METHODS {
            let rejection = validate(method, &json!({}));
            assert_ne!(
                rejection,
                Err(Rejection::UnknownMethod),
                "{method} が allowlist にあるのに match されていない"
            );
        }
    }
}
