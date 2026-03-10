# Register Edward AutoStart in Windows Task Scheduler
#
# Run this script ONCE as Administrator:
#   Right-click register-autostart.ps1 → "Run as administrator"
#
# What it does:
#   Creates a Task Scheduler task "EdwardAutostart" that runs autostart.ps1
#   silently at login for the current user.
#
# To remove the task later:
#   schtasks /Delete /TN "EdwardAutostart" /F
#
# To check status:
#   schtasks /Query /TN "EdwardAutostart"

$TaskName    = "EdwardAutostart"
$ProjectDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath  = Join-Path $ProjectDir "autostart.ps1"
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

# Check admin
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: Must run as Administrator. Right-click this script and choose 'Run as administrator'." -ForegroundColor Red
    exit 1
}

Write-Host "Registering Task Scheduler entry for Edward..." -ForegroundColor Green
Write-Host "  Task name : $TaskName"
Write-Host "  Script    : $ScriptPath"
Write-Host "  User      : $CurrentUser"
Write-Host ""

# Remove existing task if present
$existing = schtasks /Query /TN $TaskName 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Removing existing task..." -ForegroundColor Yellow
    schtasks /Delete /TN $TaskName /F | Out-Null
}

# Build XML task definition
$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Auto-start Edward AI assistant (backend, frontend, Cloudflare tunnel, watchdog)</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>$CurrentUser</UserId>
      <Delay>PT10S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$CurrentUser</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-ExecutionPolicy Bypass -WindowStyle Hidden -File "$ScriptPath"</Arguments>
      <WorkingDirectory>$ProjectDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

# Write XML to temp file and register
$xmlPath = Join-Path $env:TEMP "edward-task.xml"
$taskXml | Out-File -FilePath $xmlPath -Encoding Unicode

$result = schtasks /Create /TN $TaskName /XML $xmlPath /F 2>&1
Remove-Item $xmlPath -Force -ErrorAction SilentlyContinue

if ($LASTEXITCODE -eq 0) {
    Write-Host "Task registered successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. ngrok and WhatsApp already configured"
    Write-Host "  2. Run: .\autostart.ps1 to test"
    Write-Host "  3. Setup complete - tunnel and WhatsApp already configured"
    Write-Host "  4. Test manually: .\autostart.ps1"
    Write-Host "  5. Test manually: .\autostart.ps1"
    Write-Host "  6. Reboot to verify automatic start"
} else {
    Write-Host "ERROR registering task:" -ForegroundColor Red
    Write-Host $result
    exit 1
}
