<#
.SYNOPSIS
    Tests for the local development launcher's logic.

.DESCRIPTION
    Exercises the pure, decidable parts of `scripts\DevServers.psm1`: `.env`
    parsing, the redaction summary, repository-root discovery, state
    round-tripping, process-identity checking and port probing.

    Deliberately dependency-free. The only Pester on this machine is 3.4.0,
    whose `Should Be` syntax was removed in Pester 5, so a test file written
    against it would break the day Pester is upgraded. A handful of
    assertions needs no framework.

    What is *not* tested here, on purpose: actually starting the two servers.
    That is an integration concern with real ports, a real CAD kernel and a
    real credential, and it is verified by running `.\start.ps1` -- which is
    the honest test for it. These tests never start a server, never touch
    `apps\api\.env`, and never need a credential.

.EXAMPLE
    .\scripts\Test-DevLauncher.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'DevServers.psm1') -Force

$script:Passed = 0
$script:Failed = 0
$script:Failures = @()

function Assert-That {
    param(
        [Parameter(Mandatory = $true)] [string] $Name,
        [Parameter(Mandatory = $true)] [bool] $Condition,
        [string] $Detail = ''
    )
    if ($Condition) {
        $script:Passed++
        Write-Host "  ok   $Name" -ForegroundColor DarkGray
    } else {
        $script:Failed++
        $script:Failures += $Name
        $suffix = ''
        if (-not [string]::IsNullOrEmpty($Detail)) { $suffix = " -- $Detail" }
        Write-Host "  FAIL $Name$suffix" -ForegroundColor Red
    }
}

function Assert-Equal {
    param(
        [Parameter(Mandatory = $true)] [string] $Name,
        $Expected,
        $Actual
    )
    $same = ([string] $Expected) -eq ([string] $Actual)
    Assert-That -Name $Name -Condition $same -Detail "expected '$Expected', got '$Actual'"
}

function New-TempDirectory {
    $path = Join-Path ([System.IO.Path]::GetTempPath()) ("devlauncher-" + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    return $path
}

Write-Host ""
Write-Host "Test-DevLauncher" -ForegroundColor White

# --- .env parsing -----------------------------------------------------------

Write-Host ""
Write-Host "Read-DotEnvFile" -ForegroundColor Cyan

$temp = New-TempDirectory
try {
    $envFile = Join-Path $temp '.env'

    # A file exercising every accepted and rejected shape at once.
    $lines = @(
        '# a comment',
        '',
        'ANTHROPIC_API_KEY=sk-test-not-a-real-key',
        'QUOTED_DOUBLE="double quoted"',
        "QUOTED_SINGLE='single quoted'",
        'export EXPORTED=exported-value',
        '  SPACED  =  spaced-value  ',
        'EMPTY=',
        'NO_EQUALS_SIGN',
        '=leading-equals',
        '1INVALID=nope',
        'HAS-DASH=nope',
        'URL=http://127.0.0.1:8000/health?a=b'
    )
    Set-Content -LiteralPath $envFile -Value $lines -Encoding UTF8

    $values = Read-DotEnvFile -Path $envFile

    Assert-Equal -Name 'reads a plain value' `
        -Expected 'sk-test-not-a-real-key' -Actual $values['ANTHROPIC_API_KEY']
    Assert-Equal -Name 'strips double quotes' `
        -Expected 'double quoted' -Actual $values['QUOTED_DOUBLE']
    Assert-Equal -Name 'strips single quotes' `
        -Expected 'single quoted' -Actual $values['QUOTED_SINGLE']
    Assert-Equal -Name 'tolerates a leading export' `
        -Expected 'exported-value' -Actual $values['EXPORTED']
    Assert-Equal -Name 'trims surrounding whitespace' `
        -Expected 'spaced-value' -Actual $values['SPACED']
    Assert-Equal -Name 'keeps a value containing = and :' `
        -Expected 'http://127.0.0.1:8000/health?a=b' -Actual $values['URL']

    Assert-That -Name 'skips an empty value' -Condition (-not $values.ContainsKey('EMPTY'))
    Assert-That -Name 'skips a line with no =' -Condition (-not $values.ContainsKey('NO_EQUALS_SIGN'))
    Assert-That -Name 'skips a name starting with a digit' -Condition (-not $values.ContainsKey('1INVALID'))
    Assert-That -Name 'skips a name containing a dash' -Condition (-not $values.ContainsKey('HAS-DASH'))
    Assert-That -Name 'skips a comment' -Condition (-not $values.ContainsKey('# a comment'))
    # Exactly the six well-formed lines above: ANTHROPIC_API_KEY,
    # QUOTED_DOUBLE, QUOTED_SINGLE, EXPORTED, SPACED, URL. Everything else in
    # the fixture is malformed and must be dropped rather than half-read.
    Assert-Equal -Name 'accepts exactly the valid names, and nothing else' `
        -Expected 6 -Actual $values.Count

    # A missing file is a condition to report, not an exception to throw.
    $missing = Read-DotEnvFile -Path (Join-Path $temp 'does-not-exist.env')
    Assert-Equal -Name 'a missing file yields an empty result' -Expected 0 -Actual $missing.Count

    # --- the redaction summary --------------------------------------------
    Write-Host ""
    Write-Host "Get-DotEnvSummary" -ForegroundColor Cyan

    $summary = Get-DotEnvSummary -Values $values
    $rendered = ($summary | Out-String)

    Assert-That -Name 'summary names the variable' -Condition ($rendered -match 'ANTHROPIC_API_KEY')
    Assert-That -Name 'summary reports the length' `
        -Condition ($rendered -match [string]'sk-test-not-a-real-key'.Length)
    # The whole point: no summary may ever carry a value.
    Assert-That -Name 'summary never contains the secret value' `
        -Condition ($rendered -notmatch 'sk-test-not-a-real-key')
    Assert-That -Name 'summary never contains any loaded value' `
        -Condition ($rendered -notmatch 'exported-value' -and $rendered -notmatch 'double quoted')
    Assert-Equal -Name 'summary is sorted by name' `
        -Expected 'ANTHROPIC_API_KEY' -Actual $summary[0].Name

    $emptySummary = Get-DotEnvSummary -Values @{}
    Assert-Equal -Name 'an empty set summarises to nothing' -Expected 0 -Actual @($emptySummary).Count
} finally {
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}

# --- repository discovery ---------------------------------------------------

Write-Host ""
Write-Host "Get-RepoRoot" -ForegroundColor Cyan

$repoRoot = Get-RepoRoot -StartAt $PSScriptRoot
Assert-That -Name 'finds the repository root' -Condition (-not [string]::IsNullOrEmpty($repoRoot))
if (-not [string]::IsNullOrEmpty($repoRoot)) {
    Assert-That -Name 'the root holds apps\api' -Condition (Test-Path -LiteralPath (Join-Path $repoRoot 'apps\api'))
    Assert-That -Name 'the root holds apps\web' -Condition (Test-Path -LiteralPath (Join-Path $repoRoot 'apps\web'))
    Assert-That -Name 'the root holds packages\cad-core' -Condition (Test-Path -LiteralPath (Join-Path $repoRoot 'packages\cad-core'))
    Assert-That -Name 'the root holds start.ps1' -Condition (Test-Path -LiteralPath (Join-Path $repoRoot 'start.ps1'))
    Assert-That -Name 'the root holds stop.ps1' -Condition (Test-Path -LiteralPath (Join-Path $repoRoot 'stop.ps1'))

    # Found from a nested directory too, which is why the walk exists.
    $nested = Get-RepoRoot -StartAt (Join-Path $repoRoot 'apps\web\src')
    Assert-Equal -Name 'finds the root from a nested directory' -Expected $repoRoot -Actual $nested
}

$unrelated = Get-RepoRoot -StartAt ([System.IO.Path]::GetTempPath())
Assert-That -Name 'returns nothing outside a repository' -Condition ([string]::IsNullOrEmpty($unrelated))

# --- state round-trip -------------------------------------------------------

Write-Host ""
Write-Host "Dev state" -ForegroundColor Cyan

$temp = New-TempDirectory
try {
    Assert-That -Name 'no state before anything is written' `
        -Condition ($null -eq (Read-DevState -RepoRoot $temp))

    $written = Write-DevState -RepoRoot $temp -State @{
        repoRoot     = $temp
        backendPid   = 4242
        backendPort  = 8000
        frontendPid  = 4343
        frontendPort = 5173
        frontendUrl  = 'http://localhost:5173/'
    }
    Assert-That -Name 'the state file is created' -Condition (Test-Path -LiteralPath $written)
    Assert-That -Name 'the state file lives under .dev' -Condition ($written -match '\.dev')

    $read = Read-DevState -RepoRoot $temp
    Assert-Equal -Name 'the backend pid round-trips' -Expected 4242 -Actual $read.backendPid
    Assert-Equal -Name 'the frontend port round-trips' -Expected 5173 -Actual $read.frontendPort
    Assert-Equal -Name 'the frontend url round-trips' -Expected 'http://localhost:5173/' -Actual $read.frontendUrl

    # The state file is committed-adjacent runtime data; it must never carry a
    # credential, so assert on its literal bytes.
    $raw = Get-Content -LiteralPath $written -Raw
    Assert-That -Name 'the state file holds no API key field' `
        -Condition ($raw -notmatch 'API_KEY' -and $raw -notmatch 'sk-')

    Remove-DevState -RepoRoot $temp
    Assert-That -Name 'the state can be forgotten' `
        -Condition ($null -eq (Read-DevState -RepoRoot $temp))

    # A corrupt file is a "no state" answer, not a crash: a launcher must not
    # be wedged by a half-written file.
    Get-StateDirectory -RepoRoot $temp -Create | Out-Null
    Set-Content -LiteralPath (Get-StateFilePath -RepoRoot $temp) -Value '{not json' -Encoding UTF8
    Assert-That -Name 'corrupt state reads as no state' `
        -Condition ($null -eq (Read-DevState -RepoRoot $temp))
} finally {
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}

# --- process identity -------------------------------------------------------

Write-Host ""
Write-Host "Test-RecordedProcess" -ForegroundColor Cyan

$self = Get-Process -Id $PID
$selfStart = $self.StartTime.ToString('o')

$match = Test-RecordedProcess -Id $PID -StartTime $selfStart
Assert-That -Name 'recognises a live process by id and start time' `
    -Condition ($match.Running -and $match.Verified)

# The guard that matters: a recycled id must not be mistaken for ours.
$mismatch = Test-RecordedProcess -Id $PID -StartTime ((Get-Date).AddDays(-30).ToString('o'))
Assert-That -Name 'a wrong start time is not verified' `
    -Condition ($mismatch.Running -and -not $mismatch.Verified)

$noStart = Test-RecordedProcess -Id $PID -StartTime ''
Assert-That -Name 'a missing start time reports running but unverified' `
    -Condition ($noStart.Running -and -not $noStart.Verified)

$absent = Test-RecordedProcess -Id 999999 -StartTime $selfStart
Assert-That -Name 'an absent process is not running' -Condition (-not $absent.Running)

$zero = Test-RecordedProcess -Id 0 -StartTime ''
Assert-That -Name 'pid 0 is never a target' -Condition (-not $zero.Running)

# --- port probing -----------------------------------------------------------

Write-Host ""
Write-Host "Ports and health" -ForegroundColor Cyan

# Bind a real listener on an OS-assigned port, so the probe is tested against
# a port that is genuinely occupied rather than a guess.
$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$boundPort = $listener.LocalEndpoint.Port
try {
    Assert-That -Name 'detects a port that is listening' `
        -Condition (Test-TcpPortListening -Port $boundPort)
} finally {
    $listener.Stop()
}

Assert-That -Name 'detects a port that is free once the listener stops' `
    -Condition (-not (Test-TcpPortListening -Port $boundPort -TimeoutMilliseconds 500))

# The regression test for the duplicate-server bug. Vite binds ::1 only, and
# an IPv4-only probe called its port free, so a second Vite was started. An
# IPv6-only listener must be seen as occupying the port.
$listener6 = $null
try {
    $listener6 = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::IPv6Loopback, 0)
    $listener6.Start()
} catch {
    $listener6 = $null
}
if ($null -eq $listener6) {
    Write-Host "  skip IPv6 listener test (no IPv6 loopback on this machine)" -ForegroundColor DarkGray
} else {
    $boundPort6 = $listener6.LocalEndpoint.Port
    try {
        Assert-That -Name 'detects an IPv6-only listener (as Vite binds)' `
            -Condition (Test-TcpPortListening -Port $boundPort6) `
            -Detail "an IPv4-only probe would miss this and start a duplicate server"
    } finally {
        $listener6.Stop()
    }
}

Assert-That -Name 'an unreachable url is not healthy' `
    -Condition (-not (Test-HttpOk -Url "http://127.0.0.1:$boundPort/health" -TimeoutSeconds 2))

$waited = Wait-ForHttpOk -Url "http://127.0.0.1:$boundPort/health" -TimeoutSeconds 1 -PollMilliseconds 200
Assert-That -Name 'waiting for a dead url reports a timeout' `
    -Condition ((-not $waited.Ready) -and $waited.Reason -eq 'timeout')

$waitedExit = Wait-ForHttpOk -Url "http://127.0.0.1:$boundPort/health" -TimeoutSeconds 20 -ProcessId 999999
Assert-That -Name 'waiting stops early when the process is gone' `
    -Condition ((-not $waitedExit.Ready) -and $waitedExit.Reason -eq 'exited')

# --- toolchain discovery ----------------------------------------------------

Write-Host ""
Write-Host "Toolchain" -ForegroundColor Cyan

if (-not [string]::IsNullOrEmpty($repoRoot)) {
    $python = Resolve-VenvPython -RepoRoot $repoRoot
    Assert-That -Name 'finds the interpreter in .venv' -Condition (-not [string]::IsNullOrEmpty($python))
    if (-not [string]::IsNullOrEmpty($python)) {
        Assert-That -Name 'the interpreter exists on disk' -Condition (Test-Path -LiteralPath $python)
    }
    $none = Resolve-VenvPython -RepoRoot ([System.IO.Path]::GetTempPath())
    Assert-That -Name 'reports no interpreter when there is no .venv' -Condition ([string]::IsNullOrEmpty($none))
}

$npm = Resolve-NpmCommand
Assert-That -Name 'finds npm even when it is not on PATH' -Condition (-not [string]::IsNullOrEmpty($npm))

$names = Get-CredentialVariableNames
Assert-That -Name 'knows the Anthropic credential name' -Condition ($names -contains 'ANTHROPIC_API_KEY')
Assert-That -Name 'knows the Gemini credential name' -Condition ($names -contains 'GEMINI_API_KEY')

# --- the launcher scripts themselves ---------------------------------------

Write-Host ""
Write-Host "Launcher scripts" -ForegroundColor Cyan

if (-not [string]::IsNullOrEmpty($repoRoot)) {
    foreach ($name in @('start.ps1', 'stop.ps1', 'scripts\DevServers.psm1')) {
        $path = Join-Path $repoRoot $name
        $errors = $null
        $tokens = $null
        [System.Management.Automation.Language.Parser]::ParseFile($path, [ref] $tokens, [ref] $errors) | Out-Null
        $count = 0
        if ($null -ne $errors) { $count = $errors.Count }
        Assert-Equal -Name "$name parses without syntax errors" -Expected 0 -Actual $count
    }

    # The architecture must not drift: the key belongs in the backend
    # process's environment, and the frontend must never be handed one.
    $startText = Get-Content -LiteralPath (Join-Path $repoRoot 'start.ps1') -Raw
    Assert-That -Name 'start.ps1 contains no literal credential' `
        -Condition ($startText -notmatch 'sk-ant' -and $startText -notmatch 'AIza')
    Assert-That -Name 'start.ps1 removes injected variables after spawning' `
        -Condition ($startText -match 'Remove-Item -Path "Env:\\\$name"')
    Assert-That -Name 'start.ps1 starts the existing ASGI factory unchanged' `
        -Condition ($startText -match 'cad_api\.app:app_from_environment')
    Assert-That -Name 'start.ps1 uses --strictPort so the printed URL is the real one' `
        -Condition ($startText -match '--strictPort')

    # .dev must stay out of git, since it holds pids, logs and the cache.
    $gitignore = Get-Content -LiteralPath (Join-Path $repoRoot '.gitignore') -Raw
    Assert-That -Name '.gitignore excludes the .dev state directory' `
        -Condition ($gitignore -match '(?m)^\.dev/')
    Assert-That -Name '.gitignore still excludes .env' `
        -Condition ($gitignore -match '(?m)^\.env')
}

# --- report -----------------------------------------------------------------

Write-Host ""
if ($script:Failed -eq 0) {
    Write-Host "OK - $($script:Passed) assertions passed" -ForegroundColor Green
    Write-Host ""
    exit 0
}

Write-Host "FAILED - $($script:Failed) of $($script:Passed + $script:Failed) assertions failed" -ForegroundColor Red
foreach ($failure in $script:Failures) { Write-Host "  - $failure" -ForegroundColor Red }
Write-Host ""
exit 1
