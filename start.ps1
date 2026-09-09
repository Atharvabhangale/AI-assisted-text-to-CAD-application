<#
.SYNOPSIS
    Start the whole local MVP with one command: backend, frontend, browser.

.DESCRIPTION
    Replaces the manual sequence -- activate `.venv`, set `CAD_API_CACHE_ROOT`,
    load `apps/api/.env`, run `uvicorn`, run `npm run dev`, open the browser --
    with `.\start.ps1`.

    It changes no application behaviour. The same ASGI app is started through
    the same factory on the same port, and the same Vite dev server proxies
    `/api` to it, so the browser still sees a single origin and the backend
    still needs no CORS policy. Everything the application decides for itself
    -- which provider answers, which model, what a valid document is -- is
    left to the application.

    The credential is handled by one rule: it is placed in the **backend
    process's** environment and nowhere else. It is not written to the state
    file, not passed as a command-line argument, not given to the Vite
    process, and not printed. Only variable names and value lengths are
    reported. `apps/api/.env` is read by this script; the application still
    reads no `.env` file, which is the invariant `cad_ai.config` documents.

    Already-running servers are adopted, never duplicated.

.PARAMETER BackendPort
    Port for the FastAPI backend. Defaults to 8000, which is what
    `apps/web/vite.config.ts` proxies to.

.PARAMETER FrontendPort
    Port for the Vite dev server. Defaults to 5173, Vite's own default.

.PARAMETER NoBrowser
    Do not open the frontend in the default browser.

.PARAMETER TimeoutSeconds
    How long to wait for each server to become healthy. Defaults to 90; the
    backend imports the CAD kernel on startup, which is not instant.

.EXAMPLE
    .\start.ps1

.EXAMPLE
    .\start.ps1 -NoBrowser -BackendPort 8001 -FrontendPort 5200
#>

[CmdletBinding()]
param(
    [int] $BackendPort = 8000,
    [int] $FrontendPort = 5173,
    [switch] $NoBrowser,
    [int] $TimeoutSeconds = 90,

    # Where to read the credential from, when it is not in this checkout.
    # Exists for the git-worktree case: an experiment branch checked out
    # beside the main tree has no `.env` of its own, because `.env` is
    # gitignored and must not be copied -- one credential, in one place. This
    # lets the experiment *read* the main tree's file without a second copy
    # existing anywhere on disk. The value still goes only into the backend
    # process's environment, and is still never printed.
    [string] $DotEnvPath
)

$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'scripts\DevServers.psm1') -Force

# --- presentation helpers ---------------------------------------------------

function Write-Step   ($Text) { Write-Host "  $Text" }
function Write-Good   ($Text) { Write-Host "  $Text" -ForegroundColor Green }
function Write-Warn   ($Text) { Write-Host "  $Text" -ForegroundColor Yellow }
function Write-Bad    ($Text) { Write-Host "  $Text" -ForegroundColor Red }
function Write-Heading($Text) { Write-Host ""; Write-Host $Text -ForegroundColor Cyan }

function Show-LogTail {
    <#
    .SYNOPSIS
        Print the tail of a log so a startup failure explains itself.
    #>
    param([string] $Path, [string] $Label, [int] $Lines = 20)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    $content = Get-Content -LiteralPath $Path -Tail $Lines -ErrorAction SilentlyContinue
    if ($null -eq $content -or $content.Count -eq 0) { return }
    Write-Host ""
    Write-Host "  --- last $Lines lines of $Label ---" -ForegroundColor DarkGray
    foreach ($line in $content) { Write-Host "  $line" -ForegroundColor DarkGray }
}

function Fail-With ($Message) {
    Write-Host ""
    Write-Bad $Message
    Write-Host ""
    exit 1
}

function Get-OwnerDescription {
    <#
    .SYNOPSIS
        Describe who holds a port, saying so when it was us last time.
    .DESCRIPTION
        A previous run recorded both the process it launched and the process
        that ended up holding the port; either match means these servers are
        ours, so the message can say "an earlier start.ps1" rather than
        alarming the reader about an unknown process.
    #>
    param(
        [int] $OwnerPid,
        $Recorded
    )

    if ($null -ne $Recorded) {
        foreach ($candidate in $Recorded) {
            if ($null -eq $candidate) { continue }
            if ([int] $candidate -gt 0 -and [int] $candidate -eq $OwnerPid) {
                return 'an earlier start.ps1'
            }
        }
    }
    if ($OwnerPid -gt 0) {
        return (Get-ProcessDescription -Id $OwnerPid)
    }
    return 'another process'
}

Write-Host ""
Write-Host "Text to CAD - local development" -ForegroundColor White

# --- 1. locate the repository ----------------------------------------------

Write-Heading "Repository"

$repoRoot = Get-RepoRoot -StartAt $PSScriptRoot
if ([string]::IsNullOrEmpty($repoRoot)) {
    Fail-With "Could not find the repository root from '$PSScriptRoot'. Run this script from inside the repository."
}
Write-Good "root: $repoRoot"

$stateDirectory = Get-StateDirectory -RepoRoot $repoRoot -Create
$backendOutLog = Join-Path $stateDirectory 'backend.out.log'
$backendErrLog = Join-Path $stateDirectory 'backend.err.log'
$frontendOutLog = Join-Path $stateDirectory 'frontend.out.log'
$frontendErrLog = Join-Path $stateDirectory 'frontend.err.log'

# --- 2. the existing virtual environment ------------------------------------

Write-Heading "Python"

$python = Resolve-VenvPython -RepoRoot $repoRoot
if ([string]::IsNullOrEmpty($python)) {
    Write-Bad "No virtual environment found at '$(Join-Path $repoRoot '.venv')'."
    Write-Host ""
    Write-Host "  Create it once, then re-run this script:" -ForegroundColor DarkGray
    Write-Host "    py -3 -m venv .venv" -ForegroundColor DarkGray
    Write-Host "    .\.venv\Scripts\python.exe -m pip install -e packages\cad-core -e `"apps\api[test,serve,ai]`"" -ForegroundColor DarkGray
    Fail-With "Cannot start without a Python environment."
}
Write-Good "interpreter: $python"

# --- 3. load apps/api/.env (this script reads it; the application does not) --

Write-Heading "Environment"

if ([string]::IsNullOrWhiteSpace($DotEnvPath)) {
    $dotEnvPath = Join-Path $repoRoot 'apps\api\.env'
} else {
    $dotEnvPath = $DotEnvPath
    Write-Step "reading credentials from: $dotEnvPath (-DotEnvPath)"
}
$dotEnv = Read-DotEnvFile -Path $dotEnvPath

if (-not (Test-Path -LiteralPath $dotEnvPath -PathType Leaf)) {
    Write-Warn "No 'apps\api\.env' found."
    Write-Host "    The app will start, and the CAD-JSON flow under 'Advanced' will work," -ForegroundColor DarkGray
    Write-Host "    but 'Generate CAD' will answer model_error because no credential is set." -ForegroundColor DarkGray
    Write-Host "    To enable it, create the file (it is already gitignored) with a line:" -ForegroundColor DarkGray
    Write-Host "      ANTHROPIC_API_KEY=<your key>" -ForegroundColor DarkGray
} elseif ($dotEnv.Count -eq 0) {
    Write-Warn "'apps\api\.env' exists but defines no usable NAME=VALUE line."
    Write-Host "    'Generate CAD' will answer model_error until a credential is set." -ForegroundColor DarkGray
} else {
    # Names and lengths only. A value is never printed, by design.
    foreach ($entry in (Get-DotEnvSummary -Values $dotEnv)) {
        Write-Good "loaded $($entry.Name) (present, $($entry.Length) characters)"
    }
}

$credentialNames = Get-CredentialVariableNames
$haveCredential = $false
foreach ($name in $credentialNames) {
    if (-not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) { $haveCredential = $true }
    if ($dotEnv.ContainsKey($name)) { $haveCredential = $true }
}
if (-not $haveCredential) {
    Write-Warn "No provider credential is available; text-to-CAD generation will be unavailable."
}

# The build cache root the application requires. It refuses to guess a path,
# so the launcher supplies one -- inside the gitignored state directory, so it
# is predictable and easy to clear. An existing setting always wins.
$cacheRoot = $env:CAD_API_CACHE_ROOT
if ([string]::IsNullOrWhiteSpace($cacheRoot)) {
    if ($dotEnv.ContainsKey('CAD_API_CACHE_ROOT')) {
        $cacheRoot = $dotEnv['CAD_API_CACHE_ROOT']
    } else {
        $cacheRoot = Join-Path $stateDirectory 'cad-cache'
    }
}
if (-not (Test-Path -LiteralPath $cacheRoot)) {
    New-Item -ItemType Directory -Path $cacheRoot -Force | Out-Null
}
Write-Good "CAD_API_CACHE_ROOT: $cacheRoot"

# --- port availability ------------------------------------------------------

Write-Heading "Ports"

$backendHealthUrl = "http://127.0.0.1:$BackendPort/health"
$frontendUrl = "http://localhost:$FrontendPort/"
$frontendApiHealthUrl = "http://localhost:$FrontendPort/api/health"

$previous = Read-DevState -RepoRoot $repoRoot

$backendAlreadyUp = $false
$frontendAlreadyUp = $false

if (Test-TcpPortListening -Port $BackendPort) {
    if (Test-HttpOk -Url $backendHealthUrl) {
        $backendAlreadyUp = $true
        $owner = Get-PortListenerPid -Port $BackendPort
        if ($null -eq $owner) { $owner = 0 }
        $recorded = $null
        if ($null -ne $previous) { $recorded = @($previous.backendPid, $previous.backendListenerPid) }
        $who = Get-OwnerDescription -OwnerPid $owner -Recorded $recorded
        Write-Good "backend already healthy on $BackendPort (started by $who) - reusing it"
    } else {
        $owner = Get-PortListenerPid -Port $BackendPort
        $description = "an unknown process"
        if ($null -ne $owner) { $description = Get-ProcessDescription -Id $owner }
        Write-Bad "Port $BackendPort is held by $description, and it does not answer $backendHealthUrl."
        Write-Host "    That is not this project's backend. Free the port, or choose another:" -ForegroundColor DarkGray
        Write-Host "      .\stop.ps1" -ForegroundColor DarkGray
        Write-Host "      .\start.ps1 -BackendPort 8001" -ForegroundColor DarkGray
        Fail-With "Refusing to start a second backend on an occupied port."
    }
} else {
    Write-Step "backend port $BackendPort is free"
}

if (Test-TcpPortListening -Port $FrontendPort) {
    if (Test-HttpOk -Url $frontendUrl) {
        $frontendAlreadyUp = $true
        $owner = Get-PortListenerPid -Port $FrontendPort
        if ($null -eq $owner) { $owner = 0 }
        $recorded = $null
        if ($null -ne $previous) { $recorded = @($previous.frontendPid, $previous.frontendListenerPid) }
        $who = Get-OwnerDescription -OwnerPid $owner -Recorded $recorded
        Write-Good "frontend already serving on $FrontendPort (started by $who) - reusing it"
    } else {
        $owner = Get-PortListenerPid -Port $FrontendPort
        $description = "an unknown process"
        if ($null -ne $owner) { $description = Get-ProcessDescription -Id $owner }
        Write-Bad "Port $FrontendPort is held by $description, and it does not serve a page."
        Write-Host "    Free the port, or choose another:" -ForegroundColor DarkGray
        Write-Host "      .\stop.ps1" -ForegroundColor DarkGray
        Write-Host "      .\start.ps1 -FrontendPort 5200" -ForegroundColor DarkGray
        Fail-With "Refusing to start a second frontend on an occupied port."
    }
} else {
    Write-Step "frontend port $FrontendPort is free"
}

# --- 6. start the backend ---------------------------------------------------

$backendProcess = $null
$backendPid = 0
$backendStart = ''

if ($backendAlreadyUp) {
    # Adopting: prefer the id a previous run launched, because stopping that
    # one takes its children with it. Fall back to whoever holds the port.
    $owner = Get-PortListenerPid -Port $BackendPort
    if ($null -eq $owner) { $owner = 0 }
    $wasOurs = $false
    if ($null -ne $previous) {
        foreach ($candidate in @($previous.backendPid, $previous.backendListenerPid)) {
            if ($null -ne $candidate -and [int] $candidate -gt 0 -and [int] $candidate -eq $owner) { $wasOurs = $true }
        }
    }
    if ($wasOurs) {
        $backendPid = [int] $previous.backendPid
        $backendStart = [string] $previous.backendStart
    } else {
        $backendPid = $owner
        $backendStart = ''
    }
} else {
    Write-Heading "Backend"

    # PYTHONPATH is set so the launcher works whether or not the packages were
    # installed into the environment as editable.
    $pythonPath = @(
        (Join-Path $repoRoot 'packages\cad-core\src'),
        (Join-Path $repoRoot 'apps\api\src')
    ) -join ';'

    $savedPythonPath = $env:PYTHONPATH
    $savedCacheRoot = $env:CAD_API_CACHE_ROOT
    $injected = @()

    try {
        $env:PYTHONPATH = $pythonPath
        $env:CAD_API_CACHE_ROOT = $cacheRoot

        # Put the .env values into *this* process's environment only long
        # enough for Start-Process to hand a copy to the backend, then remove
        # them again. An already-set non-empty value wins, so a variable
        # exported in the caller's shell still takes precedence.
        foreach ($name in ($dotEnv.Keys | Sort-Object)) {
            $existing = [Environment]::GetEnvironmentVariable($name)
            if (-not [string]::IsNullOrWhiteSpace($existing)) { continue }
            Set-Item -Path "Env:\$name" -Value $dotEnv[$name]
            $injected += $name
        }

        $arguments = @(
            '-m', 'uvicorn',
            '--factory', 'cad_api.app:app_from_environment',
            '--host', '127.0.0.1',
            '--port', "$BackendPort"
        )

        Write-Step "uvicorn --factory cad_api.app:app_from_environment on 127.0.0.1:$BackendPort"

        $backendProcess = Start-Process -FilePath $python `
            -ArgumentList $arguments `
            -WorkingDirectory (Join-Path $repoRoot 'apps\api') `
            -RedirectStandardOutput $backendOutLog `
            -RedirectStandardError $backendErrLog `
            -WindowStyle Hidden `
            -PassThru
    } finally {
        # The credential must not outlive the spawn, and must never be
        # visible to the frontend process started below.
        foreach ($name in $injected) {
            Remove-Item -Path "Env:\$name" -ErrorAction SilentlyContinue
        }
        $env:PYTHONPATH = $savedPythonPath
        $env:CAD_API_CACHE_ROOT = $savedCacheRoot
    }

    if ($null -eq $backendProcess) {
        Show-LogTail -Path $backendErrLog -Label 'backend stderr'
        Fail-With "The backend process could not be started."
    }

    $backendPid = $backendProcess.Id
    try { $backendStart = $backendProcess.StartTime.ToString('o') } catch { $backendStart = '' }

    Write-Step "waiting for $backendHealthUrl ..."
    $ready = Wait-ForHttpOk -Url $backendHealthUrl -TimeoutSeconds $TimeoutSeconds -ProcessId $backendPid
    if (-not $ready.Ready) {
        if ($ready.Reason -eq 'exited') {
            Write-Bad "The backend exited during startup."
        } else {
            Write-Bad "The backend did not become healthy within $TimeoutSeconds seconds."
        }
        Show-LogTail -Path $backendErrLog -Label 'backend stderr'
        Show-LogTail -Path $backendOutLog -Label 'backend stdout' -Lines 10
        if ($null -ne $backendProcess) { Stop-ProcessTree -Id $backendPid | Out-Null }
        Fail-With "Backend startup failed. Nothing was left running."
    }
    Write-Good "backend healthy (pid $backendPid)"
}

# --- 7. start the frontend --------------------------------------------------

$frontendProcess = $null
$frontendPid = 0
$frontendStart = ''

if ($frontendAlreadyUp) {
    $owner = Get-PortListenerPid -Port $FrontendPort
    if ($null -eq $owner) { $owner = 0 }
    $wasOurs = $false
    if ($null -ne $previous) {
        foreach ($candidate in @($previous.frontendPid, $previous.frontendListenerPid)) {
            if ($null -ne $candidate -and [int] $candidate -gt 0 -and [int] $candidate -eq $owner) { $wasOurs = $true }
        }
    }
    if ($wasOurs) {
        $frontendPid = [int] $previous.frontendPid
        $frontendStart = [string] $previous.frontendStart
    } else {
        $frontendPid = $owner
        $frontendStart = ''
    }
} else {
    Write-Heading "Frontend"

    $npm = Resolve-NpmCommand
    if ([string]::IsNullOrEmpty($npm)) {
        Write-Bad "Could not find 'npm'. Node.js does not appear to be installed, or is not on PATH."
        Write-Host "    Install Node.js, then re-run this script." -ForegroundColor DarkGray
        if (-not $backendAlreadyUp -and $backendPid -gt 0) {
            Stop-ProcessTree -Id $backendPid | Out-Null
            Write-Step "stopped the backend again, so nothing is left half-started"
        }
        Fail-With "Cannot start the frontend without npm."
    }
    Write-Step "npm: $npm"

    # `npm.cmd` shells out to `node`, so npm on its own is not enough: if Node
    # is installed but absent from PATH, npm fails with
    # '"node"' is not recognized as an internal or external command.
    # Node's directory is prepended to the PATH the child receives.
    $node = Resolve-NodeCommand
    if ([string]::IsNullOrEmpty($node)) {
        Write-Bad "Found npm at '$npm' but could not find 'node'."
        Write-Host "    npm cannot run without Node. Install Node.js, or add it to PATH." -ForegroundColor DarkGray
        if (-not $backendAlreadyUp -and $backendPid -gt 0) {
            Stop-ProcessTree -Id $backendPid | Out-Null
            Write-Step "stopped the backend again, so nothing is left half-started"
        }
        Fail-With "Cannot start the frontend without node."
    }
    $nodeDirectory = Split-Path -Parent $node
    Write-Step "node: $node"

    $webRoot = Join-Path $repoRoot 'apps\web'
    if (-not (Test-Path -LiteralPath (Join-Path $webRoot 'node_modules'))) {
        Write-Warn "'apps\web\node_modules' is missing; installing dependencies once (this may take a while)."
        Push-Location -LiteralPath $webRoot
        $savedPathForInstall = $env:Path
        try {
            $env:Path = "$nodeDirectory;$env:Path"
            & $npm 'install' | Out-Null
        } finally {
            $env:Path = $savedPathForInstall
            Pop-Location
        }
        if ($LASTEXITCODE -ne 0) {
            if (-not $backendAlreadyUp -and $backendPid -gt 0) { Stop-ProcessTree -Id $backendPid | Out-Null }
            Fail-With "'npm install' failed. Run it manually in apps\web to see why."
        }
    }

    # --strictPort so an occupied port is an error rather than a silent move
    # to 5174 -- the printed URL must be the URL that works.
    $arguments = @('run', 'dev', '--', '--port', "$FrontendPort", '--strictPort')

    $savedApiOrigin = $env:CAD_API_ORIGIN
    $savedPath = $env:Path
    try {
        # Keep the proxy pointing at the backend this script actually started,
        # so a non-default -BackendPort still works end to end.
        $env:CAD_API_ORIGIN = "http://127.0.0.1:$BackendPort"
        $env:Path = "$nodeDirectory;$env:Path"

        Write-Step "vite dev server on port $FrontendPort (proxying /api to $env:CAD_API_ORIGIN)"

        $frontendProcess = Start-Process -FilePath $npm `
            -ArgumentList $arguments `
            -WorkingDirectory $webRoot `
            -RedirectStandardOutput $frontendOutLog `
            -RedirectStandardError $frontendErrLog `
            -WindowStyle Hidden `
            -PassThru
    } finally {
        $env:CAD_API_ORIGIN = $savedApiOrigin
        $env:Path = $savedPath
    }

    if ($null -eq $frontendProcess) {
        Show-LogTail -Path $frontendErrLog -Label 'frontend stderr'
        if (-not $backendAlreadyUp -and $backendPid -gt 0) { Stop-ProcessTree -Id $backendPid | Out-Null }
        Fail-With "The frontend process could not be started."
    }

    $frontendPid = $frontendProcess.Id
    try { $frontendStart = $frontendProcess.StartTime.ToString('o') } catch { $frontendStart = '' }

    Write-Step "waiting for $frontendUrl ..."
    $ready = Wait-ForHttpOk -Url $frontendUrl -TimeoutSeconds $TimeoutSeconds -ProcessId $frontendPid
    if (-not $ready.Ready) {
        if ($ready.Reason -eq 'exited') {
            Write-Bad "The frontend exited during startup (port $FrontendPort may already be taken)."
        } else {
            Write-Bad "The frontend did not start serving within $TimeoutSeconds seconds."
        }
        Show-LogTail -Path $frontendErrLog -Label 'frontend stderr'
        Show-LogTail -Path $frontendOutLog -Label 'frontend stdout' -Lines 10
        Stop-ProcessTree -Id $frontendPid | Out-Null
        if (-not $backendAlreadyUp -and $backendPid -gt 0) {
            Stop-ProcessTree -Id $backendPid | Out-Null
            Write-Step "stopped the backend again, so nothing is left half-started"
        }
        Fail-With "Frontend startup failed."
    }
    Write-Good "frontend serving (pid $frontendPid)"
}

# --- 8. both healthy, including the proxy hop the browser depends on --------

Write-Heading "Health"

Write-Good "backend  $backendHealthUrl"
if (Test-HttpOk -Url $frontendApiHealthUrl) {
    Write-Good "proxy    $frontendApiHealthUrl (the browser can reach the API)"
} else {
    Write-Warn "The frontend is up but $frontendApiHealthUrl did not answer."
    Write-Host "    The page will load; API calls from it may fail." -ForegroundColor DarkGray
}

# --- record what we started, for stop.ps1 ----------------------------------
# Ports, ids, times and paths. No credential, by construction.

# The process that *holds* each port is not always the process that was
# launched: `npm.cmd` starts Vite's Node process as a child, so the listener
# is that child. Both are recorded -- the launched id so the whole tree can be
# stopped, the listener id so a re-run recognises its own servers and so an
# orphaned child can still be cleaned up.
$backendListenerPid = Get-PortListenerPid -Port $BackendPort
if ($null -eq $backendListenerPid) { $backendListenerPid = 0 }
$frontendListenerPid = Get-PortListenerPid -Port $FrontendPort
if ($null -eq $frontendListenerPid) { $frontendListenerPid = 0 }

$state = @{
    repoRoot            = $repoRoot
    backendPid          = $backendPid
    backendListenerPid  = $backendListenerPid
    backendPort         = $BackendPort
    backendStart        = $backendStart
    frontendPid         = $frontendPid
    frontendListenerPid = $frontendListenerPid
    frontendPort        = $FrontendPort
    frontendStart       = $frontendStart
    frontendUrl         = $frontendUrl
    cacheRoot           = $cacheRoot
    startedAt           = (Get-Date).ToString('o')
}
Write-DevState -RepoRoot $repoRoot -State $state | Out-Null

# --- 9 and 10. tell the human, and open the page ---------------------------

Write-Heading "Ready"
Write-Host ""
Write-Host "    $frontendUrl" -ForegroundColor White
Write-Host ""
Write-Step "logs:  $stateDirectory"
Write-Step "stop:  .\stop.ps1"
Write-Host ""

if (-not $NoBrowser) {
    try {
        Start-Process $frontendUrl | Out-Null
        Write-Step "opened in your default browser (use -NoBrowser to skip)"
    } catch {
        Write-Warn "Could not open a browser automatically; open the URL above."
    }
}

Write-Host ""
exit 0
