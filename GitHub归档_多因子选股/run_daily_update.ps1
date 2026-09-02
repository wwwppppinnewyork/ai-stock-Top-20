$ErrorActionPreference = "Stop"

$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $project "logs"
$mainLog = Join-Path $logDir "daily_update.log"
$errorLog = Join-Path $logDir "daily_update_error.log"
$python = "C:\Users\Lenovo\AppData\Local\Programs\Python\Python315\python.exe"
$strategy = Join-Path $project "run_wind_strategy.py"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$started = Get-Date
Add-Content -LiteralPath $mainLog -Encoding UTF8 -Value "[$($started.ToString('yyyy-MM-dd HH:mm:ss'))] Daily update started"

try {
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Python executable not found: $python"
    }
    if (-not (Test-Path -LiteralPath $strategy)) {
        throw "Strategy entry point not found: $strategy"
    }

    Push-Location $project
    try {
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo.FileName = $python
        $process.StartInfo.Arguments = "`"$strategy`""
        $process.StartInfo.WorkingDirectory = $project
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.CreateNoWindow = $true
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        $null = $process.Start()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
        $exitCode = $process.ExitCode
        if ($stdout) { Add-Content -LiteralPath $mainLog -Encoding UTF8 -Value $stdout.TrimEnd() }
        if ($stderr) { Add-Content -LiteralPath $errorLog -Encoding UTF8 -Value $stderr.TrimEnd() }
    }
    finally {
        Pop-Location
    }

    if ($exitCode -ne 0) {
        throw "Strategy exited with code $exitCode. Check Wind login and the error log."
    }

    $finished = Get-Date
    Add-Content -LiteralPath $mainLog -Encoding UTF8 -Value "[$($finished.ToString('yyyy-MM-dd HH:mm:ss'))] Daily update completed in $([math]::Round(($finished-$started).TotalMinutes, 1)) minutes"
    exit 0
}
catch {
    $failed = Get-Date
    Add-Content -LiteralPath $errorLog -Encoding UTF8 -Value "[$($failed.ToString('yyyy-MM-dd HH:mm:ss'))] $($_.Exception.Message)"
    Add-Content -LiteralPath $mainLog -Encoding UTF8 -Value "[$($failed.ToString('yyyy-MM-dd HH:mm:ss'))] Daily update failed"
    exit 1
}
