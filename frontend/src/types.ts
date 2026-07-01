/** SSE 事件中的 rich 组件数据 */
export interface RichData {
  type: string;
  id: string;
  lifecycle: string;
  timestamp: string;
  visible: boolean;
  interactive: boolean;
  data: Record<string, unknown>;
}

/** 单个 SSE 事件 */
export interface SSEEvent {
  rich: RichData;
  simple: {
    type: string;
    semantic_type: string | null;
    metadata: unknown;
    text: string;
  } | null;
  conversation_id: string;
  request_id: string;
  timestamp: number;
}

/** DataFrame 中的表格数据 */
export interface DataFrameData {
  data: Array<Record<string, unknown>>;
  columns: string[];
  row_count: number;
  column_count: number;
  title?: string;
  description?: string;
  /** 列名→中文标签映射（从 Markdown 表格表头解析或后端提供） */
  columnLabels?: Record<string, string>;
}

export type ChartType =
  | 'bar'
  | 'horizontal_bar'
  | 'line'
  | 'area'
  | 'pie'
  | 'donut'
  | 'scatter'
  | 'bubble'
  | 'radar'
  | 'heatmap'
  | 'boxplot'
  | 'gauge'
  | 'combo'
  | 'none';

export type RenderableChartType = Exclude<ChartType, 'none'>;

/** 单个图表类型的可用性评估结果 */
export interface ChartTypeAvailability {
  type: RenderableChartType;
  label: string;
  supported: boolean;
  /** 不支持时的原因说明 */
  reason: string;
  /** 通过 buildChartOption 验证的完整 Spec（supported=true 时非 null） */
  spec: ChartSpec | null;
}

export interface ChartSpec {
  type: ChartType;
  title?: string;
  xField?: string | null;
  yFields?: string[];
  seriesField?: string | null;
  sizeField?: string | null;
  /** 热力值 / 箱线值 / 仪表盘值 */
  valueField?: string | null;
  /** 仪表盘最小值 */
  min?: number | null;
  /** 仪表盘最大值 */
  max?: number | null;
  /** 仪表盘单位 */
  unit?: string | null;
}

/** 图表数据（从 dataframe 提取后构造） */
export interface ChartData {
  /** 稳定 ID，格式 `${messageId}-chart-${matchIndex}` */
  id: string;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  spec: ChartSpec;
  title: string;
  /** 错误信息（解析失败 / 校验失败 / option 为空），非空时前端显示错误卡片 */
  error?: string;
  /** 数据版本号，每次 DataFrame SSE 事件递增，用于 Error Boundary 恢复判断 */
  dataVersion: number;
  /** 用户显式指定的图表类型（追加/切换），为 true 时 ChartView 跳过智能候选覆盖 */
  explicitType?: boolean;
  /** 仅图表模式（追加图表）：隐藏工具栏，固定使用 spec.type */
  chartOnly?: boolean;
}

/** 会话元数据 */
export interface SessionMeta {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
}

/** 仪表板布局信息（拖拽和缩放后的位置和尺寸） */
export interface DashboardLayoutInfo {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 仪表板中的图表快照 */
export interface DashboardChartItem {
  type: 'chart';
  id: string;
  sourceSessionId: string;
  sourceMessageId: string;
  addedAt: number;
  chart: ChartData;
  /** 网格布局坐标（x=列, y=行, w=宽度, h=高度），缺失时自动生成 */
  layout?: DashboardLayoutInfo;
}

/** 仪表板中的表格快照 */
export interface DashboardTableItem {
  type: 'table';
  id: string;
  sourceSessionId: string;
  sourceMessageId: string;
  addedAt: number;
  table: DataFrameData;
  /** 网格布局坐标（x=列, y=行, w=宽度, h=高度），缺失时自动生成 */
  layout?: DashboardLayoutInfo;
}

export type DashboardItem = DashboardChartItem | DashboardTableItem;

/** 单个仪表板元数据（含所有卡片） */
export interface DashboardMeta {
  id: string;
  name: string;
  createdAt: number;
  updatedAt: number;
  items: DashboardItem[];
}

/** 一条聊天消息 */
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  /** Markdown 文本 */
  text: string;
  /** AI 消息的数据表格列表（中间步骤在前，最终在后） */
  dataframes: DataFrameData[];
  /** 图表数组 —— 运行时唯一图表数据源，单图时长度为 1 */
  charts: ChartData[];
  /** 中间步骤是否折叠 */
  thinkingCollapsed: boolean;
  /** 当前是否在流式传输中 */
  streaming: boolean;
  /** 本次查询实际执行的 SQL（仅 SELECT 成功时返回） */
  sql?: string | null;
}
