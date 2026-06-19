// VEGA 데스크탑 셸 본체.
//
// --features client  : 백엔드 sidecar 없이 외부 서버 URL에 붙는 얇은 클라이언트.
//                      설정 창에서 서버 URL + 언어 변경 가능.
// --features daemon  : 첫 실행 시 LaunchAgent를 등록해 백엔드를 상시 데몬으로 실행.
//
// 공통: 트레이 아이콘 + 창 토글 + 언어 선택기

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
// Python 백엔드와 같은 로그 디렉터리의 파일에 남긴다.
// (번들 내부가 아니라 OS 표준 사용자 로그 위치 — 코드서명/자동업데이트 안전)

/// 로그 디렉터리. 없으면 생성한다.
/// macOS: ~/Library/Logs/VEGA (Python 백엔드 data_paths.log_dir 와 동일)
/// Windows: %LOCALAPPDATA%\VEGA\logs / Linux: ~/.local/share/VEGA/logs
fn log_dir() -> std::path::PathBuf {
    #[cfg(target_os = "macos")]
    let dir = dirs_next::home_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("/tmp"))
        .join("Library/Logs/VEGA");
    #[cfg(not(target_os = "macos"))]
    let dir = dirs_next::data_local_dir()
        .unwrap_or_else(std::env::temp_dir)
        .join("VEGA")
        .join("logs");
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
    std::net::TcpStream::connect("127.0.0.1:8100").is_ok() // cxt-ignore: fake_data
}

/// 백엔드 /api/health 가 HTTP 200을 돌려주는지 std TcpStream 으로 직접 확인한다.
/// reqwest 등 무거운 의존성 없이 한 번의 GET 으로 판단. uvicorn 이 막 떠서
/// TCP 는 열렸지만 앱 startup(MCP 등록 등)이 안 끝났으면 200이 아니므로
/// 진짜 "준비됨" 신호로 쓸 수 있다.
fn backend_health_ok() -> bool {
    use std::io::{Read, Write};
    let mut stream = match std::net::TcpStream::connect("127.0.0.1:8100") { // cxt-ignore: fake_data
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
/// 예) VEGA_SERVER_URL=https://vega.example.com cargo tauri ios build  // cxt-ignore: fake_data
#[cfg(mobile)]
const MOBILE_DEFAULT_SERVER_URL: &str = match option_env!("VEGA_SERVER_URL") {
    Some(u) => u,
    None => "http://localhost:8100", // cxt-ignore: fake_data
};

/// 백엔드 베이스 URL (스킴+호스트+포트, 경로 없음).
fn backend_base() -> String {
    // 모바일: 저장된 client config가 기본값이 아니면 그것을, 아니면 빌드타임 주입값을 쓴다.
    #[cfg(mobile)]
    {
        let cfg = client_config::load_config();
        let stored = cfg.server_url.trim_end_matches('/').to_string();
        if stored == "http://localhost:8100" { // cxt-ignore: fake_data
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
    "http://localhost:8100".to_string() // cxt-ignore: fake_data
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
/// 오프셋 기반 로그 파일 tail — 폴링마다 새로 추가된 라인만 반환.
/// 파일이 없으면 조용히 빈 결과, 로테이션(길이 감소) 시 처음부터 다시 읽는다.
struct LogTail {
    path: std::path::PathBuf,
    pos: u64,
}

impl LogTail {
    /// start_pos=None 이면 현재 EOF부터(이전 세션 라인 제외), Some(n)이면 n부터.
    fn new(path: std::path::PathBuf, start_pos: Option<u64>) -> Self {
        let pos = start_pos.unwrap_or_else(|| {
            std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0)
        });
        Self { path, pos }
    }

    fn read_new_lines(&mut self) -> Vec<String> {
        use std::io::{Read, Seek, SeekFrom};
        let Ok(mut f) = std::fs::File::open(&self.path) else { return Vec::new() };
        let len = f.metadata().map(|m| m.len()).unwrap_or(0);
        if len < self.pos {
            self.pos = 0; // 로테이션/truncate
        }
        if len == self.pos {
            return Vec::new();
        }
        if f.seek(SeekFrom::Start(self.pos)).is_err() {
            return Vec::new();
        }
        let mut buf = String::new();
        if f.take(64 * 1024).read_to_string(&mut buf).is_err() {
            // UTF-8 경계가 깨졌으면 이번 틱은 건너뛴다 (다음 틱에 재시도)
            return Vec::new();
        }
        // 마지막 라인이 개행 없이 잘려 있으면 다음 틱으로 미룬다.
        let consumed = match buf.rfind('\n') {
            Some(i) => i + 1,
            None => return Vec::new(),
        };
        self.pos += consumed as u64;
        buf[..consumed]
            .lines()
            .filter(|l| !l.trim().is_empty())
            .map(|l| {
                let mut s = l.to_string();
                if s.len() > 240 {
                    let mut cut = 240;
                    while cut > 0 && !s.is_char_boundary(cut) {
                        cut -= 1;
                    }
                    s.truncate(cut);
                    s.push('…');
                }
                s
            })
            .collect()
    }
}

fn wait_and_navigate(win: tauri::WebviewWindow, url: String, shell_log_from: u64) {
    if IS_MOBILE {
        let _ = win.eval(&format!("window.location.href = {url:?}")); // cxt-ignore: security
        return;
    }
    std::thread::spawn(move || {
        let health = format!("{}/api/health", backend_base());

        // index.html 의 window.vegaProgress(pct, label) 를 호출하는 헬퍼.
        let progress = |win: &tauri::WebviewWindow, pct: u32, label: &str| {
            let safe = label.replace('\'', "");
            let _ = win.eval(&format!("window.vegaProgress && window.vegaProgress({pct}, '{safe}')")); // cxt-ignore: security
        };

        // 실제 기동 로그 tail — 셸 로그(업데이트 체크·LaunchAgent·spawn)와 백엔드
        // stdout/stderr(uvicorn·DB·MCP 초기화)를 그대로 로딩 화면 콘솔에 흘린다.
        // 연출된 가짜 단계가 아니라 진짜 로그다 (INT-1465).
        let log = log_dir();
        let mut tails = [
            LogTail::new(shell_log_path(), Some(shell_log_from)),
            LogTail::new(log.join("vega-backend.stdout.log"), None),
            LogTail::new(log.join("vega-backend.stderr.log"), None),
        ];
        let push_logs = |win: &tauri::WebviewWindow, tails: &mut [LogTail]| {
            let mut lines: Vec<String> = Vec::new();
            for t in tails.iter_mut() {
                lines.extend(t.read_new_lines());
            }
            // 폭주 방지 — 틱당 마지막 10줄만
            let skip = lines.len().saturating_sub(10);
            for line in lines.into_iter().skip(skip) {
                if let Ok(js_str) = serde_json::to_string(&line) {
                    let _ = win.eval(&format!("window.vegaLog && window.vegaLog({js_str})")); // cxt-ignore: security
                }
            }
        };

        // 폴링: 500ms × 240 = 최대 120초.
        // 단계별 실제 신호로 진행률을 채운다.
        //  - 프로세스 부팅 대기(TCP 미연결): 경과 시간 기반 0→80% (백엔드 spawn~uvicorn 기동)
        //  - TCP listen 감지: 85% ("서버 응답 확인 중")
        //  - health 200: 100% 후 navigate (앱 startup·MCP 등록까지 완료)
        let mut listening_seen = false;
        for i in 0..240u32 {
            push_logs(&win, &mut tails);
            if backend_health_ok() {
                push_logs(&win, &mut tails);
                progress(&win, 100, "준비 완료");
                std::thread::sleep(std::time::Duration::from_millis(380));
                let _ = win.eval(&format!("window.location.href = {:?}", url)); // cxt-ignore: security
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
        // cxt-ignore-next-line: security
        let _ = win.eval(&format!(
            "document.body.innerHTML = '<div style=\"font-family:sans-serif;padding:40px;color:#e6edf3;background:#0d1117\"><h2>백엔드 연결 실패</h2><p>VEGA 서버({})에 접속할 수 없습니다.</p><p style=\"color:#9aa4b2\">{} 의 로그를 확인하세요.</p></div>'",
            health,
            // Windows 역슬래시는 JS 문자열 리터럴에서 이스케이프로 먹히므로 슬래시로 표기
            log_dir().display().to_string().replace('\\', "/")
        ));
    });
}

fn make_loading_page() -> WebviewUrl {
    // 로딩 중 표시할 인라인 HTML — 백엔드 준비되면 JS가 교체
    WebviewUrl::App("index.html".into())
}

// ── 자동 업데이트 (데스크탑 전용) ─────────────────────────────────────────────
// 앱 시작 시 백그라운드로 CF R2(plugins.updater.endpoints)에서 최신 버전을 조회한다.
// 새 버전이 있으면 조용히 내려받아 설치만 하고 **재시작은 하지 않는다**(작업 중 강제
// 재시작 방지). 설치 완료 후 프론트에 `update-ready` 이벤트를 emit → 비방해적 배너로
// "다음 실행 때 적용" 안내. 사용자가 평소처럼 앱을 껐다 켜면 새 버전이 뜬다.
// (Claude/ChatGPT 데스크탑 방식.) 엔드포인트 placeholder/네트워크 실패 시 조용히 무시.
//
// remote 페이지(localhost:8100)에선 커스텀 invoke가 ACL 차단되므로, 프론트 알림은
// invoke 가 아니라 event emit + listen 패턴으로 전달한다(tauri-remote-acl-invoke-trap).
// 업데이트 체크 주기 — 앱이 며칠 켜져 있어도 새 릴리즈를 잡도록 주기적으로 폴링한다.
// 시작 직후 1회 + 이후 이 간격마다 반복 + 창 포커스 시(트레이 상주로 재실행이 드문 점 보완).
// "언제나 새 업데이트를 받도록" 주기를 1시간으로 단축 (INT-1561, 2026-06-19).
#[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
const UPDATE_CHECK_INTERVAL_SECS: u64 = 60 * 60; // 1시간

// 업데이트 체크 공유 상태 — 주기 loop 와 창 포커스 핸들러가 함께 쓴다.
// 설치·안내한 버전(매 주기/포커스마다 같은 새 버전 재설치·재알림 방지) +
// 마지막 체크 시각(창 포커스 연타 시 과다 체크 디바운스).
#[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
static UPDATE_INSTALLED_VER: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
#[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
static UPDATE_LAST_CHECK: std::sync::Mutex<Option<std::time::Instant>> = std::sync::Mutex::new(None);

// 앱 시작 직후 1회 + UPDATE_CHECK_INTERVAL_SECS 마다 반복 체크.
// tokio 직접 의존이 없어 std::thread + async_runtime::block_on 으로 주기 loop 를 돌린다
// (기존 코드 관례 — 340행 std::thread sleep 와 동일).
#[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
fn spawn_update_check(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        loop {
            tauri::async_runtime::block_on(run_update_check(&app));
            std::thread::sleep(std::time::Duration::from_secs(UPDATE_CHECK_INTERVAL_SECS));
        }
    });
}

// 1회 업데이트 체크 — 새 버전이면 조용히 내려받아 설치만 하고(재시작 안 함, INT-1434),
// OS 알림 + `update-ready` 이벤트로 안내한다. 사용자는 배너의 "지금 재시작"으로 적용한다.
#[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
async fn run_update_check(app: &tauri::AppHandle) {
    use tauri::Emitter;
    use tauri_plugin_updater::UpdaterExt;

    // 창 포커스 핸들러가 연타로 호출해도 과다 체크되지 않도록 10분 디바운스.
    // 주기 loop(1h)는 항상 통과한다. 첫 호출(시작 직후)도 last=None 이라 통과.
    {
        let now = std::time::Instant::now();
        let mut last = UPDATE_LAST_CHECK.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(t) = *last {
            if now.duration_since(t).as_secs() < 600 {
                return;
            }
        }
        *last = Some(now);
    }

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
            // updater.check()는 실행 중 버전이 안 바뀌어 재시작 전까지 매 주기 같은 새
            // 버전을 반환한다. 이미 설치·안내했으면 재설치/재알림하지 않는다(loop/포커스 공유).
            if UPDATE_INSTALLED_VER.lock().unwrap_or_else(|e| e.into_inner()).as_deref()
                == Some(version.as_str())
            {
                return;
            }
            vlog!("[VEGA] 새 버전 발견: {version} — 백그라운드 다운로드 시작");
            // download + install 만 수행하고 restart 는 호출하지 않는다.
            // macOS 에선 .app 이 교체되어 재시작 때 새 버전이 적용된다.
            match update.download_and_install(|_chunk, _total| {}, || {}).await {
                Ok(_) => {
                    *UPDATE_INSTALLED_VER.lock().unwrap_or_else(|e| e.into_inner()) =
                        Some(version.clone());
                    vlog!("[VEGA] 업데이트 설치 완료(대기) — 재시작 시 v{version} 적용");
                    // OS 알림 — 앱 창을 안 보고 있어도 재시작 안내가 보이도록 (INT-1467).
                    // 실패(권한 거부 등)는 무시 — 채팅 배너가 폴백.
                    {
                        use tauri_plugin_notification::NotificationExt;
                        if let Err(e) = app
                            .notification()
                            .builder()
                            .title("VEGA 업데이트 준비 완료")
                            .body(format!("v{version} 설치됨 — 앱을 재시작하면 적용됩니다."))
                            .show()
                        {
                            vlog!("[VEGA] 업데이트 OS 알림 실패(무시): {e}");
                        }
                    }
                    // 프론트(웹뷰)가 아직 로드 전이거나 리스너가 늦게 붙을 수 있으니
                    // 별도 OS 스레드에서 간격을 두고 3회 emit(멱등 — 프론트가 중복 무시).
                    let app2 = app.clone();
                    std::thread::spawn(move || {
                        for _ in 0..3 {
                            let _ = app2.emit("update-ready", version.clone());
                            std::thread::sleep(std::time::Duration::from_secs(3));
                        }
                    });
                }
                Err(e) => {
                    // 조용한 실패 금지 — 다운로드/설치 실패를 사용자에게 알린다(2026-06-19).
                    // 이게 없으면 "재시작해도 업데이트가 안 됨"의 원인을 사용자가 알 수 없다.
                    vlog!("[VEGA] 업데이트 설치 실패: {e}");
                    {
                        use tauri_plugin_notification::NotificationExt;
                        let _ = app
                            .notification()
                            .builder()
                            .title("VEGA 업데이트 실패")
                            .body("새 버전 설치에 실패했습니다 — 잠시 후 자동 재시도하며, 계속 실패하면 최신 버전 수동 설치가 필요할 수 있습니다.")
                            .show();
                    }
                    let msg = format!("{e}");
                    let app2 = app.clone();
                    std::thread::spawn(move || {
                        for _ in 0..3 {
                            let _ = app2.emit("update-error", msg.clone());
                            std::thread::sleep(std::time::Duration::from_secs(3));
                        }
                    });
                }
            }
        }
        Ok(None) => vlog!("[VEGA] 최신 버전 — 업데이트 없음"),
        Err(e) => vlog!("[VEGA] 업데이트 체크 실패(무시): {e}"),
    }
}

#[cfg(desktop)]
fn show_main_window(app: &tauri::AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.unminimize(); // cxt-ignore: error_swallow
        let _ = win.show(); // cxt-ignore: error_swallow
        let _ = win.set_focus(); // cxt-ignore: error_swallow
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
        let _ = win.show(); // cxt-ignore: error_swallow
        let _ = win.set_focus(); // cxt-ignore: error_swallow
        // 이미 열려있으면 fragment를 갱신해 해당 탭으로 전환(settings.html의 hashchange 처리).
        if !section.is_empty() {
            let _ = win.eval(&format!("window.location.hash = {section:?}; window.dispatchEvent(new HashChangeEvent('hashchange'))")); // cxt-ignore: security
        }
        return;
    }

    let base = if cfg!(feature = "client") { "client-settings.html" } else { "settings.html" };
    let settings_html = if section.is_empty() { base.to_string() } else { format!("{base}#{section}") };
    let title = strings().settings_title;
    let builder = WebviewWindowBuilder::new(app, "settings", WebviewUrl::App(settings_html.into()))
        .title(title)
        .inner_size(880.0, 640.0)
        .min_inner_size(640.0, 480.0)
        .resizable(true)
        .center();
    // 오버레이 타이틀바는 macOS 전용 API — Windows/Linux 는 기본 타이틀바.
    #[cfg(target_os = "macos")]
    let builder = builder
        .title_bar_style(tauri::TitleBarStyle::Overlay)
        .hidden_title(true);
    let _ = builder.build();
}


// ── LaunchAgent 관리 (daemon + macOS 전용) ────────────────────────────────────
// launchd 는 macOS 에만 있다. Windows/Linux daemon 빌드는 LaunchAgent 등록 없이
// spawn_backend_directly 로 백엔드를 셸이 직접 띄운다(트레이 quit 후에도 child 는 생존).

#[cfg(all(feature = "daemon", not(mobile), target_os = "macos"))]
fn launchagent_plist_path() -> std::path::PathBuf {
    dirs_next::home_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join("Library/LaunchAgents/com.unohee.vega-backend.plist")
}

#[cfg(all(feature = "daemon", not(mobile), target_os = "macos"))]
fn resources_dir(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    app.path().resource_dir().ok()
}

#[cfg(all(feature = "daemon", not(mobile), target_os = "macos"))]
fn plist_text(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

/// 번들된 sidecar 백엔드 실행 파일 경로.
/// macOS: VEGA.app/Contents/MacOS/vega-backend (externalBin 이 메인 바이너리 옆에 실림)
/// Windows/Linux: 메인 실행 파일과 같은 디렉터리의 vega-backend(.exe)
#[cfg(all(feature = "daemon", not(mobile)))]
fn bundled_backend_path(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    #[cfg(target_os = "macos")]
    {
        app.path()
            .resource_dir()
            .ok()
            .and_then(|p| p.parent().map(|contents| contents.join("MacOS/vega-backend")))
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = app;
        let name = if cfg!(windows) { "vega-backend.exe" } else { "vega-backend" };
        std::env::current_exe()
            .ok()
            .and_then(|exe| exe.parent().map(|dir| dir.join(name)))
    }
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

    let mut cmd = std::process::Command::new(&backend);
    cmd.stdout(stdout).stderr(stderr);

    // Windows: console=True 인 PyInstaller 바이너리를 그대로 spawn 하면 콘솔 창이
    // 뜬다. CREATE_NO_WINDOW(0x08000000) 로 창 없이 백그라운드 실행.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000);
    }

    match cmd.spawn() {
        Ok(child) => vlog!("[VEGA] 백엔드 직접 실행: pid={}", child.id()),
        Err(e) => vlog!("[VEGA] 백엔드 직접 실행 실패: {e}"),
    }
}

/// Resources의 LaunchAgent plist를 매 실행마다 갱신하고 재등록한다.
/// 기존 백엔드가 떠 있으면 새 앱 설치 후에도 오래된 프로세스가 8100을 계속 잡을 수 있으므로
/// bootout/bootstrap/kickstart로 현재 /Applications/VEGA.app의 백엔드를 강제로 반영한다.
#[cfg(all(feature = "daemon", not(mobile), target_os = "macos"))]
fn ensure_launchagent(app: &tauri::AppHandle) -> bool {
    // launchd 는 StandardOutPath/StandardErrorPath 의 상위 디렉터리를 자동 생성하지 않는다.
    // plist 등록 전에 로그 디렉터리를 만들어두지 않으면 백엔드 출력이 어디에도 안 남는다.
    let _ = log_dir(); // cxt-ignore: error_swallow

    let plist_dst = launchagent_plist_path();
    let Some(backend_path) = bundled_backend_path(app) else {
        vlog!("[VEGA] LaunchAgent 등록 실패: 백엔드 실행 파일 경로 확인 실패");
        return false;
    };
    if !backend_path.exists() {
        vlog!("[VEGA] LaunchAgent 등록 실패: 백엔드 실행 파일 없음: {}", backend_path.display());
        return false;
    }
    let backend_arg = backend_path.to_string_lossy().into_owned();
    let backend_plist_arg = plist_text(&backend_arg);

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
                    let home_plist = plist_text(&home);
                    let replaced = content
                        .replace("__HOME__", &home_plist)
                        .replace("__BACKEND__", &backend_plist_arg)
                        .replace("/Applications/VEGA.app/Contents/MacOS/vega-backend", &backend_plist_arg);
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
#[cfg(all(feature = "daemon", not(mobile), target_os = "macos"))]
fn restart_backend(_app: &tauri::AppHandle) {
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

/// 트레이 "백엔드 재시작" — 비-macOS: launchd 가 없으므로 프로세스를 직접 내리고 재spawn.
#[cfg(all(feature = "daemon", not(mobile), not(target_os = "macos")))]
fn restart_backend(app: &tauri::AppHandle) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let _ = std::process::Command::new("taskkill")
            .args(["/IM", "vega-backend.exe", "/F"])
            .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
            .status();
    }
    #[cfg(not(windows))]
    {
        let _ = std::process::Command::new("pkill")
            .args(["-f", "vega-backend"])
            .status();
    }
    std::thread::sleep(std::time::Duration::from_millis(700));
    spawn_backend_directly(app);
    vlog!("[VEGA] 백엔드 재시작 (direct respawn)");
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
        .plugin(tauri_plugin_dialog::init())
        // 클립보드 — chat.html(원격 origin)에서 navigator.clipboard 차단 시 폴백 경로 (INT-1472)
        .plugin(tauri_plugin_clipboard_manager::init());

    // 자동 업데이트 플러그인 — 데스크탑 전용 (모바일은 스토어 정책상 자체 업데이트 불가).
    #[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
    {
        builder = builder.plugin(tauri_plugin_updater::Builder::new().build());
        // OS 알림 — 업데이트 설치 완료 시 "재시작하면 적용" 안내 (INT-1467)
        builder = builder.plugin(tauri_plugin_notification::init());
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

            // 업데이트 적용 재시작 — 배너의 "지금 재시작"이 emit 하는 이벤트 (INT-1562).
            // remote 페이지라 invoke 불가 → listen 으로 받는다. daemon 이면 백엔드 데몬도
            // 새 바이너리로 재기동한 뒤 GUI 셸을 재시작해 업데이트를 적용한다.
            #[cfg(desktop)]
            {
                use tauri::Listener;
                let handle = app.handle().clone();
                app.listen_any("request-restart", move |_event| {
                    vlog!("[VEGA] 업데이트 적용 재시작 요청");
                    #[cfg(all(feature = "daemon", not(mobile)))]
                    restart_backend(&handle);
                    // restart()는 비메인 스레드(listen_any 워커)에서 호출하면 exit 처리를
                    // 기다리며 그 스레드가 무한 park될 수 있다 — 메인 스레드에서 실행한다 (code-review).
                    let h = handle.clone();
                    let _ = handle.run_on_main_thread(move || { h.restart(); });
                });
            }

            // remote 페이지(설치 마법사·chat·설정)에서 외부 브라우저를 여는 경로.
            // open-settings와 같은 이유 — remote 오리진은 invoke(open_url)가 ACL에 막힌다.
            {
                use tauri::Listener;
                app.listen_any("open-url", move |event| {
                    let raw = event.payload();
                    let url = serde_json::from_str::<String>(raw)
                        .unwrap_or_else(|_| raw.trim_matches('"').to_string());
                    if let Err(e) = open_url(url) {
                        vlog!("[VEGA] open-url 이벤트 처리 실패: {e}");
                    }
                });
            }

            // 데스크탑: 크기 지정 + macOS 오버레이 타이틀바.
            // 모바일(iOS/Android): 전체화면 단일 윈도우 — TitleBarStyle/inner_size 등은
            // macOS 전용이거나 무의미하므로 적용하지 않는다.
            let win_builder = WebviewWindowBuilder::new(app, "main", make_loading_page())
                .title("VEGA")
                // 외부 http(s) 링크는 WebView 안에서 열지 않고 OS 기본 브라우저로 보낸다.
                // (앱 안에서 브라우저가 열리던 버그 수정 — 2026-06-19). 내부(로컬 백엔드 daemon,
                // client 원격 서버, 앱 자산/로딩 페이지)는 정상 네비게이션 허용.
                .on_navigation(|url| {
                    let s = url.as_str();
                    if s.starts_with("tauri://") || s.starts_with("about:") || s.starts_with("data:") {
                        return true;
                    }
                    if s.starts_with("http://localhost") || s.starts_with("http://127.0.0.1")
                        || s.starts_with("https://localhost") || s.starts_with("https://127.0.0.1")
                    {
                        return true;
                    }
                    let base = backend_base();
                    if !base.is_empty() && s.starts_with(&base) {
                        return true;
                    }
                    if s.starts_with("http://") || s.starts_with("https://") {
                        let _ = open_url(s.to_string());
                        return false; // WebView 네비게이션 차단
                    }
                    true
                });

            #[cfg(desktop)]
            let win_builder = win_builder
                .inner_size(980.0, 760.0)
                .min_inner_size(420.0, 480.0)
                .resizable(true)
                .center();

            // 오버레이 타이틀바는 macOS 전용 API — Windows/Linux 는 기본 타이틀바.
            #[cfg(target_os = "macos")]
            let win_builder = win_builder
                .title_bar_style(tauri::TitleBarStyle::Overlay)
                .hidden_title(true);

            let win = win_builder.build()?;

            // 로딩 콘솔 tail 기준점 — 이 시점 이후의 셸 로그(업데이트 체크·
            // LaunchAgent 등록·spawn)만 로딩 화면에 보여준다 (INT-1465).
            let shell_log_from = std::fs::metadata(shell_log_path())
                .map(|m| m.len())
                .unwrap_or(0);

            // 자동 업데이트 백그라운드 체크 (데스크탑 전용)
            #[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
            {
                spawn_update_check(app.handle().clone());
            }

            // 백엔드 기동 (daemon 모드)
            // macOS: LaunchAgent 등록(실패 시 직접 실행 fallback)
            // Windows/Linux: launchd 가 없으므로 항상 직접 spawn (이미 떠 있으면 skip)
            #[cfg(all(feature = "daemon", not(mobile), target_os = "macos"))]
            if !ensure_launchagent(&app.handle()) {
                spawn_backend_directly(&app.handle());
            }
            #[cfg(all(feature = "daemon", not(mobile), not(target_os = "macos")))]
            spawn_backend_directly(&app.handle());

            // 백엔드 준비 후 실제 URL로 전환 (흰 화면 방지)
            wait_and_navigate(win, backend_url(), shell_log_from);

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

            let tray_icon = app.default_window_icon()
                .ok_or("tray icon not configured in tauri.conf.json")?
                .clone();
            TrayIconBuilder::with_id("vega-tray")
                .icon(tray_icon)
                .tooltip(s.tooltip)
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show" => show_main_window(app),
                    "hide" => {
                        if let Some(win) = app.get_webview_window("main") {
                            let _ = win.hide(); // cxt-ignore: error_swallow
                        }
                    }
                    "settings" => open_settings_window(app),
                    "restart-backend" => {
                        #[cfg(all(feature = "daemon", not(mobile)))]
                        restart_backend(app);
                    }
                    "quit" => {
                        // GUI 셸만 종료 — LaunchAgent 데몬은 계속 실행
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            // macOS 앱 메뉴에 "설정… (⌘,)" — 기본 메뉴(Edit 복사/붙여넣기 단축키 포함)를
            // 유지한 채 앱 서브메뉴의 About 다음 위치에 삽입한다 (표준 macOS 배치).
            #[cfg(target_os = "macos")]
            {
                use tauri::menu::{Menu, MenuItemKind};
                let menu = Menu::default(app.handle())?;
                let settings_menu_item = MenuItemBuilder::with_id("menu-settings", s.settings)
                    .accelerator("Cmd+,")
                    .build(app)?;
                if let Some(MenuItemKind::Submenu(app_submenu)) =
                    menu.items()?.into_iter().next()
                {
                    app_submenu.insert(&settings_menu_item, 1)?;
                }
                app.set_menu(menu)?;
                app.on_menu_event(|app, event| {
                    if event.id().as_ref() == "menu-settings" {
                        open_settings_window(app);
                    }
                });
            }
            } // end #[cfg(desktop)] 트레이 블록

            Ok(())
        })
        .on_window_event(|window, event| {
            // 창 닫기 시 숨김 처리는 데스크탑 전용 (트레이로 다시 열 수 있으므로).
            // 모바일엔 트레이가 없어 닫기를 가로채면 앱을 다시 띄울 수 없다.
            #[cfg(desktop)]
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    let _ = window.hide(); // cxt-ignore: error_swallow
                    api.prevent_close();
                }
            }
            // 창이 포커스를 받을 때마다 업데이트 확인 — 트레이 상주로 프로세스 재시작이
            // 드문 점을 보완한다(창을 볼 때마다 최신 확인 = "항상 업데이트 알림").
            // run_update_check 내부 10분 디바운스로 포커스 연타 시 과다 체크를 막는다 (2026-06-19).
            #[cfg(all(desktop, not(any(target_os = "android", target_os = "ios"))))]
            if let WindowEvent::Focused(true) = event {
                if window.label() == "main" {
                    use tauri::Manager;
                    let app = window.app_handle().clone();
                    std::thread::spawn(move || {
                        tauri::async_runtime::block_on(run_update_check(&app));
                    });
                }
            }
            #[cfg(not(desktop))]
            let _ = (window, event);
        })
        .run(tauri::generate_context!())
        .expect("VEGA desktop error"); // cxt-ignore: panic_risk
}

#[cfg(test)]
mod log_tail_tests {
    use super::LogTail;
    use std::io::Write;

    fn tmp(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("vega-tail-test-{}-{name}.log", std::process::id()))
    }

    #[test]
    fn skips_existing_reads_appended_and_handles_partial_lines() {
        let p = tmp("basic");
        std::fs::write(&p, "old-line\n").unwrap();
        let mut t = LogTail::new(p.clone(), None); // EOF 부터 — 기존 라인 제외
        assert!(t.read_new_lines().is_empty());

        let mut f = std::fs::OpenOptions::new().append(true).open(&p).unwrap();
        write!(f, "a\nb\n").unwrap();
        assert_eq!(t.read_new_lines(), vec!["a", "b"]);

        // 개행 없는 부분 라인은 보류했다가 완성되면 반환
        write!(f, "partial").unwrap();
        assert!(t.read_new_lines().is_empty());
        writeln!(f).unwrap();
        assert_eq!(t.read_new_lines(), vec!["partial"]);
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn resets_on_truncate_and_starts_from_offset() {
        let p = tmp("rotate");
        std::fs::write(&p, "first\nsecond\n").unwrap();
        let mut t = LogTail::new(p.clone(), Some(6)); // "first\n" 이후부터
        assert_eq!(t.read_new_lines(), vec!["second"]);

        std::fs::write(&p, "rotated\n").unwrap(); // truncate + 새 내용
        assert_eq!(t.read_new_lines(), vec!["rotated"]);
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn missing_file_is_silent() {
        let mut t = LogTail::new(tmp("nope-not-created"), None);
        assert!(t.read_new_lines().is_empty());
    }
}
