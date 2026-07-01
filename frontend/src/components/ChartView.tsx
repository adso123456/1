import { useState, useMemo, useRef, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import type { ChartData, ChartSpec, ChartTypeAvailability, RenderableChartType } from '../types';
import {
  buildChartOption,
  CHART_TYPE_LABELS,
  getChartTypeAvailability,
  isNullValue,
  isRenderableChartType,
} from '../chartRegistry';
import { generateChartDescription } from '../chartDescription';
import { formatCellValue, formatColumnLabel } from '../utils/tableFormatting';

interface Props {
  chart: ChartData;
  /** 多图模式下隐藏 ECharts 内部标题（由外部卡片标题代替） */
  hideTitle?: boolean;
  onChangeType?: (type: RenderableChartType) => void;
  /** 隐藏"图表/表格"切换，始终显示图表（仪表板和弹窗预览用） */
  hideTableToggle?: boolean;
  /** 隐藏图表下方自动生成的文字说明（仪表板精简用） */
  hideDescription?: boolean;
  /** 撑满父容器高度（仪表板缩放用），设置后 ECharts 不再使用固定 350px */
  fillHeight?: boolean;
}

type ViewMode = 'chart' | 'table';

export function ChartView({ chart, hideTitle, onChangeType, hideTableToggle, hideDescription, fillHeight }: Props) {
  const isChartOnly = !!chart.chartOnly;

  const [viewMode, setViewMode] = useState<ViewMode>('chart');

  // hideTableToggle 时始终按图表模式处理
  const effectiveViewMode: ViewMode = hideTableToggle ? 'chart' : viewMode;

  const echartsRef = useRef<ReactECharts>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // 下拉菜单状态
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** 显示 toast 提示，自动清除上一次 */
  const showToast = (msg: string) => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToastMessage(msg);
    toastTimerRef.current = setTimeout(() => setToastMessage(null), 2000);
  };

  /** 全部 13 种图表类型的可用性评估（含完整 Spec） */
  const allTypes = useMemo<ChartTypeAvailability[]>(
    () => getChartTypeAvailability(chart),
    [chart],
  );

  /** 从 allTypes 中按优先级选取默认类型 */
  function pickDefault(): RenderableChartType {
    const recommended = isRenderableChartType(chart.spec.type) ? chart.spec.type : null;

    // 1. LLM 推荐类型可用（包括 combo）
    if (recommended) {
      const rec = allTypes.find(t => t.type === recommended && t.supported);
      if (rec) return rec.type;
    }

    // 2. 柱状图可用
    const bar = allTypes.find(t => t.type === 'bar' && t.supported);
    if (bar) return bar.type;

    // 3. 第一个可用类型
    const first = allTypes.find(t => t.supported);
    if (first) return first.type;

    return 'bar';
  }

  const [localType, setLocalType] = useState<RenderableChartType>(() => pickDefault());

  // 跟踪上次 dataVersion 和 spec.type，用于判断数据/推荐类型是否真正变化
  const prevDataVersionRef = useRef(chart.dataVersion);
  const prevSpecTypeRef = useRef(chart.spec.type);

  useEffect(() => {
    const dataChanged = prevDataVersionRef.current !== chart.dataVersion;
    const specChanged = prevSpecTypeRef.current !== chart.spec.type;

    prevDataVersionRef.current = chart.dataVersion;
    prevSpecTypeRef.current = chart.spec.type;

    if (dataChanged || specChanged) {
      setLocalType(pickDefault());
    }
  }, [chart.dataVersion, chart.spec.type, allTypes]);

  /** 当前类型对应的完整渲染 Spec（从 availability 中获取） */
  const activeSpec = useMemo<ChartSpec | null>(() => {
    const item = allTypes.find(t => t.type === localType);
    return item?.spec ?? null;
  }, [allTypes, localType]);

  // 首次初始化完成后 resize，解决 container 宽度未定导致图例错位的问题
  const handleChartReady = () => {
    const instance = echartsRef.current?.getEchartsInstance();
    if (instance) {
      requestAnimationFrame(() => instance.resize());
    }
  };

  const handleTypeChange = (type: RenderableChartType) => {
    // 切换前清空画布，避免旧图表类型配置残留
    const instance = echartsRef.current?.getEchartsInstance();
    if (instance) {
      instance.clear();
    }
    setLocalType(type);
    onChangeType?.(type);
  };

  // 图表类型或显示模式变化后，在下一帧 resize 确保图例布局正确
  useEffect(() => {
    if (effectiveViewMode === 'chart' || isChartOnly) {
      const instance = echartsRef.current?.getEchartsInstance();
      if (instance) {
        requestAnimationFrame(() => instance.resize());
      }
    }
  }, [effectiveViewMode, localType, isChartOnly]);

  // fillHeight 模式下监听容器尺寸变化，驱动 ECharts resize
  useEffect(() => {
    if (!fillHeight) return;
    const el = containerRef.current;
    if (!el) return;

    let rafId: number | null = null;

    const observer = new ResizeObserver(() => {
      if (rafId !== null) return; // 合并同一帧
      rafId = requestAnimationFrame(() => {
        rafId = null;
        const instance = echartsRef.current?.getEchartsInstance();
        if (instance) instance.resize();
      });
    });

    observer.observe(el);
    return () => {
      observer.disconnect();
      if (rafId !== null) cancelAnimationFrame(rafId);
    };
  }, [fillHeight]);

  // 下拉菜单：点击外部或按 Escape 关闭
  useEffect(() => {
    if (!dropdownOpen) return;

    const handleMouseDown = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDropdownOpen(false);
    };

    document.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleMouseDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [dropdownOpen]);

  const option = useMemo(() => {
    // 使用 availability 中验证通过的完整 Spec（不容许仅替换 type）
    if (!activeSpec) return null;
    // 所有类型切换均为用户显式操作（包括自动选择），避免 buildAxisChart 的 explicitType 守卫误判
    const opt = buildChartOption({ ...chart, spec: activeSpec, explicitType: true });
    if (opt && hideTitle) {
      const stripped = { ...opt };
      delete (stripped as Record<string, unknown>).title;
      return stripped;
    }
    return opt;
  }, [chart, activeSpec, hideTitle]);

  // 图表说明（基于当前实际渲染类型）
  const effectiveType = localType;

  const description = useMemo(() => {
    return generateChartDescription(chart, effectiveType);
  }, [chart, effectiveType]);

  // 表格渲染
  const cleanRows = chart.rows.filter(
    r => !isNullValue(r[chart.columns[0]])
  );

  return (
    <div
      ref={containerRef}
      style={{
        ...(fillHeight ? { height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column' } : {}),
        marginTop: isChartOnly ? 0 : (fillHeight ? 0 : 12),
      }}
    >
      {/* 工具栏 */}
      {/* chartOnly 时隐藏表格切换但保留类型下拉 */}
      {(effectiveViewMode === 'chart' || isChartOnly) && (
      <div style={{ display: 'flex', gap: 8, marginBottom: isChartOnly ? 0 : 12, flexWrap: 'wrap', alignItems: 'center' }}>
        {/* 一级：图表 / 表格 — chartOnly 或 hideTableToggle 时隐藏 */}
        {!isChartOnly && !hideTableToggle && (
        <div style={{ display: 'flex', gap: 4, border: '1px solid #e5e7eb', borderRadius: 6, padding: 3 }}>
          <button
            onClick={() => setViewMode('chart')}
            style={{
              padding: '4px 14px',
              border: 'none',
              borderRadius: 4,
              cursor: 'pointer',
              fontSize: 12,
              fontWeight: viewMode === 'chart' ? 600 : 400,
              backgroundColor: viewMode === 'chart' ? '#2563eb' : 'transparent',
              color: viewMode === 'chart' ? '#fff' : '#6b7280',
              transition: 'all .15s',
            }}
          >
            图表
          </button>
          <button
            onClick={() => setViewMode('table')}
            style={{
              padding: '4px 14px',
              border: 'none',
              borderRadius: 4,
              cursor: 'pointer',
              fontSize: 12,
              fontWeight: effectiveViewMode === 'table' ? 600 : 400,
              backgroundColor: effectiveViewMode === 'table' ? '#2563eb' : 'transparent',
              color: effectiveViewMode === 'table' ? '#fff' : '#6b7280',
              transition: 'all .15s',
            }}
          >
            表格
          </button>
        </div>
        )}

        {/* 二级：图表类型下拉菜单（始终显示） */}
        <div ref={dropdownRef} style={{ position: 'relative' }}>
          {/* 当前类型按钮 */}
          <button
            onClick={() => setDropdownOpen(prev => !prev)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              padding: '4px 10px',
              border: '1px solid #d1d5db',
              borderRadius: 6,
              cursor: 'pointer',
              fontSize: 12,
              fontWeight: 500,
              backgroundColor: dropdownOpen ? '#f3f4f6' : '#fff',
              color: '#374151',
              transition: 'all .15s',
            }}
          >
            {CHART_TYPE_LABELS[localType]}
            <span style={{ fontSize: 9, color: '#9ca3af' }}>
              {dropdownOpen ? '▲' : '▼'}
            </span>
          </button>

          {/* 下拉面板 */}
          {dropdownOpen && (
            <>
              {/* Toast 提示 */}
              {toastMessage && (
                <div style={{
                  position: 'absolute',
                  bottom: 'calc(100% + 8px)',
                  left: 0,
                  padding: '6px 12px',
                  backgroundColor: '#374151',
                  color: '#fff',
                  borderRadius: 6,
                  fontSize: 12,
                  whiteSpace: 'nowrap',
                  zIndex: 102,
                  boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
                }}>
                  {toastMessage}
                </div>
              )}

              <div style={{
                position: 'absolute',
                top: '100%',
                left: 0,
                marginTop: 4,
                backgroundColor: '#fff',
                border: '1px solid #e5e7eb',
                borderRadius: 8,
                boxShadow: '0 4px 16px rgba(0,0,0,0.1)',
                zIndex: 100,
                minWidth: 150,
                maxHeight: 360,
                overflowY: 'auto',
                padding: '4px 0',
              }}>
                {allTypes.map(t => {
                  const isCurrent = t.type === localType;
                  return (
                    <button
                      key={t.type}
                      onClick={() => {
                        if (t.supported && t.spec) {
                          handleTypeChange(t.type);
                          setDropdownOpen(false);
                        } else {
                          showToast('该数据类型暂不支持该图表');
                        }
                      }}
                      aria-disabled={!t.supported || undefined}
                      style={{
                        display: 'block',
                        width: '100%',
                        textAlign: 'left',
                        padding: '6px 14px',
                        border: 'none',
                        cursor: t.supported ? 'pointer' : 'not-allowed',
                        fontSize: 12,
                        backgroundColor: isCurrent ? '#eff6ff' : 'transparent',
                        color: t.supported ? '#374151' : '#d1d5db',
                        fontWeight: isCurrent ? 500 : 400,
                        transition: 'background-color .1s',
                      }}
                      onMouseEnter={e => {
                        if (t.supported) {
                          e.currentTarget.style.backgroundColor = isCurrent ? '#dbeafe' : '#f3f4f6';
                        }
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.backgroundColor = isCurrent ? '#eff6ff' : 'transparent';
                      }}
                    >
                      {t.label}
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
      )}

      {/* 内容区 */}
      {(isChartOnly || effectiveViewMode === 'chart') && option ? (
        <ReactECharts
          ref={echartsRef}
          option={option}
          notMerge={true}
          onChartReady={handleChartReady}
          style={fillHeight ? { flex: 1, minHeight: 0, width: '100%' } : { height: 350 }}
        />
      ) : !isChartOnly && effectiveViewMode === 'chart' && !option ? (
        <div
          style={{
            color: '#ef4444',
            fontSize: 13,
            padding: '16px',
            backgroundColor: '#fef2f2',
            borderRadius: 6,
            border: '1px solid #fecaca',
            textAlign: 'center',
          }}
        >
          该图表类型不适用于当前数据
        </div>
      ) : null}

      {/* 图表说明 — hideDescription 时隐藏 */}
      {!hideDescription && (isChartOnly || effectiveViewMode === 'chart') && option && description && (
        <div style={{
          marginTop: 10,
          fontSize: 12,
          color: '#6b7280',
          lineHeight: 1.6,
          padding: '6px 12px',
          backgroundColor: '#f9fafb',
          borderRadius: 4,
          border: '1px solid #f3f4f6',
        }}>
          {description}
        </div>
      )}

      {!isChartOnly && (effectiveViewMode === 'table' || !option) && (
        <div style={{ overflowX: 'auto' }}>
          {cleanRows.length === 0 ? (
            <div style={{ color: '#9ca3af', padding: 20, textAlign: 'center' }}>
              该条件下没有查到数据
            </div>
          ) : (
            <table style={{ borderCollapse: 'collapse', fontSize: 12, width: '100%' }}>
              <thead>
                <tr style={{ backgroundColor: '#f3f4f6' }}>
                  <th style={{ padding: '6px 8px', textAlign: 'center', borderBottom: '2px solid #e5e7eb', fontWeight: 500, color: '#9ca3af', width: 40 }}>
                    #
                  </th>
                  {chart.columns.map(col => (
                    <th key={col} style={{ padding: '6px 12px', textAlign: 'left', borderBottom: '2px solid #e5e7eb', fontWeight: 500 }}>
                      {formatColumnLabel(col)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {cleanRows.map((row, ri) => (
                  <tr key={ri} style={{ borderBottom: '1px solid #f3f4f6' }}>
                    <td style={{ padding: '5px 8px', textAlign: 'center', color: '#9ca3af', fontSize: 11 }}>
                      {ri + 1}
                    </td>
                    {chart.columns.map(col => (
                      <td key={col} style={{ padding: '5px 12px', color: '#374151' }}>
                        {formatCellValue(row[col])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
