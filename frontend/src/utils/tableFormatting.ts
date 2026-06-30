/**
 * 表格格式化工具 — 仪表板 TableView 与对话 Markdown 表格统一展示规则
 *
 * 对话中的表格由 LLM 生成 Markdown，包含中文列名、格式化日期、— 表示空值。
 * 仪表板 TableView 直接消费 DataFrameData 原始数据，需要同一套展示规则。
 */

// ---------- 大小写需要修正的常见字段 ----------
const CASE_FIX_MAP: Record<string, string> = {
  ph: 'pH',
  cod: 'COD',
  bod: 'BOD',
  tn: 'TN',
  tp: 'TP',
  tpn: 'TPN',
  nh3n: 'NH3N',
  nh3_n: 'NH3-N',
  do_: 'DO',
  do: 'DO',
  ss: 'SS',
  tds: 'TDS',
  tss: 'TSS',
  orp: 'ORP',
  toc: 'TOC',
  ec: 'EC',
};

/** ISO 日期时间正则：2025-01-15T00:00:00 或 2025-01-15T00:00:00.000Z */
const ISO_DATETIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/;

/** 纯日期正则：2025-01-15 */
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * 格式化单元格值
 * - null / undefined / '' → '—'
 * - ISO 日期时间（含 T）→ 只保留日期部分
 * - object → JSON.stringify
 * - 其他 → String(v)
 */
export function formatCellValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'object') {
    try { return JSON.stringify(v); } catch { return '—'; }
  }
  const str = String(v);
  // ISO datetime: 2025-01-15T00:00:00 → 2025-01-15
  if (ISO_DATETIME_RE.test(str)) return str.slice(0, 10);
  return str;
}

/**
 * 格式化列名
 * - 有 labelMap 时优先使用映射值
 * - 对已知字段做大小写修正（ph→pH, cod→COD 等）
 * - 否则返回原始列名
 */
export function formatColumnLabel(col: string, labelMap?: Record<string, string>): string {
  if (labelMap && labelMap[col]) return labelMap[col];
  return CASE_FIX_MAP[col.toLowerCase()] ?? col;
}

/**
 * 从 Markdown 文本中解析第一个表格的表头
 * 返回表头列名数组，解析失败返回 null
 */
export function parseMarkdownTableHeaders(markdown: string): string[] | null {
  if (!markdown) return null;

  const lines = markdown.split('\n');

  // 找到第一个以 | 开头的行（表头行）
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line.startsWith('|') || !line.endsWith('|')) continue;
    // 下一行必须是分隔行 |---|---|
    const nextLine = i + 1 < lines.length ? lines[i + 1].trim() : '';
    if (!/^\|[\s\-:|]+\|$/.test(nextLine)) continue;

    const headers = line
      .split('|')
      .map(s => s.trim())
      .filter(s => s !== '');
    if (headers.length > 0) return headers;
  }

  return null;
}

/**
 * 判断列名是否为行号列（LLM 在 Markdown 表格中习惯加的这一列）
 */
function isRowNumberHeader(col: string): boolean {
  return /^(#|排名|序号|序|No\.?|编号|行号|row|num|index)$/i.test(col);
}

/**
 * 尝试从列名数组和 Markdown 表头建立列标签映射
 * - headers 比 columns 多一列且首列为行号列：自动剥离首列后映射
 * - 长度仍不一致：返回 undefined
 */
export function buildColumnLabelMap(
  columns: string[],
  markdownHeaders: string[],
): Record<string, string> | undefined {
  let headers = markdownHeaders;
  if (
    headers.length === columns.length + 1 &&
    headers.length >= 2 &&
    isRowNumberHeader(headers[0])
  ) {
    headers = headers.slice(1);
  }
  if (columns.length !== headers.length) return undefined;
  const map: Record<string, string> = {};
  for (let i = 0; i < columns.length; i++) {
    map[columns[i]] = headers[i];
  }
  return map;
}
