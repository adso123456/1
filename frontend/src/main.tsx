import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { EmbedDemoPage } from './EmbedDemoPage.tsx'
import { WidgetApp } from './WidgetApp.tsx'
import { resolveApplicationMode } from './appMode.ts'

const mode = resolveApplicationMode(
  window.location.pathname,
  window.location.search,
)

const application = mode === 'embed-demo'
  ? <EmbedDemoPage />
  : mode === 'widget'
    ? <WidgetApp />
    : <App />

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {application}
  </StrictMode>,
)
