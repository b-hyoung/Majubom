$ErrorActionPreference='Stop'
$projectDirectory=$PSScriptRoot
$runtimePath=Join-Path (Split-Path -Parent $projectDirectory) '.venv-training\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePath)) { throw 'GPU 환경이 없습니다. training\Setup-Training.ps1을 먼저 실행하세요.' }
Push-Location -LiteralPath $projectDirectory
try { & $runtimePath -u tools/start_gpu_monitor.py } finally { Pop-Location }
