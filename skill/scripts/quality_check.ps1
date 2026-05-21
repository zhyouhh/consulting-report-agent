# 咨询报告质量检查脚本 (PowerShell)
# 用途：对 Markdown 报告做非阻断式分级告警 / 生成 S5 AI 味自查报告

param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,

    [string]$OutputPath = "",

    [switch]$DryRun
)

$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

if (-not (Test-Path $FilePath)) {
    Write-Host "[ERROR] 文件不存在: $FilePath" -ForegroundColor Red
    exit 1
}

$rawContent = Get-Content -LiteralPath $FilePath -Raw -Encoding UTF8
if ($null -eq $rawContent) {
    $rawContent = ""
}
$lines = @($rawContent -split "`r?`n")

$transitionWordPattern = "(首先|其次|最后|此外|另外|接下来)"
$emptyAdjectivePattern = "(?<![0-9A-Za-z])(?:非常(?!\s*显著)|极其(?!\s*\d+\s*[%％])|十分|相当)"
$aiStylePattern = "(本章|本报告|后文|下文|进一步看|换言之|总体来看|本质上看|也正因为如此|值得注意的是|需要指出的是|重要的是|必须强调的是|$emptyAdjectivePattern)"
$contentGapPattern = "(XXX|待确认|TBD|TODO|待补|技术规范书|内部材料|AI\s+reference|有待进一步|暂无数据|后续研究|有待考证)"
$numberPattern = "\d+(?:\.\d+)?\s*(亿元|万元|%|％|百分点|家|人|吨|万|亿)"
$sourcePattern = "(来源|数据来源|资料来源|\[DL-|https?://|根据)"
$actionPattern = "(建议|应当|需要|可以|应该|优先|推动|落地|执行|调整|建立|明确|补充)"

$script:SectionId = 0
$script:FindingId = 0
$script:AllFindings = New-Object System.Collections.ArrayList

function New-Section {
    param([string]$Title)

    $script:SectionId += 1
    return [pscustomobject]@{
        Id = $script:SectionId
        Title = $Title
        ActionCount = 0
        BodyLines = 0
        TransitionCount = 0
        TransitionReported = $false
    }
}

function Add-Finding {
    param(
        [object]$Section,
        [int]$LineNumber,
        [string]$Label,
        [string]$Text,
        [string]$Hint
    )

    $script:FindingId += 1
    $finding = [pscustomobject]@{
        Id = $script:FindingId
        SectionId = $Section.Id
        LineNumber = $LineNumber
        Label = $Label
        Text = $Text.Trim()
        Hint = $Hint
    }
    [void]$script:AllFindings.Add($finding)
}

$sections = New-Object System.Collections.ArrayList
$currentSection = New-Section -Title "开篇"
[void]$sections.Add($currentSection)

for ($i = 0; $i -lt $lines.Count; $i++) {
    $line = [string]$lines[$i]
    $lineNumber = $i + 1
    $trimmed = $line.Trim()

    if ($line -match "^\s{0,3}#{1,2}\s+(.+)$") {
        $currentSection = New-Section -Title $Matches[1].Trim()
        [void]$sections.Add($currentSection)
        continue
    }

    if (-not [string]::IsNullOrWhiteSpace($trimmed)) {
        $currentSection.BodyLines += 1
    }

    $actionMatches = [regex]::Matches($line, $actionPattern)
    if ($actionMatches.Count -gt 0) {
        $currentSection.ActionCount += $actionMatches.Count
    }

    if ([regex]::IsMatch($line, $aiStylePattern)) {
        Add-Finding `
            -Section $currentSection `
            -LineNumber $lineNumber `
            -Label "AI 腔" `
            -Text $trimmed `
            -Hint "删掉自我解释、机械过渡或空泛强调，直接写判断和影响。"
    }

    if (-not [string]::IsNullOrWhiteSpace($trimmed)) {
        $transitionMatches = [regex]::Matches($line, $transitionWordPattern)
        if ($transitionMatches.Count -gt 0) {
            $currentSection.TransitionCount += $transitionMatches.Count
            if ($currentSection.TransitionCount -ge 3 -and -not $currentSection.TransitionReported) {
                Add-Finding `
                    -Section $currentSection `
                    -LineNumber $lineNumber `
                    -Label "AI 腔" `
                    -Text $trimmed `
                    -Hint "同章连续机械过渡词过密，改成具体判断或直接推进论证。"
                $currentSection.TransitionReported = $true
            }
        }
    }

    if ([regex]::IsMatch($line, $contentGapPattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
        Add-Finding `
            -Section $currentSection `
            -LineNumber $lineNumber `
            -Label "内容缺失" `
            -Text $trimmed `
            -Hint "补齐或删除占位内容，避免半成品信号进入可审草稿。"
    }

    if ([regex]::IsMatch($line, $numberPattern) -and -not [regex]::IsMatch($line, $sourcePattern)) {
        Add-Finding `
            -Section $currentSection `
            -LineNumber $lineNumber `
            -Label "缺标注" `
            -Text $trimmed `
            -Hint "数字、比例和规模判断后补来源、时间或 data-log 引用。"
    }
}

$soWhatRows = New-Object System.Collections.ArrayList
foreach ($section in $sections) {
    if ($section.Title -eq "开篇" -or $section.BodyLines -eq 0) {
        continue
    }
    $tip = "充分"
    $isLow = $false
    if ($section.ActionCount -eq 0) {
        $tip = "**缺失** — 该章未给任何行动建议"
        $isLow = $true
    } elseif ($section.ActionCount -lt 2) {
        $tip = "偏少，光分析没结论"
        $isLow = $true
    }
    [void]$soWhatRows.Add([pscustomobject]@{
        Section = $section.Title
        ActionCount = $section.ActionCount
        Tip = $tip
        IsLow = $isLow
    })
}

$aiStyleCount = @($script:AllFindings | Where-Object { $_.Label -eq "AI 腔" }).Count
$contentGapCount = @($script:AllFindings | Where-Object { $_.Label -eq "内容缺失" }).Count
$citationGapCount = @($script:AllFindings | Where-Object { $_.Label -eq "缺标注" }).Count
$soWhatLowCount = @($soWhatRows | Where-Object { $_.IsLow }).Count
$estimatedSeconds = ($aiStyleCount * 5) + ($contentGapCount * 10) + ($citationGapCount * 10) + ($soWhatLowCount * 60)
$estimatedMinutes = [int][Math]::Ceiling($estimatedSeconds / 60.0)

$displayFindings = @($script:AllFindings | Sort-Object LineNumber)
$truncated = $false
if ($rawContent.Length -gt 30000 -and $script:AllFindings.Count -gt 100) {
    $displayFindings = @($displayFindings | Select-Object -First 30)
    $truncated = $true
}

function Build-MarkdownReport {
    $displayIds = @{}
    foreach ($finding in $displayFindings) {
        $displayIds[$finding.Id] = $true
    }

    $markdown = New-Object System.Collections.Generic.List[string]
    [void]$markdown.Add("# AI 味自查报告")
    [void]$markdown.Add("")
    [void]$markdown.Add("**自查时间**：$((Get-Date).ToString("o"))")
    [void]$markdown.Add("**自查脚本**：quality_check.ps1 v2")
    [void]$markdown.Add("**检查文件**：content/report_draft_v1.md ($($rawContent.Length) 字)")
    [void]$markdown.Add("")
    [void]$markdown.Add("---")
    [void]$markdown.Add("")
    [void]$markdown.Add("## 按章节排列")
    [void]$markdown.Add("")

    foreach ($section in $sections) {
        if ($section.Title -eq "开篇" -and $section.BodyLines -eq 0) {
            continue
        }
        [void]$markdown.Add("### $($section.Title)")
        $sectionFindings = @($displayFindings | Where-Object { $_.SectionId -eq $section.Id -and $displayIds.ContainsKey($_.Id) })
        if ($sectionFindings.Count -eq 0) {
            [void]$markdown.Add("无命中")
        } else {
            foreach ($finding in $sectionFindings) {
                $safeText = $finding.Text -replace '`', "'"
                [void]$markdown.Add(('- **行 {0}** `{1}` `{2}` → {3}' -f $finding.LineNumber, $finding.Label, $safeText, $finding.Hint))
            }
        }
        [void]$markdown.Add("")
    }

    [void]$markdown.Add("---")
    [void]$markdown.Add("")
    [void]$markdown.Add("## 章节 So What 密度")
    [void]$markdown.Add("")
    [void]$markdown.Add("| 章节 | 行动词数 | 提示 |")
    [void]$markdown.Add("|---|---|---|")
    if ($soWhatRows.Count -eq 0) {
        [void]$markdown.Add("| 无章节 | 0 | 未检测到 H1/H2 正文章节 |")
    } else {
        foreach ($row in $soWhatRows) {
            [void]$markdown.Add("| $($row.Section) | $($row.ActionCount) | $($row.Tip) |")
        }
    }
    [void]$markdown.Add("")
    [void]$markdown.Add("---")
    [void]$markdown.Add("")
    [void]$markdown.Add("## 总览")
    [void]$markdown.Add("")
    [void]$markdown.Add("- AI 腔：$aiStyleCount 处")
    [void]$markdown.Add("- 内容缺失：$contentGapCount 处")
    [void]$markdown.Add("- 缺标注：$citationGapCount 处")
    [void]$markdown.Add("- 章节 So What 偏少：$soWhatLowCount 章")
    if ($truncated) {
        [void]$markdown.Add("")
        [void]$markdown.Add("**注**：超长报告，仅显示前 30 条 issue")
    }
    [void]$markdown.Add("")
    [void]$markdown.Add("**预估改完所需时间**：约 $estimatedMinutes 分钟")
    [void]$markdown.Add("")
    [void]$markdown.Add("<!-- lint-report:complete -->")

    return ($markdown -join [Environment]::NewLine) + [Environment]::NewLine
}

function Write-LegacyOutput {
    Write-Host "[CHECK] 开始检查报告质量: $FilePath" -ForegroundColor Green
    Write-Host ""

    $legacyGroups = @(
        @{ Level = "高风险"; Label = "内容缺失" },
        @{ Level = "中风险"; Label = "AI 腔" },
        @{ Level = "低风险"; Label = "缺标注" }
    )

    foreach ($group in $legacyGroups) {
        $items = @($script:AllFindings | Where-Object { $_.Label -eq $group.Label })
        if ($items.Count -eq 0) {
            continue
        }
        Write-Host "$($group.Level) | $($group.Label)" -ForegroundColor Yellow
        foreach ($item in $items | Select-Object -First 30) {
            Write-Host "  行 $($item.LineNumber): $($item.Text)"
        }
        Write-Host ""
    }

    if (-not [regex]::IsMatch($rawContent, $sourcePattern)) {
        Write-Host "低风险 | 数据来源提示" -ForegroundColor Cyan
        Write-Host "  未检测到数据来源标注，请人工确认是否需要补充来源和时间。"
        Write-Host ""
    }

    Write-Host "[SUMMARY] 检查摘要" -ForegroundColor Green
    Write-Host "  高风险: $contentGapCount"
    Write-Host "  中风险: $aiStyleCount"
    Write-Host "  低风险: $($citationGapCount + $soWhatLowCount)"
    Write-Host ""
    Write-Host "[OK] 检查完成（内容问题默认不阻断流程）" -ForegroundColor Green
}

$markdownReport = Build-MarkdownReport

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    if ($DryRun) {
        Write-Output $markdownReport
    } else {
        Write-LegacyOutput
    }
    exit 0
}

if ($DryRun) {
    Write-Output $markdownReport
    exit 0
}

$outputParent = Split-Path -Parent $OutputPath
if (-not [string]::IsNullOrWhiteSpace($outputParent)) {
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
}
Set-Content -LiteralPath $OutputPath -Value $markdownReport -Encoding UTF8
Write-Host "[OK] 已生成 AI 味自查报告: $OutputPath" -ForegroundColor Green
exit 0
