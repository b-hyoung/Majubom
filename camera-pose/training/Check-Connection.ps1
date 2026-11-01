param(
    [string]$Pi = '192.168.0.4',
    [double]$Seconds = 10,
    [ValidateSet('supine','prone','left_side','right_side','sitting','edge_sitting','empty')][string]$TemporaryCameraLabel,
    [string]$CameraUrl,
    [string]$Out = ('outputs/connection-check-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
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
    if (-not (Test-Path -LiteralPath $connectionPath)) { throw 'Provide -CameraUrl with the localhost dashboard URL including #token' }
    $CameraUrl = (Get-Content -LiteralPath $connectionPath -Raw | ConvertFrom-Json).url
}
$checkArguments = @('--pi',$Pi,'--camera-url',$CameraUrl,'--seconds',"$Seconds",'--out',$Out)
if ($TemporaryCameraLabel) { $checkArguments += @('--temporary-camera-label',$TemporaryCameraLabel) }
& $trainingPython -m bed_pose.tof_learning check @checkArguments
if ($LASTEXITCODE -ne 0) { throw 'Connection check failed; inspect error above' }
