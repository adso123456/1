import { useState, useCallback, useMemo } from 'react';
import type { DashboardItem, DashboardMeta, DashboardChartItem, DashboardLayoutInfo, ChartSpec } from '../types';
import type { Layout } from 'react-grid-layout';

/** v3 localStorage key —— 细粒度纵向网格（rowHeight=10, margin=6） */
const STORE_KEY = 'water_qa_dashboards_v3';
/** v2 key（仅迁移读取，不删除）—— 旧粗粒度网格（rowHeight=100, margin=16） */
const V2_KEY = 'water_qa_dashboards_v2';
/** 旧版单仪表板 key（仅迁移读取，不删除） */
const OLD_KEY = 'water_qa_dashboard';

/** 网格常量（细粒度纵向网格） */
const COLS = 6;
const DEFAULT_W = 3;
/** 图表默认高度 ≈ 原 448px：ceil((448+6)/16)=29 */
const DEFAULT_CHART_H = 29;
/** 新表格临时默认高度 ≈ 原 216px：ceil((216+6)/16)=14，挂载后由 TableView 实测内容高度自动校正 */
const DEFAULT_TABLE_H = 14;

/** 内部存储结构 */
interface StoreData {
  version: 3;
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

/** v2→v3 网格坐标迁移：旧网格 rowHeight=100/margin=16（单元=116px），
 *  新网格 rowHeight=10/margin=6（单元=16px）。仅改 y 和 h，保留 x、w。 */
function migrateLayoutV2toV3(layout: DashboardLayoutInfo): DashboardLayoutInfo {
  // 旧像素顶部位置 = oldY * 116
  const oldPixelTop = layout.y * 116;
  const newY = Math.round(oldPixelTop / 16);
  // 旧像素高度 = oldH * 100 + (oldH - 1) * 16 = oldH * 116 - 16
  const oldPixelHeight = layout.h * 116 - 16;
  const newH = Math.ceil((oldPixelHeight + 6) / 16);
  return {
    x: layout.x,
    y: newY,
    w: layout.w,
    h: newH > 0 ? newH : 1,
  };
}

/** 读取 v2 存储并迁移到 v3（仅迁移读取，不删除 v2 key） */
function loadV2Store(): StoreData | null {
  try {
    const raw = localStorage.getItem(V2_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.version !== 2 || !Array.isArray(parsed.dashboards)) return null;
    // 迁移每个仪表板的每个项目 layout
    for (const db of parsed.dashboards) {
      for (const item of db.items) {
        if (item.layout) {
          item.layout = migrateLayoutV2toV3(item.layout);
        }
      }
    }
    parsed.version = 3;
    return parsed as StoreData;
  } catch {
    return null;
  }
}

function loadStore(): StoreData {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && parsed.version === 3 && Array.isArray(parsed.dashboards)) {
        return parsed as StoreData;
      }
    }
  } catch {
    // 解析失败，走迁移/初始化逻辑
  }

  // 迁移 v2 数据（只迁移一次：写入 v3 key 后下次直接读 v3）
  const migrated = loadV2Store();
  if (migrated) {
    saveStore(migrated);
    return migrated;
  }

  // 迁移旧数据：只要旧 key 存在（含空数组[]），就包装成"默认仪表板"
  const oldItems = loadOldDashboard();
  if (oldItems !== null) {
    // 旧单仪表板项目若带 layout（旧粗粒度网格），同样迁移到 v3 网格
    for (const item of oldItems) {
      if (item.layout) {
        item.layout = migrateLayoutV2toV3(item.layout);
      }
    }
    generateLayout(oldItems);
    const dashboard: DashboardMeta = {
      id: generateId(),
      name: '默认仪表板',
      createdAt: Date.now(),
      updatedAt: Date.now(),
      items: oldItems,
    };
    const store: StoreData = {
      version: 3,
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
    version: 3,
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

  /** 单项高度校正：仅改当前仪表板指定项目的 layout.h，保留 x/y/w。
   *  新高度与旧高度相同时不写 localStorage；写入失败返回 false。 */
  const updateItemHeight = useCallback((id: string, h: number): boolean => {
    if (!Number.isFinite(h) || h <= 0) return false;
    const current = reload();
    const db = current.dashboards.find(d => d.id === current.currentDashboardId);
    if (!db) return false;

    const item = db.items.find(c => c.id === id);
    if (!item) return false;

    const gridH = Math.round(h);
    if (item.layout && item.layout.h === gridH) return true; // 高度未变，不写存储

    item.layout = {
      x: item.layout?.x ?? 0,
      y: item.layout?.y ?? 0,
      w: item.layout?.w ?? DEFAULT_W,
      h: gridH,
    };
    db.updatedAt = Date.now();
    return persist(current);
  }, [persist, reload]);

  /** 更新当前仪表板中指定图表项目的 spec（仪表板内切换图表类型后持久化）。
   *  仅写 chart.spec 与 explicitType，保留 columns/rows/title/dataVersion 及 item 的 layout/sourceSql/addedAt/lastRefreshedAt。
   *  原 spec 与新 spec 完全一致且 explicitType 已为 true 时跳过写入。 */
  const updateChartSpec = useCallback((id: string, spec: ChartSpec): boolean => {
    const current = reload();
    const db = current.dashboards.find(d => d.id === current.currentDashboardId);
    if (!db) return false;

    const item = db.items.find(c => c.id === id);
    if (!item || item.type !== 'chart') return false;

    // 去重：spec 完全相同且已显式标记，避免重复写 localStorage
    if (JSON.stringify(item.chart.spec) === JSON.stringify(spec) && item.chart.explicitType === true) {
      return true;
    }

    item.chart.spec = spec;
    item.chart.explicitType = true;
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

  /** 向指定仪表板添加项目（upsert）：更新已有卡片时保留原 layout，新卡片生成 layout */
  const addItemsToDashboard = useCallback((dashboardId: string, newItems: DashboardItem[]): boolean => {
    const current = reload();
    const db = current.dashboards.find(d => d.id === dashboardId);
    if (!db) return false;

    for (const item of newItems) {
      const idx = db.items.findIndex(c => c.id === item.id);
      if (idx >= 0) {
        // 更新已有卡片：保留原 layout，仅覆盖其余字段
        const prevLayout = db.items[idx].layout;
        db.items[idx] = { ...item, layout: prevLayout ?? item.layout };
      } else {
        db.items.push(item);
      }
    }

    generateLayout(db.items);
    db.updatedAt = Date.now();
    return persist(current);
  }, [persist, reload]);

  /** 创建指定名称的新仪表板并初始包含传入项目；不自动切换 currentDashboardId。返回新仪表板 ID 或 null */
  const createDashboardWithItems = useCallback((name: string, items: DashboardItem[]): string | null => {
    const trimmed = name.trim();
    if (!trimmed || trimmed.length > 64) return null;
    const current = reload();
    if (current.dashboards.some(d => d.name === trimmed)) return null;

    const newDashboard: DashboardMeta = {
      id: generateId(),
      name: trimmed,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      items: items.map(item => ({ ...item })),
    };
    generateLayout(newDashboard.items);
    current.dashboards = [...current.dashboards, newDashboard];
    if (!persist(current)) return null;
    return newDashboard.id;
  }, [persist, reload]);

  return {
    dashboards,
    currentDashboardId,
    currentDashboardName,
    currentItems,
    addItems,
    removeItem,
    updateLayout,
    updateItemHeight,
    updateChartSpec,
    createDashboard,
    switchDashboard,
    addItemsToDashboard,
    createDashboardWithItems,
  };
}
