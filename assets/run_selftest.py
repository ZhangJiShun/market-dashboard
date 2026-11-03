#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键跑浏览器自检：生成 selftest.html → 起本地服务 → Chrome 无头跑 → 抓断言结果。

为什么要起服务：`file://` 下 Chrome 拒绝 localStorage，本地存储相关断言会假失败。

用法：
    python3 run_selftest.py
退出码 0 = 全部通过。
"""

import html
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# 用当前解释器跑自检与本地服务，不依赖任何特定安装路径
PY = sys.executable
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    chrome = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)
    if not chrome:
        print("❌ 本机找不到 Chrome / Edge，无法跑浏览器自检")
        return 2

    # 1) 生成自检页
    r = subprocess.run([PY, os.path.join(HERE, "selftest.py")], capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        return r.returncode

    # 2) 起本地服务
    port = free_port()
    srv = subprocess.Popen([PY, "-m", "http.server", str(port)],
                           cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    dom, ok = "", False
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/selftest.html", timeout=1)
                ok = True
                break
            except Exception:
                time.sleep(0.15)
        if not ok:
            print("❌ 本地服务没起来")
            return 2

        # 3) Chrome 无头跑
        prof = "/tmp/wk_selftest_prof"
        shutil.rmtree(prof, ignore_errors=True)
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--user-data-dir=" + prof, "--virtual-time-budget=150000", "--dump-dom",
               f"http://127.0.0.1:{port}/selftest.html"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        dom = p.stdout
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()
        # 4) 清理测试产物（发布目录里不该留它）
        try:
            os.remove(os.path.join(HERE, "selftest.html"))
        except OSError:
            pass

    m = re.search(r'<pre id="TESTOUT">(.*?)</pre>', dom, re.S)
    if not m:
        print("❌ 没抓到测试输出（页面可能没跑起来）")
        return 2
    lines = [l for l in html.unescape(m.group(1)).split("\n") if " | " in l]
    npass = sum(1 for l in lines if l.startswith("PASS"))
    nfail = sum(1 for l in lines if l.startswith("FAIL"))
    nskip = sum(1 for l in lines if l.startswith("SKIP"))
    for l in lines:
        tag = l.split(" | ", 1)[0]
        mark = {"PASS": "  ✅ ", "FAIL": "  ❌ ", "SKIP": "  ⏭️ "}.get(tag, "  ?  ")
        print(mark + l.split(" | ", 1)[1])
    print("=" * 56)
    tail = f"{npass} / {npass + nfail}" + (f"（跳过 {nskip} 项）" if nskip else "")
    print(("✅ 全部通过 " if nfail == 0 else "❌ 有失败项 ") + tail)
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
