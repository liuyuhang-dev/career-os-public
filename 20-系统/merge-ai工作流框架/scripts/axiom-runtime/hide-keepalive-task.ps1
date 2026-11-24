[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = 'Merge-Axiom-Robot-KeepAlive',
    [string]$TaskPath = '\',
    [string]$OutputDirectory = (Join-Path $env:LOCALAPPDATA 'Axiom\launchers')
)
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
$actions = @($task.Actions)
if ($actions.Count -ne 1) { throw 'Expected one keepalive action; no changes made.' }
$action = $actions[0]
if ([IO.Path]::GetFileName($action.Execute) -ieq 'wscript.exe') {
    Write-Output 'Keepalive already uses a console-free launcher.'
    return
}
if ([IO.Path]::GetFileName($action.Execute) -notin @('powershell.exe', 'pwsh.exe')) {
    throw 'Expected a PowerShell keepalive action; no changes made.'
}
if (($action.Execute + $action.Arguments) -match '[\r\n]') { throw 'Invalid multiline action.' }
if (!$PSCmdlet.ShouldProcess($TaskPath + $TaskName, 'Hide keepalive console; preserve triggers and principal')) { return }

[IO.Directory]::CreateDirectory($OutputDirectory) | Out-Null
$safeName = [Convert]::ToHexString([Text.Encoding]::UTF8.GetBytes($TaskPath + $TaskName))
$backup = Join-Path $OutputDirectory ($safeName + '.before-hidden.xml')
if (!(Test-Path -LiteralPath $backup)) {
    Export-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath | Set-Content -LiteralPath $backup -Encoding Unicode
}
$launcher = Join-Path $OutputDirectory ($safeName + '.vbs')
$command = '"' + $action.Execute + '" ' + $action.Arguments
$quoted = $command.Replace('"', '""')
$source = @"
Option Explicit
Dim shell, result
Set shell = CreateObject("WScript.Shell")
result = shell.Run("$quoted", 0, True)
WScript.Quit result
"@
Set-Content -LiteralPath $launcher -Value $source -Encoding Unicode
$actionOptions = @{
    Execute = (Join-Path $env:WINDIR 'System32\wscript.exe')
    Argument = ('//B //NoLogo "' + $launcher + '"')
}
if ($action.WorkingDirectory) { $actionOptions.WorkingDirectory = $action.WorkingDirectory }
$replacement = New-ScheduledTaskAction @actionOptions
Set-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Action $replacement | Out-Null
Write-Output 'Keepalive launcher updated. Running UI and AI processes were not restarted.'
