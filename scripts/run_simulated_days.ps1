param(
    [string]$InstanceRoot,
    [string]$StartDate
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python virtual environment is unavailable: $python"
}
if ([string]::IsNullOrWhiteSpace($InstanceRoot)) {
    $localData = [Environment]::GetFolderPath('LocalApplicationData')
    if ([string]::IsNullOrWhiteSpace($localData)) {
        throw 'LocalApplicationData is unavailable; specify -InstanceRoot explicitly.'
    }
    $InstanceRoot = Join-Path $localData 'RoxyPlanTestInstances\simulated-days'
}
if (-not [System.IO.Path]::IsPathRooted($InstanceRoot)) {
    throw 'InstanceRoot must be an absolute path outside the project.'
}
$instance = [System.IO.Path]::GetFullPath($InstanceRoot)
$projectPrefix = $projectRoot.TrimEnd('\', '/') + '\'
$instancePrefix = $instance.TrimEnd('\', '/') + '\'
if ($instance -eq $projectRoot -or
    $instance.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $projectRoot.StartsWith($instancePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'InstanceRoot must be separate from the project directory.'
}
# Establish the directory before Python resolves its identity. On packaged
# Windows hosts, creating LocalAppData paths can redirect them to LocalCache;
# resolving a missing root first would leave runtime containment checks with
# the pre-redirect identity. Only the validated, separate instance is created.
[System.IO.Directory]::CreateDirectory($instance) | Out-Null
# A first launch starts on the real day by default. A marked instance resumes
# its saved business day; reopening must never reset it to the system date.
if ([string]::IsNullOrWhiteSpace($StartDate) -and
    -not (Test-Path -LiteralPath (Join-Path $instance 'simulation_state.json') -PathType Leaf)) {
    $StartDate = Get-Date -Format 'yyyy-MM-dd'
}
Write-Host 'RoxyPlan simulated-day test instance (not normal user data).'
Write-Host "Isolated instance: $instance"
Write-Host 'The chat date control advances this instance only; Windows time is unchanged.'

function Get-FormalFingerprints {
    $paths = @(
        (Join-Path $projectRoot 'memory.json'),
        (Join-Path $projectRoot 'config.json'),
        (Join-Path $projectRoot 'data\pet_config.json'),
        (Join-Path $projectRoot 'data\roxy_personality.json')
    )
    foreach ($directory in @('data\private', 'logs', 'data\knowledge')) {
        $full = Join-Path $projectRoot $directory
        if (Test-Path -LiteralPath $full -PathType Container) {
            $paths += @(Get-ChildItem -LiteralPath $full -Recurse -File | ForEach-Object FullName)
        }
    }
    $result = @{}
    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            # All monitored files are inside the project. Use a checked prefix
            # instead of GetRelativePath, which Windows PowerShell 5.1 lacks.
            $rootPrefix = $projectRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
            $fullPath = [System.IO.Path]::GetFullPath($path)
            if (-not $fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw 'A monitored file is outside the project directory.'
            }
            $relative = $fullPath.Substring($rootPrefix.Length).Replace('\', '/')
            # .NET also works when Windows PowerShell cannot discover the
            # optional Get-FileHash script command through an inherited module path.
            $hash = [System.Security.Cryptography.SHA256]::Create()
            $stream = $null
            try {
                $stream = [System.IO.File]::OpenRead($path)
                $result[$relative] = [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '')
            }
            finally {
                if ($null -ne $stream) { $stream.Dispose() }
                $hash.Dispose()
            }
        }
    }
    return $result
}

$before = Get-FormalFingerprints
$previousBytecode = $env:PYTHONDONTWRITEBYTECODE
$exitCode = 1
try {
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $arguments = @('-B', '-m', 'frontend.pet_app', '--acceptance-root', $instance, '--simulate-days', '--open-chat')
    if ($StartDate) { $arguments += @('--start-date', $StartDate) }
    Push-Location $projectRoot
    try {
        & $python @arguments
        $exitCode = $LASTEXITCODE
    }
    finally { Pop-Location }
}
finally {
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecode
    $after = Get-FormalFingerprints
    $changed = @(@($before.Keys) + @($after.Keys) | Sort-Object -Unique | Where-Object { $before[$_] -ne $after[$_] })
    if (Test-Path -LiteralPath (Join-Path $instance 'simulation_state.json') -PathType Leaf) {
        $evidence = [ordered]@{
            checked_at = (Get-Date).ToString('o')
            formal_files_before = $before
            formal_files_after = $after
            changed_formal_paths = $changed
            process_exit_code = $exitCode
        }
        $evidence | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $instance 'formal_fingerprints.json') -Encoding UTF8
    }
    if ($changed.Count -gt 0) {
        Write-Warning ("Formal files changed during simulation; inspect evidence, do not infer source: " + ($changed -join ', '))
    } else {
        Write-Host 'Formal file fingerprints remained unchanged during this launch.'
    }
}
exit $exitCode
