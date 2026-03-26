#!/bin/bash

# 咨询报告质量检查脚本
# 用途：对 Markdown 报告做非阻断式分级告警

if [ $# -eq 0 ]; then
    echo "❌ 文件不存在: 未提供文件路径"
    exit 1
fi

FILE="$1"

if [ ! -f "$FILE" ]; then
    echo "❌ 文件不存在: $FILE"
    exit 1
fi

HIGH_RISK=0
MEDIUM_RISK=0
LOW_RISK=0

echo "🔍 开始检查报告质量: $FILE"
echo ""

show_finding() {
    local level="$1"
    local title="$2"
    local pattern="$3"
    local hint="$4"
    local matches

    matches=$(grep -n -E "$pattern" "$FILE" || true)
    if [ -z "$matches" ]; then
        return
    fi

    echo "$level | $title"
    echo "$matches" | while IFS= read -r line; do
        echo "  $line"
    done
    echo "  建议: $hint"
    echo ""

    local count
    count=$(printf "%s\n" "$matches" | grep -c . || true)
    case "$level" in
        高风险) HIGH_RISK=$((HIGH_RISK + count)) ;;
        中风险) MEDIUM_RISK=$((MEDIUM_RISK + count)) ;;
        低风险) LOW_RISK=$((LOW_RISK + count)) ;;
    esac
}

show_low_risk_warning() {
    local title="$1"
    local message="$2"
    echo "低风险 | $title"
    echo "  $message"
    echo ""
    LOW_RISK=$((LOW_RISK + 1))
}

show_finding "高风险" "元叙事表达" "(本章|本报告|后文|下文|进一步看|换言之|总体来看|本质上看|也正因为如此)" "删除解释报告自己的句子，改成直接陈述判断、发现和动作。"
show_finding "高风险" "后台推进表述" "(技术规范书|内部材料|内部资料|AI reference|AI材料|技规)" "删除项目推进过程中的后台词汇，正文只保留面向客户的表达。"
show_finding "高风险" "占位符或待补项" "(XXX|待确认|TBD|TODO|待补)" "补齐或删除占位内容，避免半成品信号进入正式稿。"

show_finding "中风险" "机械过渡词" "(首先|其次|最后|此外|另外|接下来)" "用因果、对比、递进等语义衔接替代机械连接词。"
show_finding "中风险" "空洞强调句" "(值得注意的是|需要指出的是|重要的是|必须强调的是)" "直接给出判断和影响，不要先做空泛强调。"
show_finding "中风险" "空洞形容词" "(非常|极其|十分|相当)" "优先用数据、范围、影响程度替代空洞形容词。"

SOURCE=$(grep -n -E "(根据|来源：|数据来源|资料来源)" "$FILE" || true)
if [ -z "$SOURCE" ]; then
    show_low_risk_warning "数据来源提示" "未检测到数据来源标注，请人工确认是否需要补充来源和时间。"
fi

STRUCTURE=$(grep -n -E "(执行摘要|附录|术语与定义)" "$FILE" || true)
if [ -n "$STRUCTURE" ]; then
    show_low_risk_warning "结构敏感项提示" "检测到“执行摘要 / 附录 / 术语与定义”等结构词，请人工确认是否符合当前项目结构。"
fi

echo "📝 检查图表编号连续性..."
FIGURE_NUMS=$(grep -o "图[0-9]\+" "$FILE" | grep -o "[0-9]\+" | sort -n -u || true)
if [ -n "$FIGURE_NUMS" ]; then
    PREV=0
    GAP_FOUND=false
    for NUM in $FIGURE_NUMS; do
        if [ $PREV -ne 0 ] && [ $((NUM - PREV)) -gt 1 ]; then
            echo "低风险 | 图表编号提示"
            echo "  图编号不连续：图$PREV -> 图$NUM"
            echo ""
            LOW_RISK=$((LOW_RISK + 1))
            GAP_FOUND=true
        fi
        PREV=$NUM
    done
    if [ "$GAP_FOUND" = false ]; then
        echo "✅ 图编号连续"
        echo ""
    fi
else
    echo "   未发现图编号"
    echo ""
fi

echo "📝 检查表格编号连续性..."
TABLE_NUMS=$(grep -o "表[0-9]\+" "$FILE" | grep -o "[0-9]\+" | sort -n -u || true)
if [ -n "$TABLE_NUMS" ]; then
    PREV=0
    GAP_FOUND=false
    for NUM in $TABLE_NUMS; do
        if [ $PREV -ne 0 ] && [ $((NUM - PREV)) -gt 1 ]; then
            echo "低风险 | 表格编号提示"
            echo "  表编号不连续：表$PREV -> 表$NUM"
            echo ""
            LOW_RISK=$((LOW_RISK + 1))
            GAP_FOUND=true
        fi
        PREV=$NUM
    done
    if [ "$GAP_FOUND" = false ]; then
        echo "✅ 表编号连续"
        echo ""
    fi
else
    echo "   未发现表格编号"
    echo ""
fi

KEY_FINDINGS=$(grep -n -E "(发现|显示|表明|数据显示)" "$FILE" || true)
ACTION_WORDS=$(grep -c -E "(建议|应当|需要|可以|应该)" "$FILE" || true)
if [ -n "$KEY_FINDINGS" ] && [ "$ACTION_WORDS" -lt 3 ]; then
    show_low_risk_warning "So What 提示" "检测到关键发现表述，但行动建议偏少，请人工确认是否补充管理含义和动作。"
fi

echo "📊 检查摘要"
echo "  高风险: $HIGH_RISK"
echo "  中风险: $MEDIUM_RISK"
echo "  低风险: $LOW_RISK"
echo ""
echo "✅ 检查完成（内容问题默认不阻断流程）"
exit 0
