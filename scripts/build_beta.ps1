# PREPARATION ONLY until the coordinator grants the unique build slot.
# After installing packaging/requirements-build.txt in a dedicated Python 3.10
# x64 venv (not the main .venv), and integrating roxy_main.py:
# .\scripts\build_beta.ps1 -PythonExecutable C:\build-env\Scripts\python.exe `
#   -ProductVersion 0.9.0-beta.1 -BuildSlotApproved
# No install, cleanup, GUI launch, model call, or private config copy occurs here.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9][A-Za-z0-9.+-]{0,63}$')][string]$ProductVersion,
    [switch]$BuildSlotApproved,
    [switch]$StudyEdition,
    [string]$OutputRoot
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($PythonExecutable)
if (-not $BuildSlotApproved) {
    throw 'Build slot not approved. Ask the coordinator to schedule the sole build before running.'
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Explicit Python executable not found.' }
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $projectRoot 'test_workspace\runs\beta-builds'
}
$outputBase = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputRoot)
$runId = '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'), ([Guid]::NewGuid().ToString('N'))
$runRoot = Join-Path $outputBase $runId
# CreateNew ownership: never reuse, remove, or overwrite any existing build/dist.
if (Test-Path -LiteralPath $runRoot) { throw 'Unique build directory already exists.' }
[System.IO.Directory]::CreateDirectory($runRoot) | Out-Null
$logRoot = Join-Path $runRoot 'logs'
[System.IO.Directory]::CreateDirectory($logRoot) | Out-Null
[System.IO.Directory]::CreateDirectory((Join-Path $runRoot 'temp')) | Out-Null
$savedEnvironment = @{}
$environmentNames = @('ROXY_BETA_RUN_ROOT', 'ROXY_BETA_PROFILE', 'PYINSTALLER_CONFIG_DIR', 'TEMP', 'TMP',
                       'PYTHONPATH', 'PYTHONHOME', 'PYTHONNOUSERSITE', 'PYTHONDONTWRITEBYTECODE', 'PATH')
$exitCode = 1
$stage = 'initialize'
$pushedLocation = $false

function Invoke-BetaPython {
    param([string]$StageName, [string[]]$Arguments)
    $stageLog = Join-Path $logRoot ($StageName + '.log')
    # Native stderr belongs in the phase log; a nonzero native code is the
    # authority, not PowerShell's stderr-to-ErrorRecord conversion.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $python @Arguments > $stageLog 2>&1
        $nativeExit = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    if ($nativeExit -ne 0) {
        $script:exitCode = $nativeExit
        throw "Stage $StageName failed with exit $nativeExit; see $stageLog"
    }
    Write-Output "Completed $StageName; log=$stageLog"
}

try {
    foreach ($name in $environmentNames) {
        $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    [Environment]::SetEnvironmentVariable('ROXY_BETA_RUN_ROOT', $runRoot, 'Process')
    [Environment]::SetEnvironmentVariable('ROXY_BETA_PROFILE', $(if ($StudyEdition) { 'study' } else { 'beta' }), 'Process')
    [Environment]::SetEnvironmentVariable('PYINSTALLER_CONFIG_DIR', (Join-Path $runRoot 'pyinstaller-cache'), 'Process')
    foreach ($name in @('TEMP', 'TMP')) {
        [Environment]::SetEnvironmentVariable($name, (Join-Path $runRoot 'temp'), 'Process')
    }
    foreach ($name in @('PYTHONPATH', 'PYTHONHOME')) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    [Environment]::SetEnvironmentVariable('PYTHONNOUSERSITE', '1', 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', '1', 'Process')
    # Prevent host tools (e.g. Poppler) from supplying same-named native DLLs.
    # Only the selected Python base and Windows system are dependency sources.
    $basePrefix = (& $python -B -c 'import sys; print(sys.base_prefix)' | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($basePrefix)) {
        throw 'Cannot determine the selected Python base prefix.'
    }
    $basePrefix = $basePrefix.Trim()
    if (-not (Test-Path -LiteralPath $basePrefix -PathType Container)) {
        throw 'Selected Python base prefix was not found.'
    }
    $gitDirectory = Split-Path -Parent (Get-Command git.exe -ErrorAction Stop).Source
    $buildPath = @((Split-Path -Parent $python), $basePrefix,
                  (Join-Path $basePrefix 'DLLs'), (Join-Path $basePrefix 'Library\bin'),
                  (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot, $gitDirectory)
    [Environment]::SetEnvironmentVariable('PATH', ($buildPath -join ';'), 'Process')
    Push-Location -LiteralPath $projectRoot
    $pushedLocation = $true
    $checker = Join-Path $PSScriptRoot 'check_beta_release.py'
    $stage = 'prepare'
    Invoke-BetaPython $stage @('-B', $checker, '--prepare', '--root', $projectRoot,
                             '--run-root', $runRoot, '--version', $ProductVersion)
    $stage = 'pyinstaller'
    Invoke-BetaPython $stage @('-B', '-m', 'PyInstaller', '--noconfirm',
                             '--distpath', (Join-Path $runRoot 'dist'),
                             '--workpath', (Join-Path $runRoot 'build'),
                             (Join-Path $projectRoot 'packaging\RoxyPlan.spec'))
    $stage = 'finalize'
    Invoke-BetaPython $stage @('-B', $checker, '--finalize', '--root', $projectRoot, '--run-root', $runRoot)
    $stage = 'complete'
    $exitCode = 0
}
catch {
    ($_ | Out-String) | Set-Content -LiteralPath (Join-Path $logRoot 'failure.log') -Encoding UTF8
    Write-Warning "Beta failed at $stage; directory retained: $runRoot"
}
finally {
    if ($pushedLocation) { Pop-Location }
    foreach ($name in $savedEnvironment.Keys) {
        if ($null -eq $savedEnvironment[$name]) {
            Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue
        }
        else {
            [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
        }
    }
    $result = [ordered]@{ stage=$stage; exit_code=$exitCode; run_id=$runId;
                         runtime_acceptance='not_performed'; zip_created=($exitCode -eq 0) }
    $result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runRoot 'build-result.json') -Encoding UTF8
    Write-Output "Build exit=$exitCode; evidence=$runRoot; runtime acceptance not performed."
}
exit $exitCode
