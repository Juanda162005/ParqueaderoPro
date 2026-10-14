$ErrorActionPreference = "Stop"
$appRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $appRoot ".env.local"

function Require-Command([string] $name, [string] $message) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw $message
    }
}

Require-Command "docker" "Docker Desktop no está instalado o no está disponible en PATH."
Require-Command "py" "Python Launcher no está instalado. Instala Python 3.11 de 64 bits."

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop está instalado, pero no está iniciado. Ábrelo y vuelve a ejecutar el instalador."
}

$python = (py -3.11 -c "import sys; print(sys.executable)" 2>$null)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($python)) {
    throw "No se encontró Python 3.11. Instálalo desde https://www.python.org/downloads/"
}

if (-not (Test-Path $envFile)) {
    $jwt = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(48))
    $password = Read-Host "Contraseña inicial del administrador (mínimo 12 caracteres)"
    if ($password.Length -lt 12) {
        throw "La contraseña debe tener mínimo 12 caracteres."
    }
    @"
JWT_SECRET_KEY=$jwt
INITIAL_ADMIN_PASSWORD=$password
"@ | Set-Content -LiteralPath $envFile -Encoding UTF8
}

$venv = Join-Path $appRoot ".venv_lpr"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    & py -3.11 -m venv $venv
}
& (Join-Path $venv "Scripts\python.exe") -m pip install --upgrade pip
& (Join-Path $venv "Scripts\python.exe") -m pip install -r (Join-Path $appRoot "LPR_Cliente\requirements.txt")

& docker compose -f (Join-Path $appRoot "docker-compose.local.yml") --env-file $envFile up -d --build
if ($LASTEXITCODE -ne 0) {
    throw "No se pudieron iniciar los servicios Docker."
}

Write-Host ""
Write-Host "ParqueaderoPRO quedó instalado."
Write-Host "Frontend: http://localhost:8080"
Write-Host "API:      http://localhost:8000/health"
