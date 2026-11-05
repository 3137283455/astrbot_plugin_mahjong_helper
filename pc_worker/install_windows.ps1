param([string]$AppDirectory=$PSScriptRoot)
$ErrorActionPreference='Stop'
$appRoot=[IO.Path]::GetFullPath($AppDirectory)
$pythonPath=Join-Path $appRoot 'runtime\pythonw.exe'
$workerPath=Join-Path $appRoot 'supervisor.py'
$configPath=Join-Path $appRoot 'private\config.json'
foreach($required in @($pythonPath,$workerPath,$configPath)){if(-not(Test-Path -LiteralPath $required)){throw "缺少工作端文件：$required"}}
$taskUser=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$action=New-ScheduledTaskAction -Execute $pythonPath -Argument ('-I "'+$workerPath+'" --config "'+$configPath+'"') -WorkingDirectory $appRoot
$trigger=New-ScheduledTaskTrigger -AtLogOn -User $taskUser
$principal=New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'Mortal4.1b电脑工作端' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description '登录后自动连接雀魂机器人；断线重连，仅提交官方Mortal4.1b复盘任务。' -Force | Out-Null
Write-Output '已设置：Windows登录后自动启动，异常退出后自动重启。'
