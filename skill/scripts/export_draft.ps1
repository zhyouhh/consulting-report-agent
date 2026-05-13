param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"

function Resolve-PandocPath {
    $bundledCandidates = @(
        (Join-Path $PSScriptRoot "..\..\pandoc.exe"),
        (Join-Path $PSScriptRoot "..\..\pandoc\pandoc.exe")
    )

    foreach ($candidate in $bundledCandidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $systemPandoc = Get-Command pandoc -ErrorAction SilentlyContinue
    if ($systemPandoc) {
        return $systemPandoc.Source
    }

    return $null
}

if (-not (Test-Path -LiteralPath $InputPath)) {
    Write-Error "文件不存在: $InputPath"
    exit 1
}

$pandocPath = Resolve-PandocPath
if (-not $pandocPath) {
    Write-Error "未找到 Pandoc：应用包内缺少 _internal\pandoc.exe，且系统 PATH 中也没有 pandoc。请重新安装完整的 dist\咨询报告助手 文件夹，或联系维护者补齐打包资产。"
    exit 1
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$resolvedInput = (Resolve-Path -LiteralPath $InputPath).Path
$baseName = [System.IO.Path]::GetFileNameWithoutExtension($resolvedInput)
$outputPath = Join-Path $OutputDir ($baseName + ".docx")

& $pandocPath $resolvedInput -o $outputPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "pandoc 导出失败，未生成可审草稿。"
    exit $LASTEXITCODE
}

Write-Host "已生成可审草稿: $outputPath"
Write-Host "说明: 当前产物用于预审和传阅，不替代最终中文排版。"
exit 0
