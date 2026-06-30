import { Component } from 'react';
import type { ReactNode } from 'react';
import type { ChartData, RenderableChartType } from '../types';
import { ChartView } from './ChartView';

// ---- Error Boundary ----

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback: ReactNode;
  /** 变化时重置错误状态，由 chart.id + error + rows.length 组成 */
  resetKey: string;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

export class ChartErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidUpdate(prevProps: ErrorBoundaryProps) {
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false });
    }
  }

  render() {
    if (this.state.hasError) return this.props.fallback;
    return this.props.children;
  }
}

// ---- ChartCard ----

interface ChartCardProps {
  chart: ChartData;
  index: number;
  isSingle: boolean;
  onChangeType?: (type: RenderableChartType) => void;
}

export function ChartCard({ chart, index, isSingle, onChangeType }: ChartCardProps) {
  const showError =
    !!chart.error ||
    (!chart.columns.length && !chart.rows.length && chart.spec.type === 'none');

  const errorMessage = chart.error || '图表数据不可用';

  // 反映 spec、error 和数据版本的变化，用于 Error Boundary 恢复
  const resetKey = `${chart.id}|${chart.error ?? ''}|${JSON.stringify(chart.spec)}|${chart.columns.join(',')}|${chart.dataVersion}`;

  return (
    <div
      style={{
        border: '1px solid #e5e7eb',
        borderRadius: 10,
        backgroundColor: '#fff',
        overflow: 'hidden',
        boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
      }}
    >
      {/* 外置标题 */}
      <div
        style={{
          padding: '10px 16px',
          borderBottom: '1px solid #f3f4f6',
          fontWeight: 500,
          fontSize: 14,
          color: '#374151',
          backgroundColor: '#fafafa',
        }}
      >
        {chart.title || `图表 ${index + 1}`}
      </div>

      {/* 内容 */}
      <div style={{ padding: showError ? '16px' : '12px' }}>
        {showError ? (
          <div
            style={{
              color: '#ef4444',
              fontSize: 13,
              padding: '12px',
              backgroundColor: '#fef2f2',
              borderRadius: 6,
              border: '1px solid #fecaca',
            }}
          >
            <div style={{ fontWeight: 500, marginBottom: 4 }}>图表错误</div>
            <div style={{ color: '#6b7280', fontSize: 12 }}>{errorMessage}</div>
          </div>
        ) : (
          <ChartErrorBoundary
            resetKey={resetKey}
            fallback={
              <div
                style={{
                  color: '#ef4444',
                  fontSize: 13,
                  padding: '12px',
                  backgroundColor: '#fef2f2',
                  borderRadius: 6,
                  border: '1px solid #fecaca',
                }}
              >
                图表渲染失败
              </div>
            }
          >
            <ChartView
              chart={chart}
              hideTitle={!isSingle}
              onChangeType={onChangeType}
            />
          </ChartErrorBoundary>
        )}
      </div>
    </div>
  );
}
