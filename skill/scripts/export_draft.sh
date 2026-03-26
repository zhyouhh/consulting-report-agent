#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "用法: bash scripts/export_draft.sh <输入.md> <输出目录>" >&2
  exit 1
fi

input_path="$1"
output_dir="$2"

if [[ ! -f "$input_path" ]]; then
  echo "文件不存在: $input_path" >&2
  exit 1
fi

if ! command -v pandoc >/dev/null 2>&1; then
  echo "未找到 pandoc，请先安装 pandoc 后再导出可审草稿。" >&2
  exit 1
fi

mkdir -p "$output_dir"

base_name="$(basename "$input_path" .md)"
output_path="$output_dir/${base_name}.docx"

pandoc "$input_path" -o "$output_path"

echo "已生成可审草稿: $output_path"
echo "说明: 当前产物用于预审和传阅，不替代最终中文排版。"
