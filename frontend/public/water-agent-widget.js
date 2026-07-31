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
    parentOrigin: '',
    instanceId: '',
    appId: '',
    apiBaseUrl: '',
    ready: false,
    loadTimer: null,
    requests: Object.create(null),
    appearance: {
      theme: '#1677ff',
      float_icon_url: '',
      float_icon_draggable: false,
      float_x_anchor: 'right',
      float_x_offset: 24,
      float_y_anchor: 'bottom',
      float_y_offset: 24,
    },
    dragPosition: null,
    pointer: null,
    suppressClick: false,
    triggerHandler: null,
    messageHandler: null,
    resizeHandler: null,
    pointerDownHandler: null,
    pointerMoveHandler: null,
    pointerUpHandler: null,
  };

  function createElement(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    return element;
  }

  function validAssetUrl(value) {
    if (value === '') return true;
    if (
      typeof value !== 'string'
      || value.length > 2048
      || value !== value.trim()
      || value.indexOf('<') !== -1
      || value.indexOf('>') !== -1
    ) return false;
    try {
      var url = new URL(value);
      return (
        (url.protocol === 'http:' || url.protocol === 'https:')
        && !url.username
        && !url.password
      );
    } catch {
      return false;
    }
  }

  function normalizeAppearance(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return null;
    }
    var expectedKeys = [
      'float_icon_draggable',
      'float_icon_url',
      'float_x_anchor',
      'float_x_offset',
      'float_y_anchor',
      'float_y_offset',
      'theme',
    ];
    var actualKeys = Object.keys(value).sort();
    if (
      actualKeys.length !== expectedKeys.length
      || actualKeys.some(function (key, index) {
        return key !== expectedKeys[index];
      })
      || typeof value.theme !== 'string'
      || !/^#[0-9a-fA-F]{6}$/.test(value.theme)
      || !validAssetUrl(value.float_icon_url)
      || typeof value.float_icon_draggable !== 'boolean'
      || (value.float_x_anchor !== 'left'
        && value.float_x_anchor !== 'right')
      || (value.float_y_anchor !== 'top'
        && value.float_y_anchor !== 'bottom')
      || !Number.isInteger(value.float_x_offset)
      || value.float_x_offset < 0
      || value.float_x_offset > 1000
      || !Number.isInteger(value.float_y_offset)
      || value.float_y_offset < 0
      || value.float_y_offset > 1000
    ) {
      return null;
    }
    return {
      theme: value.theme.toLowerCase(),
      float_icon_url: value.float_icon_url,
      float_icon_draggable: value.float_icon_draggable,
      float_x_anchor: value.float_x_anchor,
      float_x_offset: value.float_x_offset,
      float_y_anchor: value.float_y_anchor,
      float_y_offset: value.float_y_offset,
    };
  }

  function viewportWidth() {
    return Number(global.innerWidth) || 1024;
  }

  function viewportHeight() {
    return Number(global.innerHeight) || 768;
  }

  function isDesktop() {
    return viewportWidth() > 600;
  }

  function elementSize(element, axis, fallback) {
    var value = axis === 'width' ? element.offsetWidth : element.offsetHeight;
    return Number(value) || fallback;
  }

  function clamp(value, minimum, maximum) {
    return Math.min(Math.max(value, minimum), Math.max(minimum, maximum));
  }

  function triggerRect() {
    var width = elementSize(state.trigger, 'width', 58);
    var height = elementSize(state.trigger, 'height', 58);
    if (
      state.trigger
      && typeof state.trigger.getBoundingClientRect === 'function'
    ) {
      var rect = state.trigger.getBoundingClientRect();
      if (Number.isFinite(rect.left) && Number.isFinite(rect.top)) {
        return {
          left: rect.left,
          top: rect.top,
          width: Number(rect.width) || width,
          height: Number(rect.height) || height,
        };
      }
    }
    var appearance = state.appearance;
    var left = appearance.float_x_anchor === 'left'
      ? appearance.float_x_offset
      : viewportWidth() - appearance.float_x_offset - width;
    var top = appearance.float_y_anchor === 'top'
      ? appearance.float_y_offset
      : viewportHeight() - appearance.float_y_offset - height;
    return { left: left, top: top, width: width, height: height };
  }

  function applyPanelPosition() {
    if (
      !state.panel
      || !state.panel.style
      || !state.trigger
      || !state.trigger.style
    ) return;
    if (!isDesktop()) {
      state.panel.style.left = '';
      state.panel.style.right = '';
      state.panel.style.top = '';
      state.panel.style.bottom = '';
      return;
    }
    var rect = triggerRect();
    var panelWidth = elementSize(state.panel, 'width', 440);
    var panelHeight = elementSize(state.panel, 'height', 700);
    state.panel.style.left = '';
    state.panel.style.right = '';
    state.panel.style.top = '';
    state.panel.style.bottom = '';
    var horizontalAnchor = state.dragPosition
      ? state.dragPosition.float_x_anchor
      : state.appearance.float_x_anchor;
    var verticalAnchor = state.dragPosition
      ? state.dragPosition.float_y_anchor
      : state.appearance.float_y_anchor;
    if (horizontalAnchor === 'left') {
      state.panel.style.left = clamp(
        rect.left,
        8,
        viewportWidth() - panelWidth - 8,
      ) + 'px';
    } else {
      state.panel.style.right = clamp(
        viewportWidth() - rect.left - rect.width,
        8,
        viewportWidth() - panelWidth - 8,
      ) + 'px';
    }
    if (verticalAnchor === 'top') {
      state.panel.style.top = clamp(
        rect.top + rect.height + 12,
        8,
        viewportHeight() - panelHeight - 8,
      ) + 'px';
    } else {
      state.panel.style.bottom = clamp(
        viewportHeight() - rect.top + 12,
        8,
        viewportHeight() - panelHeight - 8,
      ) + 'px';
    }
  }

  function applyTriggerPosition() {
    if (!state.trigger || !state.trigger.style) return;
    var style = state.trigger.style;
    style.left = '';
    style.right = '';
    style.top = '';
    style.bottom = '';
    if (!isDesktop()) {
      state.dragPosition = null;
      applyPanelPosition();
      return;
    }
    var width = elementSize(state.trigger, 'width', 58);
    var height = elementSize(state.trigger, 'height', 58);
    if (state.dragPosition) {
      state.dragPosition.left = clamp(
        state.dragPosition.left,
        8,
        viewportWidth() - width - 8,
      );
      state.dragPosition.top = clamp(
        state.dragPosition.top,
        8,
        viewportHeight() - height - 8,
      );
      style.left = state.dragPosition.left + 'px';
      style.top = state.dragPosition.top + 'px';
    } else {
      var horizontal = clamp(
        state.appearance.float_x_offset,
        0,
        viewportWidth() - width,
      );
      var vertical = clamp(
        state.appearance.float_y_offset,
        0,
        viewportHeight() - height,
      );
      style[state.appearance.float_x_anchor] = horizontal + 'px';
      style[state.appearance.float_y_anchor] = vertical + 'px';
    }
    applyPanelPosition();
  }

  function applyTriggerIcon() {
    if (!state.trigger) return;
    var face = state.trigger.querySelector
      ? state.trigger.querySelector('.water-agent-face')
      : null;
    var image = state.trigger.querySelector
      ? state.trigger.querySelector('.water-agent-trigger-image')
      : null;
    if (!face || !image) return;
    image.style.display = 'none';
    face.style.display = '';
    image.onload = null;
    image.onerror = null;
    if (!state.appearance.float_icon_url) {
      image.removeAttribute('src');
      return;
    }
    image.onload = function () {
      image.style.display = 'block';
      face.style.display = 'none';
    };
    image.onerror = function () {
      image.style.display = 'none';
      face.style.display = '';
      image.removeAttribute('src');
    };
    image.src = state.appearance.float_icon_url;
  }

  function applyAppearance(appearance) {
    var previous = state.appearance;
    var persistentPositionChanged = (
      previous.float_x_anchor !== appearance.float_x_anchor
      || previous.float_x_offset !== appearance.float_x_offset
      || previous.float_y_anchor !== appearance.float_y_anchor
      || previous.float_y_offset !== appearance.float_y_offset
    );
    var draggingDisabled = (
      previous.float_icon_draggable
      && !appearance.float_icon_draggable
    );
    state.appearance = appearance;
    if (
      !isDesktop()
      || persistentPositionChanged
      || draggingDisabled
    ) {
      state.dragPosition = null;
    }
    if (state.trigger && state.trigger.style) {
      state.trigger.style.setProperty('--water-agent-theme', appearance.theme);
    }
    applyTriggerIcon();
    applyTriggerPosition();
  }

  function setOpen(open) {
    if (!state.panel || !state.trigger) return;
    state.panel.hidden = !open;
    state.trigger.setAttribute('aria-expanded', String(open));
    state.trigger.setAttribute(
      'aria-label',
      open ? '收起智能问数' : '打开智能问数',
    );
    if (open && state.iframe && state.iframe.contentWindow) {
      applyPanelPosition();
      state.iframe.contentWindow.postMessage(
        {
          type: 'water-agent-widget:opened',
          instanceId: state.instanceId,
        },
        state.widgetOrigin,
      );
    }
  }

  function clearLoadTimer() {
    if (state.loadTimer && typeof global.clearTimeout === 'function') {
      global.clearTimeout(state.loadTimer);
    }
    state.loadTimer = null;
  }

  function showLoadError(message) {
    clearLoadTimer();
    if (!state.loading) return;
    state.loading.hidden = false;
    state.loading.textContent = message;
    state.loading.setAttribute('role', 'alert');
  }

  function createInstanceId() {
    return [
      'water-agent',
      Date.now().toString(36),
      Math.random().toString(36).slice(2, 10),
    ].join('-');
  }

  function postToFrame(message) {
    if (!state.iframe || !state.iframe.contentWindow) return;
    state.iframe.contentWindow.postMessage(message, state.widgetOrigin);
  }

  function validRequestId(value) {
    return (
      typeof value === 'string'
      && /^[A-Za-z0-9_-]{1,128}$/.test(value)
    );
  }

  function validReportId(value) {
    return (
      typeof value === 'string'
      && /^wqr-[0-9a-f]{32}$/.test(value)
    );
  }

  function requestDefinition(operation, payload) {
    var base = (
      state.apiBaseUrl
      + '/api/embed/apps/'
      + encodeURIComponent(state.appId)
    );
    if (operation === 'application') {
      return { url: base + '/application', method: 'GET' };
    }
    if (operation === 'data-sources') {
      return { url: base + '/data-sources', method: 'GET' };
    }
    if (operation === 'chat') {
      return {
        url: base + '/chat_sse',
        method: 'POST',
        body: payload,
        stream: true,
      };
    }
    if (operation === 'report-options') {
      return { url: base + '/reports/options', method: 'GET' };
    }
    if (operation === 'report-generate') {
      return {
        url: base + '/reports/generate',
        method: 'POST',
        body: payload,
      };
    }
    if (
      (operation === 'report-preview' || operation === 'report-pdf')
      && payload
      && validReportId(payload.reportId)
    ) {
      return {
        url: (
          base
          + '/reports/artifacts/'
          + encodeURIComponent(payload.reportId)
          + (operation === 'report-preview' ? '/preview' : '/pdf')
        ),
        method: 'GET',
        preview: operation === 'report-preview',
        download: operation === 'report-pdf',
      };
    }
    return null;
  }

  function responseFilename(response) {
    var disposition = response.headers.get('Content-Disposition') || '';
    var match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (match) {
      try {
        return decodeURIComponent(match[1]);
      } catch {
        return 'water-quality-report.pdf';
      }
    }
    return 'water-quality-report.pdf';
  }

  async function handleRpcRequest(message) {
    var requestId = message.requestId;
    if (!validRequestId(requestId) || state.requests[requestId]) return;
    var definition = requestDefinition(message.operation, message.payload);
    if (!definition) {
      postToFrame({
        type: 'water-agent-widget:rpc-error',
        instanceId: state.instanceId,
        requestId: requestId,
        status: 400,
        message: '不支持的 Widget 请求',
      });
      return;
    }
    var controller = new AbortController();
    state.requests[requestId] = controller;
    try {
      var response = await fetch(definition.url, {
        method: definition.method,
        credentials: 'omit',
        headers: definition.body === undefined
          ? { Accept: definition.stream ? 'text/event-stream' : 'application/json' }
          : {
              Accept: definition.stream
                ? 'text/event-stream'
                : 'application/json',
              'Content-Type': 'application/json',
            },
        body: definition.body === undefined
          ? undefined
          : JSON.stringify(definition.body),
        signal: controller.signal,
      });
      var contentType = response.headers.get('Content-Type') || '';
      if (definition.stream && response.ok && response.body) {
        postToFrame({
          type: 'water-agent-widget:rpc-response',
          instanceId: state.instanceId,
          requestId: requestId,
          status: response.status,
          contentType: contentType,
        });
        var reader = response.body.getReader();
        var decoder = new TextDecoder();
        while (true) {
          var result = await reader.read();
          if (result.done) break;
          postToFrame({
            type: 'water-agent-widget:rpc-chunk',
            instanceId: state.instanceId,
            requestId: requestId,
            chunk: decoder.decode(result.value, { stream: true }),
          });
        }
        var tail = decoder.decode();
        if (tail) {
          postToFrame({
            type: 'water-agent-widget:rpc-chunk',
            instanceId: state.instanceId,
            requestId: requestId,
            chunk: tail,
          });
        }
        postToFrame({
          type: 'water-agent-widget:rpc-end',
          instanceId: state.instanceId,
          requestId: requestId,
        });
        return;
      }
      if (definition.download && response.ok) {
        var blob = await response.blob();
        var objectUrl = URL.createObjectURL(blob);
        var anchor = document.createElement('a');
        anchor.href = objectUrl;
        anchor.download = responseFilename(response);
        anchor.hidden = true;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(objectUrl);
        postToFrame({
          type: 'water-agent-widget:rpc-response',
          instanceId: state.instanceId,
          requestId: requestId,
          status: response.status,
          contentType: 'application/json',
          body: { downloaded: true },
        });
        return;
      }
      var text = await response.text();
      var body = text;
      if (contentType.indexOf('application/json') !== -1 && text) {
        try {
          body = JSON.parse(text);
        } catch {
          body = { detail: '服务响应格式无效' };
        }
      }
      postToFrame({
        type: 'water-agent-widget:rpc-response',
        instanceId: state.instanceId,
        requestId: requestId,
        status: response.status,
        contentType: definition.preview ? 'text/html' : contentType,
        body: body,
      });
    } catch (error) {
      if (error && error.name === 'AbortError') return;
      postToFrame({
        type: 'water-agent-widget:rpc-error',
        instanceId: state.instanceId,
        requestId: requestId,
        status: 502,
        message: '父页面请求后端失败',
      });
    } finally {
      delete state.requests[requestId];
    }
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
    var apiUrl = new URL(
      options.apiUrl || agentUrl.origin,
      global.location.href,
    );
    var appId = typeof options.appId === 'string'
      ? options.appId.trim()
      : '';
    if (
      (apiUrl.protocol !== 'http:' && apiUrl.protocol !== 'https:')
      || !/^[A-Za-z0-9_-]{3,64}$/.test(appId)
    ) {
      throw new Error('Widget 必须配置有效的 apiUrl 和公开 appId');
    }
    state.parentOrigin = global.location.origin;
    state.widgetOrigin = widgetUrl.origin;
    state.apiBaseUrl = apiUrl.origin;
    state.appId = appId;
    state.instanceId = createInstanceId();
    state.ready = false;
    state.requests = Object.create(null);
    state.dragPosition = null;
    state.pointer = null;
    state.suppressClick = false;
    widgetUrl.searchParams.set('parentOrigin', state.parentOrigin);
    widgetUrl.searchParams.set('instanceId', state.instanceId);
    widgetUrl.searchParams.set('appId', state.appId);

    var root = createElement('div');
    root.id = 'water-agent-widget-root';
    root.setAttribute('data-water-agent-widget', 'true');
    var shadow = root.attachShadow({ mode: 'open' });

    var style = createElement('style');
    style.textContent = [
      ':host{all:initial}',
      '.water-agent-layer{position:fixed;inset:0;z-index:2147483000;pointer-events:none;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}',
      '.water-agent-trigger{--water-agent-theme:#1677ff;pointer-events:auto;position:fixed;right:24px;bottom:24px;width:58px;height:58px;border:0;border-radius:50%;background:var(--water-agent-theme);box-shadow:0 10px 28px rgba(15,86,179,.3);cursor:pointer;display:grid;place-items:center;transition:transform .16s ease,box-shadow .16s ease;touch-action:none}',
      '.water-agent-trigger:hover{transform:translateY(-2px);box-shadow:0 14px 32px rgba(15,86,179,.36)}',
      '.water-agent-trigger:focus-visible{outline:3px solid var(--water-agent-theme);outline-offset:3px}',
      '.water-agent-trigger-image{display:none;width:100%;height:100%;border-radius:50%;object-fit:contain}',
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
    var triggerImage = createElement('img', 'water-agent-trigger-image');
    triggerImage.alt = '';
    trigger.appendChild(triggerImage);

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
    iframe.setAttribute('allow', 'clipboard-write');
    iframe.addEventListener('error', function () {
      showLoadError('智能助手加载失败，请确认 Agent 前端已启动。');
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
    if (typeof global.setTimeout === 'function') {
      state.loadTimer = global.setTimeout(function () {
        if (!state.ready) {
          showLoadError('智能助手加载超时，请确认 Agent 前端已启动。');
        }
      }, 10000);
    }
    state.triggerHandler = function () {
      if (state.suppressClick) {
        state.suppressClick = false;
        return;
      }
      setOpen(panel.hidden);
    };
    state.pointerDownHandler = function (event) {
      if (
        !state.appearance.float_icon_draggable
        || !isDesktop()
        || (event.button !== undefined && event.button !== 0)
      ) return;
      var rect = triggerRect();
      state.pointer = {
        id: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startLeft: rect.left,
        startTop: rect.top,
        moved: false,
      };
      if (trigger.setPointerCapture && event.pointerId !== undefined) {
        trigger.setPointerCapture(event.pointerId);
      }
    };
    state.pointerMoveHandler = function (event) {
      var pointer = state.pointer;
      if (!pointer || pointer.id !== event.pointerId) return;
      var deltaX = event.clientX - pointer.startX;
      var deltaY = event.clientY - pointer.startY;
      if (!pointer.moved && Math.hypot(deltaX, deltaY) <= 4) return;
      pointer.moved = true;
      var width = elementSize(trigger, 'width', 58);
      var height = elementSize(trigger, 'height', 58);
      state.dragPosition = {
        left: clamp(
          pointer.startLeft + deltaX,
          8,
          viewportWidth() - width - 8,
        ),
        top: clamp(
          pointer.startTop + deltaY,
          8,
          viewportHeight() - height - 8,
        ),
        float_x_anchor: (
          pointer.startLeft + deltaX + width / 2
          <= viewportWidth() / 2
            ? 'left'
            : 'right'
        ),
        float_y_anchor: (
          pointer.startTop + deltaY + height / 2
          <= viewportHeight() / 2
            ? 'top'
            : 'bottom'
        ),
      };
      applyTriggerPosition();
      if (event.preventDefault) event.preventDefault();
    };
    state.pointerUpHandler = function (event) {
      var pointer = state.pointer;
      if (!pointer || pointer.id !== event.pointerId) return;
      if (trigger.releasePointerCapture && event.pointerId !== undefined) {
        try {
          trigger.releasePointerCapture(event.pointerId);
        } catch {
          // 指针捕获可能已由浏览器释放。
        }
      }
      state.pointer = null;
      if (!pointer.moved || !state.dragPosition) return;
      var width = elementSize(trigger, 'width', 58);
      var height = elementSize(trigger, 'height', 58);
      var centerX = state.dragPosition.left + width / 2;
      var centerY = state.dragPosition.top + height / 2;
      var horizontalAnchor = (
        centerX <= viewportWidth() / 2 ? 'left' : 'right'
      );
      var verticalAnchor = (
        centerY <= viewportHeight() / 2 ? 'top' : 'bottom'
      );
      state.dragPosition = {
        left: horizontalAnchor === 'left'
          ? 8
          : viewportWidth() - width - 8,
        top: verticalAnchor === 'top'
          ? 8
          : viewportHeight() - height - 8,
        float_x_anchor: horizontalAnchor,
        float_y_anchor: verticalAnchor,
      };
      state.suppressClick = true;
      applyTriggerPosition();
    };
    state.resizeHandler = function () {
      if (!isDesktop()) {
        state.pointer = null;
        state.dragPosition = null;
      }
      applyTriggerPosition();
    };
    state.messageHandler = function (event) {
      if (
        event.origin !== state.widgetOrigin
        || !state.iframe
        || event.source !== state.iframe.contentWindow
        || !event.data
        || event.data.instanceId !== state.instanceId
      ) {
        return;
      }
      if (event.data.type === 'water-agent-widget:ready') {
        state.ready = true;
        clearLoadTimer();
        if (state.loading) state.loading.hidden = true;
        if (!panel.hidden) setOpen(true);
        return;
      }
      if (event.data.type === 'water-agent-widget:rpc-request') {
        void handleRpcRequest(event.data);
        return;
      }
      if (
        event.data.type === 'water-agent-widget:rpc-cancel'
        && validRequestId(event.data.requestId)
      ) {
        var requestController = state.requests[event.data.requestId];
        if (requestController) requestController.abort();
        return;
      }
      if (event.data.type === 'water-agent-widget:appearance') {
        var appearance = normalizeAppearance(event.data.appearance);
        if (appearance) applyAppearance(appearance);
        return;
      }
      if (
        event.data.type === 'water-agent-widget:close'
        || event.data.type === 'water-agent-widget:minimize'
      ) {
        setOpen(false);
        return;
      }
    };
    trigger.addEventListener('click', state.triggerHandler);
    trigger.addEventListener('pointerdown', state.pointerDownHandler);
    global.addEventListener('pointermove', state.pointerMoveHandler);
    global.addEventListener('pointerup', state.pointerUpHandler);
    global.addEventListener('pointercancel', state.pointerUpHandler);
    global.addEventListener('resize', state.resizeHandler);
    global.addEventListener('message', state.messageHandler);
    applyAppearance(state.appearance);
    iframe.src = widgetUrl.toString();
    return api;
  }

  function open() {
    setOpen(true);
  }

  function close() {
    setOpen(false);
  }

  function destroy() {
    clearLoadTimer();
    if (state.trigger && state.triggerHandler) {
      state.trigger.removeEventListener('click', state.triggerHandler);
    }
    if (state.trigger && state.pointerDownHandler) {
      state.trigger.removeEventListener(
        'pointerdown',
        state.pointerDownHandler,
      );
    }
    if (state.messageHandler) {
      global.removeEventListener('message', state.messageHandler);
    }
    if (state.pointerMoveHandler) {
      global.removeEventListener('pointermove', state.pointerMoveHandler);
    }
    if (state.pointerUpHandler) {
      global.removeEventListener('pointerup', state.pointerUpHandler);
      global.removeEventListener('pointercancel', state.pointerUpHandler);
    }
    if (state.resizeHandler) {
      global.removeEventListener('resize', state.resizeHandler);
    }
    if (state.root) state.root.remove();
    state.root = null;
    state.trigger = null;
    state.panel = null;
    state.iframe = null;
    state.loading = null;
    state.parentOrigin = '';
    state.instanceId = '';
    state.appId = '';
    state.apiBaseUrl = '';
    state.ready = false;
    Object.keys(state.requests).forEach(function (requestId) {
      state.requests[requestId].abort();
    });
    state.requests = Object.create(null);
    state.appearance = {
      theme: '#1677ff',
      float_icon_url: '',
      float_icon_draggable: false,
      float_x_anchor: 'right',
      float_x_offset: 24,
      float_y_anchor: 'bottom',
      float_y_offset: 24,
    };
    state.dragPosition = null;
    state.pointer = null;
    state.suppressClick = false;
    state.triggerHandler = null;
    state.messageHandler = null;
    state.resizeHandler = null;
    state.pointerDownHandler = null;
    state.pointerMoveHandler = null;
    state.pointerUpHandler = null;
    state.widgetOrigin = '';
  }

  var api = { init: init, open: open, close: close, destroy: destroy };
  global.WaterAgentWidget = api;

  var script = document.currentScript;
  if (!script || script.dataset.autoInit !== 'false') {
    var start = function () {
      init({
        agentUrl: script && script.dataset.agentUrl,
        apiUrl: script && script.dataset.apiUrl,
        appId: script && script.dataset.appId,
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
