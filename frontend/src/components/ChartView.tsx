import { useState, useMemo, useRef, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import type { ChartData, RenderableChartType } from '../types';
import {
  buildChartOption,
  CHART_TYPE_LABELS,
  getCompatibleChartTypes,
  isNullValue,
  isRenderableChartType,
  normalizeChartSpec,
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

  const candidates = useMemo(() => getCompatibleChartTypes(chart), [chart]);

  const [localType, setLocalType] = useState<RenderableChartType>(() => {
    if (chart.explicitType && isRenderableChartType(chart.spec.type)) return chart.spec.type;
    // 自动选择时跳过 combo（combo 仅用户显式操作可用）
    const auto = candidates.find(c => c !== 'combo');
    return auto ?? 'bar';
  });
  const echartsRef = useRef<ReactECharts>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // 跟踪上次 dataVersion 和 spec.type，用于判断数据/推荐类型是否真正变化
  const prevDataVersionRef = useRef(chart.dataVersion);
  const prevSpecTypeRef = useRef(chart.spec.type);

  useEffect(() => {
    const dataChanged = prevDataVersionRef.current !== chart.dataVersion;
    const specChanged = prevSpecTypeRef.current !== chart.spec.type;

    prevDataVersionRef.current = chart.dataVersion;
    prevSpecTypeRef.current = chart.spec.type;

    if (dataChanged || specChanged) {
      if (chart.explicitType && isRenderableChartType(chart.spec.type)) {
        setLocalType(chart.spec.type);
      } else if (candidates.length > 0) {
        const recommended = isRenderableChartType(chart.spec.type) ? chart.spec.type : null;
        // combo 不参与自动推荐（仅用户显式操作可用）
        const first = recommended && recommended !== 'combo' && candidates.includes(recommended)
          ? recommended
          : candidates.find(c => c !== 'combo');
        if (first) setLocalType(first);
      }
    }
  }, [chart.dataVersion, chart.spec.type, chart.explicitType, candidates]);

  // 首次初始化完成后 resize，解决 container 宽度未定导致图例错位的问题
  const handleChartReady = () => {
    const instance = echartsRef.current?.getEchartsInstance();
    if (instance) {
      requestAnimationFrame(() => instance.resize());
    }
  };

  const chartTypes = useMemo(() => {
    const types = candidates.map(type => ({ key: type, label: CHART_TYPE_LABELS[type] }));
    if (chart.explicitType && isRenderableChartType(chart.spec.type) && !candidates.includes(chart.spec.type)) {
      types.unshift({ key: chart.spec.type, label: CHART_TYPE_LABELS[chart.spec.type] });
    }
    return types;
  }, [candidates, chart.explicitType, chart.spec.type]);

  const handleTypeChange = (type: RenderableChartType) => {
    // 切换前清空画布，避免旧图表类型配置残留（双保险第一层）
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

  const option = useMemo(() => {
    // chartOnly 模式：固定使用 spec.type，忽略 localType
    const type = isChartOnly && isRenderableChartType(chart.spec.type) ? chart.spec.type : localType;
    const opt = buildChartOption({ ...chart, spec: normalizeChartSpec(chart.spec, type) });
    if (opt && hideTitle) {
      // 删除 ECharts 内部标题，由外部卡片显示
      const stripped = { ...opt };
      delete (stripped as Record<string, unknown>).title;
      return stripped;
    }
    return opt;
  }, [chart, localType, hideTitle, isChartOnly]);

  // 图表说明（基于当前实际渲染类型，与 option useMemo 一致）
  const effectiveType: RenderableChartType =
    isChartOnly && isRenderableChartType(chart.spec.type) ? chart.spec.type : localType;

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
      {/* 工具栏：仅普通查询模式显示，追加图表模式隐藏 */}
      {!isChartOnly && (
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        {/* 一级：图表 / 表格  — hideTableToggle 时隐藏 */}
        {!hideTableToggle && (
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

        {/* 二级：图表类型（仅图表模式下显示，包括 hideTableToggle 时） */}
        {effectiveViewMode === 'chart' && (
          <div style={{ display: 'flex', gap: 4, backgroundColor: '#f3f4f6', borderRadius: 6, padding: 3 }}>
            {chartTypes.map(t => (
              <button
                key={t.key}
                onClick={() => handleTypeChange(t.key)}
                style={{
                  padding: '4px 12px',
                  border: 'none',
                  borderRadius: 4,
                  cursor: 'pointer',
                  fontSize: 12,
                  backgroundColor: localType === t.key ? '#2563eb' : 'transparent',
                  color: localType === t.key ? '#fff' : '#374151',
                  fontWeight: localType === t.key ? 500 : 400,
                  transition: 'all .15s',
                }}
              >
                {t.label}
              </button>
            ))}
          </div>
        )}
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
