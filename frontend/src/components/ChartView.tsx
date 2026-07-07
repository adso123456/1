import { useState, useMemo, useRef, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import type { ChartData, ChartSpec, ChartTypeAvailability, RenderableChartType } from '../types';
import {
  buildChartOption,
  CHART_TYPE_LABELS,
  isNullValue,
  isRenderableChartType,
} from '../chartRegistry';
import { generateChartDescription } from '../chartDescription';
import { formatCellValue, formatColumnLabel } from '../utils/tableFormatting';
import {
  getChartTypeAvailabilityV2,
  prepareChartV2All,
} from '../chartPipelineV2';

interface Props {
  chart: ChartData;
  /** 多图模式下隐藏 ECharts 内部标题（由外部卡片标题代替） */
  hideTitle?: boolean;
  onChangeType?: (type: RenderableChartType) => void;
  /** 仪表板用：切换类型时回传 availability 返回的完整 ChartSpec，供上层持久化。
   *  聊天页不传，仅靠 onChangeType 通知类型变化。 */
  onChangeSpec?: (spec: ChartSpec) => void;
  /** 当前 chart 所属消息 ID（V2 切换时需要定位消息） */
  messageId?: string;
  /** 当前 chart 在 message.charts 中的索引（V2 切换时需要定位 chart） */
  chartIndex?: number;
  /** V2 图表切换：基于 sourceColumns/sourceRows 重新执行 V2 plan+transform，
   *  返回完整 ChartData。未传时 fallback 到旧 onChangeType/onChangeSpec 路径。
   *  messageId + chartIndex 用于在消息列表中定位并替换目标 chart。 */
  onV2ChartSwitch?: (messageId: string, chartIndex: number, newChart: ChartData) => void;
  /** 隐藏"图表/表格"切换，始终显示图表（仪表板和弹窗预览用） */
  hideTableToggle?: boolean;
  /** 隐藏图表下方自动生成的文字说明（仪表板精简用） */
  hideDescription?: boolean;
  /** 撑满父容器高度（仪表板缩放用），设置后 ECharts 不再使用固定 350px */
  fillHeight?: boolean;
  /** 显示"导出"按钮（聊天与仪表板图表用，弹窗预览不显示） */
  showExport?: boolean;
  /** 点击"添加到仪表板"时回调，传出当前 activeSpec 图表快照（含 explicitType=true） */
  onAddToDashboard?: (chart: ChartData) => void;
}

type ViewMode = 'chart' | 'table';

export function ChartView({ chart, hideTitle, onChangeType, onChangeSpec, onV2ChartSwitch, messageId, chartIndex, hideTableToggle, hideDescription, fillHeight, showExport, onAddToDashboard }: Props) {
  const isChartOnly = !!chart.chartOnly;

  const [viewMode, setViewMode] = useState<ViewMode>('chart');

  // hideTableToggle 时始终按图表模式处理
  const effectiveViewMode: ViewMode = hideTableToggle ? 'chart' : viewMode;

  const echartsRef = useRef<ReactECharts>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // ECharts 实际渲染 DOM 的 ResizeObserver（fillHeight 模式，替代 echarts-for-react 内置 autoResize）
  const echartsDomObserverRef = useRef<ResizeObserver | null>(null);
  const echartsRafRef = useRef<number | null>(null);

  /** 清理 ECharts DOM observer 与未执行的 animation frame */
  const cleanupEchartsObserver = () => {
    if (echartsDomObserverRef.current) {
      echartsDomObserverRef.current.disconnect();
      echartsDomObserverRef.current = null;
    }
    if (echartsRafRef.current !== null) {
      cancelAnimationFrame(echartsRafRef.current);
      echartsRafRef.current = null;
    }
  };

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

  /** 全部 13 种图表类型的可用性评估（含完整 Spec）。
   *   V2 图表有 source 数据时走 V2 planning；旧图表无 source 数据时自动 fallback 到旧逻辑。 */
  const allTypes = useMemo<ChartTypeAvailability[]>(
    () => getChartTypeAvailabilityV2(chart),
    [chart],
  );

  /** 从 allTypes 中按优先级选取默认类型 */
  function pickDefault(): RenderableChartType {
    // 1. explicitType 且当前类型 supported → 保留用户选择
    if (chart.explicitType) {
      const current = allTypes.find(t => t.type === chart.spec.type && t.supported);
      if (current) return current.type;
    }

    // 2. 模型指定类型仅在 suitability=recommended 时作为默认
    const modelType = chart.spec.type;
    if (isRenderableChartType(modelType)) {
      const modelRec = allTypes.find(t => t.type === modelType && t.supported && t.suitability === 'recommended');
      if (modelRec) return modelRec.type;
    }

    // 3. 第一个 supported 且 recommended 的类型
    const firstRecommended = allTypes.find(t => t.supported && t.suitability === 'recommended');
    if (firstRecommended) return firstRecommended.type;

    // 4. 没有 recommended → 模型指定且 supported 的类型
    if (isRenderableChartType(modelType)) {
      const modelSupported = allTypes.find(t => t.type === modelType && t.supported);
      if (modelSupported) return modelSupported.type;
    }

    // 5. 第一个 supported 类型
    const firstSupported = allTypes.find(t => t.supported);
    if (firstSupported) return firstSupported.type;

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

  /** 当前类型对应的完整渲染 Spec。
   *  优先保留 V2 Planner 产出的原始 spec（explicitType=true 且类型匹配时），
   *  避免被旧 getChartTypeAvailability() 推导的 spec 覆盖。
   *  用户手动切换后 localType 与原始 spec.type 不同，仍走旧 availability spec。 */
  const activeSpec = useMemo<ChartSpec | null>(() => {
    // V2 auto 输出的 spec 优先保留，不被旧 availability 推导覆盖
    if (chart.explicitType === true && chart.spec?.type === localType) {
      return chart.spec;
    }
    const item = allTypes.find(t => t.type === localType);
    return item?.spec ?? null;
  }, [allTypes, localType, chart.explicitType, chart.spec]);

  // 图表实例就绪后：初始 resize + fillHeight 模式下建立 ECharts 实际 DOM 的 ResizeObserver
  const handleChartReady = () => {
    const instance = echartsRef.current?.getEchartsInstance();
    if (!instance) return;

    // 清理上一次的 observer（切换图表类型时实例可能重建）
    cleanupEchartsObserver();

    // 初始 resize：覆盖 Dashboard 初始 containerWidth=600 → GridLayout 重新计算 → flex 稳定的全时序
    requestAnimationFrame(() => {
      if (instance && !instance.isDisposed()) {
        instance.resize({ width: 'auto', height: 'auto' });
      }
    });

    // 非 fillHeight 模式不需要自定义 observer（由 echarts-for-react 内置 autoResize 处理）
    if (!fillHeight) return;

    const dom = instance.getDom();
    if (!dom) return;

    const observer = new ResizeObserver(() => {
      if (echartsRafRef.current !== null) return; // 合并同一帧内的多次回调
      echartsRafRef.current = requestAnimationFrame(() => {
        echartsRafRef.current = null;
        const inst = echartsRef.current?.getEchartsInstance();
        if (inst && !inst.isDisposed()) {
          inst.resize({ width: 'auto', height: 'auto' });
        }
      });
    });

    observer.observe(dom);
    echartsDomObserverRef.current = observer;
  };

  const handleTypeChange = (type: RenderableChartType) => {
    // 仅在目标类型可用（supported 且 spec 非空）时才切换，避免切到不支持的类型
    const target = allTypes.find(t => t.type === type);
    if (!target?.supported || !target.spec) return;

    // 切换前清空画布，避免旧图表类型配置残留
    const instance = echartsRef.current?.getEchartsInstance();
    if (instance) {
      instance.clear();
    }

    // ── V2 路径：chart 有 source 数据时，基于 source 重新 plan+transform ──
    if (chart.sourceColumns && chart.sourceRows) {
      const result = prepareChartV2All({
        columns: chart.sourceColumns,
        rows: chart.sourceRows,
        source: 'user',
        intent: 'auto',
        requestedChartType: type,
        id: chart.id || '',
        title: chart.title || '',
        dataVersion: chart.dataVersion ?? 0,
      });

      if (result.ok && result.chart) {
        // V2 切换成功 → 优先走 onV2ChartSwitch 回调
        const newChart: ChartData = {
          ...result.chart,
          // 保留原 chart 的元数据字段
          chartOnly: chart.chartOnly,
          dataVersion: chart.dataVersion,
        };
        setLocalType(type);
        if (onV2ChartSwitch && messageId !== undefined && chartIndex !== undefined) {
          onV2ChartSwitch(messageId, chartIndex, newChart);
        } else {
          // 未传 onV2ChartSwitch 或缺少定位信息 → fallback 到旧 onChangeType/onChangeSpec
          onChangeType?.(type);
          onChangeSpec?.(newChart.spec);
        }
        return;
      }

      // V2 失败 → 不切换，仅提示（避免 spec 切换但 columns/rows/v2Meta 不一致）
      showToast('该数据类型暂不支持该图表');
      return;
    }

    // ── 旧路径：无 source 数据 → 完全走旧逻辑 ──
    setLocalType(type);
    onChangeType?.(type);
    // 回传 availability 返回的完整 Spec（含 xField/yFields/sizeField/valueField 等），供仪表板持久化
    onChangeSpec?.(target.spec);
  };

  /** 清理文件名中的 Windows 非法字符 */
  const sanitizeFileName = (raw: string): string =>
    raw.replace(/[\\/:*?"<>|]/g, '_').replace(/\s+/g, ' ').trim() || 'chart';

  /** 导出当前 activeSpec 对应图表为 PNG */
  const handleExport = () => {
    const instance = echartsRef.current?.getEchartsInstance();
    if (!instance) {
      showToast('图表尚未就绪，请稍后再试');
      return;
    }
    try {
      const dataURL = instance.getDataURL({
        type: 'png',
        pixelRatio: 2,
        backgroundColor: '#fff',
      });
      const title = sanitizeFileName(chart.title || '');
      const typeLabel = CHART_TYPE_LABELS[localType] || localType;
      const fileName = `${title}_${typeLabel}.png`;
      const link = document.createElement('a');
      link.href = dataURL;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch {
      showToast('导出失败，请稍后再试');
    }
  };

  /** 把当前 activeSpec 图表快照交给上层添加到仪表板 */
  const handleAddToDashboard = () => {
    if (!onAddToDashboard) return;
    if (!activeSpec) {
      showToast('当前图表类型不可用');
      return;
    }
    // 使用当前实际渲染的 activeSpec，而非原始 chart.spec；保留 columns/rows/title/dataVersion
    onAddToDashboard({ ...chart, spec: activeSpec, explicitType: true });
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

  // 组件卸载时清理 ECharts DOM observer
  useEffect(() => {
    return () => cleanupEchartsObserver();
  }, []);

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

  // 图表说明（基于当前实际渲染类型与 Spec，与 ECharts 渲染使用同一份 activeSpec）
  const effectiveType = localType;

  const description = useMemo(() => {
    if (!activeSpec) return null;
    return generateChartDescription({ ...chart, spec: activeSpec }, effectiveType);
  }, [chart, activeSpec, effectiveType]);

  // 表格渲染
  const cleanRows = chart.rows.filter(
    r => !isNullValue(r[chart.columns[0]])
  );

  return (
    <div
      ref={containerRef}
      style={{
        ...(fillHeight ? { height: '100%', minHeight: 0, width: '100%', minWidth: 0, display: 'flex', flexDirection: 'column' } : {}),
        marginTop: isChartOnly ? 0 : (fillHeight ? 0 : 12),
      }}
    >
      {/* 工具栏 */}
      {/* 普通模式：「图表/表格」切换始终显示，类型下拉仅在图表模式显示 */}
      {/* chartOnly：只显示类型下拉；hideTableToggle：只隐藏「图表/表格」切换 */}
      {(!isChartOnly && !hideTableToggle || effectiveViewMode === 'chart' || isChartOnly) && (
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

        {/* 二级：图表类型下拉菜单（仅图表模式显示；表格模式下隐藏） */}
        {(effectiveViewMode === 'chart' || isChartOnly) && (
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

          {/* Toast 提示：独立于下拉开关，导出失败/图表未就绪/添加失败等均能显示；data-export-exclude 避免进入整板 PNG */}
          {toastMessage && (
            <div data-export-exclude style={{
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

          {/* 下拉面板 */}
          {dropdownOpen && (
            <>
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
        )}

        {/* 三级：导出 / 添加到仪表板（仅图表模式显示；data-export-exclude 确保整板 PNG 不含按钮） */}
        {(showExport || onAddToDashboard) && (effectiveViewMode === 'chart' || isChartOnly) && (
          <div data-export-exclude style={{ display: 'flex', gap: 6, marginLeft: 'auto', alignItems: 'center' }}>
            {showExport && (
              <button
                onClick={handleExport}
                title="导出当前图表为 PNG"
                style={{
                  padding: '4px 10px',
                  border: '1px solid #d1d5db',
                  borderRadius: 6,
                  cursor: 'pointer',
                  fontSize: 12,
                  backgroundColor: '#fff',
                  color: '#374151',
                  transition: 'all .15s',
                }}
              >
                导出
              </button>
            )}
            {onAddToDashboard && (
              <button
                onClick={handleAddToDashboard}
                title="添加到仪表板"
                style={{
                  padding: '4px 10px',
                  border: '1px solid #d1d5db',
                  borderRadius: 6,
                  cursor: 'pointer',
                  fontSize: 12,
                  backgroundColor: '#fff',
                  color: '#374151',
                  transition: 'all .15s',
                }}
              >
                添加到仪表板
              </button>
            )}
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
          autoResize={!fillHeight}
          onChartReady={handleChartReady}
          style={fillHeight ? { flex: 1, minHeight: 0, minWidth: 0, width: '100%' } : { height: 350 }}
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
