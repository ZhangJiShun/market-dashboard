#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新手行情看板 · 生成器 v2

架构（数据与算法分离）：
    data/raw.json    ← 公开接口的原始响应包（fetch_public.py 产出）
    build.py         ← 把原始包内嵌进 HTML 骨架（不做任何聚合）
    index.html       ← 产物。页面加载后：
                          ① 先用内嵌快照立刻渲染（秒开、离线可看）
                          ② 再直接调公开接口拉实时数据 → 覆盖渲染 + 挂「实时」徽标
                       —— 聚合逻辑只在页面 JS 里写一份，避免 Python/JS 两套算法漂移。

版本要点：
    * 按 A 股交易时段（上午 09:30–11:30 / 下午 13:00–15:00）拆分行情与资金
    * 午评 / 午后盘中快评 / 收评 三段式（对齐主流交易软件「位置 + 评」的命名）
    * 资金流向细化：主力净流入（含上午/下午拆分）、资金结构四档、
      行业板块流入流出 TOP、个股主力 TOP、两融
    * 降级路径：无需连接器，公开免费接口直连

用法：
    python3 build.py
    python3 build.py --raw data/raw.json --out index.html
"""

import argparse
import json
import os
import sys
import time

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>新手行情看板</title>
<style>
  :root{
    --primary:#1F4E78; --up:#D93636; --down:#12925C; --flat:#8A8F98;
    --border:#E2E6EB; --ink:#1F2329; --sub:#6B7280; --warn:#B45309;
    --bg:#F5F7FA; --card:#FFFFFF; --soft:#F7F9FC;
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0}
  body{background:var(--bg);color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",Arial,sans-serif;
    font-size:14px;line-height:1.65;-webkit-font-smoothing:antialiased}
  /* 两栏：左侧固定导航（页签 / 目录 / 显示内容），右侧正文。
     左侧 sticky 到顶，滚正文时导航一直在。 */
  .wrap{max-width:1320px;margin:0 auto;padding:0 22px 56px;
    display:grid;grid-template-columns:238px minmax(0,1fr);gap:30px;align-items:start}
  .side{position:sticky;top:0;max-height:100vh;overflow-y:auto;padding:22px 0 28px;
    display:flex;flex-direction:column;gap:16px;scrollbar-width:none}
  .side::-webkit-scrollbar{display:none}
  .main{min-width:0;padding-top:22px}

  /* 页头 */
  header.top{margin-bottom:18px}
  .top-line{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
  .top-line h1{margin:0;font-size:24px;letter-spacing:.5px;color:var(--primary)}
  .top-line .sub{color:var(--sub);font-size:13px}
  .top-line .badge{margin-left:auto;border-radius:999px;padding:3px 12px;font-size:12px;font-weight:600;border:1px solid}
  .badge-live{background:#EAF7EF;color:#0F7A4B;border-color:#BFE4CE}
  .badge-snap{background:#FFF7E6;color:#92600A;border-color:#F0DBA8}
  .badge-err{background:#FDEEEE;color:#B3261E;border-color:#F3C6C3}
  .badge-mix{background:#EEF4FB;color:#1F4E78;border-color:#C8D9EA}
  /* 「这块是旧的」就地提示：长在那一块的顶部，不往页面顶部堆 */
  .stale-line{margin:10px 0 0;padding:9px 12px;border-radius:8px;background:#FFF7E6;border:1px solid #F0DBA8;color:#7A4F07;font-size:12.5px;line-height:1.6}
  .stale-line b{font-weight:600}
  .top-note{color:var(--sub);font-size:12.5px;margin-top:8px}
  /* 可折叠的两处：页头长说明、术语 + 连板梯队。收起时只留一行标题，不占版面 */
  .top-fold{margin-top:6px}
  .top-fold>summary,.fold-detail>summary{cursor:pointer;font-size:12.5px;color:#1F4E78;
    padding:3px 0;list-style:none;user-select:none}
  .top-fold>summary::-webkit-details-marker,.fold-detail>summary::-webkit-details-marker{display:none}
  .top-fold>summary:before,.fold-detail>summary:before{content:'▸ ';color:#9AA3AD}
  .top-fold[open]>summary:before,.fold-detail[open]>summary:before{content:'▾ '}
  .fold-detail{margin:10px 0 0}
  .fold-detail>summary{font-weight:600;color:#4B5563}

  /* 交易时段状态条 */
  .session-bar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
    background:#fff;border:1px solid var(--border);border-radius:11px;padding:11px 16px;margin-top:12px}
  .sb-state{display:flex;align-items:center;gap:8px;font-weight:700;color:var(--primary);font-size:14.5px}
  .dot{width:9px;height:9px;border-radius:50%;background:#B9C0C8;flex:0 0 auto}
  .dot.on{background:#12925C;box-shadow:0 0 0 4px rgba(18,146,92,.16)}
  .sb-clock{color:var(--sub);font-size:12.5px;font-variant-numeric:tabular-nums}
  .sb-periods{display:flex;gap:8px;margin-left:auto;flex-wrap:wrap}
  .sb-p{font-size:12px;padding:2px 10px;border-radius:999px;background:#F1F4F8;color:var(--sub);white-space:nowrap}
  .sb-p.active{background:#EAF2F8;color:var(--primary);font-weight:700;border:1px solid #CDE0EF}
  .sb-track{flex:1 1 100%;height:6px;background:#EEF1F5;border-radius:99px;overflow:hidden;margin-top:2px}
  .sb-fill{height:100%;background:linear-gradient(90deg,#4A90C2,#1F4E78);border-radius:99px;transition:width .4s}

  /* 卡片 */
  .card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px 22px;margin-bottom:18px;
    box-shadow:0 1px 2px rgba(16,24,40,.04)}
  .card-head{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:6px}
  .card-head h2{margin:0;font-size:17px;color:var(--primary);display:flex;align-items:center;gap:9px}
  .no{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;
    background:var(--primary);color:#fff;font-size:13px;font-style:normal;font-weight:700;flex:0 0 auto}
  .stamp{margin-left:auto;color:var(--sub);font-size:12px}
  .lead{margin:6px 0 14px;color:#374151;font-size:13.5px}
  .lead b{color:var(--ink)}
  .hint{margin:10px 0 0;color:var(--sub);font-size:12.5px;line-height:1.6}
  .hint b{color:#4B5563}
  .subhead{font-size:13px;font-weight:700;color:var(--primary);margin:16px 0 9px;display:flex;align-items:center;gap:8px}
  .subhead::before{content:"";width:3px;height:13px;background:var(--primary);border-radius:2px}
  .subhead .tip{font-weight:400;color:var(--sub);font-size:12px}

  /* 按钮 */
  .head-actions{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
  .btn{font:inherit;font-size:12.5px;padding:5px 12px;border-radius:7px;cursor:pointer;border:1px solid var(--primary);
    background:var(--primary);color:#fff;font-weight:600;transition:opacity .15s}
  .btn:hover{opacity:.88}
  .btn.ghost{background:#fff;color:var(--primary);border-color:#CDE0EF;font-weight:500}

  /* 通用表格 */
  .table-wrap{overflow-x:auto;margin:0 -4px}
  table.grid{width:100%;border-collapse:separate;border-spacing:0}
  table.grid th{background:#F2F5F9;color:var(--primary);font-size:12px;font-weight:700;text-align:left;
    padding:8px 9px;border-top:1px solid var(--border);border-bottom:1px solid var(--border);white-space:nowrap}
  table.grid td{padding:6px 9px;border-bottom:1px solid #EEF1F5;vertical-align:middle;font-variant-numeric:tabular-nums}
  table.grid tr:last-child td{border-bottom:1px solid var(--border)}
  .num{text-align:right}
  .name-cell{font-weight:600}
  .code-cell{color:#9AA3AD;font-size:11.5px;margin-left:5px;font-weight:400}
  .bar-cell{min-width:110px}
  .minibar{height:7px;border-radius:4px;background:#EEF1F5;overflow:hidden;position:relative}
  .minibar i{display:block;height:100%;border-radius:4px}
  .minibar i.up{background:var(--up)} .minibar i.down{background:var(--down)}

  /* 持仓表 */
  table.hold{width:100%;border-collapse:separate;border-spacing:0;min-width:980px}
  table.hold th{background:#F2F5F9;color:var(--primary);font-size:12px;font-weight:700;text-align:left;
    padding:8px 9px;border-top:1px solid var(--border);border-bottom:1px solid var(--border);white-space:nowrap}
  table.hold td{padding:5px 9px;border-bottom:1px solid #EEF1F5;vertical-align:middle}
  table.hold tr:last-child td{border-bottom:1px solid var(--border)}
  .c-num{text-align:right;width:8.8%}.c-name{width:14%}.c-code{width:16%}.c-del{width:38px}
  .c-px{min-width:96px}
  table.hold input{font:inherit;font-size:13px;width:100%;border:1px solid transparent;background:#FFFCF3;
    border-radius:6px;padding:5px 7px;color:#1F4E79}
  .c-num input{text-align:right}
  table.hold input:focus{outline:none;border-color:#9EC3E0;background:#fff}
  table.hold input[readonly]{background:transparent;border-color:transparent;cursor:default}
  .calc{font-variant-numeric:tabular-nums;font-weight:600;white-space:nowrap}
  .calc .sub{display:block;font-weight:400;font-size:11.5px;color:var(--flat)}
  .del{border:0;background:transparent;color:#B9C0C8;cursor:pointer;font-size:16px;line-height:1;padding:4px 6px;border-radius:6px}
  .del:hover{background:#FDEEEE;color:var(--up)}
  .badge-src{font-size:10.5px;color:#9AA3AD;font-weight:400;margin-left:4px}

  /* 自动查询：代码格 / 候选行 / 状态字 */
  .code-w{display:flex;align-items:center;gap:4px}
  .code-w input{flex:1 1 auto;min-width:58px}
  .lk-btn{flex:0 0 auto;border:0;background:#EAF2F8;color:var(--primary);font-size:11px;font-weight:700;
    border-radius:5px;padding:4px 6px;cursor:pointer;line-height:1;font-family:inherit}
  .lk-btn:hover{background:#DCE9F5}
  .lk-state{font-size:11px;color:#9AA3AD;white-space:nowrap}
  .lk-state.busy{color:#B98900}.lk-state.ok{color:#2E7D32}.lk-state.err{color:#B03A3A}
  .lnk{font-size:10.5px;color:#7E8A96;cursor:pointer;margin-left:4px;
    text-decoration:none;border-bottom:1px dotted #C7CDD4;font-weight:400}
  .lnk:hover{color:var(--primary);border-bottom-color:var(--primary)}
  tr.pick-tr td{background:#F7FAFD;padding:7px 9px;border-bottom:1px solid #E6EEF6}
  .pick-box{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
  .pick-lab{font-size:12px;color:var(--sub);font-weight:600}
  button.pick{border:1px solid var(--border);background:#fff;border-radius:7px;padding:5px 9px;cursor:pointer;
    display:inline-flex;align-items:center;gap:6px;font:inherit;font-size:12.5px;color:#1F4E79}
  button.pick:hover{border-color:#9EC3E0;background:#F2F7FC}
  .pk-code{font-variant-numeric:tabular-nums;color:var(--sub);font-size:11.5px}
  .sg-type{font-size:10.5px;color:#8A949E;background:#F2F5F9;border-radius:4px;padding:1px 5px}
  button.pick-x{border:0;background:transparent;color:#9AA3AD;font-size:12px;cursor:pointer;padding:4px 6px;font-family:inherit}
  button.pick-x:hover{color:var(--up)}

  /* 指数格 */
  .idx-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:12px}
  .idx{border:1px solid var(--border);border-radius:10px;padding:12px 14px;background:#FCFDFE}
  .idx-name{font-size:13px;color:var(--sub);font-weight:600}
  .idx-code{font-size:11px;color:#B0B8C1;margin-left:6px;font-weight:400}
  .idx-price{font-size:25px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.5px;margin-top:2px}
  .idx-chg{font-size:13.5px;font-weight:700;font-variant-numeric:tabular-nums}
  .idx-abs{font-weight:400;color:var(--sub);margin-left:4px;font-size:12px}
  .idx-split{display:flex;gap:0;margin-top:8px;border-top:1px dashed #EDF0F3;padding-top:7px}
  .idx-split div{flex:1;font-size:11.5px;color:var(--sub)}
  .idx-split div+div{border-left:1px solid #EDF0F3;padding-left:10px;margin-left:10px}
  .idx-split b{display:block;font-size:13px;font-variant-numeric:tabular-nums;margin-top:1px}
  .idx-foot{font-size:11px;color:#9AA3AD;margin-top:7px}

  /* 三段总结 */
  .seg-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:12px}
  .seg{border:1px solid var(--border);border-radius:11px;padding:14px 16px;background:#FCFDFE;position:relative;overflow:hidden}
  .seg::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:#C3D4E3}
  .seg-am::before{background:#4A90C2} .seg-pm::before{background:#7A6BB5} .seg-all::before{background:#1F4E78}
  .seg-title{font-size:12.5px;font-weight:700;color:var(--primary);display:flex;align-items:center;gap:8px}
  .seg-title .range{font-weight:400;color:var(--sub);font-size:11.5px}
  .seg-main{font-size:27px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.5px;margin:6px 0 2px}
  .seg-unit{font-size:12px;font-weight:500;color:var(--sub)}
  .seg-desc{font-size:12.8px;color:#374151;margin-top:8px;padding-top:8px;border-top:1px dashed #EDF0F3;line-height:1.7}
  .seg-desc b{color:var(--ink)}
  .seg-kv{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:7px;font-size:11.5px;color:var(--sub)}
  .seg-kv span b{color:var(--ink);font-variant-numeric:tabular-nums}

  /* 整宽复盘块：左侧是这一段的几个数字，右侧是文字复盘 */
  .seg-full{display:grid;grid-template-columns:236px 1fr;gap:22px;align-items:start;
    border:1px solid var(--border);border-radius:11px;padding:15px 18px;margin-bottom:12px;background:#FCFDFE}
  .seg-side{border-right:1px solid #EEF1F5;padding-right:18px}
  .seg-side .seg-main{font-size:30px;margin:5px 0 3px;line-height:1.1}
  .seg-kv2{display:flex;flex-direction:column;gap:4px;margin-top:10px;font-size:12px;color:var(--sub)}
  .seg-kv2 b{color:var(--ink);font-variant-numeric:tabular-nums}
  .seg-am .seg-side{border-right-color:#F3D9D9}
  .seg-all{background:#F8FBFF;border-color:#DCE7F3}
  @media (max-width:780px){
    .seg-full{grid-template-columns:1fr;gap:12px}
    .seg-side{border-right:0;padding-right:0;border-bottom:1px solid #EEF1F5;padding-bottom:12px}
    .seg-kv2{flex-direction:row;flex-wrap:wrap;gap:6px 16px}
  }

  /* 资金流 */
  .fund-hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
  .fcard{border:1px solid var(--border);border-radius:10px;padding:13px 15px;background:#FCFDFE}
  .fcard .k{font-size:12.5px;color:var(--sub);font-weight:600}
  .fcard .v{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.5px}
  .fcard .n{font-size:11.5px;color:#9AA3AD;margin-top:3px}
  .struct{display:flex;flex-direction:column;gap:8px;margin-top:4px}
  .st-row{display:grid;grid-template-columns:78px 1fr 96px;align-items:center;gap:12px;font-size:12.5px}
  .st-k{color:var(--sub);font-weight:600}
  .st-v{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
  .st-track{height:16px;background:#F1F4F8;border-radius:5px;position:relative;overflow:hidden}
  .st-track .mid{position:absolute;left:50%;top:0;bottom:0;width:1px;background:#D8DEE5}
  .st-track i{position:absolute;top:2px;bottom:2px;border-radius:3px}
  .st-track i.up{background:var(--up);left:50%} .st-track i.down{background:var(--down);right:50%}
  /* 三段式：上涨 | 平盘 | 下跌，各自按家数占比给宽度，三段加起来正好铺满 */
  .st-track i.flat{background:#C7CDD4}
  .st-track i.up,.st-track i.down,.st-track i.flat{right:auto}
  .chart-meta b.flat{color:#6B7280}

  .two-col{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:16px}
  .rank-list{list-style:none;margin:0;padding:0}
  .rank-list li{display:grid;grid-template-columns:20px 1fr auto auto;align-items:center;gap:10px;
    padding:6px 0;border-bottom:1px dashed #EEF1F5;font-size:13px}
  .rank-list li:last-child{border-bottom:0}
  .rk{color:#B0B8C1;font-size:11.5px;font-weight:700;text-align:center}
  .rk-nm{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .rk-pct{font-variant-numeric:tabular-nums;font-size:12px;min-width:56px;text-align:right}
  .rk-amt{font-variant-numeric:tabular-nums;font-weight:700;min-width:78px;text-align:right}

  /* 图 */
  svg.spark{display:block;width:100%;height:150px}
  .chart-legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px;font-size:12px;color:var(--sub)}
  .chart-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
  .chart-meta{display:flex;gap:20px;flex-wrap:wrap;margin-top:10px;font-size:12.5px;color:var(--sub)}
  .chart-meta b{color:var(--ink);font-variant-numeric:tabular-nums}

  /* 颜色 */
  .up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--flat)}
  .warn{color:var(--warn)} .muted{color:#B9C0C8}
  .tag{font-size:11px;font-weight:700;padding:1px 6px;border-radius:4px;white-space:nowrap}
  .tag-hit{background:#FDEEEE;color:var(--up)}
  .tag-wait{background:#F1F4F8;color:var(--sub)}
  .tag-trig{background:#FDF6E3;color:var(--warn)}
  .tag-live{background:#EAF7EF;color:#0F7A4B}
  .tag-snap{background:#FFF7E6;color:#92600A}

  footer{margin-top:22px;color:var(--sub);font-size:12px;line-height:1.75}
  footer .disc{background:#FFFBF0;border:1px solid #F3E3BE;border-radius:9px;padding:11px 14px;color:#8A6D3B;margin-bottom:10px}
  footer b{color:#4B5563}

  /* ── 目录导航（吸顶） ───────────────────────────── */
  nav.toc{display:flex;flex-direction:column;gap:2px;overflow:visible;
    padding:0;margin:0;background:none;box-shadow:none}
  .toc-item{display:flex;align-items:flex-start;gap:8px;padding:7px 10px;border-radius:8px;
    border:1px solid transparent;background:none;color:#4B5563;font-size:12.5px;cursor:pointer;
    text-align:left;line-height:1.5;transition:background .18s,border-color .18s,color .18s}
  .toc-item i{font-style:normal;font-weight:700;color:#B0B8C1;font-size:11px;flex:0 0 auto;padding-top:1px}
  .toc-item em{font-style:normal;color:#B0B8C1;font-size:11px}
  .toc-item:hover{border-color:#CFDDEA;background:#F2F7FC}
  .toc-item.on{background:var(--primary);border-color:var(--primary);color:#fff;font-weight:700}
  .toc-item.on i,.toc-item.on em{color:rgba(255,255,255,.72)}
  /* 三组之间的分隔（一条细线；编号只由目录的 1/2/3/4 提供，别在这里再起一套） */
  .toc-sep{width:auto;height:1px;background:#E3E9F0;margin:7px 6px}
  section.card{scroll-margin-top:68px}

  /* ── 复盘文字（午评 / 午后 / 收评） ──────────────── */
  .report{margin-top:4px}
  .report .rp{display:grid;grid-template-columns:54px 1fr;gap:12px;margin:0;padding:10px 0;
    border-bottom:1px dashed #EEF1F5;font-size:13.5px;line-height:1.85;color:#374151}
  .report .rp:last-child{border-bottom:0}
  .rp-l{font-size:12px;font-weight:700;color:var(--primary);background:#EAF2F8;border-radius:5px;
    text-align:center;height:22px;line-height:22px;align-self:start;margin-top:2px}
  .rp-t b{font-variant-numeric:tabular-nums}
  .note{color:#9AA3AD;font-size:12px}

  /* ── 涨停梯队 ─────────────────────────────────── */
  .ladder{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
  .lad-chip{border:1px solid var(--border);border-radius:9px;padding:7px 14px;background:#FCFDFE;text-align:center;min-width:66px}
  .lad-chip b{display:block;font-size:18px;color:var(--primary);font-variant-numeric:tabular-nums;line-height:1.3}
  .lad-chip span{font-size:11.5px;color:var(--sub)}
  .lad-chip.hi{background:#FDEEEE;border-color:#F3C6C3}
  .lad-chip.hi b{color:var(--up)}

  /* ── 板块地图 ─────────────────────────────────── */
  .board-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:14px}
  .board-list{list-style:none;margin:0;padding:0}
  .board-list li{display:grid;grid-template-columns:1fr auto;gap:10px;padding:6px 0;font-size:13px;
    border-bottom:1px dashed #EEF1F5;align-items:center}
  .board-list li:last-child{border-bottom:0}
  .bl-name{font-weight:600;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .bl-lead{font-size:11px;color:#9AA3AD;font-weight:400;margin-left:6px}
  .bl-right{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;font-size:12.5px}
  .bl-sub{display:block;font-size:11px;color:#9AA3AD;font-weight:400}
  /* 成分股太少的行业：给一条显眼的小标记，否则读者会把「教育运营及其他 +9.84%」当成大方向 */
  .bl-thin-tag{display:block;font-size:11px;color:#854F0B;background:#FAEEDA;border-radius:4px;padding:0 5px;margin-top:2px}

  /* ── 成交额柱（看今天的量算大算小） ─────────────── */
  .volbars{display:flex;gap:8px;height:172px;padding:12px 10px;border:1px solid var(--border);
    border-radius:10px;background:#FCFDFE}
  .vb{flex:1;display:flex;flex-direction:column;align-items:center;gap:5px;min-width:0}
  .vb-v{font-size:11px;color:var(--sub);font-variant-numeric:tabular-nums;white-space:nowrap}
  .vb-track{flex:1;width:100%;display:flex;align-items:flex-end;justify-content:center}
  .vb-track i{display:block;width:100%;max-width:46px;background:#C6DAEB;border-radius:4px 4px 0 0;min-height:5px}
  .vb.now i{background:var(--primary)}
  .vb-d{font-size:10.5px;color:#9AA3AD;white-space:nowrap}
  .vb.now .vb-v{color:var(--primary);font-weight:700}

  /* ── 一级页签：今天 / 历史与位置 ─────────────────── */
  .page-tabs{display:flex;flex-direction:column;gap:7px;padding:0}
  .pt{display:flex;flex-direction:column;gap:3px;align-items:flex-start;cursor:pointer;width:100%;text-align:left;
    border:1px solid var(--border);background:#fff;border-radius:10px;padding:9px 13px;
    font-family:inherit;font-size:15px;font-weight:600;color:#5A6572;line-height:1.25;
    transition:border-color .15s,background .15s}
  .pt em{font-style:normal;font-size:11.5px;font-weight:400;color:#9AA3AD}
  .pt:hover{border-color:#9EC3E0;background:#F5F9FD}
  .pt.on{background:var(--primary);border-color:var(--primary);color:#fff}
  .pt.on em{color:rgba(255,255,255,.75)}
  .page-hint{margin:9px 0 0;font-size:12.5px;color:var(--sub);line-height:1.7}
  /* ── 显示内容（分层）：精简 / 完整 / 自定义 ───────────────── */
  .lay-bar{display:flex;flex-direction:column;gap:8px;padding:0}
  .lay-t{font-size:12.5px;color:var(--sub);font-weight:600}
  .lay-chips{display:flex;gap:6px;flex-wrap:wrap}
  .lay-chips .chip{padding:4px 12px;font-size:12px}
  .lay-note{font-size:12px;color:#9AA3AD}
  .lay-panel{flex:none;display:flex;flex-direction:column;gap:12px;margin-top:2px;
    border:1px solid var(--border);border-radius:10px;background:var(--soft);padding:11px 12px}
  .lay-g{display:flex;flex-direction:column;align-items:flex-start;gap:6px;font-size:12px;color:var(--sub)}
  .lay-g b{color:var(--primary);font-size:12px}
  .lay-item{display:flex;align-items:flex-start;gap:6px;cursor:pointer;color:#4A5560;line-height:1.5}
  .lay-item em{display:none}
  .lay-item em{font-style:normal;color:#9AA3AD;font-size:11.5px}
  .lay-lock{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:999px;
    background:#EDF3F9;color:#5A6572;font-size:12px;margin:0 4px 4px 0}
  /* 自定义档关掉的段：整段隐藏，目录也自动少一项（curToc 会跳过它） */
  .lay-off{display:none!important}
  /* 分页显隐：只认 #root 的直接子节点（那三个 <div data-page>）。
     写成「先全隐、再按当前页显」而不是两两互斥，加第四页时不用改规则。 */
  #root > [data-page]{display:none}
  #root[data-page="today"] > [data-page="today"],
  #root[data-page="hist"]  > [data-page="hist"],
  #root[data-page="book"]  > [data-page="book"]{display:block}

  /* ── 图表：悬停读数（十字准线 + 浮层） ─────────────── */
  .chart-wrap{position:relative;border:1px solid var(--border);border-radius:10px;
    overflow:hidden;background:#FCFDFE;cursor:crosshair}
  .chart-wrap svg.spark{cursor:crosshair}
  .chart-foot{display:flex;justify-content:space-between;gap:10px;font-size:11.5px;
    color:#9AA3AD;font-variant-numeric:tabular-nums;padding:5px 8px 0}
  .chart-tip{position:fixed;z-index:60;pointer-events:none;background:rgba(28,36,46,.94);
    color:#fff;border-radius:8px;padding:8px 11px;font-size:12px;line-height:1.65;
    font-variant-numeric:tabular-nums;white-space:nowrap;box-shadow:0 4px 16px rgba(0,0,0,.18)}
  .chart-tip[hidden]{display:none}
  .chart-tip .ct-t{font-weight:700;color:#fff;margin-bottom:3px;font-size:12px}
  .chart-tip .ct-r{display:flex;justify-content:space-between;gap:14px}
  .chart-tip .ct-k{color:#AEB8C4}
  .chart-tip .ct-v{font-weight:600}
  .chart-tip .up{color:#FF8F8F}
  .chart-tip .down{color:#7FD6A8}
  .chart-tip hr{border:0;border-top:1px solid rgba(255,255,255,.18);margin:5px 0}
  .vb{cursor:pointer}
  .vb:hover i{filter:brightness(.94)}

  /* ── 历史与位置：多指数卡片 + 区间位置条 ───────────── */
  .hist-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}
  .hist-card{border:1px solid var(--border);border-radius:11px;background:#FCFDFE;padding:13px 15px 11px}
  .hist-card .hc-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:4px}
  .hist-card .hc-name{font-size:13.5px;font-weight:600;color:var(--ink)}
  .hist-card .hc-last{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums;margin-left:auto}
  .hist-card .hc-chg{font-size:12.5px;font-variant-numeric:tabular-nums;font-weight:600}
  .hist-card .chart-wrap{border:0;background:transparent;border-radius:0}
  .posbar{margin-top:8px}
  .posbar .pb-track{position:relative;height:7px;border-radius:999px;
    background:linear-gradient(90deg,#DCEDDD 0%,#F2F2F2 50%,#F7DEDE 100%)}
  .posbar .pb-dot{position:absolute;top:-3px;width:13px;height:13px;border-radius:50%;
    background:#fff;border:2.5px solid var(--primary);transform:translateX(-50%)}
  .posbar .pb-lab{display:flex;justify-content:space-between;font-size:11px;color:#9AA3AD;
    margin-top:5px;font-variant-numeric:tabular-nums}
  .hist-note{margin-top:10px;font-size:12.5px;color:var(--sub);line-height:1.75}
  @media (max-width:900px){ .hist-grid{grid-template-columns:1fr} }

  /* ── 我的账本（工作台） ───────────────────────── */
  .bk-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 12px}
  .bk-sep{flex:0 0 auto;width:1px;height:18px;background:var(--border);margin:0 4px}
  .bk-btn{font:inherit;font-family:inherit;font-size:12.5px;padding:6px 13px;border:1px solid var(--border);
    border-radius:8px;background:#fff;color:var(--primary);cursor:pointer;font-weight:600;white-space:nowrap}
  .bk-btn:hover{background:#F3F7FC}
  .bk-btn.primary{background:var(--primary);border-color:var(--primary);color:#fff}
  .bk-btn.primary:hover{background:#173C5C}
  .bk-btn.danger{color:#B23A3A}
  /* 云端备份条（我的账本页最上面一条）：说清楚「现在存哪」+ 一个动作 */
  .cloud-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;background:var(--card);
    border:1px solid var(--border);border-left:4px solid #C9D3DE;border-radius:12px;
    padding:11px 14px;margin:0 0 14px;box-shadow:0 1px 2px rgba(20,40,70,.04)}
  .cloud-bar.on{border-left-color:#2E9E5B}
  .cloud-bar.err{border-left-color:#D08B8B}
  .cloud-bar .cb-dot{flex:0 0 auto;width:8px;height:8px;border-radius:50%;background:#C9D3DE}
  .cloud-bar .cb-dot.on{background:#2E9E5B}
  .cloud-bar .cb-dot.wait{background:#B98900}
  .cloud-bar .cb-dot.err{background:#B03A3A}
  .cloud-bar .cb-txt{flex:1 1 260px;min-width:0;font-size:12.5px;color:var(--sub);line-height:1.6}
  .cloud-bar .cb-txt b{color:var(--ink);font-weight:700}
  .cloud-bar .bk-btn{flex:0 0 auto}
  .bk-x{font:inherit;font-family:inherit;border:1px solid var(--border);background:#fff;color:#9AA3AD;
    width:24px;height:24px;line-height:1;border-radius:6px;cursor:pointer}
  .bk-x:hover{color:#B23A3A;border-color:#E7B9B9}
  .lg-wrap{overflow-x:auto;margin:0 -4px}
  table.hold.lg{min-width:860px}
  table.hold.lg td.num,table.hold.lg th.num{text-align:right;font-variant-numeric:tabular-nums}
  .lg-side{display:inline-flex;border:1px solid var(--border);border-radius:7px;overflow:hidden}
  .lg-side button{font:inherit;font-family:inherit;font-size:12px;border:0;background:#fff;color:var(--sub);
    padding:5px 10px;cursor:pointer}
  .lg-side button+button{border-left:1px solid var(--border)}
  .lg-side button[data-side="buy"].on{background:var(--up);color:#fff;font-weight:700}
  .lg-side button[data-side="sell"].on{background:var(--down);color:#fff;font-weight:700}
  .bk-mini{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}
  .bm-card{border:1px solid var(--border);border-radius:10px;padding:11px 13px;background:var(--soft)}
  .bm-card.flat{opacity:.72}
  .bm-name{font-size:13px;font-weight:700;color:var(--primary);margin-bottom:7px}
  .bm-name em{font-style:normal;font-weight:400;font-size:11px;color:#9AA3AD;margin-left:6px}
  .bm-row{display:flex;justify-content:space-between;font-size:12.5px;padding:2px 0;color:var(--sub)}
  .bm-row b{color:var(--ink);font-variant-numeric:tabular-nums}
  .bm-warn{margin-top:6px;font-size:11.5px;color:var(--warn)}
  .bk-hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
  .bh{border:1px solid var(--border);border-radius:10px;padding:12px 14px;background:#fff}
  .bh .k{display:block;font-size:12px;color:var(--sub)}
  .bh .v{display:block;font-size:21px;font-weight:700;font-variant-numeric:tabular-nums;margin:3px 0 2px}
  .bh em{font-style:normal;font-size:11px;color:#9AA3AD}
  .bh-in{font:inherit;font-family:inherit;font-size:19px;font-weight:700;width:100%;border:1px solid transparent;
    background:#FFFCF3;border-radius:6px;padding:2px 5px;color:#1F4E79;margin:3px 0 2px;font-variant-numeric:tabular-nums}
  .bh-in:focus{outline:none;border-color:#9EC3E0;background:#fff}
  .est-tag{font-size:10.5px;color:var(--warn);border:1px solid #EBD9BC;background:#FDF6EC;
    border-radius:5px;padding:1px 5px;white-space:nowrap}
  .bk-empty{margin-top:10px;padding:14px 16px;border:1px dashed var(--border);border-radius:10px;
    background:var(--soft);font-size:12.5px;color:var(--sub);line-height:1.8}
  .jrn{margin-top:12px;border:1px solid var(--border);border-radius:10px;padding:14px 16px;background:#fff}
  .jrn-day{font-size:12px;color:var(--sub);margin-bottom:10px;font-variant-numeric:tabular-nums}
  .jrn-row{display:block;margin-bottom:11px}
  .jrn-row>span{display:block;font-size:12.5px;color:var(--primary);font-weight:600;margin-bottom:5px}
  .jrn-row input,.jrn-row textarea{font:inherit;font-family:inherit;font-size:13px;width:100%;
    border:1px solid var(--border);border-radius:7px;padding:7px 9px;background:#FFFCF3;color:#1F4E79;
    box-sizing:border-box;resize:vertical}
  .jrn-row input:focus,.jrn-row textarea:focus{outline:none;border-color:#9EC3E0;background:#fff}
  .chips{display:flex;gap:8px;flex-wrap:wrap}
  .chip{font:inherit;font-family:inherit;font-size:12.5px;padding:5px 14px;border:1px solid var(--border);
    border-radius:999px;background:#fff;color:var(--sub);cursor:pointer}
  .chip.on{background:var(--primary);border-color:var(--primary);color:#fff;font-weight:700}
  .jrn-foot{display:flex;align-items:center;gap:12px}
  .jrn-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}
  .jl{border:1px solid var(--border);border-radius:10px;padding:11px 13px;background:var(--soft);font-size:12.5px;color:var(--sub)}
  .jl-d{font-weight:700;color:var(--primary);margin-bottom:5px;font-variant-numeric:tabular-nums}
  .jl-mood{font-style:normal;font-weight:400;font-size:11px;color:#8C6D3F;background:#FBF3E4;
    border-radius:999px;padding:1px 8px;margin-left:8px}
  .jl p{margin:3px 0;line-height:1.65}
  .jl p b{color:var(--ink);font-weight:600;margin-right:5px}
  @media (max-width:900px){ .bk-hero{grid-template-columns:repeat(2,1fr)} .bk-mini{grid-template-columns:1fr} }

  /* ── 视图切换按钮组 ───────────────────────────── */
  .view-switch{display:flex;border:1px solid var(--border);border-radius:8px;overflow:hidden;background:#fff}
  .vs{font:inherit;font-size:12.5px;padding:5px 14px;border:0;background:transparent;color:var(--sub);
    cursor:pointer;font-family:inherit;white-space:nowrap}
  .vs.on{background:var(--primary);color:#fff;font-weight:700}
  .watch-sum{display:flex;gap:20px;flex-wrap:wrap;margin:2px 0 12px;font-size:13px;color:var(--sub)}
  .watch-sum b{font-variant-numeric:tabular-nums;font-size:15px;color:var(--ink);font-weight:700}

  @media (max-width:640px){
    .wrap{padding:16px 12px 40px}
    .card{padding:16px 14px}
    .top-line h1{font-size:20px}
    .top-line .badge{margin-left:0}
    .head-actions{width:100%;margin-left:0}
    .st-row{grid-template-columns:64px 1fr 84px;gap:8px}
    .idx-price{font-size:22px}
    .seg-main{font-size:24px}
    .toc-item em{display:none}
    .view-switch{order:3;margin-left:0}
  }

  /* ── 全球市场：市场切换 / 四地时钟 / 区域表 ─────────────── */
  .mkt-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:2px 0 13px}
  .mt{font-family:inherit;font-size:14px;font-weight:600;padding:8px 18px;border-radius:9px;
    border:1px solid var(--border);background:#fff;color:#4B5563;cursor:pointer;
    transition:background .18s,border-color .18s,color .18s;white-space:nowrap}
  .mt:hover{border-color:#9EC3E0;background:#F2F7FC}
  .mt.on{background:var(--primary);border-color:var(--primary);color:#fff}
  .mt em{font-style:normal;font-size:11px;font-weight:400;margin-left:7px;color:#B0B8C1}
  .mt.on em{color:rgba(255,255,255,.75)}
  /* ── 此刻各地几点 / 谁在开市（原来是一排城市钟 + 一排状态灯，两套名字对不上，已合并） ── */
  .tzbar{margin-bottom:15px}
  .tzbar-h{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:9px}
  .tzbar-h b{font-size:13.5px;color:var(--ink)}
  .tzbar-h span{font-size:12px;color:#8A939D;line-height:1.65;max-width:760px}
  .tzgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
  .tzc{border:1px solid var(--border);border-radius:11px;padding:11px 14px;background:#FCFDFE}
  .tzc.on{border-color:#E9B6B6;background:#FDF4F4}
  .tzc.break{border-color:#EBD9AE;background:#FEFAF0}
  .tzc-n{font-size:13px;font-weight:600;color:var(--ink);display:flex;align-items:baseline;
    gap:6px;flex-wrap:wrap}
  .tzc-n em{font-style:normal;font-size:10.5px;color:#9AA3AD;font-weight:400}
  .tzc-t{font-size:22px;font-weight:700;color:var(--ink);font-variant-numeric:tabular-nums;
    letter-spacing:-.5px;margin:4px 0 2px}
  .tzc-s{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--sub)}
  .tzc-s i{width:7px;height:7px;border-radius:50%;background:#C7CDD4;flex:0 0 auto}
  .tzc.on .tzc-s{color:#B93A3A;font-weight:600}
  .tzc.on .tzc-s i{background:#D93636;box-shadow:0 0 0 3px rgba(217,54,54,.15)}
  .tzc.break .tzc-s{color:#8A6A1F}
  .tzc.break .tzc-s i{background:#D9A93A}
  .tzc-o{margin-top:7px;padding-top:7px;border-top:1px dashed #E7EBF0;font-size:11px;color:#9AA3AD}
  .gidx-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
  .gcard{border:1px solid var(--border);border-radius:11px;padding:13px 15px;background:#FCFDFE}
  .gcard .gn{font-size:13.5px;font-weight:600;color:var(--ink);display:flex;align-items:baseline;gap:7px;flex-wrap:wrap}
  .gcard .gn span{font-size:11px;color:#9AA3AD;font-weight:400}
  .gcard .gp{font-size:25px;font-weight:700;font-variant-numeric:tabular-nums;margin:8px 0 3px;letter-spacing:-.5px}
  .gcard .gc{font-size:13px;font-variant-numeric:tabular-nums}
  .gcard .gf{margin-top:9px;font-size:11.5px;color:#9AA3AD;font-variant-numeric:tabular-nums}
  .g-sub{margin-top:16px}
  .wgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:0 18px}
  /* ⚠️ 放进两栏容器时改成单列：3 列挤在 ~600px 里会顶破卡片右边界（08-16 实测截图） */
  .two-col .wgrid{grid-template-columns:1fr}
  .wrow{display:flex;align-items:baseline;gap:8px;font-size:12.5px;padding:4px 0;border-bottom:1px dashed #EDF1F5}
  .wrow .wn{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#4B5563}
  .wrow .wp{font-variant-numeric:tabular-nums;color:#6B7280}
  .wrow .wc{font-variant-numeric:tabular-nums;font-weight:600;min-width:62px;text-align:right}
  .g-trend .gt-head{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin:10px 0 6px}
  .g-trend .gt-head b{font-size:23px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.5px}
  .g-trend .gt-head span{font-size:13.5px;font-variant-numeric:tabular-nums;font-weight:600}
  .g-trend .gt-head i{font-style:normal;font-size:12.5px;color:var(--sub);font-variant-numeric:tabular-nums}
  .g-trend svg.spark{width:100%;height:210px;display:block}
  .gl-note{margin-top:16px;padding:11px 14px;border-radius:9px;background:#F7F9FC;
    border:1px solid var(--border);font-size:12.5px;color:var(--sub);line-height:1.8}
  .gl-note b{color:var(--ink)}
  @media (max-width:900px){ .gidx-grid{grid-template-columns:repeat(2,1fr)} .wgrid{grid-template-columns:repeat(2,1fr)} .tzgrid{grid-template-columns:repeat(2,1fr)} }
  @media (max-width:640px){
    .gidx-grid{grid-template-columns:1fr}
    .wgrid{grid-template-columns:1fr}
    .tzgrid{grid-template-columns:1fr}
    .mt{padding:7px 13px;font-size:13px}
    .mt em{display:none}
  }
  /* ── 窄屏回退：左栏收回顶部，恢复横排 ───────────────────── */
  @media (max-width:1080px){
    /* 单栏时用 display:contents 让 header / root / footer 变成 .wrap 的直接子元素，
       再用 order 把标题排到导航前面（否则标题会被挤到页签下面）。 */
    /* ⚠️ align-items 必须改回 stretch：基础样式是 grid 的 align-items:start，
       换成 flex 后它会让子项按「内容宽度」排，于是被 980px 的持仓表
       撑成整页横向溢出（实测 375 视口下正文宽 1002）。 */
    .wrap{display:flex;flex-direction:column;align-items:stretch;max-width:1060px;padding:26px 20px 56px}
    .main{display:contents}
    header.top{order:1}
    .side{order:2;position:static;max-height:none;overflow:visible;padding:0;
      display:block;margin:2px 0 12px;min-width:0}
    /* ⚠️ 必须显式给 #root / footer 也定 order：默认 order:0 会排到 header 前面，
       正文跑到页面最上方、标题被挤到后面。 */
    /* ⚠️ min-width:0 不能少：它们是 flex 子项，默认 min-width:auto 意味着
       「不能小于内容的最小尺寸」，会被里面 980px 的持仓表撑开，
       于是 .table-wrap 的 overflow-x:auto 永远不生效，整页横向溢出。 */
    #root{order:3;min-width:0}
    footer{order:4;min-width:0}
    .page-tabs{flex-direction:row;gap:10px;flex-wrap:wrap;padding:12px 0 0}
    .pt{width:auto}
    nav.toc{flex-direction:row;gap:7px;overflow-x:auto;padding:9px 0;margin:-4px -2px 16px;
      background:#FBFCFE;box-shadow:0 1px 0 rgba(31,78,120,.07);scrollbar-width:none}
    nav.toc::-webkit-scrollbar{display:none}
    .toc-item{align-items:center;white-space:nowrap;padding:6px 13px;border-radius:999px;
      border:1px solid var(--border);background:#fff}
    .toc-sep{width:1px;height:20px;margin:0 6px;align-self:center}
    .lay-bar{flex-direction:row;align-items:center;gap:8px;flex-wrap:wrap;padding:11px 0 0}
    .lay-panel{flex:1 0 100%;flex-direction:row;flex-wrap:wrap;gap:14px}
    .lay-g{flex-direction:row;align-items:center;gap:11px}
    .lay-item em{display:inline}
  }
</style>
</head>
<body>
<div class="wrap">
  <aside class="side">
    <div class="page-tabs" id="pageTabs"></div>
    <nav class="toc" id="toc"></nav>
    <div class="lay-bar" id="layBar"></div>
  </aside>

  <div class="main">
  <header class="top">
    <div class="top-line">
      <h1>新手行情看板</h1>
      <span class="sub">先看自己 · 再看今天 · 最后看位置</span>
      <span class="badge badge-snap" id="dataBadge">正在加载…</span>
    </div>
    <!-- 精简档把这段长说明折起来：新手第一屏不该先读一段四行的方法论。
         默认展开还是收起由 applyLayering() 按档位设 open，纯 CSS 做不到。 -->
    <details class="top-fold" id="topNote">
      <summary>这一页怎么分块，各块回答什么问题</summary>
      <div class="top-note">整页分成三块，点左边的页签切换。<b>「今天」</b>只放当天的事——我持有的今天怎么样、今天发生了什么、钱往哪儿走、是不是普涨、谁在涨；<b>「历史与位置」</b>只放过去的事——近 60 个交易日大概在什么位置、今天的成交算大算小、这几天的钱是进是出；<b>「我的账本」</b>只放你自己的钱——买卖过什么、现在持仓多少、每天的总资产怎么变、以及每次动手时是怎么想的。三块分开看，不用来回对照。全页只做记录和提示，不给任何买卖建议。</div>
    </details>
    <div class="session-bar">
      <div class="sb-state"><span class="dot" id="sbDot"></span><span id="sbState">—</span></div>
      <div class="sb-clock" id="sbClock">—</div>
      <div class="sb-periods" id="sbPeriods"></div>
      <div class="sb-track"><div class="sb-fill" id="sbFill" style="width:0%"></div></div>
    </div>
  </header>

  <div id="root"></div>

  <footer>
    <div class="disc">
      <b>使用说明</b>：<b>交易时段内每 60 秒自动更新一次</b>，收盘后显示当天收盘数据；你正在填表时会暂停，填完接着更新。右上角那个标签写的就是<b>数据到几点</b>。价格是接口返回那一刻的值，<b>不保证实时</b>，也不是目标价，页面<b>不提供任何买卖建议</b>。个别栏目偶尔会空着，那一栏会自己说明，不影响已经填好的数据。
      <br><b>数据保存</b>：不登录时只存在这台设备的浏览器（本地存储）里；登录 WorkBuddy 后自动同步一份到云端，<b>换设备</b>登录同一个账号就能看到，也可以在「我的账本」用<b>导出 / 导入</b>搬家。
    </div>
  </footer>
  </div>
</div>

<script>
/* ══════════════════════════════════════════════════════════════════
   0 · 内嵌快照（构建时写入）。页面先用它渲染，再尝试拉实时覆盖。
   ══════════════════════════════════════════════════════════════════ */
const SNAPSHOT = __RAW__;

/* 公开接口地址模板（与 fetch_public.py 完全一致，改一处要同步改另一处） */
const UT = 'fa5fd1943c7b386f172d6893dbfba10b';
const UT2 = 'b2884a393a59ad64002292a3e90d46a5';
const P2 = 'https://push2.eastmoney.com/api/qt';
const P2H = 'https://push2his.eastmoney.com/api/qt';
const P2E = 'https://push2ex.eastmoney.com';
const DC = 'https://datacenter-web.eastmoney.com/api/data/v1/get';

/* ── 行情主机的备用通道 ──────────────────────────────────────────
   东方财富把同一套接口挂在好几台机器上。push2 / push2his 在部分网络里是
   **连接被直接掐断**（不是跨域被拒，是根本没连上），而镜像机 push2test
   用一模一样的路径和参数就能取到同一份数据。
   所以：先走正门，取不到就换镜像重试一次。镜像只在正门不通时才会用到，
   数据来源会写进 _meta，页面上的「数据来源」跟着变。 */
const P2_MIRROR = 'https://push2test.eastmoney.com/api/qt';
const HOST_PAIRS = [
  ['https://push2.eastmoney.com/api/qt', P2_MIRROR],
  ['https://push2his.eastmoney.com/api/qt', P2_MIRROR]
];
const HOST_DEAD = {};      // 记住哪台已经不通，后面的请求直接走镜像，不必每次都白试
let VIA_MIRROR = false;    // 这次真的用了镜像 → 徽标与数据来源要如实写

function hostCands(u){
  const pair = HOST_PAIRS.filter(p => u.indexOf(p[0]) === 0)[0];
  if(!pair) return [u];
  const mirror = u.replace(pair[0], pair[1]);
  /* 正门已经确认不通时，镜像放前面试，省掉一轮必然失败的等待 */
  return HOST_DEAD[pair[0]] ? [mirror, u] : [u, mirror];
}

/* 取一条 JSON。换主机只换前缀，路径与参数原样不动。
   判「成功」的标准是拿到了 data：接口不通时回的是 HTML 错误页或空 body，
   JSON.parse 会先炸；而部分参数不对时 rc 有值、data 为空，那种也算没取到。 */
/* 空壳判定：接口在「当日数据还没生成 / 参数不对」时会正常回 200，
   但内容是 {data:{code, market, name, tradePeriods, klines:[]}} —— 有 data、里面是空的。
   必须当没取到：否则拿这层空壳覆盖快照，会把快照里那条 240 点的曲线整条冲空。
   diff / klines / trends / pool 是这几类接口唯一的装数据的字段，看第一个出现的就是了。 */
/* 哪些字段是真的「数值」：价格 / 涨跌 / 量额 / 家数 / 主力净额 ……
   身份字段（f12 代码、f13 市场、f14 名称）不算，它们永远有值。 */
const VALUE_F = {f1:0, f2:1, f3:1, f4:1, f5:1, f6:1, f7:1, f8:1, f9:1, f10:1, f11:1,
                 f15:1, f16:1, f17:1, f18:1, f20:1, f21:1, f62:1, f104:1, f105:1, f106:1, f184:1};
function hollow(j){
  const d = j && j.data;
  if(!d || typeof d !== 'object') return false;
  const carriers = ['diff', 'klines', 'trends', 'pool'];
  for(let i = 0; i < carriers.length; i++){
    const v = d[carriers[i]];
    if(!Array.isArray(v)) continue;
    if(!v.length) return true;                 // 空数组：这组当日没数据
    /* diff 的每一项都是一条行情。备用主机上有的市场（比如美股个股）会回来
       「名字对、价格和涨跌全是 -」——数组不空但一个数都没有，同样是空壳。 */
    if(carriers[i] === 'diff'){
      /* 判断「有没有值」只能看**真正的数值字段**：
         f12 代码 / f13 市场 / f14 名称 这些是身份字段，永远有值；
         只认 f2/f3 也不行——涨跌家数那组回的是 f104/f105/f106。
         所以列一张数值字段表，命中任意一个就算有值。 */
      const ok = v.some(x => x && typeof x === 'object' && Object.keys(x).some(k =>
        VALUE_F[k] && typeof x[k] === 'number' && isFinite(x[k])));
      if(!ok) return true;
    }
    return false;
  }
  return false;
}

async function fetchJSON(u){
  const cands = hostCands(u);
  let last = null;
  for(let i = 0; i < cands.length; i++){
    const c = cands[i];
    try{
      const r = await fetch(c, {cache:'no-store'});
      const j = await r.json();
      if(j && j.data && !hollow(j)){
        /* 只要这次用的不是文首那个地址，就算走了备用通道——不能只看 i>0：
           正门确认不通后镜像会被排到前面，那时 i 就是 0。 */
        if(c !== u){ VIA_MIRROR = true; HOST_DEAD[u] = true; }
        return j;
      }
      last = new Error('data 为空');
    }catch(e){
      last = e;
      /* 正门失败才记为不通；镜像失败不记，免得把两台都记成死的 */
      if(i === 0) HOST_DEAD[c] = true;
    }
  }
  throw last || new Error('取不到');
}
/* 腾讯行情：长周期日K 的备用源。2026-10-16 实测浏览器 CORS 通过、可用；
   同一天东财 push2* 整族 API 对本机不可达（curl 也是 Empty reply）。
   两源收盘价逐一对得上，换源不影响口径。 */
const TX = 'https://web.ifzq.gtimg.cn/appstock/app';
const ALL_A = 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23';
const SECIDS = '1.000001,0.399001,0.399006,1.000300,1.000985';
/* ⚠️ 下面三个 secids 串必须与 fetch_public.py 的 GL_CORE / GL_HK / GL_US 逐字一致，
   否则「快照能看、实时刷新后全球板块空白」——2026-10-16 实测踩过（前端漏加 URLS 导致切市场全空）。 */
const GL_CORE = '1.000001,0.399001,0.399006,1.000300,' +
  '100.HSI,100.HSCEI,124.HSTECH,100.HKEXT100,' +          // 恒生科技在 124 段，不是 100 段
  '100.DJIA,100.NDX,100.NDX100,100.SPX,' +
  '100.N225,100.KS11,100.TWII,100.STI,100.AS51,' +
  '100.GDAXI,100.FTSE,100.FCHI,100.SX5E,100.MIB,100.IBEX,100.SSMI,100.AEX,' +
  '100.SENSEX,100.TSX,100.BVSP,100.UDI,100.XIN9';
/* ⚠️ 港美股不能用 clist 涨幅榜（m:116 / m:105 前排全是牛熊证、窝轮、仙股） */
const GL_HK = '116.00700,116.09988,116.03690,116.00388,116.01810,116.01211,' +
  '116.00941,116.09618,116.01024,116.00005,116.00939,116.02318';
const GL_US = '105.AAPL,105.NVDA,105.MSFT,105.TSLA,105.GOOGL,105.AMZN,' +
  '105.META,105.AVGO,105.NFLX,105.AMD,105.INTC';

const URLS = {
  quote: P2 + '/ulist.np/get?fltt=2&invt=2&secids=' + SECIDS +
         '&fields=f1,f2,f3,f4,f5,f6,f12,f13,f14,f15,f16,f17,f18&ut=' + UT,
  breadth: P2 + '/ulist.np/get?fltt=2&invt=2&secids=1.000001,0.399001&fields=f12,f14,f104,f105,f106&ut=' + UT,
  trend_sh000001: P2 + '/stock/trends2/get?secid=1.000001&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut=' + UT,
  trend_sz399006: P2 + '/stock/trends2/get?secid=0.399006&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut=' + UT,
  trend_sz399001: P2 + '/stock/trends2/get?secid=0.399001&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut=' + UT,
  trend_sh000300: P2 + '/stock/trends2/get?secid=1.000300&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut=' + UT,
  /* ⚠️⚠️ 资金流必须**沪市 + 深市两个都取**再相加，不能只取 1.000001：
     secid 1.000001 = 上证指数（**沪市**）、0.399001 = 深证成指（**深市**）。
     只取前者拿到的是只算沪市——沪市约 -5.6 亿时，沪深两市合计约 -49 亿
     （沪 -5.61 ＋ 深 -43.75），差近 9 倍，会被当成算错了。 */
  fflow_min: P2 + '/stock/fflow/kline/get?secid=1.000001&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&klt=1&lmt=0&ut=' + UT2,
  fflow_day: P2H + '/stock/fflow/daykline/get?secid=1.000001&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&klt=101&lmt=12&ut=' + UT2,
  fflow_min_sz: P2 + '/stock/fflow/kline/get?secid=0.399001&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&klt=1&lmt=0&ut=' + UT2,
  fflow_day_sz: P2H + '/stock/fflow/daykline/get?secid=0.399001&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&klt=101&lmt=12&ut=' + UT2,
  sector_in: P2 + '/clist/get?pn=1&pz=8&po=1&np=1&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f2,f3,f62,f184&ut=' + UT2,
  sector_out: P2 + '/clist/get?pn=1&pz=8&po=0&np=1&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f2,f3,f62,f184&ut=' + UT2,
  sector_up: P2 + '/clist/get?pn=1&pz=30&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f2,f3,f62,f104,f105,f128,f140&ut=' + UT2,
  sector_down: P2 + '/clist/get?pn=1&pz=30&po=0&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f2,f3,f62,f104,f105,f128,f140&ut=' + UT2,
  concept_up: P2 + '/clist/get?pn=1&pz=30&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:3&fields=f12,f14,f2,f3,f62,f104,f105,f128,f140&ut=' + UT2,
  /* ⚠️ 多要几条：个股榜要剔掉 N/C 开头的当日新股，剔完还得有 8 条 */
  stock_in: P2 + '/clist/get?pn=1&pz=12&po=1&np=1&fltt=2&invt=2&fid=f62&fs=' + encodeURIComponent(ALL_A) + '&fields=f12,f14,f2,f3,f62,f184,f100&ut=' + UT2,
  stock_chg: P2 + '/clist/get?pn=1&pz=12&po=1&np=1&fltt=2&invt=2&fid=f3&fs=' + encodeURIComponent(ALL_A) + '&fields=f12,f14,f2,f3,f4,f5,f6,f100&ut=' + UT2,
  stock_amt: P2 + '/clist/get?pn=1&pz=10&po=1&np=1&fltt=2&invt=2&fid=f6&fs=' + encodeURIComponent(ALL_A) + '&fields=f12,f14,f2,f3,f4,f5,f6,f100&ut=' + UT2,
  margin: DC + '?reportName=RPTA_RZRQ_LSHJ&columns=ALL&sortColumns=DIM_DATE&sortTypes=-1&pageSize=2&pageNumber=1&source=WEB&client=WEB',
  kline: P2H + '/stock/kline/get?secid=1.000300&klt=101&fqt=0&lmt=60&end=20500101&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&ut=' + UT,
  kday_sh: P2H + '/stock/kline/get?secid=1.000001&klt=101&fqt=0&lmt=6&end=20500101&fields1=f1,f2,f3&fields2=f51,f53,f57&ut=' + UT,
  kday_sz: P2H + '/stock/kline/get?secid=0.399001&klt=101&fqt=0&lmt=6&end=20500101&fields1=f1,f2,f3&fields2=f51,f53,f57&ut=' + UT,
  /* ══ 全球市场 ═══════════════════════════════════════════════════ */
  gl_core: P2 + '/ulist.np/get?fltt=2&invt=2&secids=' + GL_CORE +
           '&fields=f1,f2,f3,f4,f5,f6,f12,f13,f14,f15,f16,f17,f18&ut=' + UT,
  gl_world: P2 + '/clist/get?pn=1&pz=80&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:100' +
            '&fields=f12,f13,f14,f2,f3,f4,f6&ut=' + UT2,
  gl_hk: P2 + '/ulist.np/get?fltt=2&invt=2&secids=' + GL_HK +
         '&fields=f1,f2,f3,f4,f6,f12,f13,f14,f15,f16,f17,f18&ut=' + UT,
  gl_us: P2 + '/ulist.np/get?fltt=2&invt=2&secids=' + GL_US +
         '&fields=f1,f2,f3,f4,f6,f12,f13,f14,f15,f16,f17,f18&ut=' + UT,
  gl_trend_hk: P2 + '/stock/trends2/get?secid=100.HSI&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut=' + UT,
  gl_trend_us: P2 + '/stock/trends2/get?secid=100.DJIA&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut=' + UT,
  gl_trend_jp: P2 + '/stock/trends2/get?secid=100.N225&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1&ut=' + UT,
  gl_kline_hk: P2H + '/stock/kline/get?secid=100.HSI&klt=101&fqt=0&lmt=60&end=20500101&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&ut=' + UT,
  gl_kline_us: P2H + '/stock/kline/get?secid=100.DJIA&klt=101&fqt=0&lmt=60&end=20500101&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&ut=' + UT,
  /* ══ 长周期日K（腾讯源，走「历史与位置」页） ═══════════════════════ */
  kline_sh000001: TX + '/fqkline/get?param=sh000001,day,,,60,qfq',
  kline_sz399001: TX + '/fqkline/get?param=sz399001,day,,,60,qfq',
  kline_sz399006: TX + '/fqkline/get?param=sz399006,day,,,60,qfq',
  /* ══ 资讯流：**不在页面里实时拉**，只由构建时抓进快照（与 fetch_public.py 逐字同步） ══
     ⚠️ 原先这里挂了 np-weblist / np-listapi 三条地址，浏览器控制台每次加载都会报跨域失败。
     查清了才明白问题不在「转发」：
       · news_724（快讯）已经没有消费方——「全天快讯」那一段早就删了，纯属白拉；
       · 剩下两条是盘后复盘导读和隔夜导读，**一天只出一份**，就算 60 秒刷一次也不会变。
     所以三条都从实时清单里拿掉：要闻一律用快照里那一份，标签上写明是哪天几点抓的；
     快照里也没有时，那一段明说「这次没取到」，不留一片空白。
     快照由收盘后的定时任务重生成，要闻跟着一起更新。 */
};
const POOL_URL = {
  ztpool: d => P2E + '/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=80&sort=fbt:asc&date=' + d,
  dtpool: d => P2E + '/getTopicDTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=80&sort=fund:asc&date=' + d,
  zbpool: d => P2E + '/getTopicZBPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=80&sort=fbt:asc&date=' + d
};

/* ── 资讯流统一解析（np 系两个接口字段同义，这里拍平成一种） ─────────
   getFastNewsList 给 fastNewsList（快讯，titleColor=3 为重点条目）；
   getNewsByColumns 给 list（复盘导读，带 mediaName 来源名）。
   tags：stockList 是数组（实测），取每项去掉市场前缀后的代码（"90.BK0538"→"BK0538"），
   每条最多 3 个——渲染时 BK 前缀查板块名映射表（bkNameMap），其余原码显示。 */
function normNewsData(resp){
  const d = resp && resp.data;
  const lst = (d && (d.fastNewsList || d.list)) || [];
  return lst.map(x => ({
    time: x.showTime || '', title: x.title || '', desc: (x.summary || '').trim(),
    imp: x.titleColor === 3, src: x.mediaName || '东方财富', url: x.url || '',
    tags: (Array.isArray(x.stockList) ? x.stockList : [])
      .map(s => String(s).split('.').pop()).filter(Boolean).slice(0, 3)
  })).filter(x => x.title).slice(0, 20);
}

/* ══════════════════════════════════════════════════════════════════
   1 · 小工具
   ══════════════════════════════════════════════════════════════════ */
const N = v => { const n = parseFloat(String(v==null?'':v).replace(/[,，%\s]/g,'')); return isFinite(n)?n:null; };
const cls = v => (v==null?'flat':(v>0?'up':(v<0?'down':'flat')));
const ARROW = v => (v==null?'':(v>0?'▲':(v<0?'▼':'—')));
const pct = (v,d=2) => (v==null?'—':(v>=0?'+':'−') + Math.abs(v).toFixed(d) + '%');
const num = (v,d=2) => (v==null?'—':v.toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d}));
const esc = s => String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
/** 元 → 亿，自适应万亿 */
function money(yi){ if(yi==null) return '—'; const a=Math.abs(yi);
  if(a>=10000) return (yi/10000).toFixed(2).replace(/\.00$/,'') + ' 万亿';
  if(a>=1) return yi.toFixed(a>=100?0:1) + ' 亿';
  return (yi*10000).toFixed(0) + ' 万'; }
const H = n => n==null?'—':n.toLocaleString('zh-CN');
/** 去掉东财行业名末尾的层级罗马数字（航空装备Ⅲ → 航空装备），用于去重 */
const stripLv = s => String(s==null?'':s).replace(/[ⅠⅡⅢⅣⅤ]+$/,'');
/** 当日新股：沪深两市用 N 表示上市首日、C 表示上市后第 2–5 日。
    这类票当天能涨 300%+，混进「全 A 股涨幅榜」会让整张榜单失真，所以榜单要剔掉。
    只认「N/C + 一个汉字」这个形态——正常股票名不会长成这样（TCL、ST 开头都不匹配）。 */
const isNewStock = nm => /^[NC][\u4e00-\u9fa5]/.test(String(nm==null?'':nm));
/** 2026-10-16 → 10-16 */
const md = s => String(s==null?'':s).replace(/^(\d{4})-(\d{2})-(\d{2})$/,'$2-$3');

/* ══════════════════════════════════════════════════════════════════
   2 · 聚合：原始响应包 → 看板模型（唯一一份聚合逻辑）
   ══════════════════════════════════════════════════════════════════ */
const WANT = [
  {code:'000001', market:1, name:'上证指数'},
  {code:'399001', market:0, name:'深证成指'},
  {code:'399006', market:0, name:'创业板指'},
  {code:'000300', market:1, name:'沪深300'}
];

/** 交易时段常量（A 股） */
const SESSIONS = [
  {key:'am', label:'上午', b:'09:30', e:'11:30'},
  {key:'pm', label:'午后', b:'13:00', e:'15:00'}
];

/* ══════════════════════════════════════════════════════════════════
   全球市场（数据来自东财公开接口；取数见 fetch_public.py 的 GL_* 组）
   MKTS    市场 tab —— 顺序与东财 APP 的「沪深京 / 港股 / 美股 / 全球」一致
   GIDX    各市场的核心指数（sec 必须与 fetch_public.py 的 GL_CORE 一致）
   WREGION 全球指数全量（63 个）的区域归属，供「全球」视图分组
   ⚠️ 颜色：本页**统一按中国习惯红涨绿跌**；港美股本地习惯相反，提示里写明。
   ══════════════════════════════════════════════════════════════════ */
const MKTS = [
  {k:'cn', label:'沪深京'},
  {k:'hk', label:'港股'},
  {k:'us', label:'美股'},
  {k:'gl', label:'全球'}
];
const GIDX = [
  {k:'cn', sec:'1.000001',  name:'上证指数',        short:'上证'},
  {k:'cn', sec:'0.399001',  name:'深证成指',        short:'深证'},
  {k:'cn', sec:'0.399006',  name:'创业板指',        short:'创业板'},
  {k:'cn', sec:'1.000300',  name:'沪深300',         short:'沪深300'},
  {k:'hk', sec:'100.HSI',   name:'恒生指数',        short:'恒生指数'},
  {k:'hk', sec:'100.HSCEI', name:'恒生中国企业指数', short:'国企指数'},
  {k:'hk', sec:'124.HSTECH', name:'恒生科技指数',   short:'恒生科技'},
  {k:'hk', sec:'100.HKEXT100', name:'港交所科技100', short:'港科技100'},
  {k:'us', sec:'100.DJIA',  name:'道琼斯工业平均',  short:'道指'},
  {k:'us', sec:'100.NDX',   name:'纳斯达克综合',    short:'纳指'},
  {k:'us', sec:'100.NDX100', name:'纳斯达克100',    short:'纳指100'},
  {k:'us', sec:'100.SPX',   name:'标普500',         short:'标普500'},
  {k:'gl', sec:'100.N225',  name:'日经225',         region:'亚太'},
  {k:'gl', sec:'100.KS11',  name:'韩国KOSPI',       region:'亚太'},
  {k:'gl', sec:'100.TWII',  name:'中国台湾加权',     region:'亚太'},
  {k:'gl', sec:'100.STI',   name:'富时新加坡海峡时报', region:'亚太'},
  {k:'gl', sec:'100.AS51',  name:'澳大利亚标普200',  region:'亚太'},
  {k:'gl', sec:'100.SENSEX', name:'印度孟买SENSEX',  region:'亚太'},
  {k:'gl', sec:'100.GDAXI', name:'德国DAX30',       region:'欧洲'},
  {k:'gl', sec:'100.FTSE',  name:'英国富时100',      region:'欧洲'},
  {k:'gl', sec:'100.FCHI',  name:'法国CAC40',       region:'欧洲'},
  {k:'gl', sec:'100.SX5E',  name:'欧洲斯托克50',     region:'欧洲'},
  {k:'gl', sec:'100.TSX',   name:'加拿大S&P/TSX',   region:'美洲'},
  {k:'gl', sec:'100.BVSP',  name:'巴西BOVESPA',     region:'美洲'},
  {k:'gl', sec:'100.UDI',   name:'美元指数',         region:'商品与汇率'}
];
const R_AP = '亚太', R_EU = '欧洲', R_AM = '美洲', R_CM = '商品与汇率';
const WREGION = {
  PSI:R_AP, KS11:R_AP, KOSPI200:R_AP, N225:R_AP, HSI:R_AP, HSCEI:R_AP, HKEXT100:R_AP,
  TWII:R_AP, STI:R_AP, FSTAS:R_AP, FSTM:R_AP, SENSEX:R_AP, VNINDEX:R_AP, SET:R_AP,
  JKSE:R_AP, KLSE:R_AP, NZ50:R_AP, AORD:R_AP, AS51:R_AP, KSE100:R_AP, CSEALL:R_AP, XIN9:R_AP,
  HEX:R_EU, HEX25:R_EU, OMXSPI:R_EU, OMXC20:R_EU, GDAXI:R_EU, MIB:R_EU, IBEX:R_EU,
  ASE:R_EU, OSEBX:R_EU, AXX:R_EU, WIG:R_EU, SX5E:R_EU, AEX:R_EU, ISEQ:R_EU, RTS:R_EU,
  SXXP:R_EU, ATX:R_EU, PSI20:R_EU, MCX:R_EU, SSMI:R_EU, ASX:R_EU, NMX:R_EU, BFX:R_EU,
  FTSE:R_EU, FCHI:R_EU, PX:R_EU, ICEXI:R_EU,
  TSX:R_AM, MXX:R_AM, DJIA:R_AM, SPX:R_AM, NDX:R_AM, NDX100:R_AM, BVSP:R_AM,
  CRB:R_CM, BDI:R_CM, UDI:R_CM,
  // 港交所科技 100 的全收益/净收益版本：与 HKEXT100 观感重复，不展示
  HKEXTT:'skip', HKEXTN:'skip', HSAHP:'skip'
};
/** 各市场的时段与规则差异——切到对应市场时提示给新手 */
const MKT_RULE = {
  cn:{ full:'沪深京', sess:'09:30–11:30 / 13:00–15:00（北京时间）',
       rule:'有涨跌停：一天最多涨 10%、最多跌 10%（创业板、科创板 20%，北交所 30%；ST 股跟所在板块一致——<b>2026 年 7 月 6 日起</b>主板 ST 也由 5% 放宽到 10%）——比如 10 元的股票，主板一天最多涨到 11 元',
       color:'红涨绿跌，与中国内地习惯一致' },
  hk:{ full:'中国香港', sess:'09:30–12:00 / 13:00–16:00（北京时间）',
       rule:'没有涨跌停，理论上一天能涨跌任意幅度；今天买的当天就能卖（T+0），但钱要两天后才到账（T+2）',
       color:'⚠️ 中国香港本地习惯是「绿涨红跌」，与本页相反' },
  us:{ full:'美国',   sess:'夏令时 21:30–次日 04:00 / 冬令时 22:30–次日 05:00（北京时间）',
       rule:'没有涨跌停，跌得太急时交易所会临时停牌几分钟（熔断），避免一路砸下去；开盘前和收盘后也能交易',
       color:'⚠️ 欧美本地习惯是「绿涨红跌」，与本页相反' },
  gl:{ full:'全球',   sess:'亚太多在白天、欧洲在下午、美洲在夜间（均已折算北京时间）',
       rule:'各市场规则不同，本页只记录点位与涨跌',
       color:'⚠️ 各地红绿习惯不同，本页统一按中国习惯' }
};
/* 时区：夏令时交给 Intl 处理，不自己算 */
function clockOf(tz){
  try{ return new Intl.DateTimeFormat('zh-CN', {timeZone:tz, hour:'2-digit', minute:'2-digit', hour12:false}).format(new Date()); }
  catch(e){ return '—'; }
}
/** 某时区「此刻」相对 UTC 的分钟偏移（夏令时同样交给 Intl，免得自己判错）
    ⚠️ formatToParts 在 hour12:false 下凌晨可能给出 '24' ⇒ 必须 %24 */
function tzOffMin(tz){
  try{
    const d = new Date();
    const p = new Intl.DateTimeFormat('en-US', {timeZone:tz, hour12:false,
      year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}).formatToParts(d);
    const g = k => { const f = p.filter(x => x.type === k)[0]; return f ? +f.value : NaN; };
    const asUTC = Date.UTC(g('year'), g('month') - 1, g('day'), g('hour') % 24, g('minute'));
    return Math.round((asUTC - Math.floor(d.getTime() / 60000) * 60000) / 60000);
  }catch(e){ return null; }
}
/** 时差说成人话：「比北京晚 12 小时」，而不是丢一个 −12 让人自己换算 */
function tzDiffText(min){
  if(min == null) return '';
  if(min === 0) return '与北京同一时刻';
  const abs = Math.abs(min), h = Math.floor(abs / 60), m = abs % 60;
  return '比北京' + (min > 0 ? '早' : '晚') + (h ? h + ' 小时' : '') + (m ? (h ? ' ' : '') + m + ' 分' : '');
}
/** 美国夏令时：3 月第 2 个周日 ~ 11 月第 1 个周日 */
function usDST(d){
  const y = d.getFullYear();
  const mar = new Date(y,2,1), nov = new Date(y,10,1);
  const marSun = 8 + ((7 - mar.getDay()) % 7);
  const novSun = 1 + ((7 - nov.getDay()) % 7);
  const t = d.getTime();
  return t >= new Date(y,2,marSun).getTime() && t < new Date(y,10,novSun).getTime();
}
/** 各市场「此刻」的状态。美股跨零点，必须先于周末判断处理 */
function mktState(k, now){
  now = now || new Date();
  const d = now.getDay(), hm = now.getHours()*60 + now.getMinutes();
  const inR = (a,b) => hm >= a*60 && hm < b*60;
  if(k === 'us'){
    const dst = usDST(now), o = dst ? 21*60+30 : 22*60+30, c = dst ? 4*60 : 5*60;
    if(d === 6) return hm < c ? {s:'交易中 · 周五盘延续', c:'on'} : {s:'休市', c:'off'};
    if(d === 0) return {s:'休市', c:'off'};
    if(hm >= o || hm < c) return {s:'交易中 · ' + (dst ? '夏令时' : '冬令时'), c:'on'};
    return {s:'已收盘', c:'off'};
  }
  if(d === 0 || d === 6) return {s:'休市', c:'off'};
  /* ⚠️ 这里的单位是**分钟**，不能写成 9.5 / 8——那是把 9 分半当成了 9:30。
     旧写法下早上 8 点打开页面会显示「已收盘」。 */
  if(k === 'cn'){
    if(inR(9.5,11.5) || inR(13,15)) return {s:'交易中', c:'on'};
    if(inR(11.5,13)) return {s:'午间休市', c:'break'};
    return hm < 9*60+30 ? {s:'未开盘', c:'off'} : {s:'已收盘', c:'off'};
  }
  if(k === 'hk'){
    if(inR(9.5,12) || inR(13,16)) return {s:'交易中', c:'on'};
    if(inR(12,13)) return {s:'午间休市', c:'break'};
    return hm < 9*60+30 ? {s:'未开盘', c:'off'} : {s:'已收盘', c:'off'};
  }
  if(k === 'jp'){
    if(inR(8,10.5) || inR(11.5,14.5)) return {s:'交易中', c:'on'};
    if(inR(10.5,11.5)) return {s:'午间休市', c:'break'};
    return hm < 8*60 ? {s:'未开盘', c:'off'} : {s:'已收盘', c:'off'};
  }
  return {s:'跨时区', c:'off'};
}

/** 把一串分时点归类成一句「形态」——只描述走势长相，不含判断
    ⚠️ 形态词必须和**净涨跌同向**。旧写法只看「收在自己区间的什么位置」，
    于是出现「午后相对上午收盘 −0.11%」和「午后收成震荡走高」写在相邻两行——
    读者当然会说这是自相矛盾。现在先按净涨跌分三档，再在各档内部挑长相词。 */
function shapeOf(pts, pre){
  if(!pts || pts.length < 5 || !pre) return null;
  const cl = pts.map(p => p.close);
  /* 高低点同样取每根分钟 K 的最高/最低价，与 splitTrend 的口径保持一致 */
  const hs = pts.map(p => p.high != null ? p.high : p.close);
  const ls = pts.map(p => p.low  != null ? p.low  : p.close);
  const hi = Math.max.apply(null, hs), lo = Math.min.apply(null, ls);
  const c0 = cl[0], cN = cl[cl.length-1];
  let hiI = 0, loI = 0;
  hs.forEach((v,i) => { if(v > hs[hiI]) hiI = i; });
  ls.forEach((v,i) => { if(v < ls[loI]) loI = i; });
  const hiPos = hiI/(cl.length-1), loPos = loI/(cl.length-1);
  const pos = (hi === lo) ? 0.5 : (cN - lo)/(hi - lo);
  const endPct = (cN - pre)/pre*100;
  let text;
  if((hi-lo)/pre*100 < 0.35){
    text = '窄幅震荡';
  } else if(endPct < -0.05){
    /* 这一段是跌的：只能说「跌到后面才见底、然后回来一点」或「冲高回落」，
       绝不能说「走高」。探底回升不等于收涨——它只说明低点在段里的位置。 */
    text = (loPos > 0.55 && pos > 0.6) ? '探底回升'
         : (hiPos < 0.35) ? '冲高回落' : '震荡走低';
  } else if(endPct > 0.05){
    text = (hiPos > 0.6 && pos > 0.75) ? '单边上行'
         : (loPos < 0.35 && pos > 0.6) ? '探底回升'
         : (hiPos < 0.3) ? '冲高回落' : '震荡走高';
  } else {
    text = '区间震荡';
  }
  return {
    openPct: (c0 - pre)/pre*100,
    endPct:  endPct,
    range:   (hi - lo)/pre*100,
    pos:     pos,
    hiTime:  pts[hiI].time, loTime: pts[loI].time,
    hi: hi, lo: lo,
    text: text
  };
}

/** 开盘定性：高开 / 低开 / 平开 —— 相对上一交易日收盘 */
const openWord = v => v==null ? '' : (v > 0.15 ? '高开' : (v < -0.15 ? '低开' : '平开'));

/** 把 trends2 的分时点按交易时段切开（noon 是上下午分界，港股是 12:00） */
function splitTrend(tr, noon){
  noon = noon || '11:30';
  if(!tr || !tr.data || !tr.data.trends) return null;
  const pre = N(tr.data.preClose);
  const pts = tr.data.trends.map(s => {
    const p = s.split(',');
    return {t:p[0], time:p[0].slice(11), open:N(p[1]), close:N(p[2]), high:N(p[3]), low:N(p[4]), amount:N(p[6])};
  }).filter(p => p.close != null);
  if(!pts.length) return null;
  const am = pts.filter(p => p.time <= noon);
  const pm = pts.filter(p => p.time >  noon);
  const amClose = am.length ? am[am.length-1].close : null;
  const allClose = pts[pts.length-1].close;
  const sum = a => a.reduce((s,p)=>s+(p.amount||0),0);
  /* ⚠️ 最高/最低要取**每分钟那根 K 线的最高价/最低价**，不能取收盘价的极值。
     分时每分钟只有一个收盘价，全天真正的最低点常常落在两分钟之间：
     2026-11-03 上证午后按收盘价算是 3581.20，按分钟最低价是 3580.38，
     而行情快照（字段 f16）给的也是 3580.38 —— 两处对不上就是这么来的。 */
  const hiOf = a => a.length ? Math.max.apply(null, a.map(p => p.high != null ? p.high : p.close)) : null;
  const loOf = a => a.length ? Math.min.apply(null, a.map(p => p.low  != null ? p.low  : p.close)) : null;
  const amHigh = hiOf(am), amLow = loOf(am);
  const pmHigh = hiOf(pm), pmLow = loOf(pm);
  const tail = (arr, k) => arr.length > k ? (arr[arr.length-1].close - arr[arr.length-1-k].close)/arr[arr.length-1-k].close*100 : null;
  const t0 = t => (t || '').slice(0,5);
  return {
    pts, am, pm, preClose:pre, amClose, allClose,
    amChgPct: (amClose!=null&&pre)?(amClose-pre)/pre*100:null,
    pmChgPct: (amClose!=null&&allClose)?(allClose-amClose)/amClose*100:null,
    allChgPct: (allClose!=null&&pre)?(allClose-pre)/pre*100:null,
    amAmount: sum(am)/1e8, pmAmount: sum(pm)/1e8, allAmount: sum(pts)/1e8,
    amHigh, amLow, pmHigh, pmLow,
    /* —— 新增：分时形态与关键时点，供「详细复盘」用 —— */
    amShape: shapeOf(am, pre),
    pmShape: shapeOf(pm, amClose),
    allShape: shapeOf(pts, pre),
    openTime: pts.length ? t0(pts[0].time) : '',
    closeTime: pts.length ? t0(pts[pts.length-1].time) : '',
    amFirst: am.length ? t0(am[0].time) : '',
    amLast: am.length ? t0(am[am.length-1].time) : '',
    pmFirst: pm.length ? t0(pm[0].time) : '',
    pmLast: pm.length ? t0(pm[pm.length-1].time) : '',
    open30Pct: (am.length > 30 && pre) ? (am[30].close - pre)/pre*100 : null,
    amTailPct: tail(am, 30),
    pmTailPct: tail(pm, 30),
    amTurn: (am.length > 60) ? (am[am.length-1].close - am[Math.floor(am.length/2)].close)/am[Math.floor(am.length/2)].close*100 : null
  };
}

/** 海外指数的分时：不切上下午（美股连场、时区不同），只取首尾与高低点时刻 */
function trendLite(tr){
  if(!tr || !tr.data || !tr.data.trends || !tr.data.trends.length) return null;
  const pre = N(tr.data.preClose);
  const pts = tr.data.trends.map(s => {
    const p = s.split(',');
    return {t:p[0], time:p[0].slice(11), close:N(p[2]), amount:N(p[6])};
  }).filter(p => p.close != null);
  if(!pts.length) return null;
  const cl = pts.map(p => p.close);
  let hiI = 0, loI = 0;
  cl.forEach((v,i) => { if(v > cl[hiI]) hiI = i; if(v < cl[loI]) loI = i; });
  const last = pts[pts.length-1];
  return {
    pts, preClose:pre, date: pts[0].t.slice(0,10),
    firstTime: pts[0].time.slice(0,5), lastTime: last.time.slice(0,5),
    open: cl[0], close: last.close, hi: cl[hiI], lo: cl[loI],
    hiTime: pts[hiI].time.slice(0,5), loTime: pts[loI].time.slice(0,5),
    amount: pts.reduce((s,p)=>s+(p.amount||0),0)/1e8,
    chgPct: pre ? (last.close - pre)/pre*100 : null,
    shape: shapeOf(pts, pre)
  };
}

/** 把一条 fflow 响应解成逐点净额（原始值，未换算单位） */
function flowRows(f){
  const kl = f && f.data && f.data.klines;
  if(!kl || !kl.length) return null;
  const rows = kl.map(s => {
    const p = s.split(',');
    return {t:p[0], main:N(p[1]), small:N(p[2]), mid:N(p[3]), big:N(p[4]), superBig:N(p[5])};
  }).filter(r => r.t && r.main != null);
  return rows.length ? rows : null;
}

/* 沪市 ＋ 深市 → 沪深两市。
   ⚠️ 两源的**分钟点位数量不一样**（2026-10-16 实测：沪市 240 点、深市 326 点），
      所以必须按时间戳**取交集**再逐点相加；直接拿较长的那个去配会整体错位。
   ⚠️ 只有一个源时不硬凑：返回 both:false，让页面把口径如实写成「沪市」，
      而不是悄悄拿沪市冒充两市——那正是「怎么才 5.6 亿」这个疑问的根因。 */
function mergeFlow(fSh, fSz){
  const a = flowRows(fSh), b = flowRows(fSz);
  if(!a && !b) return null;
  /* src 要带出去：只剩一个源时得知道剩的是沪市还是深市，
     以前一律显示「沪市」，深市独活时会标错口径。 */
  if(!a || !b) return {rows: a || b, both:false, src: a ? 'sh' : 'sz'};
  const mb = new Map(b.map(r => [r.t, r]));
  const rows = [];
  a.forEach(r => {
    const o = mb.get(r.t);
    if(!o) return;                                    /* ← 取交集 */
    rows.push({t:r.t, main:r.main+o.main, small:r.small+o.small, mid:r.mid+o.mid,
               big:r.big+o.big, superBig:r.superBig+o.superBig});
  });
  /* 交集太小说明两源的时段根本对不上，宁可退回单源，也不要画一条半截的两市曲线 */
  if(rows.length < 60) return {rows: a, both:false, src:'sh'};
  return {rows, both:true, src:'both'};
}

/** 把合并后的分钟序列切开（累计值） */
function splitFlow(merged){
  if(!merged || !merged.rows || !merged.rows.length) return null;
  const kl = merged.rows.map(r => ({t:r.t, time:r.t.slice(11), main:r.main,
    small:r.small, mid:r.mid, big:r.big, superBig:r.superBig}));
  const am = kl.filter(p => p.time <= '11:30');
  const pm = kl.filter(p => p.time >  '11:30');
  const amEnd = am.length ? am[am.length-1].main : 0;
  const allEnd = kl[kl.length-1].main;
  /* date：这条分时属于哪一天。全天卡要拿它和日线对日期，
     对不上就说明日线还是昨天的，不能拿来当今天。 */
  const date = kl.length ? kl[kl.length-1].t.slice(0, 10) : null;
  return {kl, am, pm, amEnd:amEnd/1e8, pmDelta:(allEnd-amEnd)/1e8, allEnd:allEnd/1e8,
          both: merged.both !== false, src: merged.src || (merged.both === false ? 'sh' : 'both'),
          date:date};
}

/* ── 日K：两种源都吃，统一成「日期,开,收,高,低,量」这一种行 ──
   东财 push2his → data.klines: ["2026-07-17,3765.32,3764.15,…", …]
   腾讯 fqkline  → data.<code>.day: [["2026-07-17","3765.32","3764.15",…], …]
   ⚠️ 两条路径的第 [2] 位都是「收盘价」，所以下游只需要按逗号切、取下标 2。 */
function klLines(src){
  const d = src && src.data;
  if(!d) return null;
  if(d.klines && d.klines.length) return d.klines;
  const k = Object.keys(d).find(x => d[x] && (d[x].day || d[x].qfqday));
  if(!k) return null;
  const arr = d[k].qfqday || d[k].day;
  if(!arr || !arr.length) return null;
  return arr.map(a => [a[0], a[1], a[2], a[3], a[4], a[5]].join(','));
}
/** 从任一种源里取出「日期序列 + 收盘序列」 */
/** 日K序列，只留最近 60 个交易日。
    ⚠️ 数据源一次多给一两天，不截断就会出现「标题写近 60 个交易日、右边写着 61 个」——
    页面所有提到「近 60」的地方都以这里为准，改这个数字要全文一起改。 */
const KL_DAYS = 60;
function klSeries(src){
  const kl = klLines(src);
  if(!kl) return null;
  let rows = kl.map(s => s.split(','))
               .map(p => ({d:p[0], c:N(p[2])}))
               .filter(x => x.d && x.c != null);
  if(rows.length > KL_DAYS) rows = rows.slice(-KL_DAYS);
  if(rows.length < 2) return null;
  return {closes: rows.map(x => x.c), dates: rows.map(x => x.d), n: rows.length,
          start: rows[0].d, end: rows[rows.length-1].d};
}

function normalize(raw){
  const m = {ok:false};
  const q = raw.quote && raw.quote.data && raw.quote.data.diff;
  if(!q) return m;

  /* 指数 */
  m.indices = WANT.map(w => {
    const it = q.find(x => x.f12 === w.code && x.f13 === w.market);
    if(!it) return null;
    const tr = splitTrend(raw['trend_' + (w.market?'sh':'sz') + w.code]);
    return {
      code: w.code, market: w.market, name: w.name,
      price: N(it.f2), chgPct: N(it.f3), chg: N(it.f4),
      amount: N(it.f6)/1e8, high: N(it.f15), low: N(it.f16),
      open: N(it.f17), prevClose: N(it.f18),
      amp: (N(it.f18)&&N(it.f15)&&N(it.f16)) ? (N(it.f15)-N(it.f16))/N(it.f18)*100 : null,
      trend: tr
    };
  }).filter(Boolean);

  const zqz = q.find(x => x.f12 === '000985');   // 中证全指
  const sh = q.find(x => x.f12 === '000001'), sz = q.find(x => x.f12 === '399001');

  /* 涨跌家数（沪 + 深） */
  let up=null,down=null,flat=null;
  if(raw.breadth && raw.breadth.data && raw.breadth.data.diff){
    const b = raw.breadth.data.diff;
    const s1 = b.find(x=>x.f12==='000001'), s2 = b.find(x=>x.f12==='399001');
    const pick = (a,k) => (a && N(a[k])!=null) ? N(a[k]) : 0;
    up = pick(s1,'f104') + pick(s2,'f104');
    down = pick(s1,'f105') + pick(s2,'f105');
    flat = pick(s1,'f106') + pick(s2,'f106');
  }
  /* 涨停 / 跌停 / 炸板 */
  const tc = k => { const d = raw[k] && raw[k].data; return d ? N(d.tc) : null; };
  m.breadth = {up, down, flat, total: (up!=null&&down!=null&&flat!=null)?up+down+flat:null,
               limitUp: tc('ztpool'), limitDown: tc('dtpool'), bomb: tc('zbpool')};

  /* 成交额：沪市 + 深市 */
  const amtSh = sh ? N(sh.f6)/1e8 : null, amtSz = sz ? N(sz.f6)/1e8 : null;
  const amTr = (m.indices.find(i=>i.code==='000001')||{}).trend;
  m.turnover = {
    amount: (amtSh!=null&&amtSz!=null) ? amtSh+amtSz : (zqz?N(zqz.f6)/1e8:null),
    /* 降级时不能还叫「两市」：中证全指比真值低约 3.7% */
    scope: (amtSh!=null&&amtSz!=null) ? '沪深两市' : (zqz ? '中证全指' : '—'),
    amtSh, amtSz,
    am: amTr?amTr.amAmount:null, pm: amTr?amTr.pmAmount:null
  };

  /* 资金流：沪市 ＋ 深市 合成「沪深两市」
     （页面其他地方——成交额、涨跌家数——都是沪深两市合计，资金流必须跟上，否则
       会显得「成交 1.56 万亿、主力只跑了 5.6 亿」，其实那 5.6 亿只是**沪市**。） */
  const fm = splitFlow(mergeFlow(raw.fflow_min, raw.fflow_min_sz));
  /* 目标交易日：以**分时最后一条**为准。日线接口在盘中不生成当天数据，最后一条是
     昨天——直接拿它当「今天全天」，就成了「昨天的数 + 今天的日期」，而「上午 / 午后」
     用的是分时，两下一加必然对不上（2026-11-03 实测：两市分时 −535 亿，
     日线最后一条停在 11-02 的 +117 亿，差 650 亿）。 */
  const dayKey = (fm && fm.date) ||
    (raw._meta && raw._meta.tradeDate
      ? String(raw._meta.tradeDate).replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3') : null);
  /* 按日期挑日线，不认「最后一条」——盘中的最后一条是昨天 */
  const dayRead = f => {
    const kl = f && f.data && f.data.klines;
    if(!kl || !kl.length) return null;
    const hit = kl.filter(x => !dayKey || x.slice(0, 10) === dayKey).pop();
    if(!hit) return null;
    const p = hit.split(',');
    return {date:p[0], main:N(p[1])/1e8, small:N(p[2])/1e8, mid:N(p[3])/1e8,
            big:N(p[4])/1e8, superBig:N(p[5])/1e8};
  };
  /* ⚠️ 全天一律优先用**分时末值**：上午 / 午后 / 全天从同一份数据里切出来，
     「上午＋午后＝全天」必然成立，不会出现三个数互相打架。 */
  let dayRow = null, fundScope = '';
  if(fm && fm.kl && fm.kl.length){
    const last = fm.kl[fm.kl.length-1];
    dayRow = {date:fm.date || dayKey, both:fm.both,
              main:last.main/1e8, small:last.small/1e8, mid:last.mid/1e8,
              big:last.big/1e8, superBig:last.superBig/1e8, fromMin:true};
    fundScope = ({sh:'沪市', sz:'深市', both:'沪深两市'})[fm.src] || '沪深两市';
  } else {
    /* 没有分时（收盘很久 / 接口不通）才退回日线，且**日期必须对得上**；
       对不上就宁可不显示，也不把昨天的数标成今天。 */
    const dSh = dayRead(raw.fflow_day), dSz = dayRead(raw.fflow_day_sz);
    if(dSh && dSz){
      dayRow = {date:dSh.date, both:true,
                main:dSh.main+dSz.main, small:dSh.small+dSz.small, mid:dSh.mid+dSz.mid,
                big:dSh.big+dSz.big, superBig:dSh.superBig+dSz.superBig};
      fundScope = '沪深两市';
    } else if(dSh || dSz){
      dayRow = Object.assign({}, dSh || dSz, {both:false});
      fundScope = dSh ? '沪市' : '深市';
    }
  }
  m.fund = {min: fm, day: dayRow, scope: fundScope || '沪深两市'};
  /* 近几个交易日的日级主力净额（「历史与位置」页画柱状；接口给几天就是几天）
     ⚠️ 这里仍是**沪市**口径——该接口对指数只回 1 天，凑不出两市的历史序列。
        等它给得多时再合成（届时 secHFund 也才有得画）。 */
  /* 近 12 个交易日的主力净额（沪深两市）
     ⚠️ 必须用 push2his 的 daykline：kline 家族（含 klt=101）对指数只回 1 天，lmt 给多少都一样。 */
  const dayRows = f => {
    const kl = f && f.data && f.data.klines;
    if(!kl) return [];
    return kl.map(s => {
      const p = s.split(',');
      return {date:p[0], main:N(p[1])/1e8, superBig:N(p[5])/1e8};
    }).filter(x => x.main != null);
  };
  const dsSh = dayRows(raw.fflow_day), dsSz = dayRows(raw.fflow_day_sz);
  if(dsSh.length && dsSz.length){
    const mSz = new Map(dsSz.map(r => [r.date, r]));
    m.fund.days = dsSh.map(r => {
      const o = mSz.get(r.date);
      return o ? {date:r.date, main:r.main + o.main, superBig:r.superBig + o.superBig, both:true}
               : {date:r.date, main:r.main, superBig:r.superBig, both:false};
    });
  } else {
    m.fund.days = (dsSh.length ? dsSh : dsSz).map(r => Object.assign({}, r, {both:false}));
  }
  /* 历史柱状图的口径另存一个字段。⚠️ 不能直接覆盖 m.fund.scope：
     那个是「当天分时」的口径，与历史日线是两件事，叠在一起会互相打脸。 */
  m.fund.histScope = (m.fund.days.length && m.fund.days.every(d => d.both)) ? '沪深两市'
                   : (dsSh.length ? '沪市' : '深市');

  /* —— 资讯流：复盘导读（只来自快照，页面不再实时拉这三条，原因见 URLS 里的注释）——
     快照里没有就是空数组，消费方会明说「这次没取到」。 */
  m.flash     = normNewsData(raw.news_724);
  m.newsARev  = normNewsData(raw.news_a_rev);
  m.newsGlRev = normNewsData(raw.news_gl_rev);

  /* 排行
     ⚠️ 东财行业榜里「航空装备Ⅱ」「航空装备Ⅲ」是同一个指数的两个别名（数值完全一样），
        直接展示会重复。做法：去掉末尾罗马数字后缀后按名字去重，保留靠前（涨得最多）的那条。 */
  const diffRows = rawKey => {
    const d = raw[rawKey] && raw[rawKey].data;
    if(!d || !d.diff) return null;
    return Array.isArray(d.diff) ? d.diff : Object.keys(d.diff).map(k => d.diff[k]);
  };
  const uniqLevel = rows => {
    const seen = {}, out = [];
    rows.forEach(x => {
      const nm = stripLv(x.f14);
      if(seen[nm]) return; seen[nm] = 1; out.push(x);
    });
    return out;
  };
  /* ⚠️ 列表长度按标题写：标题说「前 8」就真的给 8 条。
     以前统一 slice(0,6)，于是「主力净流入前 8」底下只有 6 行。
     skipNew = 剔掉当日新股（N/C 开头），个股榜专用。 */
  const rank = (rawKey, skipNew) => {
    const rows = diffRows(rawKey);
    if(!rows) return [];
    const pool = skipNew ? rows.filter(x => !isNewStock(x.f14)) : rows;
    return uniqLevel(pool).slice(0,8).map(x => ({
      name:stripLv(x.f14), code:x.f12, chgPct:N(x.f3), main:(N(x.f62)||0)/1e8,
      mainPct:N(x.f184), ind:x.f100||null
    }));
  };
  m.rank = {
    sectorIn: rank('sector_in'), sectorOut: rank('sector_out'),
    stockIn: rank('stock_in', true)
  };

  /* —— 板块地图：按【涨跌幅】排的行业涨幅榜/跌幅榜 + 概念涨幅榜 ——
     ⚠️ 东财把行业切得很细，有的细分行业只有一两只成分股，一只票涨停就能把它顶到
        榜首（2026-11-03 涨幅榜前四名的成分股数分别是 2 / 2 / 1 / 1）。这种排名对
        新手是纯噪音，会被读成「教育运营及其他」是当天的大方向。
     做法：先挑成分股 ≥ BOARD_MIN 的；凑不满 6 条，再拿样本少的补上，
        补上来的那几条由 row() 标一句「成分股仅 N 只」。 */
  const BOARD_MIN = 5;
  const board = (rawKey, n) => {
    const rows = diffRows(rawKey);
    if(!rows) return [];
    const all = uniqLevel(rows).map(x => ({
      name:stripLv(x.f14), code:x.f12, chgPct:N(x.f3), main:(N(x.f62)||0)/1e8,
      up:N(x.f104), down:N(x.f105), lead:x.f128||null, leadCode:x.f140||null,
      /* 成分股数 = 涨的 + 跌的 */
      total: (N(x.f104)||0) + (N(x.f105)||0)
    }));
    const want = n || 6, out = [];
    all.forEach(x => { if(out.length < want && x.total >= BOARD_MIN) out.push(x); });
    if(out.length < want) all.forEach(x => {
      if(out.length < want && out.indexOf(x) < 0) out.push(x);
    });
    return out;
  };
  m.board = {
    indUp: board('sector_up', 6), indDown: board('sector_down', 6),
    conUp: board('concept_up', 8)
  };

  /* —— 个股榜：涨幅榜 / 成交额榜 ——
     ⚠️ 涨幅榜必须剔掉当日新股：上市首日（N 开头）涨 300%+ 是规则允许的，
        但把它排在「全 A 股涨幅榜」第一名，读者会以为市场普遍大涨。 */
  const list8 = (rawKey, skipNew) => {
    const rows = diffRows(rawKey);
    if(!rows) return [];
    const pool = skipNew ? rows.filter(x => !isNewStock(x.f14)) : rows;
    return pool.slice(0,8).map(x => ({
      code:x.f12, name:x.f14, chgPct:N(x.f3), price:N(x.f2),
      amount:(N(x.f6)||0)/1e8, ind:x.f100||null
    }));
  };
  m.stockRank = {chg:list8('stock_chg', true), amt:list8('stock_amt')};

  /* —— 量能：从沪指/深成指日K算两市成交额，用于「较上一交易日放量/缩量」—— */
  const kday = k => {
    const kl = raw[k] && raw[k].data && raw[k].data.klines;
    if(!kl) return null;
    const o = {};
    kl.forEach(s => { const p = s.split(','); o[p[0]] = N(p[2])/1e8; });
    return o;
  };
  const shD = kday('kday_sh'), szD = kday('kday_sz');
  /* 同一天还要能拿到「收盘」，才能在量能柱上顺带标出当天的涨跌（量价要一起看） */
  const kclose = k => {
    const kl = raw[k] && raw[k].data && raw[k].data.klines;
    if(!kl) return null;
    const o = {};
    kl.forEach(s => { const p = s.split(','); o[p[0]] = N(p[1]); });
    return o;
  };
  const shC = kclose('kday_sh');
  m.volume = null;
  if(shD && szD){
    const dates = Object.keys(shD).filter(d => szD[d] != null).sort();
    if(dates.length >= 2){
      const today = dates[dates.length-1], prev = dates[dates.length-2];
      const past = dates.slice(0, -1);          // 不含「今天」的那几行
      const series = dates.map((d, i) => {
        const pc = (i > 0 && shC) ? shC[dates[i-1]] : null;
        const cc = shC ? shC[d] : null;
        return {date:d, total: shD[d] + szD[d], close:cc,
                chg:(pc && cc != null) ? (cc - pc) / pc * 100 : null};
      });
      m.volume = {
        date: today, prevDate: prev,
        today: series[series.length-1].total,
        prev: series[series.length-2].total,
        avg: past.reduce((s,d) => s + shD[d] + szD[d], 0) / past.length,
        days: past.length,
        sh: shD[today], sz: szD[today],
        series: series,
        matched: ((raw._meta && raw._meta.tradeDate) || '').replace(/-/g,'') === today.replace(/-/g,'')
      };
    }
  }

  /* —— 涨停梯队：连板高度分布 + 最高板个股 + 涨停最多的行业 —— */
  m.ladder = null;
  const zp = raw.ztpool && raw.ztpool.data;
  if(zp){
    const pool = zp.pool || [];
    const dist = {};
    pool.forEach(r => { const k = N(r.lbc) || 1; dist[k] = (dist[k]||0) + 1; });
    const hs = Object.keys(dist).map(Number).sort((a,b) => b - a);
    const byInd = {};
    pool.forEach(r => { const k = r.hybk || '其他'; byInd[k] = (byInd[k]||0) + 1; });
    m.ladder = {
      n: pool.length,
      max: hs.length ? hs[0] : null,
      dist: hs.map(h => ({h:h, n:dist[h]})),
      top: pool.filter(r => (N(r.lbc)||1) >= 2)
               .sort((a,b) => (N(b.lbc)||1)-(N(a.lbc)||1) || (N(b.fund)||0)-(N(a.fund)||0))
               .slice(0,8)
               .map(r => ({code:r.c, name:r.n, lbc:N(r.lbc)||1,
                           fund:(N(r.fund)||0)/1e8, ind:r.hybk||'', zbc:N(r.zbc)||0,
                           zt:(r.zttj && N(r.zttj.days)) || null})),
      hot: Object.keys(byInd).map(k => ({name:k, n:byInd[k]}))
                 .sort((a,b) => b.n - a.n).slice(0,6)
    };
  }

  /* 两融 */
  const md = raw.margin && raw.margin.result && raw.margin.result.data;
  if(md && md.length){
    const r = md[0], prev = md[1];
    m.margin = {
      date: (r.DIM_DATE||'').slice(0,10),
      rzye: N(r.RZYE)/1e8, rzmre: N(r.RZMRE)/1e8, rzche: N(r.RZCHE)/1e8,
      rqye: N(r.RQYE)/1e8,
      net: (N(r.RZMRE)-N(r.RZCHE))/1e8,
      chg: (prev && N(prev.RZYE)) ? (N(r.RZYE)-N(prev.RZYE))/1e8 : null,
      chgPct: (prev && N(prev.RZYE)) ? (N(r.RZYE)-N(prev.RZYE))/N(prev.RZYE)*100 : null
    };
  }

  /* 日K（沪深300，东财源） */
  m.kline = klSeries(raw.kline);
  /* 「历史与位置」页：四个 A 股主要指数的近 60 个交易日
     —— 上证/深证/创业板走腾讯源，沪深300 复用上面那条（东财源） */
  m.klIdx = [
    {key:'sh',    name:'上证指数', short:'上证',   series:klSeries(raw.kline_sh000001)},
    {key:'sz',    name:'深证成指', short:'深证',   series:klSeries(raw.kline_sz399001)},
    {key:'cyb',   name:'创业板指', short:'创业板', series:klSeries(raw.kline_sz399006)},
    {key:'hs300', name:'沪深300',  short:'沪深300', series:m.kline}
  ].filter(x => x.series);

  /* ══ 全球市场 ══════════════════════════════════════════════════
     gl_core 一次拿回 26 个核心指数，用 GIDX 的 sec 分到 4 个市场。
     注意 A股 是 1./0. 段，港股恒指系列在 100. 段、恒生科技在 124. 段。 */
  const arrify = x => !x ? [] : (Array.isArray(x) ? x : Object.keys(x).map(k => x[k]));

  const glc = arrify(raw.gl_core && raw.gl_core.data && raw.gl_core.data.diff);
  if(glc.length){
    const byKey = {};
    glc.forEach(x => { if(x && x.f12 != null) byKey[x.f13 + '.' + x.f12] = x; });
    m.gidx = GIDX.map(g => {
      const it = byKey[g.sec];
      if(!it) return null;
      const a = N(it.f6);
      return Object.assign({}, g, {
        price:N(it.f2), chgPct:N(it.f3), chg:N(it.f4),
        amount: a == null ? null : a/1e8,
        high:N(it.f15), low:N(it.f16), open:N(it.f17), prevClose:N(it.f18)
      });
    }).filter(Boolean);
  }
  /* 港股 / 美股精选标的（⚠️ 不用 clist 涨幅榜：那边前排全是牛熊证、窝轮、仙股） */
  const glStocks = src => arrify(raw[src] && raw[src].data && raw[src].data.diff).map(x => ({
    code:x.f12, name:x.f14, price:N(x.f2), chgPct:N(x.f3), chg:N(x.f4),
    amount: N(x.f6) == null ? null : N(x.f6)/1e8, high:N(x.f15), low:N(x.f16)
  })).filter(x => x.name && x.price != null);
  m.ghk = glStocks('gl_hk');
  m.gus = glStocks('gl_us');

  /* 全球指数全量（东财 m:100 共 63 个）→ 按 亚太/欧洲/美洲/商品与汇率 分组 */
  const gw = arrify(raw.gl_world && raw.gl_world.data && raw.gl_world.data.diff);
  if(gw.length){
    m.gworld = gw.map(x => ({code:x.f12, name:x.f14, price:N(x.f2), chgPct:N(x.f3),
                             region: WREGION[x.f12] || '其他'}))
                 .filter(x => x.region !== 'skip' && x.name && x.price != null);
  }
  /* 海外分时与 60 日线（切市场时第 2 段走时间、第 7 段看位置） */
  m.gtrend = {
    hk: splitTrend(raw.gl_trend_hk, '12:00'),   // 港股午休是 12:00–13:00，与 A 股不同
    us: trendLite(raw.gl_trend_us),
    jp: trendLite(raw.gl_trend_jp)
  };
  m.gkline = {hk: klSeries(raw.gl_kline_hk), us: klSeries(raw.gl_kline_us)};

  m.meta = raw._meta || {};
  m.ok = true;
  return m;
}

/* ══════════════════════════════════════════════════════════════════
   3 · 交易时段状态
   ══════════════════════════════════════════════════════════════════ */
function sessionState(now){
  const d = now.getDay(), hm = now.getHours()*60 + now.getMinutes();
  const mk = (name, activeKey, progress) => ({name, activeKey, progress});
  if(d===0 || d===6) return mk('休市（周末）', null, 0);
  if(hm < 9*60+15)      return mk('盘前', null, 0);
  if(hm < 9*60+30)      return mk('开盘前挂单中（9:30 才成交）', null, 0);
  if(hm <= 11*60+30)    return mk('上午交易中', 'am', (hm-(9*60+30))/(11*60+30-(9*60+30)));
  if(hm < 13*60)        return mk('午间休市', null, 0.5);
  if(hm <= 15*60)       return mk('午后交易中', 'pm', 0.5 + (hm-13*60)/(15*60-13*60)*0.5);
  return mk('已收盘', null, 1);
}

/* ══════════════════════════════════════════════════════════════════
   4 · 图表（手写 SVG，零依赖）
   —— 每张图都支持「鼠标移过去读数」：十字准线 + 跟随浮层。
      做法：把每张图的序列与坐标换算登记进 CHARTS，事件走一次委托，
      不在每张图上各绑一套监听（图多、还会因切页/切市场重建）。
   ══════════════════════════════════════════════════════════════════ */
let __cid = 0;
const CHARTS = {};
const CTIP = {el:null, sid:null};

function chartTipEl(){
  if(!CTIP.el){
    const d = document.createElement('div');
    d.className = 'chart-tip'; d.id = 'chartTip'; d.hidden = true;
    document.body.appendChild(d);
    CTIP.el = d;
  }
  return CTIP.el;
}
function clearCursor(cid){
  const g = document.getElementById('cur-' + cid);
  if(g) g.style.opacity = 0;
}
function hideChartTip(){
  if(CTIP.el) CTIP.el.hidden = true;
  if(CTIP.sid){ clearCursor(CTIP.sid); CTIP.sid = null; }
}
/** 把浮层放到鼠标附近；靠近右/下边界时自动翻边，避免被窗口切掉 */
function placeTip(tip, clientX, anchorTop){
  tip.hidden = false;
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  let left = clientX + 14;
  if(left + tw > window.innerWidth - 8) left = clientX - tw - 14;
  let top = anchorTop - th / 2;
  top = Math.max(8, Math.min(Math.max(8, window.innerHeight) - th - 8, top));
  tip.style.left = Math.max(8, left) + 'px';
  tip.style.top = top + 'px';
}
function showChartTip(svg, clientX){
  const cid = svg.getAttribute('data-cid');
  const c = CHARTS[cid];
  if(!c) return;
  const r = svg.getBoundingClientRect();
  if(!r.width || !r.height) return;
  const vbx = (clientX - r.left) / r.width * c.w;
  let i = Math.round((vbx - c.pad) / Math.max(1, c.w - c.pad * 2) * (c.n - 1));
  i = Math.max(0, Math.min(c.n - 1, i));
  const v = c.series[i] && c.series[i].v;
  if(v == null){ hideChartTip(); return; }

  const px = c.X(i), py = c.Y(v);
  if(CTIP.sid && CTIP.sid !== cid) clearCursor(CTIP.sid);
  const g = document.getElementById('cur-' + cid);
  if(g){
    g.style.opacity = 1;
    const vline = g.querySelector('.cur-x'), dot = g.querySelector('.cur-d');
    vline.setAttribute('x1', px); vline.setAttribute('x2', px);
    dot.setAttribute('cx', px); dot.setAttribute('cy', py);
  }
  CTIP.sid = cid;

  const tip = chartTipEl();
  tip.innerHTML = c.tipHtml(i, v);
  placeTip(tip, clientX, r.top + (py / c.h) * r.height);
}
/** 柱状图（成交额 / 资金流）的读数：内容存在 BARS 注册表里，按 data-bar 取
    为什么不用 data-tip 属性：tip 里带 HTML 与双引号，塞进属性要转义、转义后又会被
    当成字面量显示；注册表最省事。（2026-10-16 修：此前选择器写的是 .vb[data-tip]，
    与渲染出的 data-bar 对不上，柱子悬停始终不响应。） */
function showBarTip(vb){
  hideChartTip();
  const tip = chartTipEl();
  tip.innerHTML = BARS[vb.getAttribute('data-bar')] || '';
  tip.hidden = false;                       // 先显示才能量到尺寸
  const rr = vb.getBoundingClientRect();
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  let left = rr.left + rr.width / 2 - tw / 2;
  left = Math.max(8, Math.min(window.innerWidth - tw - 8, left));
  // ⚠️ 上下都要夹住：柱子常在屏幕下半部，只按「放在上方」算会把浮层顶到视口外面
  let top = rr.top - th - 10;               // 优先放柱子上方
  if(top < 8) top = rr.bottom + 10;         // 上方不够就翻到下方
  top = Math.max(8, Math.min(window.innerHeight - th - 8, top));
  tip.style.left = left + 'px';
  tip.style.top = top + 'px';
}
function bindChartTips(){
  if(window._ctip) return;
  window._ctip = 1;
  const onMove = e => {
    const t = e.target;
    if(!t || !t.closest){ hideChartTip(); return; }
    const svg = t.closest('svg.spark[data-cid]');
    if(svg){ showChartTip(svg, e.clientX); return; }
    const vb = t.closest('.vb[data-bar]');
    if(vb){ showBarTip(vb); return; }
    hideChartTip();
  };
  document.addEventListener('pointermove', onMove, {passive:true});
  document.addEventListener('pointerdown', onMove, {passive:true});
  document.addEventListener('pointerleave', hideChartTip);
  window.addEventListener('blur', hideChartTip);
  window.addEventListener('scroll', hideChartTip, {passive:true});
}

function lineChart(series, opts){
  opts = opts || {};
  const w = opts.w || 900, h = opts.h || 160, pad = 8;
  const vals = series.map(p => p.v).filter(v => v!=null);
  if(vals.length < 2) return '<div class="muted" style="padding:24px;text-align:center">暂无走势数据</div>';
  let lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  if(opts.zero){ lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  if(hi - lo < 1e-9){ hi = lo + 1; }
  const n = series.length, iw = w - pad*2, ih = h - pad*2;
  const X = i => pad + iw * i / Math.max(1, n-1);
  const Y = v => pad + ih * (1 - (v-lo)/(hi-lo));
  const pts = series.map((p,i) => X(i).toFixed(2) + ',' + Y(p.v).toFixed(2)).join(' ');
  const rising = series[n-1].v >= series[0].v;
  const color = opts.color || (rising ? '#D93636' : '#12925C');
  let zeroLine = '';
  if(opts.zero && lo < 0 && hi > 0){
    zeroLine = '<line x1="' + pad + '" y1="' + Y(0).toFixed(2) + '" x2="' + (w-pad) + '" y2="' + Y(0).toFixed(2) +
               '" stroke="#D8DEE5" stroke-width="1" stroke-dasharray="3 3"/>';
  }
  // 午间断点
  let gap = '';
  if(opts.gapIndex != null && opts.gapIndex > 0 && opts.gapIndex < n-1){
    const gx = X(opts.gapIndex).toFixed(2);
    gap = '<line x1="' + gx + '" y1="' + pad + '" x2="' + gx + '" y2="' + (h-pad) + '" stroke="#EDF0F3" stroke-width="1"/>';
  }
  const last = pts.split(' ').pop().split(',');
  // 交互登记：坐标系、序列、浮层内容工厂
  const cid = opts.cid || ('ch' + (++__cid));
  CHARTS[cid] = {series:series, w:w, h:h, pad:pad, n:n, X:X, Y:Y,
                 tipHtml: opts.tip || (() => '<div class="ct-r"><span class="ct-k">数值</span>' +
                                                '<span class="ct-v">' + num(series[0] && series[0].v, 2) + '</span></div>')};
  return '<div class="chart-wrap">' +
    '<svg class="spark" data-cid="' + cid + '" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" role="img">' +
    '<defs><linearGradient id="g' + cid + '" x1="0" y1="0" x2="0" y2="1">' +
    '<stop offset="0%" stop-color="' + color + '" stop-opacity="0.16"/>' +
    '<stop offset="100%" stop-color="' + color + '" stop-opacity="0"/></linearGradient></defs>' +
    zeroLine + gap +
    '<polygon points="' + pad + ',' + (h-pad) + ' ' + pts + ' ' + (w-pad) + ',' + (h-pad) + '" fill="url(#g' + cid + ')"/>' +
    '<polyline points="' + pts + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' +
    '<g class="cur" id="cur-' + cid + '" style="opacity:0">' +
      '<line class="cur-x" x1="0" y1="' + pad + '" x2="0" y2="' + (h-pad) + '" stroke="#8A94A0" stroke-width="1" ' +
        'stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>' +
      '<circle class="cur-d" cx="0" cy="0" r="4" fill="#fff" stroke="' + color + '" stroke-width="2"/>' +
    '</g>' +
    '<circle cx="' + last[0] + '" cy="' + last[1] + '" r="3.5" fill="' + color + '" stroke="#fff" stroke-width="1.6"/></svg>' +
    (opts.foot ? '<div class="chart-foot">' + opts.foot + '</div>' : '') +
  '</div>';
}

/* ══════════════════════════════════════════════════════════════════
   5 · 复盘文字（午评 / 午后 / 收评）
   原则：只陈述这一天实际发生了什么（点位、成交、家数、资金、板块），
        不做预测、不给方向、不出现任何买卖建议。
   ══════════════════════════════════════════════════════════════════ */
const word = v => v>0.3?'上涨':(v<-0.3?'下跌':'基本走平');
const weak = v => v>1.2?'明显':(v>0.3?'小幅':(v<-1.2?'明显':(v<-0.3?'小幅':'')));
/** 一段复盘：左侧固定小标签，右侧正文 */
const rp = (label, body) => '<p class="rp"><span class="rp-l">' + label + '</span><span class="rp-t">' + body + '</span></p>';
/** 交易日是否已经走完（收盘或休市），决定用「全天」还是「截至此刻」 */
function dayWord(){
  const n = sessionState(new Date()).name;
  return (n === '已收盘' || n.indexOf('休市') >= 0) ? '全天' : '截至此刻';
}
/** 三点指数一起说 */
function idxPhrase(idx, key){
  return idx.map(i => i.name + ' <b class="' + cls(i[key]) + '">' + pct(i[key]) + '</b>').join('、');
}
/** 放量 / 缩量 措辞 */
function volWord(r){
  if(r == null) return '';
  const a = Math.abs(r);
  if(a < 1.5) return '与上一交易日基本持平';
  return (r > 0 ? '较上一交易日放量 ' : '较上一交易日缩量 ') + a.toFixed(1) + '%';
}

/* ── 午评（半场） ─────────────────────────────────────────── */
/* ── 分时资金的口径备注 ──────────────────────────────────────── */
/* 只有单市取到时才加注；只看分时自己的 src，不与日线历史的口径混用。 */
function flowNote(fm){
  const t = fm && ({sh:'沪市', sz:'深市'})[fm.src];
  return t ? '<span class="note">（只算' + t + '）</span>' : '';
}

function reportAM(m, am){
  if(!am) return rp('数据', '暂无上午分时数据。');
  const idx = m.indices.filter(i => i.trend);
  const sh = m.indices.find(i => i.code === '000001') || {};
  const L = [];

  /* ① 指数 */
  const ups = idx.filter(i => i.trend.amChgPct > 0).length;
  const allW = ups === idx.length ? '全线上扬' : (ups === 0 ? '全线走低' : '涨跌互现');
  const shape = am.amShape;
  L.push(rp('指数', '上午四个主要指数<b>' + allW + '</b>：'
    + idx.map(i => i.name + ' <b class="' + cls(i.trend.amChgPct) + '">' + pct(i.trend.amChgPct) + '</b>').join('、')
    + '。' + (shape ? openWord(shape.openPct) + '开局。' : '')));

  /* ② 走势 */
  if(shape) L.push(rp('走势', '上午收成<b>' + shape.text + '</b>的形态：'
    + '最高 ' + num(am.amHigh) + '（' + shape.hiTime + '）、最低 ' + num(am.amLow) + '（' + shape.loTime + '），'
    + '半日振幅 ' + shape.range.toFixed(2) + '%。'
    + (am.open30Pct == null ? ''
        : (Math.abs(am.open30Pct) < 0.05 ? '开盘头半小时基本走平。'
           : '开盘头半小时' + word(am.open30Pct) + ' ' + Math.abs(am.open30Pct).toFixed(2) + '%。'))));

  /* ③ 量能（口径：上证指数 = 沪市） */
  let vtxt = '沪市半日成交 <b>' + money(am.amAmount) + '</b>';
  if(sh.amount){
    const r = am.amAmount / sh.amount * 100;
    if(r > 98) vtxt += '，这就是沪市今天全部的成交。';
    else vtxt += '，占沪市全天成交的 <b>' + r.toFixed(0) + '%</b>'
      + (r > 55 ? '——上午的参与度明显高于下午。' : (r < 45 ? '——成交更集中在下午。' : '——上下午参与度接近。'));
  } else vtxt += '。';
  L.push(rp('成交', vtxt + '<span class="note">（分时成交额以上证指数为例，是只算沪市，不是两市合计）</span>'));

  /* ④ 资金 */
  const fm = m.fund && m.fund.min;
  if(fm) L.push(rp('资金', '上午主力资金（大单＋超大单）累计净'
    + (fm.amEnd >= 0 ? '<b class="up">流入 ' : '<b class="down">流出 ') + money(Math.abs(fm.amEnd)) + '</b>。'
    + flowNote(fm)));
  return L.join('');
}

/* ── 午后（盘中快评） ─────────────────────────────────────────────────── */
function reportPM(m, am){
  if(!am) return rp('数据', '暂无下午分时数据。');
  const d = am.pmChgPct;
  const L = [];

  /* ① 下午相对上午 —— ⚠️ 必须按「上午方向 × 午后方向」分四种写。
     旧写法只按午后涨跌挑词，于是「上午 −0.71%、午后 −0.11%」也被写成
     「回吐了上午的涨幅」——上午全天在跌，根本没有涨幅可以回吐。 */
  const sh = m.indices.find(i => i.code === '000001') || {};
  const amD = (am.amChgPct == null) ? null : am.amChgPct;
  const pmWord = (d == null) ? ''
    : (d > 0
        ? (amD == null ? '指数上行' : (amD > 0 ? '在上午上涨的基础上继续走高'
                                              : '把上午的跌幅收回来一部分'))
        : (d < 0
          ? (amD == null ? '指数回落' : (amD > 0 ? '把上午的一部分涨幅回吐掉'
                                                : '在上午下跌的基础上继续走低'))
          : '基本横住'));
  L.push(rp('指数', (d == null ? '' : '午后相对上午收盘 <b class="' + cls(d) + '">' + pct(d) + '</b>，' + pmWord)
    + (sh.chgPct != null ? '；全天最终收在 <b class="' + cls(sh.chgPct) + '">' + pct(sh.chgPct) + '</b>。' : '。')));

  /* ② 量能：下午 vs 上午 */
  let vtxt = '午后沪市成交 <b>' + money(am.pmAmount) + '</b>';
  if(am.amAmount){
    const r = (am.pmAmount - am.amAmount) / am.amAmount * 100;
    vtxt += '，比上午的 ' + money(am.amAmount) + (Math.abs(r) < 6 ? '基本持平' : (r > 0 ? '放大 ' + r.toFixed(0) + '%' : '缩小 ' + Math.abs(r).toFixed(0) + '%')) + '。';
  }
  L.push(rp('成交', vtxt));

  /* ③ 走势 */
  const ps = am.pmShape;
  if(ps) L.push(rp('走势', '午后收成<b>' + ps.text + '</b>的形态：'
    + '下午最高 ' + num(am.pmHigh) + '（' + ps.hiTime + '）、最低 ' + num(am.pmLow) + '（' + ps.loTime + '）。'
    + (am.pmTailPct == null ? ''
        : (Math.abs(am.pmTailPct) < 0.15 ? '尾盘最后半小时基本走平。'
           : '尾盘最后半小时' + (am.pmTailPct > 0 ? '回升 ' : '继续走低 ') + Math.abs(am.pmTailPct).toFixed(2) + '%。'))));

  /* ④ 资金：上午 vs 下午 的对照，这是分时段看板最有信息量的一句 */
  const fm = m.fund && m.fund.min;
  if(fm){
    let f = '午后主力资金净' + (fm.pmDelta >= 0 ? '<b class="up">流入 ' : '<b class="down">流出 ') + money(Math.abs(fm.pmDelta)) + '</b>';
    if(fm.amEnd > 0 && fm.pmDelta < 0) f += '——上午净流入的 ' + money(fm.amEnd) + '，在下午被大幅回吐。';
    else if(fm.amEnd < 0 && fm.pmDelta > 0) f += '——上午净流出的 ' + money(Math.abs(fm.amEnd)) + '，下午部分回流。';
    else if(fm.amEnd > 0 && fm.pmDelta > 0) f += '——上下午同向净流入，资金持续加仓。';
    else if(fm.amEnd < 0 && fm.pmDelta < 0) f += '——上下午同向净流出，资金持续撤离。';
    L.push(rp('资金', f + flowNote(fm)));
  }
  return L.join('');
}

/* ── 全天（收评） ───────────────────────────────────────────── */
function reportAll(m, t){
  const idx = m.indices, b = m.breadth, v = m.volume, fd = m.fund && m.fund.day;
  const dw = dayWord();
  const L = [];

  /* ① 收盘 */
  const ups = idx.filter(i => i.chgPct > 0).length;
  const allW = ups === idx.length ? '全线上涨' : (ups === 0 ? '全线下跌' : '涨跌互现');
  L.push(rp('收盘', dw + '四个主要指数<b>' + allW + '</b>：'
    + idx.map(i => i.name + ' <b class="' + cls(i.chgPct) + '">' + pct(i.chgPct) + '</b>（' + num(i.price) + '）').join('、')
    + '。' + (t && t.allShape ? '全天走成<b>' + t.allShape.text + '</b>的形态。' : '')));

  /* ② 量能：与上一交易日、与近几日对比 */
  let vtxt = '';
  if(v){
    vtxt = '两市成交额 <b>' + money(v.today) + '</b>（沪市 ' + money(v.sh) + '、深市 ' + money(v.sz) + '），'
         + volWord((v.today - v.prev) / v.prev * 100) + '（上一交易日 ' + money(v.prev) + '）。';
    if(v.avg) vtxt += '近 ' + v.days + ' 个交易日的平均成交是 ' + money(v.avg) + '，今天是它的 ' + (v.today / v.avg * 100).toFixed(0) + '%。';
    if(!v.matched) vtxt += '<span class="note">（最近一个交易日 ' + md(v.date) + '）</span>';
  } else {
    vtxt = '两市成交额 <b>' + money(m.turnover.amount) + '</b>。';
  }
  L.push(rp('成交', vtxt));

  /* ③ 市场广度：赚钱面 + 情绪温度 */
  if(b.up != null){
    const share = b.total ? b.up / b.total * 100 : null;
    let s = '沪深两市上涨 <b class="up">' + H(b.up) + '</b> 家、下跌 <b class="down">' + H(b.down) + '</b> 家、平盘 ' + H(b.flat) + ' 家';
    if(share != null) s += '，上涨家数占 <b>' + share.toFixed(1) + '%</b>';
    s += '（<b>不含北交所</b>）。';
    if(b.limitUp != null){
      const seal = (b.limitUp + (b.bomb || 0)) ? b.limitUp / (b.limitUp + (b.bomb || 0)) * 100 : null;
      s += '涨停 <b class="up">' + H(b.limitUp) + '</b> 家、跌停 <b class="down">' + H(b.limitDown) + '</b> 家、炸板 ' + H(b.bomb) + ' 家';
      if(seal != null) s += '，封板率 <b>' + seal.toFixed(0) + '%</b>'
        + (seal > 70 ? '（资金愿意把板封住，情绪偏强）。' : (seal < 45 ? '（炸板偏多，追高容易被套）。' : '。'));
      else s += '。';
    }
    L.push(rp('涨跌家数', s));
  }

  /* ④ 板块地图 */
  const bd = m.board || {};
  if(bd.indUp && bd.indUp.length){
    let s = '行业里 <b>' + bd.indUp[0].name + '</b> 领涨 <b class="up">' + pct(bd.indUp[0].chgPct) + '</b>'
          + (bd.indUp[0].lead ? '（' + bd.indUp[0].lead + ' 等领涨）' : '') + '，';
    s += '紧随其后的是 ' + bd.indUp.slice(1, 4).map(x => x.name + ' ' + pct(x.chgPct)).join('、') + '；';
    if(bd.indDown && bd.indDown.length){
      s += '跌幅靠前的是 <b>' + bd.indDown[0].name + '</b> <b class="down">' + pct(bd.indDown[0].chgPct) + '</b>、'
        + bd.indDown.slice(1, 3).map(x => x.name + ' ' + pct(x.chgPct)).join('、') + '。';
    }
    L.push(rp('板块', s));
    if(bd.conUp && bd.conUp.length){
      L.push(rp('题材', '概念板块中 <b>' + bd.conUp[0].name + '</b> 涨幅居前 <b class="up">' + pct(bd.conUp[0].chgPct) + '</b>'
        + (bd.conUp[0].lead ? '，领涨股 ' + bd.conUp[0].lead : '') + '；'
        + bd.conUp.slice(1, 4).map(x => x.name + ' ' + pct(x.chgPct)).join('、') + '。'));
    }
  }

  /* ⑤ 资金全景 */
  if(fd){
    let s = '主力资金（大单＋超大单）' + dw + '净' + (fd.main >= 0 ? '<b class="up">流入 ' : '<b class="down">流出 ')
      + money(Math.abs(fd.main)) + '</b>。';
    const small = fd.small, big = fd.big + fd.superBig;
    if(small != null && big != null && Math.sign(small) !== Math.sign(big) && Math.abs(big) > 1){
      s += big < 0
        ? '结构上<b>大单在卖、小单在买</b>——通常意味着大资金在减、散户在接。'
        : '结构上<b>大单在买、小单在卖</b>——通常意味着大资金在吸、散户在交筹码。';
    }
    const mg = m.margin;
    if(mg && mg.chg != null) s += ' 两融方面，' + mg.date + ' 融资余额 ' + money(mg.rzye) + '，较前一日'
      + (mg.chg >= 0 ? '增加 ' : '减少 ') + money(Math.abs(mg.chg)) + '（两融数据由交易所次日发布，天然滞后一天）。';
    L.push(rp('资金', s));
  }

  /* ⑥ 涨停梯队 */
  const lad = m.ladder;
  if(lad && lad.n){
    let s = '全市场涨停 <b>' + lad.n + '</b> 家';
    if(lad.max) s += '，最高 <b>' + lad.max + ' 连板</b>';
    const mid = lad.dist.filter(x => x.h >= 2);
    if(mid.length) s += '；' + mid.map(x => x.h + ' 板 ' + x.n + ' 家').join('、') + '，其余为首板。';
    else s += '，全部为首板（没有连续涨停的个股）。';
    if(lad.hot && lad.hot.length) s += ' 涨停个股里，' + lad.hot.slice(0, 3).map(x => x.name + ' ' + x.n + ' 家').join('、') + ' 相对集中。';
    L.push(rp('梯队', s));
  }

  /* ⑦ 一句话概括 */
  const face = [];
  if(b.up != null && b.down != null && idx.length){
    const idxUp = ups > idx.length / 2, mktUp = b.up > b.down;
    face.push(idxUp === mktUp
      ? (idxUp ? '指数与个股同向上涨，是普涨的一天。' : '指数与个股同向走弱，是普跌的一天。')
      : (idxUp ? '指数在涨、但下跌家数更多——权重股撑住了指数，多数个股没跟上。'
               : '指数走弱、但上涨家数更多——权重拖累指数，个股其实还行。'));
  }
  if(v && v.prev){
    const up = (idx.filter(i => i.code === '000001')[0] || {}).chgPct;
    if(up != null){
      const r = v.today - v.prev;
      face.push(up > 0 ? (r > 0 ? '成交比昨天放大，上涨有量配合。' : '成交比昨天缩小，属于缩量上涨。')
                       : (r > 0 ? '成交比昨天放大，下跌伴随抛压。' : '成交比昨天缩小，属于缩量调整。'));
    }
  }
  if(b.limitUp != null) face.push('涨停 ' + b.limitUp + ' 家对跌停 ' + b.limitDown + ' 家。');
  face.push('以上都是已经发生的事，不构成对后市的判断。');
  L.push(rp('一句话', face.join('')));

  return L.join('');
}

/* ══════════════════════════════════════════════════════════════════
   6 · 渲染
   ══════════════════════════════════════════════════════════════════ */
/* ── 持仓表：一张表，两层用法 ──────────────────────────────
   监控层：只看价格（代码一填就自动带行情），适合"想盯着"的标的；
   纪律线层：多出成本价 / 浮动盈亏 / 红线 / 触发价，适合"已经买了"的标的。
   两套列共用同一份数据，切换视图不丢任何填写内容。 */
const COLS = {
  name:      {h:'名称',           cls:'c-name',       kind:'text', k:'name', ph:'如 沪深300ETF'},
  code:      {h:'代码（只填数字）', cls:'c-code',       kind:'code'},
  price:     {h:'现价（打开页面时）',           cls:'c-num c-px',   kind:'price'},
  chg:       {h:'今日涨跌',       cls:'c-num',        kind:'chg'},
  chgAmt:    {h:'涨跌额',         cls:'c-num',        kind:'chgAmt'},
  range:     {h:'今日最低 – 最高',   cls:'c-num',        kind:'range'},
  amount:    {h:'成交额',         cls:'c-num',        kind:'amount'},
  cost:      {h:'我的成本价',     cls:'c-num',        kind:'text', k:'cost', ph:'买入均价'},
  pnl:       {h:'浮动盈亏',       cls:'c-num calc',   kind:'pnl'},
  red:       {h:'纪律红线',       cls:'c-num',        kind:'text', k:'red',  ph:'跌到这里重新评估'},
  redDist:   {h:'距红线',         cls:'c-num calc',   kind:'redDist'},
  next:      {h:'下一档触发价',   cls:'c-num',        kind:'text', k:'next', ph:'预设价位'},
  nextState: {h:'触发状态',       cls:'c-num calc',   kind:'nextState'},
  del:       {h:'',               cls:'c-del',        kind:'del'}
};
const VIEWS = {
  watch: ['name','code','price','chg','chgAmt','range','amount','del'],
  disc:  ['name','code','cost','price','pnl','red','redDist','next','nextState','del']
};
const VKEY = 'mb_hold_view';
let VIEW = 'watch';
function loadView(){ try{ const v = localStorage.getItem(VKEY); if(VIEWS[v]) VIEW = v; }catch(e){} }
function saveView(){ try{ localStorage.setItem(VKEY, VIEW); }catch(e){} }

function cellHtml(row, i, key){
  const C = COLS[key];
  if(C.kind === 'text') return '<td class="' + C.cls + '"><input data-i="' + i + '" data-k="' + C.k + '" value="' + esc(row[C.k]) + '" placeholder="' + C.ph + '"></td>';
  if(C.kind === 'code') return '<td class="' + C.cls + '"><div class="code-w">' +
      '<input data-i="' + i + '" data-k="code" value="' + esc(row.code) + '" placeholder="510300" inputmode="numeric">' +
      '<button class="lk-btn" data-lk="' + i + '" title="按代码或名称查一次">查</button></div>' +
      '<span class="lk-state" id="lk-' + i + '"></span></td>';
  if(C.kind === 'price')     return '<td class="' + C.cls + '" id="px-' + i + '"></td>';
  if(C.kind === 'chg')       return '<td class="' + C.cls + ' calc" id="chg-' + i + '"></td>';
  if(C.kind === 'chgAmt')    return '<td class="' + C.cls + ' calc" id="amt-' + i + '"></td>';
  if(C.kind === 'range')     return '<td class="' + C.cls + ' calc" id="rng-' + i + '"></td>';
  if(C.kind === 'amount')    return '<td class="' + C.cls + ' calc" id="vol-' + i + '"></td>';
  if(C.kind === 'pnl')       return '<td class="' + C.cls + '" id="pnl-' + i + '"></td>';
  if(C.kind === 'redDist')   return '<td class="' + C.cls + '" id="red-' + i + '"></td>';
  if(C.kind === 'nextState') return '<td class="' + C.cls + '" id="trg-' + i + '"></td>';
  if(C.kind === 'del')       return '<td class="' + C.cls + '"><button class="del" data-del="' + i + '" title="删除这一行">✕</button></td>';
  return '<td></td>';
}

function secWatch(){
  const isW = VIEW === 'watch';
  return '<section class="card" id="sec-watch">' +
    '<div class="card-head"><h2><i class="no">1</i>我的持仓监控</h2>' +
    '<div class="view-switch">' +
      '<button class="vs' + (isW ? ' on' : '') + '" data-view="watch">监控</button>' +
      '<button class="vs' + (isW ? '' : ' on') + '" data-view="disc">纪律线</button>' +
    '</div>' +
    '<div class="head-actions">' +
    '<button class="btn" id="addRow">+ 添加一行</button>' +
    '<button class="btn ghost" id="lookupAll">刷新现价</button>' +
    '<button class="btn ghost" id="expBtn">导出</button>' +
    '<button class="btn ghost" id="impBtn">导入</button>' +
    '<button class="btn ghost" id="resetBtn">恢复示例</button>' +
    '<input type="file" id="impFile" accept=".json" hidden></div></div>' +
    '<p class="lead">想盯什么就放这儿：<b>输入代码即加入监控</b>，名称、现价、涨跌、成交额自动带出，<b>每次打开看板自动刷新</b>。股票、场内 ETF 都行；场外基金取不到场内行情，会明确提示你手填。</p>' +
    '<div class="watch-sum" id="watchSum"></div>' +
    '<div class="table-wrap"><table class="hold"><thead id="hHead"></thead><tbody id="hRows"></tbody></table></div>' +
    '<p class="hint" id="watchHint"></p>' +
    '<p class="hint"><b>注意</b>：这一整块只存在你自己的浏览器里（本地存储），不会上传到任何服务器；看板更新、重新打开都不会被冲掉。</p></section>';
}

function paintWatchMeta(){
  const sum = document.getElementById('watchSum');
  if(sum){
    const n = ROWS.length, up = ROWS.filter(r => { const q = quoteOf(r); return q && q.chgPct > 0; }).length;
    const dn = ROWS.filter(r => { const q = quoteOf(r); return q && q.chgPct < 0; }).length;
    const fl = ROWS.filter(r => { const q = quoteOf(r); return q && q.chgPct === 0; }).length;
    const has = ROWS.filter(r => { const q = quoteOf(r); return !!q; }).length;
    let s = '<span>监控 <b>' + n + '</b> 个标的</span>';
    if(has) s += '<span>今天 <b class="up">' + up + '</b> 涨 / <b class="down">' + dn + '</b> 跌' + (fl ? ' / ' + fl + ' 平' : '') + '</span>';
    else s += '<span class="muted">填入代码后自动拉行情</span>';
    if(VIEW === 'disc'){
      const below = ROWS.filter(r => { const q = quoteOf(r), c = N(r.cost); return q && c > 0 && q.price < c; }).length;
      const hasCost = ROWS.filter(r => N(r.cost) > 0).length;
      if(hasCost) s += '<span>其中 <b class="down">' + below + '</b> 个低于你的成本价</span>';
    }
    sum.innerHTML = s;
  }
  const hint = document.getElementById('watchHint');
  if(hint){
    hint.innerHTML = (VIEW === 'watch')
      ? '<b>怎么加</b>：在「代码」列输入数字代码（如 <b>510300</b>、<b>600519</b>）即自动带出名称与行情，不用管 sh / sz 前缀；也可以直接输名字或拼音，比如「茅台」「hs300」。想给已买的标的设提醒，点上面的「<b>纪律线</b>」切换。'
      : '<b>这三条线要你自己定</b>：<b>成本价</b>＝你实际的买入均价；<b>纪律红线</b>＝跌到这里就停下来重新评估；<b>下一档触发价</b>＝你事先想好的加/减仓价位。填了才计算，留空不影响其他列。';
  }
}

/* ══════════════════════════════════════════════════════════════════
   5b · 第 3 段「今天发生了什么 · 按市场复盘」
   ──────────────────────────────────────────────────────────────────
   四个市场能取到的东西**厚度差很多**，所以复盘深度必须跟着数据走：
   拿不到的就不写，并在页面上说清「这里为什么没有」，而不是硬凑一段像模像样的。

     市场     当日分时        上下午拆分      家数/涨停  资金流  板块
     A 股     有              有（2 段）      有         有      有
     港股     有              有（早/午市）   —          —      —
     美股     有（无成交额）   不能拆          —          —      —
     全球     —               —              —          —      —

   ⚠️ 各市场的时间边界一律**写死在文案里**（北京 21:30 / 次日 04:00 这种），
      不在页面上再做一次时区换算——换算是这一页最容易出错的地方。
   ⚠️ 美股分时接口的成交额字段恒为 0（2026-10-16 实测），所以美股那一段**不写成交**。
   ⚠️ 港股分时只有恒生指数一条，「国企指数 / 恒生科技」给的是当日到此刻的涨跌。
   ══════════════════════════════════════════════════════════════════ */

/* 复盘视角的四个标签。与第 2 段的市场标签**彼此独立**：
   那边回答「此刻什么状态」，这边回答「这一天发生了什么」。 */
const RMKTS = [
  {k:'cn', label:'沪深京 · A 股'},
  {k:'hk', label:'港股'},
  {k:'us', label:'美股'},
  {k:'gl', label:'全球市场'}
];
const RKEY = 'mb_report_mkt';
let RPT_MKT = 'cn';
function loadReportMkt(){ try{ const v = localStorage.getItem(RKEY); if(RMKTS.some(x => x.k === v)) RPT_MKT = v; }catch(e){} }
function saveReportMkt(){ try{ localStorage.setItem(RKEY, RPT_MKT); }catch(e){} }
/** 标签后面那行小字。全球是跨时区的，写「跨时区」而不是某个市场的开闭市状态 */
function rptState(k){ return k === 'gl' ? '跨时区' : mktState(k).s; }

/** 指数卡网格 —— A股 4 张 / 港股 4 张 / 美股 4 张，共用一套版式（就是原来 A 股那张卡的样式） */
function idxCards(list, o){
  o = o || {};
  return '<div class="idx-grid">' + (list || []).map(it => {
    const tr = it.trend;
    const codeTxt = it.codeLabel != null ? it.codeLabel
      : ((it.market === 0 || it.market === 1) ? ((it.market ? 'sh' : 'sz') + (it.code || '')) : (it.code || ''));
    const amp = it.amp != null ? it.amp
      : ((it.high != null && it.low != null && it.prevClose) ? (it.high - it.low) / it.prevClose * 100 : null);
    const chgTxt = it.chgPct == null ? '—'
      : (ARROW(it.chgPct) + ' ' + Math.abs(it.chgPct).toFixed(2) + '%');
    const split = (o.split && tr) ? (
      '<div class="idx-split">' +
      '<div data-seg="am">' + esc(o.amLabel) + '<b class="' + cls(tr.amChgPct) + '">' + ARROW(tr.amChgPct) + ' ' + pct(tr.amChgPct) + '</b></div>' +
      '<div data-seg="pm">' + esc(o.pmLabel) + '<b class="' + cls(tr.pmChgPct) + '">' + ARROW(tr.pmChgPct) + ' ' + pct(tr.pmChgPct) + '</b></div>' +
      '</div>') : '';
    return '<div class="idx"' + (it.code ? ' data-idx="' + esc(it.code) + '"' : '') + '>' +
      '<div class="idx-name">' + esc(it.name) + (codeTxt ? '<span class="idx-code">' + esc(codeTxt) + '</span>' : '') + '</div>' +
      '<div class="idx-price ' + cls(it.chgPct) + '">' + num(it.price) + '</div>' +
      '<div class="idx-chg ' + cls(it.chgPct) + '">' + chgTxt +
        (it.chg == null ? '' : '<span class="idx-abs">(' + (it.chg >= 0 ? '+' : '') + num(it.chg) + ')</span>') + '</div>' +
      '<div class="idx-foot">今开 ' + num(it.open) + ' · 最高 ' + num(it.high) + ' · 最低 ' + num(it.low) +
        ' · 振幅 ' + num(amp, 2) + '%' + (it.amount != null ? ' · 成交 ' + money(it.amount) : '') + '</div>' +
      split + '</div>';
  }).join('') + '</div>';
}
/** 取某个市场的核心指数，补上代码小标与振幅（m.gidx 里本来没有这两个字段） */
function mktIdx(m, k){
  return ((m && m.gidx) || []).filter(x => x.k === k).map(x => Object.assign({}, x, {
    codeLabel: String(x.sec || '').split('.')[1] || '',
    amp: (x.high != null && x.low != null && x.prevClose) ? (x.high - x.low) / x.prevClose * 100 : null
  }));
}
/** 一个整宽复盘块：左边是这一段的关键数字，右边是文字 */
function segFull(mod, title, range, mainVal, unit, kv, reportHtml){
  return '<div class="seg-full' + (mod ? ' ' + mod : '') + '">' +
    '<div class="seg-side">' +
      '<div class="seg-title">' + title + '<span class="range">' + range + '</span></div>' +
      '<div class="seg-main ' + cls(mainVal) + '">' +
        (mainVal == null ? '—' : ARROW(mainVal) + ' ' + pct(mainVal)) + '</div>' +
      '<div class="seg-unit">' + unit + '</div>' +
      '<div class="seg-kv2">' + (kv || '') + '</div>' +
    '</div><div class="report">' + reportHtml + '</div></div>';
}
const kv2 = pairs => (pairs || []).map(x => '<span>' + x[0] + ' <b>' + x[1] + '</b></span>').join('');
/** 名单里涨了几只、跌了几只——**这不是全市场涨跌家数**，只是列举出来的那几只 */
function selBreadth(list, label){
  const l = (list || []).filter(x => x.chgPct != null);
  if(!l.length) return '';
  const up = l.filter(x => x.chgPct > 0).length, dn = l.filter(x => x.chgPct < 0).length;
  return '下面这 ' + l.length + ' 只' + label + '里，<b class="up">' + up + '</b> 只上涨、<b class="down">' + dn + '</b> 只下跌'
       + (l.length - up - dn ? '、' + (l.length - up - dn) + ' 只平盘' : '') + '。';
}

/* ── A 股：「午评 / 午后快评 / 收评」三段 ───────────────── */
function rptCn(m){
  const sh = m.indices.find(i => i.code === '000001') || m.indices[0];
  const tr = sh.trend;
  const amSess = SESSIONS[0], pmSess = SESSIONS[1];
  const cards = idxCards(m.indices, {
    split:true,
    amLabel:'上午 (09:30–11:30)',
    pmLabel:'午后 (13:00–15:00)'
  });

  const segAM = '<div class="seg-full seg-am"><div class="seg-side">' +
    '<div class="seg-title">午评<span class="range">' + amSess.b + ' – ' + amSess.e + '</span></div>' +
    '<div class="seg-main ' + cls(tr&&tr.amChgPct) + '" id="segAmMain">' + ARROW(tr&&tr.amChgPct) + ' ' + pct(tr&&tr.amChgPct) + '</div>' +
    '<div class="seg-unit">相对昨收 · ' + sh.name + '</div>' +
    '<div class="seg-kv2">' + (tr?('<span>半日最高 <b>' + num(tr.amHigh) + '</b></span><span>半日最低 <b>' + num(tr.amLow) + '</b></span><span>半日成交 <b>' + money(tr.amAmount) + '</b></span>'):'') + '</div>' +
    '</div><div class="report">' + reportAM(m, tr) + '</div></div>';

  const segPM = '<div class="seg-full seg-pm"><div class="seg-side">' +
    '<div class="seg-title">午后 · 盘中快评<span class="range">' + pmSess.b + ' – ' + pmSess.e + '</span></div>' +
    '<div class="seg-main ' + cls(tr&&tr.pmChgPct) + '" id="segPmMain">' + ARROW(tr&&tr.pmChgPct) + ' ' + pct(tr&&tr.pmChgPct) + '</div>' +
    '<div class="seg-unit">午后相对上午收盘的变化 · 盘中</div>' +
    '<div class="seg-kv2">' + (tr?('<span>下午最高 <b>' + num(tr.pmHigh) + '</b></span><span>下午最低 <b>' + num(tr.pmLow) + '</b></span><span>下午成交 <b>' + money(tr.pmAmount) + '</b></span>'):'') + '</div>' +
    '</div><div class="report">' + reportPM(m, tr) + '</div></div>';

  const segAll = '<div class="seg-full seg-all"><div class="seg-side">' +
    '<div class="seg-title">收评<span class="range">09:30 – 15:00</span></div>' +
    '<div class="seg-main ' + cls(sh.chgPct) + '" id="segAllMain">' + ARROW(sh.chgPct) + ' ' + pct(sh.chgPct) + '</div>' +
    '<div class="seg-unit">相对昨收 · ' + sh.name + '</div>' +
    '<div class="seg-kv2"><span>两市成交额 <b>' + money(m.turnover.amount) + '</b></span>' +
      '<span>振幅 <b>' + num(sh.amp,2) + '%</b></span>' +
      (m.ladder && m.ladder.max ? '<span>最高连板 <b>' + m.ladder.max + ' 板</b></span>' : '') + '</div>' +
    '</div><div class="report">' + reportAll(m, tr) + '</div></div>';

  /* ── 焦点复盘：收盘后把当天最值得记住的事挑出来。
     两块：① 数据焦点（连板梯队 / 领涨题材 / 资金主攻行业，全用已有数据）；
           ② 盘后要闻（东财 A 股复盘栏目的公司公告与新闻导读）。
     ⚠️ 做成**独立 subhead 块**，绝不塞进 .seg-all——selftest 对 .seg-all 的
        标签全串有逐字断言（收盘/量能/广度/板块/题材/资金/梯队/一句话）。 */
  const focusL = [];
  const lad = m.ladder, bd = m.board || {conUp: []}, rk = m.rank || {sectorIn: []};
  if(lad && lad.max) focusL.push(rp('梯队', '全天涨停 <b>' + lad.n + '</b> 家，最高 <b>' + lad.max + ' 板</b>'
    + (lad.hot && lad.hot.length ? '；涨停个股集中在 <b>' + lad.hot.slice(0, 3).map(x => x.name + '（' + x.n + ' 家）').join('、') + '</b>' : '') + '。'));
  if(bd.conUp && bd.conUp.length) focusL.push(rp('题材', '概念板块里 <b>' + esc(bd.conUp[0].name) + '</b> 涨幅居前（<b class="up">' + pct(bd.conUp[0].chgPct) + '</b>）'
    + (bd.conUp[0].lead ? '，领涨股 ' + esc(bd.conUp[0].lead) : '') + '。'));
  if(rk.sectorIn && rk.sectorIn.length) focusL.push(rp('资金', '主力净流入最多的行业是 <b>' + esc(rk.sectorIn[0].name) + '</b>'
    + '（净流入 <b class="up">' + money(rk.sectorIn[0].main) + '</b>）'
    + (m.fund && m.fund.day ? '——注意它是全天净额的一部分，和上面收评里的资金流向是同一个算法' : '') + '。'));
  const revL = (m.newsARev || []).slice(0, 4);
  /* ⚠️ 要闻不参与页面里的实时拉取：一天只出一份，拿快照里那份就够。
     取不到也必须占一行说清楚，让读者知道「这里本来有东西」，而不是以为这一块不存在。 */
  focusL.push(rp('要闻', revL.length
    ? revL.map(x => esc(x.title) + '<span class="note">（' + esc(x.src) + '）</span>').join('<br>') + newsStamp()
    : '这次没取到' + newsStamp() + '<span class="note">要闻随收盘后的复盘一起生成，晚一点再打开看看。</span>'));
  const focusHtml = focusL.length
    ? '<div class="subhead">焦点复盘<span class="tip">收盘后把当天最值得记住的事挑出来</span></div>' +
      '<div class="report">' + focusL.join('') + '</div>'
    : '';

  const intraday = tr ? lineChart(tr.pts.map(p => ({v:p.close})), {
    id:'intra', gapIndex:tr.am.length-0.5,
    foot:'<span>09:30 开盘</span><span>11:30 / 13:00 午间休市</span><span>15:00 收盘</span>',
    tip:(i, v) => {
      const p = tr.pts[i] || {};
      const c = tr.preClose ? (v - tr.preClose) / tr.preClose * 100 : null;
      return '<div class="ct-t">' + (p.time || '') + '</div>' +
        '<div class="ct-r"><span class="ct-k">' + esc(sh.name) + '</span><span class="ct-v">' + num(v) + '</span></div>' +
        '<hr><div class="ct-r"><span class="ct-k">相对昨收</span><span class="ct-v ' + cls(c) + '">' +
          ARROW(c) + ' ' + pct(c) + '</span></div>' +
        '<div class="ct-r"><span class="ct-k">当分钟成交</span><span class="ct-v">' + money(p.amount == null ? null : p.amount/1e8) + '</span></div>';
    }})
    : '';

  return '<p class="lead">下面按「上午半场 → 午后半场 → 全天」的顺序，把今天讲一遍。每一段都只讲已经发生的事。</p>' +
    cards +
    '<div class="subhead" style="margin-top:20px">午评<span class="tip">11:30 半场小结</span></div>' + segAM +
    '<div class="subhead">午后 · 盘中快评<span class="tip">午后的量能与热点 · 对应 14:00 快评</span></div>' + segPM +
    '<div class="subhead">' + (dayWord() === '全天' ? '收评' : '盘中 · 到目前为止') + '<span class="tip">收盘后才是完整的 09:30–15:00</span></div>' + segAll +
    focusHtml +
    '<div class="subhead">' + sh.name + ' · 当日分时<span class="tip">鼠标移上去看具体时刻的数值 · 竖虚线为午间休市分界</span></div>' +
    intraday +
    '<p class="hint"><b>怎么看</b>：这三段讲的都是<b>已经发生的事</b>，不是接下来会怎么走的线索。分段成交额以上证指数（<b>只算沪市</b>）为例，和「两市成交额」不是一个口径，别直接相加。</p>';
}

/* ── 港股：早市 09:30–12:00 / 午市 13:00–16:00（午休 12:00–13:00） ── */
function rptHkAM(m, tr){
  const s = tr.amShape, L = [];
  L.push(rp('午评', '恒生指数从 ' + num(tr.pts[0].close) + ' 开盘'
    + (s ? '（' + openWord(s.openPct) + ' ' + Math.abs(s.openPct).toFixed(2) + '%）' : '')
    + '，半日收在 <b class="' + cls(tr.amChgPct) + '">' + num(tr.amClose) + '（' + pct(tr.amChgPct) + '）</b>，基准是前一交易日收盘。'));
  if(s) L.push(rp('走势', '上午走成<b>' + s.text + '</b>的形态：最高 ' + num(s.hi) + '（' + s.hiTime + '）、最低 '
    + num(s.lo) + '（' + s.loTime + '），半日振幅 ' + s.range.toFixed(2) + '%。'
    + (tr.open30Pct == null ? '' : (Math.abs(tr.open30Pct) < 0.05 ? '开盘头半小时基本走平。'
        : '开盘头半小时' + word(tr.open30Pct) + ' ' + Math.abs(tr.open30Pct).toFixed(2) + '%。'))));
  L.push(rp('成交', '半日成交 <b>' + money(tr.amAmount) + '</b>'
    + '<span class="note">（把恒生指数分时里每分钟的成交额加起来，是数据源给出的<b>只统计指数成分股</b>，不等于港股全市场成交额——所以它和 A 股那段里的「两市成交额」不能直接比）</span>'));
  return L.join('');
}
function rptHkPM(m, tr){
  const d = tr.pmChgPct, s = tr.pmShape, L = [];
  /* 同样按「早市方向 × 午后方向」分四种：早市在跌就不能说「把上午涨的还回去」 */
  const amD = (tr.amChgPct == null) ? null : tr.amChgPct;
  const pmWord = (d == null) ? ''
    : (d > 0 ? (amD == null ? '，往上做' : (amD > 0 ? '，接着往上做' : '，把早市的跌幅收回来一部分'))
    : (d < 0 ? (amD == null ? '，往下走' : (amD > 0 ? '，把早市涨的一部分还了回去' : '，在早市下跌的基础上继续走低'))
    : '，基本横住'));
  L.push(rp('午后', '午后段相对早市收盘 <b class="' + cls(d) + '">' + pct(d) + '</b>' + pmWord
    + '；全天最终收在 <b class="' + cls(tr.allChgPct) + '">' + num(tr.allClose) + '（' + pct(tr.allChgPct) + '）</b>。'));
  let v = '午后成交 <b>' + money(tr.pmAmount) + '</b>';
  if(tr.amAmount){
    const r = (tr.pmAmount - tr.amAmount) / tr.amAmount * 100;
    v += '，比早市的 ' + money(tr.amAmount) + (Math.abs(r) < 6 ? '基本持平' : (r > 0 ? '放大 ' + r.toFixed(0) + '%' : '缩小 ' + Math.abs(r).toFixed(0) + '%')) + '。';
  }
  L.push(rp('成交', v));
  if(s) L.push(rp('走势', '午后走成<b>' + s.text + '</b>的形态：最高 ' + num(s.hi) + '（' + s.hiTime + '）、最低 '
    + num(s.lo) + '（' + s.loTime + '）。'
    + (tr.pmTailPct == null ? '' : (Math.abs(tr.pmTailPct) < 0.15 ? '尾盘最后半小时基本走平。'
        : '尾盘最后半小时' + (tr.pmTailPct > 0 ? '回升 ' : '继续走低 ') + Math.abs(tr.pmTailPct).toFixed(2) + '%。'))));
  return L.join('');
}
function rptHkAll(m, tr){
  const list = mktIdx(m, 'hk'), s = tr.allShape, L = [];
  const ups = list.filter(x => x.chgPct > 0).length;
  L.push(rp('收盘', dayWord() + '这几个港股指数<b>'
    + (ups === list.length ? '全线上涨' : (ups === 0 ? '全线下跌' : '涨跌互现')) + '</b>：'
    + list.map(x => esc(x.short || x.name) + ' <b class="' + cls(x.chgPct) + '">' + pct(x.chgPct) + '</b>（' + num(x.price) + '）').join('、')
    + '。' + (s ? '全天走成<b>' + s.text + '</b>的形态。' : '')));
  L.push(rp('标的', selBreadth(m.ghk, '常见港股标的')
    + '<span class="note">（这里只有名单里这十几只，<b>不是</b>港股全市场的涨跌家数——港股的全市场家数、资金流、板块这几类数据，公开接口给不了，所以这一段到此为止）</span>'));
  L.push(rp('一句话', '以上都是已经发生的事，不构成对后市的判断。'));
  return L.join('');
}
function rptHk(m){
  const tr = m.gtrend && m.gtrend.hk;
  const list = mktIdx(m, 'hk');
  const cards = idxCards(list, {});
  const head = '<p class="lead">港股和 A 股一样分上下两场，只是时间不同：<b>早市 09:30–12:00</b>、<b>午市 13:00–16:00</b>'
    + '（都是北京时间），中午休 <b>12:00–13:00</b>。行内的习惯叫法是：12:00 早市收盘这一档是<b>午评</b>、16:00 全天收盘是<b>收评</b>，中间同样有午后盘中观察。'
    + '下面这条分时是<b>恒生指数</b>的；其余三个指数只有现在的涨跌、没有走势线。'
    + '要提前说清楚：港股这边<b>没有</b>像 A 股那样的涨跌家数、涨停跌停和主力资金口径，所以这一段只讲<b>指数走势</b>。</p>';
  if(!tr) return head + cards
    + '<p class="hint">港股分时这次没取到，上面的指数点位与涨跌仍然是当日快照。</p>'
    + '<div class="subhead">港股 · 常见标的<span class="tip">精选名单，不是涨幅榜</span></div>' + gRows(m.ghk);
  const segs =
    segFull('seg-am', '午评', '09:30 – 12:00', tr.amChgPct, '相对昨收 · 恒生指数',
      kv2([['半日最高', num(tr.amHigh)], ['半日最低', num(tr.amLow)], ['半日成交', money(tr.amAmount)]]), rptHkAM(m, tr)) +
    segFull('seg-pm', '午后 · 盘中', '13:00 – 16:00', tr.pmChgPct, '相对早市收盘的变化 · 恒生指数',
      kv2([['午后最高', num(tr.pmHigh)], ['午后最低', num(tr.pmLow)], ['午后成交', money(tr.pmAmount)]]), rptHkPM(m, tr)) +
    segFull('seg-all', '收评', '09:30 – 16:00', tr.allChgPct, '相对昨收 · 恒生指数',
      kv2([['全天成交', money(tr.allAmount)],
           ['全天最高', num(tr.allShape && tr.allShape.hi)],
           ['全天最低', num(tr.allShape && tr.allShape.lo)]]), rptHkAll(m, tr));
  const chart = lineChart(tr.pts.map(p => ({v:p.close})), {
    w:900, h:190, gapIndex:tr.am.length-0.5,
    foot:'<span>09:30 开盘</span><span>12:00 / 13:00 午间休市</span><span>16:00 收盘</span>',
    tip:(i, v) => {
      const p = tr.pts[i] || {};
      const c = tr.preClose ? (v - tr.preClose) / tr.preClose * 100 : null;
      return '<div class="ct-t">' + (p.time || '') + '</div>' +
        '<div class="ct-r"><span class="ct-k">恒生指数</span><span class="ct-v">' + num(v) + '</span></div>' +
        '<hr><div class="ct-r"><span class="ct-k">相对昨收</span><span class="ct-v ' + cls(c) + '">'
          + ARROW(c) + ' ' + pct(c) + '</span></div>';
    }});
  return head + cards + segs +
    '<div class="subhead">恒生指数 · 当日分时<span class="tip">鼠标移上去看具体时刻的数值 · 竖虚线为午间休市分界</span></div>' + chart +
    '<div class="subhead">港股 · 常见标的<span class="tip">精选名单，不是涨幅榜</span></div>' + gRows(m.ghk) +
    '<p class="hint">这一段看的是<b>十几家常见港股</b>，不是涨幅榜——前排几乎全是<b>牛熊证与窝轮</b>这类带杠杆的衍生品，' +
    '能一天翻倍也能一天归零，对刚接触的人没有参考价值。所以这里只放大家听过的公司：它们是<b>名单</b>，不是「港股涨得最好的 12 只」。</p>' +
    '<p class="hint"><b>颜色提醒</b>：中国香港本地习惯是「绿涨红跌」，本页统一按<b>中国内地习惯红涨绿跌</b>。</p>';
}

/* ── 美股：连续交易、不设午休、跨零点（北京 21:30 → 次日 04:00） ── */
function rptUs(m){
  const tr = m.gtrend && m.gtrend.us;
  const list = mktIdx(m, 'us');
  const cards = idxCards(list, {});
  const st = m.gus || [];
  const dst = usDST(new Date());
  const tc = dst ? '21:30 – 次日 04:00' : '22:30 – 次日 05:00';
  const head = '<p class="lead">美股和 A 股、港股都不一样：<b>连续交易、中途不休息</b>，而且是<b>跨零点</b>的一整段——'
    + '美国现在' + (dst ? '是夏令时' : '是冬令时') + '，对应北京时间 <b>' + tc + '</b>。所以美股没有「上午 / 午后」这种两段分法，'
    + '下面把它当成<b>一整段</b>来看。另外要留意：这个时段里你在睡觉，白天看到的数字讲的是<b>刚刚过去的那一夜</b>。</p>';
  const listBlock = '<div class="subhead">美股 · 常见标的<span class="tip">精选名单，不是涨幅榜</span></div>' + gRows(st)
    + '<p class="hint">' + selBreadth(st, '常见美股标的')
    + '<b>这不是全市场涨跌家数</b>——只是名单里这十几只。美股也没有涨跌停，靠熔断机制限制异常波动，'
    + '个股单日波动会比 A 股大得多。</p>';
  if(!tr) return head + cards
    + '<p class="hint">美股分时这次没取到，上面的指数点位与涨跌仍然是当日快照。</p>' + listBlock;
  const s = tr.shape;
  const L = [];
  L.push(rp('这一夜', esc(GMAIN.us) + '从 ' + num(tr.open) + ' 开始，' + tr.firstTime + ' 到 ' + tr.lastTime
    + '（北京时间）走成<b>' + (s ? s.text : '—') + '</b>的形态：'
    + '区间内最高 ' + num(tr.hi) + '（' + tr.hiTime + '）、最低 ' + num(tr.lo) + '（' + tr.loTime + '）'
    + (s ? '，振幅 ' + s.range.toFixed(2) + '%' : '') + '。'));
  L.push(rp('点位', (tr.lastTime === '04:00' || tr.lastTime === '05:00' ? '收在 ' : '最新 ') + num(tr.close)
    + '，相对前一交易日收盘 <b class="' + cls(tr.chgPct) + '">' + pct(tr.chgPct) + '</b>'
    + (s ? '，' + openWord(s.openPct) + '开局。' : '。')));
  L.push(rp('成交', '<b>这一段没有成交额可写</b><span class="note">——美股指数查不到这个数，'
    + '所以这里直接不写，免得把一个 0 当成「今天没人交易」。想看美股的量，只能去具体的行情软件查个股成交量。</span>'));
  L.push(rp('四个指数', '道指、纳指、纳指100、标普500 今天分别是 '
    + list.map(x => '<b class="' + cls(x.chgPct) + '">' + pct(x.chgPct) + '</b>').join('、')
    + '——注意它们<b>不一定同向</b>：指数之间的差异，往往说明领涨的是大盘蓝筹还是科技股。'));
  L.push(rp('一句话', '以上都是已经发生的事，不构成对后市的判断。'));

  return head + cards +
    segFull('seg-all', GMAIN.us + ' · 这一夜', tc, tr.chgPct, '相对前一交易日收盘 · ' + GMAIN.us,
      kv2([['收在', num(tr.close)], ['区间最高', num(tr.hi)], ['区间最低', num(tr.lo)],
           ['这一段', tr.firstTime + ' → ' + tr.lastTime]]), L.join('')) +
    listBlock;
}

/* ── 全球：只有「60 个指数的当日涨跌」，没有分时、没有成交额、没有家数 ── */
function rptGl(m){
  const all = (m.gworld || []).filter(x => x.name && x.chgPct != null);
  const eq = all.filter(x => x.region !== R_CM);
  const cm = all.filter(x => x.region === R_CM);
  const avg = a => a.length ? a.reduce((s, x) => s + x.chgPct, 0) / a.length : null;
  const up = eq.filter(x => x.chgPct > 0).length, dn = eq.filter(x => x.chgPct < 0).length;
  const fl = eq.length - up - dn;
  const sorted = eq.slice().sort((a, b) => b.chgPct - a.chgPct);
  const regions = [R_AP, R_EU, R_AM].map(r => {
    const l = eq.filter(x => x.region === r);
    return {r:r, n:l.length, avg:avg(l), up:l.filter(x => x.chgPct > 0).length, dn:l.filter(x => x.chgPct < 0).length};
  }).filter(g => g.n);
  const byAvg = regions.slice().sort((a, b) => b.avg - a.avg);
  const L = [];
  L.push(rp('分布', '这里收录的 <b>' + eq.length + '</b> 个全球股票指数里，涨 <b class="up">' + up + '</b> 个、跌 <b class="down">'
    + dn + '</b> 个' + (fl ? '、平 ' + fl + ' 个' : '') + '，上涨占 <b>'
    + (eq.length ? (up / eq.length * 100).toFixed(0) : '—') + '%</b>；全部指数的平均涨跌是 <b class="' + cls(avg(eq)) + '">'
    + pct(avg(eq)) + '</b>。这是<b>横向</b>的对比——同一时刻谁强谁弱。'));
  if(regions.length) L.push(rp('分区', regions.map(g => '<b>' + g.r + '</b> ' + g.n + ' 个平均 <b class="' + cls(g.avg) + '">'
    + pct(g.avg) + '</b>（' + g.up + ' 涨 / ' + g.dn + ' 跌）').join('；') + '。'
    + (byAvg.length > 1 && (byAvg[0].avg - byAvg[byAvg.length - 1].avg) > 0.3
        ? '今天最靠前的是<b>' + byAvg[0].r + '</b>，最弱的是<b>' + byAvg[byAvg.length - 1].r + '</b>。'
        : '今天几个区域的表现差得不多。')));
  L.push(rp('口径', '<b>这些数字不是同一时刻的</b><span class="note">——同一个瞬间，亚太多半已经收盘、欧洲可能刚收、'
    + '美洲还在盘中，所以这里是「各地<b>各自最新一笔</b>报价」的横向排列，不是一张全球统一时点的大合照。'
    + '看的时候只需要知道「此刻那边大体是涨是跌」，不要把它们当成精确到分钟的同步对比。</span>'));
  L.push(rp('商品汇率', cm.length
    ? cm.map(x => esc(x.name) + ' <b class="' + cls(x.chgPct) + '">' + pct(x.chgPct) + '</b>（' + num(x.price, 2) + '）').join('、')
      + '<span class="note">（美元指数、商品、航运运价这类和股票不是一回事，单独看它们的涨跌，别和股指混着比）</span>'
    : '这次没取到商品与汇率数据。'));

  /* 环球与隔夜要闻：盘面数字之外的隔夜消息（东财环球栏目导读）。
     与 A 股那一段同一处理：要闻只走快照，取不到也要占一行说清楚，不留空白。 */
  const glNews = (m.newsGlRev || []).slice(0, 4);
  const glNewsHtml = '<div class="subhead" style="margin-top:16px">环球与隔夜要闻<span class="tip">盘面数字之外的隔夜消息 · 东财环球栏目</span></div>'
    + (glNews.length
        ? rp('导读', glNews.map(x =>
            esc(x.title) + '<span class="note">（' + esc(x.src) + '）</span>').join('<br>') + newsStamp())
        : rp('导读', '这次没取到' + newsStamp() + '<span class="note">晚一点再打开看看。</span>'));

  return L.join('') +
    '<div class="watch-sum" style="margin:14px 0 4px">'
    + '<span>收录 <b>' + eq.length + '</b> 个股票指数</span>'
    + '<span>涨 <b class="up">' + up + '</b> / 跌 <b class="down">' + dn + '</b>' + (fl ? ' / 平 ' + fl : '') + '</span>'
    + (cm.length ? '<span>商品与汇率 <b>' + cm.length + '</b> 个</span>' : '')
    + '</div>'
    + '<div class="two-col" style="margin-top:12px">'
      + '<div><div class="subhead">涨幅前 8<span class="tip">按当日涨跌幅</span></div>' + gRows(sorted.slice(0, 8)) + '</div>'
      + '<div><div class="subhead">跌幅前 8<span class="tip">按当日涨跌幅</span></div>' + gRows(sorted.slice(-8).reverse()) + '</div>'
    + '</div>'
    + (cm.length ? '<div class="subhead">商品与汇率<span class="tip">不参与上面的涨跌统计</span></div>' + gRows(cm) : '')
    + glNewsHtml +
    '<p class="hint">这一段只看<b>指数点位与涨跌</b>，没有走势图：全球不是一个市场，是十几个时区拼起来的——北京时间白天亚太在交易、'
    + '下午轮到欧洲、夜里到凌晨才是美洲。<b>同一时刻，有的市场已经收盘、有的还没开盘</b>，所以不存在一条「全球统一的分时」。'
    + '能看的是<b>横向对比</b>（此刻谁强谁弱）；想看纵向走势，切到上面的「港股 / 美股」标签看单个市场，'
    + '或者看本页第 2 段里对应市场的当日分时。</p>';
}

/* ── 第 3 段本体 ─────────────────────────────────────────────── */
function rTabsHtml(){
  return RMKTS.map(x => '<button class="mt' + (x.k === RPT_MKT ? ' on' : '') + '" data-rpt="' + x.k + '">'
    + x.label + '<em>' + rptState(x.k) + '</em></button>').join('');
}
function reportBody(m){
  if(RPT_MKT === 'hk') return rptHk(m);
  if(RPT_MKT === 'us') return rptUs(m);
  if(RPT_MKT === 'gl') return rptGl(m);
  return rptCn(m);
}
function sec2Indices(m){
  const d = m.meta && m.meta.tradeDate;
  return '<section class="card" id="sec-market">' +
    '<div class="card-head"><h2><i class="no">6</i>今天发生了什么 · 按市场复盘</h2>' +
    '<span class="stamp">行情日期 ' + (d ? md(d.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')) : '—') + '</span></div>' +
    '<p class="lead">同一根日线背后，不同市场的「一天」长得完全不一样：A 股和中国香港分上下两场，' +
    '美股是<b>跨零点的一条连续交易</b>，而「全球」是十几个时区拼起来的、<b>根本没有统一的一天</b>。' +
    '所以这里<b>按市场分别复盘</b>——四个标签，点哪个看哪个，讲的全是那个市场当天<b>实际发生的事</b>；' +
    '哪个市场缺哪类数据，那一段会直接说清楚「为什么没有」，不硬凑。</p>' +
    '<div class="mkt-tabs" id="rTabs">' + rTabsHtml() + '</div>' +
    '<div id="rptBody">' + reportBody(m) + '</div>' +
  '</section>';
}
/* 切复盘市场：只重绘这一段，不动整页（保住持仓表 / 台账里正在填的内容） */
function repaintReport(){
  const m = window.__MODEL; if(!m) return;
  setHtml('rptBody', reportBody(m));
  document.querySelectorAll('#rTabs .mt').forEach(b => {
    b.classList.toggle('on', b.dataset.rpt === RPT_MKT);
    const em = b.querySelector('em');
    if(em) em.textContent = rptState(b.dataset.rpt);
  });
  bindSegmentJump();
}
function bindReport(){
  const box = document.getElementById('rTabs');
  if(box && !box._b){
    box._b = 1;
    box.onclick = e => {
      const b = e.target && e.target.closest ? e.target.closest('.mt') : null;
      if(!b) return;
      const k = b.dataset.rpt;
      if(!k || k === RPT_MKT) return;
      RPT_MKT = k; saveReportMkt(); repaintReport();
    };
  }
  bindSegmentJump();
}
function sec3Fund(m){
  const fd = m.fund.day, fm = m.fund.min;
  const scope = (m.fund && m.fund.scope) || '沪深两市';
  const hero = [];
  if(fd){
    hero.push('<div class="fcard"><div class="k">主力资金 · 全天净' + (fd.main>=0?'流入':'流出') + '</div>' +
      '<div class="v ' + cls(fd.main) + '" id="fundMain">' + money(Math.abs(fd.main)) + '</div>' +
      '<div class="n">' + scope + ' · 大单 + 超大单 · ' + fd.date + '</div></div>');
  }
  if(fm){
    hero.push('<div class="fcard"><div class="k">其中 · 上午</div>' +
      '<div class="v ' + cls(fm.amEnd) + '" id="fundAm">' + (fm.amEnd>=0?'+':'−') + money(Math.abs(fm.amEnd)) + '</div>' +
      '<div class="n">' + scope + ' 09:31 – 11:30 累计净额</div></div>');
    hero.push('<div class="fcard"><div class="k">其中 · 午后</div>' +
      '<div class="v ' + cls(fm.pmDelta) + '" id="fundPm">' + (fm.pmDelta>=0?'+':'−') + money(Math.abs(fm.pmDelta)) + '</div>' +
      '<div class="n">' + scope + ' 13:01 – 15:00 段内净额</div></div>');
  }

  /* ⚠️ 下面这段是**必须**的：不解释，「成交上万亿、主力才净流出几十亿」看着就像算错了
     （典型疑问：「上午/下午各一百多亿、全天怎么只剩 5.6 亿」）。 */
  const caliber = '<p class="hint"><b>这组数怎么读</b>：' +
    '① 它是<b>净额</b>，不是<b>成交额</b>——' +
    (m.turnover && m.turnover.amount ? '今天两市成交额<b>达到 ' + money(m.turnover.amount) + '</b>，' : '') +
    '但每一笔成交都同时算一买一卖，全天相抵之后剩下的净额本来就只有<b>几十亿到几百亿</b>的量级；' +
    '所以「万亿级成交 ＋ 净流出几十上百亿」并不矛盾，两件事说的不是一回事。' +
    (fm ? '② <b>上午 ＋ 下午 ＝ 全天</b>——上面三张卡是一段关系，不是三个并列的结论。' : '') +
    '③ 口径是 <b>' + scope + '</b>' +
    (scope === '沪深两市'
      ? '，和上面的「两市成交额」「沪深两市涨跌家数」对齐。'
      : '（这次只取到' + scope + '、没取到另一市）——别的平台把沪深加起来的那种「大盘资金流」会比这里大一截，别直接对比。') +
    '</p>';

  // 资金结构四档
  let struct = '';
  if(fd){
    const rows = [['超大单', fd.superBig], ['大单', fd.big], ['中单', fd.mid], ['小单', fd.small]];
    const mx = Math.max.apply(null, rows.map(r=>Math.abs(r[1])||0)) || 1;
    struct = '<div class="struct">' + rows.map(r => {
      const v = r[1], wpct = Math.max(2, Math.abs(v)/mx*50);
      return '<div class="st-row" data-st="' + r[0] + '"><span class="st-k">' + r[0] + '</span>' +
        '<span class="st-track"><span class="mid"></span><i class="' + (v>=0?'up':'down') + '" style="width:' + wpct.toFixed(1) + '%"></i></span>' +
        '<span class="st-v ' + cls(v) + '">' + (v>=0?'+':'−') + money(Math.abs(v)) + '</span></div>';
    }).join('') + '</div>' +
    '<p class="hint"><b>怎么读</b>：主力（大单+超大单）净流出、而小单净流入，通常说明<b>大资金在减、散户在接</b>；反过来则是大资金在吸。这只是资金结构的一层事实，不是买卖信号。</p>';
  }

  const flowChart = fm ? lineChart(fm.kl.map(p => ({v:p.main/1e8})), {
    id:'flow', zero:true, color:'#1F4E78', gapIndex:fm.am.length-0.5,
    foot:'<span>09:30 开盘</span><span>11:30 / 13:00 午间休市</span><span>15:00 收盘</span>',
    tip:(i, v) => {
      const p = fm.kl[i] || {};
      const seg = (p.time || '') <= '11:30' ? '上午' : '午后';
      return '<div class="ct-t">' + (p.time || '') + ' · ' + seg + '</div>' +
        '<div class="ct-r"><span class="ct-k">主力累计净额</span><span class="ct-v ' + cls(v) + '">' +
          (v >= 0 ? '+' : '−') + money(Math.abs(v)) + '</span></div>' +
        '<hr>' +
        '<div class="ct-r"><span class="ct-k">超大单</span><span class="ct-v">' + (p.superBig>=0?'+':'−') + money(Math.abs(p.superBig/1e8)) + '</span></div>' +
        '<div class="ct-r"><span class="ct-k">大单</span><span class="ct-v">' + (p.big>=0?'+':'−') + money(Math.abs(p.big/1e8)) + '</span></div>' +
        '<div class="ct-r"><span class="ct-k">小单</span><span class="ct-v">' + (p.small>=0?'+':'−') + money(Math.abs(p.small/1e8)) + '</span></div>';
    }}) : '';

  const rankBox = (title, list, tip) => {
    if(!list || !list.length) return '<div><div class="subhead">' + title + '</div><div class="muted">暂无数据</div></div>';
    return '<div><div class="subhead">' + title + '<span class="tip">' + tip + '</span></div><ul class="rank-list">' +
      list.map((x,i) => '<li><span class="rk">' + (i+1) + '</span>' +
        '<span class="rk-nm">' + esc(x.name) + (x.ind?'<span class="code-cell">' + esc(x.ind) + '</span>':'') + '</span>' +
        '<span class="rk-pct ' + cls(x.chgPct) + '">' + ARROW(x.chgPct) + ' ' + Math.abs(x.chgPct||0).toFixed(2) + '%</span>' +
        '<span class="rk-amt ' + cls(x.main) + '">' + (x.main>=0?'+':'−') + money(Math.abs(x.main)) + '</span></li>').join('') +
      '</ul></div>';
  };

  const r = m.rank || {sectorIn:[],sectorOut:[],stockIn:[]};
  const mg = m.margin;

  return '<section class="card" id="sec-fund">' +
    '<div class="card-head"><h2><i class="no">5</i>资金流向</h2>' +
    '<span class="stamp">' + (fd ? '资金数据 ' + fd.date : '') + (mg ? ' · 两融 ' + mg.date : '') + '</span></div>' +
    (LIVE_GAPS.fund ? staleLine('资金流向') : '') +
    '<p class="lead">看钱往哪儿走。<b>主力</b>＝大单＋超大单；把全天拆成上午、下午两段，才知道资金是在<b>哪半场</b>撤的、又是哪半场回的。</p>' +
    '<div class="fund-hero">' + hero.join('') + '</div>' +
    caliber +
    (struct ? '<div class="subhead">资金结构 · 谁在买、谁在卖</div>' + struct : '') +
    '<div class="subhead">主力资金分时累计<span class="tip">零轴以上为净流入 · 鼠标移上去看具体时刻</span></div>' +
    flowChart +
    '<div class="two-col" style="margin-top:16px">' +
      rankBox('行业板块 · 主力净流入前 8', r.sectorIn, '按净额排序') +
      rankBox('行业板块 · 主力净流出前 8', r.sectorOut, '') +
    '</div>' +
    '<div class="two-col" style="margin-top:4px">' +
      rankBox('个股 · 主力净流入前 8', r.stockIn, '') +
      (mg ? '<div><div class="subhead">融资融券<span class="tip">次日发布，天然滞后一天</span></div>' +
        '<table class="grid"><thead><tr><th>项目</th><th class="num">金额</th><th class="num">较前一日</th></tr></thead><tbody>' +
        '<tr><td class="name-cell">融资余额</td><td class="num">' + money(mg.rzye) + '</td>' +
          '<td class="num ' + cls(mg.chgPct) + '">' + (mg.chgPct==null?'—':pct(mg.chgPct)) + '</td></tr>' +
        '<tr><td class="name-cell">当日融资买入</td><td class="num">' + money(mg.rzmre) + '</td><td class="num muted">—</td></tr>' +
        '<tr><td class="name-cell">当日融资偿还</td><td class="num">' + money(mg.rzche) + '</td><td class="num muted">—</td></tr>' +
        '<tr><td class="name-cell">融资净买入</td><td class="num ' + cls(mg.net) + '">' + (mg.net>=0?'+':'−') + money(Math.abs(mg.net)) + '</td>' +
          '<td class="num muted">' + (mg.net>=0?'加杠杆':'去杠杆') + '</td></tr>' +
        '<tr><td class="name-cell">融券余额</td><td class="num">' + money(mg.rqye) + '</td><td class="num muted">—</td></tr>' +
        '</tbody></table></div>' : '') +
    '</div>' +
    '<p class="hint"><b>注意</b>：<b>北向资金</b>是分两步停的——<b>2024 年 5 月 13 日</b>起不再盘中实时披露，<b>同年 8 月 19 日</b>起连收盘后的<b>当日净买入额</b>也取消了，只公布成交总额。所以这里没有「北向净流入」这一项，不是漏了，是公开数据里确实没有了；<b>别的 App 如果还在跳这个数，那是它自己估的</b>，不是交易所数据。两融数据由交易所次日发布，永远滞后一天。</p>' +
    '</section>';
}

function secSenti(m){
  const b = m.breadth, t = m.turnover;
  /* 三段占比，分母都是总家数。
     ⚠️ 旧写法把「下跌」算成 100 − 上涨，等于把平盘塞进了下跌：
     2067/5293 = 39.1%，3065/5293 = 57.9%，但页面写着「下跌 60.9%」——
     那个数其实是 (3065+161)/5293。三段必须各自算，加起来才等于 100%。 */
  const upW   = (b.up   != null && b.total) ? b.up  /b.total*100 : 50;
  const flatW = (b.flat != null && b.total) ? b.flat/b.total*100 : 0;
  const downW = (b.down != null && b.total) ? b.down/b.total*100 : Math.max(0, 100-upW-flatW);
  const card = (k,v,extra,cl) => '<div class="fcard"><div class="k">' + k + '</div><div class="v ' + (cl||'') + '">' + v + '</div>' +
    (extra?'<div class="n">' + extra + '</div>':'') + '</div>';
  /* 封板率 = 涨停 /（涨停 + 炸板）：资金愿不愿意把板封住 */
  const seal = (b.limitUp != null && (b.limitUp + (b.bomb||0)) > 0)
    ? b.limitUp / (b.limitUp + (b.bomb||0)) * 100 : null;
  const lad = m.ladder;

  /* 术语 + 梯队表放进 <details>：精简档默认收起（由 applyFolds 按档位设 open），
     完整档默认展开。表格本身不删，自检仍能查到节点。 */
  const ladderHtml = lad ? (
    '<details class="fold-detail" id="foldLadder"><summary>术语解释 + 连板梯队（点开看）</summary>' +
    '<p class="hint"><b>先把几个词说清楚</b>：<b>涨停</b>＝当天涨到上限、不能再涨；<b>炸板</b>＝涨到上限又掉下来了；<b>封板率</b>＝涨停家数 ÷（涨停＋炸板），越高说明买的人越愿意把价格顶在上限；<b>连板</b>＝连着几天涨停，2 连板就是连涨两天；<b>首板</b>＝这一波第一个涨停。</p>' +
    '<div class="subhead">连板梯队<span class="tip">越高说明资金接力意愿越强</span></div>' +
    '<div class="ladder">' +
      lad.dist.map(d => '<div class="lad-chip' + (d.h >= 3 ? ' hi' : '') + '"><b>' + d.h + '</b><span>' + d.n + ' 家</span></div>').join('') +
    '</div>' +
    (lad.top.length ? '<div class="subhead">高位板个股<span class="tip">连板数 ≥ 2，按板数与封单资金排</span></div>' +
      '<div class="table-wrap"><table class="grid"><thead><tr><th>名称</th><th>所属行业</th><th class="num">连板</th><th class="num">封单资金</th></tr></thead><tbody>' +
      lad.top.map(r => '<tr><td class="name-cell">' + esc(r.name) + '<span class="code-cell">' + esc(r.code) + '</span></td>' +
        '<td>' + esc(r.ind) + '</td>' +
        '<td class="num"><b class="up">' + r.lbc + ' 板</b></td>' +
        '<td class="num">' + money(r.fund) + '</td></tr>').join('') +
      '</tbody></table></div>' : '') +
    (lad.hot.length ? '<p class="hint"><b>涨停个股集中在</b>：' +
      lad.hot.map(x => x.name + '（' + x.n + ' 家）').join('、') + '。</p>' : '') +
    '</details>'
  ) : '';

  return '<section class="card" id="sec-senti">' +
    '<div class="card-head"><h2><i class="no">3</i>市场情绪 · 是不是普涨</h2>' +
    '<span class="stamp">涨跌家数为沪深两市合计 · 不含北交所</span></div>' +
    '<p class="lead">指数涨跌是「大票的体重」，<b>涨跌家数</b>才是「多少人真的赚到钱」；<b>涨停与炸板</b>反映的是资金愿不愿意把板封住。<b>指数涨 ≠ 你在赚钱</b>，这一段就是用来拆穿这件事的。</p>' +
    '<div class="fund-hero">' +
      card('上涨 / 下跌', '<span id="bUp" class="up">' + H(b.up) + '</span><span class="mc-slash" style="color:#C7CDD4;margin:0 4px">/</span><span id="bDown" class="down">' + H(b.down) + '</span>',
           '平盘 ' + H(b.flat) + ' 家 · 合计 ' + H(b.total) + ' 家') +
      card('涨停 / 跌停', '<span id="limUp" class="up">' + H(b.limitUp) + '</span><span style="color:#C7CDD4;margin:0 4px">/</span><span id="limDown" class="down">' + H(b.limitDown) + '</span>',
           '炸板 ' + H(b.bomb) + ' 家' + (seal != null ? ' · 封板率 ' + seal.toFixed(0) + '%' : '')) +
      card((t.scope || '两市') + '成交额', '<span id="turnoverAll">' + money(t.amount) + '</span>', '沪市 ' + money(t.amtSh) + ' · 深市 ' + money(t.amtSz) +
           (m.volume ? ' · ' + volWord((m.volume.today - m.volume.prev) / m.volume.prev * 100) : '')) +
    '</div>' +
    '<div class="subhead">涨跌家数占比</div>' +
    '<div class="st-track" style="height:22px">' +
      '<i class="up" style="left:0;width:' + upW.toFixed(2) + '%"></i>' +
      '<i class="flat" style="left:' + upW.toFixed(2) + '%;width:' + flatW.toFixed(2) + '%"></i>' +
      '<i class="down" style="left:' + (upW+flatW).toFixed(2) + '%;width:' + downW.toFixed(2) + '%"></i>' +
    '</div>' +
    '<div class="chart-meta"><span>上涨 <b class="up">' + upW.toFixed(1) + '%</b></span>' +
      '<span>平盘 <b class="flat">' + flatW.toFixed(1) + '%</b></span>' +
      '<span>下跌 <b class="down">' + downW.toFixed(1) + '%</b></span>' +
      (seal != null ? '<span>封板率 <b>' + seal.toFixed(0) + '%</b></span>' : '') + '</div>' +
    ladderHtml +
    '<p class="hint"><b>怎么看</b>：指数和涨跌家数经常不同向。指数微涨、但下跌家数远多于上涨家数，就是典型的<b>「指数失真」</b>——权重股撑住了指数，多数个股其实在跌。反过来也一样，别只看指数。<b>封板率</b>低于 45% 说明炸板偏多、追高容易被套；高于 70% 说明资金愿意把板封住。</p></section>';
}

/* ── 谁在涨：板块与题材（按涨跌幅排，和 sec3 的按资金排是两个维度） ── */
function secBoard(m){
  const bd = m.board || {indUp: [], indDown: [], conUp: []};
  const sr = m.stockRank || {chg: [], amt: []};
  /* 成分股少于这个数的行业/概念，一只票就能把整条板块顶到榜首，排名参考价值低 */
  const THIN = 5;
  const row = x => {
    const thin = (x.total != null && x.total > 0 && x.total < THIN);
    return '<li><span class="bl-name">' + esc(x.name) +
      (x.lead ? '<span class="bl-lead">领涨 ' + esc(x.lead) + '</span>' : '') +
      '</span><span class="bl-right"><b class="' + cls(x.chgPct) + '">' + pct(x.chgPct) + '</b>' +
      '<span class="bl-sub">' + (x.up != null ? x.up + ' 涨 / ' + x.down + ' 跌' : '') + '</span>' +
      (thin ? '<span class="bl-thin-tag">成分股仅 ' + x.total + ' 只</span>' : '') +
      '</span></li>';
  };  const block = (title, tip, arr) => (arr && arr.length)
    ? '<div><div class="subhead">' + title + '<span class="tip">' + tip + '</span></div>' +
      '<ul class="board-list">' + arr.map(row).join('') + '</ul></div>' : '<div></div>';

  const stockList = '<div><div class="subhead">个股涨幅榜<span class="tip">全 A 股，不含当日新股</span></div>' +
    (sr.chg.length ? '<ul class="board-list">' + sr.chg.map(x =>
      '<li><span class="bl-name">' + esc(x.name) + '<span class="bl-lead">' + esc(x.code) + ' · ' + esc(x.ind || '') + '</span></span>' +
      '<span class="bl-right"><b class="' + cls(x.chgPct) + '">' + pct(x.chgPct) + '</b>' +
      '<span class="bl-sub">成交 ' + money(x.amount) + '</span></span></li>').join('') + '</ul>'
      : '<div class="muted">暂无数据</div>') + '</div>';

  return '<section class="card" id="sec-board">' +
    '<div class="card-head"><h2><i class="no">4</i>谁在涨 · 板块与题材</h2>' +
    '<span class="stamp">按当日涨跌幅排序 · 不是按资金排的</span></div>' +
    '<p class="lead">前面看的是「整体涨不涨」，这里看<b>钱集中到了哪些行业和题材</b>。<b>行业榜</b>看大方向，<b>概念榜</b>看当天被反复提起的热点。注意：下面「资金流向」一段里按<b>主力净额</b>排的榜，和这里是<b>两套算法</b>，对不上很正常。</p>' +
    '<div class="board-grid">' +
      block('行业 · 涨幅榜', '按行业涨跌幅', bd.indUp) +
      block('行业 · 跌幅榜', '按行业涨跌幅', bd.indDown) +
    '</div>' +
    '<div class="board-grid" style="margin-top:18px">' +
      block('概念 · 涨幅榜', '当天的热点题材', bd.conUp) +
      stockList +
    '</div>' +
    '<p class="hint"><b>怎么看</b>：领涨的行业和题材是当天资金最集中的地方，领跌的相反。但记住——<b>这只是当天已经发生的事</b>。某个题材今天涨得最好，不代表明天还会涨，更不构成买入理由。' +
    '<br><b>先看它有多少只成分股</b>：行业里成分股太少的（标着「成分股仅 N 只」的那几条），一两只股票涨跌就能把整个板块顶到榜首，这种排名的参考价值低——它反映的是那两只票，不是整个行业的方向。</p></section>';
}

/* ── 柱状图（成交额 / 主力净额共用）：每根柱挂一个读数，悬停出浮层 ──
   为什么用注册表而不是 data-* 属性带 HTML：HTML 里带双引号，塞进属性就要转义，
   转义后又会被 innerHTML 当字面量显示。存进 BARS 表最省事。 */
const BARS = {};
let __bid = 0;
function barChart(items){
  const mx = Math.max.apply(null, items.map(d => Math.abs(d.value))) || 1;
  return '<div class="volbars">' + items.map((d, i) => {
    const last = i === items.length - 1;
    const h = Math.max(5, Math.abs(d.value) / mx * 100);
    const bid = 'b' + (++__bid);
    BARS[bid] = d.tip;
    return '<div class="vb' + (last ? ' now' : '') + (d.value < 0 ? ' neg' : '') + '" data-bar="' + bid + '">' +
      '<span class="vb-v">' + d.top + '</span>' +
      '<span class="vb-track"><i style="height:' + h.toFixed(1) + '%"></i></span>' +
      '<span class="vb-d">' + d.label + '</span></div>';
  }).join('') + '</div>';
}
/* 两市成交额近几个交易日（今天在最后一根） */
function volBars(v){
  return barChart(v.series.map(d => ({
    label: md(d.date), value: d.total, top: money(d.total),
    tip: '<div class="ct-t">' + md(d.date) + '</div>' +
         '<div class="ct-r"><span class="ct-k">两市成交额</span><span class="ct-v">' + money(d.total) + '</span></div>' +
         (d.chg == null ? '' :
           '<hr><div class="ct-r"><span class="ct-k">当日上证</span><span class="ct-v ' + cls(d.chg) + '">' +
             ARROW(d.chg) + ' ' + pct(d.chg) + '</span></div>' +
           '<div class="ct-r"><span class="ct-k">上证收盘</span><span class="ct-v">' + num(d.close, 2) + '</span></div>')
  }))) +
  '<div class="chart-meta">' +
    '<span>' + md(v.date) + ' 两市成交额 <b>' + money(v.today) + '</b></span>' +
    '<span>近 ' + v.days + ' 日均量 <b>' + money(v.avg) + '</b></span>' +
    '<span>今天 / 均量 <b>' + (v.today / v.avg * 100).toFixed(0) + '%</b></span>' +
    '<span>较上一交易日 <b class="' + cls(v.today - v.prev) + '">' + (v.today - v.prev >= 0 ? '放量 ' : '缩量 ') +
      money(Math.abs(v.today - v.prev)) + '</b></span>' +
  '</div>';
}
/* 近几个交易日的主力净额柱（正=净流入，负=净流出） */
function fundBars(days){
  return barChart(days.map(d => ({
    label: md(d.date), value: d.main,
    top: (d.main >= 0 ? '+' : '−') + Math.abs(d.main).toFixed(1) + ' 亿',
    tip: '<div class="ct-t">' + md(d.date) + '</div>' +
         '<div class="ct-r"><span class="ct-k">主力净额</span><span class="ct-v ' + cls(d.main) + '">' +
           (d.main >= 0 ? '+' : '−') + money(Math.abs(d.main)) + '</span></div>'
  })));
}

/* ══════════════════════════════════════════════════════════════════
   历史与位置 · 一张指数卡：近 60 个交易日走势 + 区间位置条
   ══════════════════════════════════════════════════════════════════ */
function histCard(name, series){
  if(!series) return '';
  const c = series.closes, n = c.length;
  const lo = Math.min.apply(null, c), hi = Math.max.apply(null, c);
  const last = c[n-1], first = c[0];
  const chg = first ? (last - first) / first * 100 : null;
  const pos = hi > lo ? (last - lo) / (hi - lo) * 100 : 50;
  const posWord = pos >= 80 ? '接近区间上沿' : (pos <= 20 ? '接近区间下沿' : '区间中部偏' + (pos >= 50 ? '上' : '下'));
  const chart = lineChart(c.map(v => ({v})), {
    w:900, h:150,
    tip:(i, v) => {
      const rel = first ? (v - first) / first * 100 : null;
      return '<div class="ct-t">' + (series.dates[i] || '') + '</div>' +
        '<div class="ct-r"><span class="ct-k">' + esc(name) + ' 收盘</span><span class="ct-v">' + num(v, 2) + '</span></div>' +
        '<hr><div class="ct-r"><span class="ct-k">相对区间起点</span><span class="ct-v ' + cls(rel) + '">' +
          ARROW(rel) + ' ' + pct(rel) + '</span></div>' +
        '<div class="ct-r"><span class="ct-k">距区间最高</span><span class="ct-v">' + ((v - hi) / hi * 100).toFixed(2) + '%</span></div>';
    }});
  return '<div class="hist-card">' +
    '<div class="hc-head"><span class="hc-name">' + esc(name) + '</span>' +
      '<span class="hc-chg ' + cls(chg) + '">' + ARROW(chg) + ' ' + pct(chg) + '</span>' +
      '<b class="hc-last ' + cls(chg) + '">' + num(last, 2) + '</b></div>' +
    chart +
    '<div class="posbar">' +
      '<div class="pb-track"><span class="pb-dot" style="left:' + Math.max(0, Math.min(100, pos)).toFixed(1) + '%"></span></div>' +
      '<div class="pb-lab"><span>区间最低 ' + num(lo, 2) + '</span>' +
        '<span>' + posWord + '（' + pos.toFixed(0) + '%）</span>' +
        '<span>区间最高 ' + num(hi, 2) + '</span></div>' +
    '</div>' +
  '</div>';
}

/* ── 历史 1 · 指数在什么位置 ─────────────────────────────── */
function secHMain(m){
  const list = m.klIdx || [];
  if(!list.length) return '';
  const s0 = list[0].series;
  const days = s0.n;
  return '<section class="card" data-page="hist" id="sec-h-main">' +
    '<div class="card-head"><h2><i class="no">1</i>近 60 个交易日，指数在什么位置</h2>' +
    '<span class="stamp">' + s0.start + ' 至 ' + s0.end + ' · ' + days + ' 个交易日</span></div>' +
    '<p class="lead">这一段把镜头拉远：<b>不看今天涨跌，看现在站在哪儿</b>。每张卡片上面是近 60 个交易日的收盘走势，' +
    '下面那条是<b>位置尺</b>——圆点越靠右，说明现在的点位越接近这两个月的最高点；越靠左则越接近最低点。' +
    '<b>认位置，比猜方向有用。</b></p>' +
    '<div class="hist-grid">' + list.map(x => histCard(x.name, x.series)).join('') + '</div>' +
    '<p class="hist-note"><b>怎么看这张尺</b>：位置高不等于要跌，位置低也不等于该买——它只回答「现在是在高位还是低位」这个问题的一半。' +
    '另一半是这家公司到底赚不赚钱（<b>基本面</b>），以及你自己是多少钱买的（<b>成本</b>）。上面几张图里如果只有一张跑到上沿、其他还在中部，说明涨的是其中一部分股票、不是全部——这种时候叫<b>结构性行情</b>，' +
    '鼠标移到任意一条线上，可以看那一天的收盘点位。</p></section>';
}

/* ── 历史 2 · 量能 ───────────────────────────────────────── */
function secHVol(m){
  const v = m.volume;
  if(!v) return '';
  const r = (v.today - v.prev) / v.prev * 100;
  const word = Math.abs(r) < 1.5 ? '今天两市成交额和前一个交易日基本持平。'
    : (r > 0 ? '今天比昨天放量 ' + r.toFixed(1) + '%。放量只说明今天换手的钱更多：在涨的时候出现，通常意味着追的人变多；在跌的时候出现，通常意味着卖的人变多。'
             : '今天比昨天缩量 ' + Math.abs(r).toFixed(1) + '%。缩量只说明今天换手的钱更少：想买和想卖的都暂时不动了，方向往往要等下一次放量才看得出来。');
  return '<section class="card" data-page="hist" id="sec-h-vol">' +
    '<div class="card-head"><h2><i class="no">2</i>今天的量，算大还是算小</h2>' +
    '<span class="stamp">最近 ' + v.series.length + ' 个交易日</span></div>' +
    '<p class="lead">价格告诉你「涨了多少」，成交额告诉你<b>今天有多少钱在换手</b>——参与的资金比昨天多，还是少。同一根阳线，' +
    '放量和缩量是两件完全不同的事。这里的柱高是<b>沪市＋深市合计成交额</b>，最后一根是今天。</p>' +
    volBars(v) +
    '<p class="hint"><b>怎么看</b>：' + word +
    '成交额是「参与度」，不是好坏——它只描述当天有多少钱在换手。' +
    '把今天的量放到最近 ' + v.series.length + ' 天里比，才知道这根柱子算不算异常。</p></section>';
}

/* ── 历史 3 · 资金 ───────────────────────────────────────── */
function secHFund(m){
  const days = (m.fund && m.fund.days) || [];
  if(days.length < 2) return '';
  const inflow = days.filter(d => d.main > 0).length;
  const sum = days.reduce((s, d) => s + d.main, 0);
  return '<section class="card" data-page="hist" id="sec-h-fund">' +
    '<div class="card-head"><h2><i class="no">3</i>这几天的钱，是进还是出</h2>' +
    '<span class="stamp">' + md(days[0].date) + ' 至 ' + md(days[days.length-1].date) + ' · ' + days.length + ' 个交易日</span></div>' +
    '<p class="lead">单看一天的资金流说明不了什么，<b>连着看几天</b>才有意义。' +
    '这里每根柱是当天的<b>主力净额</b>（大单＋超大单）：红柱是净流入，绿柱是净流出。</p>' +
    fundBars(days) +
    '<div class="chart-meta">' +
      '<span>区间合计 <b class="' + cls(sum) + '">' + (sum >= 0 ? '+' : '−') + money(Math.abs(sum)) + '</b></span>' +
      '<span>净流入 <b class="up">' + inflow + '</b> 天 / 净流出 <b class="down">' + (days.length - inflow) + '</b> 天</span>' +
    '</div>' +
    '<p class="hint"><b>数据长度</b>：这里展示最近 ' + days.length + ' 个交易日的日级主力净额（' + ((m.fund && m.fund.histScope) || '沪市') + '），' +
    '所以能看到的是<b>很短的一小段</b>，' +
    '不足以判断趋势，只能看个大概的方向。要更长的历史，得自己每天记一笔。</p></section>';
}

/* ── 历史 4 · 全球长周期 ─────────────────────────────────── */
function secHLong(m){
  const g = m.gkline || {};
  const cards = histCard('恒生指数（中国香港）', g.hk) + histCard('道琼斯工业平均（美国）', g.us);
  if(!cards) return '';
  return '<section class="card" data-page="hist" id="sec-h-globe">' +
    '<div class="card-head"><h2><i class="no">4</i>外面的世界，近 60 个交易日</h2>' +
    '<span class="stamp">中国香港 · 美国</span></div>' +
    '<p class="lead">A 股不是孤岛。这一段同样把镜头拉远，看<b>中国香港和美国这两个我们最常打交道的市场，近 60 个交易日站在什么位置</b>。' +
    '和上面四张 A 股图用同一把尺子，可以直接对比。</p>' +
    '<div class="hist-grid">' + cards + '</div>' +
    '<p class="hist-note"><b>为什么单看这两个</b>：中国香港和内地资金互通，情绪经常互相传导；' +
    '美国是隔夜行情的源头，A 股开盘时常会先消化一遍。看它们的位置，是给自己一个<b>外部参照</b>，不是拿来预测 A 股。</p></section>';
}

/* ══════════════════════════════════════════════════════════════════
   第 2 段 · 全球市场（市场切换：沪深京 / 港股 / 美股 / 全球）
   数据全来自东财公开接口，页面在线时与 A 股部分一样会实时刷新。
   ══════════════════════════════════════════════════════════════════ */
const MKEY = 'mb_market';
let MARKET = 'cn';
function loadMarket(){ try{ const v = localStorage.getItem(MKEY); if(MKTS.some(x => x.k === v)) MARKET = v; }catch(e){} }
function saveMarket(){ try{ localStorage.setItem(MKEY, MARKET); }catch(e){} }

/* 一个指数卡：点位 + 涨跌 + 涨跌幅 +（成交额 或 高低） */
function gCard(it){
  const foot = it.amount != null ? '成交 ' + money(it.amount)
    : (it.low != null && it.high != null ? '最低 ' + num(it.low,2) + ' · 最高 ' + num(it.high,2) : '—');
  return '<div class="gcard">' +
    '<div class="gn">' + esc(it.short || it.name) + (it.region ? '<span>' + esc(it.region) + '</span>' : '') + '</div>' +
    '<div class="gp ' + cls(it.chgPct) + '">' + num(it.price, 2) + '</div>' +
    '<div class="gc ' + cls(it.chgPct) + '">' + ARROW(it.chgPct) + ' ' + pct(it.chgPct) +
      (it.chg != null ? '<span class="idx-abs">(' + (it.chg >= 0 ? '+' : '−') + num(Math.abs(it.chg), 2) + ')</span>' : '') +
    '</div>' +
    '<div class="gf">' + foot + '</div>' +
  '</div>';
}
/* 一大串指数 / 标的：紧凑三列表 */
function gRows(list){
  if(!list || !list.length) return '<div class="muted">暂无数据</div>';
  return '<div class="wgrid">' + list.map(x =>
    '<div class="wrow"><span class="wn">' + esc(x.name) + '</span>' +
    '<span class="wp">' + num(x.price, x.price >= 100 ? 2 : 3) + '</span>' +
    '<span class="wc ' + cls(x.chgPct) + '">' + ARROW(x.chgPct) + ' ' + pct(x.chgPct) + '</span></div>').join('') +
  '</div>';
}
/* 全球指数：按 亚太 / 欧洲 / 美洲 / 商品与汇率 分组，组内按涨跌幅降序 */
function gRegions(m){
  const all = m.gworld || [];
  const groups = [R_AP, R_EU, R_AM, R_CM]
    .map(r => ({r, list: all.filter(x => x.region === r).sort((a,b) => (b.chgPct||0) - (a.chgPct||0))}))
    .filter(g => g.list.length);
  if(!groups.length) return '<div class="muted">暂无全球指数数据</div>';
  return groups.map(g => '<div class="g-sub"><div class="subhead">' + g.r +
      '<span class="tip">' + g.list.length + ' 个 · 按当日涨跌幅排</span></div>' + gRows(g.list) + '</div>').join('');
}
/* ── 此刻各地几点 / 谁在开市 ────────────────────────────────────
   这里原来是两排互不相干的东西：一排城市钟（北京/伦敦/纽约/悉尼）
   ＋ 一排状态灯（沪深京/港股/美股/日本）。两套名字对不上，读者看不出
   它们说的是同一件事（典型反馈：「这里看不懂，你想表达什么意思」）。
   现在并成一排卡片，一格说清一件事：市场 · 当地几点 · 开没开市 · 与北京时差。 */
const MK_CLOCK = [
  {k:'cn', l:'沪深京',   tz:'Asia/Shanghai',    city:'上海 · 深圳 · 北京'},
  {k:'hk', l:'中国香港', tz:'Asia/Hong_Kong',   city:'香港交易所'},
  {k:'us', l:'美股',     tz:'America/New_York', city:'纽约 · 纳斯达克'},
  {k:'jp', l:'日本',     tz:'Asia/Tokyo',       city:'东京'}
];
function gTzBar(){
  const bj = tzOffMin('Asia/Shanghai');
  const usOff = tzOffMin('America/New_York');
  /* ⚠️ 举例里的钟点必须取实时值：写死「18:13」而页面显示「18:18」，一眼就是错的 */
  const usD = (usOff == null || bj == null) ? null : usOff - bj;
  return '<div class="tzbar">' +
    '<div class="tzbar-h"><b>此刻各地几点、谁在开市</b>' +
      '<span>同一个时刻，四个市场最多差 12–13 个小时——所以「现在涨没涨」得先看那个市场<b>在不在交易时段</b>。' +
      '<b>红点</b>＝正在交易，<b>黄点</b>＝午间休息，<b>灰点</b>＝没开市。' +
      '每格底下那行是它与北京时间的时差，用来把四地的时间对齐到同一把尺子上。</span></div>' +
    '<div class="tzgrid">' + MK_CLOCK.map(x => {
      const st = mktState(x.k), off = tzOffMin(x.tz);
      const diff = (off == null || bj == null) ? null : off - bj;
      return '<div class="tzc ' + st.c + '">' +
        '<div class="tzc-n">' + x.l + '<em>' + x.city + '</em></div>' +
        '<div class="tzc-t">' + clockOf(x.tz) + '</div>' +
        '<div class="tzc-s"><i></i>' + st.s + '</div>' +
        '<div class="tzc-o">' + (tzDiffText(diff) || '—') + '</div>' +
      '</div>';
    }).join('') + '</div>' +
    '<p class="hint"><b>为什么同一个数字不是同一件事</b>：每格最上面那行是<b>当地此刻几点</b>——' +
    '北京和中国香港本来就在同一个时区，所以数字一样；而美股那边' +
    (usD == null ? '和北京差着十几个小时' : tzDiffText(usD)) +
    '，你在晚饭后看盘，那边的人可能还没起床。' +
    '所以看涨跌之前先看状态：<b>灰点＝那个市场此刻不在交易时段</b>，它「没动」<b>不等于</b>它没涨。</p>' +
    '</div>';
}
/* 每个市场的代表指数——分时图画的就是它（与东财行情页一致：点哪个市场，就看哪个市场的分时） */
const GMAIN = {cn:'上证指数', hk:'恒生指数', us:'道指', gl:'日经225'};
/** 当日分时走势：四个市场各一张，切标签时跟着换 */
function gTrend(m){
  let d = null;
  if(MARKET === 'cn'){
    const it = (m.indices || []).find(x => x.code === '000001');
    d = (it && it.trend) ? {t:it.trend, tip:'只算沪市'} : null;
  } else if(MARKET === 'hk'){
    d = (m.gtrend && m.gtrend.hk) ? {t:m.gtrend.hk, tip:'中国香港午休 12:00–13:00'} : null;
  } else if(MARKET === 'us'){
    d = (m.gtrend && m.gtrend.us) ? {t:m.gtrend.us, tip:'连续交易，不设午休'} : null;
  } else {
    d = (m.gtrend && m.gtrend.jp) ? {t:m.gtrend.jp, tip:'亚太时段'} : null;
  }
  if(!d || !d.t || !d.t.pts || d.t.pts.length < 2) return '';
  const pts = d.t.pts, pre = d.t.preClose, am = d.t.am;
  const cl = pts.map(p => p.close);
  let hiI = 0, loI = 0;
  cl.forEach((v, i) => { if(v > cl[hiI]) hiI = i; if(v < cl[loI]) loI = i; });
  const last = cl[cl.length - 1];
  const chg = pre ? (last - pre) / pre * 100 : null;
  const t5 = t => String(t == null ? '' : t).slice(-5);
  const chart = lineChart(cl.map(v => ({v})), {
    w:900, h:210, gapIndex: (am && am.length) ? am.length : null,
    color: chg == null ? '#5A6572' : (chg >= 0 ? '#D93636' : '#12925C'),
    foot:'<span>' + t5(pts[0].time) + ' 开</span><span>' +
         ((am && am.length) ? t5(am[am.length-1].time) + ' 上午收' : '') + '</span><span>' +
         t5(pts[pts.length-1].time) + ' 最新</span>',
    tip:(i, v) => {
      const c2 = pre ? (v - pre) / pre * 100 : null;
      return '<div class="ct-t">' + t5((pts[i] || {}).time) + '</div>' +
        '<div class="ct-r"><span class="ct-k">' + esc(GMAIN[MARKET]) + '</span><span class="ct-v">' + num(v, 2) + '</span></div>' +
        '<hr><div class="ct-r"><span class="ct-k">相对昨收</span><span class="ct-v ' + cls(c2) + '">' +
          ARROW(c2) + ' ' + pct(c2) + '</span></div>';
    }});
  return '<div class="g-sub g-trend">' +
    '<div class="subhead">' + esc(GMAIN[MARKET]) + ' · 当日分时<span class="tip">' + esc(d.tip) + ' · 鼠标移上去看具体时刻</span></div>' +
    '<div class="gt-head">' +
      '<b class="' + cls(chg) + '">' + num(last, 2) + '</b>' +
      '<span class="' + cls(chg) + '">' + ARROW(chg) + ' ' + pct(chg) + '</span>' +
      '<i>今开 ' + num(cl[0], 2) + '</i>' +
      '<i>最高 ' + num(cl[hiI], 2) + '（' + t5(pts[hiI].time) + '）</i>' +
      '<i>最低 ' + num(cl[loI], 2) + '（' + t5(pts[loI].time) + '）</i>' +
    '</div>' + chart + '</div>';
}
/* 这一段的动态部分——切市场只重绘这里 */
function gBody(m){
  const list = (m.gidx || []).filter(x => x.k === MARKET);
  let extra = '';
  if(MARKET === 'cn'){
    const b = m.breadth, t = m.turnover;
    extra = '<div class="g-sub"><div class="subhead">沪深两市 · 今日概况</div>' +
      '<div class="watch-sum">' +
        '<span>上涨 <b class="up">' + H(b.up) + '</b> 家 / 下跌 <b class="down">' + H(b.down) + '</b> 家</span>' +
        '<span>涨停 <b class="up">' + H(b.limitUp) + '</b> / 跌停 <b class="down">' + H(b.limitDown) + '</b></span>' +
        '<span>两市成交额 <b>' + money(t.amount) + '</b></span>' +
      '</div>' +
      '<p class="hint"><b>下一步</b>：完整复盘在下面「今天发生了什么」一段——那里默认就是沪深京，把这一天拆成<b>午评、午后快评、收评</b>三段；' +
      '想看别的市场，那一段顶部有<b>港股 / 美股 / 全球市场</b>三个标签可以切。' +
      '<a class="lnk" data-jump="sec-market">跳过去 ↓</a></p></div>';
  } else if(MARKET === 'hk'){
    extra = '<div class="g-sub"><div class="subhead">港股 · 常见标的<span class="tip">精选名单，不是涨幅榜</span></div>' +
      gRows(m.ghk) +
      '<p class="hint"><b>为什么不用涨幅榜</b>：港股涨幅榜前排几乎全是<b>牛熊证与窝轮</b>——带杠杆的衍生品，' +
      '能一天翻倍也能一天归零，对刚接触的人没有参考价值。所以这里只放大家听过的公司。</p></div>';
  } else if(MARKET === 'us'){
    extra = '<div class="g-sub"><div class="subhead">美股 · 常见标的<span class="tip">精选名单，不是涨幅榜</span></div>' +
      gRows(m.gus) +
      '<p class="hint"><b>为什么不用涨幅榜</b>：美股涨幅榜前排多是<b>仙股与权证</b>（价格几分钱、单日暴涨暴跌），' +
      '美股又没有涨跌停，这类标的的波动会更极端。</p></div>';
  } else {
    extra = '<div class="g-sub"><div class="subhead">全球主要指数<span class="tip">按区域分组</span></div>' + gRegions(m) +
      '<p class="hint"><b>怎么看这一段</b>：同一时刻只有一部分市场在交易——白天是亚太，下午轮到欧洲，' +
      '晚上到凌晨才是美洲。上面那张<b>「此刻各地几点」</b>就是用来对齐这件事的：' +
      '先看那个市场开没开市，再看它的涨跌。这里只放<b>指数点位与涨跌</b>；单个市场的分时走势，切到对应标签去看。</p></div>';
  }
  return gTzBar() +
    '<div class="gidx-grid">' + (list.length ? list.map(gCard).join('') : '<div class="muted">暂无数据</div>') + '</div>' +
    gTrend(m) + extra;
}
/* 底部提示：这个市场的时段、规则、颜色习惯 */
function gNote(){
  const r = MKT_RULE[MARKET] || MKT_RULE.cn;
  return '<b>' + r.full + ' · 交易时段</b>：' + r.sess + '。' +
         '<b>规则</b>：' + r.rule + '。' +
         '<b>颜色</b>：' + r.color + '。' +
         '<b>本页数字是接口返回时刻的快照</b>，不保证实时，也不是目标价。';
}
function secGlobal(m){
  return '<section class="card" id="sec-global">' +
    '<div class="card-head"><h2><i class="no">2</i>全球市场 · 一屏看全球</h2>' +
    '<span class="stamp">默认沪深京 · 点标签切换</span></div>' +
    '<p class="lead">同一个时刻，全球市场处在<b>不同状态</b>：北京时间白天是亚太在交易，下午轮到欧洲，晚上到凌晨是美股。' +
    '下面四个标签对应四类市场，<b>默认停在沪深京</b>，点一下就能看那一边现在什么情况。</p>' +
    '<div class="mkt-tabs" id="gTabs">' + MKTS.map(x =>
      '<button class="mt' + (x.k === MARKET ? ' on' : '') + '" data-mkt="' + x.k + '">' + x.label +
      '<em>' + mktState(x.k).s + '</em></button>').join('') + '</div>' +
    '<div id="gBody">' + gBody(m) + '</div>' +
    '<div class="gl-note" id="gNote">' + gNote() + '</div>' +
  '</section>';
}
/* 切市场：只重绘这一段，不动整页（保住持仓表里正在填的内容） */
function repaintGlobal(){
  const m = window.__MODEL; if(!m) return;
  setHtml('gBody', gBody(m));
  setHtml('gNote', gNote());
  document.querySelectorAll('#gTabs .mt').forEach(b => {
    b.classList.toggle('on', b.dataset.mkt === MARKET);
    const em = b.querySelector('em');
    if(em) em.textContent = mktState(b.dataset.mkt).s;
  });
  bindSegmentJump();
}
function bindGlobal(){
  const box = document.getElementById('gTabs');
  if(box && !box._b){
    box._b = 1;
    box.onclick = e => {
      const b = e.target && e.target.closest ? e.target.closest('.mt') : null;
      if(!b) return;
      const k = b.dataset.mkt;
      if(!k || k === MARKET) return;
      MARKET = k; saveMarket(); repaintGlobal();
    };
  }
  bindReport();          // 第 3 段「按市场复盘」的四个标签
  bindSegmentJump();
}
/* 段内「跳过去」链接（和目录条是同一个跳转动作）
   ⚠️ 选择器放宽到全页：第 2 段与第 3 段里都有这种链接，写死 #sec-global 会漏掉第 3 段的 */
function bindSegmentJump(){
  document.querySelectorAll('[data-jump]').forEach(a => {
    a.onclick = () => {
      const el = document.getElementById(a.dataset.jump);
      if(el) el.scrollIntoView({behavior:'smooth', block:'start'});
    };
  });
}

/* ══════════════════════════════════════════════════════════════════
   8 · 我的账本（工作台）
   与「看板」的分工：看板回答「市场现在怎么样」，账本回答「我的钱现在怎么样、
   我当时为什么这么做」。三张表递进：
     ① 交易台账 —— 你只记「哪天、买还是卖、多少钱、多少份」，
        成本和持仓、已实现盈亏全部自动算出来（不让你算任何东西）。
     ② 持仓与净值 —— 台账推出来的持仓 + 每天一条总资产快照 → 净值曲线。
        ⚠️ 净值曲线要攒够天数才有意义，所以快照越早开始存越好。
     ③ 复盘日记 —— 每天一条：决定 / 理由 / 情绪。这是「工作台」和「看板」最本质的分界线，
        它逼你把决定写下来，方便以后回看自己当时到底在想什么。
   数据全部存浏览器本地，配「导出 / 导入 JSON」防丢；不引后端、不引第三方库。
   ══════════════════════════════════════════════════════════════════ */
const LKEY = 'mb_ledger_v1';
const SKEY = 'mb_snap_v1';
const JKEY = 'mb_journal_v1';
const CKEY = 'mb_cash_v1';
const MOODS = ['平静', '兴奋', '焦虑', '犹豫', '后悔'];

/* 云端连接状态。放这里是因为下面每个 saveXxx 都要问一句「现在连上没有」。
   用 var 不是 let：var 会先被初始化成 undefined，保存动作万一早于赋值也不会炸。 */
var CLOUD = { sdk:false, ready:false, booted:false, busy:false,
              user:null, uid:null, at:0, err:'', offsite:false, rids:null, jdates:null, counts:null };

let LEDGER = [];       // [{rid, date, code, mkt, name, side:'buy'|'sell', price, qty}]
let SNAPS = {};        // { 'YYYY-MM-DD': {date,total,cash,est,rows:[{code,name,qty,cost,price,mv,pnl}]} }
let JOURNAL = {};      // { 'YYYY-MM-DD': {dec, why, mood} }
let CASH = 0;          // 没买成股票的现金（不计入持仓），默认 0
let _qBusy = 0;        // 行情批量补拉中标志，避免重复请求

const _lgT = {};       // 台账输入防抖计时器
const _jT = {};        // 日记输入防抖计时器

function todayStr(){
  const d = new Date(), p = n => (n < 10 ? '0' : '') + n;
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}
function jget(k, def){ try{ const v = localStorage.getItem(k); return v == null ? def : JSON.parse(v); }catch(e){ return def; } }
function jset(k, v){ try{ localStorage.setItem(k, JSON.stringify(v)); }catch(e){} }
/* D1 · 数据存放提醒：第一次真正存下数据时弹一次「只存本设备、记得导出」，之后不再打扰 */
function dataHintOnce(){
  try{
    if(localStorage.getItem('mb_hint_shown')) return;
    localStorage.setItem('mb_hint_shown', '1');
    if(CLOUD && CLOUD.ready) return;      // 已经连着云端，这条提醒就不成立了
    window.alert('已保存。提醒一句：你的填写只存在这台设备的这个浏览器里，不上传服务器。\n' +
      '换设备、换浏览器、清理浏览器数据都会丢——建议定期到「我的账本」页点「导出我的数据」备份一份；' +
      '同一页点「使用 WorkBuddy 登录」，登录后会自动存一份到云端。');
  }catch(e){}
}
/* D3 · 上次导出备份时间（「导出我的数据」每次成功导出都会记时间戳） */
function bkExpText(){
  let t = '';
  try{ t = localStorage.getItem('mb_bk_exp') || ''; }catch(e){}
  return t ? '上次导出备份：' + t + '。' : '还没有导出过备份——建议现在就点一次「导出我的数据」。';
}
/* 金额：账本里都是「元」，不走看板那套「亿」口径 */
const yuan = v => (v == null || !isFinite(v)) ? '—' : v.toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2});
const qtyF = v => (v == null || !isFinite(v)) ? '—' : v.toLocaleString('zh-CN', {maximumFractionDigits:3});

function loadBook(){
  const a = jget(LKEY, null);
  LEDGER = Array.isArray(a) ? a.map(x => ({
    date: (x && x.date) || todayStr(),
    code: stripCode(x && x.code),
    mkt: (x && x.mkt) || '',
    name: (x && x.name) || '',
    side: (x && x.side) === 'sell' ? 'sell' : 'buy',
    price: (x && x.price != null) ? x.price : '',
    qty: (x && x.qty != null) ? x.qty : '',
    rid: (x && x.rid) || ''
  })) : [];
  /* 每笔流水都要有一个不会变的行号（rid）：云端靠它对行——
     没有它，两台设备上的同一笔会被当成两笔，越同步越多。只补一次，补完就落盘。 */
  let _ridNew = false;
  LEDGER.forEach(r => { if(!r.rid){ r.rid = cloudRid(); _ridNew = true; } });
  if(_ridNew) jset(LKEY, LEDGER);
  SNAPS = jget(SKEY, {}) || {};
  JOURNAL = jget(JKEY, {}) || {};
  const c = jget(CKEY, 0);
  CASH = (typeof c === 'number' && isFinite(c)) ? c : 0;
}
/* 每次保存：本地先落盘（永远成功），再看要不要顺手推一份到云端（没登录就跳过） */
const saveLedger  = () => {
  /* 新加的流水行没有 rid，落盘前统一补上——否则刷新一次行号就变，云端认不出这是同一笔 */
  LEDGER.forEach(r => { if(!r.rid) r.rid = cloudRid(); });
  jset(LKEY, LEDGER); dataHintOnce(); cloudTouch();
};
const saveSnaps   = () => { jset(SKEY, SNAPS); cloudTouch(); };
const saveJournal = () => { jset(JKEY, JOURNAL); cloudTouch(); };
const saveCash    = () => { jset(CKEY, CASH); cloudTouch(); };

  /* 台账 → 持仓：移动加权平均成本。
     ⚠️ 成本价只在「买入」时重算，卖出不动成本（移动加权平均的通行做法，
        但各家券商含不含手续费、是否摊薄并不统一，所以页面要写明口径）；
        卖出时把 (成交价 − 成本价) × 卖出份数 结转到「已实现盈亏」。 */
function derivePositions(){
  const map = {}, order = [];
  LEDGER.forEach((r, idx) => {
    const code = stripCode(r.code);
    if(!code) return;
    if(!map[code]){
      map[code] = {code:code, mkt:r.mkt || inferMarket(code), name:r.name || '',
                   qty:0, cost:0, buyAmt:0, sellAmt:0, realized:0, warn:'', n:0};
      order.push(code);
    }
    const p = map[code];
    p.n++;
    if(r.name) p.name = r.name;
    if(r.mkt) p.mkt = r.mkt;
    const price = N(r.price), qty = N(r.qty);
    if(price == null || qty == null || qty <= 0) return;
    if(r.side === 'sell'){
      const sellQ = Math.min(qty, p.qty);
      if(qty > p.qty + 1e-9) p.warn = '卖出份数超过持仓，超出部分按 0 计';
      p.realized += (price - p.cost) * sellQ;
      p.sellAmt += price * sellQ;
      p.qty -= sellQ;
      if(p.qty <= 1e-9){ p.qty = 0; p.cost = 0; }   // 清仓后成本归零，下次买入重新起算
    }else{
      const amt = price * qty;
      p.cost = (p.cost * p.qty + amt) / (p.qty + qty);
      p.qty += qty;
      p.buyAmt += amt;
    }
  });
  return order.map(k => map[k]);
}
/* 给持仓补现价：优先用手上已有的行情，缺的批量拉一次。
   取不到价就退回成本价并标记 est —— 宁可显示「按成本估」也不显示「—」。 */
function bookPositions(){
  const pos = derivePositions();
  pos.forEach(p => {
    const q = quoteOf(p);
    if(q && q.price != null){ p.price = q.price; p.est = false; }
    else { p.price = p.cost; p.est = true; }
    p.mv = p.qty * p.price;
  });
  return pos;
}
function needQuotes(){
  const pos = derivePositions().filter(p => p.qty > 0);
  const miss = pos.filter(p => { const q = quoteOf(p); return !q || q.price == null; });
  if(!miss.length || _qBusy) return;
  _qBusy = 1;
  fetchQuotes(miss).then(map => {
    Object.keys(map || {}).forEach(k => putQuote(k.slice(0, 2), k.slice(2), map[k]));
    _qBusy = 0;
    paintBook();
  }).catch(() => { _qBusy = 0; });
}

/* ── 8.1 交易台账 ─────────────────────────────────────────── */
const LG_COLS = [
  {k:'date',  t:'日期'},
  {k:'code',  t:'代码 / 名称'},
  {k:'side',  t:'买还是卖'},
  {k:'price', t:'成交价'},
  {k:'qty',   t:'份数'},
  {k:'amt',   t:'金额（自动算）'},
  {k:'_',     t:''}
];
function lgAmt(r){
  const p = N(r.price), q = N(r.qty);
  return (p == null || q == null) ? null : p * q;
}
function lgRowHtml(i){
  const r = LEDGER[i] || {};
  const amt = lgAmt(r);
  return '<tr data-lgr="' + i + '">' +
    '<td><input data-i="' + i + '" data-k="date" value="' + esc(r.date) + '" placeholder="2026-10-16"></td>' +
    '<td><input data-i="' + i + '" data-k="code" value="' + esc(r.code) + '" placeholder="510300 或 沪深300ETF">' +
      '<span class="lk-state" id="lgk-' + i + '"></span></td>' +
    '<td><div class="lg-side">' +
      '<button type="button" data-lgi="' + i + '" data-side="buy" class="' + (r.side !== 'sell' ? 'on' : '') + '">买入</button>' +
      '<button type="button" data-lgi="' + i + '" data-side="sell" class="' + (r.side === 'sell' ? 'on' : '') + '">卖出</button>' +
    '</div></td>' +
    '<td class="c-num"><input class="num" data-i="' + i + '" data-k="price" value="' + esc(r.price) + '" placeholder="0.000" inputmode="decimal"></td>' +
    '<td class="c-num"><input class="num" data-i="' + i + '" data-k="qty" value="' + esc(r.qty) + '" placeholder="0" inputmode="decimal"></td>' +
    '<td class="num" id="lgamt-' + i + '">' + (amt == null ? '—' : yuan(amt)) + '</td>' +
    '<td><button type="button" class="bk-x" data-lgdel="' + i + '" title="删除这一笔">×</button></td>' +
  '</tr>';
}
function secBookLedger(){
  const pos = derivePositions();
  const rows = LEDGER.length ? LEDGER.map((_, i) => lgRowHtml(i)).join('')
    : '<tr><td colspan="7" class="muted" style="padding:18px;text-align:center">' +
      '还没有记录。点「+ 记一笔」开始，或者先「填入一笔示例」看看长什么样。</td></tr>';
  return '<section class="card" data-page="book" id="sec-bk-ledger">' +
    '<div class="card-head"><h2><i class="no">1</i>交易台账 · 我只记流水</h2>' +
    '<span class="stamp">买了什么、卖了多少</span></div>' +
    '<p class="lead">你只填四样：<b>哪天、买还是卖、成交价、份数</b>。' +
    '后面的「金额、成本价、持仓份数、已实现盈亏」全部自动算——' +
    '<b>手算成本是最容易出错的一步，所以干脆不让你算。</b></p>' +
    '<div class="bk-bar">' +
      '<button type="button" class="bk-btn primary" id="lgAdd">+ 记一笔</button>' +
      '<button type="button" class="bk-btn" id="lgDemo">填入一笔示例</button>' +
      '<button type="button" class="bk-btn" id="lgClear">清空台账</button>' +
      '<span class="bk-sep"></span>' +
      '<button type="button" class="bk-btn" id="lgSync" title="把台账算出来的持仓写进「今天 · 我的持仓监控」">→ 同步到持仓监控</button>' +
    '</div>' +
    '<div class="lg-wrap"><table class="hold lg"><thead><tr>' +
      LG_COLS.map(c => '<th class="' + (c.k === 'amt' ? 'num' : '') + '">' + c.t + '</th>').join('') +
    '</tr></thead><tbody id="lgRows">' + rows + '</tbody></table></div>' +
    '<div id="lgSummary">' + lgSummaryHtml(pos) + '</div>' +
    '<p class="hint"><b>为什么台账比手填成本表靠谱</b>：你只要保证「每一笔流水都记下来」这一件事，' +
    '成本和盈亏是算出来的，不会因为你记错一个小数点就越错越远。' +
    '分几次买入时，成本价是这么算的：把这几次花的钱全加起来，再除以总份数，得出一个新成本价。卖出只减份数、不改成本价。' +
    '这里是<b>按加权平均成本</b>算的，<b>不含手续费和印花税</b>，和你的券商显示的成本价可能有差异。</p>' +
    '</section>';
}
/* 台账下方的持仓小结（每只标的：份数 / 成本 / 已实现） */
function lgSummaryHtml(pos){
  const held = (pos || []).filter(p => p.qty > 0 || p.n > 0);
  if(!held.length) return '';
  const tot = held.reduce((s, p) => s + p.qty * p.cost, 0);
  return '<div class="subhead">台账算出来的持仓</div>' +
    '<div class="bk-mini">' +
      held.map(p =>
        '<div class="bm-card' + (p.qty > 0 ? '' : ' flat') + '">' +
          '<div class="bm-name">' + esc(p.name || ('代码 ' + p.code)) + '<em>' + p.code + '</em></div>' +
          (p.qty > 0
            ? '<div class="bm-row"><span>持仓</span><b>' + qtyF(p.qty) + ' 份</b></div>' +
              '<div class="bm-row"><span>成本价</span><b>' + num(p.cost, p.cost >= 100 ? 2 : 3) + '</b></div>' +
              '<div class="bm-row"><span>持仓成本</span><b>' + yuan(p.qty * p.cost) + '</b></div>'
            : '<div class="bm-row"><span>当前</span><b>已清仓</b></div>') +
          (Math.abs(p.realized) > 0.005
            ? '<div class="bm-row"><span>已实现盈亏</span><b class="' + cls(p.realized) + '">' +
              (p.realized >= 0 ? '+' : '−') + yuan(Math.abs(p.realized)) + '</b></div>'
            : '') +
          (p.warn ? '<div class="bm-warn">' + esc(p.warn) + '</div>' : '') +
        '</div>').join('') +
    '</div>' +
    '<p class="hint">按成本计的持仓合计 <b>' + yuan(tot) + '</b>（这是你投进去的钱，不是现在值多少）。</p>';
}
/* 只重绘「自动算」的格子，不碰输入框本身 ⇒ 正在输入的字和光标都不会丢 */
function paintLgDerived(){
  const pos = derivePositions();
  LEDGER.forEach((r, i) => {
    const cell = document.getElementById('lgamt-' + i);
    if(cell){ const a = lgAmt(r); cell.textContent = (a == null ? '—' : yuan(a)); }
  });
  const box = document.getElementById('lgSummary');
  if(box) box.innerHTML = lgSummaryHtml(pos);
}
function lgAddRow(){
  LEDGER.push({date: todayStr(), code:'', mkt:'', name:'', side:'buy', price:'', qty:''});
  saveLedger();
  const tb = document.getElementById('lgRows');
  if(tb){
    const empty = tb.querySelector('td.muted');
    if(empty) tb.innerHTML = LEDGER.map((_, i) => lgRowHtml(i)).join('');
    else tb.insertAdjacentHTML('beforeend', lgRowHtml(LEDGER.length - 1));
  }
  paintLgDerived();
  refreshBookTabs();
  const last = document.querySelector('#lgRows tr[data-lgr="' + (LEDGER.length - 1) + '"] input[data-k="code"]');
  if(last) last.focus();
}
function lgDelRow(i){
  LEDGER.splice(i, 1);
  saveLedger();
  const tb = document.getElementById('lgRows');
  if(tb){
    if(!LEDGER.length) tb.innerHTML = '<tr><td colspan="7" class="muted" style="padding:18px;text-align:center">' +
      '还没有记录。点「+ 记一笔」开始。</td></tr>';
    else tb.innerHTML = LEDGER.map((_, n) => lgRowHtml(n)).join('');
  }
  paintLgDerived();
  refreshBookTabs();
  needQuotes();
}
/* 台账里的代码 → 名称（复用持仓那套腾讯 smartbox 搜索，不额外引接口） */
function lookupLedger(i){
  const r = LEDGER[i]; if(!r) return;
  const q = String(r.code || '').trim() || String(r.name || '').trim();
  const el = document.getElementById('lgk-' + i);
  const say = (c, t) => { if(el){ el.className = 'lk-state' + (c ? ' ' + c : ''); el.textContent = t || ''; } };
  if(!q){ say('', ''); return; }
  say('busy', '查询中…');
  searchAny(q).then(res0 => {
    const list = (res0 && res0.list) || [], rawH = (res0 && res0.raw) || '';
    if(!LEDGER[i]) return;
    if(!list.length){
      say('err', (res0 && res0.otc) || /(^|\^)jj~/.test(rawH) ? '场外基金，取不到行情' : '没找到，可换名称试试');
      return;
    }
    const d = stripCode(r.code);
    const pick = (d && list.filter(x => x.code === d)[0]) || list[0];
    if(!pick) return;
    LEDGER[i].code = pick.code;
    LEDGER[i].mkt = pick.mkt;
    if(pick.name) LEDGER[i].name = pick.name;
    saveLedger();
    const tr = document.querySelector('#lgRows tr[data-lgr="' + i + '"]');
    if(tr){
      const ci = tr.querySelector('input[data-k="code"]');
      if(ci && document.activeElement !== ci) ci.value = pick.code;
    }
    say('ok', '已带出名称');
    paintLgDerived();
    needQuotes();
    refreshBookTabs();
  }).catch(() => say('err', '查询失败'));
}

/* ── 8.2 持仓与净值 ───────────────────────────────────────── */
function snapSeries(){
  return Object.keys(SNAPS).sort().map(d => ({date:d, total:SNAPS[d].total, est:!!SNAPS[d].est}));
}
function bookTotals(){
  const pos = bookPositions().filter(p => p.qty > 0);
  const mv = pos.reduce((s, p) => s + p.mv, 0);
  const cost = pos.reduce((s, p) => s + p.qty * p.cost, 0);
  return {pos:pos, mv:mv, cost:cost, est:pos.some(p => p.est), total:mv + CASH};
}
function secBookPos(){
  const t = bookTotals();
  const series = snapSeries();
  const held = t.pos.filter(p => p.qty > 0);
  const rows = held.length ? held.map(p => {
    const pnl = p.mv - p.qty * p.cost;
    const chgPct = p.cost > 0 ? (p.price - p.cost) / p.cost * 100 : null;   // ⚠️ 别叫 pct，会遮蔽全局的 pct()
    const w = t.total > 0 ? p.mv / t.total * 100 : 0;
    return '<tr><td>' + esc(p.name || ('代码 ' + p.code)) + '<span class="code-cell">' + p.code + '</span></td>' +
      '<td class="num">' + qtyF(p.qty) + '</td>' +
      '<td class="num">' + num(p.cost, p.cost >= 100 ? 2 : 3) + '</td>' +
      '<td class="num">' + (p.est ? '<span class="est-tag">成本价</span>' : num(p.price, p.price >= 100 ? 2 : 3)) + '</td>' +
      '<td class="num">' + yuan(p.mv) + '</td>' +
      '<td class="num ' + cls(pnl) + '">' + (pnl >= 0 ? '+' : '−') + yuan(Math.abs(pnl)) + '</td>' +
      '<td class="num ' + cls(pnl) + '">' + pct(chgPct) + '</td>' +
      '<td class="num">' + w.toFixed(1) + '%</td></tr>';
  }).join('') : '<tr><td colspan="8" class="muted" style="padding:18px;text-align:center">' +
      '台账里还没有买过的标的。先在上一段记一笔。</td></tr>';

  const chart = series.length >= 2
    ? lineChart(series.map(s => ({v:s.total})), {
        w:900, h:170,
        tip:(i, v) => {
          const s = series[i], prev = i > 0 ? series[i-1] : null;
          const base = series[0].total;
          return '<div class="ct-t">' + s.date + '</div>' +
            '<div class="ct-r"><span class="ct-k">总资产</span><span class="ct-v">' + yuan(v) + '</span></div>' +
            (prev ? '<hr><div class="ct-r"><span class="ct-k">较上一条</span><span class="ct-v ' + cls(v - prev.total) + '">' +
              (v - prev.total >= 0 ? '+' : '−') + yuan(Math.abs(v - prev.total)) + '</span></div>' : '') +
            '<div class="ct-r"><span class="ct-k">较第一条（' + series[0].date + '）</span><span class="ct-v ' + cls(v - base) + '">' +
              (v - base >= 0 ? '+' : '−') + yuan(Math.abs(v - base)) + '（' + pct(base ? (v - base) / base * 100 : null) + '）</span></div>';
        }})
    : '';
  const first = series.length ? series[0] : null;
  const last = series.length ? series[series.length-1] : null;
  const flow = (first && last) ? (last.total - first.total) : null;

  return '<section class="card" data-page="book" id="sec-bk-pos">' +
    '<div class="card-head"><h2><i class="no">2</i>我的持仓与净值</h2>' +
    '<span class="stamp">' + (series.length ? ('快照 ' + series.length + ' 条 · ' + first.date + ' 起') : '还没有快照') + '</span></div>' +
    '<p class="lead">上面那张表是台账算出来的，你不用再填一遍。' +
    '<b>总资产 = 持仓市值 + 现金</b>；每天打开页面会自动存一条快照，攒够两天就能画出下面这条线。' +
    (t.est ? '（<b>现价暂时取不到</b>，标了「成本价」的那几只按成本计，不影响份数与成本。）' : '') + '</p>' +
    '<div class="bk-hero">' +
      '<div class="bh"><span class="k">总资产</span><b class="v">' + yuan(t.total) + '</b>' +
        '<em>' + (t.est ? '含按成本计的持仓' : '持仓市值 ' + yuan(t.mv) + ' ＋ 现金') + '</em></div>' +
      '<div class="bh"><span class="k">持仓成本</span><b class="v">' + yuan(t.cost) + '</b>' +
        '<em>你投进去的钱</em></div>' +
      '<div class="bh"><span class="k">浮动盈亏</span><b class="v ' + cls(t.mv - t.cost) + '">' +
        ((t.mv - t.cost) >= 0 ? '+' : '−') + yuan(Math.abs(t.mv - t.cost)) + '</b>' +
        /* ⚠️ 现价取不到时全部按成本计 ⇒ 这里必然是 +0.00，直接写百分比会被误读成「真没赚没亏」 */
        '<em' + (t.est ? '' : ' class="' + cls(t.mv - t.cost) + '"') + '>' +
          (t.est ? '现价取不到，按成本计' : pct(t.cost > 0 ? (t.mv - t.cost) / t.cost * 100 : null)) +
        '</em></div>' +
      '<div class="bh"><span class="k">现金</span>' +
        '<input class="bh-in" id="bkCash" value="' + esc(CASH) + '" inputmode="decimal" title="没买成股票的现金，默认 0">' +
        '<em>不计入持仓</em></div>' +
    '</div>' +
    '<div class="lg-wrap" style="margin-top:14px"><table class="hold"><thead><tr>' +
      '<th>标的</th><th class="num">持仓份数</th><th class="num">成本价</th><th class="num">现价</th>' +
      '<th class="num">市值</th><th class="num">浮动盈亏</th><th class="num">较成本涨跌</th><th class="num">占总资产</th>' +
    '</tr></thead><tbody id="bkPos">' + rows + '</tbody></table></div>' +
    '<div class="subhead">总资产曲线 · 每天一条快照</div>' +
    (chart ? chart : '<div class="bk-empty">还只有 ' + series.length + ' 条快照，画不成线。' +
      '点下面的按钮存一条今天的，明天再存一条就有了——<b>净值曲线是攒出来的，越早开始越好。</b></div>') +
    (first && last && series.length >= 2
      ? '<div class="chart-meta"><span>最早 ' + first.date + ' <b>' + yuan(first.total) + '</b></span>' +
        '<span>最新 ' + last.date + ' <b>' + yuan(last.total) + '</b></span>' +
        '<span>区间变动 <b class="' + cls(flow) + '">' + (flow >= 0 ? '+' : '−') + yuan(Math.abs(flow)) + '</b></span></div>'
      : '') +
    '<div class="bk-bar" style="margin-top:12px">' +
      '<button type="button" class="bk-btn primary" id="bkSnap">存一条今天的快照</button>' +
      '<button type="button" class="bk-btn danger" id="bkSnapClear">清空全部快照</button>' +
      '<span class="lk-state" id="bkSnapState"></span>' +
      '<span class="bk-sep"></span>' +
      '<button type="button" class="bk-btn" id="bkExport">导出我的数据</button>' +
      /* 正式站点自己就在云上，只有本地那份需要「送上去」这一步 */
      (CLOUD_CFG.endpoint ? '' : '<button type="button" class="bk-btn" id="bkCloud">备份到网上</button>') +
      '<button type="button" class="bk-btn" id="bkImport">导入数据</button>' +
      '<input type="file" id="bkFile" accept=".json,application/json" hidden>' +
    '</div>' +
    '<p class="hint"><b>快照是什么</b>：把「此刻的总资产 + 每只标的的份数、成本、现价」存一条记录。' +
    '它不是交易，只是记账——所以同一天重复存只会覆盖，不会多出一条。' +
    cloudStoreText() + '<span id="bkExpAt">' + bkExpText() + '</span></p>' +
    '</section>';
}
/* 一次快照：同一天覆盖，不追加 */
function takeSnapshot(force){
  const t = bookTotals();
  if(!t.pos.length){ return false; }
  const d = todayStr();
  SNAPS[d] = {
    date: d, total: t.total, cash: CASH, est: t.est,
    rows: t.pos.map(p => ({code:p.code, name:p.name, qty:p.qty, cost:p.cost, price:p.price, mv:p.mv,
                           pnl: p.mv - p.qty * p.cost}))
  };
  saveSnaps();
  return true;
}
/* 打开页面时自动存一条（当天已有就不动），保证曲线自然会往前走 */
function autoSnapshot(){
  if(SNAPS[todayStr()]) return;
  if(!derivePositions().filter(p => p.qty > 0).length) return;
  if(takeSnapshot()) paintBook();
}
function paintBook(){ refreshBookTabs(); }
function refreshBookTabs(){
  const s = document.getElementById('sec-bk-pos');
  if(!s) return;
  const keepId = (document.activeElement && document.activeElement.id) || '';
  const keepCode = (document.activeElement && document.activeElement.dataset)
    ? (document.activeElement.dataset.i + '|' + document.activeElement.dataset.k) : '';
  const tmp = document.createElement('div');
  tmp.innerHTML = secBookPos();
  s.parentNode.replaceChild(tmp.firstChild, s);
  bindBook();
  fixSecNos();
  /* 重绘的是「持仓与净值」那一段，正常不涉及正在输入的格子；
     万一焦点落在里面（现金框），把它还回去。 */
  if(keepId === 'bkCash'){ const c = document.getElementById('bkCash'); if(c) c.focus(); }
}

/* ── 8.3 复盘日记 ─────────────────────────────────────────── */
function secBookJournal(){
  const today = todayStr();
  const cur = JOURNAL[today] || {dec:'', why:'', mood:''};
  const keys = Object.keys(JOURNAL).filter(k => (JOURNAL[k].dec || JOURNAL[k].why || JOURNAL[k].mood)).sort().reverse();
  const past = keys.filter(k => k !== today).slice(0, 14);
  return '<section class="card" data-page="book" id="sec-bk-journal">' +
    '<div class="card-head"><h2><i class="no">3</i>复盘日记 · 我当时怎么想的</h2>' +
    '<span class="stamp">' + (keys.length ? ('已记 ' + keys.length + ' 天') : '还没有记录') + '</span></div>' +
    '<p class="lead">这一块不产生任何数字，但它是<b>账本里唯一记下「你当时怎么想」的地方</b>：' +
    '行情每天都在变，你怎么想的一旦不写下来，过一个月就彻底忘了。' +
    '三句话就够——<b>我做了什么决定、为什么、当时心情如何</b>。</p>' +
    '<div class="jrn"><div class="jrn-day">' + today + '</div>' +
      '<label class="jrn-row"><span>我今天做的决定</span>' +
        '<input id="jrnDec" value="' + esc(cur.dec) + '" placeholder="例：把沪深300ETF 的红线从 4.00 调到 3.95"></label>' +
      '<label class="jrn-row"><span>为什么这么决定</span>' +
        '<textarea id="jrnWhy" rows="2" placeholder="例：连着三天下跌、成交也在缩小，但还没跌到我定的纪律红线。我把红线从 4.00 放到 3.95，免得被一天的插针误触发">' + esc(cur.why) + '</textarea></label>' +
      '<div class="jrn-row"><span>当时的心情</span><div class="chips" id="jrnMood">' +
        MOODS.map(m => '<button type="button" class="chip' + (cur.mood === m ? ' on' : '') +
          '" data-mood="' + m + '">' + m + '</button>').join('') +
      '</div></div>' +
      '<div class="jrn-foot"><span class="lk-state" id="jrnState"></span>' +
        '<button type="button" class="bk-btn" id="jrnSave">存下今天这一条</button></div>' +
    '</div>' +
    '<div class="subhead">以前记的</div>' +
    (past.length
      ? '<div class="jrn-list">' + past.map(k => {
          const j = JOURNAL[k];
          return '<div class="jl"><div class="jl-d">' + k +
            (j.mood ? '<em class="jl-mood">' + esc(j.mood) + '</em>' : '') + '</div>' +
            (j.dec ? '<p><b>决定</b>' + esc(j.dec) + '</p>' : '') +
            (j.why ? '<p><b>理由</b>' + esc(j.why) + '</p>' : '') + '</div>';
        }).join('') + '</div>'
      : '<div class="bk-empty">还没有以前记录。今天写一条，明天这里就会有了。</div>') +
    '<p class="hint"><b>回看时能多知道一件事</b>：' +
    '当时是<b>想清楚了才动手</b>，还是<b>被行情推着动手</b>。这一步没法自动算，只能靠写。</p>' +
    '</section>';
}

/* ── 8.35 一次性搬家：本机 → 网上（不经过账号登录）─────────────
   账号登录只接受 https 正式站点，本地打开的这一份永远登不上；
   所以换设备 / 想在手机上看的走这条路：把账本编进网址，在正式站点那头确认导入。
   三点取舍：
   · 数据放在 # 后面而不是查询串——hash 不会发给服务器，也不进访问日志；
   · 超过网址能装的长度就老实退回「导出文件」，绝不截断（截断一次就再也读不回来）；
   · 读不出来也要先把 hash 清掉，否则每刷新一次就弹一次。 */
const BACKUP_SITE = 'https://newbie-market-dashboard.app.workbuddy.host/';
const BACKUP_MAX = 60000;          // 整个网址的字符上限，留足余量

function backupPayload(){
  return {v:1, exportedAt:new Date().toISOString(),
          ledger:LEDGER, snaps:SNAPS, journal:JOURNAL, cash:CASH};
}
function backupCount(o){
  const l = Array.isArray(o && o.ledger) ? o.ledger.length : 0;
  const s = (o && o.snaps && Object.keys(o.snaps).length) || 0;
  const j = (o && o.journal && Object.keys(o.journal).length) || 0;
  return {l:l, s:s, j:j};
}
function backupLine(c){ return c.l + ' 笔台账、' + c.s + ' 条快照、' + c.j + ' 天日记'; }
/* UTF-8 安全的 base64url：btoa 只吃单字节，中文必须先转成字节 */
function b64uEnc(str){
  const bytes = new TextEncoder().encode(str);
  let bin = '';
  for(let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function b64uDec(s){
  const b = String(s).replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(b + '==='.slice(0, (4 - b.length % 4) % 4));
  const bytes = new Uint8Array(bin.length);
  for(let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}
function applyBackup(o){
  if(!o || typeof o !== 'object') throw new Error('备份内容不是对象');
  if(Array.isArray(o.ledger)) LEDGER = o.ledger;
  if(o.snaps && typeof o.snaps === 'object') SNAPS = o.snaps;
  if(o.journal && typeof o.journal === 'object') JOURNAL = o.journal;
  if(typeof o.cash === 'number') CASH = o.cash;
  saveLedger(); saveSnaps(); saveJournal(); saveCash();
  loadBook(); initBookSections(); needQuotes();
}
/* 备份到网上：本地那份才有这个按钮；正式站点自己就在云上，不需要 */
function backupOnline(){
  const o = backupPayload(), c = backupCount(o);
  if(!c.l && !c.s && !c.j){
    window.alert('这台电脑上还没有记账——先在「交易台账」记一笔买入，再来备份。');
    return false;
  }
  let u = '';
  try{ u = BACKUP_SITE + '#backup=' + b64uEnc(JSON.stringify(o)); }catch(e){ u = ''; }
  if(!u){
    window.alert('打包失败（浏览器版本太旧）。请改用「导出我的数据」存成文件，再在网页里点「导入数据」。');
    return false;
  }
  if(u.length > BACKUP_MAX){
    window.alert('这次的数据有 ' + Math.round(u.length / 1024) + ' KB，网址装不下。\n' +
                 '请点「导出我的数据」存成文件，然后在打开的网页里点「导入数据」选它——效果一样。');
    return false;
  }
  if(!window.confirm('把本机的 ' + backupLine(c) + ' 送到网上？\n\n' +
      '会打开一个网页，在那边点一次「确认导入」就存好了。\n' +
      '这台电脑上的数据不受影响。')) return false;
  const w = window.open(u, '_blank');
  if(!w) window.alert('浏览器拦住了新窗口。请允许这个页面弹窗，或改用「导出我的数据」。');
  return true;
}
/* 正式站点那头：网址里带了备份，先问一句再导入 */
function tryRestoreFromHash(){
  const m = /[#&]backup=([A-Za-z0-9_-]+)/.exec(location.hash || '');
  if(!m) return false;
  let o = null;
  try{ o = JSON.parse(b64uDec(m[1])); }catch(e){ o = null; }
  try{ history.replaceState(null, '', location.pathname + location.search); }catch(e){ location.hash = ''; }
  if(!o){
    window.alert('这个备份链接没读出来（多半是复制时被截断了）。请回到原来那台电脑，改用「导出我的数据」存成文件。');
    return false;
  }
  const c = backupCount(o);
  if(!window.confirm('收到一份来自本机的备份：' + backupLine(c) + '。\n\n' +
      '导入会覆盖这个网页里现有的账本（原来那台电脑上的不受影响）。确定导入吗？' +
      (CLOUD.user ? '' : '\n\n导入后点一次「使用 WorkBuddy 登录」，这份才会存到云端。'))) return false;
  applyBackup(o);
  return true;
}

/* ── 8.4 初始化与事件 ─────────────────────────────────────── */
function initBook(){
  loadBook();
  bindBook();
  needQuotes();
  autoSnapshot();
}
function bindBook(){
  const lg = document.getElementById('lgRows');
  if(lg && !lg._bk){
    lg._bk = 1;
    lg.addEventListener('input', e => {
      const el = e.target; if(!el || !el.dataset || el.dataset.i == null) return;
      const i = +el.dataset.i, k = el.dataset.k;
      if(!LEDGER[i]) return;
      LEDGER[i][k] = el.value;
      saveLedger();
      paintLgDerived();                       // 只重绘推导列，输入框不动 ⇒ 焦点不丢
      if(k === 'code'){
        const el2 = document.getElementById('lgk-' + i);
        if(el2){ el2.className = 'lk-state'; el2.textContent = ''; }
        /* ⚠️ 代码是「归并持仓」的唯一钥匙，改它必须立刻重算：
           否则「名称查不到（场外基金 / 断网）」时，填了代码却看不到算出来的持仓。 */
        paintLgDerived();
        refreshBookTabs();
        clearTimeout(_lgT[i]);
        _lgT[i] = setTimeout(() => lookupLedger(i), 650);
      } else if(k === 'price' || k === 'qty'){
        refreshBookTabs();                       // 本地推导，立即生效（不碰输入框，焦点不丢）
        clearTimeout(_lgT['q' + i]);
        _lgT['q' + i] = setTimeout(needQuotes, 700);   // 只有「联网取现价」这一步才需要防抖
      }
    });
    lg.addEventListener('click', e => {
      const t = e.target; if(!t || !t.closest) return;
      const del = t.closest('button[data-lgdel]');
      if(del){ lgDelRow(+del.dataset.lgdel); return; }
      const sd = t.closest('button[data-side]');
      if(sd){ const i = +sd.dataset.lgi; if(LEDGER[i]){ LEDGER[i].side = sd.dataset.side; saveLedger();
        const box = sd.parentNode;
        Array.prototype.forEach.call(box.children, b => b.classList.toggle('on', b.dataset.side === LEDGER[i].side));
        paintLgDerived(); refreshBookTabs(); } }
    });
  }
  const add = document.getElementById('lgAdd');
  if(add && !add._bk){ add._bk = 1; add.onclick = lgAddRow; }
  const demo = document.getElementById('lgDemo');
  if(demo && !demo._bk){ demo._bk = 1; demo.onclick = () => {
    const d = todayStr();
    LEDGER.push({date:d, code:'510300', mkt:'sh', name:'沪深300ETF', side:'buy', price:'4.236', qty:'1000'});
    LEDGER.push({date:d, code:'588000', mkt:'sh', name:'科创50ETF',  side:'buy', price:'1.556', qty:'2000'});
    saveLedger(); initBookSections(); needQuotes();
  }; }
  const clr = document.getElementById('lgClear');
  if(clr && !clr._bk){ clr._bk = 1; clr.onclick = () => {
    if(!LEDGER.length) return;
    if(!window.confirm('清空台账？这会删掉全部交易流水（持仓和成本也会跟着没了）。快照和日记不受影响。')) return;
    LEDGER = []; saveLedger(); initBookSections();
  }; }
  const sy = document.getElementById('lgSync');
  if(sy && !sy._bk){ sy._bk = 1; sy.onclick = syncToWatch; }

  const cash = document.getElementById('bkCash');
  if(cash && !cash._bk){ cash._bk = 1;
    cash.addEventListener('input', () => {
      CASH = N(cash.value) || 0; saveCash();
      clearTimeout(_jT.cash);
      _jT.cash = setTimeout(() => { const t = bookTotals();
        const hero = cash.closest('.bk-hero');
        if(hero && hero.children[0]){ hero.children[0].querySelector('.v').textContent = yuan(t.total);
          hero.children[0].querySelector('em').textContent = t.est ? '含按成本计的持仓' : '持仓市值 ' + yuan(t.mv) + ' ＋ 现金';
          hero.children[2].querySelector('.v').textContent = ((t.mv - t.cost) >= 0 ? '+' : '−') + yuan(Math.abs(t.mv - t.cost)); }
      }, 500);
    });
  }
  const sn = document.getElementById('bkSnap');
  if(sn && !sn._bk){ sn._bk = 1; sn.onclick = () => {
    /* ⚠️ refreshBookTabs() 会把整段换掉，所以「回执」要等换完再找元素 */
    if(!takeSnapshot()){
      const st0 = document.getElementById('bkSnapState');
      if(st0){ st0.className = 'lk-state busy'; st0.textContent = '还没有持仓，先在上一段记一笔买入'; }
      return;
    }
    refreshBookTabs();
    const st = document.getElementById('bkSnapState');
    if(st){ st.className = 'lk-state ok'; st.textContent = '已存 ' + todayStr(); }
  }; }
  const sc = document.getElementById('bkSnapClear');
  if(sc && !sc._bk){ sc._bk = 1; sc.onclick = () => {
    if(!Object.keys(SNAPS).length) return;
    if(!window.confirm('清空全部快照？总资产曲线会从零开始（台账和日记不受影响）。')) return;
    SNAPS = {}; saveSnaps(); refreshBookTabs();
  }; }
  const ex = document.getElementById('bkExport');
  if(ex && !ex._bk){ ex._bk = 1; ex.onclick = () => {
    const blob = JSON.stringify({v:1, exportedAt:new Date().toISOString(),
      ledger:LEDGER, snaps:SNAPS, journal:JOURNAL, cash:CASH}, null, 2);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([blob], {type:'application/json'}));
    a.download = '我的账本-' + todayStr() + '.json';
    document.body.appendChild(a); a.click(); a.remove();
    /* D3 · 记下这次导出时间，账本页同步显示「上次导出备份」 */
    try{
      const ts = new Date().toLocaleString('zh-CN', {hour12:false});
      localStorage.setItem('mb_bk_exp', ts);
      const ea = document.getElementById('bkExpAt');
      if(ea) ea.textContent = '上次导出备份：' + ts + '。';
    }catch(e){}
  }; }
  const cb = document.getElementById('bkCloud');
  if(cb && !cb._bk){ cb._bk = 1; cb.onclick = () => { backupOnline(); }; }
  const im = document.getElementById('bkImport');
  const fl = document.getElementById('bkFile');
  if(im && fl && !im._bk){ im._bk = 1; im.onclick = () => fl.click(); }
  if(fl && !fl._bk){ fl._bk = 1; fl.onchange = () => {
    const f = fl.files && fl.files[0]; if(!f) return;
    const rd = new FileReader();
    rd.onload = () => {
      try{
        applyBackup(JSON.parse(rd.result));   // 文件导入与「备份到网上」走同一套落地逻辑
      }catch(e){ window.alert('这个文件读不出来，请选账本导出的那个 JSON。'); }
      fl.value = '';
    };
    rd.readAsText(f);
  }; }
  const mood = document.getElementById('jrnMood');
  if(mood && !mood._bk){ mood._bk = 1;
    mood.addEventListener('click', e => {
      const b = e.target && e.target.closest ? e.target.closest('.chip') : null; if(!b) return;
      const d = todayStr(), j = JOURNAL[d] || (JOURNAL[d] = {dec:'', why:'', mood:''});
      j.mood = (j.mood === b.dataset.mood) ? '' : b.dataset.mood;
      saveJournal();
      Array.prototype.forEach.call(mood.children, c => c.classList.toggle('on', c.dataset.mood === j.mood));
    });
  }
  const save = document.getElementById('jrnSave');
  if(save && !save._bk){ save._bk = 1; save.onclick = () => {
    const d = todayStr();
    const dec = (document.getElementById('jrnDec') || {}).value || '';
    const why = (document.getElementById('jrnWhy') || {}).value || '';
    const j = JOURNAL[d] || {dec:'', why:'', mood:''};
    j.dec = dec; j.why = why;
    JOURNAL[d] = j; saveJournal();
    initBookSections();                       // ⚠️ 先重绘，再写回执：回执元素在重绘时才会被换掉
    const st = document.getElementById('jrnState');
    if(st){ st.className = 'lk-state ok'; st.textContent = '已存下 ' + d; }
  }; }
}
/* 整块重绘本页（台账 / 持仓 / 日记三段一起） */
function initBookSections(){
  loadBook();
  const wrap = document.querySelector('#root > [data-page="book"]');
  if(!wrap) return;
  wrap.innerHTML = cloudBarHTML() + secBookLedger() + secBookPos() + secBookJournal();
  bindBook();
  paintCloudBar();
  fixSecNos();
  needQuotes();
}
/* 把台账算出来的持仓写进「今天 · 我的持仓监控」，省得再手输一遍 */
function syncToWatch(){
  const pos = derivePositions().filter(p => p.qty > 0);
  if(!pos.length){ window.alert('台账里还没有持仓，先在「交易台账」记一笔买入。'); return; }
  if(!window.confirm('把台账算出来的 ' + pos.length + ' 只持仓写进「今天 · 我的持仓监控」？\n' +
                     '注意：监控里原有的行会被替换掉，只有「纪律红线」和「下一档触发价」两列会保留你原来填的数字。')) return;
  const old = {};
  ROWS.forEach(r => { if(r && r.code) old[stripCode(r.code)] = r; });
  ROWS = pos.map(p => {
    const o = old[p.code] || {};
    return normRow({name:p.name, code:p.code, mkt:p.mkt, cost:String(p.cost),
                    red:o.red || '', next:o.next || '', price:o.price || ''});
  });
  saveRows();
  try{ localStorage.setItem(PAGE_KEY, 'today'); }catch(e){}
  loadPage();
  window.scrollTo(0, 0);
  setPage('today');
  initHoldings();
  refreshAllQuotes();
  window.alert('已同步 ' + pos.length + ' 只到持仓监控（在「今天」页第一段）。');
}

/* ── 目录导航（同时就是这一页的阅读路径） ──
   六个格子归成三组：关于我 → 今天的市场（全景 + 三个切面）→ 背景坐标。
   分组只体现在分隔符与 title 上，格子数与编号不变。 */
/* 一级分页：当天的事放「今天」，过去的事放「历史与位置」，自己的钱放「我的账本」。
   三块内容都渲染在 DOM 里，只靠 #root 的 data-page 切显隐——
   这样做的好处是切页不会重建表单，正在填的内容不会丢。 */
const PAGE_KEY = 'mb_page';
const PAGES = [
  {k:'today', label:'今天',       sub:'当天的行情'},
  {k:'hist',  label:'历史与位置', sub:'近 60 个交易日'},
  {k:'book',  label:'我的账本',   sub:'自己的钱'}
];
let PAGE = 'today';
function loadPage(){ try{ const v = localStorage.getItem(PAGE_KEY); if(PAGES.some(p => p.k === v)) PAGE = v; }catch(e){} }
function savePage(){ try{ localStorage.setItem(PAGE_KEY, PAGE); }catch(e){} }

/* 目录条：每一页各有一份。**不写死编号**——编号在 paintToc 里按实际渲染出来的段
   重新排（某段因数据不足自隐藏时，后面的编号自动往前挪，不会出现「目录说 4 段、
   页面上只有 3 段」的错位）。 */
const TOCS = {
  today: [
    /* 顺序对齐主流交易 App：先「跟我有关」，再从整体涨跌 → 谁在涨 → 钱怎么动
       → 定时复盘总结。某段数据缺失自隐藏时，编号由 fixSecNos 重排。 */
    {id:'sec-watch',  t:'我的持仓',       s:'跟我有关的', g:'关于我'},
    {id:'sec-global', t:'全球市场',       s:'点标签切换', g:'今天的市场'},
    {id:'sec-senti',  t:'市场情绪',       s:'是不是普涨', g:'今天的市场'},
    {id:'sec-board',  t:'板块题材',       s:'谁在涨',     g:'今天的市场'},
    {id:'sec-fund',   t:'资金流向',       s:'钱怎么动的', g:'今天的市场'},
    {id:'sec-market', t:'今天发生了什么', s:'按市场复盘', g:'今天的市场'}
  ],
  hist: [
    {id:'sec-h-main',  t:'指数位置',   s:'近 60 个交易日', g:'我处在哪'},
    {id:'sec-h-vol',   t:'成交额',     s:'今天算大算小',   g:'这几天的变化'},
    {id:'sec-h-fund',  t:'资金',       s:'钱是进是出',     g:'这几天的变化'},
    {id:'sec-h-globe', t:'全球长周期', s:'中国香港 · 美国', g:'外面的世界'}
  ],
  book: [
    {id:'sec-bk-ledger',  t:'交易台账',   s:'买了什么',    g:'我的钱'},
    {id:'sec-bk-pos',     t:'持仓与净值', s:'现在值多少',  g:'我的钱'},
    {id:'sec-bk-journal', t:'复盘日记',   s:'当时怎么想',  g:'我的记录'}
  ]
};
/* ══ 显示内容分层：精简 / 完整 / 自定义 ══════════════════════════
   给新手少看一点的权利：数据密集的三段（全球市场 / 板块题材 / 资金流向）
   在「精简」档收起；必看三段（持仓监控 / 市场情绪 / 今天发生了什么）任何档位都显示。
   实现不做重渲染——只给 section 加 .lay-off（display:none），目录由 curToc 过滤掉。 */
const LAYKEY = 'mb_layer_v1';
const LOCKED = ['sec-watch', 'sec-senti', 'sec-market'];
const LITE_HIDE = ['sec-global', 'sec-board', 'sec-fund', 'sec-h-fund', 'sec-h-globe'];
let LAY = { level: 'full', hidden: LITE_HIDE.slice() };

function loadLayer(){
  let o = null;
  try{ o = JSON.parse(localStorage.getItem(LAYKEY) || 'null'); }catch(e){ o = null; }
  LAY.level = (o && ['lite','full','custom'].indexOf(o.level) >= 0) ? o.level : 'full';
  LAY.hidden = (o && Array.isArray(o.hidden))
    ? o.hidden.filter(x => typeof x === 'string') : LITE_HIDE.slice();
}
function saveLayer(){ try{ localStorage.setItem(LAYKEY, JSON.stringify(LAY)); }catch(e){} }
/* 这一段该不该显示：必看段永远显示；完整档全显示；精简档按 LITE_HIDE；自定义按勾选 */
function secVisible(id){
  if(LOCKED.indexOf(id) >= 0) return true;
  if(LAY.level === 'full') return true;
  const hide = (LAY.level === 'lite') ? LITE_HIDE : LAY.hidden;
  return hide.indexOf(id) < 0;
}
function applyLayering(){
  Object.keys(TOCS).forEach(pg => TOCS[pg].forEach(x => {
    const el = document.getElementById(x.id);
    if(el) el.classList.toggle('lay-off', !secVisible(x.id));
  }));
  paintToc();          // 目录跟着多/少几项，段编号自动重排（fixSecNos 在 paintToc 里）
  paintTocActive();
}

/* ══ 精简档把「读之前」的东西折起来 ══════════════════════════════
   页头的四行长说明、术语解释 + 连板梯队表，在精简档里默认收起，点标题才展开；
   完整档默认展开。⚠️ 折叠状态存在 FOLD_OPEN 里而不是直接读 DOM：
   整页每 60 秒重画一次，重画会把 <details> 重建回默认状态，
   不记住的话读者刚点开的那一下就没了。 */
const FOLD_IDS = ['topNote', 'foldLadder'];
let FOLD_OPEN = null;          // null = 还没按档位初始化过
function defaultFolds(){
  const o = {};
  FOLD_IDS.forEach(id => { o[id] = (LAY.level !== 'lite'); });
  return o;
}
function applyFolds(){
  if(!FOLD_OPEN) FOLD_OPEN = defaultFolds();
  FOLD_IDS.forEach(id => {
    const el = document.getElementById(id);
    if(!el) return;
    if(!el.dataset.wired){
      el.dataset.wired = '1';
      el.addEventListener('toggle', () => { FOLD_OPEN[id] = el.open; });
    }
    if(el.open !== FOLD_OPEN[id]) el.open = FOLD_OPEN[id];
  });
}
function setLayer(k){
  if(['lite','full','custom'].indexOf(k) < 0) return;
  LAY.level = k;
  if(k === 'custom' && !LAY.hidden.length) LAY.hidden = LITE_HIDE.slice();
  FOLD_OPEN = defaultFolds();     // 换档位就按新档位的默认值来，读者自己点过的状态作废
  saveLayer(); paintLayBar(); applyLayering(); applyFolds();
}
function paintLayBar(){
  const bar = document.getElementById('layBar'); if(!bar) return;
  const chips = [['lite','精简'], ['full','完整'], ['custom','自定义']].map(([k, l]) =>
    '<button type="button" class="chip' + (LAY.level === k ? ' on' : '') + '" data-lay="' + k + '">' + l + '</button>').join('');
  /* 段数按实际算，不写死——删过一整段（快讯）却忘了改这句，页面上就多了个不存在的段 */
  const liteN = TOCS.today.filter(x => LITE_HIDE.indexOf(x.id) < 0).length;
  const note = LAY.level === 'lite'
    ? '<span class="lay-note">只留 ' + liteN + ' 段：跟我有关的、今天普不普涨、今天发生了什么</span>'
    : (LAY.level === 'full'
        ? '<span class="lay-note">今天页 ' + TOCS.today.length + ' 段全显示（想少看点就选「精简」）</span>'
        : '<span class="lay-note">勾上的才显示；「固定显示」那几段关不掉</span>');
  const opt = pg => TOCS[pg].filter(x => LOCKED.indexOf(x.id) < 0);
  const panel = LAY.level !== 'custom' ? '' :
    '<div class="lay-panel">' +
      ['today','hist'].map(pg =>
        '<div class="lay-g"><b>' + (pg === 'today' ? '今天页' : '历史页') + '</b>' +
          opt(pg).map(x =>
            '<label class="lay-item"><input type="checkbox" data-sec="' + x.id + '"' +
              (LAY.hidden.indexOf(x.id) < 0 ? ' checked' : '') + '>' + esc(x.t) +
              '<em>' + esc(x.s) + '</em></label>').join('') + '</div>').join('') +
      '<div class="lay-g"><b>固定显示</b>' +
        LOCKED.map(id => {
          const x = TOCS.today.filter(y => y.id === id)[0];
          return '<span class="lay-lock">' + esc(x ? x.t : id) + '</span>';
        }).join('') + '</div>' +
    '</div>';
  bar.innerHTML = '<span class="lay-t">显示内容</span>' + '<div class="lay-chips">' + chips + '</div>' + note + panel;
  if(!bar._b){
    bar._b = 1;
    bar.onclick = e => {
      const b = e.target && e.target.closest ? e.target.closest('[data-lay]') : null;
      if(b) setLayer(b.getAttribute('data-lay'));
    };
    bar.onchange = e => {
      const cb = e.target;
      const id = cb && cb.getAttribute ? cb.getAttribute('data-sec') : null;
      if(!id) return;
      const i = LAY.hidden.indexOf(id);
      if(cb.checked){ if(i >= 0) LAY.hidden.splice(i, 1); }
      else if(i < 0) LAY.hidden.push(id);
      saveLayer(); applyLayering();
    };
  }
}

/** 目录只列「真的渲染出来了、且没被显示设置关掉」的段（不空指一个看不到的锚点） */
function curToc(){
  const all = TOCS[PAGE] || TOCS.today;
  const vis = all.filter(x => {
    const el = document.getElementById(x.id);
    return el && !el.classList.contains('lay-off');
  });
  return vis.length ? vis : all;
}

function paintPages(){
  const box = document.getElementById('pageTabs');
  if(!box) return;
  box.innerHTML = PAGES.map(p =>
    '<button class="pt' + (p.k === PAGE ? ' on' : '') + '" data-page="' + p.k + '">' +
    esc(p.label) + '<em>' + esc(p.sub) + '</em></button>').join('');
  box.onclick = e => {
    const b = e.target && e.target.closest ? e.target.closest('.pt') : null;
    if(!b) return;
    const k = b.getAttribute('data-page');
    if(k && k !== PAGE) setPage(k);
  };
}
function setPage(k){
  PAGE = PAGES.some(p => p.k === k) ? k : 'today';
  savePage();
  const root = document.getElementById('root');
  if(root) root.setAttribute('data-page', PAGE);
  paintPages();
  paintToc();
  paintTocActive();
  hideChartTip();
  window.scrollTo(0, 0);
}

function paintToc(){
  fixSecNos();          // 段标题编号跟着目录走（切页 / 某段自隐藏时都要重排）
  const nav = document.getElementById('toc');
  if(!nav) return;
  const toc = curToc();
  nav.innerHTML = toc.map((x, i) => {
    const sep = (i > 0 && toc[i-1].g !== x.g)
      ? '<span class="toc-sep" title="' + x.g + '"></span>' : '';
    return sep + '<a class="toc-item" data-go="' + x.id + '" title="' + x.g + ' · ' + x.t + ' · ' + x.s + '">' +
      '<i>' + (i + 1) + '</i>' + x.t + '<em>' + x.s + '</em></a>';
  }).join('');
  nav.onclick = e => {
    const a = e.target && e.target.closest ? e.target.closest('.toc-item') : null;
    if(!a) return;
    const el = document.getElementById(a.dataset.go);
    if(el) el.scrollIntoView({behavior:'smooth', block:'start'});
  };
  if(!window._tocScroll){
    window._tocScroll = 1;
    // 用定时器节流而不是 requestAnimationFrame：无头/无合成帧的环境下 rAF 回调不会执行，
    // 高亮会一直停在初始值；定时器在两边都可靠，60ms 对滚动高亮完全够用。
    let tmr = 0;
    window.addEventListener('scroll', () => {
      if(tmr) return;
      tmr = setTimeout(() => { tmr = 0; paintTocActive(); }, 60);
    }, {passive:true});
  }
}
function paintTocActive(){
  const items = document.querySelectorAll('.toc-item');
  if(!items.length) return;
  const toc = curToc();
  const y = window.scrollY + 160;   // 160 ≈ 页签 + 目录条高度 + 一点余量
  let cur = null;
  toc.forEach(x => {
    const el = document.getElementById(x.id);
    if(el && el.getBoundingClientRect().height > 0 &&
       (el.getBoundingClientRect().top + window.scrollY) <= y) cur = x.id;
  });
  if(!cur) cur = toc[0].id;   // 还在页首、第一段都没进阈值时，默认点亮第一段
  // 滚到最底时，无论怎样都点亮最后一段
  const full = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
  if(window.innerHeight + window.scrollY >= full - 8) cur = toc[toc.length-1].id;
  items.forEach(a => a.classList.toggle('on', a.dataset.go === cur));
}

/** 段标题里那个大编号必须和目录一一对上：某段自隐藏时，后面的编号跟着往前挪。
    段 HTML 里的编号只是「正常情况下的默认值」，这里统一按实际可见顺序改写。 */
function fixSecNos(){
  curToc().forEach((x, i) => {
    const el = document.getElementById(x.id);
    if(!el) return;
    const no = el.querySelector('.card-head h2 .no');
    if(no) no.textContent = String(i + 1);
  });
}

function renderAll(m){
  if(!m.ok){ document.getElementById('root').innerHTML = '<div class="card"><p>数据解析失败，请稍后重试。</p></div>'; return; }
  window.__MODEL = m;
  loadMarket();
  loadReportMkt();
  loadPage();
  /* 两页内容都在 DOM 里（切页只切显隐，不重建 —— 持仓表里正在填的字不会丢）。
     当天的事一块、过去的事一块，不再混在同一屏里。 */
  document.getElementById('root').setAttribute('data-page', PAGE);
  /* 账本的数据来自本地存储，必须先读出来再渲染（否则首屏是空的） */
  loadBook();
  document.getElementById('root').innerHTML =
    '<div data-page="today">' +
      secWatch() + secGlobal(m) + secSenti(m) + secBoard(m) + sec3Fund(m) + sec2Indices(m) +
    '</div>' +
    '<div data-page="hist">' +
      secHMain(m) + secHVol(m) + secHFund(m) + secHLong(m) +
    '</div>' +
    '<div data-page="book">' +
      cloudBarHTML() + secBookLedger() + secBookPos() + secBookJournal() +
    '</div>';
  initHoldings();
  initBook();
  paintCloudBar();     // 账本页顶部那条「云端备份」：先按未登录画出来，登录态晚点覆盖
  /* 网址里带了本机备份：等页面先画出来再问，别对着一片空白点确认 */
  setTimeout(tryRestoreFromHash, 80);
  bindGlobal();
  paintPages();
  bindChartTips();
  loadLayer();         // 显示设置：读上次选的档位，再按段加 .lay-off
  paintLayBar();
  applyLayering();     // 内部会 paintToc（目录与段编号跟着重排）
  applyFolds();        // 重画会把 <details> 重建回默认状态，这里按记住的开合状态还原
  paintToc();
  paintTocActive();
}

/* ══════════════════════════════════════════════════════════════════
   7 · 持仓与纪律线（本地存储）
   ══════════════════════════════════════════════════════════════════ */
const KEY = 'mb_holdings_v1';
const DEMO = [
  {name:'沪深300ETF', code:'510300', mkt:'sh', cost:'4.50', red:'4.00', next:'4.60', price:''},
  {name:'科创50ETF',  code:'588000', mkt:'sh', cost:'1.60', red:'1.35', next:'1.55', price:''}
];
let ROWS = [];
let PICKS = {};          // 行号 → 候选列表
const _dbT = {};         // 输入防抖计时器

/* 代码归一：去掉 sh/sz 前缀与非数字字符 */
function stripCode(s){ return String(s==null?'':s).replace(/[^0-9]/g,''); }
/* 纯数字代码推断市场（查不到搜索结果时的兜底） */
function inferMarket(code){
  const c = stripCode(code);
  if(!c) return '';
  if(/^[569]/.test(c)) return 'sh';        // 6=沪市股票 9=沪市B股 5=沪市基金/ETF
  if(/^11/.test(c)) return 'sh';           // 沪市可转债
  if(/^[0123]/.test(c)) return 'sz';       // 0=深市股票 2=深市B股 3=创业板 1=深市基金
  if(/^(4|8|920)/.test(c)) return 'bj';    // 北交所（新代码已迁到 920xxx）
  return '';
}
function normRow(r){
  const o = Object.assign({name:'',code:'',mkt:'',cost:'',red:'',next:'',price:''}, r || {});
  const m = /^(sh|sz|bj)/i.exec(String(o.code || '').trim());
  if(m) o.mkt = m[1].toLowerCase();
  const d = stripCode(o.code);
  o.code = d;
  if(d && !o.mkt) o.mkt = inferMarket(d);
  return o;
}
function loadRows(){
  let a = null;
  try{ const raw = localStorage.getItem(KEY); a = raw ? JSON.parse(raw) : null; }catch(e){ a = null; }
  if(!Array.isArray(a) || !a.length) a = DEMO.map(r => Object.assign({}, r));
  ROWS = a.map(normRow);
}
function saveRows(){ try{ localStorage.setItem(KEY, JSON.stringify(ROWS)); }catch(e){} dataHintOnce(); cloudTouch(); }

/* 按代码在 quoteMap 里找这只标的的行情对象（现价/涨跌/高低/成交额） */
function quoteOf(row){
  const m = window.__MODEL; if(!m) return null;
  const q = m.quoteMap || {};
  const raw = String((row && row.code) || '').trim();
  const d = stripCode(raw);
  if(!d) return null;
  const keys = [];
  if(row.mkt) keys.push(row.mkt + d);            // 搜索结果确认过的市场最可信
  const im = inferMarket(d);
  if(im) keys.push(im + d);
  if(!row.mkt && !im) keys.push('sh' + d, 'sz' + d, 'bj' + d);   // 兜底：像 000001 这种两市都有的，不猜
  if(raw) keys.push(raw.toLowerCase());
  for(let k = 0; k < keys.length; k++){ const v = q[keys[k]]; if(v && v.price != null) return v; }
  return null;
}
/* 现价：手填优先，其次自动行情 */
function priceOf(row){
  const q = quoteOf(row);
  return q && q.price != null ? q.price : null;
}
function manualPrice(row){
  const v = row && row.price;
  return (v != null && String(v).trim() !== '') ? N(v) : null;
}
function rowTr(i){
  const tb = document.getElementById('hRows');
  return tb ? tb.querySelector('tr[data-row="' + i + '"]') : null;
}
function clearPick(i){
  delete PICKS[i];
  const tb = document.getElementById('hRows'); if(!tb) return;
  const pr = tb.querySelector('tr.pick-tr[data-pick="' + i + '"]');
  if(pr && pr.parentNode) pr.parentNode.removeChild(pr);
}
function setLk(i, c, txt){
  const el = document.getElementById('lk-' + i);
  if(el){ el.className = 'lk-state' + (c ? ' ' + c : ''); el.textContent = txt || ''; }
}
function setHtml(id, html){ const el = document.getElementById(id); if(el) el.innerHTML = html; }

function initHoldings(){
  loadRows();
  loadView();
  PICKS = {};
  const cols = VIEWS[VIEW];
  const th = document.getElementById('hHead');
  const tb = document.getElementById('hRows');
  if(!tb) return;
  if(th) th.innerHTML = '<tr>' + cols.map(k => '<th class="' + COLS[k].cls + '">' + COLS[k].h + '</th>').join('') + '</tr>';
  tb.innerHTML = '';
  if(!ROWS.length){
    tb.innerHTML = '<tr><td colspan="' + cols.length + '" class="muted" style="padding:18px;text-align:center">' +
      '还没有监控标的。点右上角「+ 添加一行」，填个代码就加进来了。</td></tr>';
  }
  ROWS.forEach((row,i) => {
    const tr = document.createElement('tr');
    tr.setAttribute('data-row', i);
    tr.innerHTML = cols.map(k => cellHtml(row, i, k)).join('');
    tb.appendChild(tr);
    refreshRow(i);
  });
  bindHoldings();
  paintWatchMeta();
  document.querySelectorAll('.vs').forEach(b => b.classList.toggle('on', b.dataset.view === VIEW));
}
function refreshRow(i, force){
  const row = ROWS[i]; if(!row) return;
  const ap = priceOf(row);
  const mp = manualPrice(row);
  const px = mp != null ? mp : ap;

  const pxEl = document.getElementById('px-' + i);
  if(pxEl){
    const ae = document.activeElement;
    // 正在这个格子里打字时不要重绘，否则光标会跳走；force 用于「手填 / 用行情」这种主动切换
    const typing = !force && ae && pxEl.contains(ae) && ae.tagName === 'INPUT';
    if(!typing){
      if(mp != null){
        pxEl.innerHTML = '<input data-i="' + i + '" data-k="price" value="' + esc(row.price) + '" placeholder="现价">' +
          (ap != null ? '<a class="lnk" data-auto="' + i + '">用行情</a>' : '');
      } else if(ap != null){
        pxEl.innerHTML = '<input readonly value="' + num(ap, ap>=100?2:3) + '" title="自动取自行情"><span class="badge-src">自动</span>' +
          '<a class="lnk" data-man="' + i + '">手填</a>';
      } else {
        pxEl.innerHTML = '<input data-i="' + i + '" data-k="price" value="' + esc(row.price) + '" placeholder="手填现价">';
      }
    }
  }
  /* 行情派生格 —— 只在监控视图存在；纪律线视图里这些元素没有，setHtml 会自己跳过 */
  const qq = quoteOf(row);
  const dec = v => (v != null && Math.abs(v) >= 100) ? 2 : 3;
  setHtml('chg-' + i, (qq && qq.chgPct != null)
    ? '<span class="' + cls(qq.chgPct) + '">' + ARROW(qq.chgPct) + ' ' + pct(qq.chgPct) + '</span>'
    : '<span class="muted">—</span>');
  setHtml('amt-' + i, (qq && qq.chg != null)
    ? '<span class="' + cls(qq.chg) + '">' + (qq.chg >= 0 ? '+' : '−') + num(Math.abs(qq.chg), dec(qq.chg)) + '</span>'
    : '<span class="muted">—</span>');
  setHtml('rng-' + i, (qq && qq.low != null && qq.high != null)
    ? num(qq.low, dec(qq.low)) + ' ~ ' + num(qq.high, dec(qq.high))
    : '<span class="muted">—</span>');
  setHtml('vol-' + i, (qq && qq.amount != null) ? money(qq.amount) : '<span class="muted">—</span>');

  const cost = N(row.cost), pnlEl = document.getElementById('pnl-' + i);
  if(pnlEl){
    if(px == null || cost == null || cost <= 0){
      pnlEl.innerHTML = '<span class="muted">—</span><span class="sub">缺成本价/现价</span>';
    } else {
      const r = (px - cost) / cost * 100;
      pnlEl.innerHTML = '<span class="' + cls(r) + '">' + pct(r) + '</span><span class="sub">每份 ' + (px - cost >= 0 ? '+' : '') + (px - cost).toFixed(2) + ' 元</span>';
    }
  }
  const red = N(row.red), redEl = document.getElementById('red-' + i);
  if(redEl){
    if(px == null || red == null || px <= 0) redEl.innerHTML = '<span class="muted">—</span>';
    else if(px <= red) redEl.innerHTML = '<span class="tag tag-hit">已跌破红线</span><span class="sub">低于红线 ' + Math.abs((px - red) / red * 100).toFixed(2) + '% · 现价 ' + num(px, px>=100?2:3) + '</span>';
    else redEl.innerHTML = '<span class="flat">距红线还有 ' + ((px - red) / px * 100).toFixed(2) + '%</span><span class="sub">红线价 ' + num(red, red>=100?2:3) + ' · 现价 ' + num(px, px>=100?2:3) + '</span>';
  }
  const tg = N(row.next), trgEl = document.getElementById('trg-' + i);
  if(trgEl){
    if(px == null || tg == null) trgEl.innerHTML = '<span class="muted">—</span>';
    else if(px >= tg) trgEl.innerHTML = '<span class="tag tag-trig">已到价位</span><span class="sub">高 ' + ((px - tg) / tg * 100).toFixed(2) + '%</span>';
    else trgEl.innerHTML = '<span class="flat">还需涨 ' + ((tg - px) / px * 100).toFixed(2) + '%</span><span class="sub">预设 ' + num(tg, tg>=100?2:3) + '</span>';
  }
}

/* 事件全部走委托：输入不再重建 DOM，焦点不会丢 */
function onRowInput(e){
  const t = e.target;
  if(!t || !t.dataset || !t.dataset.k) return;
  const i = +t.dataset.i, k = t.dataset.k;
  if(!ROWS[i]) return;
  ROWS[i][k] = t.value; saveRows();
  if(k === 'code'){
    ROWS[i].mkt = '';
    clearPick(i);
    setLk(i, '', t.value.trim() ? '待查询' : '');
    clearTimeout(_dbT['c' + i]);
    _dbT['c' + i] = setTimeout(() => lookupRow(i, false, 'code'), 600);
    refreshRow(i);
  } else if(k === 'name'){
    clearTimeout(_dbT['n' + i]);
    _dbT['n' + i] = setTimeout(() => lookupRow(i, false, 'name'), 700);
  } else {
    refreshRow(i);
  }
}
function onRowClick(e){
  const t = e.target;
  if(!t || !t.closest) return;
  let el;
  if((el = t.closest('button.pick'))){
    const i = +el.dataset.pickI, n = +el.dataset.pickN, P0 = PICKS[i];
    if(P0 && P0.list && P0.list[n]) applyPick(i, P0.list[n], P0.src);
    return;
  }
  if((el = t.closest('button.pick-x'))){ clearPick(+el.dataset.pickI); setLk(+el.dataset.pickI, '', ''); return; }
  if((el = t.closest('button[data-lk]'))){ const i = +el.dataset.lk; lookupRow(i, true, (ROWS[i] && ROWS[i].code) ? 'code' : 'name'); return; }
  if((el = t.closest('button[data-del]'))){ ROWS.splice(+el.dataset.del, 1); saveRows(); initHoldings(); return; }
  if((el = t.closest('a[data-man]'))){
    const i = +el.dataset.man, ap = priceOf(ROWS[i]);
    ROWS[i].price = ap != null ? String(ap) : ''; saveRows(); refreshRow(i, true);
    const c = document.getElementById('px-' + i), inp = c && c.querySelector('input');
    if(inp){ inp.focus(); if(inp.select) inp.select(); }
    return;
  }
  if((el = t.closest('a[data-auto]'))){ const i = +el.dataset.auto; ROWS[i].price = ''; saveRows(); refreshRow(i, true); return; }
}
/* 失焦时把代码里粘进来的 sh/sz 前缀清掉，只留数字 */
function onRowChange(e){
  const t = e.target;
  if(!t || !t.dataset || t.dataset.k !== 'code') return;
  const i = +t.dataset.i; if(!ROWS[i]) return;
  const d = stripCode(t.value);
  if(t.value !== d){ t.value = d; ROWS[i].code = d; saveRows(); lookupRow(i); }
}
function bindHoldings(){
  const tb = document.getElementById('hRows');
  if(tb && !tb._deleg){
    tb._deleg = 1;
    tb.addEventListener('input', onRowInput);
    tb.addEventListener('change', onRowChange);
    tb.addEventListener('click', onRowClick);
  }
  const byId = id => document.getElementById(id);
  if(byId('addRow')) byId('addRow').onclick = () => {
    ROWS.push({name:'',code:'',mkt:'',cost:'',red:'',next:'',price:''}); saveRows(); initHoldings();
    const last = document.getElementById('hRows').lastElementChild;
    if(last){ const f = last.querySelector('input'); if(f) f.focus(); }
  };
  /* 视图切换：监控（只看行情）/ 纪律线（成本·止损·下一关口） */
  document.querySelectorAll('.vs').forEach(b => {
    b.onclick = () => {
      const v = b.dataset.view;
      if(!VIEWS[v] || v === VIEW) return;
      VIEW = v; saveView();
      initHoldings();
    };
  });
  if(byId('lookupAll')) byId('lookupAll').onclick = refreshAllQuotes;
  if(byId('resetBtn')) byId('resetBtn').onclick = () => {
    if(!confirm('恢复成 ' + DEMO.length + ' 行示例持仓？你现在填的 ' + ROWS.length + ' 行会被全部替换，且无法撤销。')) return;
    ROWS = DEMO.map(r=>normRow(Object.assign({}, r))); saveRows(); initHoldings();
  };
  if(byId('expBtn')) byId('expBtn').onclick = () => {
    const b = new Blob([JSON.stringify(ROWS.map(r=>({name:r.name,code:r.code,mkt:r.mkt,cost:r.cost,red:r.red,next:r.next,price:r.price})),null,2)],{type:'application/json'});
    const a = document.createElement('a'); a.href = URL.createObjectURL(b);
    a.download = '我的持仓与纪律线.json'; a.click(); URL.revokeObjectURL(a.href);
  };
  if(byId('impBtn')) byId('impBtn').onclick = () => byId('impFile').click();
  if(byId('impFile')) byId('impFile').onchange = e => {
    const f = e.target.files && e.target.files[0]; if(!f) return;
    const fr = new FileReader();
    fr.onload = () => { try{ const a = JSON.parse(fr.result); if(!Array.isArray(a)) throw 0;
      ROWS = a.map(normRow); saveRows(); initHoldings(); }catch(err){ alert('这个文件读不了，请用本页「导出」出来的 JSON。'); } };
    fr.readAsText(f); e.target.value = '';
  };
}

/* ══════════════════════════════════════════════════════════════════
   7.1 · 自动查询：代码 / 名称 → 名称 + 现价
   两个公开免费接口：腾讯行情搜索（脚本标签跨域，无 CORS 限制）取编码表，
   东方财富 ulist（带 CORS）取现价。都不需要连接器。
   ══════════════════════════════════════════════════════════════════ */
const SB_URL = 'https://smartbox.gtimg.cn/s3/?v=2&q=';
const TYPE_ZH = {'GP-A':'股票','GP-B':'股票','ETF':'ETF','LOF':'LOF','ZS':'指数','ZQ':'债券'};

function parseHint(h){
  if(!h || h === 'N') return [];
  const out = [];
  h.split('^').forEach(x => {
    if(!x) return;
    const p = x.split('~');
    const mkt = String(p[0] || '').toLowerCase(), code = p[1] || '', name = p[2] || '', type = p[4] || '';
    if(!/^(sh|sz|bj)$/.test(mkt) || !code) return;
    if(out.some(o => o.mkt === mkt && o.code === code)) return;
    out.push({mkt:mkt, code:code, name:name, type:type});
  });
  const rank = t => (t === 'ZS' ? 9 : (t === 'ZQ' ? 5 : 0));   // 个股 / ETF 排在指数前面
  out.sort((a,b) => rank(a.type) - rank(b.type));
  return out;
}
function smartbox(q){
  return new Promise(resolve => {
    const s = document.createElement('script');
    let done = false;
    const finish = v => {
      if(done) return; done = true; clearTimeout(timer);
      if(s.parentNode) s.parentNode.removeChild(s);
      resolve(v);
    };
    const empty = {list:[], raw:''};
    const timer = setTimeout(() => finish(empty), 7000);
    try{ window.v_hint = ''; }catch(e){}
    s.onload = () => {
      const h = (typeof window.v_hint === 'string') ? window.v_hint : '';
      finish({list:parseHint(h), raw:h});
    };
    s.onerror = () => finish(empty);
    s.src = SB_URL + encodeURIComponent(q) + '&t=all';
    document.head.appendChild(s);
  });
}
/* 一次批量取行情。返回 { 'sh510300': {name,price,chgPct,chg,high,low,amount} }
   监控视图那四列（涨跌/涨跌额/区间/成交额）都靠这里补齐，所以字段要一次取够。 */
/* ══ 腾讯行情兜底（qt.gtimg.cn）══════════════════════════════════
   为什么要这一层：本机（以及部分网络）到 push2.eastmoney.com / push2his 整族
   是**连接层**就失败——fetch 报 Failed to fetch，用 <script> 注入也 onerror，
   所以不是 CORS，是网络根本不通；而同期的 qt.gtimg.cn、web.ifzq.gtimg.cn、
   push2ex、datacenter-web、np-weblist 都正常（2026-10-19 浏览器实测）。
   这里把腾讯的文本行情转成与东财 ulist 同构的对象，normalize / fetchQuotes 一行都不用改。
   ⚠️ 只处理 sh / sz / bj：港股 78 列、美股 73 列，下标与 A 股不同，不能套同一套。
   ⚠️ 成交额：腾讯给的是「万元」，东财给的是「元」，必须 ×1e4 才能同口径。 */
const TXQ = 'https://qt.gtimg.cn/q=';
function txSym(code, mkt){
  const c = stripCode(code); if(!c) return '';
  const mk = mkt || inferMarket(c) || 'sh';
  return (/^(sh|sz|bj)$/.test(mk) ? mk : 'sh') + c;
}
async function txQuote(items){
  const syms = [], seen = {};
  (items || []).forEach(x => {
    const y = txSym(x && x.code, x && x.mkt);
    if(y && !seen[y]){ seen[y] = 1; syms.push(y); }
  });
  if(!syms.length) return null;
  const r = await fetch(TXQ + syms.join(','), {cache: 'no-store'});
  if(!r.ok) throw new Error('腾讯行情 HTTP ' + r.status);
  const buf = await r.arrayBuffer();
  let txt = '';
  try{ txt = new TextDecoder('gbk').decode(buf); }catch(e){ txt = new TextDecoder('utf-8').decode(buf); }
  const diff = [];
  let d8 = '';
  txt.split(';').forEach(seg => {
    const m = seg.trim().match(/^v_([a-z]{2})([^=]+)="([\s\S]*)"$/);
    if(!m || !/^(sh|sz|bj)$/.test(m[1])) return;
    const p = m[3].split('~');
    if(p.length < 40) return;
    const price = N(p[3]);
    if(price == null) return;
    if(!d8 && p[30] && String(p[30]).length >= 8) d8 = String(p[30]).slice(0, 8);   // 20261019161500
    diff.push({
      f2: price,                                        // 现价
      f3: N(p[32]),                                     // 涨跌幅 %
      f4: N(p[31]),                                     // 涨跌额
      f5: N(p[6]),                                      // 成交量（手）
      f6: (N(p[37]) == null ? null : N(p[37]) * 1e4),   // 成交额：万元 → 元
      f12: p[2],                                        // 代码（纯数字）
      f13: (m[1] === 'sh' ? 1 : 0),                      // 市场：1 沪 / 0 深京
      f14: p[1],                                        // 名称
      f15: N(p[33]), f16: N(p[34]),                      // 最高 / 最低
      f17: N(p[5]), f18: N(p[4])                        // 今开 / 昨收
    });
  });
  if(!diff.length) return null;
  return {data: {total: diff.length, diff: diff}, _date: d8};
}
const QFIELDS = 'f12,f13,f14,f2,f3,f4,f15,f16,f6';
async function fetchQuotes(items){
  const map = {};
  const ids = [], seen = {};
  (items || []).forEach(x => {
    const d = stripCode(x && x.code); if(!d) return;
    const mk = (x && x.mkt) || inferMarket(d) || 'sh';
    const sid = (mk === 'sh' ? '1' : '0') + '.' + d;   // 沪深与北交所都用交易所前缀，深/北同为 0
    if(seen[sid]) return; seen[sid] = 1; ids.push(sid);
  });
  if(!ids.length) return map;
  let diff = [];
  try{
    const u = P2 + '/ulist.np/get?fltt=2&invt=2&secids=' + ids.join(',') + '&fields=' + QFIELDS + '&ut=' + UT;
    const j = await fetchJSON(u);
    diff = (j && j.data && j.data.diff) || [];
  }catch(e){ diff = []; }
  if(!diff || !(Array.isArray(diff) ? diff.length : Object.keys(diff).length)){
    /* 东财整族连不上 → 换腾讯；换完再空就真的取不到价了 */
    try{ const tq = await txQuote(items); diff = (tq && tq.data && tq.data.diff) || []; }catch(e){ diff = []; }
  }
  const arr = Array.isArray(diff) ? diff : Object.keys(diff).map(k => diff[k]);
  arr.forEach(x => {
    if(!x || !x.f12) return;
    // f6 是「元」，页面统一按「亿」显示（与内嵌快照里的 quote 口径保持一致）
    const amt = N(x.f6);
    const obj = {name:x.f14, price:N(x.f2), chgPct:N(x.f3), chg:N(x.f4),
                 high:N(x.f15), low:N(x.f16), amount: amt == null ? null : amt/1e8};
    map[(x.f13 ? 'sh' : 'sz') + x.f12] = obj;
    // 北交所（4/8 开头）在东财归在 0. 段、回包 f13=0，本地却按 bj 记，补一个别名键
    if(/^[48]/.test(x.f12)) map['bj' + x.f12] = obj;
  });
  return map;
}
function putQuote(mkt, code, q){
  if(q == null) return;
  const m = window.__MODEL || (window.__MODEL = {});
  m.quoteMap = m.quoteMap || {};
  const k = mkt + stripCode(code);
  const obj = (typeof q === 'object') ? q : {price: q};
  m.quoteMap[k] = Object.assign({}, m.quoteMap[k] || {}, obj);
}
/* ══ 东财搜索兜底（JSONP）════════════════════════════════════════
   searchapi 返回 200 但不带 CORS 头，fetch 必失败；它支持 cb= 回调，用 <script> 注入即可，
   http:// 与 file:// 两种打开方式都实测可用（2026-10-19）。
   用途：广告拦截插件把 gtimg 挡掉时，「代码 → 名称」仍然查得到。 */
const EM_SG  = 'https://searchapi.eastmoney.com/api/suggest/get';
const EM_TOK = 'D43BF722C8E33BDC906FB84D85E326E8';
let EM_SEQ = 0;
function emSuggest(q, fundOnly){
  return new Promise(resolve => {
    const empty = {list:[], raw:'', otc:false};
    const cb = '__em_sg_' + (++EM_SEQ) + '_' + Date.now();
    const s = document.createElement('script');
    let done = false;
    const finish = v => {
      if(done) return; done = true; clearTimeout(timer);
      try{ delete window[cb]; }catch(e){ window[cb] = undefined; }
      if(s.parentNode) s.parentNode.removeChild(s);
      resolve(v);
    };
    const timer = setTimeout(() => finish(empty), 7000);
    window[cb] = data => {
      const rows = (((data || {}).QuotationCodeTable || {}).Data) || [];
      const otc = rows.some(x => x.Classify === 'OTCFUND');
      const list = [];
      rows.forEach(x => {
        const mk = x.MktNum === '1' ? 'sh' : (x.MktNum === '0' ? 'sz' : null);
        if(!mk) return;                      // 150 场外 / 116 港股 / 10 期权 一律不进候选
        if(!/^(AStock|Fund|Index|NEEQ)$/.test(x.Classify)) return;
        if(list.some(o => o.mkt === mk && o.code === x.Code)) return;
        list.push({mkt: x.Classify === 'NEEQ' ? 'bj' : mk, code:x.Code, name:x.Name,
          type: x.Classify === 'Fund'  ? 'ETF'
              : x.Classify === 'Index' ? 'ZS'
              : x.Classify === 'NEEQ'  ? 'BJ' : 'GP-A'});
      });
      const rank = t => (t === 'ZS' ? 9 : (t === 'ZQ' ? 5 : 0));
      list.sort((a,b) => rank(a.type) - rank(b.type));
      finish({list:list, raw:'em', otc:otc});
    };
    s.onerror = () => finish(empty);
    s.src = EM_SG + '?input=' + encodeURIComponent(q) + '&type=14&token=' + EM_TOK
          + '&count=10&cb=' + cb + (fundOnly ? '&securitytype=8' : '');
    document.head.appendChild(s);
  });
}
/* 代码/名称 → 候选：腾讯优先，查不到再走东财；名称里带 ETF 时补一次基金过滤查询 */
function searchAny(q){
  return smartbox(q).then(r => {
    if(r && r.list && r.list.length) return r;
    return emSuggest(q).then(e => {
      if(e.list.length || e.otc) return e;
      return emSuggest(q, true);
    });
  });
}
function lookupRow(i, force, src){
  const row = ROWS[i]; if(!row) return;
  const code = String(row.code || '').trim(), name = String(row.name || '').trim();
  const q = code || name;
  const from = src || (code ? 'code' : 'name');
  if(!q){ setLk(i, '', ''); clearPick(i); return; }
  clearPick(i);
  setLk(i, 'busy', '查询中…');
  searchAny(q).then(res0 => {
    const list = (res0 && res0.list) || [], rawH = (res0 && res0.raw) || '';
    if(!ROWS[i]) return;
    if(!force && code && String(ROWS[i].code || '').trim() !== code) return;  // 期间又改了，丢弃
    if(!list.length){
      // 场外基金只在场内行情里没有报价，说清楚原因，别让人以为是代码输错了
      if((res0 && res0.otc) || /(^|\^)jj~/.test(rawH)) setLk(i, 'err', '场外基金，取不到行情');
      else if(!rawH) setLk(i, 'err', '查询服务没响应（可能被浏览器插件拦截），可手填名称');
      else setLk(i, 'err', code ? '没找到，可改用名称' : '没找到');
      return;
    }
    const d = stripCode(code);
    const same = d ? list.filter(x => x.code === d) : [];
    if(list.length === 1){ applyPick(i, list[0], from); return; }
    if(same.length === 1){ applyPick(i, same[0], from); return; }
    const use = same.length > 1 ? same : list;
    showPick(i, use, from);
    setLk(i, 'busy', '请选一个 ↓');
  }).catch(() => setLk(i, 'err', '查询失败'));
}
function showPick(i, list, from){
  clearPick(i);
  const tb = document.getElementById('hRows'); if(!tb) return;
  const tr = rowTr(i); if(!tr) return;
  PICKS[i] = {src: from || 'code', list: list};
  const pr = document.createElement('tr');
  pr.className = 'pick-tr';
  pr.setAttribute('data-pick', i);
  pr.innerHTML = '<td colspan="10"><div class="pick-box"><span class="pick-lab">查到多个，选一个：</span>' +
    list.slice(0, 7).map((x, n) =>
      '<button class="pick" data-pick-i="' + i + '" data-pick-n="' + n + '"><b>' + esc(x.name) + '</b>' +
      '<span class="pk-code">' + x.mkt.toUpperCase() + x.code + '</span>' +
      '<span class="sg-type">' + esc(TYPE_ZH[x.type] || x.type || '标的') + '</span></button>').join('') +
    '<button class="pick-x" data-pick-i="' + i + '">取消</button></div></td>';
  if(tr.nextSibling) tb.insertBefore(pr, tr.nextSibling); else tb.appendChild(pr);
}
function applyPick(i, item, from){
  const row = ROWS[i]; if(!row || !item) return;
  clearPick(i);
  row.code = item.code;
  row.mkt = item.mkt;
  // 从代码查出来的 → 名称按搜索结果刷新；从名称查出来的 → 尊重你自己写的名称
  if(item.name && (from === 'code' || !String(row.name || '').trim())) row.name = item.name;
  saveRows();
  const tr = rowTr(i);
  if(tr){
    const ci = tr.querySelector('input[data-k="code"]');
    const ni = tr.querySelector('input[data-k="name"]');
    if(ci && document.activeElement !== ci) ci.value = item.code;
    if(ni && document.activeElement !== ni) ni.value = row.name;
  }
  setLk(i, 'busy', '取价中…');
  refreshRow(i);
  fetchQuotes([{mkt:item.mkt, code:item.code}]).then(map => {
    if(!ROWS[i]) return;
    const hit = map[item.mkt + item.code];
    if(hit && hit.price != null){
      putQuote(item.mkt, item.code, hit);
      if(hit.name && !String(row.name || '').trim()){ row.name = hit.name; saveRows();
        const ni2 = rowTr(i) && rowTr(i).querySelector('input[data-k="name"]');
        if(ni2 && document.activeElement !== ni2) ni2.value = hit.name; }
      refreshRow(i);
      setLk(i, 'ok', '已带出现价');
    } else {
      setLk(i, '', '没取到价，请手填');
    }
  }).catch(() => setLk(i, '', '没取到价，请手填'));
}
async function refreshAllQuotes(){
  const b = document.getElementById('lookupAll');
  if(b){ b.disabled = true; b.textContent = '取价中…'; }
  try{
    const items = [];
    ROWS.forEach((r, idx) => { if(r.code) items.push({i:idx, mkt:r.mkt || inferMarket(r.code) || 'sh', code:r.code}); });
    if(items.length){
      const map = await fetchQuotes(items);
      items.forEach(it => {
        const hit = map[it.mkt + it.code] || map[(it.mkt === 'sh' ? 'sz' : 'sh') + it.code];
        if(!hit || hit.price == null) return;
        putQuote(it.mkt, it.code, hit);
        const r = ROWS[it.i]; if(!r) return;
        if(!r.mkt) r.mkt = it.mkt;
        if(!String(r.name || '').trim() && hit.name) r.name = hit.name;
      });
      saveRows();
      ROWS.forEach((r, i) => {
        const tr = rowTr(i);
        const ni = tr && tr.querySelector('input[data-k="name"]');
        if(ni && document.activeElement !== ni) ni.value = r.name;
        refreshRow(i);
      });
    }
  }catch(e){}
  if(b){ b.disabled = false; b.textContent = '刷新现价'; }
}

/* ══════════════════════════════════════════════════════════════════
   8 · 时段条 / 页脚 / 数据徽标
   ══════════════════════════════════════════════════════════════════ */
function paintSession(){
  const now = new Date();
  const st = sessionState(now);
  const dot = document.getElementById('sbDot');
  if(dot) dot.className = 'dot' + (st.activeKey ? ' on' : '');
  const s = document.getElementById('sbState'); if(s) s.textContent = st.name;
  const c = document.getElementById('sbClock');
  /* 加「本机」两个字：光丢一个日期时间，没人知道这是哪儿的钟 */
  if(c) c.textContent = '本机 ' + now.toLocaleString('zh-CN',{hour12:false}).replace(/\//g,'-');
  const p = document.getElementById('sbPeriods');
  if(p) p.innerHTML = SESSIONS.map(x =>
    '<span class="sb-p' + (st.activeKey===x.key?' active':'') + '">' + x.label + ' ' + x.b + '–' + x.e + '</span>').join('');
  const f = document.getElementById('sbFill'); if(f) f.style.width = (st.progress*100).toFixed(1) + '%';
}
function paintBadge(state, text){
  const b = document.getElementById('dataBadge'); if(!b) return;
  b.className = 'badge badge-' + (state==='live'?'live':(state==='err'?'err':(state==='mix'?'mix':'snap')));
  b.textContent = text;
}

/* ══════════════════════════════════════════════════════════════════
   9 · 实时拉取（降级路径：完全不需要腾讯自选股连接器）
   ══════════════════════════════════════════════════════════════════ */
async function fetchLive(){
  const keys = Object.keys(URLS);
  const out = {};
  VIA_MIRROR = false;
  const res = await Promise.all(keys.map(async k => {
    try{ return [k, await fetchJSON(URLS[k])]; }
    catch(e){ return [k, null]; }
  }));
  res.forEach(([k,v]) => { if(v) out[k] = v; });

  /* 东财 push2 整族不通时，先把指数报价换成腾讯的；
     后面的交易日、涨跌停池子都靠它的时间戳，所以必须放在最前面。 */
  const qNow = out.quote && out.quote.data && out.quote.data.diff;
  const qLen = qNow ? (Array.isArray(qNow) ? qNow.length : Object.keys(qNow).length) : 0;
  if(!qLen){
    try{
      const want = WANT.map(x => ({code: x.code, mkt: x.market ? 'sh' : 'sz'}));
      want.push({code: '000985', mkt: 'sh'});      // 中证全指：全市场口径要用
      const tq = await txQuote(want);
      if(tq){ out.quote = tq; out._fallback = 'tencent'; }
    }catch(e){}
  }

  /* 涨跌停池子挂在 push2ex 上，和 push2 不是同一台机器：
     push2 不通时这几项照样取得到，所以交易日从两个来源里任取一个。 */
  let date = null;
  const tr = out.trend_sh000001 && out.trend_sh000001.data && out.trend_sh000001.data.trends;
  if(tr && tr.length) date = tr[0].slice(0,10).replace(/-/g,'');
  if(!date && out.quote && out.quote._date) date = out.quote._date;
  if(!date) date = todayStr().replace(/-/g,'');
  if(date){
    const pk = Object.keys(POOL_URL);
    const pr = await Promise.all(pk.map(async k => {
      try{ return [k, await fetchJSON(POOL_URL[k](date))]; }
      catch(e){ return [k, null]; }
    }));
    /* ⚠️ 只有真的带回池子才覆盖：非交易日 / 日期不对时接口回空，
       覆盖上去会把快照里那份好数据冲成「0 家」。 */
    pr.forEach(([k,v]) => {
      const pool = v && v.data && v.data.pool;
      if(Array.isArray(pool) && pool.length) out[k] = v;
    });
  }
  out._meta = {fetchedAt:new Date().toLocaleString('zh-CN',{hour12:false}), tradeDate:date,
               source: out._fallback === 'tencent'
                 ? '腾讯行情（东方财富主行情机当前不可达）'
                 : (VIA_MIRROR ? '东方财富公开行情接口（备用通道）' : '东方财富公开行情接口')};
  if(VIA_MIRROR) out._meta.mirror = true;
  if(out._fallback) out._meta.fallback = out._fallback;
  delete out._fallback;

  const liveKeys = Object.keys(out).filter(k => k !== '_meta');
  out._meta.liveKeys = liveKeys;
  if(liveKeys.indexOf('quote') < 0) throw new Error('行情没取到（可用 ' + liveKeys.length + ' 项）');
  return out;
}

/* 快照打底 + 实时覆盖。
   为什么必须合并而不是整份替换：只拿到报价时如果整份换掉，板块 / 资金流 /
   涨跌家数那几段会**整段消失**——那比「全是快照」更糟，等于越修越少。 */
function mergeLive(snap, live){
  const out = {};
  Object.keys(snap || {}).forEach(k => { if(k !== '_meta') out[k] = snap[k]; });
  Object.keys(live || {}).forEach(k => { if(k !== '_meta' && live[k]) out[k] = live[k]; });
  const sm = (snap && snap._meta) || {}, lm = (live && live._meta) || {};
  out._meta = Object.assign({}, sm, lm, {snapAt: sm.fetchedAt || null, merged: true});
  return out;
}

/* 持仓要能按代码查到价：把指数 + 常见标的 + 你自己持仓里的代码并进 quoteMap */
async function buildQuoteMap(base){
  const m = normalize(base);
  const items = [];
  (m.indices||[]).forEach(i => items.push({code:i.code, mkt:i.market?'sh':'sz'}));
  // 顺带补几个新手常见标的（失败不影响主流程）
  [{code:'510300',mkt:'sh'},{code:'510500',mkt:'sh'},{code:'588000',mkt:'sh'},
   {code:'600519',mkt:'sh'},{code:'300750',mkt:'sz'}].forEach(x => items.push(x));
  // 持仓里已经填过的代码，一起取价（重新打开页面也能自动对上）
  (Array.isArray(ROWS) ? ROWS : []).forEach(r => {
    if(stripCode(r && r.code)) items.push({code:r.code, mkt:r.mkt || ''});
  });
  try{ return await fetchQuotes(items); }catch(e){ return {}; }
}

/* ══════════════════════════════════════════════════════════════════
   9 · 云端备份（登录后才走云端；不登录一切照旧）
   ── 给以后改这份代码的人 ──
   · 事实来源仍然是本地存储：云端只是「另一台设备也能拿到的那一份」。
   · 云端组件是懒加载的（真要用才去下载）。下载不到、没网、没登录 ⇒ 退回纯本地，
     不弹错、不挡路；首屏渲染完全不依赖它。
   · 三张表：ledger_entries（台账，一行一笔）· journal_entries（日记，一天一条）·
     book_state（快照 / 现金 / 持仓监控，整份同步）。
   · owner_id 由数据库按登录身份自动填，这里一律不传——传了也会被挡回来。
   ══════════════════════════════════════════════════════════════════ */
const CLOUD_SDK = 'https://cdn.jsdelivr.net/npm/@tencent-ai/workbuddy-cloud-sdk@dev/lib/index.global.js';
/* 下面三项照抄云服务开通回执里的 publicConfig：不猜、不从域名推、不从别处读 */
const CLOUD_CFG = {
  endpoint: 'https://newbie-market-dashboard.app.workbuddy.host',
  oauthRelayBaseUrl: 'https://www.workbuddy.cn/v2/as/genie-baas/oauth',
  publishableKey: 'wbpk_9M4uoHgFyRyQeKe7kb12Mm_pyh3EotqxsKvgaQpTbqQ5Zg0fTugWH8I'
};
let _cloudP = null, _cloudC = null, _pushT = null;

function cloudRid(){
  try{ if(window.crypto && crypto.randomUUID) return crypto.randomUUID(); }catch(e){}
  return 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
}
function cloudErrText(e){
  if(!e) return '原因未知';
  if(typeof e === 'string') return e;
  return e.message || e.error_description || e.code || '连接失败';
}
function cloudNum(v){ const n = N(v); return (n == null) ? null : n; }

/* 组件只下载一次、客户端只建一次 */
function loadCloudSdk(){
  if(_cloudP) return _cloudP;
  _cloudP = new Promise(function(res, rej){
    const W = window.WorkBuddyCloud;
    if(W && typeof W.createWorkBuddyCloud === 'function'){ res(W); return; }
    const sc = document.createElement('script');
    let done = false;
    const fail = function(msg){ if(done) return; done = true; rej(new Error(msg)); };
    sc.src = CLOUD_SDK; sc.async = true;
    sc.onload = function(){
      const WW = window.WorkBuddyCloud;
      if(WW && typeof WW.createWorkBuddyCloud === 'function'){ if(!done){ done = true; res(WW); } }
      else fail('云端组件不完整');
    };
    sc.onerror = function(){ fail('云端组件下载失败（网络不通或被拦截）'); };
    setTimeout(function(){ fail('云端组件加载超时'); }, 12000);
    document.head.appendChild(sc);
  });
  return _cloudP;
}
async function ensureCloud(){
  if(_cloudC) return _cloudC;
  const W = await loadCloudSdk();
  _cloudC = W.createWorkBuddyCloud({
    endpoint: CLOUD_CFG.endpoint,
    oauthRelayBaseUrl: CLOUD_CFG.oauthRelayBaseUrl,
    publishableKey: CLOUD_CFG.publishableKey
  });
  return _cloudC;
}

/* ── 登录 / 登出 ─────────────────────────────────────────────── */
async function cloudSignIn(){
  CLOUD.err = ''; CLOUD.busy = true; paintCloudBar();
  try{
    const cloud = await ensureCloud();
    const back = new URL(location.href);
    /* 回调只能落在站点根路径：静态站没有 history 回退，子路径会 404 把 code 丢掉。
       自己页面带的查询参数要留着，登录回来还在原地。 */
    back.pathname = '/';
    ['code', 'state', 'provider'].forEach(function(k){ back.searchParams.delete(k); });
    const r = await cloud.auth.signInWithOAuth({ provider: 'workbuddy', redirectTo: back.toString() });
    if(r && r.error) throw new Error(cloudErrText(r.error));
    if(!r || !r.data || !r.data.url) throw new Error('没拿到登录地址，稍后再试一次');
    /* 必须同一个标签页：登录用的校验凭据只存在本页 sessionStorage 里 */
    location.assign(r.data.url);
  }catch(e){
    CLOUD.busy = false; CLOUD.err = cloudErrText(e); paintCloudBar();
  }
}
async function cloudSignOut(){
  try{ const cloud = await ensureCloud(); await cloud.auth.signOut(); }catch(e){}
  CLOUD.ready = false; CLOUD.user = null; CLOUD.at = 0; CLOUD.err = '';
  CLOUD.rids = null; CLOUD.jdates = null; CLOUD.counts = null;
  paintCloudBar();
  initBookSections();          // 「数据存哪」那句话要跟着改回去
}

/* 启动时跑一次：先看是不是刚被登录页送回来，不是就接着用旧登录 */
async function cloudBoot(){
  if(CLOUD.booted) return;
  CLOUD.booted = true;
  if(location.origin !== CLOUD_CFG.endpoint){
    CLOUD.offsite = true;               // 本地文件 / 预览地址：云端连不上，也不该连
    paintCloudBar(); return;
  }
  const url = new URL(location.href);
  const isCb = url.searchParams.has('code') || url.searchParams.has('error');
  if(isCb){
    if(url.searchParams.get('provider') !== 'workbuddy'){
      CLOUD.err = '登录回调少了关键参数，请重新点一次登录'; paintCloudBar(); return;
    }
    try{
      const cloud = await ensureCloud();
      const fn = cloud.auth.handleWorkBuddyCallback;
      if(typeof fn !== 'function') throw new Error('当前云端组件不支持这个登录方式');
      const r = await fn.call(cloud.auth);
      if(r && r.error) throw new Error(cloudErrText(r.error));
      if(r && r.data){
        ['code', 'state', 'provider'].forEach(function(k){ url.searchParams.delete(k); });
        history.replaceState(null, '', url.pathname + url.search + url.hash);
        await cloudAfterSignIn(r.data);
        return;
      }
    }catch(e){
      CLOUD.err = cloudErrText(e); paintCloudBar(); return;
    }
  }
  try{
    const cloud = await ensureCloud();
    const r = await cloud.auth.getSession();
    if(r && r.data) await cloudAfterSignIn(r.data);
    else if(r && r.error && r.error.kind && r.error.kind !== 'unauthenticated'){
      /* 「没登录」是正常情况；凭据被拒 / 后端不可用要如实显示，别装成未登录 */
      CLOUD.sdk = true; CLOUD.busy = false; CLOUD.err = cloudErrText(r.error); paintCloudBar();
    } else { CLOUD.sdk = true; CLOUD.busy = false; paintCloudBar(); }
  }catch(e){
    /* 组件没下载到：当没这回事，页面照常 */
    CLOUD.sdk = false; CLOUD.err = ''; paintCloudBar();
  }
}

async function cloudAfterSignIn(session){
  const u = (session && session.user) || {};
  CLOUD.user = { name: u.name || '', avatar: u.avatarUrl || '' };
  CLOUD.sdk = true; CLOUD.busy = true; CLOUD.err = ''; paintCloudBar();
  try{
    let prev = '';
    try{ prev = localStorage.getItem('mb_cloud_uid') || ''; }catch(e){}
    const now = String(u.id || u.userId || '');
    if(prev && now && prev !== now){
      const go = window.confirm('这台设备上记的账，上次是另一个账号在用。\n\n' +
        '确定：把现在看到的这份也存进当前账号（两边合并，一条不丢）。\n' +
        '取消：只看当前账号云端的账，这台设备上的这份先不动。');
      if(!go){
        CLOUD.ready = false; CLOUD.busy = false; CLOUD.err = '账号不同，已暂停同步';
        paintCloudBar(); return;
      }
    }
    await cloudPull();
    await cloudPush();
    const uid = now || CLOUD.uid || '';
    if(uid){ try{ localStorage.setItem('mb_cloud_uid', uid); }catch(e){} }
    CLOUD.ready = true; CLOUD.at = Date.now(); CLOUD.busy = false;
    paintCloudBar();
    initBookSections();            // 数据变了，账本页重画一遍
  }catch(e){
    CLOUD.busy = false; CLOUD.err = cloudErrText(e); paintCloudBar();
  }
}

/* ── 拉：云端有、本机没有的补进来（同一行以本机为准） ────────────── */
async function cloudPull(){
  const cloud = await ensureCloud();

  const lr = await cloud.database.from('ledger_entries')
    .select('rid,seq,trade_date,code,mkt,name,side,price,qty,owner_id')
    .order('seq', { ascending: true });
  if(lr.error) throw new Error(cloudErrText(lr.error));
  const rows = Array.isArray(lr.data) ? lr.data : [];
  if(rows.length && rows[0].owner_id) CLOUD.uid = rows[0].owner_id;

  const mine = {};
  LEDGER.forEach(function(r){ if(r.rid) mine[r.rid] = 1; });
  const seen = {};
  rows.forEach(function(r){
    if(r.rid) seen[r.rid] = 1;
    if(mine[r.rid]) return;
    LEDGER.push({
      rid: r.rid, date: r.trade_date || todayStr(), code: r.code || '',
      mkt: r.mkt || '', name: r.name || '', side: r.side === 'sell' ? 'sell' : 'buy',
      price: (r.price == null ? '' : r.price), qty: (r.qty == null ? '' : r.qty)
    });
  });
  CLOUD.rids = seen;
  if(rows.length) jset(LKEY, LEDGER);

  const jr = await cloud.database.from('journal_entries')
    .select('entry_date,decision,reason,mood')
    .order('entry_date', { ascending: true });
  if(jr.error) throw new Error(cloudErrText(jr.error));
  const jseen = {};
  (jr.data || []).forEach(function(x){
    const d = x.entry_date; if(!d) return;
    jseen[d] = 1;
    const j = JOURNAL[d];
    if(j && (j.dec || j.why || j.mood)) return;      // 本机写过内容 → 以本机为准
    JOURNAL[d] = { dec: x.decision || '', why: x.reason || '', mood: x.mood || '' };
  });
  CLOUD.jdates = jseen;
  if(Object.keys(jseen).length) jset(JKEY, JOURNAL);

  const bs = await cloud.database.from('book_state').select('doc_key,payload');
  if(bs.error) throw new Error(cloudErrText(bs.error));
  let touched = false;
  (bs.data || []).forEach(function(doc){
    const p = (doc && doc.payload) || {};
    if(doc.doc_key === 'snaps' && p.v && typeof p.v === 'object'){
      Object.keys(p.v).forEach(function(d){
        if(SNAPS[d]) return;                            // 同一天：本机优先
        const x = p.v[d];
        if(!x || typeof x !== 'object') return;
        SNAPS[d] = x; touched = true;
      });
    }
    if(doc.doc_key === 'cash' && typeof p.v === 'number' && !CASH){ CASH = p.v; touched = true; }
    if(doc.doc_key === 'watch_rows' && Array.isArray(p.v) && p.v.length){
      let has = false;
      try{ has = !!localStorage.getItem(KEY); }catch(e){ has = false; }
      if(!has){ ROWS = p.v.map(normRow); jset(KEY, ROWS); touched = true; }
    }
  });
  if(touched){ jset(SKEY, SNAPS); jset(CKEY, CASH); }
}

/* ── 推：把本机这份整份写上去，本机删掉的行云端也删 ────────────── */
async function cloudPush(){
  const cloud = await ensureCloud();
  const nm = (CLOUD.user && CLOUD.user.name) || null;
  const iso = new Date().toISOString();
  let nL = 0, nJ = 0;

  const rows = [];
  LEDGER.forEach(function(r, i){
    const code = stripCode(r.code);
    if(!code) return;                                   // 空行不占云端位置
    rows.push({ rid: r.rid, owner_name: nm, seq: i, trade_date: r.date || todayStr(),
                code: code, mkt: r.mkt || null, name: r.name || null,
                side: r.side === 'sell' ? 'sell' : 'buy',
                price: cloudNum(r.price), qty: cloudNum(r.qty), updated_at: iso });
  });
  if(rows.length){
    const w = await cloud.database.from('ledger_entries').upsert(rows, { onConflict: 'owner_id,rid' });
    if(w.error) throw new Error(cloudErrText(w.error));
    nL = rows.length;
  }
  const keep = {};
  LEDGER.forEach(function(r){ if(r.rid) keep[r.rid] = 1; });
  const goneL = [];
  if(CLOUD.rids) Object.keys(CLOUD.rids).forEach(function(x){ if(!keep[x]) goneL.push(x); });
  if(goneL.length){
    const d = await cloud.database.from('ledger_entries').delete().in('rid', goneL);
    if(d.error) throw new Error(cloudErrText(d.error));
  }
  CLOUD.rids = keep;

  const jrows = [], keepJ = {};
  Object.keys(JOURNAL).forEach(function(d){
    const j = JOURNAL[d] || {};
    const dec = j.dec || '', why = j.why || '', mood = j.mood || '';
    if(!dec && !why && !mood) return;                   // 空白日记不上传
    keepJ[d] = 1;
    jrows.push({ entry_date: d, owner_name: nm, decision: dec || null,
                 reason: why || null, mood: mood || null, updated_at: iso });
  });
  if(jrows.length){
    const w = await cloud.database.from('journal_entries').upsert(jrows, { onConflict: 'owner_id,entry_date' });
    if(w.error) throw new Error(cloudErrText(w.error));
    nJ = jrows.length;
  }
  const goneJ = [];
  if(CLOUD.jdates) Object.keys(CLOUD.jdates).forEach(function(d){ if(!keepJ[d]) goneJ.push(d); });
  if(goneJ.length){
    const d = await cloud.database.from('journal_entries').delete().in('entry_date', goneJ);
    if(d.error) throw new Error(cloudErrText(d.error));
  }
  CLOUD.jdates = keepJ;

  const docs = [
    { doc_key: 'snaps',      owner_name: nm, payload: { v: SNAPS }, updated_at: iso },
    { doc_key: 'cash',       owner_name: nm, payload: { v: CASH },  updated_at: iso }
  ];
  let rowsSaved = false;
  try{ rowsSaved = !!localStorage.getItem(KEY); }catch(e){ rowsSaved = false; }
  if(rowsSaved) docs.push({ doc_key: 'watch_rows', owner_name: nm, payload: { v: ROWS }, updated_at: iso });
  const wb = await cloud.database.from('book_state').upsert(docs, { onConflict: 'owner_id,doc_key' });
  if(wb.error) throw new Error(cloudErrText(wb.error));

  CLOUD.counts = { ledger: nL, journal: nJ };
  CLOUD.at = Date.now();
}

/* 保存后 1.5 秒攒一次再推，避免边打字边发请求 */
function cloudTouch(){
  if(!CLOUD || !CLOUD.ready) return;
  clearTimeout(_pushT);
  _pushT = setTimeout(function(){
    cloudPush().then(function(){ paintCloudBar(); })
               .catch(function(e){ CLOUD.err = cloudErrText(e); paintCloudBar(); });
  }, 1500);
}

/* ── 界面 ───────────────────────────────────────────────────── */
function cloudStoreText(){
  /* 没有云端配置的版本：连「未登录」都不必提，直接说清存在哪、怎么搬 */
  if(!CLOUD_CFG.endpoint) return '数据只在这台设备的浏览器里（本地存储），不会上传到任何服务器；' +
    '换设备用下面的「备份到网上」或「导出我的数据」。';
  return (CLOUD && CLOUD.ready)
    ? '已经连着云端：这些数字会自动存一份到云端，换台设备登录同一个 WorkBuddy 账号就能看到。'
    : '没登录时数据只在你自己的浏览器里（本地存储），不会上传到任何服务器。';
}
function cloudBarHTML(){ return '<div class="cloud-bar" id="cloudBar"></div>'; }
function cloudTimeText(t){
  if(!t) return '';
  const d = Math.round((Date.now() - t) / 1000);
  if(d < 45) return '刚刚同步';
  if(d < 3600) return Math.max(1, Math.round(d / 60)) + ' 分钟前同步';
  return new Date(t).toLocaleString('zh-CN', {hour12: false, month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit'}) + ' 同步';
}
function paintCloudBar(){
  const el = document.getElementById('cloudBar');
  if(!el) return;
  const bt = function(id, txt, cls){
    return '<button type="button" class="bk-btn' + (cls ? ' ' + cls : '') + '" id="' + id + '">' + txt + '</button>';
  };
  let cls = '', html = '';
  if(!CLOUD_CFG.endpoint){
    /* 没有云端配置的版本（本地包）：只说明数据存哪，不给任何登录入口。
       ⚠️ 这个判断必须在这里做，不能靠在构建期替换 cloudBarHTML()——
          渲染时 paintCloudBar() 会把那条静态 HTML 整个覆盖掉，
          结果就是给出一个永远点不动的「使用 WorkBuddy 登录」。 */
    el.className = 'cloud-bar';
    el.dataset.state = 'local';
    el.dataset.sdk = '0';
    el.title = '数据只存在这台设备的浏览器里；换设备用「备份到网上」。';
    el.innerHTML = '<span class="cb-dot off"></span><span class="cb-txt"><b>数据只存这台设备</b> —— ' +
      '换电脑、换浏览器或清缓存都会丢。想搬到别的设备，用下面的「备份到网上」。</span>';
    return;
  }
  if(CLOUD.offsite){
    html = '<span class="cb-dot off"></span><span class="cb-txt"><b>云端备份</b> 只在正式站点上连得上——' +
      '现在是从本地文件或预览地址打开的，照本机记录用就行。</span>';
  } else if(CLOUD.busy){
    cls = 'on';
    html = '<span class="cb-dot wait"></span><span class="cb-txt"><b>云端备份</b> 正在连接…</span>';
  } else if(CLOUD.ready){
    cls = 'on';
    const who = (CLOUD.user && CLOUD.user.name) ? CLOUD.user.name : '已登录';
    const c = CLOUD.counts || {};
    const stat = '台账 ' + (c.ledger || 0) + ' 笔 · 日记 ' + (c.journal || 0) + ' 天';
    html = '<span class="cb-dot on"></span><span class="cb-txt"><b>云端已连上</b> · ' + esc(who) +
      ' · ' + stat + ' · ' + cloudTimeText(CLOUD.at) + '</span>' +
      bt('cbSync', '立即同步') + bt('cbOut', '退出登录');
  } else if(CLOUD.err){
    cls = 'err';
    html = '<span class="cb-dot err"></span><span class="cb-txt"><b>云端没连上</b> ' + esc(CLOUD.err) +
      '——数据还在本机，照常记。</span>' + bt('cbIn', '再试一次', 'primary');
  } else {
    html = '<span class="cb-dot off"></span><span class="cb-txt"><b>云端备份</b> 没登录 · ' +
      '现在只记在这台设备上，换设备看不到</span>' + bt('cbIn', '使用 WorkBuddy 登录', 'primary');
  }
  el.className = 'cloud-bar' + (cls ? ' ' + cls : '');
  /* 状态也写进 data-* ：自检和「到底卡在哪一步」都靠它，比看文案可靠 */
  el.dataset.state = CLOUD.offsite ? 'offsite' : CLOUD.busy ? 'busy'
                   : CLOUD.ready ? 'on' : CLOUD.err ? 'err' : 'off';
  el.dataset.sdk = CLOUD.sdk ? '1' : '0';
  /* 合并规则要写出来：两台设备都改过同一条时，以眼前这台为准 */
  el.title = '登录后：台账、日记、快照、现金、持仓监控都会存一份到云端。\n' +
    '换设备登录同一个 WorkBuddy 账号就能看到；两台设备都改过的同一条，以眼前这台为准。';
  el.innerHTML = html;
  const b1 = document.getElementById('cbIn');
  if(b1) b1.onclick = cloudSignIn;
  const b2 = document.getElementById('cbSync');
  if(b2) b2.onclick = function(){
    if(CLOUD.busy) return;
    CLOUD.busy = true; CLOUD.err = ''; paintCloudBar();
    (async function(){
      try{ await cloudPull(); await cloudPush(); }
      catch(e){ CLOUD.err = cloudErrText(e); }
      CLOUD.busy = false; paintCloudBar(); initBookSections();
    })();
  };
  const b3 = document.getElementById('cbOut');
  if(b3) b3.onclick = cloudSignOut;
}

/* ══════════════════════════════════════════════════════════════════
   9.5 · 自动刷新（交易时段每 60 秒一次）
   —— 为什么整页重渲染，而不是只改几个数字：
   页面里有大量「拿好几个数拼出来的一句话」（"上证 3,120 点、涨 0.8%，
   成交 1.7 万亿"这种），局部更新得额外维护一套「哪个数对应哪个节点」的映射，
   漏一处就变成「一部分新、一部分旧」——那比整页重画危险得多。
   代价（丢焦点 / 丢滚动）在 uiKeep / uiBack 里兜住。
   ══════════════════════════════════════════════════════════════════ */
const AUTO_MS = 60000;
let autoBusy = false;

function hhmm(d){
  const n = d || new Date();
  return String(n.getHours()).padStart(2, '0') + ':' + String(n.getMinutes()).padStart(2, '0');
}
function hhmmss(d){
  const n = d || new Date();
  return hhmm(n) + ':' + String(n.getSeconds()).padStart(2, '0');
}
/* 徽标只回答一个问题：数据到几点。
   ⚠️ 不要把「哪个接口没取到」写在这里——那是使用者既改不了、也用不上的内部细节，
      摆在最显眼处等于把故障甩给读者。缺哪块，就在那一块自己说（见 staleLine）。
   ⚠️ 也不要说「实时」：页面上的价格是接口返回那一刻的值，收盘后就不再变了，
      叫「实时」会和同一屏的「已收盘」打架。说「更新于几点几分几秒」是准确的。 */
function paintDataBadge(kind){
  const st = sessionState(new Date());
  const snapT = (SNAPSHOT._meta && SNAPSHOT._meta.fetchedAt) ? SNAPSHOT._meta.fetchedAt.slice(11, 16) : '';
  if(kind === 'loading'){ paintBadge('mix', '正在更新…'); return; }
  if(kind === 'fail'){
    paintBadge('err', '更新失败，显示的是' + (snapT ? ' ' + snapT + ' ' : '上一次') + '的数据');
    return;
  }
  if(kind === 'snap'){
    paintBadge('snap', '离线快照' + (snapT ? ' · ' + snapT + ' 抓的' : '') + '，联网后自动更新');
    return;
  }
  if(st.activeKey){
    paintBadge('live', '交易中 · 更新于 ' + hhmmss() + ' · ' + Math.round(AUTO_MS / 1000) + ' 秒后自动刷新');
    return;
  }
  const tail = st.name === '已收盘' ? '数据到 15:00'
             : st.name === '午间休市' ? '数据到 11:30'
             : '显示的是上一交易日收盘';
  paintBadge('live', st.name + ' · ' + tail);
}

/* 「这块是旧的」只在那一块自己说一句，不往主界面顶部堆 */
function staleLine(name){
  const t = (SNAPSHOT._meta && SNAPSHOT._meta.fetchedAt) ? SNAPSHOT._meta.fetchedAt.slice(5, 16) : '';
  return '<p class="stale-line"><b>' + name + '</b>这次没取到，下面是' +
    (t ? '<b>' + t + '</b>' : '上次') + '抓的那一份，其余栏目是刚才更新的。</p>';
}
/* 要闻来自快照，必须写明是几点抓的——它不会跟着 60 秒的自动刷新更新，
   不写清楚就成了「看起来跟别的栏目一样新」。 */
function newsStamp(){
  const t = (SNAPSHOT._meta && SNAPSHOT._meta.fetchedAt) ? SNAPSHOT._meta.fetchedAt.slice(5, 16) : '';
  return '<span class="note">（' + (t ? '快照 ' + t + ' 抓的' : '来自快照') + '，不随分钟级刷新）</span>';
}

/* 缺口标记：只在「拉过一次实时」之后才有意义——首屏那一遍本来就是快照，
   逐块标「这是旧的」等于满屏噪音。 */
var LIVE_GAPS = {breadth:false, sector:false, fund:false, trend:false};
function setLiveGaps(live){
  const lk = (live && live._meta && live._meta.liveKeys) || [];
  if(!lk.length) return;              // 一条都没拉到：整体走「离线快照」，不逐块标
  const has = k => lk.indexOf(k) >= 0;
  LIVE_GAPS = {breadth: !has('breadth'), sector: !has('sector_up'),
               fund: !has('fflow_min'), trend: !has('trend_sh000001')};
}

/* 重画之前把「人在哪、光标在哪」记下来，画完还回去 */
function uiKeep(){
  const a = document.activeElement;
  const k = {y: window.scrollY || 0, id:'', i:'', f:'', pos:null};
  if(a && a.tagName && /^(INPUT|TEXTAREA)$/.test(a.tagName)){
    k.id = a.id || '';
    k.i = (a.dataset && a.dataset.i != null) ? String(a.dataset.i) : '';
    k.f = (a.dataset && a.dataset.k) || '';
    try{ k.pos = a.selectionStart; }catch(e){}
  }
  return k;
}
function uiBack(k){
  try{ window.scrollTo(0, k.y); }catch(e){}
  let el = null;
  if(k.id) el = document.getElementById(k.id);
  if(!el && k.i !== '' && k.f) el = document.querySelector('input[data-i="' + k.i + '"][data-k="' + k.f + '"]');
  if(el && el.focus){
    el.focus();
    try{ if(k.pos != null) el.setSelectionRange(k.pos, k.pos); }catch(e){}
  }
}

async function refreshLive(isFirst){
  /* 自动刷新时才切换「正在更新…」：首屏那一刻本来就是快照，先闪一下「正在更新」没有意义 */
  if(!isFirst) paintDataBadge('loading');
  let live = null;
  try{
    live = await fetchLive();
  }catch(e){
    if(!isFirst) paintDataBadge('fail');
    throw e;
  }
  const merged = mergeLive(SNAPSHOT, live);
  const m2 = normalize(merged);
  if(!m2.ok){ if(!isFirst) paintDataBadge('fail'); return false; }
  m2.quoteMap = await buildQuoteMap(merged);
  const keep = isFirst ? null : uiKeep();
  setLiveGaps(live);        // ⚠️ 必须在 renderAll 之前：先定缺口，段落里才知道要不要加那句提示
  renderAll(m2);
  const got = ((live && live._meta && live._meta.liveKeys) || []).length;
  /* 一条都没拿到 = 这次更新失败（通常是没有网络），徽标要如实说「显示的是几点的数据」，
     不能继续挂着上一次的时间戳假装刚更新过。 */
  paintDataBadge(got ? 'live' : 'fail');
  if(keep) uiBack(keep);
  return got > 0;
}

function typingNow(){
  const a = document.activeElement;
  return !!(a && a.tagName && /^(INPUT|TEXTAREA)$/.test(a.tagName));
}
async function autoTick(){
  if(autoBusy) return;
  if(document.hidden) return;                        // 切到别的标签页就别拉了
  if(!sessionState(new Date()).activeKey) return;    // 只在交易时段刷
  if(typingNow()) return;                            // 正在填，这一轮让给人
  autoBusy = true;
  try{ await refreshLive(false); }
  catch(e){ /* 拉不到就保持现状，下一轮再试 */ }
  finally{ autoBusy = false; }
}

/* ══════════════════════════════════════════════════════════════════
   10 · 启动
   ══════════════════════════════════════════════════════════════════ */
(async function(){
  paintSession(); setInterval(paintSession, 30000);
  paintToc();                       // 目录条：既是导航，也是这一页的阅读顺序
  loadRows();   // 先读出本地持仓，好把它们的代码一起并入取价清单

  // ① 先用内嵌快照渲染
  const snap = normalize(SNAPSHOT);
  if(snap.ok){
    snap.quoteMap = await buildQuoteMap(SNAPSHOT);
    renderAll(snap);
    paintDataBadge('snap');
  } else {
    paintBadge('err', '数据加载失败，刷新一次试试');
  }

  // ② 再拉实时数据覆盖（拉不到就保持快照，徽标改成「更新失败，显示的是几点的数据」）
  try{ await refreshLive(true); }
  catch(e){
    if(snap.ok) paintDataBadge('fail');
    else paintBadge('err', '数据加载失败，刷新一次试试');
  }

  /* ③ 云端备份：等首屏画完再去问「这台设备登过没有」。
     没网 / 组件下载不到 / 没登录都只是没云端，页面照常用。 */
  setTimeout(cloudBoot, 1200);

  /* ④ 交易时段内每 60 秒自动刷新（不在时段内、或正在填表时，autoTick 自己会跳过） */
  setInterval(autoTick, AUTO_MS);

})();
</script>
</body>
</html>
"""


# ── 离线版（学员 Skill 用）：去掉云端同步 ──────────────────────────────
# 为什么不把 cloud 那一大段代码删掉：删起来容易漏、而且会让学员版和线上版
# 各走一套源码，以后改一处要改两遍。这里只做三处定点替换。
_OFFLINE_CFG = ("const CLOUD_CFG = { endpoint: '', oauthRelayBaseUrl: '', "
                "publishableKey: '' };")
# ⚠️ 这里**不再**替换 cloudBarHTML：渲染时 paintCloudBar() 会把那条静态 HTML 整个覆盖掉，
#    替换等于白写，而且会留下一个点不动的「登录」按钮。
#    本地包的云端条由 paintCloudBar() 里的 `if(!CLOUD_CFG.endpoint)` 分支负责（运行时判断）。
_STORE_FROM = ("      <br><b>数据保存</b>：不登录时只存在这台设备的浏览器（本地存储）里；"
               "登录 WorkBuddy 后自动同步一份到云端，<b>换设备</b>登录同一个账号就能看到，"
               "也可以在「我的账本」用<b>导出 / 导入</b>搬家。")
_STORE_TO = ("      <br><b>数据保存</b>：只存在这台设备的浏览器（本地存储）里，不上传任何服务器。"
             "<b>换电脑、换浏览器或清缓存都会丢</b>，"
             "请在「我的账本」用导出 / 导入定期备份、换设备时搬过去。")
_BOOT_FROM = "  setTimeout(cloudBoot, 1200);"
_BOOT_TO = "  /* 离线版：不启用云端同步，也不发起任何云端请求 */"


def offlineize(html):
    """把带云端同步的页面改成纯本地版。任何一处锚点找不到就直接报错。"""
    for old, new, tag in ((_STORE_FROM, _STORE_TO, "页脚数据保存说明"),
                          (_BOOT_FROM, _BOOT_TO, "cloudBoot 调用")):
        n = html.count(old)
        if n != 1:
            raise SystemExit(f"[build] --no-cloud 替换失败：{tag} 命中 {n} 次（应为 1）")
        html = html.replace(old, new)
    key = "const CLOUD_CFG = {"
    i = html.find(key)
    if i < 0 or html.count(key) != 1:
        raise SystemExit("[build] --no-cloud 找不到 CLOUD_CFG")
    j = html.find("};", i)
    if j < 0:
        raise SystemExit("[build] --no-cloud CLOUD_CFG 结构异常")
    return html[:i] + _OFFLINE_CFG + html[j + 2:]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=os.path.join(here, "data", "raw.json"))
    ap.add_argument("--out", default=os.path.join(here, "index.html"))
    ap.add_argument("--no-cloud", action="store_true",
                    help="离线版：去掉云端同步配置与登录入口，数据只存本机")
    args = ap.parse_args()

    with open(args.raw, "r", encoding="utf-8") as f:
        raw = json.load(f)

    payload = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    html = TEMPLATE.replace("__RAW__", payload)
    if args.no_cloud:
        html = offlineize(html)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    meta = raw.get("_meta", {})
    size = os.path.getsize(args.out)
    print(f"[build] 已生成 {args.out}  ({size/1024:.1f} KB)")
    print(f"[build] 内嵌快照 {len(raw)-1} 个接口 · 交易日 {meta.get('tradeDate','—')} · 取数 {meta.get('fetchedAt','—')}")

    left = [t for t in ("__RAW__",) if t in html]
    if left:
        print("[build] ⚠️ 仍有未替换占位符：" + ", ".join(left))
        return 1
    if meta.get("failed"):
        print(f"[build] ⚠️ 快照里有 {len(meta['failed'])} 个接口失败：" + "; ".join(meta["failed"][:3]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
