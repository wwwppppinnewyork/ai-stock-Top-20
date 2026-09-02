$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Show-ApiKeyDialog {
    $form = New-Object System.Windows.Forms.Form
    $form.Text = "设置 OpenAI API 密钥"
    $form.StartPosition = "CenterScreen"
    $form.FormBorderStyle = "FixedDialog"
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.ClientSize = New-Object System.Drawing.Size(520, 190)
    $form.TopMost = $true

    $title = New-Object System.Windows.Forms.Label
    $title.Text = "请粘贴你的 OpenAI API Key"
    $title.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 12, [System.Drawing.FontStyle]::Bold)
    $title.AutoSize = $true
    $title.Location = New-Object System.Drawing.Point(22, 18)
    $form.Controls.Add($title)

    $hint = New-Object System.Windows.Forms.Label
    $hint.Text = "密钥只会保存到当前 Windows 用户的环境变量，不会写入 HTML。"
    $hint.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)
    $hint.AutoSize = $true
    $hint.Location = New-Object System.Drawing.Point(23, 53)
    $form.Controls.Add($hint)

    $keyBox = New-Object System.Windows.Forms.TextBox
    $keyBox.Location = New-Object System.Drawing.Point(26, 82)
    $keyBox.Size = New-Object System.Drawing.Size(466, 28)
    $keyBox.Font = New-Object System.Drawing.Font("Consolas", 10)
    $keyBox.UseSystemPasswordChar = $true
    $form.Controls.Add($keyBox)

    $show = New-Object System.Windows.Forms.CheckBox
    $show.Text = "显示密钥"
    $show.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)
    $show.AutoSize = $true
    $show.Location = New-Object System.Drawing.Point(26, 121)
    $show.Add_CheckedChanged({ $keyBox.UseSystemPasswordChar = -not $show.Checked })
    $form.Controls.Add($show)

    $ok = New-Object System.Windows.Forms.Button
    $ok.Text = "保存并打开"
    $ok.Size = New-Object System.Drawing.Size(105, 32)
    $ok.Location = New-Object System.Drawing.Point(275, 143)
    $ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $form.Controls.Add($ok)
    $form.AcceptButton = $ok

    $cancel = New-Object System.Windows.Forms.Button
    $cancel.Text = "取消"
    $cancel.Size = New-Object System.Drawing.Size(105, 32)
    $cancel.Location = New-Object System.Drawing.Point(387, 143)
    $cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $form.Controls.Add($cancel)
    $form.CancelButton = $cancel

    $form.Add_Shown({ $keyBox.Focus() })
    $result = $form.ShowDialog()
    if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
        $form.Dispose()
        return $null
    }

    $apiKey = $keyBox.Text.Trim()
    $keyBox.Clear()
    $form.Dispose()
    return $apiKey
}

try {
    $apiKey = Show-ApiKeyDialog
    if ($null -eq $apiKey) {
        exit 0
    }
    if ([string]::IsNullOrWhiteSpace($apiKey)) {
        throw "API Key 不能为空。"
    }

    [Environment]::SetEnvironmentVariable("OPENAI_API_KEY", $apiKey, "User")
    $env:OPENAI_API_KEY = $apiKey

    $projectServer = Join-Path $PSScriptRoot "src\weight_control_server.py"
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -in @("python.exe", "pythonw.exe") -and
            $_.CommandLine -like "*$projectServer*"
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId }

    Start-Sleep -Milliseconds 500
    & python (Join-Path $PSScriptRoot "src\launch_weight_control.py") portal
    if ($LASTEXITCODE -ne 0) {
        throw "研究工作台启动失败，请检查 Python 是否可用。"
    }

    [System.Windows.Forms.MessageBox]::Show(
        "API Key 已保存，研究工作台已经打开。",
        "设置成功",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null
}
catch {
    [System.Windows.Forms.MessageBox]::Show(
        $_.Exception.Message,
        "设置失败",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
}
finally {
    $apiKey = $null
    $env:OPENAI_API_KEY = $null
}
