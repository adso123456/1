import html2canvas from 'html2canvas';

interface ExportPiece {
  /** 真实 DOM 元素（传入 html2canvas） */
  el: HTMLElement;
  /** 克隆文档中的查找标记 */
  selector: string;
  /** 相对 root 的坐标 */
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * 将仪表板 header 和各卡片分别截图，拼合到一张 Canvas 上
 *
 * 避免整板 html2canvas 时 overflow 裁剪祖先导致下方内容为空。
 * 每张截图在 onclone 中将目标节点移到 body 左上角，脱离所有
 * 滚动/transform/overflow 祖先。
 *
 * 参考 SQLBot ChartBlock.vue：html2canvas → canvas.toBlob → 下载链接 → revokeObjectURL
 */
export async function exportDashboardAsPng(
  root: HTMLElement,
  filename: string,
): Promise<void> {
  // 等待字体加载完成
  if (document.fonts?.ready) {
    await document.fonts.ready;
  }

  // 等待两帧，确保 ECharts 动画和 react-grid-layout 布局稳定
  await new Promise<void>(resolve =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
  );

  const rootWidth = root.scrollWidth || root.clientWidth;
  const rootHeight = root.scrollHeight || root.clientHeight;

  // 限制 scale，避免超长仪表板生成超大 Canvas 导致浏览器崩溃
  // 目标：Canvas 像素面积不超过 20M（约 5000×4000）
  const MAX_CANVAS_AREA = 20_000_000;
  const naturalScale = window.devicePixelRatio || 1;
  const scale = Math.min(naturalScale, Math.sqrt(MAX_CANVAS_AREA / (rootWidth * rootHeight)));

  // 根节点坐标原点（用于计算各部件相对偏移）
  const rootRect = root.getBoundingClientRect();

  // 收集所有待导出部件
  const pieces: ExportPiece[] = [];

  const header = root.querySelector('[data-export-header]') as HTMLElement | null;
  if (header) {
    const r = header.getBoundingClientRect();
    pieces.push({
      el: header,
      selector: '[data-export-header]',
      x: r.left - rootRect.left,
      y: r.top - rootRect.top,
      width: r.width,
      height: r.height,
    });
  }

  const cards = root.querySelectorAll('[data-export-card]') as NodeListOf<HTMLElement>;
  for (let i = 0; i < cards.length; i++) {
    const card = cards[i];
    const cardId = card.getAttribute('data-export-card-id');
    if (!cardId) continue;
    const r = card.getBoundingClientRect();
    pieces.push({
      el: card,
      selector: `[data-export-card-id="${cardId}"]`,
      x: r.left - rootRect.left,
      y: r.top - rootRect.top,
      width: r.width,
      height: r.height,
    });
  }

  // 创建最终合成画布，填充仪表板背景色
  const finalCanvas = document.createElement('canvas');
  finalCanvas.width = rootWidth * scale;
  finalCanvas.height = rootHeight * scale;
  const ctx = finalCanvas.getContext('2d');
  if (!ctx) {
    throw new Error('导出失败：无法创建画布');
  }
  ctx.fillStyle = '#f5f5f5';
  ctx.fillRect(0, 0, finalCanvas.width, finalCanvas.height);

  // 逐一截图并拼合
  for (const piece of pieces) {
    if (piece.width <= 0 || piece.height <= 0) continue;

    const pieceCanvas = await html2canvas(piece.el, {
      width: piece.width,
      height: piece.height,
      scale,
      backgroundColor: null,
      useCORS: true,
      logging: false,
      ignoreElements: (el: Element) => {
        return (
          el.hasAttribute('data-export-exclude') ||
          el.classList.contains('react-resizable-handle')
        );
      },
      onclone: (clonedDoc) => {
        // 在克隆文档中找到当前部件
        const target = clonedDoc.querySelector(piece.selector) as HTMLElement | null;

        // 清空 body 后仅挂入目标节点，彻底脱离所有 overflow/transform 祖先
        clonedDoc.body.innerHTML = '';
        if (target) {
          clonedDoc.body.appendChild(target);
          target.style.position = 'absolute';
          target.style.left = '0';
          target.style.top = '0';
          target.style.width = `${piece.width}px`;
          target.style.height = `${piece.height}px`;
          target.style.overflow = 'visible';
          target.style.boxSizing = 'border-box';
        }

        clonedDoc.body.style.margin = '0';
        clonedDoc.body.style.overflow = 'visible';
        clonedDoc.body.style.width = `${piece.width}px`;
        clonedDoc.body.style.height = `${piece.height}px`;
      },
    });

    ctx.drawImage(
      pieceCanvas,
      piece.x * scale,
      piece.y * scale,
      piece.width * scale,
      piece.height * scale,
    );
  }

  // canvas.toBlob → 下载链接 → revokeObjectURL（与 SQLBot 一致）
  const blob = await new Promise<Blob | null>(resolve => {
    finalCanvas.toBlob(resolve, 'image/png');
  });

  if (!blob) {
    throw new Error('导出失败：无法生成图片数据');
  }

  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/** 生成导出文件名：水利智能问答-仪表板-YYYYMMDD-HHmmss.png */
export function generateExportFilename(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  const y = now.getFullYear();
  const mo = pad(now.getMonth() + 1);
  const d = pad(now.getDate());
  const h = pad(now.getHours());
  const mi = pad(now.getMinutes());
  const s = pad(now.getSeconds());
  return `水利智能问答-仪表板-${y}${mo}${d}-${h}${mi}${s}.png`;
}
