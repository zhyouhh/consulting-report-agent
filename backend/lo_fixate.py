"""LibreOffice 页码 oracle（独立脚本，由 report_tools 以「有 uno 的 python」子进程调用）。

用法：python3 lo_fixate.py SOFFICE_BIN IN.docx OUT.json PROFILE_DIR

打开 IN.docx（headless），在内存里把目录索引更新到与最终排版一致，然后把「条目文本+页码」
清单写成 OUT.json 交回调用方——**绝不另存/导出文档**。LO 的 docx 导出回写会引入硬伤
（2026-07-17 实测）：未闭合/重名书签 + __RefHeading__ 私有锚点（WPS 点目录报「无法打开
指定的文件」）、显式分页符叠加样式 pageBreakBefore（目录后多一页空白页、页码整体偏 1）、
丢 settings updateFields。所以文档本体一律由调用方（report_tools）基于原始 docx 自行写
目录，本脚本只回答「每个标题排到第几页」。

JSON 形状：{"entries": [{"text": "1.1 客户概况", "page": 3}, ...]}（文档顺序；page 为
最终显示页码——LO 索引条目文本自带 pgNumType 偏移后的显示值）。

设计约束：
- **必须用能 `import uno` 的解释器跑**（系统 python3 装了 python3-uno，或 LibreOffice 自带
  python）——backend venv 没有 uno，故 report_tools 走子进程、不在本进程内 import。
- 不 import backend 任何模块（跨解释器、保持独立）。
- **PROFILE_DIR 由调用方（report_tools）创建并负责最终删除**——本脚本只用不删。调用方以
  `start_new_session=True` 启动本脚本，超时会 killpg 整个进程组（含 soffice 孙进程）+ 删
  PROFILE_DIR，故硬杀路径下的清理归调用方；本脚本只保证「正常/受控异常」路径下 soffice 被
  terminate（避免它比脚本活得久）。
- soffice 启动脚本会 `cd` 回启动 CWD——用 `cwd=PROFILE_DIR`（调用方建的可访问目录），避免
  继承到不可访问 CWD（如 root 会话的 /root）导致启动失败。
- SOFFICE_BIN 由调用方解析后传入（可能是 soffice 或 libreoffice 的绝对路径）——不硬编码。
- 成功 exit 0；任何失败非 0（调用方据此优雅降级、保留未固化的 docx，Word 仍自动更新目录）。
"""
import json
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

# LO 索引条目模型文本：每行「条目文本\t页码」（层级制表符样式下即 TokenTabStop）。
_ENTRY_RE = re.compile(r"^(.*)\t([0-9]+)$")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fixate(soffice_bin: str, src: str, out_json: str, profile_dir: str) -> int:
    src_uri = Path(src).resolve().as_uri()
    profile_uri = Path(profile_dir).resolve().as_uri()
    port = _free_port()
    proc = None
    ctx = None
    try:
        proc = subprocess.Popen(
            [
                soffice_bin, "--headless", "--invisible", "--norestore", "--nologo",
                "--nodefault", "--nofirststartwizard",
                f"-env:UserInstallation={profile_uri}",
                f"--accept=socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=profile_dir,
        )

        import uno
        from com.sun.star.beans import PropertyValue

        def pv(name, value):
            p = PropertyValue()
            p.Name = name
            p.Value = value
            return p

        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", local)
        for _ in range(60):  # 最多等 ~45s：soffice 冷启动 + profile 初始化
            if proc.poll() is not None:
                print("soffice exited before accepting connection", file=sys.stderr)
                return 3
            try:
                ctx = resolver.resolve(
                    f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext")
                break
            except Exception:
                time.sleep(0.75)
        if ctx is None:
            print("cannot connect to soffice", file=sys.stderr)
            return 3

        smgr = ctx.ServiceManager
        desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        doc = desktop.loadComponentFromURL(src_uri, "_blank", 0, (pv("Hidden", True),))
        if doc is None:
            print("load returned null", file=sys.stderr)
            return 4
        try:
            indexes = doc.getDocumentIndexes()
            tocs = []
            for i in range(indexes.getCount()):
                ix = indexes.getByIndex(i)
                ix.update()  # 在内存里把索引更新到最终排版（页码在此产生）
                if ix.supportsService("com.sun.star.text.ContentIndex"):
                    tocs.append(ix)
            if len(tocs) != 1:
                print(f"expect exactly 1 content index, got {len(tocs)}", file=sys.stderr)
                return 6
            entries = []
            for raw_line in tocs[0].getAnchor().getString().splitlines():
                line = raw_line.strip("\r")
                if not line.strip():
                    continue
                m = _ENTRY_RE.match(line)
                if not m:
                    # 任一行不是「文本\t页码」即 fail-closed：宁可降级也不给错页码
                    print(f"unparseable toc entry: {line!r}", file=sys.stderr)
                    return 7
                entries.append({"text": m.group(1), "page": int(m.group(2))})
            if not entries:
                print("toc updated to empty", file=sys.stderr)
                return 8
            Path(out_json).write_text(
                json.dumps({"entries": entries}, ensure_ascii=False),
                encoding="utf-8",
            )
        finally:
            doc.close(False)
        return 0
    except Exception as exc:  # noqa: BLE001 — 任何异常都降级，绝不让导出崩
        print(f"fixate error: {exc}", file=sys.stderr)
        return 5
    finally:
        # 受控退出路径下主动收掉 soffice（硬超时由调用方 killpg 兜底）。
        try:
            if ctx is not None:
                desktop = ctx.ServiceManager.createInstanceWithContext(
                    "com.sun.star.frame.Desktop", ctx)
                desktop.terminate()
        except Exception:
            pass
        if proc is not None:
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=15)
            except Exception:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except Exception:
                    pass


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("usage: lo_fixate.py SOFFICE_BIN IN.docx OUT.json PROFILE_DIR", file=sys.stderr)
        sys.exit(2)
    sys.exit(fixate(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]))
