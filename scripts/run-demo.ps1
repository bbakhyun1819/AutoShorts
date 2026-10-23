param(
    [string]$Url,
    [string]$Job = 'runs/demo',
    [int]$Start = 0,
    [int]$Duration = 7200,
    [string]$Format,
    [switch]$AllowNoChat,
    [switch]$WithAI,
    [switch]$WithAssistant,
    [int]$ClipLimit = 10
)
$ErrorActionPreference = 'Stop'
if ($WithAI -and $WithAssistant) {
    throw '-WithAI와 -WithAssistant는 함께 사용할 수 없습니다. 분석 방식을 하나만 선택하세요.'
}
if ($ClipLimit -le 0) { throw '-ClipLimit는 양수여야 합니다.' }
Set-Location (Split-Path $PSScriptRoot -Parent)
$demo = Join-Path (Get-Location) '.venv\Scripts\chzzk-demo.exe'
function Invoke-Demo {
    & $demo @args
    if ($LASTEXITCODE -ne 0) { throw "단계 실패: $($args[0]). 해당 명령을 다시 실행할 수 있습니다." }
}
if (-not (Test-Path (Join-Path $Job 'job.json'))) {
    if (-not $Url) { throw '-Url에 치지직 다시보기 주소를 입력하세요.' }
    $ingestArgs = @('ingest', '--job', $Job, '--url', $Url, '--start', $Start, '--duration', $Duration)
    if ($Format) { $ingestArgs += @('--format', $Format) }
    Invoke-Demo @ingestArgs
}
else {
    $saved = Get-Content -LiteralPath (Join-Path $Job 'job.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if (($Url -and $saved.url -ne $Url) -or $saved.start -ne $Start -or $saved.end -ne [Math]::Min($saved.source_duration, $Start + $Duration)) {
        throw 'Existing job has a different URL or time range. Use a new -Job folder.'
    }
}
if (-not $AllowNoChat) { Invoke-Demo chats --job $Job }
Invoke-Demo transcribe --job $Job
if ($AllowNoChat) { Invoke-Demo signals --job $Job --allow-no-chat }
else { Invoke-Demo signals --job $Job }
if ($WithAssistant) {
    Invoke-Demo assistant-prepare --job $Job --stage text
    Write-Host "대기: $Job 대본 분석 자료를 준비했습니다. 아직 분석·클립·검수 페이지를 완료하지 않았습니다."
    Write-Host "Codex에 요청: '$Job 작업 폴더를 AGENTS.md의 임시 assistant 분석 절차로 분석하고 상위 ${ClipLimit}개 클립을 검토한 뒤 report까지 만들어줘.'"
    Write-Host '후속 단계는 README의 임시 assistant 분석 절차를 참고하세요.'
    return
}
if ($WithAI) { Invoke-Demo text-candidates --job $Job }
Invoke-Demo combine --job $Job
Invoke-Demo clips --job $Job --limit $ClipLimit
if ($WithAI) { Invoke-Demo review-video --job $Job --limit $ClipLimit }
Invoke-Demo report --job $Job
Write-Host "완료: $Job/report.html (싱크를 확인하지 않았다면 unverified 상태입니다.)"
