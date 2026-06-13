# Created: 2026-06-13
# Purpose: VEGA Agent Windows 온보딩 — 코드 실행 샌드박스(Docker Desktop)에 필요한
#          WSL2 / 가상화 의존성을 점검하고, 누락 시 설치를 안내·자동화 (INT-1505).
# Usage:   관리자 PowerShell 에서:  powershell -ExecutionPolicy Bypass -File scripts\windows_setup.ps1
#          점검만:                   ... -File scripts\windows_setup.ps1 -CheckOnly
# Notes:   채팅·메모리·워크스페이스 연동은 Docker 없이도 동작한다. 이 스크립트는
#          bash_exec/python_exec(코드 실행) 도구를 켜기 위한 선택 단계다.

param(
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

function Write-Step($msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Fail($msg)  { Write-Host "  [X] $msg" -ForegroundColor Red }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

# ── 1. CPU 가상화(펌웨어) ──────────────────────────────────────────────────────
Write-Step "CPU 가상화(VT-x/AMD-V) 점검"
$virt = $null
try { $virt = (Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled } catch {}
if ($virt -eq $true) {
    Write-Ok "가상화 활성화됨"
} elseif ($virt -eq $false) {
    Write-Fail "가상화가 BIOS/UEFI 에서 비활성화됨 — 재부팅 후 펌웨어 설정에서 Intel VT-x / AMD-V(SVM) 를 켜세요."
} else {
    Write-Warn "가상화 상태를 확인하지 못함 (계속 진행)"
}

# ── 2. WSL ─────────────────────────────────────────────────────────────────────
Write-Step "WSL(Windows Subsystem for Linux) 점검"
$wslOk = $false
try {
    wsl --status *> $null
    if ($LASTEXITCODE -eq 0) { $wslOk = $true }
} catch {}

if ($wslOk) {
    Write-Ok "WSL 설치됨"
} else {
    Write-Warn "WSL 미설치/미구성"
    if ($CheckOnly) {
        Write-Host "    설치하려면 관리자 PowerShell 에서:  wsl --install" -ForegroundColor Gray
    } else {
        if (-not (Test-Admin)) {
            Write-Fail "WSL 설치에는 관리자 권한이 필요합니다. PowerShell 을 '관리자 권한으로 실행' 후 다시 실행하세요."
        } else {
            Write-Host "    'wsl --install' 실행 중 (완료 후 재부팅이 필요할 수 있음)..." -ForegroundColor Gray
            wsl --install
            Write-Warn "WSL 설치를 시작했습니다. 재부팅 후 이 스크립트를 다시 실행하세요."
        }
    }
}

# ── 3. Docker Desktop ──────────────────────────────────────────────────────────
Write-Step "Docker Desktop 점검"
$dockerOk = $false
try {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
} catch {}

if ($dockerOk) {
    Write-Ok "Docker 데몬 응답 — 코드 실행 샌드박스 사용 가능"
} else {
    $dockerExe = Get-Command docker -ErrorAction SilentlyContinue
    if ($dockerExe) {
        Write-Warn "Docker 는 설치됐으나 데몬이 미기동 — Docker Desktop 을 실행하고 시작될 때까지 기다리세요."
    } else {
        Write-Warn "Docker Desktop 미설치"
        if (-not $CheckOnly) {
            $winget = Get-Command winget -ErrorAction SilentlyContinue
            if ($winget) {
                Write-Host "    winget 으로 설치 중: Docker.DockerDesktop ..." -ForegroundColor Gray
                winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
                Write-Warn "설치 후 Docker Desktop 을 한 번 실행해 초기 설정(WSL2 백엔드)을 마치세요."
            } else {
                Write-Host "    winget 이 없습니다. 수동 설치: https://www.docker.com/products/docker-desktop/" -ForegroundColor Gray
            }
        } else {
            Write-Host "    설치: https://www.docker.com/products/docker-desktop/" -ForegroundColor Gray
        }
    }
}

# ── 요약 ───────────────────────────────────────────────────────────────────────
Write-Step "요약"
if ($dockerOk) {
    Write-Ok "코드 실행 샌드박스 준비 완료 — VEGA 의 bash/python 도구를 쓸 수 있습니다."
} else {
    Write-Warn "코드 실행 도구는 아직 비활성 상태입니다. 위 단계를 마친 뒤 VEGA 를 재시작하세요."
    Write-Host "    (채팅·메모리·파일·워크스페이스 연동은 지금도 정상 작동합니다.)" -ForegroundColor Gray
}
