param(
  [string]$WebServiceName = "VOXWeb",
  [string]$WorkerServiceName = "VOXWorker",
  [string]$MaintenanceTaskName = "VOX Maintenance"
)

$ErrorActionPreference = "Stop"

foreach ($Name in @($WebServiceName, $WorkerServiceName)) {
  $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
  if ($service) {
    if ($service.Status -ne "Stopped") {
      Stop-Service -Name $Name -Force
    }
    sc.exe delete $Name | Out-Null
    Write-Host "Removed service: $Name"
  } else {
    Write-Host "Service not found: $Name"
  }
}

if (Get-ScheduledTask -TaskName $MaintenanceTaskName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $MaintenanceTaskName -Confirm:$false
  Write-Host "Removed scheduled task: $MaintenanceTaskName"
} else {
  Write-Host "Scheduled task not found: $MaintenanceTaskName"
}
