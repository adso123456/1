import { useState, useCallback } from 'react';
import type { DashboardItem, DashboardChartItem, DashboardLayoutInfo } from '../types';
import type { Layout } from 'react-grid-layout';

const DASHBOARD_KEY = 'water_qa_dashboard';

/** 网格常量 */
const COLS = 6;
const DEFAULT_W = 3; // 默认半宽
const DEFAULT_CHART_H = 4;
const DEFAULT_TABLE_H = 3;

/** 为新增项目生成空闲布局（向下搜索第一个未被占用的位置） */
function generateLayout(items: DashboardItem[]): void {
  const occupied = new Set<string>();
  for (const item of items) {
    if (item.layout) {
      for (let dx = 0; dx < item.layout.w; dx++) {
        for (let dy = 0; dy < item.layout.h; dy++) {
          occupied.add(`${item.layout.x + dx},${item.layout.y + dy}`);
        }
      }
    }
  }

  for (const item of items) {
    if (item.layout) continue;

    const w = DEFAULT_W;
    const h = item.type === 'chart' ? DEFAULT_CHART_H : DEFAULT_TABLE_H;

    // 从上到下、从左到右搜索空闲位置
    let found = false;
    for (let y = 0; y < 100 && !found; y++) {
      for (let x = 0; x <= COLS - w && !found; x++) {
        let fits = true;
        for (let dx = 0; dx < w && fits; dx++) {
          for (let dy = 0; dy < h && fits; dy++) {
            if (occupied.has(`${x + dx},${y + dy}`)) fits = false;
          }
        }
        if (fits) {
          item.layout = { x, y, w, h };
          for (let dx = 0; dx < w; dx++) {
            for (let dy = 0; dy < h; dy++) {
              occupied.add(`${x + dx},${y + dy}`);
            }
          }
          found = true;
        }
      }
    }
    if (!found) {
      // 极端情况：所有位置都满了，塞到最底部
      const maxY = items.reduce((m, i) => Math.max(m, (i.layout?.y ?? 0) + (i.layout?.h ?? 0)), 0);
      item.layout = { x: 0, y: maxY, w, h };
    }
  }
}

function loadDashboard(): DashboardItem[] {
  try {
    const raw = localStorage.getItem(DASHBOARD_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((item: Record<string, unknown>) => {
        if (item.type === 'chart' || item.type === 'table') {
          return item as DashboardItem;
        }
        if (item.chart && typeof item.chart === 'object') {
          return { ...item, type: 'chart' } as DashboardChartItem;
        }
        return null;
      })
      .filter((item): item is DashboardItem => item !== null);
  } catch {
    return [];
  }
}

function saveDashboard(items: DashboardItem[]): boolean {
  try {
    localStorage.setItem(DASHBOARD_KEY, JSON.stringify(items));
    return true;
  } catch {
    return false;
  }
}

export function useDashboard() {
  const [items, setItems] = useState<DashboardItem[]>(() => {
    const loaded = loadDashboard();
    // 旧数据迁移：为缺失 layout 的项目生成布局
    generateLayout(loaded);
    return loaded;
  });

  const addItems = useCallback((newItems: DashboardItem[]): boolean => {
    const existing = loadDashboard();
    const existingIds = new Set(existing.map(c => c.id));

    const merged: DashboardItem[] = [...existing];
    for (const item of newItems) {
      const idx = merged.findIndex(c => c.id === item.id);
      if (idx >= 0) {
        merged[idx] = item;
      } else {
        merged.push(item);
      }
    }

    // 为新项目生成布局
    generateLayout(merged);

    if (!saveDashboard(merged)) return false;
    setItems(merged);
    return true;
  }, []);

  const removeItem = useCallback((id: string): boolean => {
    const existing = loadDashboard();
    const filtered = existing.filter(c => c.id !== id);

    if (filtered.length === existing.length) return true;

    if (!saveDashboard(filtered)) return false;
    setItems(filtered);
    return true;
  }, []);

  /** 拖拽/缩放后持久化布局 */
  const updateLayout = useCallback((layout: Layout): boolean => {
    const existing = loadDashboard();
    const idToLayout = new Map(layout.map(l => [l.i, l]));

    for (const item of existing) {
      const ly = idToLayout.get(item.id);
      if (ly) {
        item.layout = { x: ly.x, y: ly.y, w: ly.w, h: ly.h };
      }
    }

    if (!saveDashboard(existing)) return false;
    setItems(existing);
    return true;
  }, []);

  return { dashboardItems: items, addItems, removeItem, updateLayout };
}
