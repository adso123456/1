# 小助手会话删除板块完善 + 嵌入真实远端网站 —— 设计规格

- 日期：2026-08-04
- 状态：已评审，待实施
- 范围：嵌入小助手（widget）的会话删除/清空 + 服务端清理 + 浏览器本地持久化 + 跨域部署缺口

## 1. 背景与目标

项目小助手将通过 `water-agent-widget.js` 嵌入到远端服务器的真实网站（Bridged 跨域嵌入）。
针对此前已完成但未闭环的"会话删除板块"进行完善，目标 5 项：

1. 刷新/重开浮窗后历史会话仍保留（bridged 模式由纯内存改为浏览器 localStorage）。
2. 删除会话时服务端同步清理（解绑 + 清 Agent 会话上下文）。
3. 清空对话时服务端同步清上下文。
4. 删除交互/UI 完善（toast 反馈、删除当前会话跳转、删除最后一个会话处理）。
5. 补全真实跨域部署的代码缺口（`EMBED_ALLOWED_METHODS` 增加 `DELETE`）。

## 2. 现状分析

### 2.1 嵌入架构（Bridged 模式，已完整可用）

```
远端网站页面 (Origin = 站点A)
  └─ water-agent-widget.js 创建 iframe → agentUrl/?mode=widget&parentOrigin=...&appId=...
        iframe 内 BridgedWidgetChat → WidgetRpcClient postMessage RPC
        ← 父页面 water-agent-widget.js handleRpcRequest 代理 fetch → 后端
        /api/embed/apps/{app_id}/*   授权 = 真实 Origin + app_id 白名单（embed_access.py）
```

- `requestDefinition`（water-agent-widget.js）将 RPC operation 映射为后端 URL；
- 后端 `dynamic_embed_cors` 中间件统一处理 `/api/embed/apps/*` 的 CORS 与 OPTIONS 预检；
- 请求 `credentials: 'omit'`，不依赖 Cookie/Token。

### 2.2 会话删除现状（缺口）

- `WidgetSessionSelect` 下拉框每会话带删除按钮，`window.confirm` 后调 `useSSE.deleteSession`；另有「清空对话」按钮调 `clearMessages`。
- `deleteSession` / `clearMessages` **只清理客户端**：bridged 模式 `persistenceMode: 'memory'` 纯内存，刷新即失；localStorage 模式也只在浏览器层删除。
- 服务端**无任何会话删除接口**。删掉会话后仍残留：
  - `conversation_source_bindings`（SQLite 会话↔数据源绑定）；
  - 该数据源 Runtime 内 Agent 的 `MemoryConversationStore`（LLM 会话上下文，按 conversation_id 存，进程内持续，重启清空）。
- RPC 桥无 `delete-conversation` / `clear-conversation` 操作；`requestDefinition` 无删除映射。
- `EMBED_ALLOWED_METHODS = "GET, POST, OPTIONS"`，不含 `DELETE`，新删除路由无法过 CORS。

### 2.3 服务端会话状态边界（已确认决策 A）

每个会话服务端状态 = ① SQLite 绑定行 ② 该 source Runtime 内 `MemoryConversationStore` 的会话上下文。
**已知限制**：当前 Vanna 0.1.0 的 `ChromaAgentMemory` 文本/工具记忆不按会话隔离（`context` 参数只传不存），
故"清上下文"只清 `MemoryConversationStore`，**不清理全局 ChromaDB 记忆**。此为合理边界，不做过度改造。

## 3. 架构决策（已确认）

### 决策 A：服务端清理边界
清理目标 = 绑定行 + `MemoryConversationStore` 会话上下文。ChromaDB 全局记忆不在范围内。

### 决策 B：清理路径
走 **embed 路径** `/api/embed/apps/{app_id}/conversations/...`（真实 Origin + app_id 授权），
因为 widget 无权访问后台 admin 保护路由。主工作台行为保持不变（不接入服务端清理）。

### 决策 C：持久化
bridged 模式 `persistenceMode: 'memory'` → `'local'`，复用现有 `localSessionStorageAdapter`，
**共享 localStorage 键**（与主工作台同键，保留「完整工作台」链接的会话贯通）。
代价：同一浏览器内 widget 与工作台会话互相可见；未来多站点共用同一 agent 域名会混会话。当前单站可接受。

## 4. 后端改动

| 文件 | 改动 |
|------|------|
| `backend/data_source_catalog.py` | 新增 `unbind_conversation(conversation_id)`：SQLite `DELETE FROM conversation_source_bindings WHERE conversation_id=?` |
| `backend/conversation_data_source_binding.py` | `ConversationDataSourceBindings` 新增 `unbind(conversation_id)`：catalog 模式删 SQLite 行；内存模式 `pop`。**不改动**现有 `release()`（持久化模式下故意抛"不可解除"） |
| `backend/data_source_request_coordinator.py` | 新增 `unbind(conversation_id) -> DataSourceRequestContext`：取 binding → 校验 source 存在 → 调 bindings.unbind |
| `backend/embed_conversation_service.py` | **新增**：`clear_agent_conversation_context()`，见 4.2 |
| `step4_server.py` | ① `EMBED_ALLOWED_METHODS` 加 `DELETE` ② 新增 2 个 embed 路由 |

### 4.1 新增 embed 路由

```
DELETE /api/embed/apps/{app_id}/conversations/{conversation_id}
```
- 授权：`authorize_embed(app_id, origin)`（复用现有逻辑）。
- 解析：`coordinator.require(conversation_id)` → 未绑定抛 404「会话尚未绑定数据源」。
- 校验：`context.source_id` 必须在 `principal.application.allowed_source_ids`，否则 403。
- 清理上下文：若 `runtime_manager.runtime_revision(source_id)` 非空，`with runtime_manager.acquire(source_id) as runtime`，
  调 `runtime.agent.conversation_store.delete_conversation(conversation_id, User(id="demo"))`（与 `SimpleUserResolver` 返回的 user id 一致）。
- 解绑：`coordinator.unbind(conversation_id)`。
- 返回：`200 {"conversation_id": ..., "cleaned": true}`；未绑定返回 `404`。

```
DELETE /api/embed/apps/{app_id}/conversations/{conversation_id}/messages
```
- 授权 / 解析 / source 校验同上。
- 只清理上下文（`delete_conversation`），**不解绑**。
- 返回：`200 {"conversation_id": ..., "cleared": true}`；未绑定返回 `404`。

### 4.2 上下文清理 helper
新增独立后端模块 `backend/embed_conversation_service.py`（便于单测）：
```python
# backend/embed_conversation_service.py
async def clear_agent_conversation_context(
    runtime_manager, source_id: str, conversation_id: str
) -> bool:
    """清空指定 source Runtime 内某会话的 LLM 上下文；runtime 未构建返回 False。"""
    if runtime_manager.runtime_revision(source_id) is None:
        return False  # runtime 未构建，无会话上下文
    from vanna.core.user import User
    with runtime_manager.acquire(source_id) as runtime:
        store = getattr(runtime.agent, "conversation_store", None)
        if store is None:
            return False
        return await store.delete_conversation(conversation_id, User(id="demo"))
```
注意：`acquire` 为同步 context manager，内部 `delete_conversation` 为 async，在 async 路由中直接 await（阻塞在主事件循环的量级很小，与现有 chat 路径一致；如后续需要可再改 `asyncio.to_thread`）。

## 5. 前端改动

| 文件 | 改动 |
|------|------|
| `frontend/src/widgetMessageProtocol.ts` | `WidgetRpcOperation` 增加 `'delete-conversation'`、`'clear-conversation'` |
| `frontend/public/water-agent-widget.js` | `requestDefinition` 增加两个映射（均 `DELETE`） |
| `frontend/src/hooks/useSSE.ts` | `UseSSERequestOptions` 增加可选 `conversationDeleteEndpoint` / `conversationClearEndpoint`；`deleteSession` 改 async 并 best-effort 调服务端；`clearMessages` 触发服务端清上下文 |
| `frontend/src/WidgetApp.tsx` | BridgedWidgetChat `persistenceMode: 'memory'`→`'local'`；fetcher 增加两个 URL 映射；删除/清空 toast 反馈 |

### 5.1 RPC 操作与 URL 映射

`widgetRpcClient.ts` 的 `request()` 以 `WidgetRpcOperation` 发 postMessage，父页面 `requestDefinition` 映射：

```text
operation: 'delete-conversation', payload { conversationId }
  → DELETE  base + '/conversations/' + encodeURIComponent(conversationId)

operation: 'clear-conversation',  payload { conversationId }
  → DELETE  base + '/conversations/' + encodeURIComponent(conversationId) + '/messages'
```

`WidgetApp.tsx` 的 fetcher 增加（沿用 suggested-questions 的 URL 约定）：
```ts
if (url.startsWith('widget-rpc:delete-conversation')) {
  const id = new URLSearchParams(url.split('?')[1] || '').get('conversation_id') || '';
  return rpcClient.request('delete-conversation', { conversationId: id }, signal);
}
if (url.startsWith('widget-rpc:clear-conversation')) {
  const id = new URLSearchParams(url.split('?')[1] || '').get('conversation_id') || '';
  return rpcClient.request('clear-conversation', { conversationId: id }, signal);
}
```

### 5.2 useSSE 改动

`UseSSERequestOptions` 新增可选字段：
```ts
conversationDeleteEndpoint?: string;  // 如 'widget-rpc:delete-conversation'
conversationClearEndpoint?: string;   // 如 'widget-rpc:clear-conversation'
```
- 主工作台不配置 → 服务端清理不触发，行为完全不变（向后兼容）。
- `deleteSession(id)` 改为 `async (id) => Promise<{ deleted: boolean; serverCleanup: 'ok' | 'failed' | 'skipped' }>`：
  1. 现有本地删除逻辑（sessions/meta/pendingReports + 删除当前会话时切换）；
  2. 若配置了 `conversationDeleteEndpoint`，best-effort `requestFetch(endpoint + '?conversation_id=' + encodeURIComponent(id), { method: 'DELETE' })`；
     `404` 视为成功（本来就不存在）；其他非 2xx 记为 `serverCleanup: 'failed'`（不影响本地删除结果）；未配置端点 → `'skipped'`。
  3. UI 依据返回结构决定 toast 文案。
- `clearMessages()`：本地清空逻辑不变；若配置 `conversationClearEndpoint`，best-effort 触发服务端清上下文（同样 `404` 视为成功）。

### 5.3 WidgetApp UI 反馈

- 删除会话：toast「会话已删除」；服务端清理失败 toast「会话已删除（服务端清理失败）」。
- 清空对话：toast「对话已清空」。
- 保留 `window.confirm` 二次确认；删除当前会话跳最近会话、删最后一个进空白新会话（useSSE 现有逻辑，保留）。

## 6. 已知限制与取舍

1. **ChromaDB 全局记忆不按会话隔离**：清空/删除只能重置 LLM 会话上下文，历史 Q&A 训练记忆保留（符合训练资产治理，不误删全局记忆）。
2. **共享 localStorage 键**：widget 与主工作台同键，会话互相可见；保留「完整工作台」会话贯通。多站共用 agent 域名时需后续按 appId 命名空间隔离。
3. **MemoryConversationStore 进程内存**：服务重启后上下文清空，属既有行为，不在本次范围。
4. **删除采用 best-effort**：本地删除即时生效；服务端清理失败只提示，不阻塞（会话 ID 全局唯一，残留仅是脏数据，不影响正确性）。

## 7. 测试计划

**后端（`tools/test_*.py`，venv python 直跑）**
- `test_conversation_data_source_binding.py`：新增 `unbind` 用例（内存模式 pop、catalog 模式 SQLite 删除、未绑定抛错）。
- `test_data_source_request_coordinator.py`：新增 `coordinator.unbind` 用例。
- 新增 embed 删除路由测试：未授权 Origin 401、未知 app 401、禁用 403、source 未授权 403、未绑定会话 404、删除成功返回结构、messages 端点不解绑。

**前端**
- `widgetEmbedBridge.test.mjs`：新增 `delete-conversation` / `clear-conversation` 的 URL、method(DELETE)、payload 断言。
- `useSSE` 相关测试：`deleteSession` 本地删除 + 调端点（404 视为成功、失败返回值）、`clearMessages` 触发清上下文端点。

## 8. 验收标准

1. bridged widget 刷新页面后会话列表与当前会话保留。
2. 删除会话后：前端列表消失、SQLite `conversation_source_bindings` 无该行、`MemoryConversationStore` 无该会话。
3. 清空对话后：前端消息清空、`MemoryConversationStore` 无该会话、绑定保留（再次发送可继续问答）。
4. `DELETE` 已进入 `Access-Control-Allow-Methods`，真实跨域页面删除请求过预检。
5. 主工作台行为不变（未配置端点时不触发任何服务端清理）。
