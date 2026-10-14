param(
    [string]$Url,
    [string]$Job = 'runs/demo',
    [int]$Start = 0,
    [int]$Duration = 7200,
    [switch]$AllowNoChat,
    [switch]$WithAI,
    [int]$ClipLimit = 10
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$demo = Join-Path (Get-Location) '.venv\Scripts\chzzk-demo.exe'
function Invoke-Demo {
    & $demo @args
    if ($LASTEXITCODE -ne 0) { throw "단계 실패: $($args[0]). 해당 명령을 다시 실행할 수 있습니다." }
}
if (-not (Test-Path (Join-Path $Job 'job.json'))) {
    if (-not $Url) { throw '-Url에 치지직 다시보기 주소를 입력하세요.' }
    Invoke-Demo ingest --job $Job --url $Url --start $Start --duration $Duration
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
if ($WithAI) { Invoke-Demo text-candidates --job $Job }
Invoke-Demo combine --job $Job
Invoke-Demo clips --job $Job --limit $ClipLimit
if ($WithAI) { Invoke-Demo review-video --job $Job --limit $ClipLimit }
Invoke-Demo report --job $Job
Write-Host "완료: $Job/report.html (싱크를 확인하지 않았다면 unverified 상태입니다.)"
