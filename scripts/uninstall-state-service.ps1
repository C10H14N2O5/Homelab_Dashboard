[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter()]
    [string]$ExePath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$taskName = 'HomeLabWallpaperState'
$startupShortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\HomeLabWallpaperState.lnk'

if (-not [string]::IsNullOrWhiteSpace($ExePath)) {
    $resolvedExePath = (Resolve-Path -LiteralPath $ExePath).Path
    if (Test-Path -LiteralPath $resolvedExePath -PathType Leaf) {
        if ($PSCmdlet.ShouldProcess($resolvedExePath, 'Request the user-level API backend to stop')) {
            & $resolvedExePath stop
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "The backend stop command exited with code $LASTEXITCODE."
            }
        }
    }
}

if (Test-Path -LiteralPath $startupShortcut -PathType Leaf) {
    if ($PSCmdlet.ShouldProcess($startupShortcut, 'Remove current-user Startup shortcut')) {
        Remove-Item -LiteralPath $startupShortcut -Force
        Write-Host "Startup shortcut removed: $startupShortcut"
    }
}

$legacyTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $legacyTask) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Warning "The legacy SYSTEM scheduled task still exists. Rerun this script as Administrator to remove: $taskName"
    } elseif ($PSCmdlet.ShouldProcess($taskName, 'Stop and unregister legacy SYSTEM scheduled task')) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Legacy scheduled task removed: $taskName"
    }
}

Write-Host 'The executable, JSON configuration, state, logs, and backgrounds were intentionally preserved.'
