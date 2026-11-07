param(
    [Parameter(Mandatory=$true)][string[]]$Inputs,
    [Parameter(Mandatory=$true)][string]$Reviews,
    [Parameter(Mandatory=$true)][string]$Split,
    [string]$Out = ('runs/tof-' + (Get-Date -Format 'yyyyMMdd-HHmmss')),
    [int]$Epochs = 60
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$environmentRoot = Join-Path $projectRoot '.venv-training'
$repositoryRoot = Split-Path $projectRoot -Parent
if (Test-Path -LiteralPath (Join-Path $repositoryRoot 'server/tof_server.py')) { $environmentRoot = Join-Path $repositoryRoot '.venv-training' }
$trainingPython = Join-Path $environmentRoot 'Scripts/python.exe'
if (-not (Test-Path -LiteralPath $trainingPython)) { throw 'Run training/Setup-Training.ps1 first' }
& $trainingPython -m bed_pose.tof_learning train @Inputs --reviews $Reviews --split $Split --out $Out --epochs $Epochs
if ($LASTEXITCODE -ne 0) { throw 'Training failed; inspect error above' }
