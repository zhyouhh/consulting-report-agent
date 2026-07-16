"""LibreOffice 目录固化助手（独立脚本，由 report_tools 以「有 uno 的 python」子进程调用）。

用法：python3 lo_fixate.py SOFFICE_BIN IN.docx OUT.docx PROFILE_DIR

打开 IN.docx（headless），把所有目录索引（TOC）更新成静态条目+页码后另存 OUT.docx。
这样 Word 与 WPS 打开都能直接看到完整目录、无需手动更新域（WPS 不认 updateFields 标记，
是本脚本存在的唯一理由）。页脚 PAGE 域等其它字段不受影响、保持动态。

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
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fixate(soffice_bin: str, src: str, out: str, profile_dir: str) -> int:
    src_uri = Path(src).resolve().as_uri()
    out_uri = Path(out).resolve().as_uri()
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
            for i in range(indexes.getCount()):
                indexes.getByIndex(i).update()
            doc.storeToURL(
                out_uri,
                (pv("FilterName", "MS Word 2007 XML"), pv("Overwrite", True)),
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
        print("usage: lo_fixate.py SOFFICE_BIN IN.docx OUT.docx PROFILE_DIR", file=sys.stderr)
        sys.exit(2)
    sys.exit(fixate(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]))
