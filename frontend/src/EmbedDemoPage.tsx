import { useEffect, useState } from 'react';

interface WaterAgentWidgetApi {
  init(options?: { agentUrl?: string; widgetPath?: string }): WaterAgentWidgetApi;
  open(): void;
  close(): void;
  destroy(): void;
}

declare global {
  interface Window {
    WaterAgentWidget?: WaterAgentWidgetApi;
  }
}

const overviewCards = [
  { label: '在线监测站', value: '128', trend: '运行正常' },
  { label: '今日预警', value: '3', trend: '均已处置' },
  { label: '重点河段', value: '24', trend: '持续巡查' },
  { label: '数据完整率', value: '98.6%', trend: '较昨日 +0.4%' },
];

export function EmbedDemoPage() {
  const [loaderError, setLoaderError] = useState(false);

  useEffect(() => {
    const existing = document.querySelector<HTMLScriptElement>(
      'script[data-water-agent-demo]',
    );
    if (existing) {
      window.WaterAgentWidget?.init({ agentUrl: window.location.origin });
      return;
    }

    const script = document.createElement('script');
    script.src = '/water-agent-widget.js';
    script.dataset.waterAgentDemo = 'true';
    script.dataset.autoInit = 'false';
    script.onload = () => {
      window.WaterAgentWidget?.init({
        agentUrl: window.location.origin,
        widgetPath: '/?mode=widget',
      });
    };
    script.onerror = () => setLoaderError(true);
    document.body.appendChild(script);

    return () => {
      window.WaterAgentWidget?.destroy();
      script.remove();
    };
  }, []);

  return (
    <div className="demo-site">
      <header className="demo-header">
        <div className="demo-brand">
          <span className="demo-brand-mark">水</span>
          <div>
            <strong>水务管理平台</strong>
            <span>Water Operations Center</span>
          </div>
        </div>
        <nav aria-label="主导航">
          <a href="#overview" className="active">运行总览</a>
          <a href="#monitor">监测管理</a>
          <a href="#alerts">预警中心</a>
          <a href="#reports">统计分析</a>
        </nav>
        <span className="demo-user">值班中心</span>
      </header>

      <main className="demo-main">
        <section className="demo-hero" id="overview">
          <div>
            <span className="demo-eyebrow">综合运行态势</span>
            <h1>水务运行一张图</h1>
            <p>汇聚监测、预警与巡查信息，为日常管理提供统一的数据概览。</p>
          </div>
          <div className="demo-update-time">
            <span>数据更新时间</span>
            <strong>今天 10:32</strong>
          </div>
        </section>

        <section className="demo-overview-grid" aria-label="运行概况">
          {overviewCards.map(card => (
            <article key={card.label} className="demo-stat-card">
              <span>{card.label}</span>
              <strong>{card.value}</strong>
              <small>{card.trend}</small>
            </article>
          ))}
        </section>

        <section className="demo-content-grid">
          <article className="demo-panel demo-map-panel" id="monitor">
            <div className="demo-panel-heading">
              <div>
                <span>实时监测</span>
                <h2>重点区域运行概况</h2>
              </div>
              <button type="button">查看详情</button>
            </div>
            <div className="demo-map-visual" aria-label="区域监测示意图">
              <span className="demo-river river-one" />
              <span className="demo-river river-two" />
              <i className="demo-map-dot dot-one" />
              <i className="demo-map-dot dot-two" />
              <i className="demo-map-dot dot-three" />
              <i className="demo-map-dot dot-four" />
              <div className="demo-map-legend">
                <span><i className="normal" />正常 121</span>
                <span><i className="warning" />关注 7</span>
              </div>
            </div>
          </article>

          <article className="demo-panel" id="alerts">
            <div className="demo-panel-heading">
              <div>
                <span>待办事项</span>
                <h2>今日通知</h2>
              </div>
              <b>3</b>
            </div>
            <ul className="demo-notice-list">
              <li>
                <span className="notice-level warning">关注</span>
                <div><strong>部分监测点数据延迟</strong><small>10:18 · 自动诊断中</small></div>
              </li>
              <li>
                <span className="notice-level info">巡查</span>
                <div><strong>本周河道巡查任务</strong><small>09:40 · 已分派 8 项</small></div>
              </li>
              <li>
                <span className="notice-level success">完成</span>
                <div><strong>昨日预警复核完毕</strong><small>08:55 · 处置率 100%</small></div>
              </li>
            </ul>
          </article>
        </section>

        <section className="demo-panel demo-table-panel" id="reports">
          <div className="demo-panel-heading">
            <div>
              <span>数据概览</span>
              <h2>区域运行摘要</h2>
            </div>
          </div>
          <div className="demo-summary-row">
            <span>夷陵区<strong>监测点 32</strong><small>正常</small></span>
            <span>西陵区<strong>监测点 21</strong><small>正常</small></span>
            <span>伍家岗区<strong>监测点 18</strong><small>1 项关注</small></span>
            <span>点军区<strong>监测点 16</strong><small>正常</small></span>
          </div>
        </section>
      </main>

      {loaderError && (
        <div className="demo-loader-error" role="alert">
          智能助手加载失败，请刷新页面重试。
        </div>
      )}
    </div>
  );
}
