import { useState, useRef, useCallback, useEffect } from 'react';
import type { SSEEvent, ChatMessage, SessionMeta, DataFrameData, ChartData, ChartSpec, ChartType, RenderableChartType } from '../types';
import { fallbackSpecFromColumns, isRenderableChartType, getChartTypeAvailability, buildChartOption, normalizeChartSpec, CHART_TYPE_LABELS } from '../chartRegistry';
import { prepareChartV2All } from '../chartPipelineV2';
import type { Row } from '../datasetProfilerV2';

/* ======== 本地会话持久化（localStorage） ======== */

const SESSIONS_KEY = 'water_qa_sessions';
const META_KEY = 'water_qa_session_meta';
const CURRENT_ID_KEY = 'water_qa_current_id';

function loadAllSessions(): Record<string, ChatMessage[]> {
  try { const raw = localStorage.getItem(SESSIONS_KEY); return raw ? JSON.parse(raw) : {}; } catch { return {}; }
}

/** 只读获取指定会话的消息列表（不切换当前会话，不触发状态变更） */
export function getSessionMessages(id: string): ChatMessage[] {
  return loadAllSessions()[id] || [];
}
function saveAllSessions(data: Record<string, ChatMessage[]>) {
  try { localStorage.setItem(SESSIONS_KEY, JSON.stringify(data)); } catch { /* quota exceeded */ }
}
function loadAllMeta(): Record<string, SessionMeta> {
  try { const raw = localStorage.getItem(META_KEY); return raw ? JSON.parse(raw) : {}; } catch { return {}; }
}
function saveAllMeta(data: Record<string, SessionMeta>) {
  try { localStorage.setItem(META_KEY, JSON.stringify(data)); } catch { /* quota exceeded */ }
}
function loadCurrentId(): string {
  try { return localStorage.getItem(CURRENT_ID_KEY) || ''; } catch { return ''; }
}
function saveCurrentId(id: string) {
  try { localStorage.setItem(CURRENT_ID_KEY, id); } catch { /* quota exceeded */ }
}

/** 从消息列表提取会话标题：第一条用户消息，截断 24 字 */
function getSessionTitle(msgs: ChatMessage[]): string {
  const firstUser = msgs.find(m => m.role === 'user');
  if (!firstUser || !firstUser.text.trim()) return '新对话';
  const text = firstUser.text.trim();
  return text.length > 24 ? text.slice(0, 24) + '...' : text;
}

/** 初始 sessionId：优先读 localStorage，否则生成新 ID */
function initSessionId(): string {
  const id = loadCurrentId();
  return id || `s_${Date.now()}`;
}

/** 全局正则：提取所有完整的 <!-- chart_spec: {...} --> 标记 */
const CHART_SPEC_GLOBAL_RE = /<!--\s*chart_spec:\s*([\s\S]*?)\s*-->/gi;

/** 旧 chart_type 兼容正则 */
const CHART_TYPE_RE = /<!--\s*chart_type:\s*(bar|line|pie|none)\s*-->/i;

/** 最多保留的图表数 */
const MAX_CHARTS = 4;

/** 剥离图表注释：先匹配完整标记，再清除流式未闭合残片，最后去尾部空白 */
function stripChartAnnotations(text: string): string {
  return text
    .replace(CHART_SPEC_GLOBAL_RE, '')
    .replace(CHART_TYPE_RE, '')
    .replace(/<!--[\s\S]*$/, '')
    .trimEnd();
}

/** 校验 chart_spec 字段是否引用了不存在的列 */
function validateChartSpec(
  spec: ChartSpec,
  columns: string[],
): string | null {
  const errors: string[] = [];

  if (!spec.type || spec.type === 'none') return null;

  if (!isRenderableChartType(spec.type)) {
    return `不支持的图表类型: ${spec.type}`;
  }

  if (spec.xField && !columns.includes(spec.xField)) {
    errors.push(`xField "${spec.xField}" 不在查询结果列中`);
  }

  for (const yf of spec.yFields ?? []) {
    if (!columns.includes(yf)) {
      errors.push(`yField "${yf}" 不在查询结果列中`);
    }
  }

  if (spec.seriesField && !columns.includes(spec.seriesField)) {
    errors.push(`seriesField "${spec.seriesField}" 不在查询结果列中`);
  }

  if (spec.sizeField && !columns.includes(spec.sizeField)) {
    errors.push(`sizeField "${spec.sizeField}" 不在查询结果列中`);
  }

  if (spec.valueField && !columns.includes(spec.valueField)) {
    errors.push(`valueField "${spec.valueField}" 不在查询结果列中`);
  }

  return errors.length > 0 ? errors.join('; ') : null;
}

/** 解析单个 chart_spec JSON */
function parseChartSpec(raw: string): ChartSpec | null {
  try {
    const parsed = JSON.parse(raw) as Partial<ChartSpec>;
    const type = typeof parsed.type === 'string' ? parsed.type.toLowerCase() as ChartType : 'none';
    return {
      type,
      title: typeof parsed.title === 'string' ? parsed.title : undefined,
      xField: typeof parsed.xField === 'string' ? parsed.xField : null,
      yFields: Array.isArray(parsed.yFields) ? parsed.yFields.filter((v): v is string => typeof v === 'string') : [],
      seriesField: typeof parsed.seriesField === 'string' ? parsed.seriesField : null,
      sizeField: typeof parsed.sizeField === 'string' ? parsed.sizeField : null,
      valueField: typeof parsed.valueField === 'string' ? parsed.valueField : null,
      min: typeof parsed.min === 'number' ? parsed.min : null,
      max: typeof parsed.max === 'number' ? parsed.max : null,
      unit: typeof parsed.unit === 'string' ? parsed.unit : null,
    };
  } catch {
    return null;
  }
}

/** 从累积文本中提取所有 chart_spec，返回 ChartData 数组（不含 rows） */
function extractAllChartSpecs(
  text: string,
  messageId: string,
  columns: string[],
  validator: (spec: ChartSpec, cols: string[]) => string | null,
  dataVersion: number,
): ChartData[] {
  const charts: ChartData[] = [];

  for (const m of text.matchAll(CHART_SPEC_GLOBAL_RE)) {
    if (charts.length >= MAX_CHARTS) {
      console.warn(`[useSSE] 超过最大图表数 ${MAX_CHARTS}，忽略后续 chart_spec`);
      break;
    }

    const raw = m[1].trim();
    const spec = parseChartSpec(raw);

    if (!spec) {
      charts.push({
        id: `${messageId}-chart-${charts.length}`,
        columns: [],
        rows: [],
        spec: { type: 'none' },
        title: '图表解析失败',
        error: `chart_spec JSON 解析失败: ${raw.slice(0, 100)}`,
        dataVersion,
      });
      continue;
    }

    if (spec.type === 'none') continue;

    // 检测重复配置（只 warn，不删除）
    const sig = JSON.stringify(spec);
    for (let i = 0; i < charts.length; i++) {
      if (!charts[i].error && JSON.stringify(charts[i].spec) === sig) {
        console.warn(
          `[useSSE] 图表 ${charts.length} 与图表 ${i} 的 chart_spec 完全相同，可能存在重复配置`,
        );
        break;
      }
    }

    const validationError = validator(spec, columns);
    if (validationError) {
      charts.push({
        id: `${messageId}-chart-${charts.length}`,
        columns,
        rows: [],
        spec,
        title: spec.title || `图表 ${charts.length + 1}`,
        error: validationError,
        dataVersion,
      });
      continue;
    }

    charts.push({
      id: `${messageId}-chart-${charts.length}`,
      columns,
      rows: [],
      spec,
      title: spec.title || `图表 ${charts.length + 1}`,
      dataVersion,
    });
  }

  return charts;
}

/** 从旧 AI 回复文本中提取 chart_type 标记（兼容回退） */
function extractChartType(text: string): 'bar' | 'line' | 'pie' | 'none' | null {
  const m = text.match(CHART_TYPE_RE);
  return m ? (m[1].toLowerCase() as 'bar' | 'line' | 'pie' | 'none') : null;
}

/** 识别消息中的图表类型名 */
function detectChartTypeName(text: string): RenderableChartType | null {
  // combo 必须在 bar/line 之前检测，避免"柱状折线图"被"柱状图"或"折线图"部分匹配
  if (/组合图|柱线图|柱状折线图|双轴图|柱状图和折线图/.test(text)) return 'combo';
  if (/雷达图/.test(text)) return 'radar';
  if (/热力图/.test(text)) return 'heatmap';
  if (/箱线图|箱形图|盒须图/.test(text)) return 'boxplot';
  if (/仪表盘|仪表图/.test(text)) return 'gauge';
  if (/横向柱状图|横向条形图/.test(text)) return 'horizontal_bar';
  if (/柱状图|柱形图|直方图/.test(text)) return 'bar';
  if (/面积图/.test(text)) return 'area';
  if (/折线图|线形图|曲线图/.test(text)) return 'line';
  if (/环形图|甜甜圈图/.test(text)) return 'donut';
  if (/饼图|扇形图|圆饼/.test(text)) return 'pie';
  if (/气泡图/.test(text)) return 'bubble';
  if (/散点图/.test(text)) return 'scatter';
  return null;
}

/** 切换动词：前缀白名单（仅这些开头才算纯切换） */
const SWITCH_PREFIXES = [
  '改成', '切换为', '切换成', '换成', '变成', '变为', '转为', '转成', '展示为', '显示为', '改为',
];

/** 切换句式：用...显示 / 用...展示 / 用...呈现 / 用...画 / 用...出图 */
const SWITCH_WRAPPER_BEFORE = '用';
const SWITCH_WRAPPER_AFTERS = ['显示', '展示', '呈现', '画', '出图'];

/**
 * 判断用户输入是否为纯图表切换指令（白名单句式匹配）。
 * 只有整句话符合简短切换句式时才本地处理；其余一律发送后端。
 */
function isPureChartSwitch(msg: string): RenderableChartType | null {
  const trimmed = msg.trim();

  // 句式1：前缀 + 图表类型名  如"改成饼图""切换为柱状图""改为雷达图"
  for (const prefix of SWITCH_PREFIXES) {
    if (trimmed.startsWith(prefix)) {
      const rest = trimmed.slice(prefix.length);
      const t = detectChartTypeName(rest);
      if (t) return t;
    }
  }

  // 句式2：用 + 图表类型名 + 显示/展示/呈现/画/出图  如"用横向柱状图显示"
  if (trimmed.startsWith(SWITCH_WRAPPER_BEFORE)) {
    for (const after of SWITCH_WRAPPER_AFTERS) {
      if (trimmed.endsWith(after)) {
        const middle = trimmed.slice(SWITCH_WRAPPER_BEFORE.length, trimmed.length - after.length);
        const t = detectChartTypeName(middle);
        if (t) return t;
      }
    }
  }

  // 句式3：极短消息，只有图表类型名本身  如"饼图""柱状图"
  // 排除追加类消息（由 isPureChartAppend 单独处理）
  if (trimmed.length <= 6 && !/^(加|再|补充|新增|追加)/.test(trimmed)) {
    return detectChartTypeName(trimmed);
  }

  return null;
}

/** 追加图表前缀：白名单（仅这些开头才算追加图表指令） */
const APPEND_PREFIXES = [
  '加个', '加一个', '加一张',
  '再加个', '再加一个', '再加一张',
  '补充个', '补充一个', '补充一张',
  '再生成个', '再生成一个', '再生成一张',
  '新增个', '新增一个', '新增一张',
];

/**
 * 判断用户输入是否为纯追加图表指令（基于上一条数据新增图表类型）。
 * 白名单前缀 + 图表类型名 + 可选后缀。
 */
function isPureChartAppend(msg: string): RenderableChartType | null {
  const trimmed = msg.trim();

  // 问句不视为追加指令
  if (/[吗?？]/.test(trimmed)) return null;

  for (const prefix of APPEND_PREFIXES) {
    if (trimmed.startsWith(prefix)) {
      let rest = trimmed.slice(prefix.length);
      // 去除可选后缀
      rest = rest.replace(/(展示|显示|呈现|画|出图)$/, '');
      return detectChartTypeName(rest);
    }
  }

  return null;
}

/** 判断数据是否适合出图（聚合/统计类数据 → true，明细列表 → false） */
function isChartWorthy(cols: string[], rows: Array<Record<string, unknown>>): boolean {
  if (!cols.length || rows.length === 0) return false;
  if (cols.length < 2) return false;
  const valCol = cols[1];
  const numericCount = rows.filter(r => typeof r[valCol] === 'number').length;
  const totalCount = rows.filter(r => {
    const v = r[valCol];
    return v !== null && v !== undefined && v !== '';
  }).length;
  return numericCount > 0 && numericCount >= totalCount * 0.5 && rows.length >= 1;
}

/** 校验值是否为有效数据（null/undefined/空串视为空） */
export function isNullValue(v: unknown): boolean {
  return v === null || v === undefined || v === '';
}

/** 提取单个图表的渲染签名：覆盖影响渲染的全部元数据，不含 rows。
 *  数据变化由 dataVersion 代表，避免对大量 rows 反复 JSON.stringify。 */
function chartSignature(chart: ChartData) {
  const { spec } = chart;
  return {
    id: chart.id,
    title: chart.title,
    error: chart.error ?? null,
    columns: chart.columns,
    dataVersion: chart.dataVersion,
    // 可选布尔归一化：undefined 与 false 语义等价，避免误判为不同
    explicitType: !!chart.explicitType,
    chartOnly: !!chart.chartOnly,
    type: spec.type,
    specTitle: spec.title ?? null,
    xField: spec.xField ?? null,
    yFields: spec.yFields ?? [],
    seriesField: spec.seriesField ?? null,
    sizeField: spec.sizeField ?? null,
    valueField: spec.valueField ?? null,
    min: spec.min ?? null,
    max: spec.max ?? null,
    unit: spec.unit ?? null,
  };
}

/** 比较两个 charts 数组的签名（渲染元数据），用于判断是否需要更新状态。
 *  数组字段（columns / yFields）经 JSON.stringify 按内容与顺序比较，而非引用。 */
function chartsSignatureEqual(a: ChartData[], b: ChartData[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (JSON.stringify(chartSignature(a[i])) !== JSON.stringify(chartSignature(b[i]))) {
      return false;
    }
  }
  return true;
}

export function useSSE() {
  const [currentSessionId, setCurrentSessionId] = useState<string>(initSessionId);
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    const id = loadCurrentId();
    if (!id) return [];
    const sessions = loadAllSessions();
    return sessions[id] || [];
  });
  const [loading, setLoading] = useState(false);
  const [sessionList, setSessionList] = useState<SessionMeta[]>(() =>
    Object.values(loadAllMeta()).sort((a, b) => b.updatedAt - a.updatedAt),
  );
  const abortRef = useRef<AbortController | null>(null);
  const lastDataRef = useRef<{ columns: string[]; rows: Array<Record<string, unknown>> } | null>(null);
  const lastConvIdRef = useRef<string>('');
  const dataVersionRef = useRef<number>(0);

  /** 流式结束后自动保存当前会话到 localStorage */
  useEffect(() => {
    if (loading) return;
    if (!currentSessionId) return;

    const all = loadAllSessions();
    all[currentSessionId] = messages;
    saveAllSessions(all);

    const allMeta = loadAllMeta();
    const existing = allMeta[currentSessionId];
    allMeta[currentSessionId] = {
      id: currentSessionId,
      title: getSessionTitle(messages),
      createdAt: existing?.createdAt || Date.now(),
      updatedAt: Date.now(),
    };
    saveAllMeta(allMeta);
    saveCurrentId(currentSessionId);

    setSessionList(Object.values(allMeta).sort((a, b) => b.updatedAt - a.updatedAt));
  }, [messages, loading, currentSessionId]);

  /** 创建新会话并切换 */
  const createNewSession = useCallback(() => {
    if (loading) return;

    // 保存当前会话
    const all = loadAllSessions();
    all[currentSessionId] = messages;
    saveAllSessions(all);

    const allMeta = loadAllMeta();
    if (messages.length > 0) {
      allMeta[currentSessionId] = {
        id: currentSessionId,
        title: getSessionTitle(messages),
        createdAt: allMeta[currentSessionId]?.createdAt || Date.now(),
        updatedAt: Date.now(),
      };
    }
    saveAllMeta(allMeta);

    // 创建新会话
    const newId = `s_${Date.now()}`;
    setMessages([]);
    setCurrentSessionId(newId);
    saveCurrentId(newId);
    lastDataRef.current = null;
    lastConvIdRef.current = '';

    setSessionList(Object.values(loadAllMeta()).sort((a, b) => b.updatedAt - a.updatedAt));
  }, [currentSessionId, messages, loading]);

  /** 切换到指定历史会话（立即保存当前会话，不触发 SSE） */
  const switchToSession = useCallback((id: string) => {
    if (loading || id === currentSessionId) return;

    // 保存当前会话
    const all = loadAllSessions();
    all[currentSessionId] = messages;
    saveAllSessions(all);

    const allMeta = loadAllMeta();
    if (messages.length > 0) {
      allMeta[currentSessionId] = {
        id: currentSessionId,
        title: getSessionTitle(messages),
        createdAt: allMeta[currentSessionId]?.createdAt || Date.now(),
        updatedAt: Date.now(),
      };
    }
    saveAllMeta(allMeta);

    // 加载目标会话
    const target = all[id] || [];
    setMessages(target);
    setCurrentSessionId(id);
    saveCurrentId(id);
    lastDataRef.current = null;
    lastConvIdRef.current = '';

    setSessionList(Object.values(loadAllMeta()).sort((a, b) => b.updatedAt - a.updatedAt));
  }, [currentSessionId, messages, loading]);

  /** 删除指定会话（同步清除 localStorage + 必要时切换当前会话） */
  const deleteSession = useCallback((id: string) => {
    // 从 localStorage 移除消息和元数据
    const all = loadAllSessions();
    delete all[id];
    saveAllSessions(all);

    const allMeta = loadAllMeta();
    delete allMeta[id];
    saveAllMeta(allMeta);

    // 更新列表
    const remaining = Object.values(allMeta).sort((a, b) => b.updatedAt - a.updatedAt);
    setSessionList(remaining);

    // 删除的是当前会话 → 需要切换
    if (id === currentSessionId) {
      if (remaining.length > 0) {
        // 切换到最近更新的剩余会话
        const nextId = remaining[0].id;
        const target = all[nextId] || [];
        setMessages(target);
        setCurrentSessionId(nextId);
        saveCurrentId(nextId);
      } else {
        // 无剩余会话 → 进入新空白会话
        const newId = `s_${Date.now()}`;
        setMessages([]);
        setCurrentSessionId(newId);
        saveCurrentId(newId);
      }
      lastDataRef.current = null;
      lastConvIdRef.current = '';
    }
  }, [currentSessionId]);

  const sendMessage = useCallback(async (userText: string) => {
    const switchType = isPureChartSwitch(userText);

    // 纯图表切换：仅对单图消息生效
    if (switchType && lastDataRef.current) {
      const lastMsg = messages[messages.length - 1];
      if (lastMsg?.role === 'assistant' && lastMsg.charts.length === 1) {
        const oldChart = lastMsg.charts[0];

        // 优先从 chart 的 source 数据获取原始列行（V2 图表已保存），
        // 否则回退到 lastDataRef（SSE 流结束时的原始查询结果）。
        const sourceColumns = oldChart.sourceColumns ?? lastDataRef.current.columns;
        const sourceRows = oldChart.sourceRows ?? lastDataRef.current.rows;

        // ── 优先走 V2 Planner（从原始数据重新规划，避免基于 transform 后数据二次聚合）──
        const v2Result = prepareChartV2All({
          columns: sourceColumns,
          rows: sourceRows as Row[],
          source: 'user',
          intent: 'auto',
          requestedChartType: switchType,
          id: oldChart.id,
          title: oldChart.title,
          dataVersion: oldChart.dataVersion,
        });

        if (v2Result.ok && v2Result.chart) {
          // V2 成功 — 使用 Planner 产出的完整 ChartData（含新 transform 后的 spec/columns/rows）
          setMessages(prev =>
            prev.map((m, i) =>
              i === prev.length - 1
                ? { ...m, charts: [v2Result.chart!] }
                : m
            )
          );
          return;
        }

        // ── V2 失败 → fallback 到旧 getChartTypeAvailability ──
        const avail = getChartTypeAvailability(oldChart)
          .find(a => a.type === switchType);
        if (avail?.supported && avail.spec) {
          const newChart: ChartData = {
            ...oldChart,
            id: oldChart.id,
            spec: avail.spec,
            explicitType: true,
          };
          setMessages(prev =>
            prev.map((m, i) =>
              i === prev.length - 1
                ? { ...m, charts: [newChart] }
                : m
            )
          );
          return;
        }
        // 类型不可用 → 不吞消息，继续走正常请求
      }
      // 多图消息 / 无数据 / 不可用 → 走正常请求
    }

    // === 本地追加图表：基于上一条查询结果新增图表类型 ===
    const appendType = isPureChartAppend(userText);
    if (appendType) {
      const userMsg: ChatMessage = {
        id: `u_${Date.now()}`,
        role: 'user',
        text: userText,
        dataframes: [],
        charts: [],
        thinkingCollapsed: true,
        streaming: false,
      };
      const assistantMsgId = `a_${Date.now()}`;

      // 查找最近一次数据查询结果：优先图表，其次 dataframe，不跨消息搜索
      let sourceChart: ChartData | null = null;
      let sourceSql: string | null = null;
      let errorText: string | null = null;

      for (let i = messages.length - 1; i >= 0; i--) {
        const m = messages[i];
        if (m.role !== 'assistant') continue;

        const hasCharts = m.charts.length > 0;
        const hasDataframes = m.dataframes.length > 0;
        const hasData = hasCharts || hasDataframes;

        if (!hasData) {
          // 最近一条 assistant 无任何数据，继续往前找
          continue;
        }

        if (hasCharts) {
          const valid = m.charts.find(c => !c.error && c.columns.length > 0 && c.rows.length > 0);
          if (valid) {
            sourceChart = valid;
            sourceSql = m.sql || null;
            break;
          }
        }

        // 有 dataframe 但没有有效图表 → 无法确定字段映射
        if (hasDataframes && !sourceChart) {
          errorText = '上一次查询结果未生成图表，无法确定字段映射。请先用图表展示数据后再追加。';
        }
        break; // 无论是否找到，停在最近一次有数据的消息
      }

      if (errorText) {
        const errorMsg: ChatMessage = {
          id: assistantMsgId,
          role: 'assistant',
          text: errorText,
          dataframes: [],
          charts: [],
          thinkingCollapsed: true,
          streaming: false,
        };
        setMessages(prev => [...prev, userMsg, errorMsg]);
        return;
      }

      if (!sourceChart) {
        const errorMsg: ChatMessage = {
          id: assistantMsgId,
          role: 'assistant',
          text: '当前没有可用的图表数据，无法生成新图表。请先进行一次数据查询。',
          dataframes: [],
          charts: [],
          thinkingCollapsed: true,
          streaming: false,
        };
        setMessages(prev => [...prev, userMsg, errorMsg]);
        return;
      }

      // 用目标类型构造 spec，复用原图的字段映射
      let newSpec: ChartSpec;
      let option: unknown;
      try {
        newSpec = normalizeChartSpec(
          { ...sourceChart.spec, type: appendType },
          appendType,
        );
        const testChart: ChartData = {
          ...sourceChart,
          id: '',
          spec: newSpec,
          title: newSpec.title || sourceChart.title,
          explicitType: true,
        };
        option = buildChartOption(testChart);
      } catch {
        const errorMsg: ChatMessage = {
          id: assistantMsgId,
          role: 'assistant',
          text: '当前数据无法生成该图表类型。',
          dataframes: [],
          charts: [],
          thinkingCollapsed: true,
          streaming: false,
        };
        setMessages(prev => [...prev, userMsg, errorMsg]);
        return;
      }

      if (!option) {
        const errorMsg: ChatMessage = {
          id: assistantMsgId,
          role: 'assistant',
          text: '当前数据无法生成该图表类型。',
          dataframes: [],
          charts: [],
          thinkingCollapsed: true,
          streaming: false,
        };
        setMessages(prev => [...prev, userMsg, errorMsg]);
        return;
      }

      // 创建新 assistant 消息展示追加图表
      const chartLabel = CHART_TYPE_LABELS[appendType] || appendType;
      const assistantMsg: ChatMessage = {
        id: assistantMsgId,
        role: 'assistant',
        text: `已生成${chartLabel}`,
        dataframes: [],
        charts: [{
          ...sourceChart,
          id: `${assistantMsgId}-chart-0`,
          spec: newSpec,
          title: newSpec.title || sourceChart.title,
          explicitType: true,
          chartOnly: true,
        }],
        // 复用来源消息的 SQL，使追加图表添加到仪表板时 sourceSql 不为 null
        sql: sourceSql,
        thinkingCollapsed: true,
        streaming: false,
      };

      setMessages(prev => [...prev, userMsg, assistantMsg]);
      return;
    }

    // 正常请求
    const userMsg: ChatMessage = {
      id: `u_${Date.now()}`,
      role: 'user',
      text: userText,
      dataframes: [],
      charts: [],
      thinkingCollapsed: true,
      streaming: false,
    };

    const assistantMsgId = `a_${Date.now()}`;
    const assistantMsg: ChatMessage = {
      id: assistantMsgId,
      role: 'assistant',
      text: '',
      dataframes: [],
      charts: [],
      thinkingCollapsed: true,
      streaming: true,
    };

    setMessages(prev => [...prev, userMsg, assistantMsg]);
    setLoading(true);

    // 重置 lastDataRef：新请求不得复用上一条消息的数据
    lastDataRef.current = null;

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch('/api/vanna/v2/chat_sse', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream',
        },
        body: JSON.stringify({
          message: userText,
          conversation_id: lastConvIdRef.current || undefined,
          request_id: undefined,
        }),
        signal: controller.signal,
      });

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let buffer = '';

      const dataframes: DataFrameData[] = [];
      let finalText = '';
      let sqlText: string | null = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const dataStr = line.slice(6).trim();
          if (dataStr === '[DONE]') continue;

          try {
            const event: SSEEvent = JSON.parse(dataStr);
            const { rich, conversation_id } = event;
            lastConvIdRef.current = conversation_id;

            if (!rich) continue;

            if (rich.type === 'dataframe') {
              const dataDict = rich.data as Record<string, unknown>;
              const dfData = dataDict as unknown as DataFrameData;
              dataframes.push(dfData);
              // 每次 dataframe 事件同步更新 SQL（始终对应最后一个 dataframe）
              sqlText = typeof dataDict.sql === 'string' && dataDict.sql.trim() ? dataDict.sql : null;
              if (dfData.data && dfData.columns) {
                lastDataRef.current = {
                  columns: dfData.columns,
                  rows: dfData.data,
                };
              }
              dataVersionRef.current += 1;
              setMessages(prev =>
                prev.map(m => {
                  if (m.id !== assistantMsgId) return m;
                  // DataFrame 更新后，已有 charts 必须使用最新 columns/rows/dataVersion 引用
                  const updatedCharts = m.charts.length > 0 && dfData.data && dfData.columns
                    ? m.charts.map(c => ({
                        ...c,
                        columns: dfData.columns,
                        rows: dfData.data,
                        dataVersion: dataVersionRef.current,
                      }))
                    : m.charts;
                  const next = { ...m, dataframes: [...dataframes], charts: updatedCharts, sql: sqlText };
                  return next;
                })
              );
            } else if (rich.type === 'text') {
              const content = (rich.data as Record<string, unknown>).content as string;
              if (content) {
                finalText = stripChartAnnotations(content);
                const ct = extractChartType(content);

                // 提取所有 chart_spec
                if (lastDataRef.current) {
                  const { columns, rows } = lastDataRef.current;
                  const newCharts = extractAllChartSpecs(
                    content,
                    assistantMsgId,
                    columns,
                    validateChartSpec,
                    dataVersionRef.current,
                  );

                  if (newCharts.length > 0) {
                    // 填充 rows 引用（所有图表共享同一份 rows，不深拷贝）
                    const filledCharts = newCharts
                      .filter(c => !c.error)
                      .map(c => ({ ...c, rows }));
                    const errorCharts = newCharts.filter(c => !!c.error);
                    const merged = [...filledCharts, ...errorCharts];
                    // 只有签名变化时才更新状态
                    setMessages(prev =>
                      prev.map(m => {
                        if (m.id !== assistantMsgId) return m;
                        if (chartsSignatureEqual(m.charts, merged)) return m;
                        const next = { ...m, text: finalText, charts: merged, dataframes: [...dataframes] };
                        return next;
                      })
                    );
                  } else if (ct && ct !== 'none' && isChartWorthy(columns, rows)) {
                    // 旧 chart_type 兼容：构造单图
                    const fallbackChart: ChartData = {
                      id: `${assistantMsgId}-chart-0`,
                      columns,
                      rows,
                      spec: fallbackSpecFromColumns(ct, columns),
                      title: '数据可视化',
                      dataVersion: dataVersionRef.current,
                    };
                    const newCharts = [fallbackChart];
                    if (!chartsSignatureEqual(
                      messages.find(m => m.id === assistantMsgId)?.charts ?? [],
                      newCharts
                    )) {
                      setMessages(prev =>
                        prev.map(m =>
                          m.id === assistantMsgId
                            ? { ...m, text: finalText, charts: newCharts, dataframes: [...dataframes] }
                            : m
                        )
                      );
                    }
                  } else if (!ct) {
                    // V2 Pipeline：自动推荐图表（替代 isChartWorthy + fallbackSpecFromColumns）
                    const v2Result = prepareChartV2All({
                      columns,
                      rows,
                      source: 'auto',
                      intent: 'auto',
                      id: `${assistantMsgId}-chart-0`,
                      title: '数据可视化',
                      dataVersion: dataVersionRef.current,
                    });

                    if (v2Result.ok && v2Result.chart) {
                      // V2 成功 → 使用完整 ChartData（含 transform 后的 columns/rows/spec）
                      const nextCharts = [v2Result.chart];
                      setMessages(prev =>
                        prev.map(m => {
                          if (m.id !== assistantMsgId) return m;
                          if (chartsSignatureEqual(m.charts, nextCharts)) {
                            // 图表签名未变，但仍需更新 text 和 dataframes
                            return { ...m, text: finalText, dataframes: [...dataframes] };
                          }
                          return { ...m, text: finalText, charts: nextCharts, dataframes: [...dataframes] };
                        })
                      );
                    } else if (v2Result.errorCode === 'no_default_plan') {
                      // Planner 判断不应生成图表 → 明确清空 charts
                      setMessages(prev =>
                        prev.map(m =>
                          m.id === assistantMsgId
                            ? { ...m, text: finalText, charts: [], dataframes: [...dataframes] }
                            : m
                        )
                      );
                    } else {
                      // 其他 Pipeline 错误 → 记录 warning，清空 charts
                      console.warn(`[useSSE] V2 pipeline error: ${v2Result.errorCode}`);
                      setMessages(prev =>
                        prev.map(m =>
                          m.id === assistantMsgId
                            ? { ...m, text: finalText, charts: [], dataframes: [...dataframes] }
                            : m
                        )
                      );
                    }
                  } else {
                    // 无图表（ct === 'none'，或存在旧 chart_type 但数据不适合出图）：
                    // 显式清空 charts，清除流式过程中已经生成的临时图表
                    setMessages(prev =>
                      prev.map(m =>
                        m.id === assistantMsgId
                          ? { ...m, text: finalText, charts: [], dataframes: [...dataframes] }
                          : m
                      )
                    );
                  }
                }
              }
            } else if (rich.type === 'chart') {
              const chartInfo = rich.data as Record<string, unknown>;
              if (lastDataRef.current && isChartWorthy(lastDataRef.current.columns, lastDataRef.current.rows)) {
                const { columns, rows } = lastDataRef.current;
                const fallbackChart: ChartData = {
                  id: `${assistantMsgId}-chart-0`,
                  columns,
                  rows,
                  spec: fallbackSpecFromColumns('bar', columns, (chartInfo.title as string) || '数据可视化'),
                  title: (chartInfo.title as string) || '数据可视化',
                  dataVersion: dataVersionRef.current,
                };
                setMessages(prev =>
                  prev.map(m =>
                    m.id === assistantMsgId
                      ? { ...m, charts: [fallbackChart], dataframes: [...dataframes] }
                      : m
                  )
                );
              }
            }
          } catch {
            // 忽略 JSON 解析错误
          }
        }
      }

      // 流结束：确保最终状态正确
      setMessages(prev =>
        prev.map(m =>
          m.id === assistantMsgId
            ? {
                ...m,
                text: finalText,
                dataframes: [...dataframes],
                streaming: false,
                sql: sqlText,
              }
            : m
        )
      );
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setMessages(prev =>
        prev.map(m =>
          m.id === assistantMsgId
            ? { ...m, text: '请求失败，请重试', streaming: false }
            : m
        )
      );
    } finally {
      setLoading(false);
    }
  }, [messages]);

  const cancelRequest = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    lastDataRef.current = null;
    lastConvIdRef.current = '';
  }, []);

  /** 替换指定消息中第 chartIndex 个 chart（V2 点击切换用） */
  const replaceMessageChart = useCallback(
    (messageId: string, chartIndex: number, newChart: ChartData) => {
      setMessages(prev =>
        prev.map(msg => {
          if (msg.id !== messageId) return msg;
          const oldChart = msg.charts[chartIndex];
          if (!oldChart) return msg;
          return {
            ...msg,
            charts: msg.charts.map((c, i) =>
              i === chartIndex
                ? {
                    ...newChart,
                    // 保留原 chart 的 UI 元数据字段
                    chartOnly: oldChart.chartOnly,
                    dataVersion: oldChart.dataVersion,
                  }
                : c,
            ),
          };
        }),
      );
    },
    [],
  );

  return { messages, loading, sendMessage, cancelRequest, clearMessages, replaceMessageChart, sessionList, currentSessionId, createNewSession, switchToSession, deleteSession };
}
