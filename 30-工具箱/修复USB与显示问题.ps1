param([switch]$Revert)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '需要管理员权限：请右键点击本脚本 -> 使用 PowerShell 运行（或以管理员身份运行同目录的 .bat）。' -ForegroundColor Red
    exit 1
}

# 关闭节能时填 0；-Revert 恢复默认时填 1
$val = if ($Revert) { 1 } else { 0 }
$usbSub  = '2a737441-1930-4402-8d77-b2bebba308a3'
$usbSel  = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'
$pcieSub = '501a4d13-42af-4429-9fd1-a8218c268e20'
$aspm    = 'ee12f906-d277-404b-b6da-e5fa1a576df5'

Write-Host '== 1. 电源计划：USB 选择性暂停 / PCIe 链接状态电源管理 ==' -ForegroundColor Cyan
[void](powercfg /setacvalueindex SCHEME_CURRENT $usbSub  $usbSel $val)
[void](powercfg /setdcvalueindex SCHEME_CURRENT $usbSub  $usbSel $val)
[void](powercfg /setacvalueindex SCHEME_CURRENT $pcieSub $aspm   $val)
[void](powercfg /setdcvalueindex SCHEME_CURRENT $pcieSub $aspm   $val)
[void](powercfg /setactive SCHEME_CURRENT)

$out = powercfg /query SCHEME_CURRENT $usbSub $usbSel
$usbNow = ($out | Select-String '当前交流电源设置索引').Line -replace '.*索引: ', ''
$out = powercfg /query SCHEME_CURRENT $pcieSub $aspm
$pcieNow = ($out | Select-String '当前交流电源设置索引').Line -replace '.*索引: ', ''
Write-Host ("   USB 选择性暂停 = {0}    PCIe 链接状态电源管理 = {1}   (0 = 关闭)" -f $usbNow, $pcieNow)

Write-Host '== 2. USB 根集线器：取消“允许计算机关闭此设备以节约电源” ==' -ForegroundColor Cyan
Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Enum\USB' -ErrorAction SilentlyContinue |
    Where-Object { $_.PSChildName -like 'ROOT_HUB*' } |
    ForEach-Object { Get-ChildItem $_.PSPath -ErrorAction SilentlyContinue } |
    ForEach-Object {
        $dp = Join-Path $_.PSPath 'Device Parameters'
        try {
            if (-not (Test-Path $dp)) { New-Item -Path $dp -Force | Out-Null }
            New-ItemProperty -Path $dp -Name 'EnhancedPowerManagementEnabled' -Value $val -PropertyType DWord -Force | Out-Null
            New-ItemProperty -Path $dp -Name 'SelectiveSuspendEnabled'        -Value $val -PropertyType DWord -Force | Out-Null
            Write-Host ("   已处理: {0}" -f $_.PSChildName)
        } catch {
            Write-Host ("   跳过 {0}（{1}）" -f $_.PSChildName, $_.Exception.Message) -ForegroundColor Yellow
        }
    }

Write-Host '== 3. 键盘（MAD_HE / VID_373B）设备级节能开关 ==' -ForegroundColor Cyan
Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Enum\USB' -ErrorAction SilentlyContinue |
    Where-Object { $_.PSChildName -like 'VID_373B&PID_1054*' } |
    ForEach-Object { Get-ChildItem $_.PSPath -ErrorAction SilentlyContinue } |
    ForEach-Object {
        $dp = Join-Path $_.PSPath 'Device Parameters'
        try {
            if (-not (Test-Path $dp)) { New-Item -Path $dp -Force | Out-Null }
            New-ItemProperty -Path $dp -Name 'SelectiveSuspendEnabled'        -Value $val -PropertyType DWord -Force | Out-Null
            New-ItemProperty -Path $dp -Name 'EnhancedPowerManagementEnabled' -Value $val -PropertyType DWord -Force | Out-Null
            Write-Host ("   已处理: {0}" -f $_.PSChildName)
        } catch {
            Write-Host ("   跳过 {0}（{1}）" -f $_.PSChildName, $_.Exception.Message) -ForegroundColor Yellow
        }
    }

Write-Host ''
if ($Revert) {
    Write-Host '已恢复默认节能设置。' -ForegroundColor Green
} else {
    Write-Host '完成。请重启一次，让 USB 根集线器重新加载设置。' -ForegroundColor Green
    Write-Host '（如需恢复默认：在本脚本上加 -Revert 运行）'
}
