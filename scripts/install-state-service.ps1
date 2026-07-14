[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ExePath,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$BackgroundDir,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Fa-f0-9]{64}$')]
    [string]$ExpectedSha256,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$AllowedOrigin = 'https://home.c10h14n2o5.com',

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$Listen = '127.0.0.1:8787',

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$StateDir = (Join-Path $env:ProgramData 'HomeLabWallpaperState')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$taskName = 'HomeLabWallpaperState'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script from an elevated PowerShell session.'
    }
}

function ConvertTo-WindowsCommandLineArgument {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }

    $builder = [Text.StringBuilder]::new()
    [void]$builder.Append([char]34)
    $backslashCount = 0

    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $backslashCount++
            continue
        }

        if ($character -eq [char]34) {
            [void]$builder.Append([char]92, (2 * $backslashCount) + 1)
            [void]$builder.Append([char]34)
            $backslashCount = 0
            continue
        }

        if ($backslashCount -gt 0) {
            [void]$builder.Append([char]92, $backslashCount)
            $backslashCount = 0
        }
        [void]$builder.Append($character)
    }

    if ($backslashCount -gt 0) {
        [void]$builder.Append([char]92, 2 * $backslashCount)
    }
    [void]$builder.Append([char]34)
    return $builder.ToString()
}

function Test-PathInsideDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Directory
    )

    $trimCharacters = [char[]]@(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $candidate = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath($Directory).TrimEnd($trimCharacters)
    if ([string]::Equals($candidate.TrimEnd($trimCharacters), $root, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    $rootWithSeparator = $root + [IO.Path]::DirectorySeparatorChar
    return $candidate.StartsWith($rootWithSeparator, [StringComparison]::OrdinalIgnoreCase)
}

Assert-Administrator

$resolvedExePath = (Resolve-Path -LiteralPath $ExePath).Path
if (-not (Test-Path -LiteralPath $resolvedExePath -PathType Leaf) -or
    [IO.Path]::GetExtension($resolvedExePath) -ne '.exe') {
    throw "ExePath must point to an existing .exe file: $ExePath"
}

$programFilesRoot = if (-not [string]::IsNullOrWhiteSpace($env:ProgramW6432)) {
    $env:ProgramW6432
} else {
    $env:ProgramFiles
}
if ([string]::IsNullOrWhiteSpace($programFilesRoot)) {
    throw 'The Program Files directory could not be determined from the environment.'
}
$requiredServiceRoot = Join-Path $programFilesRoot 'HomeLabWallpaperState'
if (-not (Test-PathInsideDirectory -Path $resolvedExePath -Directory $requiredServiceRoot)) {
    throw "ExePath must be inside the protected service directory: $requiredServiceRoot"
}
$actualSha256 = (Get-FileHash -LiteralPath $resolvedExePath -Algorithm SHA256).Hash
if (-not [string]::Equals($actualSha256, $ExpectedSha256, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Executable SHA256 mismatch. Expected $ExpectedSha256 but found $actualSha256"
}

$resolvedBackgroundDir = (Resolve-Path -LiteralPath $BackgroundDir).Path
if (-not (Test-Path -LiteralPath $resolvedBackgroundDir -PathType Container)) {
    throw "BackgroundDir must point to an existing directory: $BackgroundDir"
}
if (-not (Test-Path -LiteralPath (Join-Path $resolvedBackgroundDir 'bg.jpg') -PathType Leaf)) {
    throw "BackgroundDir must contain the fallback file bg.jpg: $resolvedBackgroundDir"
}

if ($Listen -notmatch '^127\.0\.0\.1:(?<Port>\d{1,5})$') {
    throw 'Listen must use the loopback form 127.0.0.1:<port>.'
}
$listenPort = [int]$Matches.Port
if ($listenPort -lt 1 -or $listenPort -gt 65535) {
    throw 'Listen port must be between 1 and 65535.'
}

[Uri]$originUri = $null
if (-not [Uri]::TryCreate($AllowedOrigin, [UriKind]::Absolute, [ref]$originUri) -or
    $originUri.Scheme -notin @('http', 'https') -or
    -not [string]::IsNullOrEmpty($originUri.UserInfo) -or
    -not [string]::IsNullOrEmpty($originUri.Query) -or
    -not [string]::IsNullOrEmpty($originUri.Fragment) -or
    $originUri.AbsolutePath -ne '/') {
    throw 'AllowedOrigin must be a complete HTTP(S) origin without a path, query, fragment, or credentials.'
}
$normalizedOrigin = $originUri.GetLeftPart([UriPartial]::Authority)

$publicRoot = Split-Path -Parent $resolvedBackgroundDir
$unresolvedStateDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($StateDir)
if (Test-PathInsideDirectory -Path $resolvedExePath -Directory $publicRoot) {
    throw "ExePath must be outside the public Web root: $publicRoot"
}
if (Test-PathInsideDirectory -Path $unresolvedStateDir -Directory $publicRoot) {
    throw "StateDir must be outside the public Web root: $publicRoot"
}

if (-not (Test-Path -LiteralPath $unresolvedStateDir -PathType Container)) {
    if ($PSCmdlet.ShouldProcess($unresolvedStateDir, 'Create non-public state directory')) {
        $null = New-Item -ItemType Directory -Path $unresolvedStateDir -Force
    } else {
        Write-Host "What if: state directory would be created at $unresolvedStateDir"
        return
    }
}
$resolvedStateDir = (Resolve-Path -LiteralPath $unresolvedStateDir).Path

$argumentValues = @(
    '--background-dir', $resolvedBackgroundDir,
    '--allowed-origin', $normalizedOrigin,
    '--listen', $Listen,
    '--state-dir', $resolvedStateDir
)
$argumentLine = ($argumentValues | ForEach-Object {
        ConvertTo-WindowsCommandLineArgument -Value $_
    }) -join ' '

$actionParameters = @{
    Execute          = $resolvedExePath
    Argument         = $argumentLine
    WorkingDirectory = (Split-Path -Parent $resolvedExePath)
}
$action = New-ScheduledTaskAction @actionParameters
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

if ($PSCmdlet.ShouldProcess($taskName, 'Register SYSTEM startup task and start it now')) {
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $existingTask) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        $stopDeadline = (Get-Date).AddSeconds(10)
        do {
            $existingTask = Get-ScheduledTask -TaskName $taskName
            if ($existingTask.State -ne 'Running') {
                break
            }
            Start-Sleep -Milliseconds 250
        } while ((Get-Date) -lt $stopDeadline)
        if ($existingTask.State -eq 'Running') {
            throw "Existing scheduled task did not stop within 10 seconds: $taskName"
        }
    }

    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description 'HomeLab landscape and portrait wallpaper preference state service.' `
        -Force | Out-Null

    Start-ScheduledTask -TaskName $taskName

    $healthUri = "http://$Listen/healthz"
    $healthDeadline = (Get-Date).AddSeconds(15)
    $consecutiveHealthyChecks = 0
    do {
        $taskState = (Get-ScheduledTask -TaskName $taskName).State
        try {
            $health = Invoke-RestMethod -UseBasicParsing -Uri $healthUri -TimeoutSec 2
            if ($taskState -eq 'Running' -and $health.status -eq 'ok') {
                $consecutiveHealthyChecks++
            } else {
                $consecutiveHealthyChecks = 0
            }
        } catch {
            $consecutiveHealthyChecks = 0
        }

        if ($consecutiveHealthyChecks -lt 2) {
            Start-Sleep -Milliseconds 500
        }
    } while ($consecutiveHealthyChecks -lt 2 -and (Get-Date) -lt $healthDeadline)

    if ($consecutiveHealthyChecks -lt 2) {
        $task = Get-ScheduledTask -TaskName $taskName
        $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
        $logPath = Join-Path $resolvedStateDir 'service.log'
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        throw "Scheduled task did not become healthy at $healthUri. State=$($task.State); LastTaskResult=$($taskInfo.LastTaskResult); inspect $logPath"
    }

    Write-Host "Scheduled task registered, started, and healthy: $taskName"
    Write-Host "Executable: $resolvedExePath"
    Write-Host "Executable SHA256: $actualSha256"
    Write-Host "Background directory: $resolvedBackgroundDir"
    Write-Host "State directory: $resolvedStateDir"
    Write-Host "Allowed origin: $normalizedOrigin"
    Write-Host "Listen address: $Listen"
    Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
}
