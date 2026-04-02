param(
    [string]$EnvFile = ".env.local-runner"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path $EnvFile)) {
    throw "No se encontró $EnvFile. Copiá .env.local-runner.example a $EnvFile."
}

Get-Content $EnvFile | ForEach-Object {
    if (-not $_ -or $_.Trim().StartsWith("#")) { return }
    $parts = $_ -split "=", 2
    if ($parts.Count -eq 2) {
        [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
    }
}

& "$repoRoot\.venv\Scripts\python.exe" manage.py runserver 127.0.0.1:9000
