param([int]$Port = 18081)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$qaPython = Join-Path $projectRoot '.qa-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $qaPython)) { throw 'Создайте .qa-venv и установите requirements-qa.txt согласно README.' }
$checkName = 'todo-check-' + [guid]::NewGuid().ToString('N').Substring(0,12)
$taskLabel = 'todo-browser-acceptance'
$qaOutput = Join-Path $projectRoot 'artifacts\browser-final'
New-Item -ItemType Directory -Force -Path $qaOutput | Out-Null
$env:TODO_QA_URL = "http://127.0.0.1:$Port"
$env:TODO_QA_OUTPUT = $qaOutput
# Используется только headless-браузер. Никаких окон, системного ввода и буфера обмена.
$resultCode = 1
try {
    docker build -t todo-local:validated .
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка сборки образа' }
    docker run -d --rm --name $checkName --label "todo.task=$taskLabel" --label todo.purpose=temporary --tmpfs /data:uid=10001,gid=10001 -p "127.0.0.1:${Port}:8080" todo-local:validated
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка запуска тестового контейнера' }
    $healthy = $false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "$env:TODO_QA_URL/health"
            if ($health.status -eq 'ok') { $healthy = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $healthy) { throw 'Контейнер не прошёл проверку готовности' }
    # Эти учётные записи существуют только в tmpfs одноразового контейнера.
    @'
from app.main import create_user
create_user('qa_a', 'qa-password-one')
create_user('qa_b', 'qa-password-two')
'@ | docker exec -i $checkName python -
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка подготовки тестовых пользователей' }
    & $qaPython scripts/browser_acceptance.py
    $resultCode = $LASTEXITCODE
    & $qaPython scripts/browser_annotations.py
    if ($LASTEXITCODE -ne 0) { $resultCode = $LASTEXITCODE }
} finally {
    # Лог сохраняется до удаления; tmpfs не создаёт постоянного Docker-тома.
    docker logs $checkName 2>&1 | Out-File -LiteralPath (Join-Path $qaOutput 'docker.log') -Encoding utf8
    docker rm -f $checkName
    $remaining = docker ps -aq --filter "name=^/$checkName$"
    if ($remaining) { throw "Не удалось удалить временный контейнер $checkName" }
}
exit $resultCode
