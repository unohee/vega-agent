// VEGA 데스크탑 셸 본체.
//
// --features client  : 백엔드 sidecar 없이 외부 서버 URL에 붙는 얇은 클라이언트.
//                      설정 창에서 서버 URL + 언어 변경 가능.
// --features daemon  : 첫 실행 시 LaunchAgent를 등록해 백엔드를 상시 데몬으로 실행.
//
// 공통: 트레이 아이콘 + 창 토글 + 전역 단축키(Cmd+Shift+V) + 언어 선택기

pub mod client_config;

use client_config::strings;

use tauri::{
    menu::{MenuBuilder, MenuItemBuilder},
    tray::TrayIconBuilder,
    Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent,
};

fn backend_url() -> String {
    #[cfg(feature = "client")]
    {
        let cfg = client_config::load_config();
        return format!("{}/chat", cfg.server_url);
    }
    #[cfg(not(feature = "client"))]
    "http://localhost:8100/chat".to_string()
}

/// 백엔드가 응답할 때까지 최대 30초 폴링 후 창에 URL 로드.
fn wait_and_navigate(win: tauri::WebviewWindow, url: String) {
    std::thread::spawn(move || {
        let health = url.replace("/chat", "/api/health");
        for _ in 0..60 {
            if let Ok(resp) = std::net::TcpStream::connect("127.0.0.1:8100") {
                drop(resp);
                // TCP 연결 가능 → HTTP 응답 대기 (uvicorn 초기화 시간)
                std::thread::sleep(std::time::Duration::from_millis(300));
                let _ = win.eval(&format!("window.location.href = {:?}", url));
                return;
            }
            std::thread::sleep(std::time::Duration::from_millis(500));
        }
        // 30초 후에도 안 뜨면 오류 페이지
        let _ = win.eval(&format!(
            "document.body.innerHTML = '<div style=\"font-family:sans-serif;padding:40px;color:#e6edf3;background:#0d1117\"><h2>백엔드 연결 실패</h2><p>VEGA 서버({})에 접속할 수 없습니다.</p></div>'",
            health
        ));
    });
}

fn make_loading_page() -> WebviewUrl {
    // 로딩 중 표시할 인라인 HTML — 백엔드 준비되면 JS가 교체
    WebviewUrl::App("index.html".into())
}

fn toggle_main_window(app: &tauri::AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        match win.is_visible() {
            Ok(true) => { let _ = win.hide(); }
            _ => {
                let _ = win.unminimize();
                let _ = win.show();
                let _ = win.set_focus();
            }
        }
    }
}

fn show_main_window(app: &tauri::AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
}

fn open_settings_window(app: &tauri::AppHandle) {
    if let Some(win) = app.get_webview_window("settings") {
        let _ = win.show();
        let _ = win.set_focus();
        return;
    }

    let settings_html = if cfg!(feature = "client") { "client-settings.html" } else { "settings.html" };
    let title = strings().settings_title;
    let _ = WebviewWindowBuilder::new(app, "settings", WebviewUrl::App(settings_html.into()))
        .title(title)
        .inner_size(480.0, 340.0)
        .resizable(false)
        .center()
        .title_bar_style(tauri::TitleBarStyle::Overlay)
        .hidden_title(true)
        .build();
}

// ── LaunchAgent 관리 (daemon 전용) ────────────────────────────────────────────

#[cfg(feature = "daemon")]
fn launchagent_plist_path() -> std::path::PathBuf {
    dirs_next::home_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join("Library/LaunchAgents/com.unohee.vega-backend.plist")
}

#[cfg(feature = "daemon")]
fn resources_dir(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    app.path().resource_dir().ok()
}

/// LaunchAgent plist가 없으면 Resources에서 복사 후 등록.
/// 이미 등록되어 있으면 아무것도 하지 않음.
#[cfg(feature = "daemon")]
fn ensure_launchagent(app: &tauri::AppHandle) {
    let plist_dst = launchagent_plist_path();

    if !plist_dst.exists() {
        if let Some(res) = resources_dir(app) {
            let plist_src = res.join("com.unohee.vega-backend.plist");
            if plist_src.exists() {
                if let Some(parent) = plist_dst.parent() {
                    let _ = std::fs::create_dir_all(parent);
                }
                // __HOME__ 플레이스홀더를 실제 홈 경로로 치환
                match std::fs::read_to_string(&plist_src) {
                    Ok(content) => {
                        let home = dirs_next::home_dir()
                            .map(|p| p.to_string_lossy().into_owned())
                            .unwrap_or_else(|| "/tmp".to_string());
                        let replaced = content.replace("__HOME__", &home);
                        if let Err(e) = std::fs::write(&plist_dst, replaced) {
                            eprintln!("[VEGA] LaunchAgent plist 쓰기 실패: {e}");
                            return;
                        }
                    }
                    Err(e) => {
                        eprintln!("[VEGA] LaunchAgent plist 읽기 실패: {e}");
                        return;
                    }
                }
            } else {
                eprintln!("[VEGA] LaunchAgent plist 소스 없음: {}", plist_src.display());
                return;
            }
        }
    }

    // launchctl bootstrap (이미 등록돼 있으면 오류 무시)
    let uid = unsafe { libc::getuid() };
    let domain = format!("gui/{uid}");
    let status = std::process::Command::new("launchctl")
        .args(["bootstrap", &domain, plist_dst.to_str().unwrap_or("")])
        .status();

    match status {
        Ok(s) if s.success() => eprintln!("[VEGA] LaunchAgent 등록 완료"),
        Ok(_) => eprintln!("[VEGA] LaunchAgent 이미 등록됨 (무시)"),
        Err(e) => eprintln!("[VEGA] launchctl 실행 실패: {e}"),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init());

    #[cfg(feature = "client")]
    {
        builder = builder.invoke_handler(tauri::generate_handler![
            client_config::get_server_url,
            client_config::set_server_url,
            client_config::get_lang,
            client_config::set_lang,
        ]);
    }
    #[cfg(feature = "daemon")]
    {
        builder = builder.invoke_handler(tauri::generate_handler![
            client_config::get_lang,
            client_config::set_lang,
        ]);
    }

    #[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
    {
        use tauri_plugin_global_shortcut::{Code, Modifiers, Shortcut, ShortcutState};
        let toggle_shortcut = Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::KeyV);
        builder = builder.plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, shortcut, event| {
                    if event.state() == ShortcutState::Pressed && shortcut == &toggle_shortcut {
                        toggle_main_window(app);
                    }
                })
                .build(),
        );
    }

    builder
        .setup(|app| {
            let win = WebviewWindowBuilder::new(app, "main", make_loading_page())
                .title("VEGA")
                .inner_size(980.0, 760.0)
                .min_inner_size(420.0, 480.0)
                .resizable(true)
                .center()
                .title_bar_style(tauri::TitleBarStyle::Overlay)
                .hidden_title(true)
                .build()?;

            // 백엔드 준비 후 실제 URL로 전환 (흰 화면 방지)
            wait_and_navigate(win, backend_url());

            #[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
            {
                use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};
                let toggle_shortcut =
                    Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::KeyV);
                let _ = app.global_shortcut().register(toggle_shortcut);
            }

            // LaunchAgent 등록 (daemon 모드 첫 실행 시)
            #[cfg(feature = "daemon")]
            ensure_launchagent(&app.handle());

            // 트레이 메뉴
            let s = strings();
            let show_item     = MenuItemBuilder::with_id("show",     s.open).build(app)?;
            let hide_item     = MenuItemBuilder::with_id("hide",     s.hide).build(app)?;
            let settings_item = MenuItemBuilder::with_id("settings", s.settings).build(app)?;
            let quit_item     = MenuItemBuilder::with_id("quit",     s.quit).build(app)?;
            let menu = MenuBuilder::new(app)
                .items(&[&show_item, &hide_item])
                .separator()
                .items(&[&settings_item])
                .separator()
                .items(&[&quit_item])
                .build()?;

            TrayIconBuilder::with_id("vega-tray")
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip(s.tooltip)
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show" => show_main_window(app),
                    "hide" => {
                        if let Some(win) = app.get_webview_window("main") {
                            let _ = win.hide();
                        }
                    }
                    "settings" => open_settings_window(app),
                    "quit" => {
                        // GUI 셸만 종료 — LaunchAgent 데몬은 계속 실행
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    let _ = window.hide();
                    api.prevent_close();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("VEGA desktop error");
}
