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
$chosen = if ($Postures.Count) { $Postures } else { @($all.Keys) }
foreach ($p in $chosen) { if (-not $all.Contains($p)) { throw "Unknown posture '$p'. Use: $($all.Keys -join ', ')" } }
# Shuffle so a model cannot learn the posture from its position in the session.
$order = if ($KeepOrder) { $chosen } else { @($chosen | Get-Random -Count $chosen.Count) }

function Say($text) { $voice.Speak($text) }
function Stamp { (Get-Date).ToString('HH:mm:ss.fff') }
function Post($path, $body) {
    Invoke-RestMethod -Method Post -ContentType 'application/json' -Uri "$Viewer$path" `
        -Body ([Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Compress)))
}

$marks = [ordered]@{}
$started = Post '/api/record/start' @{ session = 'TEST'; activity = "camera+mmWave sync: $($order -join ', ')"; note = 'tools/Record-CamMmw.ps1' }
$mmw = $started.name
Write-Host "mmWave recording: $mmw"
Write-Host "order: $($order -join ' -> ')"
try {
    Say "엠엠웨이브 녹화를 시작했습니다. 카메라 작업실에서 그룹 측정 시작을 누르고 침대로 가세요."
    Start-Sleep -Seconds $LeadSeconds
    foreach ($p in $order) {
        $marks[$p] = Stamp
        Write-Host "$($marks[$p])  $p"
        Say $all[$p]
        Start-Sleep -Seconds $HoldSeconds
    }
    $marks['끝'] = Stamp
    Write-Host "$($marks['끝'])  끝"
    Say "끝났습니다. 카메라 작업실에서 그룹 측정 종료를 누르세요."
    Start-Sleep -Seconds $TailSeconds
}
finally {
    $stopped = Post '/api/record/stop' @{}
    Write-Host "mmWave stopped: $($stopped.name)"
    $result = [ordered]@{ mmwave_session = $mmw; hold_seconds = $HoldSeconds; order = $order; marks = $marks }
    $file = Join-Path $ViewerDir "sessions\$mmw\marks.json"
    [IO.File]::WriteAllText($file, ($result | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
    Write-Host "marks saved: $file"
    Write-Host ("--marks " + (($marks.GetEnumerator() | % { '"{0}={1}"' -f $_.Key, $_.Value }) -join ' '))
    Say "엠엠웨이브 녹화도 멈췄습니다."
}
