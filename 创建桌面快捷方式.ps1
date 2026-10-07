# Run from the extracted portable application directory (UTF-8 BOM for Windows PowerShell 5).
$ErrorActionPreference = 'Stop'
$appPath = Join-Path $PSScriptRoot '声栖.exe'
if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {
    throw '未找到声栖.exe，请将此脚本放在完整解压后的程序目录中运行。'
}
$desktopPath = [Environment]::GetFolderPath('DesktopDirectory')
if (-not $desktopPath) { throw '无法获取当前用户桌面目录。' }
$shortcutPath = Join-Path $desktopPath '声栖.lnk'
if (Test-Path -LiteralPath $shortcutPath) {
    throw '桌面已经有“声栖”快捷方式，未覆盖。请先确认现有快捷方式是否仍需保留。'
}
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcut = $shortcutShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $appPath
$shortcut.WorkingDirectory = $PSScriptRoot
$iconPath = Join-Path $PSScriptRoot '_internal\assets\app.ico'
if (Test-Path -LiteralPath $iconPath -PathType Leaf) {
    $shortcut.IconLocation = $iconPath
} else {
    $shortcut.IconLocation = "$appPath,0"
}
$shortcut.Description = '声栖：本地音视频转文字与音频提取'
$shortcut.Save()
Write-Host "已创建：$shortcutPath"
