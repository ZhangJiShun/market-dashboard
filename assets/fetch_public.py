#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新手行情看板 · 公开免费接口取数（服务端）

定位：**离线兜底 / 快照生产**。无连接器环境走这条。
     —— 但注意：页面本身也会在浏览器里直接调同样的接口（实时刷新）。
     这个脚本的作用是产出一份 `data/raw.json` 快照，内嵌进 HTML，
     让页面在「拿不到实时数据」时仍有东西可看（离线、接口被墙、放假）。

设计要点：
    * 只负责「取原始响应」，不做聚合。聚合逻辑只在页面 JS 里写一份，
      避免 Python / JS 两套算法漂移。
    * 所有接口均已实测 CORS 可用（见项目记忆）。
    * 输出是纯 JSON，字段与页面 fetch 拿到的完全同构。

用法：
    python3 fetch_public.py                 # 写 data/raw.json
    python3 fetch_public.py --out x.json
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UT = "fa5fd1943c7b386f172d6893dbfba10b"
UT2 = "b2884a393a59ad64002292a3e90d46a5"
PUSH2 = "https://push2.eastmoney.com/api/qt"
PUSH2HIS = "https://push2his.eastmoney.com/api/qt"
PUSH2EX = "https://push2ex.eastmoney.com"
DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
# 腾讯行情（备用源：长周期日K；2026-10-16 实测浏览器 CORS 通过）
# ⚠️ 2026-11-03 起这台机器访问腾讯会被 WAF 挡成 HTTP 501（返回一个跳转页），
#    所以下面 KLINE_ALT 给它配了东方财富镜像的兜底，缺一条「历史与位置」就少一张卡。
TX = "https://web.ifzq.gtimg.cn/appstock/app"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 全 A 股过滤串（沪深主板+创业板+科创板）
ALL_A = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"

# 看板盯的指数：上证指数 / 深证成指 / 创业板指 / 沪深300 / 中证全指
INDEX_SECIDS = "1.000001,0.399001,0.399006,1.000300,1.000985"

# ── 全球市场 ──────────────────────────────────────────────────────
# 核心指数：A股 4 个 + 港股 4 个 + 美股 4 个 + 全球主要 18 个（均实测可用）
# ⚠️ 恒生科技指数是 124.HSTECH（不是 100 段），2026-10-16 实测确认
GL_CORE = ("1.000001,0.399001,0.399006,1.000300,"
           "100.HSI,100.HSCEI,124.HSTECH,100.HKEXT100,"
           "100.DJIA,100.NDX,100.NDX100,100.SPX,"
           "100.N225,100.KS11,100.TWII,100.STI,100.AS51,"
           "100.GDAXI,100.FTSE,100.FCHI,100.SX5E,100.MIB,100.IBEX,100.SSMI,100.AEX,"
           "100.SENSEX,100.TSX,100.BVSP,100.UDI,100.XIN9")
# ⚠️ 港美股**不要用涨幅榜**：m:116 / m:105 的 clist 涨幅榜前排全是牛熊证、窝轮、
#    仙股（实测：港股「联想法兴七三牛B +121%」、美股「Vivid Seats Wt +500%」），
#    给新手看是灾难。改成「精选知名标的」，一次 ulist 拿全，稳定且可解释。
GL_HK = ("116.00700,116.09988,116.03690,116.00388,116.01810,116.01211,"
         "116.00941,116.09618,116.01024,116.00005,116.00939,116.02318")
GL_US = ("105.AAPL,105.NVDA,105.MSFT,105.TSLA,105.GOOGL,105.AMZN,"
         "105.META,105.AVGO,105.NFLX,105.AMD,105.INTC")

ENDPOINTS = {
    # —— 指数快照
    "quote": (f"{PUSH2}/ulist.np/get?fltt=2&invt=2&secids={INDEX_SECIDS}"
              f"&fields=f1,f2,f3,f4,f5,f6,f12,f13,f14,f15,f16,f17,f18&ut={UT}"),
    # —— 涨跌家数（沪 / 深分开给）
    "breadth": (f"{PUSH2}/ulist.np/get?fltt=2&invt=2&secids=1.000001,0.399001"
                f"&fields=f12,f14,f104,f105,f106&ut={UT}"),
    # —— 逐分钟分时（给出交易时段边界 tradePeriods）
    "trend_sh000001": (f"{PUSH2}/stock/trends2/get?secid=1.000001"
                       f"&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
                       f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut={UT}"),
    "trend_sz399006": (f"{PUSH2}/stock/trends2/get?secid=0.399006"
                       f"&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
                       f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut={UT}"),
    "trend_sh000300": (f"{PUSH2}/stock/trends2/get?secid=1.000300"
                       f"&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
                       f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut={UT}"),
    "trend_sz399001": (f"{PUSH2}/stock/trends2/get?secid=0.399001"
                       f"&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
                       f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut={UT}"),
    # —— 大盘资金流：分时（切上午/下午）+ 日级（对昨）
    # ⚠️⚠️ 必须**沪市 + 深市两条都抓再相加**，不能只抓 1.000001：
    #    1.000001 = 上证指数（沪市）、0.399001 = 深证成指（深市）。
    #    只抓前者得到的是「沪市」口径——2026-10-16 沪市 -5.61 亿，而东财「沪深两市」
    #    是 -49.36 亿（沪 -5.61 ＋ 深 -43.75），差近 9 倍。
    "fflow_min": (f"{PUSH2}/stock/fflow/kline/get?secid=1.000001"
                  f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&klt=1&lmt=0&ut={UT2}"),
    "fflow_day": (f"{PUSH2HIS}/stock/fflow/daykline/get?secid=1.000001"
                  f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&klt=101&lmt=12&ut={UT2}"),
    "fflow_min_sz": (f"{PUSH2}/stock/fflow/kline/get?secid=0.399001"
                     f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&klt=1&lmt=0&ut={UT2}"),
    "fflow_day_sz": (f"{PUSH2HIS}/stock/fflow/daykline/get?secid=0.399001"
                     f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&klt=101&lmt=12&ut={UT2}"),
    # —— 行业板块资金流：流入 TOP / 流出 TOP（按主力净额 f62 排）
    "sector_in": (f"{PUSH2}/clist/get?pn=1&pz=8&po=1&np=1&fltt=2&invt=2&fid=f62"
                  f"&fs=m:90+t:2&fields=f12,f14,f2,f3,f62,f184&ut={UT2}"),
    "sector_out": (f"{PUSH2}/clist/get?pn=1&pz=8&po=0&np=1&fltt=2&invt=2&fid=f62"
                   f"&fs=m:90+t:2&fields=f12,f14,f2,f3,f62,f184&ut={UT2}"),
    # —— 行业板块按【涨跌幅】的涨幅榜 / 跌幅榜（与上面按资金排是两个维度）
    #    f128=领涨股名 f140=领涨股代码 f104/f105=板块内涨/跌家数
    "sector_up": (f"{PUSH2}/clist/get?pn=1&pz=30&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2"
                  f"&fields=f12,f14,f2,f3,f62,f104,f105,f128,f140&ut={UT2}"),
    "sector_down": (f"{PUSH2}/clist/get?pn=1&pz=30&po=0&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2"
                    f"&fields=f12,f14,f2,f3,f62,f104,f105,f128,f140&ut={UT2}"),
    # —— 概念板块涨幅榜（今天的题材/热点）
    "concept_up": (f"{PUSH2}/clist/get?pn=1&pz=30&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:3"
                   f"&fields=f12,f14,f2,f3,f62,f104,f105,f128,f140&ut={UT2}"),
    # —— 个股主力净流入 TOP（要 12 条：剔掉当日新股后还得够 8 条）
    "stock_in": (f"{PUSH2}/clist/get?pn=1&pz=12&po=1&np=1&fltt=2&invt=2&fid=f62"
                 f"&fs={ALL_A}&fields=f12,f14,f2,f3,f62,f184,f100&ut={UT2}"),
    # —— 个股涨幅榜 / 成交额榜（涨幅榜同理多要 2 条）
    "stock_chg": (f"{PUSH2}/clist/get?pn=1&pz=12&po=1&np=1&fltt=2&invt=2&fid=f3&fs={ALL_A}"
                  f"&fields=f12,f14,f2,f3,f4,f5,f6,f100&ut={UT2}"),
    "stock_amt": (f"{PUSH2}/clist/get?pn=1&pz=10&po=1&np=1&fltt=2&invt=2&fid=f6&fs={ALL_A}"
                  f"&fields=f12,f14,f2,f3,f4,f5,f6,f100&ut={UT2}"),
    # —— 沪深指数日K：取最近 6 个交易日的【成交额 f57】，用来算放量/缩量
    "kday_sh": (f"{PUSH2HIS}/stock/kline/get?secid=1.000001&klt=101&fqt=0&lmt=6&end=20500101"
                f"&fields1=f1,f2,f3&fields2=f51,f53,f57&ut={UT}"),
    "kday_sz": (f"{PUSH2HIS}/stock/kline/get?secid=0.399001&klt=101&fqt=0&lmt=6&end=20500101"
                f"&fields1=f1,f2,f3&fields2=f51,f53,f57&ut={UT}"),
    # —— 涨停 / 跌停 / 炸板 池（总数 tc + 明细，明细用来做连板梯队）
    #    ⚠️ 必须带 date，否则返回空。见 POOL_TPL，第二阶段用真实交易日拼 URL
    # —— 两融（次日发布，取最近 2 日）
    "margin": (f"{DC}?reportName=RPTA_RZRQ_LSHJ&columns=ALL&sortColumns=DIM_DATE&sortTypes=-1"
               f"&pageSize=2&pageNumber=1&source=WEB&client=WEB"),
    # —— 沪深300 近 60 日日K
    "kline": (f"{PUSH2HIS}/stock/kline/get?secid=1.000300&klt=101&fqt=0&lmt=60&end=20500101"
              f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&ut={UT}"),
    # —— 其余三大指数的近 60 日日K（「历史与位置」页要四条线一起看位置）——
    # ⚠️ 这三条走【腾讯】`fqkline`，不走东财：
    #    2026-10-16 傍晚实测东财 push2* 整族 API 对本机不可达（curl 也一样，代理层挡了），
    #    而腾讯可用且浏览器 CORS 通过。两源收盘价逐一对得上（上证 3716.280 / 深证 11802.460 /
    #    创业板 2772.380 / 沪深300 4170.610 与东财一致），换源不影响口径。
    #    返回结构 {data:{sh000001:{day:[[日期,开,收,高,低,量],…]}}}，页面用 klLines() 统一解析。
    "kline_sh000001": f"{TX}/fqkline/get?param=sh000001,day,,,60,qfq",
    "kline_sz399001": f"{TX}/fqkline/get?param=sz399001,day,,,60,qfq",
    "kline_sz399006": f"{TX}/fqkline/get?param=sz399006,day,,,60,qfq",

    # ══ 全球市场（新增） ══════════════════════════════════════════════
    # 核心指数一次拿全：A股 4 + 港股 4 + 美股 4 + 全球主要 18
    "gl_core": (f"{PUSH2}/ulist.np/get?fltt=2&invt=2&secids={GL_CORE}"
                f"&fields=f1,f2,f3,f4,f5,f6,f12,f13,f14,f15,f16,f17,f18&ut={UT}"),
    # 全球指数全量（东财 m:100 共 63 个）——「全球」视图按 亚洲/欧洲/美洲/澳洲 分组
    "gl_world": (f"{PUSH2}/clist/get?pn=1&pz=80&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:100"
                 f"&fields=f12,f13,f14,f2,f3,f4,f6&ut={UT2}"),
    # 港股 / 美股精选知名标的（不用涨幅榜，见上面注释）
    "gl_hk": (f"{PUSH2}/ulist.np/get?fltt=2&invt=2&secids={GL_HK}"
              f"&fields=f1,f2,f3,f4,f6,f12,f13,f14,f15,f16,f17,f18&ut={UT}"),
    "gl_us": (f"{PUSH2}/ulist.np/get?fltt=2&invt=2&secids={GL_US}"
              f"&fields=f1,f2,f3,f4,f6,f12,f13,f14,f15,f16,f17,f18&ut={UT}"),
    # 分时：港股（09:30–16:00）/ 美股（北京时间 21:30–次日 04:00）
    "gl_trend_hk": (f"{PUSH2}/stock/trends2/get?secid=100.HSI"
                    f"&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
                    f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut={UT}"),
    "gl_trend_us": (f"{PUSH2}/stock/trends2/get?secid=100.DJIA"
                    f"&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
                    f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut={UT}"),
    "gl_trend_jp": (f"{PUSH2}/stock/trends2/get?secid=100.N225"
                    f"&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
                    f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut={UT}"),
    # 日K 近 60 日：港股 / 美股（切市场时第 6 段的「位置」跟着换）
    "gl_kline_hk": (f"{PUSH2HIS}/stock/kline/get?secid=100.HSI&klt=101&fqt=0&lmt=60&end=20500101"
                    f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&ut={UT}"),
    "gl_kline_us": (f"{PUSH2HIS}/stock/kline/get?secid=100.DJIA&klt=101&fqt=0&lmt=60&end=20500101"
                    f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&ut={UT}"),

    # ══ 资讯流（v8 新增，2026-10-17 实测可用） ════════════════════════════
    # ⚠️ 这族接口必须带 req_trace 参数（值随意），否则报
    #    {"code":0,"message":"Required String parameter 'req_trace' is not present"}
    # —— 7×24 全球快讯（滚动直播，titleColor=3 为重点条目，翻页把上次响应的
    #    data.sortEnd 填进 sortEnd 参数；fastColumn 只有 102 有效，350 返回 null）
    "news_724": ("https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
                 "?client=web&biz=web_724&fastColumn=102&sortEnd=&pageSize=20&req_trace=1"),
    # —— A 股复盘资讯（上市公司业绩/回购/机构动向，column=349）
    "news_a_rev": ("https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
                   "?client=web&biz=web_724&column=349&order=1&needInteractData=0"
                   "&page_index=1&page_size=20&req_trace=1"
                   "&fields=code,showTime,title,mediaName,summary,url&types=1,20"),
    # —— 环球/隔夜市场复盘（美股收评等，column=351；与 349 字段结构完全一致）
    "news_gl_rev": ("https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
                    "?client=web&biz=web_724&column=351&order=1&needInteractData=0"
                    "&page_index=1&page_size=20&req_trace=1"
                    "&fields=code,showTime,title,mediaName,summary,url&types=1,20"),
}

# 池子接口模板：{date} 必须是 YYYYMMDD 的交易日
# ⚠️ pagesize 取 80：`tc` 是总数（与 pagesize 无关），明细用来做「连板梯队」
POOL_TPL = {
    "ztpool": (f"{PUSH2EX}/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
               f"&dpt=wz.ztzt&Pageindex=0&pagesize=80&sort=fbt:asc&date={{date}}"),
    "dtpool": (f"{PUSH2EX}/getTopicDTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
               f"&dpt=wz.ztzt&Pageindex=0&pagesize=80&sort=fund:asc&date={{date}}"),
    "zbpool": (f"{PUSH2EX}/getTopicZBPool?ut=7eea3edcaed734bea9cbfc24409ed989"
               f"&dpt=wz.ztzt&Pageindex=0&pagesize=80&sort=fbt:asc&date={{date}}"),
}


# 主机掐断是按路径挑着来的（实测：ulist 放行、clist 被切），
# 所以不能靠「探一下主机通不通」做判断，必须到请求层逐条兜底。
MIRROR = {"push2.eastmoney.com": "push2test.eastmoney.com",
          "push2his.eastmoney.com": "push2test.eastmoney.com"}

# 腾讯日K失败时换东方财富镜像补：这三个指数是「历史与位置」第 1 段的主卡，
# 缺一条那一排就少一张图（响应形状与 klLines 兼容，页面无需改）
KLINE_ALT = {
    "kline_sh000001": "1.000001",
    "kline_sz399001": "0.399001",
    "kline_sz399006": "0.399006",
}
PUSH2_HIS_MIRROR = PUSH2HIS.replace("push2his.eastmoney.com", "push2test.eastmoney.com")


def mirror_of(url):
    """同源镜像，路径参数完全一致，只换主机。"""
    for a, b in MIRROR.items():
        if "//" + a + "/" in url:
            return url.replace("//" + a + "/", "//" + b + "/", 1)
    return None


def fetch_one(item):
    key, url = item
    targets = [url]
    alt = mirror_of(url)
    if alt:
        targets.append(alt)
    last = None
    for target in targets:
        ref = ("https://gu.qq.com/" if "gtimg.cn" in target
               else "https://quote.eastmoney.com/")
        for attempt in (1, 2):
            req = urllib.request.Request(target, headers={"User-Agent": UA, "Referer": ref})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return key, json.loads(r.read().decode("utf-8", "replace")), None
            except Exception as e:
                last = str(e)
                if attempt == 1:
                    time.sleep(0.4)
    return key, None, last


def fetch_all(pairs, label):
    out, errs = {}, []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for key, data, err in ex.map(fetch_one, pairs):
            if err:
                errs.append(f"{key}: {err}")
                print(f"  ❌ {key}: {err}")
            else:
                out[key] = data
                print(f"  ✅ {key}")
    return out, errs


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(here, "data", "raw.json"))
    args = ap.parse_args()

    print(f"[fetch] 阶段一 · 并发拉取 {len(ENDPOINTS)} 个公开接口…")
    bundle, errs = fetch_all(ENDPOINTS.items(), "phase1")

    # 从分时数据里取真实交易日（池子接口必须带这个日期）
    trade_date = None
    tr = (bundle.get("trend_sh000001") or {}).get("data") or {}
    if tr.get("trends"):
        trade_date = tr["trends"][0][:10].replace("-", "")
    if not trade_date:
        trade_date = time.strftime("%Y%m%d")
        print(f"  ⚠️ 分时数据缺失，池子日期退回本机日期 {trade_date}")
    print(f"[fetch] 阶段二 · 涨停/跌停/炸板池（date={trade_date}）…")
    pools = {k: v.format(date=trade_date) for k, v in POOL_TPL.items()}
    p2, e2 = fetch_all(pools.items(), "phase2")
    bundle.update(p2)
    errs += e2

    # 阶段三：腾讯日K没拿到的，换东方财富镜像补一次
    alt = {k: f"{PUSH2_HIS_MIRROR}/stock/kline/get?secid={secid}&klt=101&fqt=0&lmt=70"
                f"&end=20500101&fields1=f1,f2,f3,f4,f5,f6"
                f"&fields2=f51,f52,f53,f54,f55,f56&ut={UT}"
           for k, secid in KLINE_ALT.items() if k not in bundle}
    if alt:
        print(f"[fetch] 阶段三 · {len(alt)} 条指数日K换镜像重试…")
        p3, e3 = fetch_all(alt.items(), "phase3")
        bundle.update(p3)
        errs = [e for e in errs if e.split(":", 1)[0] not in p3] + e3

    bundle["_meta"] = {
        "fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tradeDate": trade_date,
        "source": "东方财富公开行情接口",
        "failed": errs,
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, separators=(",", ":"))

    size = os.path.getsize(args.out)
    total = len(ENDPOINTS) + len(POOL_TPL)
    print(f"[fetch] 已写 {args.out} ({size/1024:.1f} KB)  成功 {len(bundle)-1}/{total}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
