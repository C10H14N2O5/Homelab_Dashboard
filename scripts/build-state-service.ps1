[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$SourceDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'state-service'),

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$OutputDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist'),

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$OutputName = 'HomeLabWallpaperState.exe',

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$expectedServiceVersion = 4

if ([IO.Path]::GetFileName($OutputName) -ne $OutputName -or
    $OutputName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*\.exe$') {
    throw 'OutputName must be a simple Windows file name ending in .exe.'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $PythonPath = $venvPython
    } else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $pythonCommand) {
            throw 'Python was not found. Create .venv or install Python 3.11 or newer.'
        }
        $PythonPath = $pythonCommand.Source
    }
}
$resolvedPython = (Resolve-Path -LiteralPath $PythonPath).Path
$resolvedSourceDir = (Resolve-Path -LiteralPath $SourceDir).Path
$entryPoint = Join-Path $resolvedSourceDir 'main.py'
$testFile = Join-Path $resolvedSourceDir 'test_main.py'
if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf) -or
    -not (Test-Path -LiteralPath $testFile -PathType Leaf)) {
    throw "Python service source or tests are missing in: $resolvedSourceDir"
}

& $resolvedPython --version
if ($LASTEXITCODE -ne 0) {
    throw "Python failed with exit code $LASTEXITCODE"
}
& $resolvedPython -c "import PIL, PyInstaller, send2trash; from PIL import features; required = {'JPEG': 'jpg', 'PNG': 'zlib', 'WebP': 'webp', 'AVIF': 'avif'}; missing = [name for name, feature in required.items() if not features.check(feature)]; assert not missing, 'Pillow is missing required codec support: ' + ', '.join(missing); print('PyInstaller', PyInstaller.__version__); print('Pillow', PIL.__version__, 'codecs:', ', '.join(required)); print('Send2Trash import ok')"
if ($LASTEXITCODE -ne 0) {
    throw 'A pinned build/runtime dependency or required Pillow codec is unavailable. Install requirements with: python -m pip install -r requirements.txt'
}

Write-Host 'Checking Python syntax...'
& $resolvedPython -m py_compile $entryPoint $testFile
if ($LASTEXITCODE -ne 0) {
    throw "Python syntax check failed with exit code $LASTEXITCODE"
}

Write-Host 'Running Python unit tests...'
& $resolvedPython -m unittest discover -s $resolvedSourceDir -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) {
    throw "Python unit tests failed with exit code $LASTEXITCODE"
}

$null = New-Item -ItemType Directory -Path $OutputDir -Force
$resolvedOutputDir = (Resolve-Path -LiteralPath $OutputDir).Path
$outputPath = Join-Path $resolvedOutputDir $OutputName
$buildRoot = Join-Path $resolvedOutputDir ".pyinstaller-$PID"
$temporaryDist = Join-Path $buildRoot 'dist'
$temporaryWork = Join-Path $buildRoot 'work'
$temporarySpec = Join-Path $buildRoot 'spec'
$temporaryArtifactPath = Join-Path $temporaryDist $OutputName

try {
    $null = New-Item -ItemType Directory -Path $temporaryDist, $temporaryWork, $temporarySpec -Force
    Write-Host "Building one-file Windows executable: $outputPath"
    & $resolvedPython -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --console `
        --noupx `
        --name ([IO.Path]::GetFileNameWithoutExtension($OutputName)) `
        --distpath $temporaryDist `
        --workpath $temporaryWork `
        --specpath $temporarySpec `
        $entryPoint
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $temporaryArtifactPath -PathType Leaf)) {
        throw "PyInstaller did not create the expected artifact: $temporaryArtifactPath"
    }
    $temporaryArtifact = Get-Item -LiteralPath $temporaryArtifactPath
    if ($temporaryArtifact.Length -le 0) {
        throw 'PyInstaller produced an empty executable.'
    }

    Write-Host 'Running packaged executable smoke test...'
    $smokeRoot = Join-Path $buildRoot 'smoke'
    $smokeBackgrounds = Join-Path $smokeRoot 'web\html\backgrounds'
    $smokeState = Join-Path $smokeRoot 'HomeLabWallpaperState-data'
    $smokeConfig = Join-Path $smokeRoot 'HomeLabWallpaperState.json'
    $null = New-Item -ItemType Directory -Path $smokeBackgrounds, $smokeState -Force
    [IO.File]::WriteAllBytes((Join-Path $smokeBackgrounds 'bg.jpg'), [byte[]](1, 2, 3))
    [IO.File]::WriteAllBytes((Join-Path $smokeBackgrounds 'test.jpg'), [byte[]](4, 5, 6))

    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $smokePort = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    $listener.Stop()
    $smokeToken = 'build-smoke-control-token-0123456789abcdef'
    $smokeSettings = [ordered]@{
        version                 = 2
        backgroundDir           = $smokeBackgrounds
        allowedOrigins          = @('https://home.example.test')
        listen                  = "127.0.0.1:$smokePort"
        stateDir                = $smokeState
        controlToken            = $smokeToken
        probeConnectAddress     = '127.0.0.1:443'
        probeDomainSuffix       = 'home.example.test'
        probeTimeoutSeconds     = 5
    }
    [IO.File]::WriteAllText(
        $smokeConfig,
        (($smokeSettings | ConvertTo-Json -Depth 4) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )

    Write-Host 'Checking packaged console UTF-8 output...'
    $consoleConfigName = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5Lit5paH6YWN572uLmpzb24='))
    $consoleRoot = Join-Path $smokeRoot 'console-test'
    $consoleBackgrounds = Join-Path $consoleRoot 'web\html\backgrounds'
    $consoleState = Join-Path $consoleRoot 'HomeLabWallpaperState-data'
    $consoleConfig = Join-Path $consoleRoot $consoleConfigName
    $null = New-Item -ItemType Directory -Path $consoleBackgrounds, $consoleState -Force
    [IO.File]::WriteAllBytes((Join-Path $consoleBackgrounds 'bg.jpg'), [byte[]](1, 2, 3))
    [IO.File]::WriteAllText(
        (Join-Path $consoleState 'state.json'),
        '{"version":1,"landscape":"bg.jpg","portrait":"bg.jpg","updatedAt":"2026-07-22T00:00:00Z"}',
        [Text.UTF8Encoding]::new($false)
    )
    $strictUtf8 = [Text.UTF8Encoding]::new($false, $true)
    $consoleStartInfo = [Diagnostics.ProcessStartInfo]::new()
    $consoleStartInfo.FileName = $temporaryArtifactPath
    $consoleStartInfo.Arguments = 'configure --config "' + $consoleConfig + '"'
    $consoleStartInfo.WorkingDirectory = $consoleRoot
    $consoleStartInfo.UseShellExecute = $false
    $consoleStartInfo.CreateNoWindow = $true
    $consoleStartInfo.RedirectStandardInput = $true
    $consoleStartInfo.RedirectStandardOutput = $true
    $consoleStartInfo.RedirectStandardError = $true
    $consoleStartInfo.StandardOutputEncoding = $strictUtf8
    $consoleStartInfo.StandardErrorEncoding = $strictUtf8
    $consoleProcess = [Diagnostics.Process]::new()
    $consoleProcess.StartInfo = $consoleStartInfo
    try {
        if (-not $consoleProcess.Start()) {
            throw 'Packaged console encoding smoke test did not start.'
        }
        $consoleInputBytes = $strictUtf8.GetBytes($consoleBackgrounds + "`n`n`n`n`n`n`n" + "n`n")
        $consoleInputStream = $consoleProcess.StandardInput.BaseStream
        $consoleInputStream.Write($consoleInputBytes, 0, $consoleInputBytes.Length)
        $consoleInputStream.Flush()
        $consoleInputStream.Close()
        $consoleOutput = $consoleProcess.StandardOutput.ReadToEnd()
        $consoleError = $consoleProcess.StandardError.ReadToEnd()
        $consoleProcess.WaitForExit()
        if ($consoleProcess.ExitCode -ne 0) {
            throw "Packaged console encoding smoke test exited with code $($consoleProcess.ExitCode). Output: $consoleOutput Error: $consoleError"
        }
        foreach ($encodedText in @(
            'SG9tZUxhYiBXYWxscGFwZXIgU3RhdGUg6aaW5qyh6YWN572u',
            '6YWN572u44CB54q25oCB5ZKM5pel5b+X5LiN5Lya5pS+5YWl5YWs5byA55qEIHdlYlxodG1sIOebruW9leOAgg==',
            '6IOM5pmv55uu5b2VIFtEOlxab3JheHlcd3d3XGh0bWxcYmFja2dyb3VuZHNd77ya'
        )) {
            $expectedText = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encodedText))
            if (-not $consoleOutput.Contains($expectedText)) {
                throw "Packaged executable did not emit expected UTF-8 console text: $expectedText"
            }
        }
    } finally {
        if (-not $consoleProcess.HasExited) {
            $consoleProcess.Kill()
            $consoleProcess.WaitForExit()
        }
        $consoleProcess.Dispose()
    }

    $smokeProcess = Start-Process `
        -FilePath $temporaryArtifactPath `
        -ArgumentList @('serve', '--config', ('"' + $smokeConfig + '"')) `
        -WorkingDirectory $smokeRoot `
        -WindowStyle Hidden `
        -PassThru
    try {
        $healthUri = "http://127.0.0.1:$smokePort/healthz"
        $deadline = (Get-Date).AddSeconds(20)
        $health = $null
        do {
            try {
                $health = Invoke-RestMethod -UseBasicParsing -Uri $healthUri -TimeoutSec 2
            } catch {
                Start-Sleep -Milliseconds 250
            }
        } while (($null -eq $health -or $health.status -ne 'ok') -and (Get-Date) -lt $deadline)
        if ($null -eq $health -or
            $health.status -ne 'ok' -or
            $health.service -ne 'HomeLabWallpaperState' -or
            $health.version -ne $expectedServiceVersion) {
            throw 'Packaged executable did not become healthy during the smoke test.'
        }

        $preferences = Invoke-RestMethod `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$smokePort/api/background-preferences" `
            -TimeoutSec 2
        if ($preferences.landscape -ne 'bg.jpg' -or $preferences.portrait -ne 'bg.jpg') {
            throw 'Packaged executable returned an unexpected initial state.'
        }

        $patchBody = @{ mode = 'landscape'; background = 'test.jpg' } | ConvertTo-Json -Compress
        $patched = Invoke-RestMethod `
            -UseBasicParsing `
            -Method Patch `
            -Uri "http://127.0.0.1:$smokePort/api/background-preferences" `
            -Headers @{ Origin = 'https://home.example.test'; 'X-Requested-With' = 'homelab-dashboard' } `
            -ContentType 'application/json' `
            -Body $patchBody `
            -TimeoutSec 2
        if ($patched.landscape -ne 'test.jpg') {
            throw 'Packaged executable failed the PATCH compatibility smoke test.'
        }

        $smokePngBytes = [Convert]::FromBase64String(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
        )
        $uploadResponse = Invoke-WebRequest `
            -UseBasicParsing `
            -Method Post `
            -Uri "http://127.0.0.1:$smokePort/api/background-files/upload?name=smoke.png" `
            -Headers @{ Origin = 'https://home.example.test'; 'X-Requested-With' = 'homelab-dashboard' } `
            -ContentType 'application/octet-stream' `
            -Body $smokePngBytes `
            -TimeoutSec 5
        if ($uploadResponse.StatusCode -ne 201) {
            throw "Packaged executable returned HTTP $($uploadResponse.StatusCode) for the upload smoke test."
        }
        $uploadedSmokePath = Join-Path $smokeBackgrounds 'smoke.png'
        if (-not (Test-Path -LiteralPath $uploadedSmokePath -PathType Leaf)) {
            throw 'Packaged executable accepted the upload but did not create smoke.png.'
        }
        $uploadedSmokeBytes = [IO.File]::ReadAllBytes($uploadedSmokePath)
        if ($uploadedSmokeBytes.Length -ne $smokePngBytes.Length) {
            throw 'Packaged executable changed the uploaded PNG byte length.'
        }
        for ($byteIndex = 0; $byteIndex -lt $smokePngBytes.Length; $byteIndex++) {
            if ($uploadedSmokeBytes[$byteIndex] -ne $smokePngBytes[$byteIndex]) {
                throw 'Packaged executable changed the uploaded PNG bytes.'
            }
        }

        $bestdoriReconcile = Invoke-RestMethod `
            -UseBasicParsing `
            -Method Post `
            -Uri "http://127.0.0.1:$smokePort/api/bestdori/history/reconcile" `
            -Headers @{ Origin = 'https://home.example.test'; 'X-Requested-With' = 'homelab-dashboard' } `
            -ContentType 'application/json' `
            -Body '{}' `
            -TimeoutSec 2
        if (($bestdoriReconcile.removed -isnot [int] -and $bestdoriReconcile.removed -isnot [long]) -or
            ($bestdoriReconcile.remaining -isnot [int] -and $bestdoriReconcile.remaining -isnot [long]) -or
            $bestdoriReconcile.removed -lt 0 -or
            $bestdoriReconcile.remaining -lt 0) {
            throw 'Packaged executable failed the offline Bestdori history reconciliation smoke test.'
        }

        $serviceStatusBody = @{ targets = @('https://outside.example.test/') } | ConvertTo-Json -Compress
        $serviceStatus = Invoke-RestMethod `
            -UseBasicParsing `
            -Method Post `
            -Uri "http://127.0.0.1:$smokePort/api/service-status" `
            -Headers @{ Origin = 'https://home.example.test'; 'X-Requested-With' = 'homelab-dashboard' } `
            -ContentType 'application/json' `
            -Body $serviceStatusBody `
            -TimeoutSec 2
        if ($serviceStatus.results.Count -ne 1 -or
            $serviceStatus.results[0].target -ne 'https://outside.example.test/' -or
            $serviceStatus.results[0].error -ne 'invalid_target') {
            throw 'Packaged executable failed the service-status API smoke test.'
        }

        $null = Invoke-RestMethod `
            -UseBasicParsing `
            -Method Post `
            -Uri "http://127.0.0.1:$smokePort/_control/shutdown" `
            -Headers @{ 'X-Control-Token' = $smokeToken } `
            -TimeoutSec 2
        if (-not $smokeProcess.WaitForExit(10000)) {
            throw 'Packaged executable did not stop after the control request.'
        }
        if ($smokeProcess.ExitCode -ne 0) {
            throw "Packaged executable exited with code $($smokeProcess.ExitCode)."
        }
    } finally {
        if (-not $smokeProcess.HasExited) {
            Stop-Process -Id $smokeProcess.Id -Force -ErrorAction SilentlyContinue
            $smokeProcess.WaitForExit()
        }
        $smokeProcess.Dispose()
    }

    $temporaryHash = Get-FileHash -LiteralPath $temporaryArtifactPath -Algorithm SHA256
    Write-Host "Verified temporary artifact: $($temporaryArtifact.Length) bytes, SHA256 $($temporaryHash.Hash)"
    Move-Item -LiteralPath $temporaryArtifactPath -Destination $outputPath -Force
} finally {
    if (Test-Path -LiteralPath $buildRoot) {
        Remove-Item -LiteralPath $buildRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$artifact = Get-Item -LiteralPath $outputPath
$hash = Get-FileHash -LiteralPath $outputPath -Algorithm SHA256
Write-Host 'Build completed.'
[pscustomobject]@{
    Path   = $artifact.FullName
    Bytes  = $artifact.Length
    SHA256 = $hash.Hash
}
