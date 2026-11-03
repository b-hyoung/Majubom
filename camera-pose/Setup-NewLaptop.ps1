param([switch]$RestoreSameInstallation)
$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
$repositoryDirectory = Split-Path $projectDirectory -Parent
function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Executable" }
}
Push-Location -LiteralPath $projectDirectory
try {
    & (Join-Path $projectDirectory 'training/Setup-Training.ps1')
    $runtime = Join-Path $repositoryDirectory '.venv-training/Scripts/python.exe'
    Invoke-Checked $runtime @('-m', 'pip', 'install', '-e', '.[live,stereo,clip]')
    Invoke-Checked $runtime @('tools/restore_laptop.py', '--models')
    $poseDirectory = Join-Path $repositoryDirectory '.venv-pose'
    $poseRuntime = Join-Path $poseDirectory 'Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $poseRuntime)) {
        Invoke-Checked $runtime @('-m', 'venv', $poseDirectory)
    }
    Invoke-Checked $poseRuntime @('-m', 'pip', 'install', 'rtmlib==0.0.16', 'onnxruntime-gpu==1.23.2', 'opencv-contrib-python>=4.10,<5')
    if (-not (Test-Path -LiteralPath 'checkpoints/pose-candidates')) {
        Invoke-Checked $runtime @('tools/pose_candidates.py', 'prepare')
    }
    if (-not (Test-Path -LiteralPath 'checkpoints/person-guard.json')) {
        Invoke-Checked $runtime @('tools/pose_candidates.py', 'guard', '--python', $poseRuntime, '--min-area-fraction', '0.08')
    }
    Invoke-Checked $runtime @('tools/restore_laptop.py', '--rebind-guard')
    if ($RestoreSameInstallation) {
        Invoke-Checked $runtime @('tools/restore_laptop.py', '--settings')
    }
    Write-Host 'Setup complete. Run ./Start-CameraGPU.ps1. Existing calibration remains unverified.'
} finally { Pop-Location }
