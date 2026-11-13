# Camera + mmWave synchronized recording with spoken posture cues (Windows).
#
# Starts the mmWave test-viewer recording, tells you to start the camera group
# measurement, reads each posture aloud in a shuffled order, and saves each cue
# time (PC clock) to marks.json inside the mmWave session folder. Use those times
# with tools/cam_mmw_join.py --marks or tools/match_cam_mmw.py.
#
# Before running: mmWave viewer on http://localhost:8765, camera workspace
# (Start-PiUsb.ps1) set up up to "group measurement start" (both inputs,
# allowed receive-time difference 250ms, blackbox and video recording off).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\Record-CamMmw.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\Record-CamMmw.ps1 -HoldSeconds 45 -Postures 걸터앉음,누움
param(
    [int]$LeadSeconds = 20,      # time to press "group measurement start" and walk to the bed
    [int]$HoldSeconds = 60,      # how long each posture is held
    [int]$TailSeconds = 20,      # time to press "group measurement stop" before mmWave stops
    [string[]]$Postures = @(),   # subset of the labels below; default: all
    [switch]$KeepOrder,          # do not shuffle
    [switch]$Gait,               # walking cues (mmWave only, no camera) instead of bed postures
    [switch]$NormalOnly,         # with -Gait: only the usual-pace segment (baseline), e.g. -HoldSeconds 180
    [string]$Participant = '',   # who is measured (saved in marks.json)
    [string]$ViewerDir = (Join-Path $env:USERPROFILE 'Downloads\mmw_viewer'),
    [string]$Viewer = 'http://localhost:8765'
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $voice.SelectVoice('Microsoft Heami Desktop') } catch {}

$all = [ordered]@{
    '걸터앉음'     = '침대 가장자리에 걸터앉으세요'
    '침대 위 앉음' = '침대 위로 올라가 앉으세요'
    '누움'         = '침대에 바로 누우세요'
    '옆누움'       = '침대에 옆으로 누우세요'
    '섬'           = '침대 옆에 서세요'
}
if ($Gait) {
    # Same circle path as the 2026-11-05 S3 recordings: keep walking without stopping between cues.
    $all = [ordered]@{
        '평소 걷기'   = '원을 따라 평소 속도로 계속 걸으세요'
        '천천히 걷기' = '원을 따라 천천히 계속 걸으세요'
        '종종걸음'    = '원을 따라 보폭을 줄여 종종걸음으로 걸으세요'
    }
    if ($NormalOnly) { $Postures = @('평소 걷기') }
}
$chosen = if ($Postures.Count) { $Postures } else { @($all.Keys) }
foreach ($p in $chosen) { if (-not $all.Contains($p)) { throw "Unknown posture '$p'. Use: $($all.Keys -join ', ')" } }
# Shuffle so a model cannot learn the posture from its position in the session.
$order = if ($KeepOrder) { $chosen } else { @($chosen | Get-Random -Count $chosen.Count) }

function Say($text) { $voice.Speak($text) }
# Big on-screen cue + countdown, for when the speaker cannot be heard.
function Banner($text, $color = 'Yellow') {
    $line = '=' * 60
    Write-Host ''; Write-Host $line -ForegroundColor $color
    Write-Host "   >>>  $text  <<<" -ForegroundColor $color
    Write-Host $line -ForegroundColor $color
}
function Wait($seconds, $label) {
    for ($left = $seconds; $left -gt 0; $left -= 5) {
        Write-Host ("   [{0}]  남은 시간 {1,3}초" -f $label, $left)
        Start-Sleep -Seconds ([Math]::Min(5, $left))
    }
}
function Stamp { (Get-Date).ToString('HH:mm:ss.fff') }
function Post($path, $body) {
    Invoke-RestMethod -Method Post -ContentType 'application/json' -Uri "$Viewer$path" `
        -Body ([Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Compress)))
}

$marks = [ordered]@{}
$kind = if ($Gait) { 'gait' } else { 'camera+mmWave sync' }
$started = Post '/api/record/start' @{ session = 'TEST'; activity = "${kind}: $($order -join ', ')"; participant = $Participant; note = 'tools/Record-CamMmw.ps1' }
$mmw = $started.name
Write-Host "mmWave recording: $mmw"
Write-Host "order: $($order -join ' -> ')"
try {
    if ($Gait) {
        Banner '원 모양 길의 시작점에 서세요' 'Cyan'
        Say "엠엠웨이브 녹화를 시작했습니다. 원 모양 길의 시작점에 서세요."
    } else {
        Banner '카메라 작업실에서 [그룹 측정 시작]을 누르고 침대로 가세요' 'Cyan'
        Say "엠엠웨이브 녹화를 시작했습니다. 카메라 작업실에서 그룹 측정 시작을 누르고 침대로 가세요."
    }
    Wait $LeadSeconds '준비'
    $i = 0
    foreach ($p in $order) {
        $i++
        $marks[$p] = Stamp
        Write-Host "$($marks[$p])  $p"
        Banner "($i/$($order.Count)) $($all[$p])"
        $next = if ($i -lt $order.Count) { "다음: $($order[$i])" } else { '다음: 끝' }
        Write-Host "   $next"
        Say $all[$p]
        Wait $HoldSeconds $p
    }
    $marks['끝'] = Stamp
    Write-Host "$($marks['끝'])  끝"
    if ($Gait) {
        Banner '끝났습니다. 멈추셔도 됩니다' 'Green'
        Say "끝났습니다. 멈추셔도 됩니다."
    } else {
        Banner '끝났습니다. 카메라 작업실에서 [그룹 측정 종료]를 누르세요' 'Green'
        Say "끝났습니다. 카메라 작업실에서 그룹 측정 종료를 누르세요."
    }
    Wait $TailSeconds '마무리'
}
finally {
    $stopped = Post '/api/record/stop' @{}
    Write-Host "mmWave stopped: $($stopped.name)"
    $result = [ordered]@{ mmwave_session = $mmw; kind = $(if ($Gait) { 'gait' } else { 'posture' }); participant = $Participant
                          hold_seconds = $HoldSeconds; order = [object[]]@($order); marks = $marks }
    $file = Join-Path $ViewerDir "sessions\$mmw\marks.json"
    [IO.File]::WriteAllText($file, ($result | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
    Write-Host "marks saved: $file"
    Write-Host ("--marks " + (($marks.GetEnumerator() | % { '"{0}={1}"' -f $_.Key, $_.Value }) -join ' '))
    Say "엠엠웨이브 녹화도 멈췄습니다."
}
