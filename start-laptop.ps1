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
function Find-SystemPython {
    # Windows has a fake "python" that only points to the Microsoft Store, so run each
    # candidate and keep the first that reports a real Python 3.
    $candidates = @(
        @("py", "-3"),
        @("python")
    )
    $installed = @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending)
    foreach ($exe in $installed) { $candidates += , @($exe.FullName) }
    foreach ($cmd in $candidates) {
        try {
            $exeArgs = @($cmd | Select-Object -Skip 1)
            $version = & $cmd[0] @exeArgs --version 2>&1
            if ($LASTEXITCODE -eq 0 -and "$version" -match "^Python 3") { return , $cmd }
        } catch {}
    }
    return $null
}

if (-not (Test-Path $python)) {
    # First run on this computer: create the virtual environment.
    $sys = Find-SystemPython
    if (-not $sys) {
        Write-Host ""
        Write-Host "Python isn't installed on this computer."
        Write-Host "Install it from https://www.python.org/downloads/ - on the first installer screen,"
        Write-Host "tick 'Add python.exe to PATH' - then run this again."
        Start-Process "https://www.python.org/downloads/"
        exit 1
    }
    Write-Host "First run: setting up with $(& $sys[0] @($sys | Select-Object -Skip 1) --version) (takes a minute)..."
    & $sys[0] @($sys | Select-Object -Skip 1) -m venv (Join-Path $PSScriptRoot ".venv")
    if (-not (Test-Path $python)) { Write-Host "Couldn't create the Python environment in $PSScriptRoot."; exit 1 }
}

# Install the Spot SDK if it's missing, including after an earlier install was cut short.
& $python -c "import bosdyn.client, fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing the Spot SDK (needs internet)..."
    & $python -m pip install --quiet --disable-pip-version-check -r (Join-Path $PSScriptRoot "requirements.txt")
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
