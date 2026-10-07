# UTF-8 BOM keeps Chinese messages readable in Windows PowerShell 5.
$ErrorActionPreference = 'Stop'
$rootPath = [IO.Path]::GetFullPath($PSScriptRoot)
$manifestPath = Join-Path $rootPath 'package-integrity.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw '未找到完整性清单。' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema -ne 1 -or $manifest.files.Count -eq 0) { throw '清单格式错误或为空。' }
$badCount = 0
foreach ($entry in $manifest.files) {
    $filePath = [IO.Path]::GetFullPath((Join-Path $rootPath $entry.path))
    if (-not $filePath.StartsWith($rootPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw '清单含有越界路径，停止校验。'
    }
    if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
        Write-Host "缺失：$($entry.path)"; $badCount++; continue
    }
    $actual = (Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash
    if ($actual -ne $entry.sha256 -or (Get-Item -LiteralPath $filePath).Length -ne $entry.bytes) {
        Write-Host "内容变化：$($entry.path)"; $badCount++
    }
}
if ($badCount -gt 0) { throw "发现 $badCount 个文件缺失或变化，请重新取得完整程序包。" }
Write-Host "校验通过：$($manifest.files.Count) 个清单内文件完整。"
Write-Host '此检查不验证发布者身份，也不检查清单外文件；仅用于发现缺失或损坏。'
