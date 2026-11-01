param([switch]$PrepareModels, [switch]$EnablePersonGuard, [double]$MinimumPersonArea = 0.08)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$repositoryRoot = Split-Path $projectRoot -Parent
$environmentRoot = Join-Path $projectRoot '.venv-pose'
if (Test-Path -LiteralPath (Join-Path $repositoryRoot 'server/tof_server.py')) { $environmentRoot = Join-Path $repositoryRoot '.venv-pose' }
$posePython = Join-Path $environmentRoot 'Scripts/python.exe'
if (-not (Test-Path -LiteralPath $posePython)) { python -m venv $environmentRoot; if ($LASTEXITCODE -ne 0) { throw 'Pose environment creation failed' } }
& $posePython -m pip install -r (Join-Path $PSScriptRoot 'requirements-pose.txt')
if ($LASTEXITCODE -ne 0) { throw 'Pose comparison dependencies failed' }
# Install GPU last: RTMLIB also declares the CPU distribution with the same Python module.
& $posePython -m pip install --force-reinstall --no-deps onnxruntime-gpu==1.23.2
if ($LASTEXITCODE -ne 0) { throw 'ONNX Runtime GPU installation failed' }
& $posePython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Pose environment dependency check failed' }
if ($PrepareModels) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'checkpoints/pose-candidates/manifest.json'))) {
        & $posePython (Join-Path $projectRoot 'tools/pose_candidates.py') prepare
        if ($LASTEXITCODE -ne 0) { throw 'Explicit model preparation failed' }
    }
}
if ($EnablePersonGuard) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'checkpoints/person-guard.json'))) {
        & $posePython (Join-Path $projectRoot 'tools/pose_candidates.py') guard --python $posePython --min-area-fraction $MinimumPersonArea
        if ($LASTEXITCODE -ne 0) { throw 'Person evidence configuration failed' }
    } else { Write-Output 'Existing person guard configuration preserved.' }
}
