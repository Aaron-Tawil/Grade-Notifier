param(
    [string]$EnvFile = ".env",
    [int]$Port = 8080
)

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$defaultVenvPython = Join-Path $repoRoot ".venv311\Scripts\python.exe"
if (Test-Path $defaultVenvPython) {
    $pythonExe = $defaultVenvPython
} else {
    $pythonExe = "python"
}

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if (-not [string]::IsNullOrWhiteSpace($_) -and -not $_.StartsWith('#')) {
            $parts = $_ -split '=', 2
            if ($parts.Length -eq 2) {
                $envKey = $parts[0].Trim()
                $envValue = $parts[1].Trim()
                Set-Item -Path "Env:$envKey" -Value $envValue
            }
        }
    }
}

$env:FUNCTION_TARGET = "main"
$env:PLAYWRIGHT_BROWSERS_PATH = "0"  # force local browsers under cwd
$env:PYTHONUNBUFFERED = "1"

Write-Host "Using Python executable: $pythonExe"
Write-Host "Starting Functions Framework on port $Port"
& $pythonExe -m functions_framework --target $env:FUNCTION_TARGET --port $Port --debug
