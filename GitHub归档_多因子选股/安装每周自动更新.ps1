$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $project "install_daily_task.cmd")
if ($LASTEXITCODE -ne 0) { throw "Could not install the daily scheduled task." }
$service = New-Object -ComObject "Schedule.Service"
$service.Connect()
$folder = $service.GetFolder("\")
$definition = $folder.GetTask("MultiFactor-Wind-Daily-2100").Definition
$definition.Settings.DisallowStartIfOnBatteries = $false
$definition.Settings.StopIfGoingOnBatteries = $false
$definition.Settings.StartWhenAvailable = $true
$definition.Settings.ExecutionTimeLimit = "PT4H"
$null = $folder.RegisterTaskDefinition("MultiFactor-Wind-Daily-2100", $definition, 6, $null, $null, 3, $null)
