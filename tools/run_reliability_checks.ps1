$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Project Python is missing. Follow the README setup steps first.'
}
$taskNode = Get-Command node -ErrorAction Stop

Push-Location -LiteralPath $taskRoot
try {
    $taskPythonChecks = @'
import sys
import unittest

suite = unittest.defaultTestLoader.discover('tests', pattern='test_*.py')
if suite.countTestCases() == 0:
    raise SystemExit('No Python regression tests were found.')
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
'@
    $taskPythonChecks | & $taskPython -B -
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $taskJsTests = @(Get-ChildItem -LiteralPath (Join-Path $taskRoot 'tests') -Filter 'test_*.cjs' -File | ForEach-Object { $_.FullName })
    if ($taskJsTests.Count -eq 0) { throw 'No extension regression tests were found.' }
    & $taskNode.Source --test @taskJsTests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Output 'Reliability regression checks passed. Real-model/browser acceptance is a separate check.'
}
finally {
    Pop-Location
}
