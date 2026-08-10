const DSML_TOOL_CALL_BLOCK_RE = /<\|\s*DSML\s*\|\s*tool_calls\s*>[\s\S]*?<\/\|\s*DSML\s*\|\s*tool_calls\s*>/gi;
const DSML_TOOL_CALL_TAIL_RE = /<\|\s*DSML\s*\|\s*tool_calls\s*>[\s\S]*$/i;

/** 清除模型提供商内部工具协议，避免协议文本进入用户可见正文。 */
export function sanitizeInternalProtocolText(text: string): string {
  return text
    .replace(DSML_TOOL_CALL_BLOCK_RE, '')
    .replace(DSML_TOOL_CALL_TAIL_RE, '')
    .trimEnd();
}

/** 结构化查询结果统一使用紧凑回复文案，避免重复展示模型生成的表格和长分析。 */
export function formatStructuredResultMessage(
  rowCount: number,
  hasChart: boolean,
): string {
  const count = Number.isFinite(rowCount) ? Math.max(0, Math.trunc(rowCount)) : 0;
  return hasChart
    ? `已查询到 ${count} 条记录，并生成图表。`
    : `已查询到 ${count} 条记录。`;
}
