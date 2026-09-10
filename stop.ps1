<#
.SYNOPSIS
    Stop the servers that start.ps1 started.

.DESCRIPTION
    Reads `.dev\dev-servers.json` and stops exactly what is recorded there,
    then confirms both ports are free.

    Two details make this safe rather than merely convenient:

      * **Process trees, not processes.** Both servers have children -- the
        backend runs CAD builds in child processes, and `npm` runs Vite as a
        child -- so stopping only the parent would leave a child holding the
        port. `taskkill /T` is used.
      * **Identity, not just an id.** Windows reuses process ids. Each id was
        recorded together with its start time, and a process whose start time
        no longer matches is left alone and reported, rather than killed on
        the strength of a stale number.

    By default nothing that this project did not start is touched. Anything
    else still holding a port is reported so you can decide; `-Force` will
    stop it.

.PARAMETER Force
    Also stop a process that is holding one of the recorded ports but was not
    started by start.ps1.

.EXAMPLE
    .\stop.ps1

.EXAMPLE
    .\stop.ps1 -Force
#>

[CmdletBinding()]
param(
    [switch] $Force
)

$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'scripts\DevServers.psm1') -Force

function Write-Step   ($Text) { Write-Host "  $Text" }
function Write-Good   ($Text) { Write-Host "  $Text" -ForegroundColor Green }
function Write-Warn   ($Text) { Write-Host "  $Text" -ForegroundColor Yellow }
function Write-Bad    ($Text) { Write-Host "  $Text" -ForegroundColor Red }
function Write-Heading($Text) { Write-Host ""; Write-Host $Text -ForegroundColor Cyan }

Write-Host ""
Write-Host "Text to CAD - stopping local development" -ForegroundColor White

$repoRoot = Get-RepoRoot -StartAt $PSScriptRoot
if ([string]::IsNullOrEmpty($repoRoot)) {
    Write-Host ""
    Write-Bad "Could not find the repository root from '$PSScriptRoot'."
    Write-Host ""
    exit 1
}

$state = Read-DevState -RepoRoot $repoRoot

Write-Heading "Stopping"

$stoppedAnything = $false

if ($null -eq $state) {
    Write-Warn "No record of servers started by start.ps1 (.dev\dev-servers.json is absent)."
} else {
    $targets = @(
        [pscustomobject] @{ Label = 'backend';  Id = $state.backendPid;  Start = $state.backendStart },
        [pscustomobject] @{ Label = 'frontend'; Id = $state.frontendPid; Start = $state.frontendStart }
    )

    foreach ($target in $targets) {
        $id = 0
        if ($null -ne $target.Id) { $id = [int] $target.Id }
        if ($id -le 0) {
            Write-Step "$($target.Label): nothing recorded"
            continue
        }

        $check = Test-RecordedProcess -Id $id -StartTime ([string] $target.Start)
        if (-not $check.Running) {
            Write-Step "$($target.Label): pid $id is not running (already stopped)"
            continue
        }

        if (-not $check.Verified) {
            # The id exists but is not provably the process we started.
            if (-not $Force) {
                Write-Warn "$($target.Label): pid $id is running but does not match what was recorded ($($check.Name))."
                Write-Host "      Leaving it alone. Use -Force to stop it anyway." -ForegroundColor DarkGray
                continue
            }
            Write-Warn "$($target.Label): pid $id does not match the record; stopping it because -Force was given."
        }

        if (Stop-ProcessTree -Id $id) {
            Write-Good "$($target.Label): stopped pid $id and its children"
            $stoppedAnything = $true
        } else {
            Write-Bad "$($target.Label): could not stop pid $id"
        }
    }
}

# --- confirm the ports are actually free -----------------------------------

Write-Heading "Ports"

$ports = @()
if ($null -ne $state) {
    if ($null -ne $state.backendPort)  { $ports += [pscustomobject] @{ Label = 'backend';  Port = [int] $state.backendPort;  Listener = $state.backendListenerPid } }
    if ($null -ne $state.frontendPort) { $ports += [pscustomobject] @{ Label = 'frontend'; Port = [int] $state.frontendPort; Listener = $state.frontendListenerPid } }
}
if ($ports.Count -eq 0) {
    $ports = @(
        [pscustomobject] @{ Label = 'backend';  Port = 8000; Listener = $null },
        [pscustomobject] @{ Label = 'frontend'; Port = 5173; Listener = $null }
    )
    Write-Step "no ports recorded; checking the defaults 8000 and 5173"
}

$leftOver = @()

foreach ($entry in $ports) {
    if (-not (Test-TcpPortListening -Port $entry.Port)) {
        Write-Good "$($entry.Label) port $($entry.Port) is free"
        continue
    }

    $owner = Get-PortListenerPid -Port $entry.Port
    if ($null -eq $owner) { $owner = 0 }

    # A child that outlived its parent -- an orphaned Vite process, say --
    # was still started by us, so it is ours to clean up.
    $wasOurs = $false
    if ($null -ne $entry.Listener -and [int] $entry.Listener -gt 0 -and [int] $entry.Listener -eq $owner) {
        $wasOurs = $true
    }

    if ($wasOurs -or $Force) {
        $reason = "it was started by start.ps1"
        if (-not $wasOurs) { $reason = "-Force was given" }
        Write-Step "$($entry.Label) port $($entry.Port) still held by $(Get-ProcessDescription -Id $owner); stopping it because $reason"
        if (Stop-ProcessTree -Id $owner) {
            Write-Good "$($entry.Label) port $($entry.Port) is now free"
            $stoppedAnything = $true
        } else {
            Write-Bad "$($entry.Label) port $($entry.Port) could not be freed"
            $leftOver += $entry
        }
    } else {
        Write-Warn "$($entry.Label) port $($entry.Port) is still held by $(Get-ProcessDescription -Id $owner), which start.ps1 did not start."
        Write-Host "      Leaving it alone. Use -Force to stop it as well." -ForegroundColor DarkGray
        $leftOver += $entry
    }
}

# --- forget the record ------------------------------------------------------

if ($leftOver.Count -eq 0) {
    Remove-DevState -RepoRoot $repoRoot
} else {
    Write-Host ""
    Write-Warn "Keeping .dev\dev-servers.json because something is still listening."
}

Write-Heading "Done"
if ($stoppedAnything) {
    Write-Good "local development servers stopped"
} else {
    Write-Step "nothing was running"
}
Write-Host ""
Write-Step "start again with:  .\start.ps1"
Write-Host ""
exit 0
