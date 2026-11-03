param(
    [Parameter(Mandatory=$true)][string]$Pi,
    [Parameter(Mandatory=$true)][string]$Subject,
    [string]$Session = ('session-' + (Get-Date -Format 'yyyyMMdd-HHmmss')),
    [ValidateSet('verified','pseudo')][string]$LabelPolicy = 'verified',
    [string]$CameraUrl,
    [int]$Port = 5012,
    [switch]$CaptureAll,
    [double]$Seconds = 0
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$environmentRoot = Join-Path $projectRoot '.venv-training'
$repositoryRoot = Split-Path $projectRoot -Parent
if (Test-Path -LiteralPath (Join-Path $repositoryRoot 'server/tof_server.py')) { $environmentRoot = Join-Path $repositoryRoot '.venv-training' }
$trainingPython = Join-Path $environmentRoot 'Scripts/python.exe'
if (-not (Test-Path -LiteralPath $trainingPython)) { throw 'Run training/Setup-Training.ps1 first' }
if (-not $CameraUrl) {
    $connectionPath = Join-Path $projectRoot 'outputs/live-connection.json'
    if (-not (Test-Path -LiteralPath $connectionPath)) { throw 'Open camera dashboard then provide -CameraUrl with the full localhost URL' }
    $CameraUrl = (Get-Content -LiteralPath $connectionPath -Raw | ConvertFrom-Json).url
}
$streamUrl = "http://${Pi}:${Port}/api/stream?sensors=tof1,tof2"
$collectionArguments = @('--stream-url',$streamUrl,'--camera-url',$CameraUrl,'--subject',$Subject,'--session',$Session,'--label-policy',$LabelPolicy,'--seconds',"$Seconds",'--out',(Join-Path $projectRoot 'data/paired'))
if ($CaptureAll) { $collectionArguments += '--capture-all' }
& $trainingPython -m bed_pose.tof_learning collect @collectionArguments
if ($LASTEXITCODE -ne 0) { throw 'Collection failed; inspect error above' }
