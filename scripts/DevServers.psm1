<#
.SYNOPSIS
    Shared logic for the local development launcher (start.ps1 / stop.ps1).

.DESCRIPTION
    The launcher's job is to remove setup steps, not to change how the
    application works. Every decision the application already owns stays with
    the application:

      * the backend is still the same ASGI app started through the same
        `uvicorn --factory cad_api.app:app_from_environment`;
      * the frontend is still the same Vite dev server, still proxying `/api`
        to the backend, so the browser still sees one origin;
      * `cad_ai.config` still reads its credential from an environment
        variable and nothing else. **This module loads `apps/api/.env`; the
        application never does.** That keeps the documented invariant that no
        `.env` file is read or written by the application itself.

    Credential handling has one rule: the value is put into the *backend
    process's* environment and nowhere else. It is never written to the state
    file, never passed as a command-line argument (arguments are visible to
    other processes), never given to the Vite process, and never printed. Only
    variable *names* and value *lengths* are ever reported.

    Written for Windows PowerShell 5.1, which has no ternary operator, no
    null-coalescing operator and no `&&`/`||` chaining.
#>

# Strict mode is deliberately *not* set here. A launcher runs on whatever
# machine it is handed, and several of the probes below legitimately touch
# environment variables and WMI-backed properties that may be absent; under
# strict mode an absent one becomes an exception instead of a "no" answer,
# which would turn a recoverable condition into a crash.

#: Where per-developer runtime state lives, relative to the repository root.
#: Gitignored: it holds process ids, logs and the build cache, none of which
#: are source. No credential is ever written here.
$script:StateDirectoryName = '.dev'
$script:StateFileName = 'dev-servers.json'

#: The credential variables the application knows about, used only to report
#: whether one is present. The values are never read here.
$script:CredentialVariableNames = @('ANTHROPIC_API_KEY', 'GEMINI_API_KEY')


function Get-RepoRoot {
    <#
    .SYNOPSIS
        The repository root, found from this module's own location.
    .DESCRIPTION
        Walks upward looking for the markers this repository actually has, so
        the launcher works no matter which directory it is invoked from. Falls
        back to the parent of `scripts/` when no marker is found, which is
        where this module lives.
    #>
    [CmdletBinding()]
    param(
        [string] $StartAt
    )

    if ([string]::IsNullOrWhiteSpace($StartAt)) {
        $StartAt = $PSScriptRoot
    }

    $current = $null
    try {
        $current = (Resolve-Path -LiteralPath $StartAt -ErrorAction Stop).Path
    } catch {
        return $null
    }

    while (-not [string]::IsNullOrEmpty($current)) {
        $hasApi = Test-Path -LiteralPath (Join-Path $current 'apps\api')
        $hasWeb = Test-Path -LiteralPath (Join-Path $current 'apps\web')
        $hasCore = Test-Path -LiteralPath (Join-Path $current 'packages\cad-core')
        if ($hasApi -and $hasWeb -and $hasCore) {
            return $current
        }
        $parent = Split-Path -Path $current -Parent
        if ($parent -eq $current) {
            break
        }
        $current = $parent
    }

    return $null
}


function Read-DotEnvFile {
    <#
    .SYNOPSIS
        Parse a `.env` file into a hashtable of name -> value.
    .DESCRIPTION
        Deliberately small and strict. Accepts `NAME=VALUE` lines, tolerates a
        leading `export `, skips blank lines and `#` comments, strips one
        layer of matching single or double quotes, and ignores a line whose
        name is not a valid environment-variable name or whose value is empty.

        A UTF-8 byte-order mark is tolerated, because a file written by
        `Out-File -Encoding utf8` on Windows PowerShell has one.

        Returns an empty hashtable when the file does not exist, so a missing
        `.env` is a reportable condition rather than an exception.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    $values = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $values
    }

    $lines = @()
    try {
        $lines = [System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)
    } catch {
        return $values
    }

    foreach ($rawLine in $lines) {
        $line = $rawLine
        # Strip a byte-order mark that survived onto the first line.
        $line = $line -replace "^﻿", ''
        $line = $line.Trim()

        if ([string]::IsNullOrEmpty($line)) { continue }
        if ($line.StartsWith('#')) { continue }
        if ($line -match '^export\s+') { $line = $line -replace '^export\s+', '' }

        $separator = $line.IndexOf('=')
        if ($separator -lt 1) { continue }

        $name = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()

        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { continue }

        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        if ([string]::IsNullOrEmpty($value)) { continue }

        $values[$name] = $value
    }

    return $values
}


function Get-DotEnvSummary {
    <#
    .SYNOPSIS
        A printable description of loaded variables that cannot leak a value.
    .DESCRIPTION
        Returns one object per variable carrying the name and the value's
        character length only. This is the *only* shape in which loaded
        `.env` contents are ever shown to a human or written to a log.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Values
    )

    $summary = @()
    foreach ($name in ($Values.Keys | Sort-Object)) {
        $summary += [pscustomobject] @{
            Name   = $name
            Length = ([string] $Values[$name]).Length
        }
    }
    return $summary
}


function Test-TcpPortListening {
    <#
    .SYNOPSIS
        Whether something accepts TCP connections on a local port.
    .DESCRIPTION
        Address family matters here, and getting it wrong causes exactly the
        failure this launcher must not have. Measured on this project: Uvicorn
        binds `127.0.0.1` (IPv4) while Vite binds `::1` (IPv6) only. A probe
        that checks IPv4 alone reports Vite's port as free, and the launcher
        then starts a second Vite -- a duplicate server, which is the thing
        it exists to prevent.

        So two independent signals are used, and either one is enough:

        1. the kernel's listening-socket table, which sees a socket bound to
           any address family; and
        2. an actual connection attempt to IPv4 *and* IPv6 loopback, with the
           socket created for the matching family -- `New-Object TcpClient`
           with no argument creates an **IPv4** socket, so connecting it to
           `::1` can never succeed.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [int] $Port,

        [int] $TimeoutMilliseconds = 1000
    )

    # The listening table answers for every address family at once.
    try {
        $entries = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
        if ($entries.Count -gt 0) {
            return $true
        }
    } catch {
        # Not available on this machine; the connection probe below decides.
    }

    $addresses = @(
        [System.Net.IPAddress]::Loopback,
        [System.Net.IPAddress]::IPv6Loopback
    )

    foreach ($address in $addresses) {
        $client = $null
        try {
            # The family is taken from the address, not left to the default.
            $client = New-Object System.Net.Sockets.TcpClient($address.AddressFamily)
            $async = $client.BeginConnect($address, $Port, $null, $null)
            $completed = $async.AsyncWaitHandle.WaitOne($TimeoutMilliseconds, $false)
            if ($completed -and $client.Connected) {
                $client.EndConnect($async)
                return $true
            }
        } catch {
            # An unreachable address family is not an answer; try the next.
        } finally {
            if ($null -ne $client) { $client.Close() }
        }
    }

    return $false
}


function Get-PortListenerPid {
    <#
    .SYNOPSIS
        Best-effort process id of whatever is listening on a port.
    .DESCRIPTION
        Used only to make error messages specific ("port 8000 is held by
        pid 1234 (node)"). Never used to decide anything, because a port can
        be held by a process this user cannot see.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [int] $Port
    )

    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        foreach ($connection in $connections) {
            if ($connection.OwningProcess) {
                return [int] $connection.OwningProcess
            }
        }
    } catch {
        return $null
    }
    return $null
}


function Get-ProcessDescription {
    <#
    .SYNOPSIS
        A short "pid 1234 (node)" description, or "pid 1234" if unknown.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [int] $Id
    )

    try {
        $process = Get-Process -Id $Id -ErrorAction Stop
        return "pid $Id ($($process.ProcessName))"
    } catch {
        return "pid $Id"
    }
}


function Test-HttpOk {
    <#
    .SYNOPSIS
        Whether a URL answers with a 2xx status.
    .DESCRIPTION
        `-UseBasicParsing` keeps this off the legacy Internet Explorer
        parsing engine, which is not present on every Windows install.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Url,

        [int] $TimeoutSeconds = 5
    )

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing `
            -TimeoutSec $TimeoutSeconds -ErrorAction Stop
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300)
    } catch {
        return $false
    }
}


function Wait-ForHttpOk {
    <#
    .SYNOPSIS
        Poll a URL until it answers, a process dies, or the deadline passes.
    .DESCRIPTION
        Returns an object with `Ready` and `Reason`. When `ProcessId` is
        given, a process that has exited ends the wait immediately rather
        than burning the whole timeout -- a server that crashed on startup
        should be reported in seconds, not after a minute.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $Url,

        [int] $TimeoutSeconds = 60,

        [int] $PollMilliseconds = 500,

        [int] $ProcessId = 0
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk -Url $Url -TimeoutSeconds 3) {
            return [pscustomobject] @{ Ready = $true; Reason = 'ready' }
        }

        if ($ProcessId -gt 0) {
            $alive = $true
            try {
                $process = Get-Process -Id $ProcessId -ErrorAction Stop
                if ($process.HasExited) { $alive = $false }
            } catch {
                $alive = $false
            }
            if (-not $alive) {
                return [pscustomobject] @{ Ready = $false; Reason = 'exited' }
            }
        }

        Start-Sleep -Milliseconds $PollMilliseconds
    }

    return [pscustomobject] @{ Ready = $false; Reason = 'timeout' }
}


function Resolve-VenvPython {
    <#
    .SYNOPSIS
        The interpreter inside the repository's existing `.venv`.
    .DESCRIPTION
        Returns `$null` when there is no virtual environment, so the caller
        can print an actionable message instead of failing obscurely. The
        launcher never creates or modifies the environment.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $RepoRoot
    )

    foreach ($relative in @('.venv\Scripts\python.exe', '.venv\bin\python')) {
        $candidate = Join-Path $RepoRoot $relative
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}


function Resolve-NpmCommand {
    <#
    .SYNOPSIS
        The `npm` launcher, from PATH or a standard Node installation.
    .DESCRIPTION
        PATH is checked first. It is not always enough: a non-interactive
        shell on this machine does not have Node on PATH even though Node is
        installed, so the usual install locations are checked as well.
    #>
    [CmdletBinding()]
    param()

    $command = Get-Command -Name 'npm.cmd' -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        $command = Get-Command -Name 'npm' -CommandType Application -ErrorAction SilentlyContinue
    }
    if ($null -ne $command) {
        return $command.Source
    }

    # Each base is checked before joining: on some machines one of these
    # environment variables is not set at all, and joining onto an empty base
    # would throw rather than simply not match.
    $bases = @(
        @{ Base = $env:ProgramFiles;            Leaf = 'nodejs\npm.cmd' },
        @{ Base = ${env:ProgramFiles(x86)};     Leaf = 'nodejs\npm.cmd' },
        @{ Base = $env:LOCALAPPDATA;            Leaf = 'Programs\nodejs\npm.cmd' },
        @{ Base = $env:APPDATA;                 Leaf = 'npm\npm.cmd' }
    )
    foreach ($entry in $bases) {
        if ([string]::IsNullOrWhiteSpace($entry.Base)) { continue }
        $candidate = Join-Path $entry.Base $entry.Leaf
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return $null
}


function Resolve-NodeCommand {
    <#
    .SYNOPSIS
        The `node` executable, from PATH or a standard installation.
    .DESCRIPTION
        Needed separately from `npm`: `npm.cmd` is a shim that shells out to
        `node`, so finding `npm` is not enough. On a machine where Node is
        installed but not on PATH, running `npm.cmd` fails with
        `'"node"' is not recognized as an internal or external command` --
        measured, not hypothetical. The caller puts this executable's
        directory on the PATH it hands to the Vite process.

        Looked up independently of `npm` because a globally installed `npm`
        shim under `%APPDATA%\npm` does not sit next to `node.exe`.
    #>
    [CmdletBinding()]
    param()

    $command = Get-Command -Name 'node.exe' -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        $command = Get-Command -Name 'node' -CommandType Application -ErrorAction SilentlyContinue
    }
    if ($null -ne $command) {
        return $command.Source
    }

    $bases = @(
        @{ Base = $env:ProgramFiles;        Leaf = 'nodejs\node.exe' },
        @{ Base = ${env:ProgramFiles(x86)}; Leaf = 'nodejs\node.exe' },
        @{ Base = $env:LOCALAPPDATA;        Leaf = 'Programs\nodejs\node.exe' }
    )
    foreach ($entry in $bases) {
        if ([string]::IsNullOrWhiteSpace($entry.Base)) { continue }
        $candidate = Join-Path $entry.Base $entry.Leaf
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return $null
}


function Get-StateDirectory {
    <#
    .SYNOPSIS
        The gitignored directory holding runtime state, created on demand.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $RepoRoot,

        [switch] $Create
    )

    $directory = Join-Path $RepoRoot $script:StateDirectoryName
    if ($Create -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    return $directory
}


function Get-StateFilePath {
    <#
    .SYNOPSIS
        Path of the file recording what start.ps1 started.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $RepoRoot
    )

    return (Join-Path (Get-StateDirectory -RepoRoot $RepoRoot) $script:StateFileName)
}


function Read-DevState {
    <#
    .SYNOPSIS
        The recorded state, or `$null` when nothing was recorded.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $RepoRoot
    )

    $path = Get-StateFilePath -RepoRoot $RepoRoot
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return $null
    }
    try {
        $text = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
        if ([string]::IsNullOrWhiteSpace($text)) { return $null }
        return ($text | ConvertFrom-Json)
    } catch {
        return $null
    }
}


function Write-DevState {
    <#
    .SYNOPSIS
        Record what was started, so stop.ps1 can stop exactly that.
    .DESCRIPTION
        Process start times are recorded alongside process ids. Windows
        reuses process ids, so a stale id on its own is not safe to kill; the
        pair identifies the process. **No credential is written here** -- the
        state carries ports, ids, times and paths only.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $RepoRoot,

        [Parameter(Mandatory = $true)]
        [hashtable] $State
    )

    Get-StateDirectory -RepoRoot $RepoRoot -Create | Out-Null
    $path = Get-StateFilePath -RepoRoot $RepoRoot
    $json = $State | ConvertTo-Json -Depth 6
    [System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
    return $path
}


function Remove-DevState {
    <#
    .SYNOPSIS
        Forget the recorded state.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $RepoRoot
    )

    $path = Get-StateFilePath -RepoRoot $RepoRoot
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }
}


function Test-RecordedProcess {
    <#
    .SYNOPSIS
        Whether a recorded (id, start time) pair is still that same process.
    .DESCRIPTION
        Guards against process-id reuse. When no start time was recorded the
        check falls back to existence alone, and says so through
        `Verified = $false`, so a caller can be cautious about killing it.
    #>
    [CmdletBinding()]
    param(
        [int] $Id,

        [string] $StartTime
    )

    if ($Id -le 0) {
        return [pscustomobject] @{ Running = $false; Verified = $false; Name = $null }
    }

    $process = $null
    try {
        $process = Get-Process -Id $Id -ErrorAction Stop
    } catch {
        return [pscustomobject] @{ Running = $false; Verified = $false; Name = $null }
    }

    if ([string]::IsNullOrWhiteSpace($StartTime)) {
        return [pscustomobject] @{ Running = $true; Verified = $false; Name = $process.ProcessName }
    }

    $recorded = [datetime]::MinValue
    $parsed = [datetime]::TryParse(
        $StartTime, [ref] $recorded
    )
    if (-not $parsed) {
        return [pscustomobject] @{ Running = $true; Verified = $false; Name = $process.ProcessName }
    }

    $actual = $null
    try {
        $actual = $process.StartTime
    } catch {
        return [pscustomobject] @{ Running = $true; Verified = $false; Name = $process.ProcessName }
    }

    $sameProcess = ([math]::Abs(($actual - $recorded).TotalSeconds) -lt 2)
    return [pscustomobject] @{
        Running  = $true
        Verified = $sameProcess
        Name     = $process.ProcessName
    }
}


function Stop-ProcessTree {
    <#
    .SYNOPSIS
        Stop a process and everything it started.
    .DESCRIPTION
        `taskkill /T` is used because both servers spawn children that must
        go with them: Uvicorn runs CAD builds in child processes, and Vite
        spawns its own workers. Stopping only the parent would leave those
        holding the port. Falls back to `Stop-Process` if `taskkill` is
        unavailable.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [int] $Id
    )

    # Already gone is a success, not a failure -- and asking taskkill to kill
    # a process that has exited prints a confusing "not found" error.
    try {
        Get-Process -Id $Id -ErrorAction Stop | Out-Null
    } catch {
        return $true
    }

    $taskkill = Get-Command -Name 'taskkill.exe' -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $taskkill) {
        & $taskkill.Source '/PID' "$Id" '/T' '/F' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
    }

    try {
        Stop-Process -Id $Id -Force -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}


function Get-CredentialVariableNames {
    <#
    .SYNOPSIS
        The credential variable names the application recognises.
    #>
    [CmdletBinding()]
    param()
    return $script:CredentialVariableNames
}


Export-ModuleMember -Function @(
    'Get-RepoRoot',
    'Read-DotEnvFile',
    'Get-DotEnvSummary',
    'Test-TcpPortListening',
    'Get-PortListenerPid',
    'Get-ProcessDescription',
    'Test-HttpOk',
    'Wait-ForHttpOk',
    'Resolve-VenvPython',
    'Resolve-NpmCommand',
    'Resolve-NodeCommand',
    'Get-StateDirectory',
    'Get-StateFilePath',
    'Read-DevState',
    'Write-DevState',
    'Remove-DevState',
    'Test-RecordedProcess',
    'Stop-ProcessTree',
    'Get-CredentialVariableNames'
)
