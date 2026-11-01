param(
    [Parameter(Mandatory=$true)][string]$Pi,
    [Parameter(Mandatory=$true)][string]$Subject,
    [string]$Session = ('session-' + (Get-Date -Format 'yyyyMMdd-HHmmss')),
    [ValidateSet('verified','pseudo')][string]$LabelPolicy = 'verified',
    [string]$CameraUrl,
    [int]$Port = 5012
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$trainingPython = Join-Path (Split-Path $projectRoot -Parent) '.venv-training/Scripts/python.exe'
if (-not $CameraUrl) {
    $connectionPath = Join-Path $projectRoot 'outputs/live-connection.json'
    if (-not (Test-Path -LiteralPath $connectionPath)) { throw 'Open camera dashboard then provide -CameraUrl with the full localhost URL' }
    $CameraUrl = (Get-Content -LiteralPath $connectionPath -Raw | ConvertFrom-Json).url
}
$streamUrl = "http://${Pi}:${Port}/api/stream?sensors=tof1,tof2"
& $trainingPython -m bed_pose.tof_learning collect --stream-url $streamUrl --camera-url $CameraUrl --subject $Subject --session $Session --label-policy $LabelPolicy --out (Join-Path $projectRoot 'data/paired')
if ($LASTEXITCODE -ne 0) { throw 'Collection failed; inspect error above' }
