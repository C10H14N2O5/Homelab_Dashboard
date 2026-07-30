Set-StrictMode -Version Latest

function Stop-BuildProcessTree {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [Diagnostics.Process]$Process,

        [Parameter()]
        [ValidateRange(1, 60000)]
        [int]$ConfirmTimeoutMilliseconds = 5000
    )

    $processId = $Process.Id
    try {
        $Process.Refresh()
        if ($Process.HasExited) {
            return
        }
    } catch {
        Write-Verbose "Process state could not be read before tree termination for PID $processId`: $($_.Exception.Message)"
    }

    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $taskkillProcess = $null
    $taskkillSucceeded = $false
    try {
        $taskkillPath = Join-Path ([Environment]::GetFolderPath('System')) 'taskkill.exe'
        if (Test-Path -LiteralPath $taskkillPath -PathType Leaf) {
            $taskkillStartInfo = [Diagnostics.ProcessStartInfo]::new()
            $taskkillStartInfo.FileName = $taskkillPath
            $taskkillStartInfo.Arguments = "/PID $processId /T /F"
            $taskkillStartInfo.UseShellExecute = $false
            $taskkillStartInfo.CreateNoWindow = $true
            $taskkillStartInfo.RedirectStandardOutput = $true
            $taskkillStartInfo.RedirectStandardError = $true

            $taskkillProcess = [Diagnostics.Process]::new()
            $taskkillProcess.StartInfo = $taskkillStartInfo
            if ($taskkillProcess.Start()) {
                $taskkillOutputTask = $taskkillProcess.StandardOutput.ReadToEndAsync()
                $taskkillErrorTask = $taskkillProcess.StandardError.ReadToEndAsync()
                if (-not $taskkillProcess.WaitForExit([Math]::Min(2000, $ConfirmTimeoutMilliseconds))) {
                    $taskkillProcess.Kill()
                    $null = $taskkillProcess.WaitForExit(500)
                }
                if ($taskkillProcess.HasExited -and $taskkillOutputTask.IsCompleted) {
                    $null = $taskkillOutputTask.GetAwaiter().GetResult()
                }
                if ($taskkillProcess.HasExited -and $taskkillErrorTask.IsCompleted) {
                    $null = $taskkillErrorTask.GetAwaiter().GetResult()
                }
                $taskkillSucceeded = $taskkillProcess.HasExited -and
                    $taskkillProcess.ExitCode -eq 0
            }
        }
    } catch {
        Write-Verbose "taskkill could not terminate process tree $processId`: $($_.Exception.Message)"
    } finally {
        if ($null -ne $taskkillProcess) {
            try {
                if (-not $taskkillProcess.HasExited) {
                    $taskkillProcess.Kill()
                    $null = $taskkillProcess.WaitForExit(500)
                }
            } catch {
            }
            $taskkillProcess.Dispose()
        }
    }

    try {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            $Process.Kill()
        }
    } catch {
    }

    $remainingMilliseconds = $ConfirmTimeoutMilliseconds - [int]$stopwatch.ElapsedMilliseconds
    if ($remainingMilliseconds -lt 0) {
        $remainingMilliseconds = 0
    }
    try {
        if (-not $Process.HasExited -and -not $Process.WaitForExit($remainingMilliseconds)) {
            throw "Process tree $processId did not stop within $ConfirmTimeoutMilliseconds ms."
        }
        $Process.Refresh()
        if (-not $Process.HasExited) {
            throw "Process tree $processId did not stop within $ConfirmTimeoutMilliseconds ms."
        }
        if (-not $taskkillSucceeded) {
            throw "taskkill /T /F did not confirm termination of the complete process tree; parent PID $processId was stopped with the fallback."
        }
    } catch {
        throw "Process tree $processId could not be confirmed stopped: $($_.Exception.Message)"
    } finally {
        $stopwatch.Stop()
    }
}

function Invoke-BuildRedirectedProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$FilePath,

        [Parameter()]
        [AllowEmptyString()]
        [string]$Arguments = '',

        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$WorkingDirectory,

        [Parameter()]
        [AllowNull()]
        [byte[]]$StandardInputBytes,

        [Parameter(Mandatory = $true)]
        [ValidateNotNull()]
        [Text.Encoding]$OutputEncoding,

        [Parameter()]
        [ValidateRange(1, 3600)]
        [int]$TimeoutSeconds = 30,

        [Parameter()]
        [ValidateRange(1, 60)]
        [int]$TerminationTimeoutSeconds = 5
    )

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = $Arguments
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = $OutputEncoding
    $startInfo.StandardErrorEncoding = $OutputEncoding

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $started = $false
    $standardOutputTask = $null
    $standardErrorTask = $null
    $standardOutput = ''
    $standardError = ''
    try {
        if (-not $process.Start()) {
            throw "Process did not start: $FilePath"
        }
        $started = $true

        # Start both reads before writing input so neither redirected pipe can fill
        # while its peer is being consumed.
        $standardOutputTask = $process.StandardOutput.ReadToEndAsync()
        $standardErrorTask = $process.StandardError.ReadToEndAsync()

        try {
            if ($null -ne $StandardInputBytes -and $StandardInputBytes.Length -gt 0) {
                $process.StandardInput.BaseStream.Write(
                    $StandardInputBytes,
                    0,
                    $StandardInputBytes.Length
                )
                $process.StandardInput.BaseStream.Flush()
            }
        } finally {
            $process.StandardInput.Close()
        }

        $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
        if ($timedOut) {
            Stop-BuildProcessTree `
                -Process $process `
                -ConfirmTimeoutMilliseconds ($TerminationTimeoutSeconds * 1000)
        }

        $redirectedTasks = [Threading.Tasks.Task[]]@(
            $standardOutputTask,
            $standardErrorTask
        )
        if (-not [Threading.Tasks.Task]::WaitAll(
            $redirectedTasks,
            $TerminationTimeoutSeconds * 1000
        )) {
            throw "Redirected process streams did not close within $TerminationTimeoutSeconds seconds."
        }
        $standardOutput = $standardOutputTask.GetAwaiter().GetResult()
        $standardError = $standardErrorTask.GetAwaiter().GetResult()

        if ($timedOut) {
            $timeoutException = [TimeoutException]::new(
                "Process exceeded the $TimeoutSeconds second timeout: $FilePath"
            )
            $timeoutException.Data['StandardOutput'] = $standardOutput
            $timeoutException.Data['StandardError'] = $standardError
            throw $timeoutException
        }

        [pscustomobject]@{
            ExitCode       = $process.ExitCode
            StandardOutput = $standardOutput
            StandardError  = $standardError
        }
    } finally {
        if ($started) {
            try {
                $process.StandardInput.Close()
            } catch {
            }
            try {
                if (-not $process.HasExited) {
                    Stop-BuildProcessTree `
                        -Process $process `
                        -ConfirmTimeoutMilliseconds ($TerminationTimeoutSeconds * 1000)
                }
            } catch {
                Write-Warning "Failed to clean up process $($process.Id): $($_.Exception.Message)"
            }
        }
        $process.Dispose()
    }
}
