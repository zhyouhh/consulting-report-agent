param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $InputPath)) {
    Write-Error "文件不存在: $InputPath"
    exit 1
}

$pandoc = Get-Command pandoc -ErrorAction SilentlyContinue
if (-not $pandoc) {
    Write-Error "未找到 pandoc，请先安装 pandoc 后再导出可审草稿。"
    exit 1
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$resolvedInput = (Resolve-Path -LiteralPath $InputPath).Path
$baseName = [System.IO.Path]::GetFileNameWithoutExtension($resolvedInput)
$outputPath = Join-Path $OutputDir ($baseName + ".docx")

& $pandoc.Source $resolvedInput -o $outputPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "pandoc 导出失败，未生成可审草稿。"
    exit $LASTEXITCODE
}

Write-Host "已生成可审草稿: $outputPath"
Write-Host "说明: 当前产物用于预审和传阅，不替代最终中文排版。"
exit 0
