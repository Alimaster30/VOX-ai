param(
  [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path,
  [string]$PythonExe = "",
  [string]$WebServiceName = "VOXWeb",
  [string]$WorkerServiceName = "VOXWorker",
  [string]$MaintenanceTaskName = "VOX Maintenance"
)

$ErrorActionPreference = "Stop"

if (-not $PythonExe) {
  $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
}

if (-not (Test-Path $PythonExe)) {
  throw "Python virtual environment not found: $PythonExe"
}

function Install-VOXService {
  param(
    [string]$Name,
    [string]$ScriptName,
    [string]$Description
  )

  $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
  if ($service) {
    Write-Host "Service already exists: $Name"
    return
  }

  $scriptPath = Join-Path $ProjectRoot $ScriptName
  $binPath = "`"$PythonExe`" `"$scriptPath`""
  $displayName = if ($Name -eq $WebServiceName) { "VOX Web" } else { "VOX Worker" }
  New-Service -Name $Name -BinaryPathName $binPath -DisplayName $displayName -StartupType Automatic -Description $Description
  Write-Host "Installed service: $Name"
}

Install-VOXService -Name $WebServiceName -ScriptName "serve.py" -Description "VOX production web server"
Install-VOXService -Name $WorkerServiceName -ScriptName "worker.py" -Description "VOX background worker"

$maintenanceScript = Join-Path $ProjectRoot "maintenance.py"
$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$maintenanceScript`"" -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 3:00AM
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Description "Run VOX database maintenance cleanup"
Register-ScheduledTask -TaskName $MaintenanceTaskName -InputObject $task -Force | Out-Null
Write-Host "Installed scheduled task: $MaintenanceTaskName"

Start-Service -Name $WebServiceName
Start-Service -Name $WorkerServiceName
Write-Host "VOX services started."
