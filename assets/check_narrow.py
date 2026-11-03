#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
窄屏横向溢出检查（手机能不能正常看）

为什么单独一个脚本：
    `verify.py` 查结构、`run_selftest.py` 跑逻辑，两个都在**桌面宽度**下跑，
    对「手机上整页被撑宽、正文右边被切掉」这类问题完全无感 ——
    它不报错、不抛异常，只是难用。只有真的按窄宽度排版量一次才看得见。

做法：
    在项目目录里临时生成一个 `_narrow_check.html`，里面放几个 `scrolling="no"`
    的 iframe（375 / 390 / 414 / 768），同源加载 index.html，
    逐个读 `documentElement.scrollWidth`，超过视口就判定溢出。
    跑完删掉临时文件（发布目录里不该留它）。

用法：
    python3 check_narrow.py
退出码 0 = 全部通过。
"""

import html
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# 用当前解释器起本地服务即可，不依赖任何特定安装路径
PY = sys.executable
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
WIDTHS = [375, 390, 414, 768]
TMP_HTML = "_narrow_check.html"

PAGE = """<!DOCTYPE html>
<meta charset="utf-8"><title>narrow check</title>
<pre id="OUT">pending</pre>
<script>
var WIDTHS = __WIDTHS__;
var done = {}, out = {};
WIDTHS.forEach(function(w){
  var f = document.createElement('iframe');
  f.setAttribute('scrolling', 'no');
  f.style.cssText = 'width:' + w + 'px;height:600px;border:0;position:absolute;left:-9999px';
  f.onload = function(){
    setTimeout(function(){
      try{
        var d = f.contentDocument;
        var vw = d.documentElement.clientWidth;
        var sw = d.documentElement.scrollWidth;
        var worst = null;
        [].slice.call(d.querySelectorAll('body *')).forEach(function(e){
          var r = e.getBoundingClientRect();
          if (r.width <= vw + 1) return;
          // 表格自身允许在 .table-wrap 内横向滑动，不算页面溢出
          if (e.closest && e.closest('.table-wrap, .lg-wrap')) return;
          if (!worst || r.right > worst.right) {
            var idPart = e.id ? '#' + e.id : (e.dataset && e.dataset.page ? '[data-page=' + e.dataset.page + ']' : '');
            var clsPart = (e.className && typeof e.className === 'string')
              ? '.' + e.className.trim().split(/\\s+/)[0] : '';
            worst = {
              el: e.tagName.toLowerCase() + idPart + clsPart,
              right: Math.round(r.right), width: Math.round(r.width)
            };
          }
        });
        out[w] = {vw: vw, sw: sw, overflow: sw - vw, worst: worst};
      }catch(err){
        out[w] = {error: err.message};
      }
      done[w] = 1;
      if (Object.keys(done).length === WIDTHS.length) {
        document.getElementById('OUT').textContent = JSON.stringify(out);
      }
    }, 9000);
  };
  f.src = 'index.html?v=' + Date.now();
  document.body.appendChild(f);
});
</script>
"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    chrome = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)
    if not chrome:
        print("❌ 本机找不到 Chrome / Edge，无法跑窄屏检查")
        return 2
    if not os.path.exists(os.path.join(HERE, "index.html")):
        print("❌ 没有 index.html，先跑 build.py")
        return 2

    probe = os.path.join(HERE, TMP_HTML)
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write(PAGE.replace("__WIDTHS__", json.dumps(WIDTHS)))

    port = free_port()
    srv = subprocess.Popen([PY, "-m", "http.server", str(port)],
                           cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    dom = ""
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/{TMP_HTML}", timeout=1)
                break
            except Exception:
                time.sleep(0.15)
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--hide-scrollbars", "--user-data-dir=/tmp/wk_narrow_prof",
               "--virtual-time-budget=60000", "--dump-dom",
               f"http://127.0.0.1:{port}/{TMP_HTML}"]
        dom = subprocess.run(cmd, capture_output=True, text=True, timeout=180).stdout
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()
        try:
            os.remove(probe)
        except OSError:
            pass

    m = re.search(r'<pre id="OUT">(.*?)</pre>', dom, re.S)
    if not m or "pending" in m.group(1):
        print("❌ 没拿到测量结果（页面可能没跑起来）")
        return 2
    data = json.loads(html.unescape(m.group(1)))

    print("窄屏横向溢出检查")
    print("=" * 56)
    bad = 0
    for w in WIDTHS:
        it = data.get(str(w)) or {}
        if "error" in it:
            print(f"  ❌ {w}px：{it['error']}")
            bad += 1
            continue
        ov = it["overflow"]
        if ov > 2:
            worst = it.get("worst") or {}
            tip = f"（最宽：{worst.get('el','?')} 右边缘 {worst.get('right','?')}px）" if worst else ""
            print(f"  ❌ {w}px：整页被撑到 {it['sw']}px，超出 {ov}px{tip}")
            bad += 1
        else:
            print(f"  ✅ {w}px：视口 {it['vw']}px，页面宽 {it['sw']}px，无横向溢出")
    print("=" * 56)
    print("✅ 窄屏检查通过" if bad == 0 else f"❌ 有 {bad} 个宽度溢出")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
