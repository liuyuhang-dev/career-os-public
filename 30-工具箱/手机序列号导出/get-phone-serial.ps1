#Requires -Version 5.1
<#
    手机序列号导出工具（Windows / PowerShell 5.1+）

    怎么用：手机用数据线插到电脑上 -> 双击同目录的「查序列号.bat」
    脚本依次走三条路，哪条能读到就用哪条：

      【一】adb               安卓机最准，需要手机上打开「USB 调试」
      【二】libimobiledevice  装了才走这条，能读到 iPhone 的真实序列号
      【三】Windows 设备记录  不用装任何东西，多数安卓机也能读到序列号

    结果同时显示在屏幕上，并导出到桌面：手机序列号_日期_时间.txt

    可选参数：
      -OutDir <目录>    指定导出目录（默认桌面）
      -AdbPath <路径>   手动指定 adb.exe 的位置（一般不用）
      -DownloadAdb      没装 adb 时自动从 Google 官方下载到临时目录使用
      -NoFile           只在屏幕上显示，不导出文件
#>
[CmdletBinding()]
param(
    [string] $OutDir = '',
    [string] $AdbPath = '',
    [switch] $DownloadAdb,
    [switch] $NoFile
)

$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'

$script:Lines = New-Object System.Collections.Generic.List[string]
$script:Found = New-Object System.Collections.Generic.List[object]

function Say {
    param([string] $Text = '', [string] $Color = 'Gray')
    Write-Host $Text -ForegroundColor $Color
    $script:Lines.Add($Text)
}

# 常见手机厂商的 USB 厂商标识（VID），用来在系统记录里认出手机
$PhoneVids = @{
    '05AC' = 'Apple 苹果'
    '04E8' = 'Samsung 三星'
    '2717' = 'Xiaomi 小米/红米'
    '18D1' = 'Google 谷歌'
    '12D1' = 'Huawei 华为'
    '2A70' = 'OnePlus 一加'
    '22D9' = 'OPPO'
    '22B8' = 'Motorola 摩托罗拉'
    '2D95' = 'vivo'
    '0BB4' = 'HTC'
    '1004' = 'LG'
    '05C6' = 'Qualcomm（通用安卓）'
    '0FCE' = 'Sony 索尼'
    '19D2' = 'ZTE 中兴'
    '17EF' = 'Lenovo 联想'
    '0421' = 'Nokia 诺基亚'
    '0B05' = 'ASUS 华硕'
}

function Get-UsbVid([string] $InstanceId) {
    if ($InstanceId -match 'VID_([0-9A-Fa-f]{4})') { return $Matches[1].ToUpper() }
    return ''
}

function Get-UsbTail([string] $InstanceId) {
    # USB\VID_2717&PID_FF40\R58M12345678  ->  R58M12345678
    if (-not $InstanceId) { return '' }
    return ($InstanceId -split '\\')[-1]
}

function Test-SerialLike([string] $Value) {
    if (-not $Value) { return $false }
    if ($Value -match '^\d+&') { return $false }   # Windows 自己生成的节点名，如 6&1a2b3c4d&0&0000
    if ($Value.Length -lt 4) { return $false }
    return $true
}

function Find-Adb {
    if ($AdbPath) {
        if (Test-Path $AdbPath) { return (Resolve-Path $AdbPath).Path }
        Say ("  指定的 adb 路径不存在：" + $AdbPath) 'Red'
    }
    $c = Get-Command adb.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $cands = @(
        "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe",
        "$env:ProgramFiles\platform-tools\adb.exe",
        "${env:ProgramFiles(x86)}\platform-tools\adb.exe",
        'C:\platform-tools\adb.exe',
        "$PSScriptRoot\platform-tools\adb.exe",
        "$env:USERPROFILE\Desktop\platform-tools\adb.exe"
    )
    foreach ($p in $cands) { if ($p -and (Test-Path $p)) { return $p } }
    return ''
}

function Install-Adb {
    $tmp = Join-Path $env:TEMP 'phone-serial-platform-tools'
    $adb = Join-Path $tmp 'platform-tools\adb.exe'
    if (Test-Path $adb) { Say '  用上次已经下载好的 adb，不用重新下载。' 'Green'; return $adb }
    $url = 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip'
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $zip = Join-Path $tmp 'pt.zip'
    Say '  正在从 Google 官方下载 platform-tools（约 8 MB，可能要一两分钟，请稍等）……' 'Yellow'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    # 先试 curl（更稳、更快），没有再退回 Invoke-WebRequest
    $ok = $false
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source -sS -L --fail --max-time 600 -o $zip $url
        if ($LASTEXITCODE -eq 0 -and (Test-Path $zip)) { $ok = $true }
        else { Say '  （curl 下载失败，换一种方式再试）' 'DarkGray' }
    }
    if (-not $ok) {
        try {
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -TimeoutSec 180
            $ok = (Test-Path $zip)
        } catch {
            Say ('  下载失败：' + $_.Exception.Message) 'Red'
        }
    }

    if ($ok) {
        try {
            Expand-Archive -Path $zip -DestinationPath $tmp -Force
            if (Test-Path $adb) { Say '  下载完成。' 'Green'; return $adb }
        } catch {
            Say ('  解压失败：' + $_.Exception.Message) 'Red'
        }
    }
    Say '  没装成。可手动下载 platform-tools 解压后，用 -AdbPath 指定 adb.exe 的位置：' 'Yellow'
    Say '  https://developer.android.google.cn/studio/releases/platform-tools' 'Yellow'
    return ''
}

# ===========================================================================
# 【一】adb
# ===========================================================================
function Invoke-AdbSection {
    Say ''
    Say '【一、adb（安卓官方调试工具，读出来最准）】' 'Cyan'

    $adb = Find-Adb
    if (-not $adb -and $DownloadAdb) { $adb = Install-Adb }
    if (-not $adb) {
        Say '  电脑上没找到 adb，跳过这一步。' 'Yellow'
        Say '  → 想让脚本自动装：双击「查序列号-自动装ADB.bat」' 'DarkGray'
        Say '  → 或手动下载 platform-tools 解压：' 'DarkGray'
        Say '    https://developer.android.google.cn/studio/releases/platform-tools' 'DarkGray'
        return
    }
    Say ("  使用 adb：" + $adb)

    & $adb start-server *> $null
    $devText = (& $adb devices -l 2>&1 | Out-String)

    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($line in ($devText -split "`r?`n")) {
        if ($line -match '^\s*(\S+)\s+(device|unauthorized|offline|recovery|sideload)\s*(.*)$') {
            $rows.Add([pscustomobject]@{ Serial = $Matches[1]; State = $Matches[2]; Extra = $Matches[3].Trim() })
        }
    }
    if ($rows.Count -eq 0) {
        Say '  没有检测到设备（adb 看不到手机）。' 'Yellow'
        Say '  → 安卓：设置 -> 关于手机 -> 连点 7 次「版本号」打开开发者选项 -> 打开「USB 调试」' 'DarkGray'
        Say '  → 插线后手机会弹「是否允许 USB 调试」，点「允许 / 始终允许」' 'DarkGray'
        return
    }

    foreach ($r in $rows) {
        Say ("  发现设备：" + $r.Serial + "   状态：" + $r.State)
        if ($r.State -ne 'device') {
            Say '    → 手机屏幕上还有弹窗没点：解锁手机，点「允许 USB 调试」，然后重新运行本脚本。' 'Yellow'
            continue
        }
        $props = @{}
        foreach ($k in @('ro.serialno', 'ro.boot.serialno', 'ro.product.brand', 'ro.product.model', 'ro.build.version.release')) {
            $v = ''
            try { $v = (& $adb -s $r.Serial shell getprop $k 2>$null | Out-String).Trim() } catch { }
            $props[$k] = $v
        }
        $model = ($props['ro.product.brand'] + ' ' + $props['ro.product.model']).Trim()
        if (-not $model) { $model = '未知机型' }
        Say ("    机型：" + $model + "    Android " + $props['ro.build.version.release'])
        Say ("    机身序列号 (ro.serialno)：" + $props['ro.serialno']) 'White'
        if ($props['ro.boot.serialno'] -and $props['ro.boot.serialno'] -ne $props['ro.serialno']) {
            Say ("    boot 序列号 (ro.boot.serialno)：" + $props['ro.boot.serialno'])
        }
        $sn = $props['ro.serialno']
        if (-not $sn -or $sn -eq 'unknown') { $sn = $props['ro.boot.serialno'] }
        if ($sn -and $sn -ne 'unknown') {
            $script:Found.Add([pscustomobject]@{ Value = $sn; Source = "adb / 机身序列号（$model）" })
        } else {
            Say '    → 系统没有给出机身序列号（新系统有权限限制），看下面的系统记录。' 'DarkGray'
        }
        Say ("    （adb 连接号 " + $r.Serial + " 也可以作为设备标识使用）") 'DarkGray'
    }
}

# ===========================================================================
# 【二】libimobiledevice（iPhone）
# ===========================================================================
function Invoke-ImobileSection {
    Say ''
    Say '【二、iPhone 工具 libimobiledevice】' 'Cyan'

    $tool = Get-Command ideviceinfo -ErrorAction SilentlyContinue
    if (-not $tool) {
        Say '  电脑上没装 libimobiledevice，跳过。' 'DarkGray'
        Say '  → iPhone 在电脑上的替代方案：装「爱思助手」等工具，或在手机上自己看' 'DarkGray'
        return
    }
    try {
        $list = (& idevice_id -l 2>$null | Out-String).Trim()
        if (-not $list) {
            Say '  没有检测到 iPhone。' 'Yellow'
            Say '  → 插线后手机上点「信任此电脑」，并保持屏幕解锁。' 'DarkGray'
            return
        }
        foreach ($row in ($list -split "`r?`n" | Where-Object { $_.Trim() })) {
            $udid  = $row.Trim()
            $sn    = (& ideviceinfo -u $udid -k SerialNumber 2>$null | Out-String).Trim()
            $model = (& ideviceinfo -u $udid -k ProductType  2>$null | Out-String).Trim()
            $name  = (& ideviceinfo -u $udid -k DeviceName   2>$null | Out-String).Trim()
            Say ("  设备：" + $name + "   (" + $model + ")")
            Say ("    序列号：" + $sn) 'White'
            Say ("    UDID  ：" + $udid)
            if ($sn) {
                $script:Found.Add([pscustomobject]@{ Value = $sn; Source = "libimobiledevice / iPhone 序列号（$name）" })
            }
        }
    } catch {
        Say ('  读取失败：' + $_.Exception.Message) 'Yellow'
    }
}

# ===========================================================================
# 【三】Windows 系统记录
# ===========================================================================
function Invoke-PnpSection {
    Say ''
    Say '【三、Windows 系统记录里的设备信息（不用装任何东西）】' 'Cyan'

    $raw = @()
    try {
        $raw = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
            $_.Class -eq 'WPD' -or
            $_.InstanceId -match 'VID_05AC' -or
            $_.FriendlyName -match 'iPhone|iPad|iPod|Android|安卓|手机' -or
            ($_.Class -eq 'USB' -and $_.InstanceId -match 'VID_(04E8|2717|18D1|12D1|2A70|22D9|22B8|2D95|0BB4|1004|05C6|0FCE|19D2|17EF|0421|0B05)')
        })
    } catch { }
    if ($raw.Count -eq 0) {
        try {
            $raw = @(Get-WmiObject Win32_PnPEntity -ErrorAction SilentlyContinue | Where-Object {
                $_.PNPClass -eq 'WPD' -or
                $_.DeviceID -match 'VID_05AC' -or
                $_.Name -match 'iPhone|iPad|iPod|Android|安卓|手机' -or
                ($_.PNPClass -eq 'USB' -and $_.DeviceID -match 'VID_(04E8|2717|18D1|12D1|2A70|22D9|22B8|2D95|0BB4|1004|05C6|0FCE|19D2|17EF|0421|0B05)')
            })
        } catch { }
    }

    $devs = New-Object System.Collections.Generic.List[object]
    foreach ($d in $raw) {
        if ($d.PSObject.Properties['InstanceId']) {
            $devs.Add([pscustomobject]@{ Id = [string]$d.InstanceId; Name = [string]$d.FriendlyName; Class = [string]$d.Class })
        } else {
            $devs.Add([pscustomobject]@{ Id = [string]$d.DeviceID; Name = [string]$d.Name; Class = [string]$d.PNPClass })
        }
    }
    $devs = @($devs | Where-Object { $_.Id -match 'VID_' })

    if ($devs.Count -eq 0) {
        Say '  系统里也没有看到手机设备 —— 说明手机没连上，或者只被当成充电器了。' 'Yellow'
        return
    }

    foreach ($d in $devs) {
        $vid   = Get-UsbVid $d.Id
        $tail  = Get-UsbTail $d.Id
        $brand = ''
        if ($PhoneVids.ContainsKey($vid)) { $brand = '  <- ' + $PhoneVids[$vid] }
        Say ("  设备：" + $d.Name + "   [类别 " + $d.Class + "]")
        Say ("    USB 标识：" + $tail + $brand) 'White'

        if ($vid -eq '05AC') {
            Say '    → 苹果设备：USB 层只能拿到 UDID（上面那串），它不是「序列号」。' 'Yellow'
            Say '      iPhone 序列号：手机上「设置 -> 通用 -> 关于本机」，或装 libimobiledevice、用爱思助手。' 'DarkGray'
            continue
        }
        if (Test-SerialLike $tail) {
            $script:Found.Add([pscustomobject]@{ Value = $tail; Source = "Windows 系统记录 / " + $d.Name + " 的 USB 序列号（多数安卓机等于机身序列号）" })
        }
    }
    Say '  （如果上面列出的是鼠标、键盘之类，忽略即可）' 'DarkGray'
}

# ===========================================================================
# 汇总 + 导出
# ===========================================================================
function Show-Summary {
    Say ''
    Say '======================== 结果 ========================' 'Green'

    $uniq = New-Object System.Collections.Generic.List[object]
    $seen = @{}
    foreach ($f in $script:Found) {
        if (-not $f.Value) { continue }
        $key = $f.Value.ToUpper()
        if (-not $seen.ContainsKey($key)) { $seen[$key] = $true; $uniq.Add($f) }
    }

    if ($uniq.Count -gt 0) {
        Say ("读到 " + $uniq.Count + " 条序列号：") 'Green'
        for ($i = 0; $i -lt $uniq.Count; $i++) {
            Say ("  " + ($i + 1) + ")  " + $uniq[$i].Value) 'White'
            Say ("      来源：" + $uniq[$i].Source)
        }
        try {
            Set-Clipboard -Value $uniq[0].Value
            Say '  （第 1 条已复制到剪贴板，可直接粘贴）' 'Green'
        } catch { }
    } else {
        Say '没有读到序列号，按顺序排查：' 'Yellow'
        Say '  1. 手机用数据线连好，手机上选「传输文件 / MTP」，不要选「仅充电」'
        Say '  2. 换一个 USB 口、换一根线（很多线只能充电、不能传数据）'
        Say '  3. 安卓：设置 -> 关于手机 -> 连点 7 次「版本号」-> 开发者选项 -> 打开「USB 调试」，插线后手机上点「允许」'
        Say '  4. iPhone：插线后在手机上点「信任此电脑」，并保持解锁'
    }
    Say ''
    Say '不用脚本自己查：' 'DarkGray'
    Say '  安卓：设置 -> 关于手机 -> 状态信息 / 全部参数 -> 序列号' 'DarkGray'
    Say '  iPhone：设置 -> 通用 -> 关于本机 -> 序列号' 'DarkGray'
    Say '  不分机型：拨号盘输入 *#06# 看 IMEI；手机包装盒背面的 S/N 就是序列号' 'DarkGray'
}

function Export-Report {
    if ($NoFile) { return }
    $dir = $OutDir
    if (-not $dir) {
        $dir = [Environment]::GetFolderPath('Desktop')
        if (-not $dir -or -not (Test-Path $dir)) { $dir = $PSScriptRoot }
    }
    try {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $file = Join-Path $dir ('手机序列号_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.txt')
        Set-Content -Path $file -Value $script:Lines -Encoding UTF8
        Write-Host ''
        Write-Host ('已导出文件：' + $file) -ForegroundColor Green
    } catch {
        Write-Host ('导出文件失败：' + $_.Exception.Message) -ForegroundColor Red
    }
}

# ===========================================================================
# 主流程
# ===========================================================================
Say '==========================================================='
Say '                手机序列号导出工具（插线即用）'
Say '==========================================================='
Say ('运行时间：' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '    电脑：' + $env:COMPUTERNAME)

Invoke-AdbSection
Invoke-ImobileSection
Invoke-PnpSection
Show-Summary
Export-Report

Say ''
Say '（可以把这个 txt 文件直接发给需要的人）' 'DarkGray'
