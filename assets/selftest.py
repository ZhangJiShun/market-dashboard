#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在真实浏览器里跑交互与数值自检（v2）。

做法：把 index.html 复制一份，在 </body> 前注入断言脚本，
用 Chrome 无头模式跑完 --dump-dom，把结果抓出来。
测的是「真正的 JS + 真正的浏览器」，不是我在 Python 里复刻一遍公式。

⚠️ 必须经 http 打开（`file://` 下 Chrome 拒绝 localStorage）。
   配套脚本 run_selftest.sh 已把「起服务 → 跑 Chrome → 抓结果 → 收工」串好。

用法： python3 selftest.py            # 生成 selftest.html
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "index.html")
DST = os.path.join(HERE, "selftest.html")

SNIPPET = r"""
<script>
(async function () {
  const out = [];
  const P = (n, ok, extra) => out.push((ok ? 'PASS' : 'FAIL') + ' | ' + n + (extra ? ' | ' + extra : ''));
  const S = (n, why) => out.push('SKIP | ' + n + ' | ' + (why || ''));
  /* D1 的「首次保存提醒」会弹 alert——无头模式弹窗会卡死流程，开头就吞掉并记数
     （后面 702 行那组局部打桩存的就是这里这个桩，恢复后行为不变） */
  window.__ALERTS = [];
  window.alert = function () { window.__ALERTS.push(String(arguments[0] || '')); };
  // 归一化：去掉空白、把 Unicode 减号(U+2212)统一成 ASCII 减号，避免符号差异造成假失败
  const norm = s => String(s == null ? '' : s).replace(/\u2212/g, '-').replace(/\s+/g, '');
  const T = id => (document.getElementById(id) || {}).textContent || '';
  const TV = id => { const el = document.getElementById(id); if (!el) return '';
                     const i = el.querySelector('input'); return i ? i.value : el.textContent; };
  const has = (s, w) => norm(s).indexOf(norm(w)) >= 0;
  /* 断言不写死某一天的数字：从页面模型/相邻单元格取期望值，换快照也不会误报 */
  const pv = el => { const m = norm((el && el.textContent) || '').match(/-?\d+(?:\.\d+)?%/); return m ? parseFloat(m[0]) : null; };
  const pvT = id => pv(document.getElementById(id));
  const near = (a, b, eps) => a != null && b != null && Math.abs(a - b) <= (eps || 0.011);
  const modelTrend = code => {
    const m = window.__MODEL || {};
    const it = (m.indices || []).filter(x => x.code === code)[0];
    return (it && it.trend) || {};
  };
  const Q = sel => document.querySelector(sel);
  const QA = sel => Array.prototype.slice.call(document.querySelectorAll(sel));
  /* 取某个数值卡片的标题行（.k），避免把标题和数值混在一起断言 */
  const fundLabel = id => { const e = document.getElementById(id);
    return e && e.parentNode ? (e.parentNode.querySelector('.k') || {}).textContent || '' : ''; };
  /* 排行榜里出现的名称（只看真实渲染出来的，不含内嵌 JSON 脚本文本） */
  const rankNames = () => QA('.rank-list .rk-nm').map(e => e.textContent).join('|');

  /* 涨跌展示自洽性：符号、颜色类、箭头三者不能互相打架。
     —— 缩略图上看「▲ -0.35%」这种错是看不出来的（第 4 周第 1 场的 Excel 表也栽在同类问题上）。 */
  function trendCheck(scope, name, needArrow){
    const root = scope ? (Q(scope) || document.body) : document.body;
    const sel = needArrow
      ? '.idx-chg, [data-seg] > b, .seg-main, .rk-pct, .gc, .wc, .g-trend .gt-head > span, .hc-chg'
      : '.bl-right > b';
    const bad = [];
    Array.prototype.slice.call(root.querySelectorAll(sel)).forEach(e => {
      const t = norm(e.textContent);
      const hasUp = t.indexOf('▲') >= 0, hasDn = t.indexOf('▼') >= 0;
      let v = null;
      const mm = t.match(/[+-][0-9]*\.?[0-9]+%/);
      if (mm) v = parseFloat(mm[0]);
      else if (hasUp) v = 1;
      else if (hasDn) v = -1;
      if (v == null || v === 0) return;
      if (v > 0 && e.classList.contains('down')) bad.push('涨却标绿「' + t.slice(0, 16) + '」');
      if (v < 0 && e.classList.contains('up')) bad.push('跌却标红「' + t.slice(0, 16) + '」');
      if (needArrow && v > 0 && !hasUp) bad.push('涨缺▲「' + t.slice(0, 16) + '」');
      if (needArrow && v < 0 && !hasDn) bad.push('跌缺▼「' + t.slice(0, 16) + '」');
    });
    P(name + (needArrow ? ' 箭头/符号/颜色自洽' : ' 符号/颜色自洽'), bad.length === 0,
      bad.slice(0, 3).join(' | ') + (bad.length > 3 ? ' …共' + bad.length : ''));
  }

  /* 主流程是 async 的（渲染完快照还要再拉一次实时），必须等它跑完再断言 */
  function waitReady(){
    return new Promise(res => {
      let n = 0;
      (function poll(){
        const cards = document.querySelectorAll('section.card').length;
        const b = document.getElementById('dataBadge');
        const txt = b ? b.textContent : '';
        if ((cards >= 9 && txt.indexOf('正在') < 0) || ++n > 400) return res();
        setTimeout(poll, 40);
      })();
    });
  }
  await waitReady();

  /* 行情源可达性探针：持仓那几列（现价 / 涨跌 / 区间 / 成交额）全靠东财 push2 实时取，
     而这台机器上 push2* 整族被代理层挡掉（2026-10-16 实测）。
     分清「代码坏了」和「网断了」——网断了就跳过，不报假失败。 */
  let QNET = true;
  try {
    const rq = await fetch(P2 + '/ulist.np/get?fltt=2&invt=2&secids=1.000001&fields=f12,f14,f2&ut=' + UT,
                           {cache: 'no-store'});
    const jq = await rq.json();
    QNET = !!(jq && jq.data);
  } catch (e) { QNET = false; }
  const QWHY = '行情源（东方财富 push2）本机不可达，跳过';
  const Pq = (n, ok, extra) => QNET ? P(n, ok, extra) : S(n, QWHY);

  try {
    /* ══ A. 数据徽标与两页分区 ══════════════════ */
    const badge = T('dataBadge');
    P('数据徽标已定稿（非加载中）', badge.indexOf('正在') < 0, badge);
    /* 徽标只回答「数据到几点」：拿到实时就别再写「快照」；
       拿不到就老老实实写「离线快照 · 几点抓的」。 */
    Pq('拿到实时后徽标不再自称快照', badge.indexOf('离线快照') < 0, badge);
    P('徽标不用笼统说法（「部分数据」这种谁也读不懂的话）', badge.indexOf('部分') < 0, badge);
    P('指数报价拿到了真实数值（东财或腾讯兜底都算数）',
      !!(window.__MODEL && window.__MODEL.indices && window.__MODEL.indices.length >= 4) &&
      window.__MODEL.indices.every(i => i.price > 0 && i.prevClose > 0),
      ((window.__MODEL || {}).indices || []).map(i => i.code + '=' + i.price).join(','));
    /* 一级分页：当天的事在「今天」，过去的事在「历史与位置」，两块同时渲染、只切显隐 */
    const TODAY_IDS = ['sec-watch','sec-global','sec-senti','sec-board','sec-fund','sec-market'];
    const HIST_IDS = ['sec-h-main','sec-h-vol','sec-h-fund','sec-h-globe'];
    const todays = TODAY_IDS.filter(i => document.getElementById(i));
    const hists = HIST_IDS.filter(i => document.getElementById(i));
    P('今天页 ' + TODAY_IDS.length + ' 段齐备', todays.length === TODAY_IDS.length, todays.join(','));
    P('历史页至少 3 段（资金段数据不足时自隐藏）', hists.length >= 3, hists.join(','));
    P('历史段都在 [data-page="hist"] 容器里（切页靠这个显隐）',
      !!Q('#root > [data-page="hist"]') && hists.every(i => Q('[data-page="hist"] #' + i)) !== false,
      String(!!Q('#root > [data-page="hist"]')));
    P('默认停在「今天」页', Q('#root').getAttribute('data-page') === 'today',
      Q('#root').getAttribute('data-page'));
    P('页签 3 个（今天 / 历史与位置 / 我的账本）', QA('#pageTabs .pt').length === 3,
      QA('#pageTabs .pt').map(b => b.textContent).join(' / '));
    P('页签文案 = 今天 + 历史与位置 + 我的账本',
      QA('#pageTabs .pt').map(b => (b.childNodes[0] || {}).nodeValue || '').join('/') === '今天/历史与位置/我的账本',
      QA('#pageTabs .pt').map(b => ((b.childNodes[0] || {}).nodeValue || '').trim()).join(' / '));
    P('时段状态已显示', norm(T('sbState')).length > 0, T('sbState'));

    /* ══ A2. 全球市场：默认沪深京 + 点标签切换 ═══ */
    const mtk = i => QA('#gTabs .mt')[i];
    P('全球市场 4 个标签', QA('#gTabs .mt').length === 4, QA('#gTabs .mt').length + ' 个');
    P('标签文案 = 沪深京/港股/美股/全球',
      QA('#gTabs .mt').map(b => (b.childNodes[0] || {}).nodeValue || '').join('/') === '沪深京/港股/美股/全球',
      QA('#gTabs .mt').map(b => ((b.childNodes[0] || {}).nodeValue || '').trim() + '(' + b.dataset.mkt + ')').join(' / '));
    P('标签顺序 = cn/hk/us/gl', QA('#gTabs .mt').map(b => b.dataset.mkt).join(',') === 'cn,hk,us,gl',
      QA('#gTabs .mt').map(b => b.dataset.mkt).join(','));
    P('有且只有一个标签被选中', QA('#gTabs .mt.on').length === 1, QA('#gTabs .mt.on').length + ' 个');
    let mktStored = null;
    try { mktStored = localStorage.getItem('mb_market'); } catch (e) {}
    P('默认停在沪深京', mtk(0).classList.contains('on') && (mktStored === null || mktStored === 'cn'),
      mtk(0).classList.contains('on') + ' / ' + String(mktStored));
    P('此刻各地几点 → 4 格（沪深京/中国香港/美股/日本）',
      QA('#gBody .tzbar .tzgrid .tzc').length === 4,
      QA('#gBody .tzbar .tzc-n').map(e => e.textContent.replace(/\s+/g, '')).join('/'));
    P('每格都是「市场 · 当地几点 · 开市状态 · 与北京时差」四行齐',
      QA('#gBody .tzbar .tzc').every(e =>
        !!e.querySelector('.tzc-n') && !!e.querySelector('.tzc-t') &&
        !!e.querySelector('.tzc-s') && !!e.querySelector('.tzc-o')),
      T('gBody').replace(/\s+/g, ' ').slice(0, 60));
    P('当地时间是 HH:MM', /^\d{2}:\d{2}$/.test((QA('#gBody .tzbar .tzc-t')[0] || {}).textContent || ''),
      (QA('#gBody .tzbar .tzc-t')[0] || {}).textContent);
    P('时差写成「与北京同一时刻 / 比北京早/晚 N 小时」',
      QA('#gBody .tzbar .tzc-o').every(e => /与北京|比北京/.test(e.textContent)),
      QA('#gBody .tzbar .tzc-o').map(e => e.textContent).join(' / '));
    P('开市状态只有三档：交易中 / 午间 / 没开市',
      QA('#gBody .tzbar .tzc').every(e => /on|break|off/.test(e.className)),
      QA('#gBody .tzbar .tzc').map(e => e.className.replace('tzc ', '')).join(','));
    P('状态含交易中/休市等字样且与格子同源', /交易中|休市|午间|未开盘|已收盘/.test(T('gBody')), '');
    P('写清「没动」不等于没涨', has(T('gBody'), '不等于') && has(T('gBody'), '没动'));
    P('页头说明写的是「三块」（与三个页签一致）',
      (function () { const n = Q('.top-note'); return !!n && has(n.textContent, '三块') && !has(n.textContent, '两块'); })(),
      (Q('.top-note') || {}).textContent ? Q('.top-note').textContent.replace(/\s+/g, ' ').slice(0, 30) : 'no .top-note');
    P('页面不再有旧的两排（城市钟 / 状态灯）',
      !Q('#gBody .clock-bar') && !Q('#gBody .state-bar'));
    P('沪深京指数卡 4 张', QA('#gBody .gidx-grid .gcard').length === 4, QA('#gBody .gidx-grid .gcard').length + ' 张');
    P('沪深京卡片含上证', has(T('gBody'), '上证'));
    P('沪深京附今日概况（涨跌家数 + 两市成交）', has(T('gBody'), '上涨') && has(T('gBody'), '两市成交'));
    P('沪深京有当日分时图', !!Q('#gBody .g-trend svg.spark polyline'));
    P('全球段不再塞历史走势（已迁到「历史与位置」页）',
      !/近[0-9]{2,3}个交易日/.test(norm(T('gBody'))), norm(T('gBody')).slice(0, 40));
    P('底部写明沪深京时段与涨跌停规则', has(T('gNote'), '09:30') && has(T('gNote'), '涨跌停'), T('gNote').slice(0, 34));
    trendCheck('#sec-global', '全球段（沪深京）', true);
    // ── 切港股
    mtk(1).click();
    await new Promise(r => setTimeout(r, 300));
    P('点港股后高亮跟着走', mtk(1).classList.contains('on') && !mtk(0).classList.contains('on'));
    P('切港股后指数卡仍是 4 张（恒生/国企/恒生科技/港科技100）',
      QA('#gBody .gidx-grid .gcard').length === 4 && has(T('gBody'), '恒生科技'),
      QA('#gBody .gidx-grid .gcard').length + ' 张');
    P('切港股后列出常见标的', has(T('gBody'), '腾讯') && has(T('gBody'), '阿里巴巴'), '');
    P('切港股后说明为何不用涨幅榜（牛熊证/窝轮）', has(T('gBody'), '牛熊证'), '');
    P('切港股后分时图换成恒生指数', has(T('gBody'), '恒生指数 · 当日分时'));
    P('切港股后分时提示午休 12:00–13:00', has(T('gBody'), '12:00–13:00'));
    P('切港股后底部标注本地「绿涨红跌」', has(T('gNote'), '绿涨红跌'), T('gNote').replace(/\s+/g, '').slice(0, 60));
    P('切港股后底部标注中国香港无涨跌停', has(T('gNote'), '没有涨跌停'));
    P('市场选择已落本地存储',
      (function () { try { return localStorage.getItem('mb_market'); } catch (e) { return null; } })() === 'hk');
    P('切市场只重绘本段（持仓表仍在）', !!document.getElementById('hRows') && !!document.getElementById('sec-watch'));
    trendCheck('#sec-global', '全球段（港股）', true);
    // ── 切美股
    mtk(2).click();
    await new Promise(r => setTimeout(r, 300));
    P('切美股后出现道指/纳指/标普', has(T('gBody'), '道指') && has(T('gBody'), '纳指') && has(T('gBody'), '标普500'));
    P('切美股后说明仙股与权证的问题', has(T('gBody'), '仙股'));
    P('切美股后分时图换成道琼斯', has(T('gBody'), '道指 · 当日分时'));
    P('切美股后底部区分夏令时/冬令时', has(T('gNote'), '夏令时') && has(T('gNote'), '冬令时'),
      T('gNote').replace(/\s+/g, '').slice(0, 60));
    trendCheck('#sec-global', '全球段（美股）', true);
    // ── 切全球
    mtk(3).click();
    await new Promise(r => setTimeout(r, 300));
    P('切全球后按区域分组', has(T('gBody'), '亚太') && has(T('gBody'), '欧洲') && has(T('gBody'), '美洲') && has(T('gBody'), '商品与汇率'));
    P('全球指数条数 ≥ 50', QA('#gBody .wgrid .wrow').length >= 50, QA('#gBody .wgrid .wrow').length + ' 条');
    P('全球分组标明组内个数与排序口径', has(T('gBody'), '按当日涨跌幅排'));
    P('切全球后分时图换成日经225', has(T('gBody'), '日经225 · 当日分时'));
    P('切全球后底部统一按中国习惯配色', has(T('gNote'), '统一按中国习惯'));
    trendCheck('#sec-global', '全球段（全球）', true);
    // ── 切回沪深京（后面的断言依赖它）
    mtk(0).click();
    await new Promise(r => setTimeout(r, 300));
    P('切回沪深京', has(T('gNote'), '沪深京'));
    P('切回后选择更新为 cn',
      (function () { try { return localStorage.getItem('mb_market'); } catch (e) { return null; } })() === 'cn');

    /* ══ B. 指数卡片：上午/下午分列 ═════════════ */
    const segPct = (code, seg) => pv(Q('[data-idx="' + code + '"] [data-seg="' + seg + '"]'));
    P('上证指数 上午 = 模型 amChgPct',
      near(segPct('000001', 'am'), modelTrend('000001').amChgPct),
      segPct('000001', 'am') + ' vs ' + modelTrend('000001').amChgPct);
    P('上证指数 午后 = 模型 pmChgPct',
      near(segPct('000001', 'pm'), modelTrend('000001').pmChgPct),
      segPct('000001', 'pm') + ' vs ' + modelTrend('000001').pmChgPct);
    P('深证成指 上午/午后 与模型一致',
      near(segPct('399001', 'am'), modelTrend('399001').amChgPct)
        && near(segPct('399001', 'pm'), modelTrend('399001').pmChgPct));
    P('创业板指 午后 与模型一致',
      near(segPct('399006', 'pm'), modelTrend('399006').pmChgPct),
      segPct('399006', 'pm') + ' vs ' + modelTrend('399006').pmChgPct);
    P('四个指数卡片齐全', QA('.idx').length === 4, QA('.idx').length + ' 个');
    /* 涨跌颜色不写死方向（今天涨、明天跌都不该报错），只查「颜色 ↔ 符号」一致 */
    const idxOf0 = c => (((window.__MODEL || {}).indices) || []).find(i => i.code === c) || {};
    const clsOf0 = c => { const e = Q('[data-idx="' + c + '"] .idx-price'); return e ? e.className : ''; };
    const clsOK0 = c => {
      const v = idxOf0(c).chgPct, k = clsOf0(c);
      if(v == null) return !/\b(up|down)\b/.test(k);
      return v > 0 ? /\bup\b/.test(k) : (v < 0 ? /\bdown\b/.test(k) : !/\b(up|down)\b/.test(k));
    };
    P('上证涨跌颜色与涨跌幅符号一致', clsOK0('000001'),
      idxOf0('000001').chgPct + ' / ' + clsOf0('000001').trim());
    P('创业板涨跌颜色与涨跌幅符号一致', clsOK0('399006'),
      idxOf0('399006').chgPct + ' / ' + clsOf0('399006').trim());

    /* ══ C. 三段复盘（细化为东方财富式分段） ══ */
    const RP = sel => QA(sel + ' .rp');
    const RTXT = sel => RP(sel).map(e => e.textContent).join(' ');
    const RLAB = sel => RP(sel).map(e => (e.querySelector('.rp-l') || {}).textContent || '').join('/');
    /* 分时资金（上午/午后）不是每次都能拿到：拿不到时复盘自然少一段、两张卡也不出现。
       ⚠️ 别把「4 段」写死——那会让「今天接口没给数据」看起来像「页面坏了」。 */
    const AMFUND = !!(window.__MODEL && window.__MODEL.fund && window.__MODEL.fund.min);
    const SEG_AM_N = AMFUND ? 4 : 3;
    P('午评主值 = 上午卡片', near(pvT('segAmMain'), segPct('000001', 'am')),
      pvT('segAmMain') + ' vs ' + segPct('000001', 'am'));
    P('午后快评主值 = 午后卡片', near(pvT('segPmMain'), segPct('000001', 'pm')),
      pvT('segPmMain') + ' vs ' + segPct('000001', 'pm'));
    P('收评主值 = 全天涨跌', near(pvT('segAllMain'), modelTrend('000001').chgPct || pvT('segAllMain')),
      String(pvT('segAllMain')));
    P('午评拆成 ' + SEG_AM_N + ' 段（指数/走势/量能' + (AMFUND ? '/资金' : '，分时资金这次没拿到') + '）',
      RP('.seg-am').length === SEG_AM_N, RP('.seg-am').length + ' 段');
    P('午后快评拆成 ' + SEG_AM_N + ' 段', RP('.seg-pm').length === SEG_AM_N, RP('.seg-pm').length + ' 段');
    P('全天复盘拆成 8 段（收盘/量能/广度/板块/题材/资金/梯队/一句话）',
      RP('.seg-all').length === 8, RP('.seg-all').length + ' 段');
    P('每段都带小标签', QA('.seg-full .rp .rp-l').length === QA('.seg-full .rp').length,
      QA('.seg-full .rp .rp-l').length + '/' + QA('.seg-full .rp').length);
    P('上午段标签齐全', RLAB('.seg-am') === (AMFUND ? '指数/走势/成交/资金' : '指数/走势/成交'), RLAB('.seg-am'));
    P('全天段标签齐全', RLAB('.seg-all') === '收盘/成交/涨跌家数/板块/题材/资金/梯队/一句话', RLAB('.seg-all'));
    P('上午给出三大指数方向',
      has(RTXT('.seg-am'), '四个主要指数') && /全线上扬|全线走低|涨跌互现/.test(RTXT('.seg-am')),
      RTXT('.seg-am').slice(0, 50));
    P('上午走势段含形态 + 高低点时刻',
      has(RTXT('.seg-am'), '形态') && /（\d{1,2}:\d{2}）/.test(norm(RTXT('.seg-am'))), RTXT('.seg-am').slice(-60));
    if(AMFUND) P('上午写明分时只算沪市', has(RTXT('.seg-am'), '只算沪市'));
    else S('上午写明分时只算沪市', '这次没取到分时资金');
    P('下午段写明相对上午收盘', has(RTXT('.seg-pm'), '相对上午收盘'));
    if(AMFUND) P('下午段给出资金上下午对照', has(RTXT('.seg-pm'), '主力资金净'));
    else S('下午段给出资金上下午对照', '这次没取到分时资金');
    P('全天段含两市成交 + 上一交易日对比', has(RTXT('.seg-all'), '两市成交') && has(RTXT('.seg-all'), '上一交易日'));
    P('全天段含上涨家数占比', has(RTXT('.seg-all'), '上涨家数占'));
    P('全天段含板块与题材', has(RTXT('.seg-all'), '领涨') && has(RTXT('.seg-all'), '概念板块'));
    P('全天段含涨停梯队', has(RTXT('.seg-all'), '连板'));
    P('全天段含澄清句（已发生的事）', has(RTXT('.seg-all'), '以上都是已经发生的事'));

    /* ══ C2. 复盘按市场分版（A股 / 港股 / 美股 / 全球） ══
       四个市场能取到的数据**厚度差很多**：分时/家数/资金流这几样，港股和美股基本没有，
       全球更是只有点位。所以这一组断言**不只查「切得动」**，还要查
       「拿不到的那块有没有如实说清」——这是最容易糊过去、也最不该糊的地方。 */
    const rtk = i => QA('#rTabs .mt')[i];
    const rkey = () => { try { return localStorage.getItem('mb_report_mkt'); } catch (e) { return null; } };
    P('复盘段 4 个市场标签', QA('#rTabs .mt').length === 4, QA('#rTabs .mt').length + ' 个');
    P('复盘标签顺序 = cn/hk/us/gl', QA('#rTabs .mt').map(b => b.dataset.rpt).join(',') === 'cn,hk,us,gl',
      QA('#rTabs .mt').map(b => b.dataset.rpt).join(','));
    P('复盘段有独立的重绘容器 #rptBody', !!Q('#rptBody'));
    P('复盘默认停在 A 股', rtk(0).classList.contains('on') && (rkey() === null || rkey() === 'cn'),
      rtk(0).classList.contains('on') + ' / ' + String(rkey()));
    P('A 股复盘仍带三段（午评 ' + SEG_AM_N + ' 条 / 午后快评 ' + SEG_AM_N + ' 条 / 收评 8 条）',
      RP('.seg-am').length === SEG_AM_N && RP('.seg-pm').length === SEG_AM_N && RP('.seg-all').length === 8,
      [RP('.seg-am').length, RP('.seg-pm').length, RP('.seg-all').length].join('/'));
    P('A 股复盘仍带当日分时图', !!Q('#rptBody svg.spark polyline'));
    // ── 切港股
    rtk(1).click();
    await new Promise(r => setTimeout(r, 260));
    P('点港股后高亮跟着走', rtk(1).classList.contains('on') && !rtk(0).classList.contains('on'));
    P('切港股后复盘换成 午评 / 午后 / 收评 三段',
      has(T('rptBody'), '午评') && has(T('rptBody'), '午后 · 盘中') && has(T('rptBody'), '收评'));
    P('港股三段各自写死时段（不做时区换算）',
      has(T('rptBody'), '09:30 – 12:00') && has(T('rptBody'), '13:00 – 16:00'));
    P('港股写明午休 12:00–13:00', has(T('rptBody'), '12:00–13:00'));
    P('港股如实说明没有全市场家数 / 资金流口径',
      has(T('rptBody'), '港股全市场的涨跌家数') && has(T('rptBody'), '资金流'));
    P('港股分时图是恒生指数', has(T('rptBody'), '恒生指数 · 当日分时') && !!Q('#rptBody svg.spark polyline'));
    P('港股段不再出现 A 股专属的涨停梯队', !has(T('rptBody'), '涨停梯队'));
    P('港股仍列出常见标的（腾讯 / 阿里巴巴）', has(T('rptBody'), '腾讯') && has(T('rptBody'), '阿里巴巴'));
    P('切走 A 股后原三段确实被换掉（不是隐藏）', !document.getElementById('segAmMain'));
    P('复盘市场已落本地存储', rkey() === 'hk', String(rkey()));
    P('切复盘市场只重绘本段（持仓表仍在）', !!document.getElementById('hRows') && !!document.getElementById('sec-watch'));
    trendCheck('#rptBody', '复盘段（港股）', true);
    // ── 切美股
    rtk(2).click();
    await new Promise(r => setTimeout(r, 260));
    P('美股复盘写清「连续交易、不设午休、跨零点」',
      has(T('rptBody'), '连续交易') && has(T('rptBody'), '跨零点'));
    P('美股时段按时令给出（夏令时 21:30–次日 04:00 / 冬令时 22:30–次日 05:00）',
      /21:30–次日04:00|22:30–次日05:00/.test(norm(T('rptBody'))),
      norm(T('rptBody')).slice(0, 40));
    P('美股如实说明分时接口不给成交额',
      has(T('rptBody'), '这一段没有成交额可写') && has(T('rptBody'), '美股指数查不到这个数'));
    P('美股不再有「午评 / 午后」这种 A 股分法', !has(T('rptBody'), '午后 · 盘中快评'));
    P('美股列出常见标的并声明不是全市场家数',
      has(T('rptBody'), '苹果') && has(T('rptBody'), '不是全市场涨跌家数'));
    P('美股不编造成交额（不出现「成交 0」）', !/成交\s*0\s*[万亿]/.test(norm(T('rptBody'))));
    trendCheck('#rptBody', '复盘段（美股）', true);
    // ── 切全球
    rtk(3).click();
    await new Promise(r => setTimeout(r, 260));
    P('全球复盘给出当日涨跌分布', has(T('rptBody'), '个全球股票指数里') && has(T('rptBody'), '上涨占'));
    P('全球复盘按区域给平均涨跌',
      has(T('rptBody'), '亚太') && has(T('rptBody'), '欧洲') && has(T('rptBody'), '美洲'));
    P('全球复盘有领涨前 8 / 领跌前 8', has(T('rptBody'), '涨幅前 8') && has(T('rptBody'), '跌幅前 8'));
    P('两张榜合计 16 条以上', QA('#rptBody .wgrid .wrow').length >= 16, QA('#rptBody .wgrid .wrow').length + ' 条');
    P('全球如实说明「不存在全球统一的分时」', has(T('rptBody'), '全球统一的分时'));
    P('全球说明这些数字不是同一时刻的', has(T('rptBody'), '不是同一时刻'));
    P('全球这一段确实没有走势图（不硬凑）', QA('#rptBody svg.spark').length === 0,
      QA('#rptBody svg.spark').length + ' 张');
    trendCheck('#rptBody', '复盘段（全球）', true);
    // ── 切回 A 股（后面的断言依赖它）
    rtk(0).click();
    await new Promise(r => setTimeout(r, 260));
    P('切回 A 股复盘（三段与分时图都回来了）',
      has(T('rptBody'), '午评') && !!document.getElementById('segAmMain') && !!Q('#rptBody svg.spark polyline'));
    P('切回后复盘选择更新为 cn', rkey() === 'cn', String(rkey()));

    /* ══ D. 资金流向 ═══════════════════════════ */
    /* ⚠️ 口径必须是**沪深两市**：沪市 -5.61 ＋ 深市 -43.75 = -49.36 亿。
       只取沪市（secid=1.000001 = 上证指数）时全天只有 -5.6 亿，会被误读成
       「成交上万亿、主力才跑 5.6 亿」——那是**口径没标**，不是算错。 */
    P('主力资金全天为净流出', has(fundLabel('fundMain'), '净流出'), fundLabel('fundMain'));
    const yv = el => { const m = norm((el && el.textContent) || '').match(/-?\d+(?:\.\d+)?/); return m ? parseFloat(m[0]) * ((el.textContent.indexOf('万亿') >= 0) ? 10000 : 1) : null; };
    /* 「全天」那张卡只显示绝对值，方向写在标题「净流入/流出」里；四档同理 */
    const mainSigned = () => {
      const el = document.getElementById('fundMain'); if(!el) return null;
      const card = el.closest ? (el.closest('.fcard') || el.parentNode) : el.parentNode;
      const v = yv(el);
      return (!card || /净流出/.test(card.textContent)) ? -Math.abs(v) : Math.abs(v);
    };
    if(AMFUND){
      P('主力资金：上午 ＋ 午后 ≈ 全天（同口径才可能成立）',
        Math.abs((yv(document.getElementById('fundAm')) || 0) + (yv(document.getElementById('fundPm')) || 0)
                 - (mainSigned() || 0)) <= 1.5,
        [yv(document.getElementById('fundAm')), yv(document.getElementById('fundPm')), mainSigned()].join(' / '));
    } else {
      S('主力资金：上午 ＋ 午后 ≈ 全天', '这次没取到分时资金，只有全天一张卡');
      P('分时资金没取到时，三张卡不硬凑（只留全天那张）',
        !document.getElementById('fundAm') && !document.getElementById('fundPm') && !!document.getElementById('fundMain'));
    }
    /* 口径备注在「今天发生了什么」的复盘文字里（flowNote），不在资金三张卡上。
       ⚠️ 写死「沪深两市」会在深市那条源断掉时假失败——页面其实如实标了「只算沪市」。 */
    const _fm = ((window.__MODEL || {}).fund || {}).min || null;
    if(!_fm || !_fm.kl || !_fm.kl.length){
      S('复盘里的资金口径备注', '分时资金流没取到');
    } else if(_fm.src === 'sh' || _fm.src === 'sz'){
      const _scope = _fm.src === 'sh' ? '只算沪市' : '只算深市';
      P('复盘里的资金口径跟实际取到的数据一致（' + _scope + '）',
        has(T('sec-market'), _scope), _scope + ' / src=' + _fm.src);
    } else {
      S('复盘里的资金口径备注', '两市都取到了，按设计不加注');
    }
    P('三张卡不会同时出现两种单市口径（自相矛盾）',
      !(has(T('sec-market'), '只算沪市') && has(T('sec-market'), '只算深市')));
    P('解释「净额 ≠ 成交额」（用户困惑的直接答案）',
      has(T('sec-fund'), '净额') && has(T('sec-fund'), '一买一卖') && has(T('sec-fund'), '并不矛盾'));
    if(AMFUND) P('说明「上午 ＋ 下午 ＝ 全天」这层关系', has(T('sec-fund'), '上午＋下午＝全天'));
    else S('说明「上午 ＋ 下午 ＝ 全天」这层关系', '这次没取到分时资金');
    const st = n => (Q('[data-st="' + n + '"]') || {}).textContent || '';
    /* 四档同样是**沪深两市**口径（沪＋深逐档相加）：
       超大单 +1.1263、大单 -50.4893、中单 -112.3579、小单 +161.7209 —— 与东财官方逐位一致。 */
    P('四档数字都已渲染（超大/大/中/小）',
      ['超大单', '大单', '中单', '小单'].every(n => /[0-9]/.test(st(n))),
      ['超大单', '大单', '中单', '小单'].map(n => st(n)).join(' | '));
    // 结构自洽：超大单 + 大单 = 主力
    const g = n => parseFloat(norm(st(n)).replace(/[^0-9.+-]/g, ''));
    P('超大单+大单 ≈ 全天主力净额',
      Math.abs((g('超大单') + g('大单')) - (mainSigned() || 0)) <= 1.5,
      (g('超大单') + g('大单')).toFixed(2) + ' vs ' + String(mainSigned()));
    /* ⚠️ 容差给到 2 亿：这四项在页面上是**四舍五入后的显示值**（≥100 亿的只留整数位），
       逐项舍入残差各最多 0.5 亿，四项合计最多 2 亿。真值合计严格为 0（净额口径守恒）。 */
    P('四档净额合计约等于 0', Math.abs(g('超大单') + g('大单') + g('中单') + g('小单')) <= 2.0,
      (g('超大单') + g('大单') + g('中单') + g('小单')).toFixed(2));
    const rk = i => (QA('.rank-list')[i] && QA('.rank-list')[i].children[0]) || null;
    const rkTxt = i => (rk(i) ? rk(i).textContent : '');
    P('行业流入榜首行为正净额、带「亿」单位',
      /\+[\d.]+亿/.test(norm(rkTxt(0))), norm(rkTxt(0)).slice(0, 40));
    P('行业流出榜首行为负净额、带「亿」单位',
      /-[\d.]+亿/.test(norm(rkTxt(1))), norm(rkTxt(1)).slice(0, 40));
    P('个股榜首行是「名称 + 净额」结构',
      /[\d.]/.test(rkTxt(2)) && /亿/.test(rkTxt(2)), norm(rkTxt(2)).slice(0, 40));
    /* 只在**同一个榜**里查重复：不同榜单同名是正常的（板块榜与个股榜都可能有同一个名字）。 */
    const stripSuffix = t => String(t).replace(/[\u2160-\u2165]+$/, '').replace(/[\uff08(]\u7533\u4e07[\uff09)]$/, '').trim();
    const rkLists = QA('.rank-list').map(l => Array.prototype.map.call(l.children, c => {
      const e = c.querySelector('.rk-nm');
      return stripSuffix(norm(e ? e.textContent : c.textContent));
    }));
    const rkDup = rkLists.map(ns => ns.filter((x, i) => ns.indexOf(x) !== i)).filter(a => a.length);
    P('同一榜单内没有重复观测项（不同榜可以同名）', rkDup.length === 0,
      rkDup.length ? rkDup[0].join('|') : rkLists.length + ' 个榜');
    P('个股榜至少 3 行、每行都带净额',
      QA('.rank-list')[2].children.length >= 3 &&
      Array.prototype.every.call(QA('.rank-list')[2].children, c => /[\d.]+亿/.test(norm(c.textContent))),
      QA('.rank-list')[2].children.length + ' 行');
    const MG = (window.__MODEL || {}).margin || {};
    P('两融表里「融资余额」与「融资净买入」都在',
      has(document.body.textContent, '融资余额') && has(document.body.textContent, '融资净买入'));
    P('融资余额量级合理（万亿级，单位亿）',
      MG.rzye != null && MG.rzye > 10000, MG.rzye != null ? MG.rzye.toFixed(0) + ' 亿' : '缺数据');
    P('已说明北向资金分两步停披露（5-13 停盘中实时、同年 8-19 停日度净额）',
      has(document.body.textContent, '北向资金')
      && has(document.body.textContent, '5 月 13 日')
      && has(document.body.textContent, '8 月 19 日'));

    /* ══ E. 市场宽度与成交 ═════════════════════ */
    const numOf = id => { const m = norm(T(id)).match(/[\d,]+/); return m ? parseInt(m[0].replace(/,/g, ''), 10) : null; };
    const MB = (window.__MODEL || {}).breadth || {};
    P('上涨家数 = 模型 up', numOf('bUp') === MB.up, numOf('bUp') + ' vs ' + MB.up);
    P('下跌家数 = 模型 down', numOf('bDown') === MB.down, numOf('bDown') + ' vs ' + MB.down);
    P('涨跌家数之和 ≈ 全市场家数',
      Math.abs((numOf('bUp') || 0) + (numOf('bDown') || 0) - ((MB.up || 0) + (MB.down || 0))) <= 2,
      [numOf('bUp'), numOf('bDown')].join(' + '));
    P('涨停家数 = 模型 limitUp', numOf('limUp') === MB.limitUp, numOf('limUp') + ' vs ' + MB.limitUp);
    /* 走备用通道时接口可能回「有壳无值」的数据；这条防的就是它把真实家数冲掉 */
    P('家数与涨跌停都是真实数值（空壳响应没有覆盖掉快照）',
      (MB.up || 0) + (MB.down || 0) > 1000 && (MB.limitUp || 0) > 0,
      [MB.up, MB.down, MB.limitUp].join(' / '));
    P('两市成交额与模型一致（口径 = 沪＋深）',
      (function(){
        /* ⚠️ 不能用 numOf()：它的正则只取整数段，"1.44 万亿" 会被读成 1。
           这里单独带小数解析，再把「万亿」换算成「亿」与模型对齐。 */
        const txt = norm(T('turnoverAll'));
        const mm = txt.match(/[\d,]+(?:\.\d+)?/);
        const a = (window.__MODEL || {}).turnover || {};
        if(!mm || a.amount == null) return false;
        const v = parseFloat(mm[0].replace(/,/g, ''));
        const shown = /万亿/.test(txt) ? v * 10000 : v;
        return Math.abs(shown - a.amount) <= Math.max(1, a.amount * 0.01);
      })(), T('turnoverAll') + ' / ' + ((window.__MODEL || {}).turnover || {}).amount);

    /* ══ F. 图表（今天页） ══════════════════════ */
    const pts = s => (s.getAttribute('points') || '').trim().split(/\s+/).length;
    P('当日分时主图仍在', !!Q('#sec-market svg.spark polyline'));
    if(AMFUND) P('资金流分时图仍在', !!Q('#sec-fund svg.spark polyline'));
    else S('资金流分时图', '这次没取到分时资金');
    P('全球段自带 1 张折线图（当日分时，历史走势已迁走）',
      QA('#sec-global svg.spark polyline').length === 1, QA('#sec-global svg.spark polyline').length + ' 张');
    const nIntra = pts(Q('#sec-market svg.spark polyline'));
    const nFlow = AMFUND ? pts(Q('#sec-fund svg.spark polyline')) : 0;
    /* 点数随开盘时长变化（盘中更少、收盘才是全量）——改成与模型对账 */
    const shTrend0 = (idxOf0('000001').trend) || {};
    const nPts0 = (shTrend0.pts || []).length;
    const nKl0 = ((((window.__MODEL || {}).fund) || {}).min || {}).kl;
    const nKlN = (nKl0 || []).length;
    P('分时曲线点数＝模型里的分时点数', nIntra === nPts0, nIntra + ' / 模型 ' + nPts0);
    if(AMFUND) P('主力资金曲线点数＝模型里的资金流点数', nFlow === nKlN, nFlow + ' / 模型 ' + nKlN);
    else S('主力资金曲线点数', '这次没取到分时资金');
    if(AMFUND) P('资金流曲线含零轴参考线', QA('#sec-fund svg.spark line').length >= 2,
      QA('#sec-fund svg.spark line').length + ' 条');
    P('分时图含午间休市分界线', QA('#sec-market svg.spark line').length >= 1);
    P('全页折线图 = 今天 ' + (AMFUND ? 3 : 2) + ' 张 + 历史 6 张',
      QA('svg.spark polyline').length === (AMFUND ? 9 : 8), QA('svg.spark polyline').length + ' 张');

    /* ══ F2. 分页切换 + 历史页 ═════════════════ */
    /* ⚠️ 页签按钮每次切页都会被 paintPages() 重绘 ⇒ 必须现查现用，
          缓存下来的旧节点已经脱离 DOM，点击不会冒泡到容器上。 */
    const ptab = k => Q('#pageTabs .pt[data-page="' + k + '"]');
    ptab('hist').click();                   // 切到「历史与位置」
    await new Promise(r => setTimeout(r, 350));
    P('切页后 #root[data-page] 变成 hist', Q('#root').getAttribute('data-page') === 'hist',
      Q('#root').getAttribute('data-page'));
    P('切页后今天页整块不可见（不是重建，是 display:none）',
      QA('#root > [data-page="today"] section.card').every(e => e.getBoundingClientRect().height === 0),
      QA('#root > [data-page="today"] section.card').length + ' 段被隐藏');
    P('切页后历史段可见',
      hists.every(i => document.getElementById(i).getBoundingClientRect().height > 0),
      hists.join(','));
    P('切页后持仓表还在 DOM 里（正在填的内容不会丢）', !!document.getElementById('hRows'));
    P('页签高亮跟着走', ptab('hist').classList.contains('on') && !ptab('today').classList.contains('on'));
    P('当前页已落本地存储',
      (function () { try { return localStorage.getItem('mb_page'); } catch (e) { return null; } })() === 'hist');
    // 历史 1：4 张指数卡 + 位置尺
    P('历史 1 · 4 张指数卡', QA('#sec-h-main .hist-card').length === 4,
      QA('#sec-h-main .hist-card').length + ' 张');
    P('历史 1 · 每张卡都有区间位置尺', QA('#sec-h-main .posbar .pb-dot').length === 4,
      QA('#sec-h-main .posbar .pb-dot').length + ' 条');
    P('历史 1 · 位置尺给出「区间最低/最高」',
      has(T('sec-h-main'), '区间最低') && has(T('sec-h-main'), '区间最高'));
    P('历史 1 · 位置尺给出位置文案', /接近区间上沿|接近区间下沿|区间中部偏/.test(norm(T('sec-h-main'))),
      norm(T('sec-h-main')).slice(0, 30));
    P('历史 1 · 四张卡都是近 60 日走势（允许 ±1 个点）',
      QA('#sec-h-main .hist-card svg.spark polyline').length === 4 &&
      QA('#sec-h-main .hist-card svg.spark polyline').every(s => Math.abs(pts(s) - 60) <= 1),
      QA('#sec-h-main .hist-card svg.spark polyline').map(s => pts(s)).join('/'));
    P('历史 1 · 覆盖 上证/深证/创业板/沪深300',
      ['上证', '深证', '创业板', '沪深300'].every(w => has(T('sec-h-main'), w)));
    P('历史 1 · 写明「认位置，比猜方向有用」', has(T('sec-h-main'), '认位置'));
    // 历史 2：量能柱
    const hbars = QA('#sec-h-vol .volbars .vb');
    P('历史 2 · 成交额柱状图 6 根', hbars.length === 6, hbars.length + ' 根');
    P('历史 2 · 最后一根标为「今天」', hbars.length > 0 && hbars[hbars.length - 1].classList.contains('now'));
    P('历史 2 · 给出「今天 / 均量」比值', has(T('sec-h-vol'), '今天 / 均量'), '');
    P('历史 2 · 给出较上一交易日放缩量', /放量|缩量/.test(T('sec-h-vol')));
    P('历史 2 · 每根柱都挂了读数锚点（data-bar）', hbars.every(b => !!b.getAttribute('data-bar')));
    // 历史 5（末段）：全球长周期
    P('历史 · 全球长周期 2 张卡（恒生 + 道指）',
      QA('#sec-h-globe .hist-card').length === 2, QA('#sec-h-globe .hist-card').length + ' 张');
    P('历史 · 全球卡与 A 股用同一把尺子（也有位置尺）',
      QA('#sec-h-globe .posbar .pb-dot').length === 2);
    P('历史 · 标注中国香港 / 美国',
      has(T('sec-h-globe'), '中国香港') && has(T('sec-h-globe'), '美国'));
    // 目录：切页后换成历史页那一份，编号从 1 重排
    const tocH = QA('#toc .toc-item');
    P('切页后目录换成历史页那份（条数 = 实际渲染出的历史段）', tocH.length === hists.length,
      tocH.length + ' 项 / ' + hists.length + ' 段');
    P('历史页目录编号从 1 连续排',
      tocH.map(a => (a.querySelector('i') || {}).textContent).join('') === hists.map((_, i) => i + 1).join(''),
      tocH.map(a => a.querySelector('i').textContent).join(''));
    P('历史页目录锚点都指向真实分区',
      tocH.map(a => a.dataset.go).every(id => !!document.getElementById(id)),
      tocH.map(a => a.dataset.go).join('→'));
    P('段标题编号与目录一致（自隐藏的段不占号）',
      hists.map(i => document.getElementById(i).querySelector('.card-head h2 .no').textContent).join('') ===
      hists.map((_, i) => i + 1).join(''),
      hists.map(i => document.getElementById(i).querySelector('.card-head h2 .no').textContent).join(''));

    /* ══ F3. 图表悬停读数（鼠标过去自动出数值） ═ */
    const tipEl = () => document.getElementById('chartTip');
    const hoverAt = (el, fx) => {
      if (!el) return;                       // 图不在就让它老老实实 FAIL，别把整段脚本带崩
      const r = el.getBoundingClientRect();
      el.dispatchEvent(new PointerEvent('pointermove',
        {clientX: r.left + r.width * fx, clientY: r.top + r.height / 2, bubbles: true}));
    };
    const svgI = Q('#sec-h-main .hist-card svg.spark');
    hoverAt(svgI, 0.5);
    await new Promise(r => setTimeout(r, 90));
    const t1 = tipEl();
    P('悬停折线图 → 浮层出现', !!t1 && !t1.hidden, t1 ? ('hidden=' + t1.hidden) : 'no #chartTip');
    P('浮层内容含日期与数值', !!t1 && /\d{4}-\d{2}-\d{2}/.test(t1.textContent) && /\d/.test(t1.textContent),
      t1 ? t1.textContent.replace(/\s+/g, '').slice(0, 46) : '');
    const cur1 = svgI.querySelector('.cur');
    P('悬停折线图 → 十字准线显示', !!cur1 && cur1.style.opacity === '1',
      cur1 ? ('opacity=' + cur1.style.opacity) : 'no .cur');
    P('准线横坐标跟着鼠标走', !!cur1 && parseFloat(cur1.querySelector('.cur-x').getAttribute('x1')) > 0,
      cur1 ? cur1.querySelector('.cur-x').getAttribute('x1') : '');
    const txt1 = t1 ? t1.textContent : '';
    hoverAt(svgI, 0.85);
    await new Promise(r => setTimeout(r, 90));
    P('移到另一个点 → 读数跟着变', !!t1 && t1.textContent !== txt1,
      t1 ? t1.textContent.replace(/\s+/g, '').slice(0, 40) : '');
    // 柱状图读数走 BARS 注册表（不是 data-tip 属性）
    const vbEl = QA('#sec-h-vol .vb[data-bar]')[2];
    hoverAt(vbEl, 0.5);
    await new Promise(r => setTimeout(r, 90));
    P('悬停柱状图 → 浮层出现且含成交额',
      !!t1 && !t1.hidden && has(t1.textContent, '两市成交额'),
      t1 ? t1.textContent.replace(/\s+/g, '').slice(0, 56) : '');
    P('柱状图读数补上「当日上证」涨跌（量价一起看）',
      !!t1 && has(t1.textContent, '当日上证'), t1 ? t1.textContent.replace(/\s+/g, '').slice(0, 64) : '');
    document.body.dispatchEvent(new PointerEvent('pointerleave', {bubbles: true}));
    await new Promise(r => setTimeout(r, 90));
    P('移出图表 → 浮层隐藏', !!t1 && t1.hidden, t1 ? ('hidden=' + t1.hidden) : '');
    // 切回「今天」页（后面的断言依赖它）
    ptab('today').click();
    await new Promise(r => setTimeout(r, 350));
    P('切回「今天」页', Q('#root').getAttribute('data-page') === 'today',
      Q('#root').getAttribute('data-page'));
    P('切回后目录又是今天页那份 ' + TOCS.today.length + ' 项',
      QA('#toc .toc-item').length === TOCS.today.length,
      QA('#toc .toc-item').length + ' 项');

    /* ══ G. 持仓双视图：监控 / 纪律线 ═══════════ */
    const vs = QA('.view-switch .vs');
    P('视图切换有 2 个按钮', vs.length === 2, vs.map(b => b.textContent).join(' / '));
    P('默认停在「监控」视图', vs[0].classList.contains('on') && !vs[1].classList.contains('on'));
    const hWatch = Q('#hHead').textContent;
    P('监控视图表头 = 名称/代码/现价/涨跌/涨跌额/区间/成交额',
      has(hWatch, '今日涨跌') && has(hWatch, '今日最低 – 最高') && !has(hWatch, '我的成本价'),
      hWatch.replace(/\s+/g, ' ').slice(0, 64));
    P('监控视图不出现纪律线列', !document.getElementById('pnl-0') && !document.getElementById('trg-0'));
    Pq('监控视图成交额量级正常（按亿计）',
      /亿|万/.test(T('vol-0')) && !/万亿/.test(T('vol-0')), T('vol-0'));
    Pq('监控视图涨跌额带符号', /[+−-]/.test(T('amt-0')), T('amt-0'));
    Pq('监控视图区间写成「最低~最高」', /~/.test(T('rng-0')), T('rng-0'));
    const rowsBefore = QA('#hRows tr[data-row]').length;
    vs[1].click();
    await new Promise(r => setTimeout(r, 400));
    const hDisc = Q('#hHead').textContent;
    P('纪律线视图表头含成本/红线/触发价',
      has(hDisc, '我的成本价') && has(hDisc, '纪律红线') && has(hDisc, '下一档触发价'),
      hDisc.replace(/\s+/g, ' ').slice(0, 72));
    P('切视图不重建行（同一份数据）', QA('#hRows tr[data-row]').length === rowsBefore,
      QA('#hRows tr[data-row]').length + ' 行 / 原 ' + rowsBefore);
    P('切视图后浮动盈亏仍在（填写内容没丢）',
      /%|缺/.test(QA('[id^="pnl-"]').map(e => e.textContent).join(' ')));
    let savedView = null;
    try { savedView = localStorage.getItem('mb_hold_view'); } catch (e) {}
    P('视图选择已落本地存储', savedView === 'disc', String(savedView));
    vs[0].click();
    await new Promise(r => setTimeout(r, 400));
    P('切回「监控」视图', Q('#hHead').textContent.indexOf('我的成本价') < 0);

    /* ══ G2. 持仓与纪律线（回归） ══════════════ */
    // 下面的断言针对「纪律线」视图（含成本价 / 浮动盈亏 / 红线 / 触发价）
    vs[1].click();
    await new Promise(r => setTimeout(r, 400));
    P('已切到纪律线视图', has(Q('#hHead').textContent, '我的成本价'));
    /* 以下四条原先照着当时的价写死，行情一动全错。
       现在只从页面取「现价 / 成本价 / 红线价」，其余一律现算 —— 任何一天打开都成立。 */
    const numOf0 = t => { const m = String(t).replace(/[−–—]/g, '-').match(/-?[\d,]+(?:\.\d+)?/); return m ? parseFloat(m[0].replace(/,/g, '')) : null; };
    const near0 = (a, b, tol) => a != null && b != null && Math.abs(a - b) <= (tol == null ? 0.02 : tol);
    const inNum0 = k => { const e = Q('#hRows input[data-i="0"][data-k="' + k + '"]'); const v = e ? parseFloat(e.value) : NaN; return isFinite(v) ? v : null; };
    const q0 = (((window.__MODEL || {}).quoteMap) || {})['sh510300'];
    const PX0 = q0 && q0.price != null ? q0.price : null;
    const COST0 = inNum0('cost'), RED0 = inNum0('red');
    const expPnl = (PX0 != null && COST0) ? (PX0 - COST0) / COST0 * 100 : null;
    /* 红线价与「下一档触发价」是两个数，不能互用。
       trg-0 文本自带两个数（还需涨 X%、预设价），直接用它们互相校验。 */
    const trgTxt0 = T('trg-0');
    const trgNums0 = (trgTxt0.replace(/[−–—]/g, '-').match(/-?[\d,]+(?:\.\d+)?/g) || []).map(x => parseFloat(x.replace(/,/g, '')));
    const showTrg0 = trgNums0.length > 0 ? trgNums0[0] : null;
    const setTrg0 = trgNums0.length > 1 ? trgNums0[1] : null;
    const expTrg = (PX0 != null && setTrg0 != null) ? (setTrg0 - PX0) / PX0 * 100 : null;
    const fmt0 = v => (v == null ? '—' : v.toFixed(2) + '%');
    Pq('现价自动取自行情（与模型报价一致）', near0(numOf0(TV('px-0')), PX0, 0.0015), TV('px-0') + ' / 模型 ' + PX0);
    Pq('浮动盈亏＝(现价−成本)/成本', near0(numOf0(T('pnl-0')), expPnl), T('pnl-0') + ' / 应为 ' + fmt0(expPnl));
    Pq('沪深300ETF 红线显示「距红线还有 X%」+ 现价', has(T('red-0'), '距红线还有') && has(T('red-0'), '现价'), T('red-0'));
    Pq('触发价还需涨＝(预设触发价−现价)/现价', near0(showTrg0, expTrg),
      trgTxt0 + ' / 应为 ' + fmt0(expTrg));
    const n0 = QA('#hRows tr').length;
    document.getElementById('addRow').click();
    P('增行 ' + n0 + ' → ' + QA('#hRows tr').length, QA('#hRows tr').length === n0 + 1);
    P('空行不报错', has(T('pnl-' + n0), '缺成本价'));
    const ci = Q('#hRows input[data-i="0"][data-k="cost"]');
    ci.value = '4.00'; ci.dispatchEvent(new Event('input', {bubbles: true}));
    Pq('改成本 4.00 → 盈亏按新成本重算',
      near0(numOf0(T('pnl-0')), PX0 == null ? null : (PX0 - 4.00) / 4.00 * 100),
      T('pnl-0') + ' / 应为 ' + fmt0(PX0 == null ? null : (PX0 - 4.00) / 4.00 * 100));
    ci.value = '4.50'; ci.dispatchEvent(new Event('input', {bubbles: true}));
    const ri = Q('#hRows input[data-i="0"][data-k="red"]');
    ri.value = '4.30'; ri.dispatchEvent(new Event('input', {bubbles: true}));
    Pq('红线高于现价 → 已跌破', has(T('red-0'), '已跌破'));
    let lsOK = false;
    try { localStorage.setItem('__t','1'); lsOK = localStorage.getItem('__t')==='1'; localStorage.removeItem('__t'); } catch(e){}
    P('本地存储可用', lsOK);
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem('mb_holdings_v1') || 'null'); } catch(e){}
    P('持仓已写入本地存储', Array.isArray(saved), saved ? saved.length + ' 行' : 'null');
    /* D1：首次保存后，必须弹过一次「只存本设备、记得导出」，且标记只写一次 */
    P('首次保存触发「只存本设备」提醒（D1）',
      (function(){ try{ return localStorage.getItem('mb_hint_shown'); }catch(e){ return null; } })() === '1'
        && window.__ALERTS.length >= 1 && /这台设备/.test(window.__ALERTS[0] || ''),
      'alerts=' + window.__ALERTS.length + ' first=' + String(window.__ALERTS[0] || '').slice(0, 20));

    /* ══ G3. 持仓表交互：逐条修复过的三件事 ═══ */
    P('代码列表头已写明「只填数字」', has(Q('#sec-watch thead').textContent, '只填数字'));
    P('代码框 placeholder 不含 sh/sz 前缀',
      (Q('#hRows input[data-k="code"]') || {}).placeholder === '510300',
      (Q('#hRows input[data-k="code"]') || {}).placeholder);

    // ① 逐字输入不丢焦点、不重建表格
    const rc0 = QA('#hRows tr').length;
    const c0 = Q('#hRows input[data-i="0"][data-k="code"]');
    c0.value = ''; c0.dispatchEvent(new Event('input', {bubbles: true}));
    c0.focus();
    let keepFocus = true, keepRows = true;
    '600519'.split('').forEach(ch => {
      c0.value += ch;
      c0.dispatchEvent(new Event('input', {bubbles: true}));
      if (document.activeElement !== c0) keepFocus = false;
      if (QA('#hRows tr').length !== rc0) keepRows = false;
    });
    P('① 逐字输入 6 个字符不丢焦点', keepFocus,
      'activeElement=' + (document.activeElement ? (document.activeElement.tagName + ':' + (document.activeElement.dataset.k || '')) : '-'));
    P('① 逐字输入不重建表格', keepRows, QA('#hRows tr').length + ' 行 / 原 ' + rc0 + ' 行');
    P('① 代码输入值完整保留', c0.value === '600519', c0.value);

    // ② 只输纯数字代码 → 自动带出名称与现价
    await new Promise(r => setTimeout(r, 8000));
    P('② 自动带出名称（600519 → 贵州茅台）',
      Q('#hRows input[data-i="0"][data-k="name"]').value === '贵州茅台',
      Q('#hRows input[data-i="0"][data-k="name"]').value);
    Pq('② 自动带出现价（未手填）', !!document.getElementById('px-0').querySelector('input[readonly]'), T('px-0'));
    Pq('② 状态提示「已带出现价」', has(T('lk-0'), '已带出现价'), T('lk-0'));

    // ③ 粘进来的 sh/sz 前缀会被自动清掉
    const c1 = Q('#hRows input[data-i="0"][data-k="code"]');
    c1.value = 'sh000001';
    c1.dispatchEvent(new Event('change', {bubbles: true}));
    P('③ 粘进来的 sh 前缀自动去掉', c1.value === '000001', c1.value);

    await new Promise(r => setTimeout(r, 8000));
    const picks = QA('#hRows tr.pick-tr');
    P('③ 一个代码对应多个结果时给出候选行', picks.length === 1, picks.length + ' 行');
    const cand = QA('#hRows tr.pick-tr button.pick');
    let hitIdx = -1;
    cand.forEach((b, n) => { if (b.textContent.indexOf('平安银行') >= 0) hitIdx = n; });
    P('③ 候选里有上证指数', cand.some(b => b.textContent.indexOf('上证指数') >= 0));
    P('③ 候选里有平安银行', hitIdx >= 0, cand.map(b => b.textContent).join(' / ').slice(0, 100));
    if (hitIdx >= 0) {
      cand[hitIdx].click();
      await new Promise(r => setTimeout(r, 8000));
      P('③ 点选后名称写入 平安银行',
        Q('#hRows input[data-i="0"][data-k="name"]').value === '平安银行',
        Q('#hRows input[data-i="0"][data-k="name"]').value);
      P('③ 点选后代码为 000001', Q('#hRows input[data-i="0"][data-k="code"]').value === '000001',
        Q('#hRows input[data-i="0"][data-k="code"]').value);
      P('③ 候选行已收起', QA('#hRows tr.pick-tr').length === 0);
      Pq('③ 点选后取到现价', !!document.getElementById('px-0').querySelector('input[readonly]'), T('px-0'));
    }

    // ④ 手填 / 用行情 切换
    const man = document.getElementById('px-0').querySelector('a[data-man]');
    Pq('④ 自动价旁有「手填」入口', !!man);
    if (man) {
      man.click();
      const pin = document.getElementById('px-0').querySelector('input[data-k="price"]');
      P('④ 点「手填」后变成可编辑', !!pin, pin ? pin.value : '—');
      const aut = document.getElementById('px-0').querySelector('a[data-auto]');
      P('④ 手填后出现「用行情」入口', !!aut);
      if (aut) { aut.click(); P('④ 点「用行情」后回到自动价', !!document.getElementById('px-0').querySelector('input[readonly]')); }
    }

    // ⑤ 场外基金：取不到行情要说清原因，而不是含糊的「没找到」
    const c3 = Q('#hRows input[data-i="0"][data-k="code"]');
    c3.value = '';
    c3.dispatchEvent(new Event('input', {bubbles: true}));
    c3.value = '110022';
    c3.dispatchEvent(new Event('input', {bubbles: true}));
    await new Promise(r => setTimeout(r, 8000));
    P('⑤ 场外基金代码给出明确说明', has(T('lk-0'), '场外基金'), T('lk-0'));

    /* ══ I. 目录导航：一键跳到对应段落 ═════════ */
    const toc = QA('#toc .toc-item');
    P('今天页目录 ' + TOCS.today.length + ' 项', toc.length === TOCS.today.length, toc.length + ' 项');
    const expNo = Array.from({length: TOCS.today.length}, (_, i) => i + 1).join('');
    P('目录项编号为 1–' + TOCS.today.length,
      toc.map(a => (a.querySelector('i') || {}).textContent).join('') === expNo,
      toc.map(a => (a.querySelector('i') || {}).textContent).join(''));
    const tocTxt = toc.map(a => a.textContent.replace(/\s+/g, '')).join('|');
    P('目录首项是「我的持仓」', tocTxt.indexOf('我的持仓') >= 0);
    P('目录第 2 项是「全球市场」', tocTxt.indexOf('全球市场') >= 0, tocTxt.slice(0, 60));
    P('目录末项是「今天发生了什么」', tocTxt.indexOf('今天发生了什么') >= 0, tocTxt.slice(-40));
    const goTargets = toc.map(a => a.dataset.go);
    P('目录锚点都指向真实分区', goTargets.every(id => !!document.getElementById(id)),
      goTargets.join('→'));
    P('目录已绑定点击', !!document.getElementById('toc').onclick);
    P('目录当前项已高亮', QA('#toc .toc-item.on').length >= 1);
    // 无头 Chrome 下 smooth 滚动不可靠，这里验证「有没有发起跳向对应分区」这件事本身
    let scrolledTo = null;
    const origSI = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function () { scrolledTo = this.id || ''; };
    toc[0].click();
    P('点「我的持仓」跳向该分区', scrolledTo === 'sec-watch', String(scrolledTo));
    toc[1].click();
    P('点「全球市场」跳向该分区', scrolledTo === 'sec-global', String(scrolledTo));
    toc[3].click();
    P('点「板块题材」跳向该分区', scrolledTo === 'sec-board', String(scrolledTo));
    toc[2].click();
    P('点「市场情绪」跳向该分区', scrolledTo === 'sec-senti', String(scrolledTo));
    toc[toc.length - 1].click();
    P('点目录末项跳向该分区', scrolledTo === TODAY_IDS[TODAY_IDS.length - 1], String(scrolledTo));
    Element.prototype.scrollIntoView = origSI;
    // 滚动高亮：真的滚下去，看高亮有没有跟着走
    const maxY = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight) - window.innerHeight;
    window.scrollTo(0, maxY);
    window.dispatchEvent(new Event('scroll'));
    await new Promise(r => setTimeout(r, 700));
    const onNow = Q('#toc .toc-item.on');
    P('滚到底后高亮切到末段（scrollY ' + Math.round(window.scrollY) + ' / 可滚 ' + Math.round(maxY) + '）',
      !!onNow && onNow.dataset.go === TODAY_IDS[TODAY_IDS.length - 1], onNow ? onNow.dataset.go : '—');
    window.scrollTo(0, 0);
    window.dispatchEvent(new Event('scroll'));
    await new Promise(r => setTimeout(r, 700));
    const onTop = Q('#toc .toc-item.on');
    P('滚回顶部后高亮回到「我的持仓」',
      !!onTop && onTop.dataset.go === 'sec-watch', onTop ? onTop.dataset.go : '—');

    /* ══ K. 第 5 段：板块与题材 ═════════════════ */
    const bl = QA('#sec-board .board-list');
    P('板块榜 4 张（行业涨/行业跌/概念涨/个股涨）', bl.length === 4, bl.length + ' 张');
    P('行业涨幅榜首行有领涨股', has(QA('#sec-board .board-list')[0].children[0].textContent, '领涨'),
      QA('#sec-board .board-list')[0].children[0].textContent.replace(/\s+/g, ' ').slice(0, 40));
    P('板块榜每行都带涨跌幅', QA('#sec-board .board-list li .bl-right b').length ===
      QA('#sec-board .board-list li').length, '');
    P('第 5 段已说明与资金榜是两套算法', has(T('sec-board'), '两套算法'));
    P('第 5 段含涨停家数口径说明（不含新股）', has(T('sec-board'), '不含当日新股') || has(T('sec-board'), '涨停'), '');

    /* ══ K2. 快讯段已移除，只留复盘侧的内容断言 ═════════════ */
    P('A 股复盘含「焦点复盘」块（数据焦点 + 盘后要闻）',
      has(T('rptBody'), '焦点复盘') && has(T('rptBody'), '要闻'));

    /* ══ L. 我的账本（工作台） ═════════════════════
       三段：交易台账（只记流水，其余自动算）→ 持仓与净值（含净值曲线）→ 复盘日记。
       测的是「新手最怕的那件事」：手算成本、手算盈亏、忘记当时怎么想的。 */
    const wait = ms => new Promise(r => setTimeout(r, ms));
    const E = id => document.getElementById(id);
    const OA = window.alert, OC = window.confirm;
    window.alert = function () {};       // 无头下弹窗会卡住流程，直接吞掉
    window.confirm = function () { return true; };   // 确认框一律放行，才能验证「确认后真执行了」
    const BK_IDS = ['sec-bk-ledger', 'sec-bk-pos', 'sec-bk-journal'];

    ptab('book').click();
    await wait(380);
    P('页签共 3 个（今天 / 历史与位置 / 我的账本）', QA('#pageTabs .pt').length === 3,
      QA('#pageTabs .pt').map(b => b.textContent.replace(/\s+/g, '')).join('/'));
    P('已切到「我的账本」页', Q('#root').getAttribute('data-page') === 'book',
      Q('#root').getAttribute('data-page'));
    P('切页后历史段整块不可见',
      hists.every(i => document.getElementById(i).getBoundingClientRect().height === 0));
    P('账本 3 段齐备', BK_IDS.every(i => !!E(i)), BK_IDS.filter(i => E(i)).join(','));
    P('账本 3 段都在 [data-page="book"] 容器里',
      BK_IDS.every(i => !!(E(i) && E(i).closest('[data-page="book"]'))));
    const tocB = QA('#toc .toc-item');
    P('账本页目录换成账本那份 3 项', tocB.length === 3, tocB.length + ' 项');
    P('账本页目录编号 1/2/3',
      tocB.map(a => (a.querySelector('i') || {}).textContent).join('') === '123',
      tocB.map(a => a.querySelector('i').textContent).join(''));
    P('账本页目录锚点都指向真实分区',
      tocB.map(a => a.dataset.go).every(id => !!document.getElementById(id)),
      tocB.map(a => a.dataset.go).join('→'));
    P('账本段标题编号与目录一致',
      BK_IDS.map(i => E(i).querySelector('.card-head h2 .no').textContent).join('') === '123',
      BK_IDS.map(i => E(i).querySelector('.card-head h2 .no').textContent).join(''));

    /* ── L1. 空白起点 ─────────────────────────── */
    E('lgClear').click();
    await wait(260);
    E('bkSnapClear').click();
    await wait(260);
    P('清空台账后回到空白起点（并说明怎么开始）',
      has(T('sec-bk-ledger'), '还没有记录') && has(T('sec-bk-ledger'), '填入一笔示例'),
      norm(T('sec-bk-ledger')).slice(-40));
    P('空白时持仓段提示去上一段记一笔', has(T('sec-bk-pos'), '台账里还没有买过的标的'));
    P('只有 0 条快照时不画线，并写明「越早开始越好」',
      has(T('sec-bk-pos'), '画不成线') && has(T('sec-bk-pos'), '越早开始越好'));
    P('写明成本算法且与券商一致（卖出不改成本）', has(T('sec-bk-ledger'), '除以总份数'));
    P('写明「手算成本最容易出错，所以不让你算」', has(T('sec-bk-ledger'), '手算成本是最容易出错'));
    P('写明数据只在本机（不上传）',
      has(T('sec-bk-pos'), '本地存储') && has(T('sec-bk-pos'), '不会上传'));
    /* D3：备份状态可见——没导出过就提示「还没有导出过备份」 */
    P('账本页显示备份状态（D3，未导出时给行动提示）',
      has(T('sec-bk-pos'), '还没有导出过备份'),
      T('bkExpAt'));

    /* ── 云端备份：登录可选，不登录不许挡路 ───────────── */
    P('账本页顶部有「云端备份」一条', !!Q('#cloudBar'));
    /* 两个构建变体的文案不同，但都必须诚实：
       线上版 = 只在正式站点连得上；本地包 = 数据只存这台设备、要搬就备份到网上 */
    P('云端条文案如实（线上版说只在正式站点可用 / 本地包说只存这台设备）',
      has(T('cloudBar'), '只在正式站点上连得上') || has(T('cloudBar'), '数据只存这台设备'),
      norm(T('cloudBar')).slice(0, 34));
    P('连不上云端时不给「登录 / 同步」按钮（不给点了必然失败的按钮）',
      !Q('#cbIn') && !Q('#cbSync'));
    P('云端条状态位可读（data-state 自检 / 排查都靠它）',
      ['offsite', 'local'].indexOf(Q('#cloudBar').dataset.state) >= 0, Q('#cloudBar').dataset.state);

    /* ── L2. 记一笔：金额自动算，使用者只填 4 样 ─── */
    const lgin = (i, k, v) => {
      const e = Q('#lgRows input[data-i="' + i + '"][data-k="' + k + '"]');
      if (!e) return null;
      e.value = v; e.dispatchEvent(new Event('input', {bubbles: true}));
      return e;
    };
    E('lgAdd').click();
    await wait(200);
    P('点「+ 记一笔」多出一行', QA('#lgRows tr[data-lgr]').length === 1,
      QA('#lgRows tr[data-lgr]').length + ' 行');
    P('新行列齐 7 列（日期/代码/买卖/成交价/份数/金额/删）',
      QA('#lgRows tr[data-lgr] td').length === 7);
    lgin(0, 'price', '4.000'); lgin(0, 'qty', '1000');
    await wait(150);
    P('金额自动算：4.000 × 1000 = 4,000.00', has(T('lgamt-0'), '4,000.00'), T('lgamt-0'));
    P('代码还空着时不算进持仓（不拿空代码去凑一个标的）',
      has(T('sec-bk-pos'), '台账里还没有买过的标的') && !has(T('lgSummary'), '1,000份'),
      norm(T('sec-bk-pos')).slice(0, 40));
    lgin(0, 'code', '510300');
    await wait(300);
    P('填上代码立刻进持仓：1,000 份 / 成本价 4.000（不等联网查名称）',
      has(T('lgSummary'), '1,000份') && has(T('lgSummary'), '4.000'),
      norm(T('lgSummary')).slice(0, 60));

    /* ── L3. 第二笔买入 → 移动加权平均 ─────────── */
    E('lgAdd').click();
    await wait(200);
    lgin(1, 'code', '510300'); lgin(1, 'price', '4.400'); lgin(1, 'qty', '500');
    await wait(150);
    P('第二笔金额自动算 2,200.00', has(T('lgamt-1'), '2,200.00'), T('lgamt-1'));
    P('两笔买入按移动加权平均合并成本 4.133（＝7,000÷1,500）',
      has(T('lgSummary'), '4.133'), norm(T('lgSummary')).slice(0, 70));
    P('持仓合计 1,500 份', has(T('lgSummary'), '1,500份'));
    P('按成本计的投入合计 6,200.00', has(T('lgSummary'), '6,200.00'));

    /* ── L4. 卖出：只减份数、不改成本、结转已实现 ─ */
    E('lgAdd').click();
    await wait(200);
    Q('#lgRows button[data-lgi="2"][data-side="sell"]').click();
    await wait(200);
    P('买卖开关可切到「卖出」',
      Q('#lgRows button[data-lgi="2"][data-side="sell"]').classList.contains('on'));
    lgin(2, 'code', '510300'); lgin(2, 'price', '5.000'); lgin(2, 'qty', '500');
    await wait(300);
    P('卖出只减份数：1,500 → 1,000 份', has(T('lgSummary'), '1,000份'), norm(T('lgSummary')).slice(0, 60));
    P('卖出不改成本价：仍是 4.133',
      has(T('lgSummary'), '4.133'));
    P('已实现盈亏 +433.33（＝(5.000−4.133)×500）',
      has(T('lgSummary'), '+433.33'), norm(T('lgSummary')).slice(-40));

    /* ── L5. 超卖：拦下来，不给负数 ───────────── */
    E('lgAdd').click();
    await wait(200);
    Q('#lgRows button[data-lgi="3"][data-side="sell"]').click();
    await wait(150);
    lgin(3, 'code', '510300'); lgin(3, 'price', '4.500'); lgin(3, 'qty', '99999');
    await wait(300);
    P('超卖被拦下并说明「超出部分按 0 计」',
      has(T('lgSummary'), '卖出份数超过持仓'), norm(T('lgSummary')).slice(-46));
    P('超卖后按清仓处理', has(T('lgSummary'), '已清仓'));
    P('不出现负数份数',
      T('lgSummary').indexOf('\u2212') < 0 && !/-\d/.test(T('lgSummary')));

    /* ── L6. 清仓后再买入 → 成本重新起算 ───────── */
    E('lgAdd').click();
    await wait(200);
    lgin(4, 'code', '510300'); lgin(4, 'price', '4.000'); lgin(4, 'qty', '1000');
    await wait(300);
    P('清仓后再买入，成本价重新起算 4.000（不带着旧成本）',
      has(T('lgSummary'), '4.000') && has(T('lgSummary'), '1,000份'), norm(T('lgSummary')).slice(0, 60));
    P('台账现在有 5 笔流水', LEDGER.length === 5, LEDGER.length + ' 笔');
    P('每笔流水都有稳定行号 rid（跨设备靠它对上同一笔）',
      LEDGER.every(r => !!r.rid) && new Set(LEDGER.map(r => r.rid)).size === LEDGER.length,
      LEDGER.map(r => String(r.rid).slice(0, 4)).join(','));
    P('rid 已落本地存储（刷新后不变，云端才对得上）',
      (function () {
        let a = [];
        try { a = JSON.parse(localStorage.getItem('mb_ledger_v1') || '[]'); } catch (e) {}
        return a.length === 5 && a.every(r => !!r.rid);
      })());

    /* ── L7. 现金计入总资产 ───────────────────── */
    const heroV = () => (Q('#sec-bk-pos .bk-hero .bh .v') || {}).textContent || '';
    const cashEl = E('bkCash');
    cashEl.value = '1000'; cashEl.dispatchEvent(new Event('input', {bubbles: true}));
    await wait(900);
    P('现金框改动写入本地存储', CASH === 1000, 'CASH=' + CASH);
    P('总资产口径 = 持仓市值 + 现金',
      Math.abs(bookTotals().total - bookTotals().mv - CASH) < 1e-6,
      bookTotals().mv + ' + ' + CASH + ' = ' + bookTotals().total);
    P('抬头「总资产」与口径一致（现金算进去、不计入持仓成本）',
      has(heroV(), yuan(bookTotals().total)), heroV() + ' / 期望 ' + yuan(bookTotals().total));
    P('持仓成本只算买标的的钱，不含现金',
      Math.abs(bookTotals().cost - 4000) < 1e-6, yuan(bookTotals().cost));

    /* ── L8. 快照：同一天只覆盖 ───────────────── */
    E('bkSnap').click();
    await wait(320);
    P('存快照后抬头显示条数', /快照1条/.test(norm(T('sec-bk-pos'))),
      norm(T('sec-bk-pos')).slice(0, 34));
    P('存快照后给出回执「已存 <今天>」', has(T('bkSnapState'), '已存'), T('bkSnapState'));
    P('快照落本地存储', Object.keys(SNAPS).length === 1, Object.keys(SNAPS).join(','));
    E('bkSnap').click();
    await wait(320);
    P('同一天重复存只覆盖，不新增一条', Object.keys(SNAPS).length === 1,
      Object.keys(SNAPS).length + ' 条 / ' + Object.keys(SNAPS).join(','));
    P('今天这一条是自动/手动存下来的（日期键 = 今天）', !!SNAPS[todayStr()],
      Object.keys(SNAPS)[0] + ' vs ' + todayStr());

    /* ── L9. 导入历史快照 → 净值曲线 ───────────── */
    const dOff = n => { const d = new Date(); d.setDate(d.getDate() + n);
      const p = x => (x < 10 ? '0' : '') + x;
      return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()); };
    const doImport = async obj => {
      /* ⚠️ 不能直接用真的 FileReader：Chrome 无头跑的是 --virtual-time-budget，
         虚拟时钟会把 setTimeout 一步跳完，而读文件是真实 I/O ⇒ onload 永远等不到。
         所以只替换「读文件」这一层，后面 change → JSON.parse → 落盘 → 重绘 全走真代码。 */
      const text = JSON.stringify(obj);
      const oFR = window.FileReader;
      window.FileReader = function () {
        const self = this;
        self.readAsText = function () {
          self.result = text;
          setTimeout(function () { if (self.onload) self.onload(); }, 0);
        };
      };
      try {
        const dt = new DataTransfer();
        dt.items.add(new File([text], '账本.json', {type: 'application/json'}));
        const fl = E('bkFile');
        Object.defineProperty(fl, 'files', {value: dt.files, configurable: true});
        fl.dispatchEvent(new Event('change', {bubbles: true}));
        await wait(400);
      } finally {
        window.FileReader = oFR;
      }
    };
    const T0 = todayStr(), D1 = dOff(-2), D2 = dOff(-1);
    const mk = (d, tot) => ({date: d, total: tot, cash: 0, est: false, rows: []});
    await doImport({v: 1, ledger: LEDGER.slice(),
      snaps: (function () { const o = {}; o[D1] = mk(D1, 9000); o[D2] = mk(D2, 9500);
                            o[T0] = mk(T0, 10000); return o; })(),
      journal: (function () { const o = {}; o[D1] = {dec: '减仓一半', why: '连续三天缩量', mood: '犹豫'}; return o; })(),
      cash: 1000});
    P('导入后快照 3 条（2 天历史 + 今天）', Object.keys(SNAPS).length === 3,
      Object.keys(SNAPS).sort().join(','));
    P('导入后台账没被冲掉（5 笔流水还在）', LEDGER.length === 5, LEDGER.length + ' 笔');
    P('净值曲线画出来了（3 个点）',
      !!Q('#sec-bk-pos svg.spark[data-cid]') &&
      pts(Q('#sec-bk-pos svg.spark polyline')) === 3,
      Q('#sec-bk-pos svg.spark polyline') ? pts(Q('#sec-bk-pos svg.spark polyline')) + ' 点' : '无曲线');
    P('曲线下方给出最早/最新/区间变动',
      has(T('sec-bk-pos'), '最早') && has(T('sec-bk-pos'), '最新') && has(T('sec-bk-pos'), '区间变动'));
    P('导入把日记一起带回来（「以前记的」有那条）',
      has(T('sec-bk-journal'), '以前记的') && has(T('sec-bk-journal'), '减仓一半') &&
      has(T('sec-bk-journal'), '犹豫'));
    P('导入的现金也生效', CASH === 1000, 'CASH=' + CASH);
    // 净值曲线悬停读数
    const svgBk = Q('#sec-bk-pos svg.spark[data-cid]');
    P('净值曲线挂了读数锚点（data-cid）', !!svgBk);
    hoverAt(svgBk, 0.5);
    await wait(140);
    P('净值曲线可悬停读数值',
      !!t1 && !t1.hidden && has(t1.textContent, '总资产'),
      t1 ? t1.textContent.replace(/\s+/g, '').slice(0, 50) : '');
    P('悬停读数带「较第一条」对比',
      !!t1 && has(t1.textContent, '较第一条'));
    document.body.dispatchEvent(new PointerEvent('pointerleave', {bubbles: true}));
    await wait(120);

    /* ── L10. 复盘日记：三句话 ─────────────────── */
    P('日记给三个填空（决定 / 为什么 / 心情）',
      !!E('jrnDec') && !!E('jrnWhy') && QA('#jrnMood .chip').length === 5,
      QA('#jrnMood .chip').map(b => b.textContent).join('/'));
    E('jrnDec').value = '把红线从 4.00 调到 3.95';
    E('jrnWhy').value = '连续缩量但没破止损位，先放宽一点';
    Q('#jrnMood .chip[data-mood="焦虑"]').click();
    await wait(150);
    P('心情标签可选中（再点一次取消）',
      Q('#jrnMood .chip[data-mood="焦虑"]').classList.contains('on'));
    E('jrnSave').click();
    await wait(300);
    P('存下后给出回执', has(T('jrnState'), '已存下'), T('jrnState'));
    let JV = null;
    try { JV = JSON.parse(localStorage.getItem('mb_journal_v1') || 'null'); } catch (e) {}
    P('日记已落本地存储（决定 + 理由 + 心情三项齐）',
      !!(JV && JV[T0] && JV[T0].dec === '把红线从 4.00 调到 3.95' &&
         JV[T0].why === '连续缩量但没破止损位，先放宽一点' && JV[T0].mood === '焦虑'),
      JV && JV[T0] ? ('mood=' + JV[T0].mood) : 'null');
    P('日记说明它不是数字、是「当时怎么想的」',
      has(T('sec-bk-journal'), '不产生任何数字') && has(T('sec-bk-journal'), '被行情推着动手'));

    /* ── L11. 导出 / 导入往返 ─────────────────── */
    let expBlob = null, expName = '';
    const oCOU = URL.createObjectURL, oClk = HTMLAnchorElement.prototype.click;
    URL.createObjectURL = function (b) { expBlob = b; return 'blob:wk-stub'; };
    HTMLAnchorElement.prototype.click = function () { expName = this.download || ''; };
    E('bkExport').click();
    URL.createObjectURL = oCOU; HTMLAnchorElement.prototype.click = oClk;
    P('导出生成一个下载文件，文件名带今天日期',
      !!expBlob && expName === '我的账本-' + T0 + '.json', expName || '（没有生成文件）');
    const expTxt = expBlob ? await expBlob.text() : '';
    let expObj = null;
    try { expObj = JSON.parse(expTxt); } catch (e) {}
    P('导出内容含台账 / 快照 / 日记 / 现金四块',
      !!(expObj && Array.isArray(expObj.ledger) && expObj.snaps && expObj.journal &&
         typeof expObj.cash === 'number'),
      expObj ? Object.keys(expObj).join(',') : '解析失败');
    P('导出含 5 笔流水 + 3 条快照 + 2 天日记',
      !!expObj && expObj.ledger.length === 5 && Object.keys(expObj.snaps).length === 3 &&
      Object.keys(expObj.journal).length === 2,
      expObj ? (expObj.ledger.length + ' 笔 / ' + Object.keys(expObj.snaps).length + ' 条 / ' +
                Object.keys(expObj.journal).length + ' 天') : '');
    await doImport(expObj || {});
    P('导出的文件能被自己导回来（往返不丢数据）',
      LEDGER.length === 5 && Object.keys(SNAPS).length === 3 && CASH === 1000,
      LEDGER.length + ' 笔 / ' + Object.keys(SNAPS).length + ' 条 / 现金 ' + CASH);

    /* ── L11b. 备份到网上：本机 → 网上的一次性搬家 ─────── */
    /* 中文必须先按 UTF-8 转字节，否则 btoa 直接抛 InvalidCharacterError */
    const _cn = '沪深300ETF · 测试';
    P('base64url 往返不丢中文（先转 UTF-8 字节再 btoa）',
      b64uDec(b64uEnc(_cn)) === _cn, b64uDec(b64uEnc(_cn)));
    const _pay = backupPayload();
    P('备份包与导出文件同构（台账 / 快照 / 日记 / 现金四块）',
      Array.isArray(_pay.ledger) && !!_pay.snaps && !!_pay.journal && typeof _pay.cash === 'number',
      Object.keys(_pay).join(','));
    P('备份包编一次再解一次，流水一条不少',
      JSON.parse(b64uDec(b64uEnc(JSON.stringify(_pay)))).ledger.length === LEDGER.length,
      LEDGER.length + ' 笔');
    P('备份说明写清带了多少东西',
      has(backupLine(backupCount(_pay)), LEDGER.length + ' 笔台账'), backupLine(backupCount(_pay)));
    P('网址长度上限有明确定义（超了就改走文件，绝不截断）',
      typeof BACKUP_MAX === 'number' && BACKUP_MAX > 8000, String(BACKUP_MAX));
    P('「备份到网上」按有没有云端配置决定显隐（正式站点自己就在云上）',
      !!Q('#bkCloud') === !CLOUD_CFG.endpoint, CLOUD_CFG.endpoint ? '正式站点：隐藏' : '本地版：显示');
    P('网址里带备份时会有一次确认再落地（不悄悄覆盖）',
      typeof tryRestoreFromHash === 'function' && has(String(tryRestoreFromHash), '导入会覆盖这个网页里现有的账本'));

    /* ── L12. 同步到「今天 · 我的持仓监控」 ────── */
    E('lgSync').click();
    await wait(900);
    P('同步后自动切回「今天」页', Q('#root').getAttribute('data-page') === 'today',
      Q('#root').getAttribute('data-page'));
    P('台账持仓写进「我的持仓监控」（1 只）',
      QA('#hRows tr[data-row]').length === 1, QA('#hRows tr[data-row]').length + ' 行');
    const syncCost = (Q('#hRows input[data-i="0"][data-k="cost"]') || {}).value || '';
    P('同步带上算出来的成本价 4（不用再手输一遍）',
      Math.abs(parseFloat(norm(syncCost)) - 4) < 1e-6, '成本价=' + syncCost);
    P('同步后回到今天页目录 ' + TOCS.today.length + ' 项', QA('#toc .toc-item').length === TOCS.today.length,
      QA('#toc .toc-item').length + ' 项');
    window.alert = OA; window.confirm = OC;

    /* ══ L. 显示内容分层（精简 / 完整 / 自定义）═════ */
    const layChip = k => Q('#layBar [data-lay="' + k + '"]');
    P('显示设置条在位（精简 / 完整 / 自定义 三档）',
      !!layChip('lite') && !!layChip('full') && !!layChip('custom'));
    P('默认「完整」档：目录 ' + TOCS.today.length + ' 段全显示',
      layChip('full').classList.contains('on') && QA('#toc .toc-item').length === TOCS.today.length,
      QA('#toc .toc-item').length + ' 项');
    const expLite = TOCS.today.filter(x => LITE_HIDE.indexOf(x.id) < 0).length;
    layChip('lite').click();
    await wait(150);
    P('切「精简」后目录只剩 ' + expLite + ' 项', QA('#toc .toc-item').length === expLite,
      QA('#toc .toc-item').map(e => e.textContent.trim()).join(' / '));
    /* 这条是拿旧文案喂出来的教训：删了一整段（快讯）却忘了改「只留 4 段」这句，
       页面上就多出一个根本不在的段。段数必须由实际段数算出。 */
    P('精简档的说明写的是实际段数（' + expLite + ' 段，不是写死的数字）',
      has(T('layBar'), '只留 ' + expLite + ' 段'), (T('layBar').match(/只留[^：]*：/) || [''])[0]);
    P('精简档下全球市场段被收起（lay-off 且高度 0）',
      Q('#sec-global').classList.contains('lay-off') && Q('#sec-global').offsetHeight === 0);
    P('精简档下必看三段仍在（持仓 / 情绪 / 复盘）',
      ['sec-watch', 'sec-senti', 'sec-market'].every(id => {
        const el = document.getElementById(id);
        return el && !el.classList.contains('lay-off') && el.offsetHeight > 0;
      }));
    const expLiteNo = Array.from({length: expLite}, (_, i) => i + 1).join('');
    P('精简档下目录编号重排为 1–' + expLite,
      QA('#toc .toc-item i').map(e => e.textContent).join('') === expLiteNo,
      QA('#toc .toc-item i').map(e => e.textContent).join(''));
    layChip('custom').click();
    await wait(150);
    const expOpt = TOCS.today.concat(TOCS.hist).filter(x => LOCKED.indexOf(x.id) < 0).length;
    P('「自定义」出现勾选面板，可选段 ' + expOpt + ' 个',
      !!Q('#layBar .lay-panel') && QA('#layBar .lay-item input').length === expOpt,
      QA('#layBar .lay-item input').length + ' 个');
    P('必看段不出现在勾选列表里（关不掉）', !Q('#layBar input[data-sec="sec-watch"]'));
    /* ⚠️ 自定义档的初始 hidden = LITE_HIDE 的副本，今天页里**可勾**的三段
       （全球市场 / 板块题材 / 资金流向）默认全是关着的，所以这里测「勾上」而不是「取消」：
       找一个默认关着的段勾上，段要显示、目录要多一项。 */
    const nBefore = QA('#toc .toc-item').length;
    const cbBoard = Q('#layBar input[data-sec="sec-board"]');
    cbBoard.checked = true; cbBoard.dispatchEvent(new Event('change', {bubbles: true}));
    await wait(150);
    P('勾选一段后该段显示、目录恰好多一项',
      !Q('#sec-board').classList.contains('lay-off')
        && QA('#toc .toc-item').length === nBefore + 1,
      QA('#toc .toc-item').length + ' 项');
    P('档位与勾选已落本地存储',
      (function(){
        try{
          const o = JSON.parse(localStorage.getItem('mb_layer_v1') || '{}');
          return o.level === 'custom' && o.hidden.indexOf('sec-board') < 0;
        }catch(e){ return false; }
      })());
    layChip('full').click();
    await wait(150);
    P('切回「完整」' + TOCS.today.length + ' 段全回来、无 lay-off 残留',
      QA('#toc .toc-item').length === TOCS.today.length
        && !Q('#sec-global').classList.contains('lay-off')
        && !Q('#sec-board').classList.contains('lay-off'));

    /* ══ G2. 徽标只回答「数据到几点」+ 自动重画不丢焦点 ═══ */
    const bt = T('dataBadge');
    P('徽标不列接口名（不出现「资金流向 / 板块题材」这类字段）',
      !/资金流向|板块题材|涨跌家数|分时走势/.test(bt), bt);
    P('徽标回答了「数据到几点」',
      /数据到 \d\d:\d\d/.test(bt) || /更新于 \d\d:\d\d:\d\d/.test(bt) || /离线快照/.test(bt), bt);
    P('徽标不出现「实时」两个字（会和同一屏的「已收盘」打架）', !/实时/.test(bt), bt);
    P('自动刷新间隔是 60 秒', AUTO_MS === 60000, String(AUTO_MS));

    /* ══ G3. 涨跌占比三段（上涨 / 平盘 / 下跌 各自算） ═══
       旧写法把「下跌」写成 100 − 上涨，等于把平盘算进下跌：
       2067/5293 = 39.1%，页面却写「下跌 60.9%」。 */
    const metaEl = Q('#sec-senti .chart-meta');
    const mtext = metaEl ? metaEl.textContent : '';
    const pcts = (mtext.match(/([\d.]+)%/g) || []).map(x => parseFloat(x));
    P('涨跌占比三段都在（上涨 / 平盘 / 下跌）', pcts.length >= 3, mtext.slice(0, 70));
    if(pcts.length >= 3){
      const sumP = pcts[0] + pcts[1] + pcts[2];
      P('三段占比加起来 = 100%（平盘没被算进下跌）', Math.abs(sumP - 100) <= 0.25, sumP.toFixed(1) + '%');
      const B0 = window.__MODEL.breadth;
      const expDown = (B0.down != null && B0.total) ? B0.down / B0.total * 100 : null;
      P('「下跌」取的是 下跌家数 ÷ 总家数（不是 100 − 上涨）',
        expDown != null && Math.abs(pcts[2] - expDown) <= 0.15,
        expDown == null ? '无家数数据' : pcts[2] + ' vs 期望 ' + expDown.toFixed(1));
      P('刻度条里三个色段都在（up / flat / down）',
        !!Q('#sec-senti .st-track i.up') && !!Q('#sec-senti .st-track i.flat')
        && !!Q('#sec-senti .st-track i.down'));
    }

    /* ══ G4. 当日新股不进榜 ═══
       2026-11-03 榜首是「N皇冠 +397.94%」、净流入榜第 2 名也是它。
       N = 上市首日、C = 上市后 2–5 日，这类涨幅是规则允许的，混进榜里整张表就失真。 */
    const NAMEP = /^[NC][\u4e00-\u9fa5]/;
    const srk = (window.__MODEL && window.__MODEL.stockRank) || {chg: [], amt: []};
    const rnk = (window.__MODEL && window.__MODEL.rank) || {stockIn: []};
    P('个股涨幅榜剔掉了当日新股',
      srk.chg.length > 0 && srk.chg.every(x => !NAMEP.test(x.name)),
      srk.chg.slice(0, 3).map(x => x.name).join(' / '));
    P('主力净流入榜剔掉了当日新股',
      rnk.stockIn.length > 0 && rnk.stockIn.every(x => !NAMEP.test(x.name)),
      rnk.stockIn.slice(0, 3).map(x => x.name).join(' / '));
    P('涨幅榜给满 8 条（多要几条才剔得起）', srk.chg.length === 8, String(srk.chg.length));
    if(LIVE_GAPS && LIVE_GAPS.fund){
      P('资金这次没取到时，在那一段自己说明（不是写进右上角徽标）',
        has(T('sec-fund'), '这次没取到'), norm(T('sec-fund')).slice(0, 46));
    } else {
      S('资金段的就地提示', '这次资金取到了，无需提示');
    }
    /* 重画保全：把焦点放进持仓输入框，模拟一次整页重画，看焦点和光标能不能回来 */
    let keepOk = false, focusBack = false, posBack = false;
    try{
      const box = Q('#hRows input[data-i="0"][data-k="cost"]');
      P('持仓输入框在位（下面测重画保全）', !!box);
      if(box){
        box.focus();
        box.setSelectionRange(1, 1);          // 值可能是「4」，光标位置不能取 2
        const k = uiKeep();
        keepOk = (k.i === '0' && k.f === 'cost' && k.pos === 1);
        P('重画前记住了焦点位置', keepOk,
          JSON.stringify({i:k.i, f:k.f, pos:k.pos, val:box.value, act:(document.activeElement||{}).id}));
        renderAll(window.__MODEL);
        uiBack(k);
        const again = Q('#hRows input[data-i="0"][data-k="cost"]');
        focusBack = document.activeElement === again;
        posBack = !!again && again.selectionStart === 1;
      }
    }catch(e){ P('重画保全流程不抛异常', false, e.message); }
    P('重画前记住了焦点在第几行第几列', keepOk);
    P('重画后焦点回到原输入框（不把光标弄丢）', focusBack);
    P('重画后光标位置也回来了', posBack);

    /* ══ H. 全页健康度 ═════════════════════════ */
    const txt = document.body.innerText;
    P('页面无 NaN', !txt.includes('NaN'));
    P('页面无 undefined', !txt.includes('undefined'));
    P('页面无 [object Object]', !txt.includes('[object Object]'));
    P('箭头符号存在（色盲兼容）', document.body.innerHTML.indexOf('▲') >= 0 && document.body.innerHTML.indexOf('▼') >= 0);
    trendCheck(null, '全页', true);
    trendCheck(null, '全页板块榜', false);
    P('免责声明在位', txt.includes('不提供任何买卖建议'));
    P('已标注不保证实时', txt.includes('不保证'));
  } catch (e) {
    out.push('FAIL | 脚本抛异常 | ' + e.message);
  }

  const pre = document.createElement('pre');
  pre.id = 'TESTOUT';
  pre.textContent = '[[TESTOUT]]\n' + out.join('\n') + '\n[[/TESTOUT]]';
  document.body.appendChild(pre);
})();
</script>
"""


def main():
    with open(SRC, "r", encoding="utf-8") as f:
        s = f.read()
    if "</body>" not in s:
        print("❌ 找不到 </body>")
        return 1
    s = s.replace("</body>", SNIPPET + "\n</body>")
    with open(DST, "w", encoding="utf-8") as f:
        f.write(s)
    print(f"[selftest] 已生成 {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
