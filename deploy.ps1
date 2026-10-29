param(
    [Parameter(Position = 0, Mandatory = $false)]
    [string]$Message = "Update bot features and fixes",

    [Parameter(Mandatory = 0)]
    [string]$SshTarget = $env:VM_SSH_TARGET,

    [Parameter(Mandatory = $false)]
    [string]$RemoteDir = "/opt/tgfinderbot",

    [Parameter(Mandatory = $false)]
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"

# Автоматичний пошук Git, якщо він не доданий у системний PATH
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    $gitPaths = @(
        "$env:LOCALAPPDATA\Programs\Git\cmd",
        "C:\Program Files\Git\cmd",
        "C:\Program Files (x86)\Git\cmd"
    )
    foreach ($gp in $gitPaths) {
        if (Test-Path "$gp\git.exe") {
            $env:PATH = "$gp;$env:PATH"
            break
        }
    }
}

Write-Host "📦 [1/3] Додавання та коміт локальних змін у Git..." -ForegroundColor Cyan
git add -A

$status = git status --porcelain
if ([string]::IsNullOrWhiteSpace($status)) {
    Write-Host "ℹ️ Немає нових незбережених змін для коміту, виконую push поточного стану..." -ForegroundColor Yellow
} else {
    git commit -m "$Message"
}

Write-Host "🚀 [2/3] Відправка коду в GitHub (гілка $Branch)..." -ForegroundColor Cyan
git push -u origin $Branch

if (-not [string]::IsNullOrWhiteSpace($SshTarget)) {
    Write-Host "🌐 [3/3] Оновлення та перезапуск бота на VM ($SshTarget)..." -ForegroundColor Cyan
    ssh $SshTarget "bash $RemoteDir/deploy/update_prod.sh $Branch"
    Write-Host "✅ Готово! Зміни залиті в прод і бот перезапущено." -ForegroundColor Green
} else {
    Write-Host "✅ Код успішно відправлено в GitHub!" -ForegroundColor Green
    Write-Host "💡 Щоб деплоїти на VM автоматично однією командою, вкажіть SSH-адресу сервера:" -ForegroundColor Yellow
    Write-Host "   .\deploy.ps1 'Оновлення' -SshTarget 'root@ВАШ_IP_СЕРВЕРА'" -ForegroundColor Gray
}
