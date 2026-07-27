import type {
  ChartData,
  ChartTypeAvailability,
} from './types';
import { buildChartOption } from './chartRegistry';
import { getCompactChartStrategy } from './compactChartLayout';

export type CompactChartTypeAvailability = ChartTypeAvailability & {
  dataSupported: boolean;
  compactSupported: boolean;
  selectable: boolean;
  menuReason: string;
};

/**
 * 将通用数据能力与浮窗展示能力组合成菜单唯一使用的可用性结果。
 * 通用 Planner 仍只负责判断数据是否支持，不感知 compact 限制。
 */
export function getCompactChartTypeAvailability(
  chart: ChartData,
  dataAvailability: ChartTypeAvailability[],
  compact: boolean,
  width: number,
): CompactChartTypeAvailability[] {
  return dataAvailability.map(item => {
    const dataSupported = item.supported && item.spec !== null;
    if (!dataSupported) {
      const menuReason = item.reason || '该数据类型暂不支持该图表';
      return {
        ...item,
        dataSupported: false,
        compactSupported: false,
        selectable: false,
        menuReason,
      };
    }

    if (!compact) {
      return {
        ...item,
        dataSupported: true,
        compactSupported: true,
        selectable: true,
        menuReason: '',
      };
    }

    const option = buildChartOption({
      ...chart,
      spec: item.spec!,
      explicitType: true,
    });
    const strategy = option
      ? getCompactChartStrategy(option, {
          width,
          chartType: item.type,
        })
      : null;
    const compactSupported = strategy?.compactAvailable ?? false;
    const menuReason = compactSupported
      ? ''
      : strategy?.compactUnavailableReason
        || '该图表不适合当前浮窗';

    return {
      ...item,
      dataSupported: true,
      compactSupported,
      selectable: compactSupported,
      menuReason,
    };
  });
}
