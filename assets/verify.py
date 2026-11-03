#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新手行情看板 · 离线结构自检（v3 · 两页分版 + 图表交互）

数值与交互断言在 run_selftest.py（真实浏览器）里做，这里只做**不需要渲染引擎**的
确定性检查：结构、完整性、依赖、口径红线。

为什么要有这一层：浏览器自检要起服务 + 起 Chrome（慢）；这一层秒级、可反复跑，
用来在改代码之后先挡住低级错误。
"""

import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(HERE, "index.html")
RAW = os.path.join(HERE, "data", "raw.json")

fails, warns = [], []


def check(name, ok, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + name + (("  — " + detail) if detail else ""))
    if not ok:
        fails.append(name)


def main():
    with open(HTML, "r", encoding="utf-8") as f:
        s = f.read()
    with open(RAW, "r", encoding="utf-8") as f:
        raw = json.load(f)

    print("【1】构建完整性")
    check("占位符 __RAW__ 已替换", "__RAW__" not in s)
    check("HTML 尺寸合理（>100 KB）", len(s.encode()) > 100 * 1024, f"{len(s.encode())/1024:.1f} KB")
    # ⚠️ 内联 JS 语法检查：重复 const 声明这类错会让整页 JS 全废（页面卡在「正在加载」），
    #    浏览器只会丢出一句 classList of undefined，肉眼极难定位 —— 用 node --check 兜住。
    js = re.search(r"<script>\n(.*?)\n</script>", s, re.S)
    check("找到内联脚本", js is not None)
    if js:
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            import glob
            cands = sorted(glob.glob(os.path.expanduser(
                "~/.workbuddy/binaries/node/versions/*/bin/node")))
            node = cands[0] if cands else "node"
        tmp_js = "/tmp/_wb_inline_check.js"
        with open(tmp_js, "w", encoding="utf-8") as f:
            f.write(js.group(1))
        try:
            r = subprocess.run([node, "--check", tmp_js], capture_output=True, text=True, timeout=30)
            err = (r.stderr or "").strip().splitlines()
            check("内联 JS 语法检查通过（node --check）", r.returncode == 0,
                  (err[0][:140] if err else ""))
        except Exception as e:
            check("内联 JS 语法检查通过（node --check）", False, str(e)[:120])

    print("\n【2】内嵌快照可解析且完整")
    m = re.search(r"const SNAPSHOT = (\{.*?\});\n", s, re.S)
    check("找到 const SNAPSHOT", m is not None)
    snap = None
    if m:
        try:
            snap = json.loads(m.group(1))
            check("SNAPSHOT 是合法 JSON", True)
        except Exception as e:
            check("SNAPSHOT 是合法 JSON", False, str(e))
    if snap:
        keys = [k for k in snap if k != "_meta"]
        check(f"快照含 38 个接口（实际 {len(keys)}）", len(keys) >= 38)
        for need in ("quote", "breadth", "fflow_min", "fflow_day", "fflow_min_sz", "fflow_day_sz",
                     "sector_in", "sector_out",
                     "sector_up", "sector_down", "concept_up", "stock_in", "stock_chg", "stock_amt",
                     "kday_sh", "kday_sz", "ztpool", "dtpool", "zbpool", "margin", "kline",
                     "trend_sh000001", "trend_sz399001", "trend_sz399006", "trend_sh000300",
                     # 全球市场
                     "gl_core", "gl_world", "gl_hk", "gl_us",
                     "gl_trend_hk", "gl_trend_us", "gl_trend_jp", "gl_kline_hk", "gl_kline_us",
                     # 长周期日K（腾讯源；东财 push2his 2026-10-16 起对本机不可达）
                     "kline_sh000001", "kline_sz399001", "kline_sz399006",
                     # 资讯流（np 系，2026-10-17 实测）
                     "news_724", "news_a_rev", "news_gl_rev"):
            if need not in snap:
                check(f"快照缺 {need}", False)
        check("快照带 tradeDate", bool(snap.get("_meta", {}).get("tradeDate")),
              str(snap.get("_meta", {}).get("tradeDate")))
        check("快照无失败接口", not snap.get("_meta", {}).get("failed"),
              "; ".join(snap.get("_meta", {}).get("failed") or []) or "无")
        # ⚠️ 前端 URLS 必须覆盖快照里的每一个接口。
        #    漏一个的后果不是「少一个数」而是「实时刷新后整块空白」——
        #    fetchLive() 用实时结果重建模型，快照里有而 URLS 里没有的键会直接消失。
        #    2026-10-16 实测踩过：9 个 gl_* 接口没加进 URLS，切到港股/美股/全球全是空的。
        mu = re.search(r"const URLS = \{(.*?)\n\};", s, re.S)
        mp = re.search(r"const POOL_URL = \{(.*?)\n\};", s, re.S)
        check("找到 const URLS（实时拉取清单）", mu is not None)
        if mu:
            front = set(re.findall(r"^\s*([A-Za-z0-9_]+)\s*:", mu.group(1), re.M))
            if mp:
                front |= set(re.findall(r"^\s*([A-Za-z0-9_]+)\s*:", mp.group(1), re.M))
            need = set(k for k in snap if k != "_meta")
            # ⚠️ 这三个是**故意**只在快照里的：资讯接口一天只出一份，实时拉没有意义，
            #    而且 np-weblist / np-listapi 在部分网络里会被浏览器拦成跨域错误。
            #    其他任何接口都必须在 URLS 里，否则实时刷新后那一块会整块消失。
            NEWS_SNAPSHOT_ONLY = {"news_724", "news_a_rev", "news_gl_rev"}
            need = need - NEWS_SNAPSHOT_ONLY
            miss = sorted(need - front)
            check("前端 URLS 覆盖快照全部接口（实时刷新不会丢整块）", not miss,
                  ("缺 " + ", ".join(miss)) if miss else f"{len(front)} 个接口")
            extra = sorted(NEWS_SNAPSHOT_ONLY & front)
            check("资讯接口只走快照（不挂在实时清单里，避免控制台跨域报错）", not extra,
                  ("仍在 URLS 里：" + ", ".join(extra)) if extra else "3 个资讯接口已移出实时清单")

    print("\n【3】页面结构：三页分区 + 三份目录 + 阅读路径")
    # 一级分页：当天的事全放「今天」，过去的事全放「历史与位置」，自己的钱放「我的账本」。
    # 顺序对齐主流交易 App：跟我有关 → 整体涨跌 → 谁在涨 → 钱怎么动 → 定时复盘 → 滚动快讯
    TODAY = ("sec-watch", "sec-global", "sec-senti", "sec-board", "sec-fund", "sec-market")
    HIST = ("sec-h-main", "sec-h-vol", "sec-h-fund", "sec-h-globe")
    BOOK = ("sec-bk-ledger", "sec-bk-pos", "sec-bk-journal")
    for sec in TODAY + HIST + BOOK:
        check(f"分区 id=#{sec}", f'id="{sec}"' in s)
    check("历史段都挂了 data-page=\"hist\"（切页靠它显隐）",
          all(f'data-page="hist" id="{h}"' in s for h in HIST))
    check("账本段都挂了 data-page=\"book\"",
          all(f'data-page="book" id="{b}"' in s for b in BOOK))
    check("旧的第 7 段 #sec-pos 已迁走（旧名残留＝死代码）", 'id="sec-pos"' not in s)
    # 目录条必须与分区一一对应：既不能少一段，也不能指向不存在的锚点。
    # 现在是**每页各一份**，编号在 paintToc 里按实际渲染出来的段重排（不写死）。
    mt = re.search(r"const TOCS = \{(.*?)\n\};", s, re.S)
    check("找到 const TOCS（分页目录配置）", mt is not None)
    if mt:
        blk = {}
        for pg in ("today", "hist", "book"):
            mm = re.search(pg + r"\s*:\s*\[(.*?)\n\s*\]", mt.group(1), re.S)
            blk[pg] = re.findall(r"id:'([a-z0-9\-]+)'", mm.group(1)) if mm else []
        check(f"today 目录 7 项且与今天页分区一致（实际 {len(blk['today'])}）",
              blk["today"] == list(TODAY), " → ".join(blk["today"]))
        check(f"hist 目录 4 项且与历史页分区一致（实际 {len(blk['hist'])}）",
              blk["hist"] == list(HIST), " → ".join(blk["hist"]))
        check(f"book 目录 3 项且与账本页分区一致（实际 {len(blk['book'])}）",
              blk["book"] == list(BOOK), " → ".join(blk["book"]))
        for pg, ids in blk.items():
            for tid in ids:
                check(f"{pg} 目录项 #{tid} 有对应分区", f'id="{tid}"' in s)
        check("目录不写死编号（编号由 paintToc 按可见段重排）",
              not re.search(r"id:'[a-z0-9\-]+',\s*n:'", mt.group(1)) and "'<i>' + (i + 1) + '</i>'" in s)
    check("分页配置 PAGES 含 today / hist / book", "const PAGES" in s and
          all(f"{{k:'{k}'" in s for k in ("today", "hist", "book")))
    check("切页函数 setPage() 写 #root[data-page]",
          "function setPage(" in s and "setAttribute('data-page'" in s)
    check("分页 CSS 用「先全隐、再按当前页显」（加页不用改规则）",
          '#root > [data-page]{display:none}' in s and
          '#root[data-page="book"]  > [data-page="book"]{display:block}' in s)
    check("页签容器 #pageTabs 存在并会被填充", 'id="pageTabs"' in s and "paintPages(" in s)
    check("页签选择持久化（localStorage）", "PAGE_KEY" in s and "savePage(" in s)
    check("目录条元素存在并会被填充", 'id="toc"' in s and "paintToc(" in s)
    check("目录滚动高亮已绑定", "paintTocActive(" in s and "addEventListener('scroll'" in s)
    check("过滤掉未渲染 / 被显示设置关掉的段（目录不空指）",
          "!el.classList.contains('lay-off')" in s and "lay-off" in s)
    # ── 显示内容分层（精简 / 完整 / 自定义）──
    check("显示分层：三档按钮（精简 / 完整 / 自定义）",
          "'lite','精简'" in s and "'full','完整'" in s and "'custom','自定义'" in s)
    check("显示分层：必看三段锁定（持仓 / 情绪 / 复盘）",
          "LOCKED = ['sec-watch', 'sec-senti', 'sec-market']" in s)
    check("显示分层：精简档只收起数据密集的三段",
          "LITE_HIDE" in s and "'sec-global', 'sec-board', 'sec-fund'" in s)
    # ⚠️ 删过一整段（快讯）却忘改「只留 4 段」那句，页面上就多了个不存在的段 ⇒ 段数必须算出来
    check("显示分层：精简档说明的段数由实际段数算出（不写死数字）",
          "TOCS.today.filter(x => LITE_HIDE.indexOf(x.id) < 0).length" in s
          and "只留 4 段" not in s)
    check("显示分层：档位与勾选落本地存储（下次打开还在）",
          "mb_layer_v1" in s and "saveLayer(" in s and "loadLayer(" in s)
    check("显示分层：自定义面板列出可选段 + 标注固定显示段",
          "lay-panel" in s and "lay-item" in s and "lay-lock" in s)
    check("目录按组分隔（可用 toc-sep 计数）", s.count('toc-sep') >= 2, f"{s.count('toc-sep')} 处")
    for fn in ("splitTrend", "flowRows", "mergeFlow", "splitFlow", "normalize", "sessionState", "lineChart",
               "reportAM", "reportPM", "reportAll", "shapeOf", "volBars",
               "fetchLive", "sec3Fund", "secSenti", "secBoard",
               # 资讯流（复盘导读）
               "normNewsData",
               "cellHtml", "initHoldings", "refreshRow", "smartbox", "lookupRow",
               # 全球市场
               "trendLite", "mktState", "usDST", "clockOf", "tzOffMin", "tzDiffText", "gTzBar",
               "secGlobal", "gCard", "gRows", "gRegions", "gBody", "gNote", "repaintGlobal", "bindGlobal",
               "gTrend",
               # 第 3 段：按市场复盘
               "loadReportMkt", "saveReportMkt", "rptState", "idxCards", "mktIdx", "segFull", "selBreadth",
               "rptCn", "rptHkAM", "rptHkPM", "rptHkAll", "rptHk", "rptUs", "rptGl",
               "rTabsHtml", "reportBody", "repaintReport", "bindReport",
               # 分页 / 双份目录
               "loadPage", "savePage", "curToc", "paintPages", "setPage", "paintToc", "paintTocActive",
               # 图表交互（悬停读数）
               "bindChartTips", "showChartTip", "showBarTip", "placeTip", "hideChartTip",
               "chartTipEl", "clearCursor", "klLines", "klSeries",
               # 历史页
               "barChart", "fundBars", "histCard",
               "secHMain", "secHVol", "secHFund", "secHLong",
               # 我的账本（工作台）
               "loadBook", "saveLedger", "saveSnaps", "saveJournal", "saveCash", "todayStr",
               "derivePositions", "bookPositions", "needQuotes",
               "secBookLedger", "secBookPos", "secBookJournal",
               "lgRowHtml", "lgAmt", "lgSummaryHtml", "paintLgDerived", "lgAddRow", "lgDelRow", "lookupLedger",
               "takeSnapshot", "autoSnapshot", "paintBook", "refreshBookTabs", "initBook", "initBookSections",
               "bindBook", "syncToWatch", "snapSeries", "bookTotals"):
        check(f"渲染/计算函数 {fn}()", fn + "(" in s)
    # 全球市场的关键事实：恒生科技在 124 段（不是 100 段）、港美股不用涨幅榜
    check("恒生科技指数用 secid 124.HSTECH", "124.HSTECH" in s)
    check("全球市场 4 个标签（沪深京/港股/美股/全球）",
          all(f"label:'{x}'" in s for x in ("沪深京", "港股", "美股", "全球")))
    check("欧美/中国香港「绿涨红跌」差异已标注",
          "本地习惯是「绿涨红跌」" in html.unescape(s))
    check("美股夏令时/冬令时已区分", "usDST(" in s and "夏令时" in s)
    # 各地时钟必须和「开市状态」绑在同一个格子里（曾经是两排、两套名字，用户看不懂）
    check("各地时间与开市状态合成一格（沪深京/中国香港/美股/日本）",
          all(f"l:'{c}'" in s for c in ("沪深京", "中国香港", "美股", "日本")) and
          'class="tzc ' in s and 'tzc-o' in s)
    check("时差写成「比北京早/晚 N 小时」（不丢数字让人自己换算）",
          "比北京' + (min > 0 ? '早' : '晚')" in s and "'与北京同一时刻'" in s)
    check("时钟有说明文字（说清「没动」不等于没涨）",
          "谁在开市" in html.unescape(s) and "不等于</b>它没涨" in s)
    check("举例里的钟点取自实时值（不写死 18:13 这类字面量）",
          "上面「18:13」" not in s and "tzDiffText(usD)" in s and
          "tzOffMin('America/New_York')" in s)
    # 文案与结构必须同步：页签从两块变三块时，页头那句话最容易留在旧版本
    check("页头说明写的是「三块」（与三个页签一致）",
          "整页分成三块" in html.unescape(s) and "整页分成两块" not in html.unescape(s))
    check("页头三块各自讲了什么（今天 / 历史与位置 / 我的账本）",
          all(w in html.unescape(s) for w in ("「今天」", "「历史与位置」", "「我的账本」")))
    check("原来的两排（城市钟 / 状态灯）已删干净",
          "clock-bar" not in s and "st-chip" not in s and "gStates(" not in s and "gClocks(" not in s)
    # 拉回来的海外分时 / 日K 必须真的画出来，不能只进模型不落地
    check("海外分时数据已接入渲染（m.gtrend 被 gTrend 使用）",
          "m.gtrend.hk" in s and "m.gtrend.us" in s and "m.gtrend.jp" in s)
    check("海外日K已接入历史页（m.gkline 被 secHLong 使用）",
          "m.gkline || {}" in s and "histCard('恒生指数（中国香港）', g.hk)" in s and
          "histCard('道琼斯工业平均（美国）', g.us)" in s)
    # ── 资金流口径：必须是**沪深两市**，不能只剩沪市 ────────────────────
    # 2026-10-16 用户问「主力才净流出 5.6 亿？全天成交不是上万亿吗」——根因有两层：
    #   ① 只取了 secid=1.000001（上证指数＝**沪市**），两市合计其实是 -49.36 亿；
    #   ② 页面没说「净额 ≠ 成交额」。
    # 两条都得有断言守着，否则改着改着很容易再退回去。
    su = html.unescape(s)
    check("资金流同时取沪市与深市两个 secid（1.000001 + 0.399001）",
          "/stock/fflow/kline/get" in s and "/stock/fflow/daykline/get" in s
          and "secid=1.000001" in s and "secid=0.399001" in s)
    check("日级资金用 push2his/daykline（kline 家族对指数只回 1 天）",
          "P2H + '/stock/fflow/daykline/get?secid=1.000001&" in s
          and "P2H + '/stock/fflow/daykline/get?secid=0.399001&" in s)
    check("沪市 + 深市按时间戳**取交集**后逐点相加（两源点数会不一样，不能直接配）",
          "function mergeFlow(" in s and "if(!o) return;" in s)
    check("只有一个源时不冒充两市（如实退回单源并置 both:false）",
          bool(re.search(r"return \{rows: a \|\| b, both:false, src: a \? 'sh' : 'sz'\}", s))
          and bool(re.search(r"return \{rows: a, both:false, src:'sh'\}", s)))
    check("全天口径跟着分时走（否则会出现「上午＋下午 ≠ 全天」）",
          "if(fm && fm.kl && fm.kl.length){" in s and "fromMin:true" in s
          and "全天一律优先用**分时末值**" in s)
    check("页面标出资金流口径（沪深两市 / 沪市 / 深市，不写死）",
          "({sh:'沪市', sz:'深市', both:'沪深两市'})[fm.src]" in s and "m.fund.scope" in s)
    # ⚠️ 下面两条锁的是 2026-11-03 那个 bug：两个字段共用一个 scope，
    #    写出「（只算沪深两市）」这种自相矛盾的话。
    check("口径备注只看分时自己的 src（不能拿历史口径去标当天）",
          "function flowNote(fm){" in s and s.count("+ flowNote(fm)") == 2
          and "m.fund.scope" not in s.split("function flowNote(fm){")[1].split("}")[0])
    check("历史柱状图口径独立存 histScope（不覆盖当天的 scope）",
          "m.fund.histScope =" in s and "m.fund.scope = (m.fund.days.length" not in s
          and "m.fund.histScope) || '沪市'" in s)
    check("解释「净额 ≠ 成交额」（用户困惑的直接答案）",
          "它是<b>净额</b>，不是<b>成交额</b>" in su and "一买一卖" in su and "并不矛盾" in su)
    check("说明「上午 ＋ 下午 ＝ 全天」这层关系", "上午 ＋ 下午 ＝ 全天" in su)
    # ── 资讯流（复盘导读）：只在快照里，页面不实时拉 ─────
    # 曾经挂过 np-weblist / np-listapi 三条在实时清单里，浏览器控制台每次加载都报跨域失败。
    # 这三条一天只出一份，实时拉本来也没有意义——改成只走快照，并在标签上写明是几点抓的。
    check("资讯接口已移出实时清单（URLS 里没有 np 系地址）",
          "https://np-weblist" not in s and "https://np-listapi" not in s
          and "req_trace=1" not in s)
    check("要闻仍从快照里解析出来（normalize 没把这条链断掉）",
          "m.newsARev  = normNewsData(raw.news_a_rev)" in s
          and "m.newsGlRev = normNewsData(raw.news_gl_rev)" in s)
    check("要闻写明「哪天的快照」并说明不随分钟级刷新（newsStamp）",
          "function newsStamp(" in s and "不随分钟级刷新" in s)
    check("要闻取不到也占一行说清楚（不留一片空白）",
          s.count("这次没取到") >= 3 and "要闻随收盘后的复盘一起生成" in s)
    check("资讯解析统一走 normNewsData（快讯与导读字段同义，拍平成一种）",
          "function normNewsData(" in s and "(d.fastNewsList || d.list)" in s)
    check("重点快讯按发布方标记识别（titleColor===3 → 红点），不自造重要度",
          "x.titleColor === 3" in s)
    check("快讯段已按需求整段移除（页面不再渲染 sec-flash）",
          "sec-flash" not in s and "function secFlash(" not in s)
    check("A 股焦点复盘是独立 subhead 块（数据焦点 + 盘后要闻，不进 .seg-all）",
          "焦点复盘<span class=" in s and "const focusHtml" in s and "m.newsARev" in s)
    check("环球复盘带「环球与隔夜要闻」导读（m.newsGlRev 有渲染者）",
          "环球与隔夜要闻" in s and "m.newsGlRev" in s)
    # ── 第 3 段「按市场复盘」──────────────────────────────────────────
    # 四个市场**能取到的东西厚度差很多**（A股全 / 港股次之 / 美股不能拆段 / 全球只有点位），
    # 所以复盘深度必须跟着数据走：缺的那块要**如实说清为什么没有**，不许硬凑一段像模像样的。
    check("复盘段四个市场标签（沪深京 · A 股 / 港股 / 美股 / 全球市场）",
          all(f"label:'{x}'" in s for x in ("沪深京 · A 股", "港股", "美股", "全球市场")))
    check("复盘视角有独立的数据键（与第 2 段的市场选择互不干扰）",
          "const RKEY = 'mb_report_mkt'" in s and "const MKEY = 'mb_market'" in s and
          "mb_report_mkt" != "mb_market")
    check("复盘段有标签容器 #rTabs 与重绘容器 #rptBody",
          'id="rTabs"' in s and 'id="rptBody"' in s)
    check("切复盘市场只重绘本段（保住持仓表 / 台账里正在填的内容）",
          bool(re.search(r"function repaintReport\(\)\{.{0,400}?setHtml\('rptBody', reportBody\(m\)\)", s, re.S)))
    check("复盘市场选择会落本地存储", "saveReportMkt()" in s and "loadReportMkt()" in s)
    check("港股复盘按行业口径命名（午评 09:30–12:00 / 午后盘中 13:00–16:00 / 收评）",
          "'午评', '09:30 – 12:00'" in s and "'午后 · 盘中', '13:00 – 16:00'" in s
          and "'收评', '09:30 – 16:00'" in s)
    check("美股复盘写死跨零点时段（21:30–次日 04:00 / 冬令时 22:30–次日 05:00）",
          "'21:30 – 次日 04:00'" in s and "'22:30 – 次日 05:00'" in s and "usDST(new Date())" in s)
    check("美股复盘如实说明「分时接口不返回成交额」（不把 0 当没成交）",
          "这一段没有成交额可写" in s and "字段恒为 0" in s)
    check("港股复盘如实说明「没有全市场家数/资金流口径」",
          "不是</b>港股全市场的涨跌家数" in s)
    check("全球复盘如实说明「不存在全球统一的分时」",
          "全球统一的分时" in s and "横向对比" in s)
    check("全球复盘给出当日涨跌分布 + 领涨/领跌榜",
          "个全球股票指数里" in s and "涨幅前 8" in s and "跌幅前 8" in s)
    check("全球复盘点明「这些数字不是同一时刻的」",
          "各自最新一笔" in s)
    check("港美股复盘不用涨幅榜（改「精选标的」，并说明牛熊证/仙股）",
          "精选名单，不是涨幅榜" in s and "牛熊证" in s and "仙股" in s)
    check("A 股卡片仍带 data-idx（保住按指数定位的老断言）",
          """it.code ? ' data-idx="' + esc(it.code) + '"' : ''""" in s)
    check("段内「跳过去」链接已覆盖全页（第 3 段也有）",
          "document.querySelectorAll('[data-jump]')" in s)
    check("改版前的旧标题不再残留", "三段复盘" not in s)
    # ── v8：复盘命名对齐主流交易软件「位置 + 评」，补行业锚点体系与家数口径 ──
    check("A股复盘用行业命名（午评 / 午后 · 盘中快评 / 收评）",
          '<div class="seg-title">午评<span class="range">' in s
          and '<div class="seg-title">午后 · 盘中快评<span class="range">' in s
          and '<div class="seg-title">收评<span class="range">09:30 – 15:00</span>' in s)
    check("不再使用「上半场 / 下半场 / 上午盘 / 下午盘」旧栏目名",
          "上半场" not in s and "下半场" not in s
          and "上午盘" not in s and "下午盘" not in s)
    check("引语只说「按半场讲一遍」（不再解释行业怎么排版）",
          "上午半场 → 午后半场 → 全天" in s)
    check("收评标题跟时间走（收盘前不说「完整一天」）",
          "dayWord() === '全天' ? '收评' : '盘中 · 到目前为止'" in s)
    check("涨跌家数说明「不含北交所」", "不含北交所" in s)
    # 长周期日K：两种数据源的解析都要在（东财 klines / 腾讯 day）
    check("日K解析兼容东财 klines（A股指数 / 沪深300）",
          "d.klines" in s and "m.kline = klSeries(raw.kline)" in s)
    check("日K解析兼容腾讯 qfqday/day（长周期指数）",
          "qfqday" in s and "d[x].day" in s)
    check("四个指数都进了历史页（klIdx 含 sh/sz/cyb/hs300）",
          all(f"key:'{k}'" in s for k in ("sh", "sz", "cyb", "hs300")))
    # ── 图表悬停读数（用户明确要求：鼠标过去要出数值）──
    check("图表交互注册表 CHARTS 存在", "const CHARTS = {}" in s)
    check("十字准线已内嵌并默认隐藏", "class=\"cur\"" in s and "id=\"cur-' + cid" in s)
    check("浮层容器 #chartTip 会挂到 body", "d.id = 'chartTip'" in s)
    check("事件用 document 级 pointermove 委托（图多且会重建，不能逐图绑）",
          "document.addEventListener('pointermove'" in s and "bindChartTips(" in s)
    check("浮层含边界翻边逻辑", "function placeTip(" in s and "window.innerWidth" in s)
    check("折线图带十字准线拖拽更新", "g.querySelector('.cur-x')" in s and "setAttribute('cx'" in s)
    check("柱状图读数走 BARS 注册表（按 data-bar 取，非 data-tip 属性）",
          "data-bar=" in s and "BARS[vb.getAttribute('data-bar')]" in s)
    check("滚屏 / 失焦时收起浮层", "addEventListener('blur', hideChartTip)" in s and
          "addEventListener('scroll', hideChartTip" in s)
    check("分时图底部刻度（开盘/午间休市/收盘）",
          "11:30 / 13:00 午间休市" in s and "15:00 收盘" in s)
    # ── 我的账本（工作台）：这些是「它为什么比手填成本表准」的根本 ──
    check("台账只让你填日期/代码/买卖/价/量，金额自动算", "'lgamt-'" in s and "function lgAmt(" in s)
    # ⚠️ 代码是归并持仓的唯一钥匙：只改代码也必须立刻重算，否则「名称查不到」（场外基金 / 断网）
    #    时用户看不到算出来的持仓。（2026-10-16 由浏览器自检发现）
    check("改「代码」就立刻重算持仓（不等联网查名称）",
          bool(re.search(r"if\(k === 'code'\)\{.{0,600}?paintLgDerived\(\);\s*\n\s*refreshBookTabs\(\);", s, re.S)))
    check("成本用移动加权平均（页面上也这么写）", "移动加权平均" in s and
          "p.cost = (p.cost * p.qty + amt) / (p.qty + qty)" in s)
    check("卖出不动成本价，只结转已实现盈亏",
          "p.realized += (price - p.cost) * sellQ" in s)
    check("清仓后成本归零（下次买入重新起算）", "if(p.qty <= 1e-9){ p.qty = 0; p.cost = 0; }" in s)
    check("卖出超过持仓会被拦下并提示，不会算出负持仓",
          "卖出份数超过持仓" in s and "Math.min(qty, p.qty)" in s)
    check("总资产 = 持仓市值 + 现金", "bookTotals" in s and "total:mv + CASH" in s)
    check("同一天只留一条快照（重复存是覆盖不是追加）",
          "SNAPS[d] = {" in s and "if(SNAPS[todayStr()]) return;" in s)
    check("取不到现价时按成本估并明确标注（不显示「—」）",
          "est-tag" in s and "p.est = true" in s)
    check("净值曲线要 2 条以上快照才画，不足会给说明",
          "series.length >= 2" in s and "画不成线" in s)
    check("净值曲线可悬停读数（较上一条 / 较第一条）", "较上一条" in s and "较第一条" in s)
    check("明确提醒「越早开始存越好」", "越早开始越好" in s)
    check("复盘日记三要素（决定 / 理由 / 心情）",
          "我今天做的决定" in s and "为什么这么决定" in s and "当时的心情" in s)
    check("写明复盘日记是「唯一记下你当时怎么想」的地方", "账本里唯一记下「你当时怎么想」的地方" in s)
    check("账本四个数据键齐全（台账/快照/日记/现金）",
          all(f"'{k}'" in s for k in ("mb_ledger_v1", "mb_snap_v1", "mb_journal_v1", "mb_cash_v1")))
    check("账本可导出 / 导入 JSON（防本地存储丢失）",
          "bkExport" in s and "bkImport" in s and "readAsText" in s)
    # ── 换设备搬家：本机 → 网上（不经过账号登录）──
    # 账号登录只收 https 正式站点，本地那份永远登不上 ⇒ 改成把账本编进网址带过去。
    check("「备份到网上」只在没有云端配置时出现（正式站点自己就在云上）",
          "(CLOUD_CFG.endpoint ? '' : '<button type=\"button\" class=\"bk-btn\" id=\"bkCloud\">" in s)
    check("备份的目标站点是 https（平台只认 https 回调）", "BACKUP_SITE = 'https://" in s)
    check("账本放在 # 后面带过去（hash 不发服务器、不进访问日志）",
          "BACKUP_SITE + '#backup='" in s)
    check("base64 先按 UTF-8 转字节（直接 btoa 中文会乱码）",
          "new TextEncoder().encode(str)" in s and "new TextDecoder().decode(bytes)" in s)
    check("网址装不下就退回文件导出，绝不截断", "BACKUP_MAX" in s and "网址装不下" in s)
    _seg = s[s.index("function tryRestoreFromHash("):][:1800]
    check("链接读不出来也要先清 hash（否则每次刷新都弹一次）",
          -1 < _seg.find("replaceState") < _seg.find("if(!o)"))
    check("网页那头先确认再覆盖（不悄悄替掉已有账本）",
          "导入会覆盖这个网页里现有的账本" in s)
    check("导入只有一套落地逻辑（文件导入与网址导入共用 applyBackup）",
          s.count("function applyBackup(") == 1 and "applyBackup(JSON.parse(rd.result))" in s)
    check("台账可一键同步到持仓监控（避免重复录入）", "function syncToWatch(" in s)
    check("账本写明数据只存本地、不上传", "不会上传到任何服务器" in s)
    check("账本与看板的分工写在页面上", "看板回答" in s and "账本回答" in s)
    check("存快照 / 存日记都有回执（点了不会没反应）",
          "bkSnapState" in s and "jrnState" in s and "'已存 '" in s and "'已存下 '" in s)
    # ⚠️ 回执元素跟着「整段重绘」被换掉 ⇒ 必须写完重绘再取元素，顺序反了就什么都看不到
    for nm, anchor, a, b in (
        ("存快照", "const sn = document.getElementById('bkSnap')", "refreshBookTabs();", "bkSnapState"),
        ("存日记", "const save = document.getElementById('jrnSave')", "initBookSections();", "jrnState"),
    ):
        seg = s[s.index(anchor):][:1400]
        m = re.search(re.escape(a) + r".{0,160}?getElementById\('" + b + r"'\)", seg, re.S)
        check(f"{nm}的回执写在重绘之后（顺序反了回执会被换掉）", bool(m), a + " → " + b)
    # 旧名不该再残留（改版后是死代码）
    for dead in ("descAM(", "descPM(", "descAll(", "sec4Breadth(", "sec1Holdings(",
                 "secPos(", "function gLong(", ".spark-wrap", ".gt-axis"):
        check(f"无改版前的死代码 {dead}", dead not in s)
    check("持仓双视图按钮（监控 / 纪律线）",
          'data-view="watch"' in s and 'data-view="disc"' in s)
    check("视图切换已绑定事件", "b.dataset.view" in s and "saveView()" in s)
    for t in ("09:30", "11:30", "13:00", "15:00"):
        check(f"交易时段常量 {t}", t in s)

    print("\n【4】零外部依赖（发布后离线也能看）")
    ext = re.findall(r'(?:src|href)\s*=\s*["\'](https?://[^"\']+)', s)
    ext = [u for u in ext if "eastmoney.com" not in u]
    check("无外链 <script>/<link>/<img> 资源", not ext, "; ".join(ext[:3]))
    # 唯一的例外：云端备份组件。它**运行时才注入**（不是 <script src> 静态标签），
    # 下载不到就当没这回事 —— 页面主体依然零外部依赖，断网照样打开。
    check("唯一外部依赖是云端组件，且只出现一处", s.count("workbuddy-cloud-sdk") == 1,
          str(s.count("workbuddy-cloud-sdk")))
    check("云端组件运行时注入，不是静态 <script src>",
          "createElement('script')" in s and 'src="https://cdn.jsdelivr.net' not in s)

    print("\n【5】标签配平")
    # ⚠️ 先剥掉注释再数标签：注释里写「那三个 <div data-page>」这种说明书式文字会被
    #    正则计成开标签，报出假的不配平（踩过一次，追了半小时）。剥注释是通用修法。
    nocomment = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    nocomment = re.sub(r"/\*.*?\*/", "", nocomment, flags=re.S)
    for tag in ("div", "section", "table", "tbody", "thead", "tr", "svg", "script", "style", "ul", "li"):
        o = len(re.findall(r"<%s[\s>]" % tag, nocomment))
        c = len(re.findall(r"</%s>" % tag, nocomment))
        check(f"<{tag}> 开 {o} / 闭 {c}", o == c)

    print("\n【6】口径与红线（只在非否定语境下判违规）")
    NEG = ("不是", "非", "不构成", "不作为", "不含", "不给", "不提供", "没有", "不保证", "停止披露")
    body = html.unescape(s)
    # 红线只扫页面自己的文案：内嵌快照里有第三方新闻标题（可能原样含「目标价」），不算我们的表述
    body = re.sub(r"const SNAPSHOT = \{.*?\};\n", "", body, flags=re.S)
    for bad, why in [("建议买入", "买卖建议"), ("建议卖出", "买卖建议"),
                     ("必涨", "预测性表述"), ("保证收益", "收益保证"),
                     ("目标价", "目标价口吻"), ("目标 ", "目标价口吻"),
                     ("实时行情", "误称实时")]:
        hit = None
        for mm in re.finditer(re.escape(bad), body):
            ctx = body[max(0, mm.start() - 16):mm.start()]
            if not any(n in ctx for n in NEG):
                hit = ctx.strip()[-16:]
                break
        check(f"无「{bad}」（{why}）", hit is None, (f"上下文…{hit}" if hit else ""))

    print("\n【7】诚实性标注")
    for must, why in [("北向资金", "北向数据已停止披露需说明"),
                      ("2024 年 5 月 13 日", "北向停盘中实时披露的时间点"),
                      ("8 月 19 日", "北向停当日净额披露的时间点"),
                      ("快照", "需说明数字非实时"),
                      ("不提供任何买卖建议", "需有免责声明"),
                      ("本地存储", "需说明个人数据存放位置")]:
        check(f"含「{must}」({why})", must in body)
    check("北向文案不再把两步合成一步（旧说法「自 2024 年 8 月起已停止披露」已废）",
          "自 2024 年 8 月起已停止披露" not in body)

    print("\n【8】对外版卫生（dashboard-public 专用）")
    # ① 内部痕迹词：制作过程用语不许出现在学员可见的源代码里（含 JS 注释——会原样进 HTML）
    for bad in ("学员", "用户", "原话", "我们算错"):
        check(f"无内部痕迹词「{bad}」", bad not in body, "")
    # ② D1–D3 / B2：数据安全提示与降级说明
    check("D1 首次保存弹「只存本设备」提醒（只弹一次）",
          "dataHintOnce" in s and "mb_hint_shown" in s)
    check("D2 使用说明写明数据搬家方法（导出 / 导入）",
          "只存在这台设备的浏览器（本地存储）里" in s and "导出 / 导入" in s and "换设备" in s)
    check("D3 账本页显示上次导出备份时间",
          "bkExpAt" in s and "mb_bk_exp" in s and "上次导出备份" in s and "还没有导出过备份" in s)
    check("B2 使用说明写明「栏目偶尔空着，刷新一次就回来」（不让接口波动变成数据丢了的错觉）",
          "刷新一次" in s and "不影响已经填好的数据" in s)

    print("\n【9】云端备份（登录可选，不登录照常记）")
    # 初始化参数必须来自 publicConfig，三项一个都不能少（少 oauthRelayBaseUrl 会在点登录时才炸）
    for k, why in [("endpoint: CLOUD_CFG.endpoint", "endpoint 必传"),
                   ("oauthRelayBaseUrl: CLOUD_CFG.oauthRelayBaseUrl", "登录中转地址必传"),
                   ("publishableKey: CLOUD_CFG.publishableKey", "公开密钥必传")]:
        check(f"初始化带 {why}", k in s)
    for t, why in [("ledger_entries", "台账表"), ("journal_entries", "日记表"), ("book_state", "附属状态表")]:
        check(f"用到 {why} {t}", t in s)
    check("owner_id 一律不传（由数据库按登录身份填）", "owner_id:" not in s)
    check("登录方式是 WorkBuddy 账号（按钮文案一致）",
          "provider: 'workbuddy'" in s and "使用 WorkBuddy 登录" in s)
    check("回调按 provider 分发，只调 handleWorkBuddyCallback",
          "handleWorkBuddyCallback" in s)
    check("没有误调微信回调 / 已废弃的 handleOAuthCallback",
          "handleWechatWebCallback" not in s and "handleOAuthCallback" not in s)
    check("登录走同一标签页（PKCE 凭据在本页）", "location.assign(" in s)
    check("登录回来只删 code/state/provider，保留其它参数",
          "history.replaceState" in s and "back.pathname = '/'" in s)
    check("没有匿名登录 / 假会话", "signInAnonymously" not in s and "mockSession" not in s)
    check("没有手写 fetch 打数据面", "/.cloud/database/rest" not in s)
    check("每次保存先本地落盘，未登录不发请求",
          "cloudTouch" in s and "if(!CLOUD || !CLOUD.ready) return;" in s)
    check("云端连不上时退回纯本地、不挡路", "CLOUD.offsite" in s and "云端没连上" in s)
    check("「数据存哪」按登录状态改口（不写死『不上传』）", "cloudStoreText()" in s)
    # 本地包没有云端配置：云端条只说明数据存哪，不能给一个点不动的「登录」
    check("没有云端配置时不给登录入口（只说明数据存哪）",
          "if(!CLOUD_CFG.endpoint){" in s and "el.dataset.state = 'local';" in s)
    check("没有云端配置时「数据存哪」不再提登录",
          "if(!CLOUD_CFG.endpoint) return '数据只在这台设备的浏览器里（本地存储）" in s)
    check("云端条容器始终存在（本地包靠它落地，不是靠构建期换 HTML）",
          'id="cloudBar"' in s and "function paintCloudBar(" in s)
    check("每笔流水有稳定行号 rid（跨设备对得上同一笔）",
          "cloudRid()" in s and "rid: (x && x.rid) || ''" in s)

    print("\n【10】实时取数兜底（东财 push2 整族不通时的降级路径）")
    check("有腾讯行情兜底（qt.gtimg.cn）", "qt.gtimg.cn/q=" in s and "function txQuote" in s)
    check("兜底只处理 sh/sz/bj（港美股 78/73 列，不能硬套下标）",
          "/^(sh|sz|bj)$/" in s)
    check("成交额「万元 → 元」换算，与东财口径一致", "N(p[37]) * 1e4" in s)
    check("快照打底 + 实时覆盖（部分成功也不让整段消失）",
          "function mergeLive" in s and "merged: true" in s)
    # ── 徽标只回答「数据到几点」；缺哪块，在那一块自己说 ──
    # 旧写法把「资金流向仍是快照 11-03 14:49」塞进右上角：主语丢了、时间没标签，
    # 而且把使用者既改不了也用不上的接口故障摆在最显眼处。
    check("徽标只说「数据到几点」，不列接口名", "function paintDataBadge" in s and "miss.push(" not in s)
    check("徽标按交易状态分档（交易中 / 收盘 / 离线快照）",
          "'交易中 · 更新于 ' + hhmmss()" in s and "'已收盘' ? '数据到 15:00'" in s and "'离线快照'" in s)
    check("徽标不出现「实时」两个字（会和同一屏的「已收盘」打架）",
          "'实时 · '" not in s and "badge-live" in s)
    check("刷新中显示「正在更新…」、失败显示「更新失败，显示的是几点的数据」",
          "'正在更新…'" in s and "'更新失败，显示的是'" in s)
    check("交易时段内写明会自己刷新（'60 秒后自动刷新'）", "秒后自动刷新" in s)
    check("缺哪块就在那一块自己说（不往页面顶部堆）",
          "function staleLine(" in s and "LIVE_GAPS.fund ? staleLine('资金流向')" in s)
    check("缺口只在「拉过一次实时」之后才标（首屏那遍不许满屏标旧）",
          "if(!lk.length) return;" in s)

    # ── 自动刷新（交易时段 60 秒一轮）──
    check("自动刷新：交易时段每 60 秒一轮", "const AUTO_MS = 60000" in s and "setInterval(autoTick, AUTO_MS)" in s)
    check("自动刷新：非交易时段不跑（收盘后不再空拉接口）",
          "if(!sessionState(new Date()).activeKey) return;" in s)
    check("自动刷新：切到别的标签页就停", "if(document.hidden) return;" in s)
    check("自动刷新：正在填表时让路（不打断输入）", "if(typingNow()) return;" in s)
    check("自动刷新：重画后还原焦点、光标与滚动位置",
          "function uiKeep(" in s and "function uiBack(" in s and "setSelectionRange(k.pos, k.pos)" in s)
    check("重画用同一套渲染入口（不另写一份局部更新，免得出「一半新一半旧」）",
          "async function refreshLive(" in s and s.count("renderAll(m2)") == 1)
    check("主行情机连不上时自动换同源备用主机（路径参数原样不动）",
          "P2_MIRROR" in s and "function hostCands" in s and "function fetchJSON" in s)
    check("空壳响应（有 data 但数组为空）当没取到，不冲掉快照里的曲线",
          "function hollow" in s and "carriers = ['diff', 'klines', 'trends', 'pool']" in s
          and "!hollow(j)" in s)
    check("换用备用主机时写进 _meta 留痕（页脚不再列数据来源表）",
          "out._meta.mirror = true" in s and "srcGrid" not in s)
    check("fetchLive 不再要求「6 项以上」才认成功（拿多少用多少）", "okCount < 6" not in s)
    check("持仓现价同样走兜底（东财不通时不至于全变 —）", "txQuote(items)" in s)

    print("\n【11】逐条回改（数据 / 文案 / 展示的批改清单）")
    # ── D1 涨跌家数占比：三段各自算，加起来必须是 100% ──
    # 旧写法把「下跌」写成 100 − 上涨，等于把平盘算进下跌：2067/5293=39.1%，
    # 页面却写「下跌 60.9%」（其实是 (3065+161)/5293）。
    check("D1 三段占比各自按总家数算（不再用 100 − 上涨）",
          "const flatW = (b.flat != null && b.total)" in s
          and "const downW = (b.down != null && b.total)" in s
          and "(100-upW).toFixed" not in s)
    check("D1 条上三段并列显示（上涨 / 平盘 / 下跌 都在刻度条里）",
          s.count('<i class="flat"') >= 1 and '<i class="down"' in s)
    check("D1 标题改成「涨跌家数占比」（不只是上涨占比）", "涨跌家数占比" in s)

    # ── D2 当日新股不进榜 ──
    check("D2 有新股识别（N/C + 汉字）", "const isNewStock = nm =>" in s and "^[NC]" in s)
    check("D2 个股涨幅榜剔掉新股后仍给满 8 条",
          "list8('stock_chg', true)" in s and "skipNew ? rows.filter" in s)
    check("D2 主力净流入榜同样剔掉新股（bypass 会排第 2 名）",
          "rank('stock_in', true)" in s)
    check("D2 副标题「不含当日新股」与实现一致（多要几条才剔得起）",
          "不含当日新股" in s and "pz=12" in s)

    # ── D3 午后快评按「上午方向 × 午后方向」分四种 ──
    check("D3 上午涨 + 午后涨 / 上午跌 + 午后涨 分开写",
          "在上午上涨的基础上继续走高" in s and "把上午的跌幅收回来一部分" in s)
    check("D3 上午涨 + 午后跌 / 上午跌 + 午后跌 分开写（不再一律说「回吐涨幅」）",
          "把上午的一部分涨幅回吐掉" in s and "在上午下跌的基础上继续走低" in s)
    check("D3 「上午的涨幅」这种默认前提已删干净", "回吐了上午的部分涨幅" not in s)
    check("D3 形态词与净涨跌同向（跌的那一段不会说「走高」）",
          "if((hi-lo)/pre*100 < 0.35){" in s and "else if(endPct < -0.05){" in s
          and "else if(endPct > 0.05){" in s)
    check("D3 港股午后同样分四种（同一个毛病，一起改）",
          "在早市下跌的基础上继续走低" in s)

    # ── D4 最高/最低取每分钟 K 的最高最低价 ──
    # 按收盘价算 3581.20，按分钟最低价算 3580.38，行情快照 f16 也是 3580.38。
    check("D4 分时段高低点用分钟 K 的 high/low（与行情快照口径一致）",
          "p.high != null ? p.high : p.close" in s and "p.low  != null ? p.low  : p.close" in s)
    check("D4 形态识别的极值点也换成同一口径", "const hs = pts.map(p => p.high" in s)

    # ── D6 成本价口径要写清楚 ──
    check("D6 不再说「券商对账单也是这么算的」", "券商对账单也是这么算的" not in s)
    check("D6 明说不含手续费和印花税、可能与券商不同",
          "不含手续费和印花税" in s and "和你的券商显示的成本价可能有差异" in s)

    # ── D7 ST 涨跌幅：2026-07-06 起主板由 5% 改为 10% ──
    check("D7 主板 ST 涨跌幅已按现行规则改为 10%",
          "2026 年 7 月 6 日起" in s and "主板 ST 也由 5% 放宽到 10%" in s)
    check("D7 旧的「ST 股 5%」不再出现", "ST 股 5%" not in s)

    # ── T2 / T3 文案 ──
    check("T2 「成交额了」补上动词", "成交额了" not in s and "成交额<b>达到 " in s)
    check("T2 净额量级对齐实际（几十亿到几百亿，不说「几亿」）",
          "几十亿到几百亿" in s and "净流出几亿" not in s)
    check("T3 复盘日记那句不再重复", "回看时能多知道一件事</b>：以后回看" not in s)

    # ── T4 近 60 个交易日：标题说的就是实际条数 ──
    check("T4 日K序列截到 60 条（标题写 60 就是 60）",
          "const KL_DAYS = 60" in s and "rows = rows.slice(-KL_DAYS)" in s)

    # ── T5 只有一套编号 ──
    check("T5 目录分组标签里不再有圈号（编号只由 1/2/3/4 提供）",
          "g:'①" not in s and "g:'②" not in s and "g:'③" not in s)

    # ── T6 成分股太少的行业要标出来 ──
    check("T6 行业/概念榜带成分股数并给「成分股仅 N 只」标记",
          "total: (N(x.f104)||0) + (N(x.f105)||0)" in s and "成分股仅 " in s)
    check("T6 说明里讲了「成分股太少的排名参考价值低」",
          "成分股太少的" in s and "参考价值低" in s)

    # ── T7 精简档折叠 ──
    check("T7 页头长说明可折叠（details#topNote）",
          'id="topNote"' in s and "<details" in s)
    check("T7 术语 + 连板梯队可折叠（details#foldLadder）",
          'id="foldLadder"' in s)
    check("T7 折叠状态按档位设、且不跟读者抢（整页 60 秒重画一次）",
          "let FOLD_OPEN = null" in s and "function applyFolds(" in s
          and "el.addEventListener('toggle'" in s)

    print("\n" + "=" * 56)
    if fails:
        print(f"❌ 自检失败 {len(fails)} 项：" + ", ".join(fails))
        return 1
    print("✅ 全部自检通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
