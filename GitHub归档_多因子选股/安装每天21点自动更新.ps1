$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $project "run_daily_update.ps1"
$taskName = "MultiFactor-Wind-Weekly-Friday-2100"
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$command = "& '$($runner.Replace("'", "''"))'; exit `$LASTEXITCODE"
$encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $encodedCommand" `
    -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At "21:00"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "每周五21:00更新Wind多因子选股；非交易日使用最近交易日数据。要求用户和Wind终端保持登录。" `
    -Force | Out-Null

Write-Host "已安装：$taskName，每周五21:00运行。"
