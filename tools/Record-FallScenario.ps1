# mmWave recording: pre-fall scenarios for the mock-data fall prediction (2026-11-16).
# Each fall trial = normal walking -> the scenario's warning behaviour -> slowly sitting down onto the mat (NOT a real fall).
# Control trials use the same room and the same kinds of motion without the warning pattern.
# Trials are interleaved so the order does not give the answer away. Cue times go to marks.json with trial number and kind.
# Kinds match analysis/20261116_prefall_mock (unsteady_fall, freeze_collapse, dizzy_collapse, walk_normal, stop_stand, sit_chair, crouch).
# SAFETY: put a thick mat down, keep a chair nearby, and never let the participant actually fall. Slow sitting is enough:
# the analysis looks at the walking BEFORE the drop.
# Plan (2026-11-16, lee then kim): 3 rounds per person. Each round = every fall scenario twice + each control once
# (10 trials, about 7 minutes), with a different order per round. 3 rounds -> 6 trials per fall scenario per person,
# 12 per scenario for lee + kim. Rest between rounds. Extra real walking for the "no fall" side is recorded separately:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\Record-CamMmw.ps1 -Gait -Participant lee -HoldSeconds 120
# Run:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\Record-FallScenario.ps1 -Participant lee -Round 1
param(
    [Parameter(Mandatory = $true)][string]$Participant,
    [ValidateRange(1, 5)][int]$Round = 1,
    [int]$LeadSeconds = 15,
    [int]$TailSeconds = 5,
    [string]$ViewerDir = (Join-Path $env:USERPROFILE 'Downloads\mmw_viewer'),
    [string]$Viewer = 'http://localhost:8765'
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $voice.SelectVoice('Microsoft Heami Desktop') } catch {}

# step = @(cue text, seconds)
$step = @{
    '걷기'       = @('방 안을 평소처럼 걸으세요', 15)      # first 10 s = this person's own walking baseline
    '비틀'       = @('좌우로 비틀거리며 걸으세요', 6)
    '종종'       = @('보폭을 아주 짧게, 종종걸음으로 걸으세요', 4)
    '멈칫'       = @('그 자리에 멈추세요', 2)
    '서기'       = @('그 자리에 멈춰 서세요', 4)
    '어지럼'     = @('서 있는 채로 어지러운 것처럼 몸을 천천히 흔드세요', 6)
    '매트앉기'   = @('매트 위로 천천히 주저앉아 누우세요', 8)
    '일어나기'   = @('천천히 일어나세요', 8)
    '멈춰서기'   = @('멈춰 서 계세요', 6)
    '의자앉기'   = @('의자에 앉으세요', 8)
    '의자일어남' = @('의자에서 일어나세요', 5)
    '쪼그리기'   = @('그 자리에 쪼그려 앉으세요', 4)
    '일어서기'   = @('일어서세요', 4)
}
$trialSteps = @{
    'unsteady_fall'   = @('걷기', '비틀', '매트앉기', '일어나기')
    'freeze_collapse' = @('걷기', '종종', '멈칫', '매트앉기', '일어나기')
    'dizzy_collapse'  = @('걷기', '서기', '어지럼', '매트앉기', '일어나기')
    'walk_normal'     = @('걷기', '걷기')
    'stop_stand'      = @('걷기', '멈춰서기', '걷기')
    'sit_chair'       = @('걷기', '의자앉기', '의자일어남')
    'crouch'          = @('걷기', '쪼그리기', '일어서기', '걷기')
}
# every round: each fall scenario twice, each control once, in a different order (rounds 4-5 reuse 1-2 reversed)
$orders = @{
    1 = @('unsteady_fall', 'walk_normal', 'freeze_collapse', 'sit_chair', 'dizzy_collapse', 'unsteady_fall', 'crouch', 'freeze_collapse', 'stop_stand', 'dizzy_collapse')
    2 = @('dizzy_collapse', 'stop_stand', 'unsteady_fall', 'freeze_collapse', 'crouch', 'dizzy_collapse', 'walk_normal', 'unsteady_fall', 'sit_chair', 'freeze_collapse')
    3 = @('freeze_collapse', 'crouch', 'dizzy_collapse', 'walk_normal', 'unsteady_fall', 'sit_chair', 'freeze_collapse', 'stop_stand', 'dizzy_collapse', 'unsteady_fall')
}
$orders[4] = [object[]]($orders[1].Clone()); [array]::Reverse($orders[4])
$orders[5] = [object[]]($orders[2].Clone()); [array]::Reverse($orders[5])
$trials = $orders[$Round]

function Say($t) { $voice.Speak($t) }
function Stamp { (Get-Date).ToString('HH:mm:ss.fff') }
function Post($path, $body) {
    Invoke-RestMethod -Method Post -ContentType 'application/json' -Uri "$Viewer$path" `
        -Body ([Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Compress)))
}

$total = $LeadSeconds + $TailSeconds
foreach ($k in $trials) { foreach ($s in $trialSteps[$k]) { $total += $step[$s][1] } }
Write-Host ("participant {0}, round {1}: {2}  (about {3:N1} min)" -f $Participant, $Round, ($trials -join ', '), ($total / 60))

$marks = @()
$started = Post '/api/record/start' @{ session = 'TEST'; activity = "pre-fall scenarios round ${Round}: $($trials -join ', ')"; participant = $Participant; note = "Record-FallScenario.ps1 round $Round" }
$mmw = $started.name
Write-Host "mmWave recording: $mmw"
try {
    Say "엠엠웨이브 녹화를 시작했습니다. 매트 옆에 서서 기다리세요. 실제로 넘어지지 말고, 주저앉을 때는 천천히 앉으세요."
    Start-Sleep -Seconds $LeadSeconds
    $n = 0
    foreach ($kind in $trials) {
        $n++
        foreach ($s in $trialSteps[$kind]) {
            $t = Stamp
            $marks += [ordered]@{ label = $s; time = $t; trial = $n; kind = $kind }
            Write-Host ("{0}  trial {1}/{2} [{3}] {4}" -f $t, $n, $trials.Count, $kind, $s)
            Say $step[$s][0]
            Start-Sleep -Seconds $step[$s][1]
        }
    }
    $marks += [ordered]@{ label = '끝'; time = (Stamp); trial = $null; kind = $null }
    Say "$Round 회차가 끝났습니다. 수고하셨습니다. 다음 회차 전에 충분히 쉬세요."
    Start-Sleep -Seconds $TailSeconds
}
finally {
    $stopped = Post '/api/record/stop' @{}
    Write-Host "mmWave stopped: $($stopped.name)"
    $result = [ordered]@{ mmwave_session = $mmw; kind = 'prefall_scenario'; participant = $Participant; round = $Round
                          trials = [object[]]$trials; marks = [object[]]$marks }
    $file = Join-Path $ViewerDir "sessions\$mmw\marks.json"
    [IO.File]::WriteAllText($file, ($result | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding $false))
    Write-Host "marks saved: $file"
    Say "엠엠웨이브 녹화도 멈췄습니다."
}
