[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$taskName = 'HomeLabWallpaperState'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell session.'
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "Scheduled task does not exist: $taskName"
    Write-Host 'No executable, background, or state files were changed.'
    return
}

if ($PSCmdlet.ShouldProcess($taskName, 'Stop and unregister scheduled task')) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Scheduled task removed: $taskName"
    Write-Host 'The executable, backgrounds, and state directory were intentionally preserved.'
}
