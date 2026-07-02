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

/**
 * 精确业务字段中文映射（键统一按小写处理）。
 * 只收录含义明确的数据库字段，不猜测模糊字段。
 */
const FIELD_LABEL_MAP: Record<string, string> = {
  // 时间
  sampling_time: '采样时间', samping_time: '采样时间', sample_time: '采样时间',
  sampling_date: '采样日期', monitoring_time: '监测时间', monitor_time: '监测时间',
  monitoring_date: '监测日期', monitor_date: '监测日期', record_time: '记录时间',
  create_time: '创建时间', created_at: '创建时间', update_time: '更新时间', updated_at: '更新时间',
  // 排口
  outlet_name: '排口名称', outfall_name: '排口名称', discharge_outlet_name: '排口名称',
  outlet_id: '排口编号', outlet_code: '排口编码', outlet_type: '排口类型',
  outlet_count: '排污口数量', outfall_count: '排污口数量',
  // 地区
  district: '区县', district_name: '区县名称', region: '地区', region_name: '地区名称',
  city: '城市', city_name: '城市名称', county: '区县', county_name: '区县名称',
  administrative_region: '行政区域',
  // 对象
  enterprise_name: '企业名称', company_name: '企业名称', unit_name: '单位名称',
  project_name: '项目名称', station_name: '站点名称', site_name: '站点名称',
  monitor_point_name: '监测点名称', section_name: '断面名称', plant_name: '水厂名称',
  // 位置
  address: '地址', location: '位置', longitude: '经度', latitude: '纬度',
  // 通用
  id: '编号', code: '编码', name: '名称', type: '类型', status: '状态',
  count: '数量', record_count: '记录数量', total_count: '总数',
  description: '说明', remark: '备注', remarks: '备注',
};

/**
 * 组合字段翻译 token 映射。所有 token 都有映射时才组合翻译，
 * 任意 token 未知则保留完整原字段名，避免出现“未知词数量”之类的混合结果。
 */
const TOKEN_LABEL_MAP: Record<string, string> = {
  sampling: '采样', sample: '采样', samping: '采样',
  monitoring: '监测', monitor: '监测',
  time: '时间', date: '日期',
  outlet: '排口', outfall: '排口',
  district: '区县', county: '区县', region: '地区', city: '城市',
  enterprise: '企业', company: '企业',
  station: '站点', site: '站点', point: '点',
  name: '名称', count: '数量', code: '编码', id: '编号',
};

/** 将字段名拆分为 token：支持 snake_case / kebab-case / camelCase */
function splitTokens(col: string): string[] {
  const tokens: string[] = [];
  // 先按 _ 和 - 分割
  const parts = col.split(/[_-]+/).filter(Boolean);
  for (const p of parts) {
    // 再拆 camelCase：在 小写/数字 与 大写 之间插入空格
    const sub = p.replace(/([a-z0-9])([A-Z])/g, '$1 $2').split(/\s+/).filter(Boolean);
    tokens.push(...sub);
  }
  return tokens;
}

/** ISO 日期时间正则：2025-01-15T00:00:00 或 2025-01-15T00:00:00.000Z */
const ISO_DATETIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/;

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
 * 格式化列名（统一字段展示入口）。优先级：
 * 1. 调用方传入的 labelMap
 * 2. 精确业务字段中文映射（FIELD_LABEL_MAP）
 * 3. BOD/COD/pH 等专业缩写大小写映射（CASE_FIX_MAP）
 * 4. 安全的英文组合字段翻译（TOKEN_LABEL_MAP，所有 token 都需有映射）
 * 5. 无法可靠翻译时保留原字段名
 * 已含中文的字段默认保留，避免二次翻译。
 */
export function formatColumnLabel(col: string, labelMap?: Record<string, string>): string {
  // 1. 调用方传入的 labelMap
  if (labelMap && labelMap[col]) return labelMap[col];
  // 已含中文 → 直接保留
  if (/[一-龥]/.test(col)) return col;
  const lower = col.toLowerCase();
  // 2. 精确业务字段中文映射
  if (FIELD_LABEL_MAP[lower]) return FIELD_LABEL_MAP[lower];
  // 3. 专业缩写大小写映射
  if (CASE_FIX_MAP[lower]) return CASE_FIX_MAP[lower];
  // 4. 安全组合翻译：所有 token 都有映射才组合，否则保留原字段名
  const tokens = splitTokens(col);
  if (tokens.length > 0) {
    const lowers = tokens.map(t => t.toLowerCase());
    if (lowers.every(t => TOKEN_LABEL_MAP[t])) {
      return lowers.map(t => TOKEN_LABEL_MAP[t]).join('');
    }
  }
  // 5. 保留原字段名
  return col;
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
