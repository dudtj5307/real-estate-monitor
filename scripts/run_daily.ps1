# Windows 작업 스케줄러용 실행 스크립트.
# GitHub Actions가 차단(429)될 경우의 대안. 등록 방법은 README 참고.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 토큰은 사용자 환경변수(TELEGRAM_BOT_TOKEN)에 등록해 두거나 아래 주석을 푼다
# $env:TELEGRAM_BOT_TOKEN = "..."

$env:PYTHONIOENCODING = "utf-8"

New-Item -ItemType Directory -Force -Path "$root\logs" | Out-Null
$log = "$root\logs\$(Get-Date -Format 'yyyy-MM-dd').log"

python -m src.main *>> $log

if ($LASTEXITCODE -ne 0) {
    Write-Error "리포트 실행 실패 (exit $LASTEXITCODE). 로그: $log"
    exit $LASTEXITCODE
}
