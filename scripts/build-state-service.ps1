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

. (Join-Path $PSScriptRoot 'process-utils.ps1')

$expectedServiceVersion = 7

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
$pythonSources = @(
    Get-ChildItem -LiteralPath $resolvedSourceDir -Filter '*.py' -File -Recurse |
        Select-Object -ExpandProperty FullName
)
if ($pythonSources.Count -eq 0) {
    throw "No Python sources were found in: $resolvedSourceDir"
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
& $resolvedPython -m py_compile @pythonSources
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
    $consoleInputBytes = $strictUtf8.GetBytes($consoleBackgrounds + "`n`n`n`n`n`n`n" + "n`n")
    $consoleResult = Invoke-BuildRedirectedProcess `
        -FilePath $temporaryArtifactPath `
        -Arguments ('configure --config "' + $consoleConfig + '"') `
        -WorkingDirectory $consoleRoot `
        -StandardInputBytes $consoleInputBytes `
        -OutputEncoding $strictUtf8 `
        -TimeoutSeconds 30 `
        -TerminationTimeoutSeconds 5
    $consoleOutput = $consoleResult.StandardOutput
    $consoleError = $consoleResult.StandardError
    if ($consoleResult.ExitCode -ne 0) {
        throw "Packaged console encoding smoke test exited with code $($consoleResult.ExitCode). Output: $consoleOutput Error: $consoleError"
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

        $initialCards = Invoke-RestMethod `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$smokePort/api/cards" `
            -TimeoutSec 2
        if ($initialCards.version -ne 1 -or
            $initialCards.initialized -ne $false -or
            $null -ne $initialCards.channels) {
            throw 'Packaged executable returned an invalid uninitialized card state.'
        }

        $smokeIcon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 2h20v20H2z"/></svg>'
        $initializeCardsBody = [ordered]@{
            version    = 1
            services   = @([ordered]@{
                id          = 'service-smoke'
                iconSvg     = $smokeIcon
                name        = 'Smoke Service'
                description = 'Packaged card API'
                url         = 'https://smoke.home.example.test/'
                probeUrl    = $null
            })
            quickLinks = @([ordered]@{
                id      = 'quick-smoke'
                iconSvg = $smokeIcon
                name    = 'Smoke Link'
                url     = 'https://example.test/'
            })
        } | ConvertTo-Json -Depth 8 -Compress
        $initializeCardsResponse = Invoke-WebRequest `
            -UseBasicParsing `
            -Method Post `
            -Uri "http://127.0.0.1:$smokePort/api/cards/initialize" `
            -Headers @{ Origin = 'https://home.example.test'; 'X-Requested-With' = 'homelab-dashboard' } `
            -ContentType 'application/json' `
            -Body $initializeCardsBody `
            -TimeoutSec 2
        if ($initializeCardsResponse.StatusCode -ne 201) {
            throw "Packaged executable returned HTTP $($initializeCardsResponse.StatusCode) for card initialization."
        }
        $initializedCards = $initializeCardsResponse.Content | ConvertFrom-Json
        if ($initializedCards.initialized -ne $true -or
            $initializedCards.channels.services.revision -ne 1 -or
            $initializedCards.channels.quickLinks.revision -ne 1 -or
            $initializedCards.channels.services.cards.Count -ne 1 -or
            $initializedCards.channels.quickLinks.cards.Count -ne 1) {
            throw 'Packaged executable returned an invalid initialized card state.'
        }

        $patchCardsBody = [ordered]@{
            version          = 1
            channel          = 'services'
            expectedRevision = 1
            cards            = @([ordered]@{
                id          = 'service-smoke'
                iconSvg     = $initializedCards.channels.services.cards[0].iconSvg
                name        = 'Updated Smoke Service'
                description = 'Packaged card API'
                url         = 'https://smoke.home.example.test/'
                probeUrl    = $null
            })
        } | ConvertTo-Json -Depth 8 -Compress
        $patchedCards = Invoke-RestMethod `
            -UseBasicParsing `
            -Method Patch `
            -Uri "http://127.0.0.1:$smokePort/api/cards" `
            -Headers @{ Origin = 'https://home.example.test'; 'X-Requested-With' = 'homelab-dashboard' } `
            -ContentType 'application/json' `
            -Body $patchCardsBody `
            -TimeoutSec 2
        if ($patchedCards.channels.services.revision -ne 2 -or
            $patchedCards.channels.services.cards[0].name -ne 'Updated Smoke Service' -or
            $patchedCards.channels.services.cards[0].iconSvg -ne $initializedCards.channels.services.cards[0].iconSvg -or
            $patchedCards.channels.quickLinks.revision -ne 1) {
            throw 'Packaged executable failed the card PATCH smoke test.'
        }
        try {
            $null = Invoke-WebRequest `
                -UseBasicParsing `
                -Method Patch `
                -Uri "http://127.0.0.1:$smokePort/api/cards" `
                -Headers @{ Origin = 'https://home.example.test'; 'X-Requested-With' = 'homelab-dashboard' } `
                -ContentType 'application/json' `
                -Body $patchCardsBody `
                -TimeoutSec 2
            throw 'Packaged executable accepted a stale card revision.'
        } catch {
            $response = $_.Exception.Response
            if ($null -eq $response -or [int]$response.StatusCode -ne 409) {
                throw
            }
            $conflictPayload = $null
            if (-not [string]::IsNullOrWhiteSpace($_.ErrorDetails.Message)) {
                try {
                    $conflictPayload = $_.ErrorDetails.Message | ConvertFrom-Json
                } catch {
                    $conflictPayload = $null
                }
            }
            if ($null -eq $conflictPayload -and
                $response.PSObject.Methods.Name -contains 'GetResponseStream') {
                $responseReader = [IO.StreamReader]::new($response.GetResponseStream())
                try {
                    $conflictPayload = $responseReader.ReadToEnd() | ConvertFrom-Json
                } finally {
                    $responseReader.Dispose()
                }
            }
            if ($null -eq $conflictPayload -or
                $conflictPayload.error -ne 'revision_conflict' -or
                $conflictPayload.currentRevision -ne 2) {
                throw 'Packaged executable returned an invalid stale card revision conflict.'
            }
        }
        if (-not (Test-Path -LiteralPath (Join-Path $smokeState 'DashboardCards.json') -PathType Leaf)) {
            throw 'Packaged executable initialized cards without persisting DashboardCards.json.'
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

        $backgroundInventory = Invoke-RestMethod `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$smokePort/api/background-files" `
            -TimeoutSec 120
        if ($backgroundInventory.version -ne 1 -or
            $null -eq $backgroundInventory.backgrounds -or
            $null -eq $backgroundInventory.thumbnails) {
            throw 'Packaged executable returned an invalid background inventory.'
        }
        $smokeInventoryItem = @($backgroundInventory.backgrounds) |
            Where-Object { $_.name -eq 'smoke.png' } |
            Select-Object -First 1
        if ($null -eq $smokeInventoryItem -or
            $smokeInventoryItem.thumbnail -notmatch '^thumbnails/[A-Za-z0-9._-]+\.webp\?v=[A-Za-z0-9._-]+$') {
            throw 'Packaged executable did not publish a safe WebP thumbnail URL for smoke.png.'
        }
        $thumbnailRelativePath = ($smokeInventoryItem.thumbnail -split '\?', 2)[0]
        $thumbnailSmokePath = Join-Path $smokeBackgrounds ($thumbnailRelativePath.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $thumbnailSmokePath -PathType Leaf)) {
            throw 'Packaged executable inventory referenced a missing smoke.png thumbnail.'
        }
        $thumbnailSmokeBytes = [IO.File]::ReadAllBytes($thumbnailSmokePath)
        if ($thumbnailSmokeBytes.Length -lt 12 -or
            [Text.Encoding]::ASCII.GetString($thumbnailSmokeBytes, 0, 4) -ne 'RIFF' -or
            [Text.Encoding]::ASCII.GetString($thumbnailSmokeBytes, 8, 4) -ne 'WEBP') {
            throw 'Packaged executable did not generate a valid WebP thumbnail.'
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
        $smokeProcess.Dispose()
        $smokeProcess = $null
        $smokeProcess = Start-Process `
            -FilePath $temporaryArtifactPath `
            -ArgumentList @('serve', '--config', ('"' + $smokeConfig + '"')) `
            -WorkingDirectory $smokeRoot `
            -WindowStyle Hidden `
            -PassThru
        $restartDeadline = (Get-Date).AddSeconds(20)
        $restartHealth = $null
        do {
            try {
                $restartHealth = Invoke-RestMethod -UseBasicParsing -Uri $healthUri -TimeoutSec 2
            } catch {
                Start-Sleep -Milliseconds 250
            }
        } while (($null -eq $restartHealth -or $restartHealth.status -ne 'ok') -and (Get-Date) -lt $restartDeadline)
        if ($null -eq $restartHealth -or $restartHealth.version -ne $expectedServiceVersion) {
            throw 'Packaged executable did not become healthy after restart.'
        }
        $persistedCards = Invoke-RestMethod `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$smokePort/api/cards" `
            -TimeoutSec 2
        if ($persistedCards.initialized -ne $true -or
            $persistedCards.channels.services.revision -ne 2 -or
            $persistedCards.channels.services.cards[0].name -ne 'Updated Smoke Service' -or
            $persistedCards.channels.services.cards[0].iconSvg -ne $initializedCards.channels.services.cards[0].iconSvg -or
            $persistedCards.channels.quickLinks.revision -ne 1) {
            throw 'Packaged executable did not preserve card state across restart.'
        }
        $null = Invoke-RestMethod `
            -UseBasicParsing `
            -Method Post `
            -Uri "http://127.0.0.1:$smokePort/_control/shutdown" `
            -Headers @{ 'X-Control-Token' = $smokeToken } `
            -TimeoutSec 2
        if (-not $smokeProcess.WaitForExit(10000)) {
            throw 'Restarted packaged executable did not stop after the control request.'
        }
        if ($smokeProcess.ExitCode -ne 0) {
            throw "Restarted packaged executable exited with code $($smokeProcess.ExitCode)."
        }
    } finally {
        if ($null -ne $smokeProcess) {
            try {
                if (-not $smokeProcess.HasExited) {
                    Stop-BuildProcessTree -Process $smokeProcess -ConfirmTimeoutMilliseconds 5000
                }
            } catch {
                Write-Warning "Failed to clean up packaged smoke process: $($_.Exception.Message)"
            }
            $smokeProcess.Dispose()
        }
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
