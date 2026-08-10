import {
  formatStructuredResultMessage,
  sanitizeInternalProtocolText,
} from '../messageSanitization.ts';

const dirty = '准备计算\n<|DSML|tool_calls><|DSML|invoke name="run_sql">SQL</|DSML|invoke></|DSML|tool_calls>\n计算完成';
const clean = sanitizeInternalProtocolText(dirty);

if (clean.includes('DSML') || clean.includes('run_sql')) {
  throw new Error(`内部工具协议未清理: ${clean}`);
}
if (!clean.includes('准备计算') || !clean.includes('计算完成')) {
  throw new Error(`清理时误删正常正文: ${clean}`);
}

const partial = sanitizeInternalProtocolText('正常正文<|DSML|tool_calls><|DSML|invoke');
if (partial !== '正常正文') {
  throw new Error(`未闭合协议尾部未清理: ${partial}`);
}

if (formatStructuredResultMessage(15, true) !== '已查询到 15 条记录，并生成图表。') {
  throw new Error('图表结果文案不符合统一模板');
}
if (formatStructuredResultMessage(5, false) !== '已查询到 5 条记录。') {
  throw new Error('表格结果文案不符合统一模板');
}

console.log('消息协议清理回归测试通过');
