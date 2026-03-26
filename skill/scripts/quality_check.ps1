# 咨询报告质量检查脚本 (PowerShell)
# 用途：对 Markdown 报告做非阻断式分级告警

param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath
)

if (-not (Test-Path $FilePath)) {
    Write-Host "❌ 文件不存在: $FilePath" -ForegroundColor Red
    exit 1
}

$content = Get-Content $FilePath -Encoding UTF8

$checks = @(
    @{
        Level = "高风险"
        Title = "元叙事表达"
        Pattern = "(本章|本报告|后文|下文|进一步看|换言之|总体来看|本质上看|也正因为如此)"
        Hint = "删除解释报告自己的句子，改成直接陈述判断、发现和动作。"
    },
    @{
        Level = "高风险"
        Title = "后台推进表述"
        Pattern = "(技术规范书|内部材料|内部资料|AI reference|AI材料|技规)"
        Hint = "删除项目推进过程中的后台词汇，正文只保留面向客户的表达。"
    },
    @{
        Level = "高风险"
        Title = "占位符或待补项"
        Pattern = "(XXX|待确认|TBD|TODO|待补)"
        Hint = "补齐或删除占位内容，避免半成品信号进入正式稿。"
    },
    @{
        Level = "中风险"
        Title = "机械过渡词"
        Pattern = "(首先|其次|最后|此外|另外|接下来)"
        Hint = "用因果、对比、递进等语义衔接替代机械连接词。"
    },
    @{
        Level = "中风险"
        Title = "空洞强调句"
        Pattern = "(值得注意的是|需要指出的是|重要的是|必须强调的是)"
        Hint = "直接给出判断和影响，不要先做空泛强调。"
    },
    @{
        Level = "中风险"
        Title = "空洞形容词"
        Pattern = "(非常|极其|十分|相当)"
        Hint = "优先用数据、范围、影响程度替代空洞形容词。"
    }
)

function Show-Finding {
    param(
        [string]$Level,
        [string]$Title,
        [string]$Pattern,
        [string]$Hint
    )

    $matches = Select-String -Path $FilePath -Pattern $Pattern -AllMatches
    if (-not $matches) {
        return 0
    }

    Write-Host "$Level | $Title" -ForegroundColor Yellow
    $matches | ForEach-Object { Write-Host "  行 $($_.LineNumber): $($_.Line.Trim())" }
    Write-Host "  建议: $Hint"
    Write-Host ""
    return $matches.Count
}

function Show-LowRiskWarning {
    param(
        [string]$Title,
        [string]$Message
    )

    Write-Host "低风险 | $Title" -ForegroundColor Cyan
    Write-Host "  $Message"
    Write-Host ""
}

Write-Host "🔍 开始检查报告质量: $FilePath" -ForegroundColor Green
Write-Host ""

$summary = @{
    "高风险" = 0
    "中风险" = 0
    "低风险" = 0
}

foreach ($check in $checks) {
    $count = Show-Finding -Level $check.Level -Title $check.Title -Pattern $check.Pattern -Hint $check.Hint
    $summary[$check.Level] += $count
}

$sourceMatches = Select-String -Path $FilePath -Pattern "(根据|来源：|数据来源|资料来源)" -AllMatches
if (-not $sourceMatches) {
    Show-LowRiskWarning -Title "数据来源提示" -Message "未检测到数据来源标注，请人工确认是否需要补充来源和时间。"
    $summary["低风险"] += 1
}

$structureMatches = Select-String -Path $FilePath -Pattern "(执行摘要|附录|术语与定义)" -AllMatches
if ($structureMatches) {
    Show-LowRiskWarning -Title "结构敏感项提示" -Message "检测到“执行摘要 / 附录 / 术语与定义”等结构词，请人工确认是否符合当前项目结构。"
    $summary["低风险"] += $structureMatches.Count
}

Write-Host "📝 检查图表编号连续性..." -ForegroundColor Cyan
$figureMatches = Select-String -Path $FilePath -Pattern "图(\d+)" -AllMatches
if ($figureMatches) {
    $figureNums = $figureMatches | ForEach-Object { $_.Matches.Groups[1].Value } | Sort-Object { [int]$_ } -Unique
    $prev = 0
    $gapFound = $false
    foreach ($num in $figureNums) {
        $numInt = [int]$num
        if ($prev -ne 0 -and ($numInt - $prev) -gt 1) {
            Write-Host "低风险 | 图表编号提示" -ForegroundColor Cyan
            Write-Host "  图编号不连续：图$prev -> 图$numInt"
            Write-Host ""
            $summary["低风险"] += 1
            $gapFound = $true
        }
        $prev = $numInt
    }
    if (-not $gapFound) {
        Write-Host "✅ 图编号连续" -ForegroundColor Green
        Write-Host ""
    }
} else {
    Write-Host "   未发现图编号"
    Write-Host ""
}

Write-Host "📝 检查表格编号连续性..." -ForegroundColor Cyan
$tableMatches = Select-String -Path $FilePath -Pattern "表(\d+)" -AllMatches
if ($tableMatches) {
    $tableNums = $tableMatches | ForEach-Object { $_.Matches.Groups[1].Value } | Sort-Object { [int]$_ } -Unique
    $prev = 0
    $gapFound = $false
    foreach ($num in $tableNums) {
        $numInt = [int]$num
        if ($prev -ne 0 -and ($numInt - $prev) -gt 1) {
            Write-Host "低风险 | 表格编号提示" -ForegroundColor Cyan
            Write-Host "  表编号不连续：表$prev -> 表$numInt"
            Write-Host ""
            $summary["低风险"] += 1
            $gapFound = $true
        }
        $prev = $numInt
    }
    if (-not $gapFound) {
        Write-Host "✅ 表编号连续" -ForegroundColor Green
        Write-Host ""
    }
} else {
    Write-Host "   未发现表格编号"
    Write-Host ""
}

$keyFindings = Select-String -Path $FilePath -Pattern "(发现|显示|表明|数据显示)" -AllMatches
$actionWords = (Select-String -Path $FilePath -Pattern "(建议|应当|需要|可以|应该)" -AllMatches).Count
if ($keyFindings -and $actionWords -lt 3) {
    Show-LowRiskWarning -Title "So What 提示" -Message "检测到关键发现表述，但行动建议偏少，请人工确认是否补充管理含义和动作。"
    $summary["低风险"] += 1
}

Write-Host "📊 检查摘要" -ForegroundColor Green
Write-Host "  高风险: $($summary['高风险'])"
Write-Host "  中风险: $($summary['中风险'])"
Write-Host "  低风险: $($summary['低风险'])"
Write-Host ""

Write-Host "✅ 检查完成（内容问题默认不阻断流程）" -ForegroundColor Green
exit 0
