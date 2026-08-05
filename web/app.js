/* 高股息雷达 · 前端渲染逻辑 */

const S = {
  data: null,
  view: [],
  chart: null,
};

const $ = (s) => document.querySelector(s);
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? '—' : Number(v).toFixed(d));
const dark = () => false;

const GRADE_CLASS = { 'A+': 'g-Aplus', 'A': 'g-A', 'B+': 'g-Bplus', 'B': 'g-B', 'C+': 'g-Cplus', 'C': 'g-C' };

/* ------------------------------------------------------------------ 启动 */

async function init() {
  try {
    const res = await fetch('data/stocks.json?t=' + Date.now());
    if (!res.ok) throw new Error('HTTP ' + res.status);
    S.data = await res.json();
  } catch (e) {
    $('#list').innerHTML = `<div class="empty">数据加载失败：${e.message}<br><br>请先在本地执行 <code>python scripts/build_data.py</code> 生成数据文件。</div>`;
    return;
  }
  renderStats();
  buildIndustryOptions();
  bindEvents();
  apply();
}

function renderStats() {
  const st = S.data.stats || {};
  $('#sOutput').textContent = st.output ?? '—';
  $('#sUniverse').textContent = `从 ${st.universe ?? '—'} 只全市场标的中筛出`;
  $('#sBuy').textContent = st.buy_count ?? '—';
  $('#sSell').textContent = st.sell_count ?? '—';
  $('#sYield').textContent = (st.avg_yield ?? '—') + '%';
  $('#tradeDate').textContent = S.data.trade_date || '—';
  $('#updatedAt').textContent = '更新于 ' + (S.data.updated_at || '—');
}

function buildIndustryOptions() {
  const sel = $('#fIndustry');
  (S.data.industries || []).forEach((n) => {
    const o = document.createElement('option');
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  });
}

function bindEvents() {
  ['#search', '#fSignal', '#fIndustry', '#fSort'].forEach((s) => {
    $(s).addEventListener('input', apply);
    $(s).addEventListener('change', apply);
  });
  document.querySelectorAll('[data-close]').forEach((el) => el.addEventListener('click', closeModal));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
  window.addEventListener('resize', () => S.chart && S.chart.resize());
}

/* ------------------------------------------------------------------ 筛选 */

function apply() {
  const q = $('#search').value.trim().toLowerCase();
  const sig = $('#fSignal').value;
  const ind = $('#fIndustry').value;
  const sort = $('#fSort').value;

  let list = (S.data.stocks || []).filter((s) => {
    if (q && !(s.code.includes(q) || s.name.toLowerCase().includes(q))) return false;
    if (sig !== 'all' && s.action.tone !== sig) return false;
    if (ind !== 'all' && s.industry !== ind) return false;
    return true;
  });

  const key = {
    score: (s) => s.score,
    dividend_yield: (s) => s.dividend_yield,
    tech: (s) => s.score_tech.total,
    fund: (s) => s.score_fund.total,
    roe: (s) => s.roe,
    dividend_years: (s) => s.dividend_years * 1000 + s.score,
  }[sort];
  list.sort((a, b) => key(b) - key(a));

  S.view = list;
  $('#resultCount').textContent = `共 ${list.length} 只标的`;
  renderList(list);
}

/* ------------------------------------------------------------------ 列表 */

function renderList(list) {
  const box = $('#list');
  if (!list.length) {
    box.innerHTML = '<div class="empty">没有符合条件的标的，试试放宽筛选</div>';
    return;
  }

  box.innerHTML = list.map((s, i) => {
    const chgCls = s.change_pct > 0 ? 'up' : s.change_pct < 0 ? 'down' : 'flat';
    const chgSign = s.change_pct > 0 ? '+' : '';
    const tone = s.action.tone;
    const nBuy = s.signals.buy.length, nSell = s.signals.sell.length;
    const metrics = `股息率 ${fmt(s.dividend_yield)}% · 连续分红 ${s.dividend_years} 年 · ROE ${fmt(s.roe)}% · 综合 ${s.score}`;

    return `
    <article class="row" data-code="${s.code}">
      <div class="row-main" data-metrics="${metrics}">
        <div class="cell-name">
          <span class="rank ${i < 3 ? 'top' : ''}">${i + 1}</span>
          <div class="name-block">
            <div class="stock-name">${s.name}</div>
            <div class="stock-sub">${s.code} · ${s.industry}</div>
          </div>
        </div>
        <div class="cell-price">
          <div class="price-main">${fmt(s.price)}</div>
          <div class="price-chg ${chgCls}">${chgSign}${fmt(s.change_pct)}%</div>
        </div>
        <div class="cell-yield"><span class="yield-val">${fmt(s.dividend_yield)}%</span></div>
        <div class="cell-years">${s.dividend_years} 年</div>
        <div class="cell-roe">${fmt(s.roe, 1)}%</div>
        <div class="cell-fund">${bar(s.score_fund.total)}</div>
        <div class="cell-tech">${bar(s.score_tech.total)}</div>
        <div class="cell-grade"><span class="grade ${GRADE_CLASS[s.grade] || 'g-C'}">${s.grade}</span></div>
        <div class="cell-action">
          <span class="tag tag-${tone}">${s.action.action}</span>
          <span class="sig-count">${nBuy ? '买' + nBuy : ''}${nBuy && nSell ? ' / ' : ''}${nSell ? '卖' + nSell : ''}</span>
        </div>
      </div>
    </article>`;
  }).join('');

  box.querySelectorAll('.row').forEach((el) => {
    el.addEventListener('click', () => openDetail(el.dataset.code));
  });
}

function bar(v) {
  return `<div class="bar-cell"><span class="bar-val">${fmt(v, 1)}</span><span class="bar"><i style="width:${Math.max(3, v)}%"></i></span></div>`;
}

/* ------------------------------------------------------------------ 详情 */

function openDetail(code) {
  const s = S.view.find((x) => x.code === code) || S.data.stocks.find((x) => x.code === code);
  if (!s) return;

  const chgCls = s.change_pct > 0 ? 'up' : s.change_pct < 0 ? 'down' : 'flat';
  const chgSign = s.change_pct > 0 ? '+' : '';
  const ind = s.indicators;

  const kv = (k, v, cls = '') => `<div class="kv"><div class="kv-k">${k}</div><div class="kv-v ${cls}">${v}</div></div>`;
  const stars = (n) => '★'.repeat(n) + '☆'.repeat(3 - n);

  const sigHtml = (arr, type) => arr.length
    ? arr.map((x) => `
      <div class="signal ${type}">
        <div class="signal-body">
          <div class="signal-name">${x.name}</div>
          <div class="signal-desc">${x.desc}</div>
        </div>
        <span class="stars">${stars(x.strength)}</span>
      </div>`).join('')
    : `<div class="signal"><div class="signal-body"><div class="signal-desc">当前无${type === 'buy' ? '买入' : '卖出'}信号触发</div></div></div>`;

  const scoreRow = (label, v) => `
    <div class="score-row">
      <span>${label}</span>
      <span class="track"><i style="width:${Math.max(2, v)}%"></i></span>
      <span class="num">${fmt(v, 1)}</span>
    </div>`;

  $('#modalBody').innerHTML = `
    <div class="detail-head">
      <div class="detail-title">
        <h2>${s.name}</h2>
        <span class="detail-code">${s.code}</span>
        <span class="detail-industry">${s.industry}</span>
        <span class="grade ${GRADE_CLASS[s.grade] || 'g-C'}">${s.grade}</span>
      </div>
      <div class="detail-price">
        <span class="p ${chgCls}">${fmt(s.price)}</span>
        <span class="${chgCls}">${chgSign}${fmt(s.change_pct)}%</span>
        <span class="tag tag-${s.action.tone}">${s.action.action}</span>
      </div>
    </div>

    <div class="detail-body">
      <div class="sec-title">操作参考价位</div>
      <div class="plan">
        <div class="plan-item">
          <div class="plan-k">买入参考区间</div>
          <div class="plan-v">${fmt(s.action.buy_zone[0])} – ${fmt(s.action.buy_zone[1])}</div>
          <div class="plan-note">MA20 与布林下轨构成的支撑带</div>
        </div>
        <div class="plan-item">
          <div class="plan-k">止损位</div>
          <div class="plan-v down">${fmt(s.action.stop_loss)}</div>
          <div class="plan-note">2 倍 ATR（${fmt(s.action.stop_loss_pct, 1)}%）</div>
        </div>
        <div class="plan-item">
          <div class="plan-k">目标位</div>
          <div class="plan-v up">${fmt(s.action.target)}</div>
          <div class="plan-note">上行空间 ${fmt(s.action.target_pct, 1)}%</div>
        </div>
        <div class="plan-item">
          <div class="plan-k">信号强度</div>
          <div class="plan-v">买 ${s.action.buy_weight} / 卖 ${s.action.sell_weight}</div>
          <div class="plan-note">按信号星级加权</div>
        </div>
      </div>

      <div class="sec-title">K 线与技术形态</div>
      <div id="chart" class="chart"></div>

      <div class="sec-title">买入信号</div>
      <div class="signal-list">${sigHtml(s.signals.buy, 'buy')}</div>

      <div class="sec-title">卖出信号</div>
      <div class="signal-list">${sigHtml(s.signals.sell, 'sell')}</div>

      <div class="sec-title">评分拆解${s.percentile !== undefined ? `<span class="sec-note">综合分超过候选池内 ${fmt(s.percentile, 0)}% 的标的</span>` : ''}</div>
      <div class="score-bars">
        ${scoreRow('基本面', s.score_fund.total)}
        ${scoreRow('· 股息回报', s.score_fund.detail.dividend)}
        ${scoreRow('· 分红持续', s.score_fund.detail.consistency)}
        ${scoreRow('· 盈利质量', s.score_fund.detail.profitability)}
        ${scoreRow('· 财务安全', s.score_fund.detail.safety)}
        ${scoreRow('技术面', s.score_tech.total)}
        ${scoreRow('· 趋势', s.score_tech.detail.trend)}
        ${scoreRow('· 动能', s.score_tech.detail.momentum)}
        ${scoreRow('· 量能', s.score_tech.detail.volume)}
      </div>

      <div class="sec-title">分红与财务</div>
      <div class="kv-grid">
        ${kv('股息率', fmt(s.dividend_yield) + '%')}
        ${kv('每股分红', fmt(s.dps, 3) + ' 元')}
        ${kv('连续分红', s.dividend_years + ' 年')}
        ${kv('分红率', s.payout_ratio ? fmt(s.payout_ratio, 1) + '%' : '—')}
        ${kv('ROE', fmt(s.roe) + '%')}
        ${kv('净利同比', fmt(s.profit_yoy, 1) + '%')}
        ${kv('市盈率', fmt(s.pe))}
        ${kv('市净率', fmt(s.pb))}
        ${kv('资产负债率', fmt(s.debt_ratio, 1) + '%')}
        ${kv('总市值', fmt(s.mktcap, 0) + ' 亿')}
      </div>

      <div class="sec-title">技术指标快照</div>
      <div class="kv-grid">
        ${kv('MA5', fmt(ind.ma5))}
        ${kv('MA20', fmt(ind.ma20))}
        ${kv('MA60', fmt(ind.ma60))}
        ${kv('MA120', fmt(ind.ma120))}
        ${kv('MACD DIF', fmt(ind.macd_dif, 3))}
        ${kv('MACD DEA', fmt(ind.macd_dea, 3))}
        ${kv('RSI(14)', fmt(ind.rsi, 1))}
        ${kv('KDJ-K', fmt(ind.kdj_k, 1))}
        ${kv('KDJ-D', fmt(ind.kdj_d, 1))}
        ${kv('布林上轨', fmt(ind.boll_up))}
        ${kv('布林下轨', fmt(ind.boll_low))}
        ${kv('ATR 波幅', fmt(ind.atr_pct) + '%')}
        ${kv('量比', fmt(ind.vol_ratio))}
        ${kv('20 日涨幅', fmt(ind.ret20) + '%', ind.ret20 >= 0 ? 'up' : 'down')}
        ${kv('60 日涨幅', fmt(ind.ret60) + '%', ind.ret60 >= 0 ? 'up' : 'down')}
        ${kv('年内回撤', fmt(ind.drawdown) + '%', 'down')}
      </div>

      <div class="note">
        评分与信号均由程序按固定规则计算，不含任何人工判断。技术指标本质上是对历史价格的统计描述，
        对未来走势没有必然预测力。请把这里的结论当作研究起点，而不是操作指令。
      </div>
    </div>`;

  $('#modal').hidden = false;
  document.body.style.overflow = 'hidden';
  requestAnimationFrame(() => drawChart(s));
}

function closeModal() {
  $('#modal').hidden = true;
  document.body.style.overflow = '';
  if (S.chart) { S.chart.dispose(); S.chart = null; }
}

/* ------------------------------------------------------------------ K线图 */

function drawChart(s) {
  const el = document.getElementById('chart');
  if (!el || !window.echarts) return;

  const dates = s.kline.map((r) => r[0]);
  const ohlc = s.kline.map((r) => [r[1], r[2], r[3], r[4]]);  // open, close, low, high
  const vols = s.kline.map((r, i) => ({
    value: r[5],
    itemStyle: { color: r[2] >= r[1] ? 'rgba(224,49,49,.55)' : 'rgba(47,158,68,.55)' },
  }));

  const isDark = dark();
  const axisColor = isDark ? '#3a424d' : '#e5e7eb';
  const textColor = isDark ? '#98a1ad' : '#6b7280';

  S.chart = echarts.init(el, null, { renderer: 'canvas' });

  const maSeries = Object.entries(s.ma_series || {}).map(([k, v], i) => ({
    name: k.toUpperCase(),
    type: 'line',
    data: v,
    smooth: true,
    symbol: 'none',
    lineWidth: 1,
    lineStyle: { width: 1.2, color: ['#e8a33d', '#1971c2', '#8b5cf6'][i] },
  }));

  S.chart.setOption({
    animation: false,
    legend: {
      data: ['K线', ...maSeries.map((m) => m.name)],
      top: 0, textStyle: { color: textColor, fontSize: 11 }, itemGap: 14,
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: isDark ? '#1c2128' : '#fff',
      borderColor: axisColor,
      textStyle: { color: isDark ? '#e8eaed' : '#1f2937', fontSize: 12 },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      { left: 52, right: 16, top: 28, height: 190 },
      { left: 52, right: 16, top: 236, height: 52 },
      { left: 52, right: 16, top: 304, height: 52 },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, boundaryGap: false, axisLine: { lineStyle: { color: axisColor } }, axisLabel: { show: false }, axisTick: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, boundaryGap: false, axisLine: { lineStyle: { color: axisColor } }, axisLabel: { show: false }, axisTick: { show: false } },
      { type: 'category', data: dates, gridIndex: 2, boundaryGap: false, axisLine: { lineStyle: { color: axisColor } }, axisLabel: { color: textColor, fontSize: 10, interval: Math.floor(dates.length / 6) }, axisTick: { show: false } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: axisColor, type: 'dashed' } }, axisLabel: { color: textColor, fontSize: 10 }, axisLine: { show: false } },
      { scale: true, gridIndex: 1, splitNumber: 2, splitLine: { show: false }, axisLabel: { color: textColor, fontSize: 10, formatter: (v) => (v >= 1e8 ? (v / 1e8).toFixed(1) + '亿' : (v / 1e4).toFixed(0) + '万') }, axisLine: { show: false } },
      { scale: true, gridIndex: 2, splitNumber: 2, splitLine: { show: false }, axisLabel: { color: textColor, fontSize: 10 }, axisLine: { show: false } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2], start: 45, end: 100 },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { color: '#e03131', color0: '#2f9e44', borderColor: '#e03131', borderColor0: '#2f9e44' },
      },
      ...maSeries.map((m) => ({ ...m, xAxisIndex: 0, yAxisIndex: 0 })),
      { name: '成交量', type: 'bar', data: vols, xAxisIndex: 1, yAxisIndex: 1 },
      {
        name: 'MACD', type: 'bar', xAxisIndex: 2, yAxisIndex: 2,
        data: buildMacd(s).hist.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? '#e03131' : '#2f9e44' } })),
      },
      { name: 'DIF', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: buildMacd(s).dif, symbol: 'none', lineStyle: { width: 1, color: '#e8a33d' } },
      { name: 'DEA', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: buildMacd(s).dea, symbol: 'none', lineStyle: { width: 1, color: '#1971c2' } },
    ],
  });
}

/* 前端按同样的公式重算 MACD 序列，省掉后端导出整条曲线的体积 */
let macdCache = new WeakMap();
function buildMacd(s) {
  if (macdCache.has(s)) return macdCache.get(s);
  const close = s.kline.map((r) => r[2]);
  const emaArr = (arr, span) => {
    const k = 2 / (span + 1);
    let prev = arr[0];
    return arr.map((v, i) => (i === 0 ? (prev = v) : (prev = v * k + prev * (1 - k))));
  };
  const e12 = emaArr(close, 12), e26 = emaArr(close, 26);
  const dif = close.map((_, i) => +(e12[i] - e26[i]).toFixed(4));
  const dea = emaArr(dif, 9).map((v) => +v.toFixed(4));
  const hist = dif.map((v, i) => +((v - dea[i]) * 2).toFixed(4));
  const out = { dif, dea, hist };
  macdCache.set(s, out);
  return out;
}

init();
