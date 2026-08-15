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

impl WindowKind {
    /// 全種別。**label との対応をここ1箇所から導く**ので、
    /// 種別を足したときに `from_label` を直し忘れることがない。
    /// 並びと値の正は `docs/contracts/wire.json`（→ ADR-022）。
    pub const ALL: [WindowKind; 4] =
        [WindowKind::Stage, WindowKind::Credits, WindowKind::Permission, WindowKind::Settings];

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
    ///
    /// 呼び出し側は `os_command::validate` の `os.input.*` / `os.capture.*` 分岐
    /// （Phase 4c）。それまではテストからしか呼ばれないので、未使用の警告だけ抑える。
    /// **impl 全体ではなくここだけに付ける**（他が死んだときに気づけなくなる）。
    #[allow(dead_code)]
    pub const fn is_protected(self) -> bool {
        true
    }

    /// **`label()` の逆写像を手で書かない。** 2つの match を並べると、
    /// 種別を足したときに片方だけ直して、その窓が `os.*` から見えなくなる。
    pub fn from_label(label: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|kind| kind.label() == label)
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
        // 枠が無いので OS のリサイズハンドルは出ないが、**Lumi 自身が大きさを変える**
        // （キャラクターの上でホイール → `shell_window_scale`）。
        // `false` のままだと環境によって `set_size` が効かない可能性があるため合わせておく。
        resizable: true,
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

/// Stage ウィンドウの大きさの下限・上限（物理ピクセル）。
///
/// **Stage は信頼されていない**（B1 / B2）。倍率を受け取ってここでクランプする。
/// 絶対値を受け取る API にすると、Stage が画面外の大きさや 1 ピクセルを要求できる。
pub const MIN_STAGE_SIZE: f64 = 160.0;
pub const MAX_STAGE_SIZE: f64 = 4000.0;

/// 倍率をかけた後の大きさを決める。**純粋関数**（docs/architecture/ui.md）。
///
/// 縦横比を保つ。片方だけが上限・下限に当たると形が崩れるので、
/// **先に倍率をクランプしてから掛ける**。
pub fn compute_scaled_size(width: f64, height: f64, factor: f64) -> (f64, f64) {
    if !factor.is_finite() || factor <= 0.0 || !width.is_finite() || !height.is_finite() {
        // 壊れた値を渡されたら**変えない**（fail-closed）。
        return (width, height);
    }
    let longest = width.max(height);
    let shortest = width.min(height);
    if shortest <= 0.0 {
        return (width, height);
    }
    // 倍率の許される範囲。短い辺が下限を、長い辺が上限を決める。
    let lowest = MIN_STAGE_SIZE / shortest;
    let highest = MAX_STAGE_SIZE / longest;
    // 極端な縦横比で両立しないときは、**上限を優先する**（画面外に出さない方が害が小さい）。
    let clamped = if lowest > highest { highest } else { factor.clamp(lowest, highest) };
    (width * clamped, height * clamped)
}

/// ウィンドウを置ける領域（**論理ピクセル**。タスクバーを除いた作業領域）。
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ScreenArea {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// 画面の高さに対するキャラクターウィンドウの高さの比。
///
/// 1440p で 720px（従来の既定値）、1080p で 540px になる。
/// **画面が変わっても見え方が変わらない**ようにするための比率。
const STAGE_HEIGHT_RATIO: f64 = 0.3;

/// 縦横比（幅 ÷ 高さ）。立ち姿のキャラクターなので縦長。
const STAGE_ASPECT: f64 = 2.0 / 3.0;

/// 右端との余白。少し内側に置く。
const STAGE_MARGIN_RIGHT: f64 = 16.0;

/// 下端との余白は **0**。作業領域の下端 = タスクバーの上端に**接地させる**。
///
/// ここを空けるとキャラクターが宙に浮いて見える。デスクトップに住んでいるものは、
/// 床の上に立っている方が自然である。
const STAGE_MARGIN_BOTTOM: f64 = 0.0;

/// 既定の大きさと位置を決める。**純粋関数**（docs/architecture/ui.md）。
///
/// **右下に置く。** デスクトップの右下は、常駐するものが伝統的に居る場所であり、
/// 作業領域（タスクバーを除いた範囲）の右下なら、タスクバーに隠れない。
pub fn compute_stage_placement(area: ScreenArea) -> StageConfig {
    let height = (area.height * STAGE_HEIGHT_RATIO).clamp(MIN_STAGE_SIZE, MAX_STAGE_SIZE);
    let width = (height * STAGE_ASPECT).clamp(MIN_STAGE_SIZE, MAX_STAGE_SIZE);
    // 画面より大きくなったら、はみ出させずに左上へ寄せる（小さい画面での fail-safe）。
    let x = (area.x + area.width - width - STAGE_MARGIN_RIGHT).max(area.x);
    let y = (area.y + area.height - height - STAGE_MARGIN_BOTTOM).max(area.y);
    StageConfig { width, height, position: Some((x, y)), content_protection: false }
}

/// ウィンドウを掴んで動かす（`shell.*`）。**座標は OS が決める。**
///
/// Stage に座標を計算させない。`setPosition` を露出すると、Stage が
/// 画面外へウィンドウを追い出せる（docs/interfaces/shell.md）。
#[tauri::command]
pub fn shell_window_drag_start(window: tauri::WebviewWindow) -> Result<(), String> {
    require_stage(&window)?;
    window.start_dragging().map_err(|error| error.to_string())
}

/// 拡大縮小したときの左上の位置。**右下の角を固定する**。**純粋関数**。
///
/// 既定でウィンドウは画面の右下に居る（`compute_stage_placement`）。
/// 左上を固定して拡大すると、キャラクターが画面の外へはみ出していく。
/// 立っているものは足元が動かない方が自然でもある。
pub fn anchor_bottom_right(position: (f64, f64), old: (f64, f64), new: (f64, f64)) -> (f64, f64) {
    (position.0 + old.0 - new.0, position.1 + old.1 - new.1)
}

/// ウィンドウの大きさを倍率で変える（`shell.*`）。**クランプは Shell 側**。
#[tauri::command]
pub fn shell_window_scale(window: tauri::WebviewWindow, factor: f64) -> Result<(), String> {
    require_stage(&window)?;
    let size = window.outer_size().map_err(|error| error.to_string())?;
    let old = (f64::from(size.width), f64::from(size.height));
    let new = compute_scaled_size(old.0, old.1, factor);

    let position = window.outer_position().map_err(|error| error.to_string())?;
    let moved = anchor_bottom_right((f64::from(position.x), f64::from(position.y)), old, new);

    window
        .set_size(tauri::PhysicalSize::new(new.0.round() as u32, new.1.round() as u32))
        .map_err(|error| error.to_string())?;
    window
        .set_position(tauri::PhysicalPosition::new(moved.0.round() as i32, moved.1.round() as i32))
        .map_err(|error| error.to_string())
}

/// **Stage 以外のウィンドウには効かせない。** クレジットや権限プロンプトの
/// 大きさをキャラクターの操作で変えられる理由がない（Shell は「拒否」を持つ）。
fn require_stage(window: &tauri::WebviewWindow) -> Result<(), String> {
    match WindowKind::from_label(window.label()) {
        Some(WindowKind::Stage) => Ok(()),
        _ => {
            log::warn!("shell.window.refused label={}", window.label());
            Err("stage 以外のウィンドウは操作できない".into())
        }
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

    fn full_hd() -> ScreenArea {
        // 1080p でタスクバー 48px を除いた作業領域。
        ScreenArea { x: 0.0, y: 0.0, width: 1920.0, height: 1032.0 }
    }

    #[test]
    fn the_window_starts_in_the_bottom_right() {
        let placement = compute_stage_placement(full_hd());
        let (x, y) = placement.position.unwrap();
        // 右端・下端から余白のぶんだけ内側。**作業領域の外に出さない。**
        assert!((x + placement.width + STAGE_MARGIN_RIGHT - 1920.0).abs() < 1e-9);
        // **下端は作業領域にぴったり。** 浮くとキャラクターが宙に立って見える。
        assert!((y + placement.height - 1032.0).abs() < 1e-9);
    }

    #[test]
    fn the_size_follows_the_screen() {
        let small = compute_stage_placement(full_hd());
        let large =
            compute_stage_placement(ScreenArea { x: 0.0, y: 0.0, width: 2560.0, height: 1392.0 });
        assert!(large.height > small.height);
        // 縦横比は画面によらず一定。
        assert!((small.width / small.height - large.width / large.height).abs() < 1e-9);
    }

    #[test]
    fn a_second_monitor_offset_is_respected() {
        // 左に別のモニタがある場合、作業領域の原点は 0 ではない。
        let placement = compute_stage_placement(ScreenArea {
            x: -1920.0,
            y: 0.0,
            width: 1920.0,
            height: 1032.0,
        });
        let (x, _) = placement.position.unwrap();
        assert!(x < 0.0);
    }

    #[test]
    fn a_tiny_screen_does_not_push_the_window_off() {
        let placement =
            compute_stage_placement(ScreenArea { x: 0.0, y: 0.0, width: 200.0, height: 200.0 });
        let (x, y) = placement.position.unwrap();
        assert!(x >= 0.0 && y >= 0.0);
    }

    #[test]
    fn scaling_keeps_the_aspect_ratio() {
        let (w, h) = compute_scaled_size(480.0, 720.0, 1.25);
        assert_eq!((w, h), (600.0, 900.0));
    }

    #[test]
    fn scaling_stops_at_the_lower_bound() {
        // **Stage が窓を消せてはいけない。** 短い辺が下限で止まる。
        let (w, h) = compute_scaled_size(480.0, 720.0, 0.01);
        assert_eq!(w, MIN_STAGE_SIZE);
        assert!((h - MIN_STAGE_SIZE * 1.5).abs() < 1e-9);
    }

    #[test]
    fn scaling_stops_at_the_upper_bound() {
        // **画面外まで広げられてはいけない。** 長い辺が上限で止まる。
        let (w, h) = compute_scaled_size(480.0, 720.0, 100.0);
        assert_eq!(h, MAX_STAGE_SIZE);
        assert!(w < MAX_STAGE_SIZE);
    }

    #[test]
    fn growing_keeps_the_bottom_right_corner() {
        // 右下に居るキャラクターが、拡大のたびに画面外へ出ていかない。
        let position = (2072.0, 672.0);
        let old = (464.0, 696.0);
        let new = (584.0, 877.0);
        let moved = anchor_bottom_right(position, old, new);
        assert!((moved.0 + new.0 - (position.0 + old.0)).abs() < 1e-9);
        assert!((moved.1 + new.1 - (position.1 + old.1)).abs() < 1e-9);
        assert!(moved.0 < position.0 && moved.1 < position.1);
    }

    #[test]
    fn a_broken_factor_changes_nothing() {
        // fail-closed。**壊れた値で窓を壊さない。**
        for factor in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert_eq!(compute_scaled_size(480.0, 720.0, factor), (480.0, 720.0));
        }
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
        for kind in WindowKind::ALL {
            assert!(kind.is_protected(), "{} が保護対象から漏れている", kind.label());
        }
        assert_eq!(WindowKind::from_label("permission"), Some(WindowKind::Permission));
        // Lumi 以外のウィンドウは保護対象ではない（そもそも判定に載らない）
        assert_eq!(WindowKind::from_label("unknown"), None);
    }
}
