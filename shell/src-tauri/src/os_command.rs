//! The `os.*` verification layer (B3) — **pure functions**. Testable without a socket or a window.
//!
//! > **Shell never trusts Core.** Core being compromised alone must never turn into an OS takeover.
//! > → docs/contracts/security-boundaries.md B3
//!
//! Applied regardless of what Core instructs:
//!
//! 1. Authentication via the WS token (`ws_client.rs`)
//! 2. **allowlist** — an unknown command is rejected and logged
//! 3. **schema** — type, range, enum values
//! 4. **Unconditional rejection for protected targets** (Invariant 8)
//!
//! Only **rejection** ever lives here. Placing "permission" here would
//! effectively destroy Invariant 1.
//!
//! ## What this layer does not guarantee
//!
//! B3 guarantees only these three things: (a) nothing outside the allowlist can
//! be done, (b) input/capture against a protected target can't happen, (c)
//! privilege escalation is never self-approved.
//! **This fixes the ceiling on privilege — it does not prevent harm.**

use serde_json::Value;

use crate::window::WindowKind;

/// An `os.*` request that passed verification.
///
/// **`os_exec::execute` accepts only this type**, so an unverified wire payload cannot
/// reach execution. That much the type guarantees.
///
/// **What it does not guarantee**: the variants are `pub`, so another module in this
/// crate can build one without asking. "`validate` is the only thing that constructs
/// these" is a rule this crate keeps, not something the compiler checks — don't skip a
/// check elsewhere on the strength of it.
#[derive(Debug, Clone, PartialEq)]
pub enum OsCommand {
    WindowGetPosition { window: WindowKind },
    WindowSetPosition { window: WindowKind, x: f64, y: f64 },
}

/// The reason for a rejection. **Always logged.** Never silently dropped.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Rejection {
    /// Not on the allowlist.
    UnknownMethod,
    /// A lane not implemented in Phase 0 (`os.input.*` / `os.capture.*`).
    NotImplemented,
    /// The payload doesn't match the schema.
    InvalidPayload(String),
    /// A target that isn't one of Lumi's own windows was specified.
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

/// **Phase 0's allowlist.** Anything not here is never let through.
///
/// Re-read docs/contracts/security-boundaries.md's B3 before adding to it.
pub(crate) const ALLOWED_METHODS: &[&str] = &["os.window.get_position", "os.window.set_position"];

/// A namespace not implemented until Phase 4c. **Not "doesn't exist" — explicitly rejected.**
///
/// This is because the holes in Invariant 8 (full-screen capture / coordinate-based
/// input injection) haven't been resolved yet (docs/roadmap.md Phase 4c "🔴 to
/// decide before starting"). When implementing this, **always route it through
/// rejection via `WindowKind::is_protected`.**
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
        // So that adding to ALLOWED_METHODS but forgetting to add a match arm
        // **never lets it through**, this rejects even if reached.
        _ => Err(Rejection::UnknownMethod),
    }
}

/// The target window is **restricted to Lumi's own windows only.**
/// Core is never allowed to specify an arbitrary HWND (B3's "fixed privilege ceiling").
fn window_of(payload: &Value) -> Result<WindowKind, Rejection> {
    let label = payload
        .get("window")
        .and_then(Value::as_str)
        .ok_or_else(|| Rejection::InvalidPayload("missing window".into()))?;
    WindowKind::from_label(label).ok_or(Rejection::UnknownWindow)
}

fn finite_number(payload: &Value, key: &str) -> Result<f64, Rejection> {
    let value = payload
        .get(key)
        .and_then(Value::as_f64)
        .ok_or_else(|| Rejection::InvalidPayload(format!("{key} must be a number")))?;
    if !value.is_finite() {
        return Err(Rejection::InvalidPayload(format!("{key} must be finite")));
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    // Tests are allowed to panic, so unwrap is permitted here.
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
        // roadmap Phase 0 verification step 9: an unknown os.* command is rejected and logged
        assert_eq!(validate("os.window.destroy", &json!({})), Err(Rejection::UnknownMethod));
        assert_eq!(validate("os.evil", &json!({})), Err(Rejection::UnknownMethod));
        assert_eq!(validate("stage.character.speak", &json!({})), Err(Rejection::UnknownMethod));
    }

    #[test]
    fn rejects_deferred_lanes_even_if_core_asks_nicely() {
        // Never let input / capture through before Invariant 8 is resolved (fail-closed)
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
        // Catches it here if a method is added to the allowlist but the match arm is forgotten.
        for method in ALLOWED_METHODS {
            let rejection = validate(method, &json!({}));
            assert_ne!(
                rejection,
                Err(Rejection::UnknownMethod),
                "{method} is in allowlist but has no match arm"
            );
        }
    }
}
