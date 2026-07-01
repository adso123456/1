import { useState, useCallback, useMemo } from 'react';
import type { DashboardItem, DashboardMeta, DashboardChartItem } from '../types';
import type { Layout } from 'react-grid-layout';

/** v2 localStorage key —— 多仪表板版本化存储 */
const STORE_KEY = 'water_qa_dashboards_v2';
/** 旧版单仪表板 key（仅迁移读取，不删除） */
const OLD_KEY = 'water_qa_dashboard';

/** 网格常量 */
const COLS = 6;
const DEFAULT_W = 3;
const DEFAULT_CHART_H = 4;
const DEFAULT_TABLE_H = 3;

/** 内部存储结构 */
interface StoreData {
  version: 2;
  currentDashboardId: string;
  dashboards: DashboardMeta[];
}

/* ================================================================
   工具函数
   ================================================================ */

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/** 为缺失 layout 的项目生成空闲网格位置 */
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
      const maxY = items.reduce((m, i) => Math.max(m, (i.layout?.y ?? 0) + (i.layout?.h ?? 0)), 0);
      item.layout = { x: 0, y: maxY, w, h };
    }
  }
}

/** 读取旧版单仪表板数据（仅用于迁移） */
function loadOldDashboard(): DashboardItem[] | null {
  try {
    const raw = localStorage.getItem(OLD_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    return parsed
      .map((item: Record<string, unknown>) => {
        if (item.type === 'chart' || item.type === 'table') {
          return item as unknown as DashboardItem;
        }
        if (item.chart && typeof item.chart === 'object') {
          return { ...item, type: 'chart' } as unknown as DashboardChartItem;
        }
        return null;
      })
      .filter((item): item is DashboardItem => item !== null);
  } catch {
    return null;
  }
}

/* ================================================================
   存储读写
   ================================================================ */

function loadStore(): StoreData {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && parsed.version === 2 && Array.isArray(parsed.dashboards)) {
        return parsed as StoreData;
      }
    }
  } catch {
    // 解析失败，走迁移/初始化逻辑
  }

  // 迁移旧数据：只要旧 key 存在（含空数组[]），就包装成"默认仪表板"
  const oldItems = loadOldDashboard();
  if (oldItems !== null) {
    generateLayout(oldItems);
    const dashboard: DashboardMeta = {
      id: generateId(),
      name: '默认仪表板',
      createdAt: Date.now(),
      updatedAt: Date.now(),
      items: oldItems,
    };
    const store: StoreData = {
      version: 2,
      currentDashboardId: dashboard.id,
      dashboards: [dashboard],
    };
    // 立即持久化到新 key，防止刷新后重复迁移、重新生成 ID
    saveStore(store);
    return store;
  }

  // 无任何旧数据：创建空"新建仪表板"，立即持久化
  const newDashboard: DashboardMeta = {
    id: generateId(),
    name: '新建仪表板',
    createdAt: Date.now(),
    updatedAt: Date.now(),
    items: [],
  };
  const initialStore: StoreData = {
    version: 2,
    currentDashboardId: newDashboard.id,
    dashboards: [newDashboard],
  };
  saveStore(initialStore);
  return initialStore;
}

function saveStore(data: StoreData): boolean {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(data));
    return true;
  } catch {
    return false;
  }
}

/** 生成不重名的仪表板名称 */
function generateDashboardName(dashboards: DashboardMeta[]): string {
  const existingNames = new Set(dashboards.map(d => d.name));
  let name = '新建仪表板';
  if (!existingNames.has(name)) return name;
  for (let i = 2; ; i++) {
    name = `新建仪表板 ${i}`;
    if (!existingNames.has(name)) return name;
  }
}

/* ================================================================
   Hook
   ================================================================ */

export function useDashboard() {
  const [store, setStore] = useState<StoreData>(() => loadStore());

  const dashboards = store.dashboards;
  const currentDashboardId = store.currentDashboardId;

  const currentDashboard = useMemo(
    () => dashboards.find(d => d.id === currentDashboardId) ?? dashboards[0] ?? null,
    [dashboards, currentDashboardId],
  );

  const currentItems = currentDashboard?.items ?? [];
  const currentDashboardName = currentDashboard?.name ?? '仪表板';

  /** 持久化并更新 React 状态 */
  const persist = useCallback((newStore: StoreData): boolean => {
    if (!saveStore(newStore)) return false;
    setStore(newStore);
    return true;
  }, []);

  /** 重新读取最新存储（用于回调中获取最新数据） */
  const reload = useCallback((): StoreData => loadStore(), []);

  /** 添加图表/表格到当前仪表板 */
  const addItems = useCallback((newItems: DashboardItem[]): boolean => {
    const current = reload();
    const db = current.dashboards.find(d => d.id === current.currentDashboardId);
    if (!db) return false;

    for (const item of newItems) {
      const idx = db.items.findIndex(c => c.id === item.id);
      if (idx >= 0) {
        db.items[idx] = item;
      } else {
        db.items.push(item);
      }
    }

    generateLayout(db.items);
    db.updatedAt = Date.now();
    return persist(current);
  }, [persist, reload]);

  /** 从当前仪表板移除卡片 */
  const removeItem = useCallback((id: string): boolean => {
    const current = reload();
    const db = current.dashboards.find(d => d.id === current.currentDashboardId);
    if (!db) return false;

    const filtered = db.items.filter(c => c.id !== id);
    if (filtered.length === db.items.length) return true; // 没找到，不算失败

    db.items = filtered;
    db.updatedAt = Date.now();
    return persist(current);
  }, [persist, reload]);

  /** 拖拽/缩放后持久化布局 */
  const updateLayout = useCallback((layout: Layout): boolean => {
    const current = reload();
    const db = current.dashboards.find(d => d.id === current.currentDashboardId);
    if (!db) return false;

    const idToLayout = new Map(layout.map(l => [l.i, l]));
    for (const item of db.items) {
      const ly = idToLayout.get(item.id);
      if (ly) {
        item.layout = { x: ly.x, y: ly.y, w: ly.w, h: ly.h };
      }
    }

    db.updatedAt = Date.now();
    return persist(current);
  }, [persist, reload]);

  /** 新建仪表板并立即切换 */
  const createDashboard = useCallback((): boolean => {
    const current = reload();
    const name = generateDashboardName(current.dashboards);
    const newDashboard: DashboardMeta = {
      id: generateId(),
      name,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      items: [],
    };
    current.dashboards = [...current.dashboards, newDashboard];
    current.currentDashboardId = newDashboard.id;
    return persist(current);
  }, [persist, reload]);

  /** 切换到指定仪表板 */
  const switchDashboard = useCallback((id: string): boolean => {
    const current = reload();
    if (!current.dashboards.find(d => d.id === id)) return false;
    current.currentDashboardId = id;
    return persist(current);
  }, [persist, reload]);

  return {
    dashboards,
    currentDashboardId,
    currentDashboardName,
    currentItems,
    addItems,
    removeItem,
    updateLayout,
    createDashboard,
    switchDashboard,
  };
}
