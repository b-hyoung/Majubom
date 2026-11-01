$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$repositoryRoot = Split-Path $projectRoot -Parent
$environmentRoot = Join-Path $projectRoot '.venv-training'
if (Test-Path -LiteralPath (Join-Path $repositoryRoot 'server/tof_server.py')) {
    $environmentRoot = Join-Path $repositoryRoot '.venv-training'
}
if (-not (Test-Path -LiteralPath (Join-Path $environmentRoot 'Scripts/python.exe'))) {
    python -m venv $environmentRoot
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
$trainingPython = Join-Path $environmentRoot 'Scripts/python.exe'
& $trainingPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed' }
& $trainingPython -m pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw 'CUDA PyTorch installation failed' }
& $trainingPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt') -e $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'Training dependencies installation failed' }
Push-Location $projectRoot
try { & $trainingPython -m bed_pose.tof_learning doctor; if ($LASTEXITCODE -ne 0) { throw 'GPU smoke check failed' } }
finally { Pop-Location }
