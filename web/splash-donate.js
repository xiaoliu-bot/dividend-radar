/*!
 * splash-donate.js — 开屏赞赏 / 打赏海报 一键嵌入脚本
 * 用法：把本文件与 donate.html、assets/ 放在同一目录，
 *       在目标网站的 <body> 末尾加一句：
 *         <script src="splash-donate.js"></script>
 *       即可。其余遮罩、按钮、事件全部由本脚本注入，无需改动目标站其它代码。
 *
 * 可选配置（写在本 <script> 标签上）：
 *   data-page="donate.html"            赞赏页路径（默认与本脚本同目录的 donate.html）
 *   data-fab="♥ 赞赏"                  右下角常驻按钮文字
 *   data-enter="进入网站 →"            开屏“进入”按钮文字
 *   data-autoshow="true"               是否一打开就弹开屏（false=只显示右下角按钮）
 *   data-z="99999"                     遮罩层级（z-index）
 *
 * 注意：请用经典 <script>（不要用 type="module"），否则无法读取配置。
 */
(function () {
  'use strict';

  var me = document.currentScript;
  function attr(k, d) {
    return me && me.getAttribute(k) != null ? me.getAttribute(k) : d;
  }

  var cfg = {
    page:      attr('data-page', 'donate.html'),
    fabText:   attr('data-fab', '♥ 赞赏'),
    enterText: attr('data-enter', '进入网站 →'),
    autoShow:  attr('data-autoshow', 'true') !== 'false',
    z:         attr('data-z', '99999')
  };

  // 让 donate.html 与脚本同目录（脚本放哪，赞赏页就按相对路径找）
  var pageUrl = cfg.page;
  if (me && me.src) {
    try {
      var base = me.src.split('/').slice(0, -1).join('/');
      if (base) pageUrl = base + '/' + cfg.page;
    } catch (e) { /* 退回相对路径 */ }
  }

  var CSS =
    '#sd-splash{position:fixed;inset:0;z-index:' + cfg.z + ';background:#0d1117;}' +
    '#sd-splash.sd-hidden{display:none;}' +
    '#sd-splash iframe{width:100%;height:100%;border:0;display:block;background:transparent;}' +
    '#sd-enter{position:absolute;left:50%;bottom:30px;transform:translateX(-50%);z-index:2;' +
      'background:#1971c2;color:#fff;border:0;border-radius:999px;padding:12px 30px;' +
      'font-size:15px;font-weight:700;cursor:pointer;box-shadow:0 8px 24px rgba(25,113,194,.45);transition:background .15s;}' +
    '#sd-enter:hover{background:#2b8aef;}' +
    '#sd-fab{position:fixed;right:22px;bottom:22px;z-index:' + cfg.z + ';' +
      'display:inline-flex;align-items:center;gap:6px;background:linear-gradient(135deg,#1971c2,#4dabf7);' +
      'color:#fff;border:0;border-radius:999px;padding:11px 18px;font-size:14px;font-weight:700;' +
      'cursor:pointer;box-shadow:0 8px 22px rgba(25,113,194,.45);transition:transform .15s,box-shadow .15s;}' +
    '#sd-fab:hover{transform:translateY(-2px);box-shadow:0 12px 28px rgba(25,113,194,.55);}' +
    '#sd-fab:active{transform:translateY(0);}';

  function inject() {
    if (document.getElementById('sd-splash')) return; // 防重复注入

    var style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    // 开屏遮罩（iframe 载入 donate.html，样式天然隔离）
    var splash = document.createElement('div');
    splash.id = 'sd-splash';
    if (!cfg.autoShow) splash.classList.add('sd-hidden');
    var iframe = document.createElement('iframe');
    iframe.src = pageUrl;
    iframe.title = '赞赏海报';
    iframe.setAttribute('loading', 'lazy');
    splash.appendChild(iframe);
    var enter = document.createElement('button');
    enter.id = 'sd-enter';
    enter.textContent = cfg.enterText;
    splash.appendChild(enter);
    document.body.appendChild(splash);

    // 右下角常驻赞赏按钮（开屏关闭后也能随时唤起）
    var fab = document.createElement('button');
    fab.id = 'sd-fab';
    fab.textContent = cfg.fabText;
    fab.title = '赞赏支持';
    document.body.appendChild(fab);

    function open()  { splash.classList.remove('sd-hidden'); }
    function close() { splash.classList.add('sd-hidden'); }

    enter.addEventListener('click', close);
    fab.addEventListener('click', open);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') close();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }
})();
