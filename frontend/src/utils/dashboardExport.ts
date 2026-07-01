import html2canvas from 'html2canvas';

/**
 * 导出仪表板为 PNG
 * 参考 SQLBot ChartBlock.vue：html2canvas → canvas.toBlob → 下载链接 → revokeObjectURL
 *
 * @param element 要导出的 DOM 元素（需有 data-export-root 属性）
 * @param filename 下载文件名
 */
export async function exportDashboardAsPng(
  element: HTMLElement,
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

  const scrollWidth = element.scrollWidth || element.clientWidth;
  const scrollHeight = element.scrollHeight || element.clientHeight;

  // 限制 scale，避免超长仪表板生成超大 Canvas 导致浏览器崩溃
  // 目标：Canvas 像素面积不超过 20M（约 5000×4000）
  const MAX_CANVAS_AREA = 20_000_000;
  const naturalScale = window.devicePixelRatio || 1;
  const scale = Math.min(naturalScale, Math.sqrt(MAX_CANVAS_AREA / (scrollWidth * scrollHeight)));

  const canvas = await html2canvas(element, {
    width: scrollWidth,
    height: scrollHeight,
    windowWidth: scrollWidth,
    windowHeight: scrollHeight,
    scrollX: 0,
    scrollY: 0,
    x: 0,
    y: 0,
    scale,
    backgroundColor: '#f5f5f5', // 仪表板背景色
    useCORS: true,
    logging: false,
    // 排除操作按钮和缩放手柄
    ignoreElements: (el: Element) => {
      return (
        el.hasAttribute('data-export-exclude') ||
        el.classList.contains('react-resizable-handle')
      );
    },
    // 在克隆文档中展开滚动容器，使 html2canvas 截取完整内容
    onclone: (clonedDoc) => {
      const root = clonedDoc.querySelector('[data-export-root]') as HTMLElement | null;
      if (root) {
        root.style.width = `${scrollWidth}px`;
        root.style.height = `${scrollHeight}px`;
        root.style.maxHeight = 'none';
        root.style.overflow = 'visible';
        root.scrollTop = 0;
        root.scrollLeft = 0;
      }

      // 同步扩展 html 和 body 到导出尺寸，防止视口裁剪
      const html = clonedDoc.documentElement;
      const body = clonedDoc.body;
      if (html) {
        html.style.width = `${Math.max(scrollWidth, html.scrollWidth || 0)}px`;
        html.style.height = `${Math.max(scrollHeight, html.scrollHeight || 0)}px`;
        html.style.overflow = 'visible';
        html.scrollTop = 0;
        html.scrollLeft = 0;
      }
      if (body) {
        body.style.width = `${Math.max(scrollWidth, body.scrollWidth || 0)}px`;
        body.style.height = `${Math.max(scrollHeight, body.scrollHeight || 0)}px`;
        body.style.overflow = 'visible';
        body.scrollTop = 0;
        body.scrollLeft = 0;
      }
    },
  });

  // canvas.toBlob → 下载链接 → revokeObjectURL（与 SQLBot 一致）
  const blob = await new Promise<Blob | null>(resolve => {
    canvas.toBlob(resolve, 'image/png');
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
