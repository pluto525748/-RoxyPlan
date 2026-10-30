# RoxyPlan isolated pytest runner.
# Uses <worktree>\.venv by default; -PythonExecutable points at another existing
# interpreter when the worktree has no .venv. Either way pytest always runs from
# this worktree's root, so the code under test, the test files, the run
# directory and every isolated data path stay inside this worktree.

[CmdletBinding()]
param(
    [switch]$KeepRun,

    [string]$PythonExecutable,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$workspaceRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRoot "test_workspace")
)
$runsRoot = [System.IO.Path]::GetFullPath((Join-Path $workspaceRoot "runs"))
$runId = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss-fff"), $PID
$runRoot = [System.IO.Path]::GetFullPath((Join-Path $runsRoot $runId))
$pytestRoot = Join-Path $runRoot "pytest"
$tempRoot = Join-Path $runRoot "temp"
$artifactsRoot = Join-Path $runRoot "artifacts"
$defaultPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

$useExplicitPython = $PSBoundParameters.ContainsKey("PythonExecutable")
if ($useExplicitPython) {
    if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
        throw "-PythonExecutable needs a path to a python.exe."
    }
    # Resolve against the caller's location so relative paths keep working.
    $python = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
        $PythonExecutable
    )
}
else {
    $python = $defaultPython
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    if ($useExplicitPython) {
        throw "Python executable was not found: $python"
    }
    throw @"
Project virtual environment was not found: $python
This worktree has no .venv.  Point the runner at an existing interpreter:
  .\scripts\run_isolated_tests.ps1 -PythonExecutable <path to python.exe> [pytest args]
That interpreter supplies Python and the installed dependencies only; the code
under test, the tests, the run directory and the isolated data still come from
this worktree.
"@
}

# The sentinel keeps the check honest: an executable that is not a Python
# interpreter with pytest installed must be refused, not reported as a
# successful probe just because it happened to exit 0.
$probeSentinel = "roxyplan-probe"
$probeOutput = & $python -c "import sys, pytest; print('$probeSentinel', sys.version.split()[0], pytest.__version__)" 2>$null
$probeLine = ($probeOutput | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $probeLine.StartsWith("$probeSentinel ")) {
    throw "The selected interpreter is not a Python with pytest installed: $python"
}
$probeText = $probeLine.Substring($probeSentinel.Length + 1)

foreach ($arg in $PytestArgs) {
    if ($arg -eq "--basetemp" -or $arg.StartsWith("--basetemp=")) {
        throw "Do not pass --basetemp. The isolated runner owns that path."
    }
}

$runsPrefix = $runsRoot.TrimEnd('\') + '\'
if (-not $runRoot.StartsWith($runsPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to create a test run outside test_workspace/runs: $runRoot"
}

New-Item -ItemType Directory -Path $pytestRoot, $tempRoot, $artifactsRoot -Force | Out-Null
Set-Content -LiteralPath (Join-Path $artifactsRoot "python-executable.txt") -Value @(
    $python,
    $probeText
)
# Which project the run actually tested: the worktree that owns this script.
# A borrowed interpreter can come from anywhere, so the run directory records
# the code under test next to the interpreter that executed it.
Set-Content -LiteralPath (Join-Path $artifactsRoot "project-root.txt") -Value $projectRoot

$oldTemp = $env:TEMP
$oldTmp = $env:TMP
$oldQtPlatform = $env:QT_QPA_PLATFORM
$oldNoBytecode = $env:PYTHONDONTWRITEBYTECODE
$oldWorkspace = $env:ROXYPLAN_TEST_WORKSPACE
$exitCode = 1
$pushedLocation = $false

try {
    $env:TEMP = $tempRoot
    $env:TMP = $tempRoot
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:ROXYPLAN_TEST_WORKSPACE = $runRoot
    if ([string]::IsNullOrWhiteSpace($env:QT_QPA_PLATFORM)) {
        $env:QT_QPA_PLATFORM = "offscreen"
    }

    # Run pytest from the worktree root: with "-m pytest" the current directory
    # is put on sys.path, so a borrowed interpreter must not inherit the
    # caller's directory or another checkout's modules could be imported.
    Push-Location $projectRoot
    $pushedLocation = $true

    Write-Host "RoxyPlan isolated test run: $runRoot"
    Write-Host "Project under test: $projectRoot"
    Write-Host "Interpreter: $python (python/pytest $probeText)"
    # pytest writes to stderr for collection errors and similar failures. With
    # $ErrorActionPreference = "Stop" that native stderr becomes a terminating
    # error and aborts the run before the output log and exit code are saved, so
    # native stderr is merged into the log and kept as data here.
    $ErrorActionPreference = "Continue"
    & $python -B -m pytest -q -p no:cacheprovider --basetemp $pytestRoot `
        --junitxml (Join-Path $artifactsRoot "junit.xml") @PytestArgs 2>&1 |
        Tee-Object -FilePath (Join-Path $artifactsRoot "pytest-output.txt")
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    Set-Content -LiteralPath (Join-Path $artifactsRoot "exit-code.txt") -Value $exitCode
}
finally {
    if ($pushedLocation) {
        Pop-Location
    }
    $ErrorActionPreference = "Stop"
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
    $env:QT_QPA_PLATFORM = $oldQtPlatform
    $env:PYTHONDONTWRITEBYTECODE = $oldNoBytecode
    $env:ROXYPLAN_TEST_WORKSPACE = $oldWorkspace
}

if ($exitCode -eq 0 -and -not $KeepRun) {
    $resolvedRunRoot = [System.IO.Path]::GetFullPath($runRoot)
    if (
        $resolvedRunRoot -eq $runsRoot -or
        -not $resolvedRunRoot.StartsWith(
            $runsPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Refusing to clean an unsafe test path: $resolvedRunRoot"
    }
    Remove-Item -LiteralPath $resolvedRunRoot -Recurse -Force
    Write-Host "Successful run cleaned: $resolvedRunRoot"
}
else {
    Write-Host "Run artifacts retained: $runRoot"
}

exit $exitCode
