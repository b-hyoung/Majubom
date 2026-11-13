# mmWave recording: lying on the right side, right arm put on the bed rail (radar-side edge) and taken back, repeated.
# Cue times go to marks.json as a list (labels repeat).
param(
    [int]$LeadSeconds = 20,
    [int]$SetupSeconds = 20,
    [int]$ArmSeconds = 15,
    [int]$Cycles = 5,
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
    '누움'            = '바로 누우세요'
    '오른쪽 옆누움'   = '오른쪽 어깨가 침대에 닿게 옆으로 누우세요. 팔은 침대 안에 두세요'
    '팔 안전바'       = '오른팔을 안전바 위에 올리세요'
    '팔 내림'         = '오른팔을 침대 안으로 내리세요'
}
$seq = @('누움', '오른쪽 옆누움')
for ($c = 0; $c -lt $Cycles; $c++) { $seq += '팔 안전바'; $seq += '팔 내림' }
$hold = @{ '누움' = $SetupSeconds; '오른쪽 옆누움' = $SetupSeconds; '팔 안전바' = $ArmSeconds; '팔 내림' = $ArmSeconds }
function Say($t) { $voice.Speak($t) }
function Stamp { (Get-Date).ToString('HH:mm:ss.fff') }
function Post($path, $body) {
    Invoke-RestMethod -Method Post -ContentType 'application/json' -Uri "$Viewer$path" `
        -Body ([Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Compress)))
}
$marks = @()
$started = Post '/api/record/start' @{ session = 'TEST'; activity = "arm on rail (right side): $($seq -join ', ')"; participant = $Participant; note = 'Record-ArmRail.ps1' }
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
        Start-Sleep -Seconds $hold[$p]
    }
    $marks += [ordered]@{ label = '끝'; time = (Stamp) }
    Say "끝났습니다. 편하게 계세요."
    Start-Sleep -Seconds $TailSeconds
}
finally {
    $stopped = Post '/api/record/stop' @{}
    Write-Host "mmWave stopped: $($stopped.name)"
    $result = [ordered]@{ mmwave_session = $mmw; kind = 'arm_rail'; participant = $Participant; rail_side = 'radar-side bed edge'
                          order = [object[]]$seq; marks = [object[]]$marks }
    $file = Join-Path $ViewerDir "sessions\$mmw\marks.json"
    [IO.File]::WriteAllText($file, ($result | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding $false))
    Write-Host "marks saved: $file"
    Say "엠엠웨이브 녹화도 멈췄습니다."
}
