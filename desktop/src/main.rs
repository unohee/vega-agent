// VEGA 데스크탑 진입점 — 윈도우 콘솔 숨김 + lib의 run() 호출
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    vega_desktop_lib::run()
}
