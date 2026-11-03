# Drive Spot from this laptop: asks for Spot's login, starts the bridge and opens the laptop page.
#
#   .\start-laptop.ps1                  # Spot on its own Wi-Fi (192.168.80.3)
#   .\start-laptop.ps1 -Hostname 10.0.0.42
#   .\start-laptop.ps1 -Mock            # try it without a robot
param(
    [string]$Hostname = "192.168.80.3",
    [int]$Port = 8000,
    [switch]$Mock,
    [switch]$NoEstop
)

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    # First run on this computer: create the virtual environment and install the Spot SDK.
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "Python isn't installed. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again."
        exit 1
    }
    Write-Host "First run: setting up (takes a minute)..."
    python -m venv (Join-Path $PSScriptRoot ".venv")
    & $python -m pip install --quiet -r (Join-Path $PSScriptRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { Write-Host "Installing packages failed. Check the internet connection and run this again."; exit 1 }
}

$serverArgs = @((Join-Path $PSScriptRoot "server.py"), "--host", "127.0.0.1", "--port", $Port)
if ($Mock) {
    $serverArgs += "--mock"
} else {
    $serverArgs += @("--hostname", $Hostname)
    if ($NoEstop) { $serverArgs += "--no-estop" }
    if (-not $env:BOSDYN_CLIENT_USERNAME) { $env:BOSDYN_CLIENT_USERNAME = Read-Host "Spot username" }
    if (-not $env:BOSDYN_CLIENT_PASSWORD) {
        $secure = Read-Host "Spot password" -AsSecureString
        $env:BOSDYN_CLIENT_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    }
}

# Open the page once the server has had a moment to start.
Start-Job -ScriptBlock { param($url) Start-Sleep 2; Start-Process $url } -ArgumentList "http://localhost:$Port/laptop" | Out-Null

Write-Host "Laptop controls: http://localhost:$Port/laptop  (Ctrl+C here to stop)"
& $python @serverArgs
