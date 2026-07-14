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
    [string]$OutputName = 'HomeLabWallpaperState.exe'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ([IO.Path]::GetFileName($OutputName) -ne $OutputName -or
    $OutputName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*\.exe$') {
    throw 'OutputName must be a simple Windows file name ending in .exe.'
}
$outputBaseName = [IO.Path]::GetFileNameWithoutExtension($OutputName).TrimEnd('.', ' ')
if ($outputBaseName -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') {
    throw "OutputName uses a reserved Windows device name: $OutputName"
}

if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
    throw 'Go was not found in PATH. Install Go and open a new PowerShell session before building.'
}

$resolvedSourceDir = (Resolve-Path -LiteralPath $SourceDir).Path
$goModulePath = Join-Path $resolvedSourceDir 'go.mod'
if (-not (Test-Path -LiteralPath $goModulePath -PathType Leaf)) {
    throw "No go.mod was found in SourceDir: $resolvedSourceDir"
}

$null = New-Item -ItemType Directory -Path $OutputDir -Force
$resolvedOutputDir = (Resolve-Path -LiteralPath $OutputDir).Path
$outputPath = Join-Path $resolvedOutputDir $OutputName
$temporaryOutputPath = Join-Path $resolvedOutputDir ".$OutputName.$PID.tmp"

$environmentNames = @('GOOS', 'GOARCH', 'GOAMD64', 'CGO_ENABLED')
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $item = Get-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    $previousEnvironment[$name] = @{
        Exists = $null -ne $item
        Value  = if ($null -ne $item) { $item.Value } else { $null }
    }
}

Push-Location -LiteralPath $resolvedSourceDir
try {
    $env:GOOS = 'windows'
    $env:GOARCH = 'amd64'
    $env:GOAMD64 = 'v1'
    $env:CGO_ENABLED = '0'

    & go version
    if ($LASTEXITCODE -ne 0) {
        throw "go version failed with exit code $LASTEXITCODE"
    }

    Write-Host "Testing Go service in $resolvedSourceDir"
    & go test -count=1 ./...
    if ($LASTEXITCODE -ne 0) {
        throw "go test failed with exit code $LASTEXITCODE"
    }

    Write-Host 'Running go vet'
    & go vet ./...
    if ($LASTEXITCODE -ne 0) {
        throw "go vet failed with exit code $LASTEXITCODE"
    }

    Write-Host "Building Windows amd64 executable: $outputPath"
    & go build -buildvcs=false -trimpath -ldflags '-s -w' -o $temporaryOutputPath .
    if ($LASTEXITCODE -ne 0) {
        throw "go build failed with exit code $LASTEXITCODE"
    }
    $temporaryArtifact = Get-Item -LiteralPath $temporaryOutputPath
    if ($temporaryArtifact.Length -le 0) {
        throw 'go build produced an empty executable.'
    }
    $temporaryHash = Get-FileHash -LiteralPath $temporaryOutputPath -Algorithm SHA256
    Write-Host "Verified temporary artifact: $($temporaryArtifact.Length) bytes, SHA256 $($temporaryHash.Hash)"
    Move-Item -LiteralPath $temporaryOutputPath -Destination $outputPath -Force
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $temporaryOutputPath -Force -ErrorAction SilentlyContinue
    foreach ($name in $environmentNames) {
        $previous = $previousEnvironment[$name]
        if ($previous.Exists) {
            Set-Item -LiteralPath "Env:$name" -Value $previous.Value
        }
        else {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
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
