//! ウィンドウ契約 — **純粋関数**。
//!
//! Tauri に依存しないので、ウィンドウを開かずにユニットテストできる
//! （docs/architecture/ui.md「ウィンドウ設定を純粋関数に切り出す」）。
//!
//! ここには「どう見えるべきか」しか書かない。**AI の判断は一切入らない**
//! （`shell.*` は絶対に AI の判断を運ばない → docs/architecture/core.md §3）。

/// ウィンドウ一覧 → docs/architecture/ui.md §1
///
/// Phase 0 で作るのは `Stage` と `Credits` だけ。`Permission` は Phase 4a だが、
/// **保護対象の判定（Invariant 8）に必要なので label だけ先に確定させる**。
// Permission は Phase 4a、Settings は Phase 1 で生成する。
// label と保護対象の判定は先に確定させておく必要があるため、未使用の警告だけ抑える。
#[allow(dead_code)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WindowKind {
    /// キャラクター本体。透過 / 最前面 / クリックスルー / 非フォーカス。
    Stage,
    /// クレジット表示（トレイ → クレジット）。通常ウィンドウ。
    Credits,
    /// 権限プロンプト。フォーカス必須 / Invariant 8 の保護対象。実装は Phase 4a。
    Permission,
    /// 設定。通常ウィンドウ。実装は Phase 1。
    Settings,
}

#[allow(dead_code)]
impl WindowKind {
    pub const fn label(self) -> &'static str {
        match self {
            WindowKind::Stage => "stage",
            WindowKind::Credits => "credits",
            WindowKind::Permission => "permission",
            WindowKind::Settings => "settings",
        }
    }

    /// **保護対象ウィンドウ**（docs/contracts/security-boundaries.md B3 / Invariant 8）。
    ///
    /// `os.input.*` / `os.capture.*` の対象になってはならないウィンドウ。
    /// **設定で無効化できない**ので、ここはハードコードのままにする。
    ///
    /// docs が列挙しているのは「権限プロンプト / メインウィンドウ / 設定」だが、
    /// ここでは **Lumi 自身のウィンドウをすべて**保護対象にする。
    /// 理由は2つ。(a) AIRI から借りる運用知見「自分自身を deny リストに入れる」、
    /// (b) 新しいウィンドウ種別を足したときに**既定で保護される**（fail-closed）。
    /// 保護しない窓を作りたくなったら、そのときに明示的に例外を書く。
    pub const fn is_protected(self) -> bool {
        true
    }

    pub fn from_label(label: &str) -> Option<Self> {
        match label {
            "stage" => Some(WindowKind::Stage),
            "credits" => Some(WindowKind::Credits),
            "permission" => Some(WindowKind::Permission),
            "settings" => Some(WindowKind::Settings),
            _ => None,
        }
    }
}

/// Core / 設定から与えられる Stage ウィンドウの構成。
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StageConfig {
    pub width: f64,
    pub height: f64,
    /// 前回終了時の位置。無ければ Shell 側で既定位置に置く。
    pub position: Option<(f64, f64)>,
    /// 画面共有に映さない（AIRI から借りる運用知見: コンテンツ保護）。
    pub content_protection: bool,
}

impl Default for StageConfig {
    fn default() -> Self {
        Self { width: 480.0, height: 720.0, position: None, content_protection: false }
    }
}

/// ウィンドウ生成の仕様。`PlatformShell.createWindow(spec)` に対応する
/// （docs/interfaces/shell.md）。Electron 実装でも同じ仕様を解釈できるようにする。
#[derive(Debug, Clone, PartialEq)]
pub struct WindowSpec {
    pub label: &'static str,
    pub title: &'static str,
    pub width: f64,
    pub height: f64,
    pub position: Option<(f64, f64)>,
    pub transparent: bool,
    pub decorations: bool,
    pub always_on_top: bool,
    pub skip_taskbar: bool,
    pub resizable: bool,
    pub shadow: bool,
    /// 表示時にフォーカスを奪わない（`showInactive` 相当）。
    pub focused: bool,
    pub visible: bool,
    pub content_protected: bool,
    /// 生成直後にクリックスルーにするか。
    pub click_through: bool,
}

/// Stage ウィンドウの仕様を決める。**純粋関数**。
pub fn compute_stage_window_options(cfg: &StageConfig) -> WindowSpec {
    WindowSpec {
        label: WindowKind::Stage.label(),
        title: "Lumi",
        width: cfg.width,
        height: cfg.height,
        position: cfg.position,
        transparent: true,
        decorations: false,
        always_on_top: true,
        skip_taskbar: true,
        resizable: false,
        shadow: false,
        // 表示時にフォーカスを奪わない。奪うと、ユーザーが作業中のウィンドウから
        // 入力先が飛ぶ（常駐キャラクターとして最も嫌われる挙動）。
        focused: false,
        visible: true,
        content_protected: cfg.content_protection,
        // 既定はクリックスルー。カーソルがキャラクター領域に入った時だけ解除する。
        click_through: true,
    }
}

/// クレジットウィンドウの仕様を決める。**純粋関数**。
///
/// クレジットは Phase 0 の必須項目（docs/licensing.md §6）。
/// 「少し探せばわかる場所」に置く必要があるため、トレイメニューから開く通常ウィンドウにする。
pub fn compute_credits_window_options() -> WindowSpec {
    WindowSpec {
        label: WindowKind::Credits.label(),
        title: "Lumi — クレジットとライセンス",
        width: 720.0,
        height: 640.0,
        position: None,
        transparent: false,
        decorations: true,
        always_on_top: false,
        skip_taskbar: false,
        resizable: true,
        shadow: true,
        focused: true,
        visible: true,
        content_protected: false,
        click_through: false,
    }
}

/// 画面上の点（物理ピクセル）。
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}

/// キャラクターの当たり判定領域。Stage が VRM の描画結果から算出して Shell に渡す
/// （docs/architecture/ui.md「ホバー検知の実装方針」）。
///
/// 座標は **Stage ウィンドウのクライアント座標**（左上原点・物理ピクセル）。
/// ウィンドウ位置との合成は呼び出し側が行う。
#[derive(Debug, Clone, Default, PartialEq)]
pub struct HitRegion {
    pub rects: Vec<HitRect>,
}

#[derive(Debug, Clone, Copy, PartialEq, serde::Deserialize)]
pub struct HitRect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

impl HitRect {
    fn contains(&self, p: Point) -> bool {
        p.x >= self.x && p.x < self.x + self.width && p.y >= self.y && p.y < self.y + self.height
    }
}

impl HitRegion {
    pub fn contains(&self, p: Point) -> bool {
        self.rects.iter().any(|r| r.contains(p))
    }

    /// 領域が空 = まだ Stage から届いていない、または描画するものが無い。
    pub fn is_empty(&self) -> bool {
        self.rects.is_empty()
    }
}

/// クリックスルーを解除すべきか。**純粋関数**。
///
/// `true` = クリックスルーする（マウスイベントを無視して背後のウィンドウに通す）。
///
/// **領域が未設定のときはクリックスルーする**（fail-open ではなく fail-safe 側）。
/// ここで fail-closed（クリックを掴む）に倒すと、Stage が壊れた瞬間に
/// デスクトップ全体がクリックできなくなる。**ユーザーが PC を操作できなくなる方が危険**。
pub fn decide_click_through(cursor: Point, region: &HitRegion) -> bool {
    if region.is_empty() {
        return true;
    }
    !region.contains(cursor)
}

/// カーソルの滞在状態。`shell.hover.state` として Stage に通知する。
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum HoverState {
    Outside,
    Inside,
}

/// ホバー状態の遷移。**純粋関数**。
///
/// 前回と同じなら `None` を返す。**変化したときだけ IPC を出す**ため
/// （60Hz でポーリングするので、毎回送ると `shell.*` の 1ms 予算を食い潰す）。
pub fn decide_hover_transition(
    cursor: Point,
    region: &HitRegion,
    prev: HoverState,
) -> Option<HoverState> {
    let next = if !region.is_empty() && region.contains(cursor) {
        HoverState::Inside
    } else {
        HoverState::Outside
    };
    if next == prev {
        None
    } else {
        Some(next)
    }
}

#[cfg(test)]
mod tests {
    // テストは panic してよい場所なので unwrap を許す。
    #![allow(clippy::unwrap_used)]

    use super::*;

    fn region() -> HitRegion {
        HitRegion { rects: vec![HitRect { x: 100.0, y: 200.0, width: 50.0, height: 60.0 }] }
    }

    #[test]
    fn stage_window_is_transparent_frameless_ontop_and_unfocused() {
        let spec = compute_stage_window_options(&StageConfig::default());
        assert!(spec.transparent);
        assert!(!spec.decorations);
        assert!(spec.always_on_top);
        assert!(!spec.focused, "表示時にフォーカスを奪ってはならない");
        assert!(spec.skip_taskbar);
        assert!(spec.click_through, "既定はクリックスルー");
    }

    #[test]
    fn stage_window_carries_content_protection_from_config() {
        let cfg = StageConfig { content_protection: true, ..StageConfig::default() };
        assert!(compute_stage_window_options(&cfg).content_protected);
        assert!(!compute_stage_window_options(&StageConfig::default()).content_protected);
    }

    #[test]
    fn stage_window_restores_position() {
        let cfg = StageConfig { position: Some((10.0, 20.0)), ..StageConfig::default() };
        assert_eq!(compute_stage_window_options(&cfg).position, Some((10.0, 20.0)));
    }

    #[test]
    fn credits_window_is_a_normal_focusable_window() {
        let spec = compute_credits_window_options();
        assert!(!spec.transparent);
        assert!(spec.decorations);
        assert!(spec.focused);
        assert!(!spec.always_on_top);
    }

    #[test]
    fn click_through_is_released_only_inside_the_region() {
        let r = region();
        assert!(!decide_click_through(Point { x: 120.0, y: 230.0 }, &r));
        assert!(decide_click_through(Point { x: 99.0, y: 230.0 }, &r));
        assert!(decide_click_through(Point { x: 120.0, y: 199.0 }, &r));
        // 右端・下端は排他（隣接矩形との二重判定を避ける）
        assert!(decide_click_through(Point { x: 150.0, y: 230.0 }, &r));
        assert!(decide_click_through(Point { x: 120.0, y: 260.0 }, &r));
    }

    #[test]
    fn click_through_stays_on_when_region_is_unknown() {
        // Stage が落ちていてもデスクトップを操作できること
        let empty = HitRegion::default();
        assert!(decide_click_through(Point { x: 120.0, y: 230.0 }, &empty));
    }

    #[test]
    fn hover_transition_fires_only_on_change() {
        let r = region();
        let inside = Point { x: 120.0, y: 230.0 };
        let outside = Point { x: 0.0, y: 0.0 };

        assert_eq!(
            decide_hover_transition(inside, &r, HoverState::Outside),
            Some(HoverState::Inside)
        );
        assert_eq!(decide_hover_transition(inside, &r, HoverState::Inside), None);
        assert_eq!(
            decide_hover_transition(outside, &r, HoverState::Inside),
            Some(HoverState::Outside)
        );
        assert_eq!(decide_hover_transition(outside, &r, HoverState::Outside), None);
    }

    #[test]
    fn every_lumi_window_is_protected() {
        for kind in
            [WindowKind::Stage, WindowKind::Credits, WindowKind::Permission, WindowKind::Settings]
        {
            assert!(kind.is_protected(), "{} が保護対象から漏れている", kind.label());
        }
        assert_eq!(WindowKind::from_label("permission"), Some(WindowKind::Permission));
        // Lumi 以外のウィンドウは保護対象ではない（そもそも判定に載らない）
        assert_eq!(WindowKind::from_label("unknown"), None);
    }
}
