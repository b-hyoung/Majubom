# mmWave recording: bed-exit attempt without legs going down (bed rail is up).
# Exit pattern: lying -> sit up while grabbing the bed rail (radar-side edge) -> turn the body toward the outside -> lie back.
# Control actions (same motions without the exit pattern): sit up only / grab the rail while lying / sit up and turn inward.
# Exit and control trials are interleaved so the order does not give the answer away.
# Cue times go to marks.json as a list (labels repeat). Each trial also gets a trial number and kind.
param(
    [int]$LeadSeconds = 20,
    [int]$MoveSeconds = 8,
    [int]$LieSeconds = 12,
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
    '앉으며 안전바' = '안전바를 잡으면서 일어나 앉으세요'
    '밖으로 돌기'   = '안전바를 잡은 채 침대 밖 쪽으로 몸을 돌리세요'
    '앉기만'        = '안전바를 잡지 말고 일어나 앉으세요'
    '안전바만'      = '누운 채로 안전바를 잡으세요'
    '앉음'          = '일어나 앉으세요'
    '안쪽으로 돌기' = '침대 안쪽으로 몸을 돌리세요'
    '누움'          = '다시 바로 누우세요'
}
# trial kinds: exit = 이탈 패턴, sit_only / rail_only / turn_in = 비교 동작
$trialSteps = @{
    'exit'      = @('앉으며 안전바', '밖으로 돌기', '누움')
    'sit_only'  = @('앉기만', '누움')
    'rail_only' = @('안전바만', '누움')
    'turn_in'   = @('앉음', '안쪽으로 돌기', '누움')
}
$trials = @('exit', 'sit_only', 'exit', 'rail_only', 'exit', 'turn_in', 'exit', 'sit_only', 'exit', 'rail_only', 'turn_in')

function Say($t) { $voice.Speak($t) }
function Stamp { (Get-Date).ToString('HH:mm:ss.fff') }
function Post($path, $body) {
    Invoke-RestMethod -Method Post -ContentType 'application/json' -Uri "$Viewer$path" `
        -Body ([Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Compress)))
}

$total = $LeadSeconds + $TailSeconds
foreach ($k in $trials) { foreach ($s in $trialSteps[$k]) { $total += $(if ($s -eq '누움') { $LieSeconds } else { $MoveSeconds }) } }
Write-Host ("trials: {0}  (about {1:N1} min)" -f ($trials -join ', '), ($total / 60))

$marks = @()
$started = Post '/api/record/start' @{ session = 'TEST'; activity = "exit turn with bed rail: $($trials -join ', ')"; participant = $Participant; note = 'Record-ExitTurn.ps1' }
$mmw = $started.name
Write-Host "mmWave recording: $mmw"
try {
    Say "엠엠웨이브 녹화를 시작했습니다. 침대에 바로 누워서 기다리세요. 안전바는 올려 두세요."
    Start-Sleep -Seconds $LeadSeconds
    $n = 0
    foreach ($kind in $trials) {
        $n++
        foreach ($step in $trialSteps[$kind]) {
            $t = Stamp
            $marks += [ordered]@{ label = $step; time = $t; trial = $n; kind = $kind }
            Write-Host ("{0}  trial {1}/{2} [{3}] {4}" -f $t, $n, $trials.Count, $kind, $step)
            Say $text[$step]
            Start-Sleep -Seconds $(if ($step -eq '누움') { $LieSeconds } else { $MoveSeconds })
        }
    }
    $marks += [ordered]@{ label = '끝'; time = (Stamp); trial = $null; kind = $null }
    Say "끝났습니다. 편하게 계세요."
    Start-Sleep -Seconds $TailSeconds
}
finally {
    $stopped = Post '/api/record/stop' @{}
    Write-Host "mmWave stopped: $($stopped.name)"
    $result = [ordered]@{ mmwave_session = $mmw; kind = 'exit_turn'; participant = $Participant; rail_side = 'radar-side bed edge'
                          move_seconds = $MoveSeconds; lie_seconds = $LieSeconds; trials = [object[]]$trials; marks = [object[]]$marks }
    $file = Join-Path $ViewerDir "sessions\$mmw\marks.json"
    [IO.File]::WriteAllText($file, ($result | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding $false))
    Write-Host "marks saved: $file"
    Say "엠엠웨이브 녹화도 멈췄습니다."
}
