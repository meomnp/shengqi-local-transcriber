param(
    [Parameter(Mandatory = $true)]
    [string]$PackageDirectory
)

$ErrorActionPreference = 'Stop'
$packagePath = (Resolve-Path -LiteralPath $PackageDirectory).Path
$exePath = Join-Path $packagePath '声栖.exe'
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "Target is not a Shengqi Windows app directory: $packagePath"
}

function Convert-CodePointsToName([int[]]$CodePoints, [string]$Extension) {
    return ((-join ([char[]]$CodePoints)) + $Extension)
}

$packageFiles = @(
    @{ Name = (Convert-CodePointsToName @(0x58F0, 0x6816, 0x005F, 0x65B0, 0x5305, 0x4F7F, 0x7528, 0x8BF4, 0x660E) '.md') },
    @{ Name = (Convert-CodePointsToName @(0x521B, 0x5EFA, 0x684C, 0x9762, 0x5FEB, 0x6377, 0x65B9, 0x5F0F) '.ps1') },
    @{ Name = (Convert-CodePointsToName @(0x6821, 0x9A8C, 0x7A0B, 0x5E8F, 0x5B8C, 0x6574, 0x6027) '.ps1') }
)

foreach ($item in $packageFiles) {
    $name = $item.Name
    $sourcePath = Join-Path $PSScriptRoot $name
    $targetPath = Join-Path $packagePath $name
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Required package input is missing: $sourcePath"
    }
    if (Test-Path -LiteralPath $targetPath -PathType Leaf) {
        $sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
        $targetHash = (Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash
        if ($sourceHash -ne $targetHash) {
            throw "Existing package file differs; refusing to overwrite: $targetPath"
        }
        continue
    }
    Copy-Item -LiteralPath $sourcePath -Destination $targetPath
}

Write-Host "User guide, desktop shortcut helper and integrity checker are ready: $packagePath"
