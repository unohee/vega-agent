// VEGA 데스크탑 셸 본체.
//
// --features client  : 백엔드 sidecar 없이 외부 서버 URL에 붙는 얇은 클라이언트.
//                      설정 창에서 서버 URL + 언어 변경 가능.
// --features daemon  : 첫 실행 시 LaunchAgent를 등록해 백엔드를 상시 데몬으로 실행.
//
// 공통: 트레이 아이콘 + 창 토글 + 전역 단축키(Cmd+Shift+V) + 언어 선택기

pub mod client_config;

// strings()는 트레이/설정창 라벨용 — 데스크탑 전용.
#[cfg(desktop)]
use client_config::strings;

use tauri::{WebviewUrl, WebviewWindowBuilder};
// Manager: get_webview_window/resource_dir — 데스크탑 또는 데스크탑 daemon(sidecar)에서만 사용.
// 모바일에선 daemon 코드가 not(mobile)로 배제되므로 Manager도 불필요.
#[cfg(any(desktop, all(feature = "daemon", not(mobile))))]
use tauri::Manager;

// 트레이/메뉴/창 이벤트는 데스크탑 전용 — 모바일 빌드에선 미사용이라 가드한다.
#[cfg(desktop)]
use tauri::{
    menu::{MenuBuilder, MenuItemBuilder},
    tray::TrayIconBuilder,
    WindowEvent,
};

// ── 셸 로깅 ───────────────────────────────────────────────────────────────────
// 배포본 .app 은 콘솔이 없어 eprintln! 출력이 사라진다. Rust 셸 측 진단을
// Python 백엔드와 같은 ~/Library/Logs/VEGA/ 디렉터리의 파일에 남긴다.
// (번들 내부가 아니라 macOS 표준 사용자 로그 위치 — 코드서명/자동업데이트 안전)

/// 로그 디렉터리(~/Library/Logs/VEGA). 없으면 생성한다.
fn log_dir() -> std::path::PathBuf {
    let dir = dirs_next::home_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("/tmp"))
        .join("Library/Logs/VEGA");
    let _ = std::fs::create_dir_all(&dir);
    dir
}

/// 셸 로그 파일 경로(~/Library/Logs/VEGA/vega-shell.log).
fn shell_log_path() -> std::path::PathBuf {
    log_dir().join("vega-shell.log")
}

/// 한 줄을 셸 로그 파일에 append 하고 stderr 로도 보낸다. 실패는 무시.
fn shell_log(msg: &str) {
    use std::io::Write;
    eprintln!("{msg}");
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(shell_log_path())
    {
        let _ = writeln!(f, "{msg}");
    }
}

/// eprintln! 대체 — 포맷 인자를 받아 셸 로그 파일+stderr 양쪽에 남긴다.
macro_rules! vlog {
    ($($arg:tt)*) => { crate::shell_log(&format!($($arg)*)) };
}

fn backend_is_listening() -> bool {
    std::net::TcpStream::connect("127.0.0.1:8100").is_ok()
}

/// 백엔드 /api/health 가 HTTP 200을 돌려주는지 std TcpStream 으로 직접 확인한다.
/// reqwest 등 무거운 의존성 없이 한 번의 GET 으로 판단. uvicorn 이 막 떠서
/// TCP 는 열렸지만 앱 startup(MCP 등록 등)이 안 끝났으면 200이 아니므로
/// 진짜 "준비됨" 신호로 쓸 수 있다.
fn backend_health_ok() -> bool {
    use std::io::{Read, Write};
    let mut stream = match std::net::TcpStream::connect("127.0.0.1:8100") {
        Ok(s) => s,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(std::time::Duration::from_millis(800)));
    let _ = stream.set_write_timeout(Some(std::time::Duration::from_millis(800)));
    let req = "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 64];
    match stream.read(&mut buf) {
        Ok(n) if n >= 12 => buf.starts_with(b"HTTP/1.1 200") || buf.starts_with(b"HTTP/1.0 200"),
        _ => false,
    }
}

/// 빌드타임에 주입하는 모바일 기본 백엔드 URL.
/// `VEGA_SERVER_URL` 환경변수가 있으면 그 값을, 없으면 localhost(시뮬레이터용)를 쓴다.
/// 예) VEGA_SERVER_URL=https://vega.example.com cargo tauri ios build
#[cfg(mobile)]
const MOBILE_DEFAULT_SERVER_URL: &str = match option_env!("VEGA_SERVER_URL") {
    Some(u) => u,
    None => "http://localhost:8100",
};

/// 백엔드 베이스 URL (스킴+호스트+포트, 경로 없음).
fn backend_base() -> String {
    // 모바일: 저장된 client config가 기본값이 아니면 그것을, 아니면 빌드타임 주입값을 쓴다.
    #[cfg(mobile)]
    {
        let cfg = client_config::load_config();
        let stored = cfg.server_url.trim_end_matches('/').to_string();
        if stored == "http://localhost:8100" {
            return MOBILE_DEFAULT_SERVER_URL.trim_end_matches('/').to_string();
        }
        return stored;
    }
    #[cfg(all(feature = "client", not(mobile)))]
    {
        let cfg = client_config::load_config();
        return cfg.server_url.trim_end_matches('/').to_string();
    }
    #[cfg(all(not(feature = "client"), not(mobile)))]
    "http://localhost:8100".to_string()
}

/// 모바일(iOS/Android) 여부 — 컴파일 타임 상수.
/// 모바일은 로컬 sidecar 백엔드를 띄울 수 없으므로 항상 원격 URL에 바로 붙는다.
#[cfg(any(target_os = "ios", target_os = "android"))]
const IS_MOBILE: bool = true;
#[cfg(not(any(target_os = "ios", target_os = "android")))]
const IS_MOBILE: bool = false;

/// 첫 진입 URL. `/entry`는 온보딩 완료 여부에 따라 서버가
/// `/install`(API 키 등록 마법사) 또는 `/chat`으로 302 리다이렉트한다.
fn backend_url() -> String {
    format!("{}/entry", backend_base())
}

/// 백엔드가 응답할 때까지 최대 120초 폴링 후 창에 URL 로드.
///
/// 모바일(iOS/Android)에서는 sidecar 백엔드가 없고 원격 서버에 붙으므로
/// 로컬 TCP 폴링을 건너뛰고 곧바로 원격 URL을 로드한다. 도달 불가 시
/// WebView 자체가 네트워크 오류를 표시한다.
fn wait_and_navigate(win: tauri::WebviewWindow, url: String) {
    if IS_MOBILE {
        let _ = win.eval(&format!("window.location.href = {url:?}"));
        return;
    }
    std::thread::spawn(move || {
        let health = format!("{}/api/health", backend_base());

        // index.html 의 window.vegaProgress(pct, label) 를 호출하는 헬퍼.
        let progress = |win: &tauri::WebviewWindow, pct: u32, label: &str| {
            let safe = label.replace('\'', "");
            let _ = win.eval(&format!("window.vegaProgress && window.vegaProgress({pct}, '{safe}')"));
        };

        // 폴링: 500ms × 240 = 최대 120초.
        // 단계별 실제 신호로 진행률을 채운다.
        //  - 프로세스 부팅 대기(TCP 미연결): 경과 시간 기반 0→80% (백엔드 spawn~uvicorn 기동)
        //  - TCP listen 감지: 85% ("서버 응답 확인 중")
        //  - health 200: 100% 후 navigate (앱 startup·MCP 등록까지 완료)
        let mut listening_seen = false;
        for i in 0..240u32 {
            if backend_health_ok() {
                progress(&win, 100, "준비 완료");
                std::thread::sleep(std::time::Duration::from_millis(180));
                let _ = win.eval(&format!("window.location.href = {:?}", url));
                return;
            }
            if backend_is_listening() {
                if !listening_seen {
                    listening_seen = true;
                    progress(&win, 88, "서버 응답 확인 중…");
                }
                // TCP 는 열렸으나 아직 health 미준비 — 90~96% 사이를 천천히 채운다.
                let creep = 90 + (i % 7);
                progress(&win, creep.min(96), "백엔드 초기화 중…");
            } else {
                // 아직 프로세스 기동 전 — 경과 시간으로 0→80% (약 16초에 80% 도달).
                let pct = (i * 5).min(80);
                progress(&win, pct, "VEGA 백엔드 시작 중…");
            }
            std::thread::sleep(std::time::Duration::from_millis(500));
        }
        // 120초 후에도 안 뜨면 오류 페이지
        let _ = win.eval(&format!(
            "document.body.innerHTML = '<div style=\"font-family:sans-serif;padding:40px;color:#e6edf3;background:#0d1117\"><h2>백엔드 연결 실패</h2><p>VEGA 서버({})에 접속할 수 없습니다.</p><p style=\"color:#9aa4b2\">~/Library/Logs/VEGA/ 의 로그를 확인하세요.</p></div>'",
            health
        ));
    });
}

fn make_loading_page() -> WebviewUrl {
    // 로딩 중 표시할 인라인 HTML — 백엔드 준비되면 JS가 교체
    WebviewUrl::App("index.html".into())
}

// ── 자동 업데이트 (데스크탑 전용) ─────────────────────────────────────────────
// 앱 시작 시 백그라운드로 CF R2(plugins.updater.endpoints)에서 최신 버전을 조회한다.
// 새 버전이 있으면 조용히 내려받아 설치 후 앱을 재시작한다. 사용자 개입 없음.
// 엔드포인트가 placeholder(미배포)이거나 네트워크 실패 시 조용히 무시한다.
#[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
fn spawn_update_check(app: tauri::AppHandle) {
    use tauri_plugin_updater::UpdaterExt;

    tauri::async_runtime::spawn(async move {
        // endpoints 미설정/placeholder면 updater()가 에러를 내므로 전부 조용히 흘린다.
        let updater = match app.updater() {
            Ok(u) => u,
            Err(e) => {
                vlog!("[VEGA] updater 초기화 skip: {e}");
                return;
            }
        };

        match updater.check().await {
            Ok(Some(update)) => {
                let version = update.version.clone();
                vlog!("[VEGA] 새 버전 발견: {version} — 다운로드 시작");
                match update.download_and_install(|_chunk, _total| {}, || {}).await {
                    Ok(_) => {
                        vlog!("[VEGA] 업데이트 설치 완료 — 재시작");
                        app.restart();
                    }
                    Err(e) => vlog!("[VEGA] 업데이트 설치 실패: {e}"),
                }
            }
            Ok(None) => vlog!("[VEGA] 최신 버전 — 업데이트 없음"),
            Err(e) => vlog!("[VEGA] 업데이트 체크 실패(무시): {e}"),
        }
    });
}

#[cfg(desktop)]
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

#[cfg(desktop)]
fn show_main_window(app: &tauri::AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
}

#[cfg(desktop)]
fn open_settings_window(app: &tauri::AppHandle) {
    open_settings_window_at(app, "");
}

/// section이 비어있지 않으면 settings.html#<section>으로 열어 해당 탭부터 표시.
#[cfg(desktop)]
fn open_settings_window_at(app: &tauri::AppHandle, section: &str) {
    if let Some(win) = app.get_webview_window("settings") {
        let _ = win.show();
        let _ = win.set_focus();
        // 이미 열려있으면 fragment를 갱신해 해당 탭으로 전환(settings.html의 hashchange 처리).
        if !section.is_empty() {
            let _ = win.eval(&format!("window.location.hash = {section:?}; window.dispatchEvent(new HashChangeEvent('hashchange'))"));
        }
        return;
    }

    let base = if cfg!(feature = "client") { "client-settings.html" } else { "settings.html" };
    let settings_html = if section.is_empty() { base.to_string() } else { format!("{base}#{section}") };
    let title = strings().settings_title;
    let _ = WebviewWindowBuilder::new(app, "settings", WebviewUrl::App(settings_html.into()))
        .title(title)
        .inner_size(880.0, 640.0)
        .min_inner_size(640.0, 480.0)
        .resizable(true)
        .center()
        .title_bar_style(tauri::TitleBarStyle::Overlay)
        .hidden_title(true)
        .build();
}


// ── LaunchAgent 관리 (daemon 전용) ────────────────────────────────────────────

#[cfg(all(feature = "daemon", not(mobile)))]
fn launchagent_plist_path() -> std::path::PathBuf {
    dirs_next::home_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join("Library/LaunchAgents/com.unohee.vega-backend.plist")
}

#[cfg(all(feature = "daemon", not(mobile)))]
fn resources_dir(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    app.path().resource_dir().ok()
}

#[cfg(all(feature = "daemon", not(mobile)))]
fn bundled_backend_path(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    app.path()
        .resource_dir()
        .ok()
        .and_then(|p| p.parent().map(|contents| contents.join("MacOS/vega-backend")))
}

#[cfg(all(feature = "daemon", not(mobile)))]
fn spawn_backend_directly(app: &tauri::AppHandle) {
    if backend_is_listening() {
        return;
    }
    let Some(backend) = bundled_backend_path(app) else {
        vlog!("[VEGA] 백엔드 실행 파일 경로 확인 실패");
        return;
    };
    if !backend.exists() {
        vlog!("[VEGA] 백엔드 실행 파일 없음: {}", backend.display());
        return;
    }

    let log = log_dir();
    let stdout = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log.join("vega-backend.stdout.log"))
        .ok()
        .map(std::process::Stdio::from)
        .unwrap_or_else(std::process::Stdio::null);
    let stderr = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log.join("vega-backend.stderr.log"))
        .ok()
        .map(std::process::Stdio::from)
        .unwrap_or_else(std::process::Stdio::null);

    match std::process::Command::new(&backend)
        .stdout(stdout)
        .stderr(stderr)
        .spawn()
    {
        Ok(child) => vlog!("[VEGA] 백엔드 직접 실행 fallback: pid={}", child.id()),
        Err(e) => vlog!("[VEGA] 백엔드 직접 실행 실패: {e}"),
    }
}

/// Resources의 LaunchAgent plist를 매 실행마다 갱신하고 재등록한다.
/// 기존 백엔드가 떠 있으면 새 앱 설치 후에도 오래된 프로세스가 8100을 계속 잡을 수 있으므로
/// bootout/bootstrap/kickstart로 현재 /Applications/VEGA.app의 백엔드를 강제로 반영한다.
#[cfg(all(feature = "daemon", not(mobile)))]
fn ensure_launchagent(app: &tauri::AppHandle) -> bool {
    // launchd 는 StandardOutPath/StandardErrorPath 의 상위 디렉터리를 자동 생성하지 않는다.
    // plist 등록 전에 로그 디렉터리를 만들어두지 않으면 백엔드 출력이 어디에도 안 남는다.
    let _ = log_dir();

    let plist_dst = launchagent_plist_path();

    if let Some(res) = resources_dir(app) {
        let plist_src = res.join("com.unohee.vega-backend.plist");
        if plist_src.exists() {
            if let Some(parent) = plist_dst.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            match std::fs::read_to_string(&plist_src) {
                Ok(content) => {
                    let home = dirs_next::home_dir()
                        .map(|p| p.to_string_lossy().into_owned())
                        .unwrap_or_else(|| "/tmp".to_string());
                    let replaced = content.replace("__HOME__", &home);
                    if let Err(e) = std::fs::write(&plist_dst, replaced) {
                        vlog!("[VEGA] LaunchAgent plist 쓰기 실패: {e}");
                        return false;
                    }
                }
                Err(e) => {
                    vlog!("[VEGA] LaunchAgent plist 읽기 실패: {e}");
                    return false;
                }
            }
        } else {
            vlog!("[VEGA] LaunchAgent plist 소스 없음: {}", plist_src.display());
            return false;
        }
    }

    let uid = unsafe { libc::getuid() };
    let domain = format!("gui/{uid}");
    let label = "com.unohee.vega-backend";
    let target = format!("{domain}/{label}");

    // 기존 등록/프로세스를 먼저 내린다. 미등록이면 실패하므로 결과는 무시한다.
    let _ = std::process::Command::new("launchctl")
        .args(["bootout", &domain, plist_dst.to_str().unwrap_or("")])
        .status();
    let _ = std::process::Command::new("launchctl")
        .args(["bootout", &target])
        .status();

    let bootstrap = std::process::Command::new("launchctl")
        .args(["bootstrap", &domain, plist_dst.to_str().unwrap_or("")])
        .status();

    match bootstrap {
        Ok(s) if s.success() => vlog!("[VEGA] LaunchAgent 등록 완료"),
        Ok(s) => {
            vlog!("[VEGA] LaunchAgent 등록 실패: {s}");
            return false;
        }
        Err(e) => {
            vlog!("[VEGA] launchctl 실행 실패: {e}");
            return false;
        }
    }

    let kickstart = std::process::Command::new("launchctl")
        .args(["kickstart", "-k", &target])
        .status();
    match kickstart {
        Ok(s) if s.success() => true,
        Ok(s) => {
            vlog!("[VEGA] LaunchAgent kickstart 실패: {s}");
            false
        }
        Err(e) => {
            vlog!("[VEGA] launchctl kickstart 실행 실패: {e}");
            false
        }
    }
}

/// 트레이 "백엔드 재시작" — 백엔드 데몬을 kickstart -k 로 재기동 (INT-1412).
/// 개발(vega.server)·배포(vega-backend) label 둘 다 시도해 환경 무관 동작.
#[cfg(all(feature = "daemon", not(mobile)))]
fn restart_backend() {
    let uid = unsafe { libc::getuid() };
    let domain = format!("gui/{uid}");
    for label in ["com.unohee.vega.server", "com.unohee.vega-backend"] {
        let target = format!("{domain}/{label}");
        // 로드돼 있을 때만 kickstart (미등록이면 조용히 skip)
        let loaded = std::process::Command::new("launchctl")
            .args(["print", &target])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        if loaded {
            let _ = std::process::Command::new("launchctl")
                .args(["kickstart", "-k", &target])
                .status();
            vlog!("[VEGA] 백엔드 재시작: {target}");
        }
    }
}

/// 외부 URL을 OS 기본 브라우저로 연다.
///
/// Tauri WebView에는 "새 탭" 개념이 없어 JS의 window.open('...','_blank')은
/// 아무 일도 하지 않는다(브라우저가 안 뜸). OAuth 동의 화면처럼 외부 브라우저로
/// 열어야 하는 흐름은 이 커맨드를 invoke 해서 시스템 브라우저로 띄운다.
/// 마법사 폴링은 WebView에 남아 콜백 완료를 감지한다.
#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    // 안전: http(s)만 허용 — 임의 스킴/명령 주입 방지.
    if !(url.starts_with("http://") || url.starts_with("https://")) {
        return Err(format!("허용되지 않은 URL 스킴: {url}"));
    }
    #[cfg(target_os = "macos")]
    let prog = "open";
    #[cfg(target_os = "linux")]
    let prog = "xdg-open";
    #[cfg(target_os = "windows")]
    let prog = "explorer";
    #[cfg(not(any(target_os = "macos", target_os = "linux", target_os = "windows")))]
    {
        // 모바일 등: 외부 브라우저 실행기가 없으므로 에러 반환(호출 측이 폴백 처리).
        return Err(format!("이 플랫폼에선 외부 URL 열기를 지원하지 않습니다: {url}"));
    }
    #[cfg(any(target_os = "macos", target_os = "linux", target_os = "windows"))]
    std::process::Command::new(prog)
        .arg(&url)
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("브라우저 열기 실패: {e}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init());

    // 자동 업데이트 플러그인 — 데스크탑 전용 (모바일은 스토어 정책상 자체 업데이트 불가).
    #[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
    {
        builder = builder.plugin(tauri_plugin_updater::Builder::new().build());
    }

    // client feature 또는 모바일: 서버 URL/언어 변경 커맨드 등록.
    #[cfg(any(feature = "client", mobile))]
    {
        builder = builder.invoke_handler(tauri::generate_handler![
            client_config::get_server_url,
            client_config::set_server_url,
            client_config::get_lang,
            client_config::set_lang,
            open_url,
        ]);
    }
    // 데스크탑 daemon 전용 (client/mobile이 아닐 때만): 언어 + 설정창 + URL 열기 커맨드.
    #[cfg(all(feature = "daemon", not(mobile), not(feature = "client")))]
    {
        builder = builder.invoke_handler(tauri::generate_handler![
            client_config::get_lang,
            client_config::set_lang,
            open_url,
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
            // 프론트(remote chat.html)에서 설정 창을 여는 경로.
            // remote 콘텐츠는 ACL상 앱 커스텀 invoke 커맨드가 막히므로(core:event는 허용),
            // invoke 대신 "open-settings" 이벤트를 emit하면 여기서 받아 창을 연다.
            // payload는 섹션 문자열(JSON 문자열, 예: "\"model\"") 또는 빈 문자열.
            #[cfg(desktop)]
            {
                use tauri::Listener;
                let handle = app.handle().clone();
                app.listen_any("open-settings", move |event| {
                    let raw = event.payload();
                    let section = serde_json::from_str::<String>(raw).unwrap_or_else(|_| raw.trim_matches('"').to_string());
                    open_settings_window_at(&handle, &section);
                });
            }

            // 데스크탑: 크기 지정 + macOS 오버레이 타이틀바.
            // 모바일(iOS/Android): 전체화면 단일 윈도우 — TitleBarStyle/inner_size 등은
            // macOS 전용이거나 무의미하므로 적용하지 않는다.
            let win_builder = WebviewWindowBuilder::new(app, "main", make_loading_page())
                .title("VEGA");

            #[cfg(desktop)]
            let win_builder = win_builder
                .inner_size(980.0, 760.0)
                .min_inner_size(420.0, 480.0)
                .resizable(true)
                .center()
                .title_bar_style(tauri::TitleBarStyle::Overlay)
                .hidden_title(true);

            let win = win_builder.build()?;

            #[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
            {
                use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};
                let toggle_shortcut =
                    Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::KeyV);
                let _ = app.global_shortcut().register(toggle_shortcut);
            }

            // 자동 업데이트 백그라운드 체크 (데스크탑 전용)
            #[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
            {
                spawn_update_check(app.handle().clone());
            }

            // LaunchAgent 등록 (daemon 모드 첫 실행 시)
            #[cfg(all(feature = "daemon", not(mobile)))]
            if !ensure_launchagent(&app.handle()) {
                spawn_backend_directly(&app.handle());
            }

            // 백엔드 준비 후 실제 URL로 전환 (흰 화면 방지)
            wait_and_navigate(win, backend_url());

            // 트레이 메뉴 — 데스크탑 전용 (모바일엔 시스템 트레이가 없음)
            #[cfg(desktop)]
            {
            let s = strings();
            let show_item     = MenuItemBuilder::with_id("show",     s.open).build(app)?;
            let hide_item     = MenuItemBuilder::with_id("hide",     s.hide).build(app)?;
            let settings_item = MenuItemBuilder::with_id("settings", s.settings).build(app)?;
            let restart_item  = MenuItemBuilder::with_id("restart-backend", s.restart).build(app)?;
            let quit_item     = MenuItemBuilder::with_id("quit",     s.quit).build(app)?;
            let menu = MenuBuilder::new(app)
                .items(&[&show_item, &hide_item])
                .separator()
                .items(&[&settings_item, &restart_item])
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
                    "restart-backend" => {
                        #[cfg(all(feature = "daemon", not(mobile)))]
                        restart_backend();
                    }
                    "quit" => {
                        // GUI 셸만 종료 — LaunchAgent 데몬은 계속 실행
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;
            } // end #[cfg(desktop)] 트레이 블록

            Ok(())
        })
        .on_window_event(|window, event| {
            // 창 닫기 시 숨김 처리는 데스크탑 전용 (트레이로 다시 열 수 있으므로).
            // 모바일엔 트레이가 없어 닫기를 가로채면 앱을 다시 띄울 수 없다.
            #[cfg(desktop)]
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    let _ = window.hide();
                    api.prevent_close();
                }
            }
            #[cfg(not(desktop))]
            let _ = (window, event);
        })
        .run(tauri::generate_context!())
        .expect("VEGA desktop error");
}
