param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryUrl,
    [string]$ProjectName = "",
    [string]$AppEnvFile = ".env.local-app",
    [string]$RunnerEnvFile = ".env.local-runner",
    [string]$Username = "local-preview-smoke",
    [string]$Password = "test-pass-123",
    [int]$ReadyTimeoutSeconds = 150,
    [switch]$UseRepoArtifacts,
    [switch]$KeepServers
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Resolve-RepoPath([string]$PathValue) {
    $candidate = $PathValue
    if (-not [System.IO.Path]::IsPathRooted($candidate)) {
        $candidate = Join-Path $repoRoot $candidate
    }
    return [System.IO.Path]::GetFullPath($candidate)
}

function Wait-HttpOk([string]$Url, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                return
            }
        }
        catch {
        }
        Start-Sleep -Seconds 2
    }
    throw "Timeout esperando $Url"
}

function Start-ManagedServer([string]$ScriptPath, [string]$EnvFilePath, [string]$Name) {
    $logDir = Join-Path $repoRoot ".codex-local"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $stdoutPath = Join-Path $logDir "$Name.$stamp.out.log"
    $stderrPath = Join-Path $logDir "$Name.$stamp.err.log"
    return Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath, "-EnvFile", $EnvFilePath) `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru
}

function Stop-ManagedServer($Process) {
    if ($null -eq $Process) {
        return
    }
    try {
        if (-not $Process.HasExited) {
            & cmd.exe /c "taskkill /PID $($Process.Id) /T /F" | Out-Null
        }
    }
    catch {
    }
}

function Get-CsrfFormToken([string]$Html) {
    $match = [regex]::Match($Html, 'name="csrfmiddlewaretoken" value="([^"]+)"')
    if (-not $match.Success) {
        throw "No se pudo extraer el csrfmiddlewaretoken del login."
    }
    return $match.Groups[1].Value
}

function Get-CookieValue($Session, [string]$Url, [string]$Name) {
    $cookie = $Session.Cookies.GetCookies($Url) | Where-Object { $_.Name -eq $Name } | Select-Object -First 1
    if ($null -eq $cookie) {
        throw "No se encontró la cookie $Name para $Url"
    }
    return $cookie.Value
}

function Invoke-LocalLogin([string]$BaseUrl, [string]$LoginPath, [string]$UserNameValue, [string]$PasswordValue) {
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $loginPage = Invoke-WebRequest -Uri "$BaseUrl$LoginPath" -WebSession $session
    $formToken = Get-CsrfFormToken $loginPage.Content
    $loginResponse = Invoke-WebRequest `
        -Uri "$BaseUrl$LoginPath" `
        -Method POST `
        -WebSession $session `
        -Headers @{
            "Accept" = "application/json"
            "X-Requested-With" = "fetch"
            "X-CSRFToken" = $formToken
            "Referer" = "$BaseUrl$LoginPath"
        } `
        -ContentType "application/x-www-form-urlencoded; charset=UTF-8" `
        -Body "username=$UserNameValue&password=$PasswordValue"
    $payload = $loginResponse.Content | ConvertFrom-Json
    if (-not $payload.ok) {
        throw "El login devolvió una respuesta no exitosa."
    }
    return $session
}

function Invoke-CsrfJsonPost($Session, [string]$BaseUrl, [string]$Path) {
    $csrfToken = Get-CookieValue -Session $Session -Url $BaseUrl -Name "csrftoken"
    return Invoke-WebRequest `
        -Uri "$BaseUrl$Path" `
        -Method POST `
        -WebSession $Session `
        -Headers @{
            "Accept" = "application/json"
            "X-Requested-With" = "fetch"
            "X-CSRFToken" = $csrfToken
            "Referer" = "$BaseUrl/"
        }
}

function Wait-ContainerRemoved([string]$ContainerName, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $runningContainers = & docker ps --format "{{.Names}}"
        if (-not (($runningContainers -split "`r?`n") -contains $ContainerName)) {
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "El contenedor $ContainerName siguió corriendo después del stop."
}

$appEnvPath = Resolve-RepoPath $AppEnvFile
$runnerEnvPath = Resolve-RepoPath $RunnerEnvFile
if (-not (Test-Path $appEnvPath)) {
    throw "No se encontró $appEnvPath"
}
if (-not (Test-Path $runnerEnvPath)) {
    throw "No se encontró $runnerEnvPath"
}

$appProcess = $null
$runnerProcess = $null
$preview = $null
$previewResponse = $null
$appBaseUrl = "http://127.0.0.1:8000"
$runnerBaseUrl = "http://127.0.0.1:9000"

try {
    & docker info --format "{{.ServerVersion}}" | Out-Null

    $appProcess = Start-ManagedServer -ScriptPath (Join-Path $repoRoot "scripts\\run-local-app.ps1") -EnvFilePath $appEnvPath -Name "app"
    $runnerProcess = Start-ManagedServer -ScriptPath (Join-Path $repoRoot "scripts\\run-local-preview-runner.ps1") -EnvFilePath $runnerEnvPath -Name "runner"

    Wait-HttpOk -Url "$appBaseUrl/health/" -TimeoutSeconds 60
    Wait-HttpOk -Url "$runnerBaseUrl/health/" -TimeoutSeconds 60

    $env:DOTENV_PATH = $appEnvPath
    $commandArgs = @(
        "manage.py",
        "prepare_local_preview_smoke",
        "--repository-url",
        $RepositoryUrl,
        "--username",
        $Username,
        "--password",
        $Password
    )
    if ($ProjectName) {
        $commandArgs += @("--project-name", $ProjectName)
    }
    if ($UseRepoArtifacts) {
        $commandArgs += "--use-repo-artifacts"
    }
    $fixture = (& "$repoRoot\\.venv\\Scripts\\python.exe" @commandArgs) -join ""
    $fixturePayload = $fixture | ConvertFrom-Json
    $analysisId = $fixturePayload.analysis_id

    $session = Invoke-LocalLogin -BaseUrl $appBaseUrl -LoginPath "/accounts/login/" -UserNameValue $fixturePayload.username -PasswordValue $fixturePayload.password
    $previewResponse = Invoke-CsrfJsonPost -Session $session -BaseUrl $appBaseUrl -Path "/api/analyses/$analysisId/preview/"
    $preview = $previewResponse.Content | ConvertFrom-Json
    $previewId = $preview.id

    $deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $detailResponse = Invoke-WebRequest -Uri "$appBaseUrl/api/previews/$previewId/" -WebSession $session
        $preview = $detailResponse.Content | ConvertFrom-Json
        if ($preview.status -eq "ready") {
            break
        }
        if ($preview.status -in @("failed", "stopped")) {
            throw "La preview terminó en estado $($preview.status): $($preview.logs)"
        }
        Start-Sleep -Seconds 5
    }
    if ($preview.status -ne "ready") {
        throw "La preview no llegó a READY dentro del timeout."
    }

    $previewBody = (Invoke-WebRequest -Uri $preview.access_url -TimeoutSec 15).Content
    if ($previewBody -ne "preview smoke ok") {
        throw "La preview respondió un contenido inesperado: $previewBody"
    }

    $stopResponse = Invoke-CsrfJsonPost -Session $session -BaseUrl $appBaseUrl -Path "/api/previews/$previewId/stop/"
    $stoppedPreview = $stopResponse.Content | ConvertFrom-Json
    if ($stoppedPreview.status -ne "stopped") {
        throw "La detención devolvió un estado inesperado: $($stoppedPreview.status)"
    }

    foreach ($resourceName in @($preview.resource_names)) {
        Wait-ContainerRemoved -ContainerName $resourceName -TimeoutSeconds 30
    }

    [pscustomobject]@{
        repository_url = $RepositoryUrl
        project_name = $fixturePayload.project_name
        analysis_id = $analysisId
        preview_id = $previewId
        access_url = $preview.access_url
        use_repo_artifacts = [bool]$UseRepoArtifacts
        result = "ok"
    } | ConvertTo-Json -Compress | Write-Output
}
finally {
    if (-not $KeepServers) {
        Stop-ManagedServer -Process $appProcess
        Stop-ManagedServer -Process $runnerProcess
    }
}
