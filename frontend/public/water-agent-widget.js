(function (global) {
  'use strict';

  if (global.WaterAgentWidget) return;

  var state = {
    root: null,
    trigger: null,
    panel: null,
    iframe: null,
    loading: null,
    widgetOrigin: '',
    triggerHandler: null,
    messageHandler: null,
  };

  function createElement(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    return element;
  }

  function setOpen(open) {
    if (!state.panel || !state.trigger) return;
    state.panel.hidden = !open;
    state.trigger.setAttribute('aria-expanded', String(open));
    state.trigger.setAttribute(
      'aria-label',
      open ? '收起智能问数' : '打开智能问数',
    );
  }

  function init(options) {
    if (state.root && state.root.isConnected) return api;

    options = options || {};
    var agentUrl = new URL(
      options.agentUrl || global.location.origin,
      global.location.href,
    );
    var widgetUrl = new URL(
      options.widgetPath || '/?mode=widget',
      agentUrl,
    );
    state.widgetOrigin = widgetUrl.origin;

    var root = createElement('div');
    root.id = 'water-agent-widget-root';
    root.setAttribute('data-water-agent-widget', 'true');
    var shadow = root.attachShadow({ mode: 'open' });

    var style = createElement('style');
    style.textContent = [
      ':host{all:initial}',
      '.water-agent-layer{position:fixed;inset:0;z-index:2147483000;pointer-events:none;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}',
      '.water-agent-trigger{pointer-events:auto;position:fixed;right:24px;bottom:24px;width:58px;height:58px;border:0;border-radius:50%;background:linear-gradient(145deg,#1677ff,#0759cf);box-shadow:0 10px 28px rgba(15,86,179,.3);cursor:pointer;display:grid;place-items:center;transition:transform .16s ease,box-shadow .16s ease}',
      '.water-agent-trigger:hover{transform:translateY(-2px);box-shadow:0 14px 32px rgba(15,86,179,.36)}',
      '.water-agent-trigger:focus-visible{outline:3px solid rgba(22,119,255,.35);outline-offset:3px}',
      '.water-agent-face{position:relative;width:30px;height:25px;border:2px solid #fff;border-radius:9px;background:rgba(255,255,255,.12)}',
      '.water-agent-face:before{content:"";position:absolute;left:6px;top:8px;width:4px;height:4px;border-radius:50%;background:#fff;box-shadow:10px 0 0 #fff}',
      '.water-agent-face:after{content:"";position:absolute;left:9px;top:-8px;width:8px;height:6px;border-left:2px solid #fff;border-top:2px solid #fff;border-radius:5px 0 0 0}',
      '.water-agent-panel{pointer-events:auto;position:fixed;right:24px;bottom:94px;width:min(440px,calc(100vw - 32px));height:min(700px,calc(100vh - 118px));border:1px solid rgba(15,23,42,.12);border-radius:18px;overflow:hidden;background:#fff;box-shadow:0 22px 65px rgba(15,23,42,.24);transform-origin:bottom right}',
      '.water-agent-panel[hidden]{display:none}',
      '.water-agent-frame{display:block;width:100%;height:100%;border:0;background:#f5f7fa}',
      '.water-agent-loading{position:absolute;inset:0;z-index:1;display:grid;place-items:center;background:#f7f9fc;color:#64748b;font-size:13px;letter-spacing:.02em}',
      '.water-agent-loading[hidden]{display:none}',
      '@media(max-width:600px){.water-agent-trigger{right:16px;bottom:16px;width:54px;height:54px}.water-agent-panel{inset:8px 8px 78px;width:auto;height:auto;border-radius:14px}}',
    ].join('');

    var layer = createElement('div', 'water-agent-layer');
    var trigger = createElement('button', 'water-agent-trigger');
    trigger.type = 'button';
    trigger.setAttribute('aria-label', '打开智能问数');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.appendChild(createElement('span', 'water-agent-face'));

    var panel = createElement('section', 'water-agent-panel');
    panel.hidden = true;
    panel.setAttribute('aria-label', '智能问数浮窗');
    var loading = createElement(
      'div',
      'water-agent-loading',
      '智能助手加载中…',
    );
    var iframe = createElement('iframe', 'water-agent-frame');
    iframe.title = '智能问数';
    iframe.src = widgetUrl.toString();
    iframe.setAttribute('allow', 'clipboard-write');
    iframe.addEventListener('load', function () {
      loading.hidden = true;
    });

    panel.appendChild(loading);
    panel.appendChild(iframe);
    layer.appendChild(panel);
    layer.appendChild(trigger);
    shadow.appendChild(style);
    shadow.appendChild(layer);
    document.body.appendChild(root);

    state.root = root;
    state.trigger = trigger;
    state.panel = panel;
    state.iframe = iframe;
    state.loading = loading;
    state.triggerHandler = function () {
      setOpen(panel.hidden);
    };
    state.messageHandler = function (event) {
      if (
        event.origin === state.widgetOrigin
        && state.iframe
        && event.source === state.iframe.contentWindow
        && event.data
        && event.data.type === 'water-agent-widget:close'
      ) {
        setOpen(false);
      }
    };
    trigger.addEventListener('click', state.triggerHandler);
    global.addEventListener('message', state.messageHandler);
    return api;
  }

  function open() {
    setOpen(true);
  }

  function close() {
    setOpen(false);
  }

  function destroy() {
    if (state.trigger && state.triggerHandler) {
      state.trigger.removeEventListener('click', state.triggerHandler);
    }
    if (state.messageHandler) {
      global.removeEventListener('message', state.messageHandler);
    }
    if (state.root) state.root.remove();
    state.root = null;
    state.trigger = null;
    state.panel = null;
    state.iframe = null;
    state.loading = null;
    state.triggerHandler = null;
    state.messageHandler = null;
    state.widgetOrigin = '';
  }

  var api = { init: init, open: open, close: close, destroy: destroy };
  global.WaterAgentWidget = api;

  var script = document.currentScript;
  if (!script || script.dataset.autoInit !== 'false') {
    var start = function () {
      init({
        agentUrl: script && script.dataset.agentUrl,
        widgetPath: script && script.dataset.widgetPath,
      });
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
      start();
    }
  }
})(window);
