# mmWave left/right side-lying recording with spoken cues (based on tools/Record-CamMmw.ps1).
# Sequence: 바로 누움, then for each cycle 왼쪽/오른쪽 (random order) with 바로 누움 between,
# so every side posture starts with a turn from supine (lets us test turning direction too).
# Each cue time is saved to marks.json (a list, because postures repeat).
param(
    [int]$LeadSeconds = 20,
    [int]$SideSeconds = 25,
    [int]$SupineSeconds = 15,
    [int]$Cycles = 4,
    [int]$TailSeconds = 5,
    [string]$Participant = '',
    [string]$ViewerDir = (Join-Path $env:USERPROFILE 'Downloads\mmw_viewer'),
    [string]$Viewer = 'http://localhost:8765'
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $voice.SelectVoice('Microsoft Heami Desktop') } catch {}
$text = @{
    '누움'          = '바로 누우세요'
    '왼쪽 옆누움'   = '왼쪽 어깨가 침대에 닿게 옆으로 누우세요'
    '오른쪽 옆누움' = '오른쪽 어깨가 침대에 닿게 옆으로 누우세요'
}
$seq = @('누움')
for ($c = 0; $c -lt $Cycles; $c++) {
    foreach ($side in (@('왼쪽 옆누움', '오른쪽 옆누움') | Get-Random -Count 2)) { $seq += $side; $seq += '누움' }
}
function Say($t) { $voice.Speak($t) }
function Stamp { (Get-Date).ToString('HH:mm:ss.fff') }
function Post($path, $body) {
    Invoke-RestMethod -Method Post -ContentType 'application/json' -Uri "$Viewer$path" `
        -Body ([Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Compress)))
}
$marks = @()
$started = Post '/api/record/start' @{ session = 'TEST'; activity = "left/right side-lying: $($seq -join ', ')"; participant = $Participant; note = 'Record-LeftRight.ps1' }
$mmw = $started.name
Write-Host "mmWave recording: $mmw"
Write-Host "order: $($seq -join ' -> ')"
try {
    Say "엠엠웨이브 녹화를 시작했습니다. 침대에 바로 누워서 기다리세요."
    Start-Sleep -Seconds $LeadSeconds
    $i = 0
    foreach ($p in $seq) {
        $i++
        $t = Stamp
        $marks += [ordered]@{ label = $p; time = $t }
        Write-Host ("{0}  ({1}/{2}) {3}" -f $t, $i, $seq.Count, $p)
        Say $text[$p]
        Start-Sleep -Seconds $(if ($p -eq '누움') { $SupineSeconds } else { $SideSeconds })
    }
    $marks += [ordered]@{ label = '끝'; time = (Stamp) }
    Say "끝났습니다. 편하게 계세요."
    Start-Sleep -Seconds $TailSeconds
}
finally {
    $stopped = Post '/api/record/stop' @{}
    Write-Host "mmWave stopped: $($stopped.name)"
    $result = [ordered]@{ mmwave_session = $mmw; kind = 'left_right'; participant = $Participant
                          side_seconds = $SideSeconds; supine_seconds = $SupineSeconds; order = [object[]]$seq; marks = [object[]]$marks }
    $file = Join-Path $ViewerDir "sessions\$mmw\marks.json"
    [IO.File]::WriteAllText($file, ($result | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding $false))
    Write-Host "marks saved: $file"
    Say "엠엠웨이브 녹화도 멈췄습니다."
}
