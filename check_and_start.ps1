$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskGuard = Join-Path $taskRoot 'phishlens_guard.py'
$taskRuntime = Join-Path $taskRoot '.maintenance\runtime'

try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $taskRoot 'tools\manage_project.ps1') -Action Start -Service Api
    if ($LASTEXITCODE -ne 0) { throw 'The analysis service could not be started.' }
    $taskExisting = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine.IndexOf(('"' + $taskGuard + '"'), [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    if ($taskExisting.Count -gt 0) {
        Write-Output 'The floating guard is already running. Check the top-right corner of your screen.'
        exit 0
    }
    New-Item -ItemType Directory -Path $taskRuntime -Force | Out-Null
    $taskProcess = Start-Process -FilePath $taskPython -ArgumentList @('"' + $taskGuard + '"') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRuntime 'guard.stdout.log') -RedirectStandardError (Join-Path $taskRuntime 'guard.stderr.log')
    Start-Sleep -Milliseconds 800
    $taskProcess.Refresh()
    if ($taskProcess.HasExited) { throw 'The floating guard exited. See .maintenance\runtime\guard.stderr.log.' }
    Write-Output 'Floating guard opened. Click it to start or pause; right-click it to exit.'
    Write-Output 'Screenshot-only mode cannot verify a website address. Use the Edge extension for browser checks.'
}
catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
