#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一句话搭好「新手行情看板」：铺文件 → 抓数据 → 构建 → 自检 → 打开。

用法：
    python3 make_dashboard.py                       # 生成到 ./行情看板 并自动打开
    python3 make_dashboard.py --dir ~/Desktop/行情看板
    python3 make_dashboard.py --no-fetch            # 不抓数据，直接用出厂快照
    python3 make_dashboard.py --no-open             # 只生成，不打开浏览器（自测用）

设计原则：
  · **永远不要因为抓数失败就不给结果**：抓不到就用出厂快照，页面打开后仍会自己拉实时。
  · 不用开终端：生成完自动起本地服务并打开浏览器。
  · 以后再想看，双击目录里的「启动看板」就行，不用重新生成。
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")

FILES = ["build.py", "fetch_public.py", "verify.py", "check_narrow.py"]
PORT = 8765

MAC_LAUNCHER = """#!/bin/bash
cd "$(dirname "$0")" || exit 1
PORT=__PORT__
while lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT+1))
done
PY=""
for c in "__PYHINT__" python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 \\
         "$HOME/.workbuddy/binaries/python/envs/default/bin/python" \\
         "$HOME/.workbuddy/binaries/python/versions/3.13.12/bin/python3"; do
  if [ -x "$c" ] || command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "没找到 python3。把这一屏截图发给老师即可。"
  read -r _
  exit 1
fi
URL="http://localhost:$PORT/index.html"
echo ""
echo "  新手行情看板正在打开：$URL"
echo ""
echo "  ⚠️ 这个窗口不要关——关了页面就打不开了。"
echo "     看完直接关掉这个窗口就行，你填的数据已经存在浏览器里。"
echo ""
echo "  换电脑 / 想在手机上看：在「我的账本」点「备份到网上」，"
echo "  按网页里的提示点一次导入就搬过去了。"
echo ""
( sleep 1; open "$URL" 2>/dev/null ) &
exec "$PY" -m http.server "$PORT" --bind 127.0.0.1
"""

WIN_LAUNCHER = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PORT=__PORT__"
rem 优先用生成这份看板的那支 python（WorkBuddy 自带），再退到系统里的
set "PY="
call :try "__PYHINT__"
call :try "python"
call :try "py"
if not defined PY (
  echo 没找到能用的 python。把这一屏截图发给老师即可。
  pause
  exit /b 1
)
echo.
echo   新手行情看板正在打开：http://localhost:%PORT%/index.html
echo.
echo   [!] 这个窗口不要关——关了页面就打不开了。
echo       看完直接关掉这个窗口就行，你填的数据已经存在浏览器里。
echo.
echo   换电脑 / 想在手机上看：在「我的账本」点「备份到网上」，
echo   按网页里的提示点一次导入就搬过去了。
echo.
start "" /b cmd /c "ping -n 3 127.0.0.1 >nul & start http://localhost:%PORT%/index.html"
"%PY%" -m http.server %PORT% --bind 127.0.0.1
goto :eof

:try
if defined PY goto :eof
"%~1" --version >nul 2>nul
if errorlevel 1 goto :eof
set "PY=%~1"
goto :eof
"""


def run(cmd, cwd, timeout=180):
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or ""), time.time() - t0


def write_launchers(out):
    """生成双击即用的启动器。没有它们，刚上手的人就得自己开终端输命令。

    ⚠️ 生成时把「跑这份脚本的 python」绝对路径写进去：
       Windows 上 `where python` 常常命中应用商店的占位符，双击后只会弹一个商店页面。
    """
    hint = (sys.executable or "").strip()
    made = []
    mac = os.path.join(out, "启动看板.command")
    with open(mac, "w", encoding="utf-8", newline="\n") as f:
        f.write(MAC_LAUNCHER.replace("__PORT__", str(PORT)).replace("__PYHINT__", hint))
    os.chmod(mac, os.stat(mac).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    made.append(mac)

    win = os.path.join(out, "启动看板.bat")
    with open(win, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(WIN_LAUNCHER.replace("__PORT__", str(PORT)).replace("__PYHINT__", hint))
    made.append(win)
    return made


def open_dashboard(out):
    """用系统默认方式跑启动器：Mac 会弹出终端窗口，Win 会弹出命令行窗口。"""
    if sys.platform == "darwin":
        subprocess.Popen(["open", os.path.join(out, "启动看板.command")])
        return True
    if os.name == "nt":
        subprocess.Popen(["cmd", "/c", "start", "", os.path.join(out, "启动看板.bat")],
                         shell=False)
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="行情看板", help="生成到哪个目录（默认 ./行情看板）")
    ap.add_argument("--no-fetch", action="store_true", help="跳过抓数，直接用出厂快照")
    ap.add_argument("--no-open", action="store_true", help="生成后不自动打开浏览器")
    ap.add_argument("--python", default=sys.executable or "python3")
    args = ap.parse_args()

    out = os.path.abspath(os.path.expanduser(args.dir))
    os.makedirs(os.path.join(out, "data"), exist_ok=True)

    # ① 铺文件（不覆盖已有的 raw.json —— 那可能是上一次抓的）
    for f in FILES:
        shutil.copy2(os.path.join(ASSETS, f), os.path.join(out, f))
    raw = os.path.join(out, "data", "raw.json")
    if not os.path.exists(raw):
        shutil.copy2(os.path.join(ASSETS, "data", "raw.json"), raw)
        print(f"[1/4] 出厂快照已铺好（{os.path.getsize(raw)//1024} KB）")
    else:
        print("[1/4] 已有 data/raw.json，保留不动（要重新抓数就删掉它）")

    # ② 抓数据（失败不阻断）
    if args.no_fetch:
        print("[2/4] 按参数跳过抓数，使用现有快照")
    else:
        print("[2/4] 正在抓公开行情数据（最多等 3 分钟，失败会自动退回快照）…")
        bak = raw + ".bak"
        shutil.copy2(raw, bak)
        code, log, dur = run([args.python, "fetch_public.py"], out, timeout=200)
        ok = code == 0 and os.path.exists(raw)
        if ok:
            print(f"      抓数完成（{dur:.0f} 秒）")
            os.remove(bak)
        else:
            shutil.copy2(bak, raw)
            os.remove(bak)
            print(f"      抓数没成功（{dur:.0f} 秒），已退回出厂快照 —— 不影响生成，")
            print("      页面打开后仍会自己尝试拉实时数据。")

    # ③ 构建（--no-cloud：这份不带云端同步，数据只存本机）
    print("[3/4] 正在生成 index.html…")
    code, log, _ = run([args.python, "build.py", "--raw", "data/raw.json",
                        "--out", "index.html", "--no-cloud"], out)
    if code != 0:
        print("      生成失败：\n" + log.strip()[-1500:])
        return 2
    for line in log.strip().splitlines():
        if line.startswith("[build]"):
            print("      " + line)

    write_launchers(out)

    # ④ 自检
    print("[4/4] 正在自检…")
    code, log, _ = run([args.python, "verify.py"], out)
    tail = [l for l in log.strip().splitlines() if "❌" in l or "全部自检通过" in l]
    for l in tail[:12]:
        print("      " + l.strip())

    index = os.path.join(out, "index.html")
    size = os.path.getsize(index) / 1024
    print("\n" + "=" * 52)
    print(("✅ 看板做好了" if code == 0 else "⚠️ 做好了，但自检有失败项（见上）"))
    print(f"   文件：{index}（{size:.0f} KB）")

    if args.no_open:
        print("\n（按参数不自动打开。手动打开：双击同目录下的「启动看板」）")
    else:
        opened = open_dashboard(out)
        if opened:
            print("\n👉 已经帮你打开了。会弹出一个黑色/蓝色的窗口，**那个窗口别关**。")
            print("   浏览器如果没自动跳出来，手动打开 http://localhost:%d/index.html" % PORT)
        else:
            print("\n请双击同目录下的「启动看板」来打开。")

    print("\n记住两件事：")
    print("   1. 以后想看，双击目录里的「启动看板」就行，不用重新生成。")
    print("   2. 你填的持仓和账本只存在这台电脑的这个浏览器里——")
    print("      换电脑、换浏览器或清缓存都会丢。")
    print("      · 想搬到别的设备：在「我的账本」点「备份到网上」，")
    print("        打开的网页里点一次「确认导入」，那边就有了。")
    print("      · 想留一份在手边：点「导出我的数据」，存成文件。")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
