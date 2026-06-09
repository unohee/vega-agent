// 데스크탑 앱 로컬 설정 — CE/daemon 공용.
// 저장 위치: ~/Library/Application Support/vega-client/config.json (macOS)
//            ~/.config/vega-client/config.json (Linux)

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use tauri::Manager;

#[derive(Serialize, Deserialize, Clone)]
pub struct ClientConfig {
    /// CE 모드에서 연결할 서버 URL (daemon 모드에서는 무시)
    #[serde(default = "default_server_url")]
    pub server_url: String,
    /// UI 표시 언어: "ko" | "en" (기본값 "en")
    #[serde(default = "default_lang")]
    pub lang: String,
}

fn default_server_url() -> String { "http://localhost:8100".to_string() }
fn default_lang() -> String { "en".to_string() }

impl Default for ClientConfig {
    fn default() -> Self {
        Self {
            server_url: default_server_url(),
            lang: default_lang(),
        }
    }
}

fn config_path() -> PathBuf {
    dirs_next::config_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("vega-client")
        .join("config.json")
}

pub fn load_config() -> ClientConfig {
    let p = config_path();
    if let Ok(raw) = std::fs::read_to_string(&p) {
        if let Ok(cfg) = serde_json::from_str::<ClientConfig>(&raw) {
            return cfg;
        }
    }
    ClientConfig::default()
}

fn save_config(cfg: &ClientConfig) -> Result<(), String> {
    let p = config_path();
    if let Some(parent) = p.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let json = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    std::fs::write(&p, json).map_err(|e| e.to_string())?;
    Ok(())
}

// ── Tauri commands ────────────────────────────────────────────────────────────

#[tauri::command]
pub fn get_server_url() -> String {
    load_config().server_url
}

#[tauri::command]
pub fn set_server_url(url: String, app: tauri::AppHandle) -> Result<(), String> {
    let url = url.trim().trim_end_matches('/').to_string();
    if url.is_empty() {
        return Err("Server URL cannot be empty.".to_string());
    }
    let mut cfg = load_config();
    cfg.server_url = url.clone();
    save_config(&cfg)?;

    if let Some(win) = app.get_webview_window("main") {
        let chat_url = format!("{}/chat", url);
        let _ = win.eval(&format!("window.location.href = {:?}", chat_url)); // cxt-ignore: security
    }
    Ok(())
}

#[tauri::command]
pub fn get_lang() -> String {
    load_config().lang
}

#[tauri::command]
pub fn set_lang(lang: String, app: tauri::AppHandle) -> Result<(), String> {
    let lang = lang.trim().to_lowercase();
    if lang != "en" && lang != "ko" {
        return Err(format!("Unsupported language: {lang}. Use 'en' or 'ko'."));
    }
    let mut cfg = load_config();
    cfg.lang = lang;
    save_config(&cfg)?;

    // 트레이 메뉴 라벨 실시간 갱신 — 데스크탑 전용 (모바일엔 트레이 없음)
    #[cfg(desktop)]
    rebuild_tray(&app);
    #[cfg(not(desktop))]
    let _ = &app;
    Ok(())
}

/// 현재 설정 언어에 따라 UI 문자열을 반환하는 헬퍼.
pub struct Strings {
    pub open:     &'static str,
    pub hide:     &'static str,
    pub settings: &'static str,
    pub restart:  &'static str,
    pub quit:     &'static str,
    pub settings_title: &'static str,
    pub tooltip:  &'static str,
}

pub fn strings() -> Strings {
    match load_config().lang.as_str() {
        "ko" => Strings {
            open:     "VEGA 열기",
            hide:     "숨기기",
            settings: "설정…",
            restart:  "백엔드 재시작",
            quit:     "종료",
            settings_title: "VEGA 설정",
            tooltip:  "VEGA",
        },
        _ => Strings {
            open:     "Open VEGA",
            hide:     "Hide",
            settings: "Settings…",
            restart:  "Restart backend",
            quit:     "Quit",
            settings_title: "VEGA Settings",
            tooltip:  "VEGA",
        },
    }
}

/// 언어 변경 후 트레이 메뉴를 새 라벨로 재빌드.
/// Tauri v2는 메뉴 아이템 라벨 in-place 변경을 지원하지 않으므로 트레이를 통째로 교체.
/// 데스크탑 전용 — 모바일엔 시스템 트레이 API(`tray_by_id`)가 없다.
#[cfg(desktop)]
pub fn rebuild_tray(app: &tauri::AppHandle) {
    use tauri::menu::{MenuBuilder, MenuItemBuilder};

    let s = strings();
    let Ok(show_item) = MenuItemBuilder::with_id("show", s.open).build(app) else { return };
    let Ok(hide_item) = MenuItemBuilder::with_id("hide", s.hide).build(app) else { return };
    let Ok(settings_item) = MenuItemBuilder::with_id("settings", s.settings).build(app) else { return };
    let Ok(restart_item) = MenuItemBuilder::with_id("restart-backend", s.restart).build(app) else { return };
    let Ok(quit_item) = MenuItemBuilder::with_id("quit", s.quit).build(app) else { return };
    let Ok(menu) = MenuBuilder::new(app)
        .items(&[&show_item, &hide_item])
        .separator()
        .items(&[&settings_item, &restart_item])
        .separator()
        .items(&[&quit_item])
        .build()
    else { return };

    if let Some(tray) = app.tray_by_id("vega-tray") {
        let _ = tray.set_menu(Some(menu));
        let _ = tray.set_tooltip(Some(s.tooltip));
    }
}
