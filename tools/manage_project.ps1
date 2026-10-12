[CmdletBinding()]
param(
    [ValidateSet('Start', 'Stop', 'Restart', 'Status')][string]$Action = 'Start',
    [ValidateSet('All', 'Api', 'Demo')][string]$Service = 'All',
    [ValidateRange(5, 120)][int]$ReadyTimeoutSeconds = 30,
    [ValidateRange(1024, 65535)][int]$ApiPort = 8765,
    [ValidateRange(1024, 65535)][int]$DemoPort = 8001,
    [switch]$OpenDemo
)

# Windows PowerShell 5.1 compatible. No administrator rights are required.
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimeDir = Join-Path $projectRoot '.maintenance\runtime'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath (Join-Path $projectRoot 'portable.json')) {
    $pythonPath = Join-Path $projectRoot 'runtime\python.exe'
    $env:TORCH_HOME = Join-Path $projectRoot 'runtime\model-cache'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    # A USB drive can move between computers and drive letters. Never reuse
    # another machine's PID records or write runtime state onto the USB drive.
    $taskHash = [Security.Cryptography.SHA256]::Create()
    try { $taskId = ([BitConverter]::ToString($taskHash.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant())))).Replace('-', '').Substring(0, 16) }
    finally { $taskHash.Dispose() }
    $runtimeDir = Join-Path $env:LOCALAPPDATA ('PhishLensPortable\' + $taskId)
    $env:PHISHLENS_EVENT_LOG = Join-Path $runtimeDir 'event.log'
}
$serverDir = Join-Path $projectRoot 'server'
$demoDir = Join-Path $projectRoot 'testset\fake'
$specs = @(
    [pscustomobject]@{
        Name = 'Api'; Port = $ApiPort; Marker = $serverDir; Module = '-m uvicorn app:app'
        Arguments = '-u -m uvicorn app:app --host 127.0.0.1 --port ' + $ApiPort + ' --app-dir "' + $serverDir + '"'
        Url = 'http://127.0.0.1:' + $ApiPort + '/health'
    },
    [pscustomobject]@{
        Name = 'Demo'; Port = $DemoPort; Marker = $demoDir; Module = '-m http.server'
        Arguments = '-u -m http.server ' + $DemoPort + ' --bind 127.0.0.1 --directory "' + $demoDir + '"'
        Url = 'http://127.0.0.1:' + $DemoPort + '/fake_alipay.html'
    }
)
if (Test-Path -LiteralPath (Join-Path $projectRoot 'portable.json')) {
    foreach ($taskSpec in $specs) { $taskSpec.Arguments = '-B ' + $taskSpec.Arguments }
}
if ($Service -ne 'All') { $specs = @($specs | Where-Object { $_.Name -eq $Service }) }

function Get-StatePath($Spec) { return Join-Path $runtimeDir ($Spec.Name.ToLowerInvariant() + '.json') }

function Get-LiveIdentity([int]$ProcessId) {
    $info = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
    if ($null -eq $info) { return $null }
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    try {
        return [pscustomobject]@{
            Pid = $ProcessId; CreatedUtc = $process.StartTime.ToUniversalTime().ToString('o')
            CommandLine = [string]$info.CommandLine; ParentPid = [int]$info.ParentProcessId
        }
    } finally { $process.Dispose() }
}

function Test-SameIdentity($Recorded, $Live) {
    return ($null -ne $Live -and [int]$Recorded.Pid -eq [int]$Live.Pid -and
        $Recorded.CreatedUtc -eq $Live.CreatedUtc -and $Recorded.CommandLine -ceq $Live.CommandLine)
}

function Test-ServiceCommand($Identity, $Spec) {
    return ($Identity.CommandLine -and
        $Identity.CommandLine.IndexOf($Spec.Marker, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $Identity.CommandLine.Contains($Spec.Module))
}

function Read-State($Spec) {
    $path = Get-StatePath $Spec
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $state = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    # PowerShell 7 parses ISO JSON dates as DateTime; 5.1 preserves strings.
    # Process identity comparisons use the original UTC round-trip format.
    foreach ($processRecord in @($state.Processes)) {
        if ($processRecord.CreatedUtc -is [datetime]) {
            $processRecord.CreatedUtc = $processRecord.CreatedUtc.ToUniversalTime().ToString('o')
        }
    }
    if ($state.ProjectRoot -ne $projectRoot -or $state.Service -ne $Spec.Name -or
        $state.LaunchArguments -cne $Spec.Arguments) {
        throw "Unrecognized process record: $path. No process was stopped."
    }
    return $state
}

function Save-State($Spec, $State) {
    $path = Get-StatePath $Spec
    $temporaryPath = $path + '.tmp'
    $State | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
    Move-Item -LiteralPath $temporaryPath -Destination $path -Force
}

function Get-OwnedIdentities($Spec, $State) {
    if ($null -eq $State) { return }
    foreach ($recorded in @($State.Processes)) {
        if (-not (Test-ServiceCommand $recorded $Spec)) { continue }
        $live = Get-LiveIdentity ([int]$recorded.Pid)
        if (Test-SameIdentity $recorded $live) { $live }
    }
}

function Get-Listeners($Spec) {
    # A port is evidence of occupancy only, never authorization to stop a PID.
    return @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object { $_.LocalPort -eq $Spec.Port })
}

function Assert-NoUnknownListener($Spec, $State) {
    $ownedIds = @(Get-OwnedIdentities $Spec $State | ForEach-Object { [int]$_.Pid })
    foreach ($listener in @(Get-Listeners $Spec)) {
        if ([int]$listener.OwningProcess -notin $ownedIds) {
            throw "Port $($Spec.Port) is occupied by unrecognized PID $($listener.OwningProcess). It was NOT stopped. Close that application yourself or inspect it before retrying."
        }
    }
}

function Update-ProcessFamily($Spec, $State) {
    # A Windows venv redirector can start a second python.exe. Follow only
    # descendants of identities we already own and record their full identity.
    $known = @(Get-OwnedIdentities $Spec $State)
    $knownIds = @($known | ForEach-Object { [int]$_.Pid })
    if ($knownIds.Count -eq 0) { return }
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    do {
        $added = $false
        foreach ($processInfo in $processes) {
            $childId = [int]$processInfo.ProcessId
            if ($childId -in $knownIds -or [int]$processInfo.ParentProcessId -notin $knownIds) { continue }
            $identity = Get-LiveIdentity $childId
            $parent = @($known | Where-Object { [int]$_.Pid -eq [int]$processInfo.ParentProcessId })[0]
            if ($null -ne $identity -and (Test-ServiceCommand $identity $Spec) -and
                [DateTime]::Parse($identity.CreatedUtc) -ge [DateTime]::Parse($parent.CreatedUtc)) {
                $State.Processes = @($State.Processes) + @($identity)
                $known += $identity
                $knownIds += $childId
                $added = $true
            }
        }
    } while ($added)
    Save-State $Spec $State
}

function Get-Readiness($Spec) {
    try {
        $response = Invoke-WebRequest -Uri $Spec.Url -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -ne 200) { return $null }
        if ($Spec.Name -eq 'Api') {
            $health = $response.Content | ConvertFrom-Json
            if ($health.status -ne 'ok') { return $null }
            return $health
        }
        return $true
    } catch { return $null }
}

function Wait-Ready($Spec, $State) {
    $deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    do {
        Update-ProcessFamily $Spec $State
        Assert-NoUnknownListener $Spec $State
        if (@(Get-OwnedIdentities $Spec $State).Count -eq 0) {
            throw "$($Spec.Name) exited before becoming ready. Check $runtimeDir\$($Spec.Name.ToLowerInvariant()).stderr.log"
        }
        $ready = Get-Readiness $Spec
        if ($null -ne $ready) { return $ready }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$($Spec.Name) did not become HTTP-ready within $ReadyTimeoutSeconds seconds. Check logs in $runtimeDir"
}

function Stop-ManagedService($Spec) {
    $state = Read-State $Spec
    if ($null -eq $state) {
        Write-Host "$($Spec.Name): no managed process record; nothing stopped."
        return
    }
    Update-ProcessFamily $Spec $state
    $recorded = @($state.Processes)
    for ($index = $recorded.Count - 1; $index -ge 0; $index--) {
        $identity = $recorded[$index]
        if (-not (Test-ServiceCommand $identity $Spec)) { continue }
        $live = Get-LiveIdentity ([int]$identity.Pid)
        if (-not (Test-SameIdentity $identity $live)) { continue }
        $process = Get-Process -Id ([int]$identity.Pid) -ErrorAction SilentlyContinue
        if ($null -eq $process) { continue }
        try {
            # Hold the OS handle and recheck creation time against PID reuse.
            $null = $process.Handle
            if ($process.StartTime.ToUniversalTime().ToString('o') -ne $identity.CreatedUtc) { continue }
            Stop-Process -InputObject $process -Force -ErrorAction Stop
            if (-not $process.WaitForExit(5000)) { throw "PID $($identity.Pid) did not exit." }
            Write-Host "$($Spec.Name): stopped owned PID $($identity.Pid)."
        } finally { $process.Dispose() }
    }
    Remove-Item -LiteralPath (Get-StatePath $Spec) -Force
}

function Start-ManagedService($Spec) {
    $state = Read-State $Spec
    Assert-NoUnknownListener $Spec $state
    if (@(Get-OwnedIdentities $Spec $state).Count -gt 0) {
        $null = Wait-Ready $Spec $state
        Write-Host "$($Spec.Name): already running and HTTP-ready."
        return $false
    }
    $stem = Join-Path $runtimeDir $Spec.Name.ToLowerInvariant()
    Write-Host "Starting $($Spec.Name) on 127.0.0.1:$($Spec.Port) ..."
    $process = Start-Process -FilePath $pythonPath -ArgumentList $Spec.Arguments `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput ($stem + '.stdout.log') -RedirectStandardError ($stem + '.stderr.log')
    try {
        $identity = Get-LiveIdentity $process.Id
        if ($null -eq $identity -or -not (Test-ServiceCommand $identity $Spec)) {
            throw "$($Spec.Name) did not provide a verifiable process identity. See $stem.stderr.log"
        }
        $state = [pscustomobject]@{
            ProjectRoot = $projectRoot; Service = $Spec.Name
            LaunchExecutable = $pythonPath; LaunchArguments = $Spec.Arguments
            Processes = @($identity)
        }
        Save-State $Spec $state
        try { $null = Wait-Ready $Spec $state }
        catch {
            $startupError = $_
            Stop-ManagedService $Spec
            throw $startupError
        }
    } finally { $process.Dispose() }
    Write-Host "$($Spec.Name): HTTP-ready."
    return $true
}

$managerLock = $null
$newServices = @()
try {
    $null = New-Item -ItemType Directory -Path $runtimeDir -Force
    try {
        $managerLock = [IO.File]::Open((Join-Path $runtimeDir 'manager.lock'),
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch { throw 'Another launcher operation is running. Wait for it to finish, then retry.' }
    if ($Action -in @('Start', 'Restart')) {
        if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
            throw "Project Python is missing: $pythonPath. Follow README setup; nothing was installed."
        }
        if ('Demo' -in @($specs | ForEach-Object { $_.Name }) -and
            -not (Test-Path -LiteralPath (Join-Path $demoDir 'fake_alipay.html') -PathType Leaf)) {
            throw "Demo page is missing: $demoDir\fake_alipay.html"
        }
        # Preflight all requested ports before changing a running service.
        foreach ($spec in $specs) { Assert-NoUnknownListener $spec (Read-State $spec) }
    }
    if ($Action -in @('Stop', 'Restart')) {
        foreach ($spec in $specs) { Stop-ManagedService $spec }
    }
    if ($Action -in @('Start', 'Restart')) {
        foreach ($spec in $specs) {
            if (Start-ManagedService $spec) { $newServices += $spec }
        }
        Write-Host ''
        foreach ($spec in $specs) { Write-Host "$($spec.Name): $($spec.Url)" }
        if ('Api' -in @($specs | ForEach-Object { $_.Name })) {
            Write-Host 'models_ready=false means visual models have not been loaded yet. Startup does not run them.'
        }
        Write-Host "Logs and process records: $runtimeDir"
        if ($OpenDemo -and 'Demo' -in @($specs | ForEach-Object { $_.Name })) {
            Start-Process -FilePath ('microsoft-edge:http://127.0.0.1:' + $DemoPort + '/fake_alipay.html') | Out-Null
        }
    }
    if ($Action -eq 'Status') {
        foreach ($spec in $specs) {
            $state = Read-State $spec
            Assert-NoUnknownListener $spec $state
            $owned = @(Get-OwnedIdentities $spec $state)
            if ($owned.Count -eq 0) { Write-Host "$($spec.Name): stopped."; continue }
            $ready = Get-Readiness $spec
            $description = if ($null -eq $ready) { 'running, HTTP not ready' } else { 'running, HTTP-ready' }
            Write-Host "$($spec.Name): $description; PIDs $(@($owned | ForEach-Object { $_.Pid }) -join ', ')."
            if ($spec.Name -eq 'Api' -and $null -ne $ready) {
                Write-Host "  models_ready=$($ready.models_ready); $($spec.Url)"
            }
        }
    }
    exit 0
} catch {
    $operationError = $_
    foreach ($spec in $newServices) {
        try { Stop-ManagedService $spec } catch { Write-Warning $_.Exception.Message }
    }
    Write-Host "ERROR: $($operationError.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    if ($null -ne $managerLock) { $managerLock.Dispose() }
}
