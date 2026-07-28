export interface EmbedCodeInput {
  appId: string;
  parentOrigin: string;
  allowedSourceIds: string[];
  tokenTtlSeconds: number;
  agentOrigin: string;
}

export interface EmbedCodeOutput {
  browserHtml: string;
  pythonFastApi: string;
  environment: string;
}

export function normalizeHttpOrigin(value: string): string {
  if (value.includes('*')) return '';
  try {
    const url = new URL(value);
    if (
      (url.protocol !== 'http:' && url.protocol !== 'https:')
      || url.origin !== value
      || url.username
      || url.password
      || url.pathname !== '/'
      || url.search
      || url.hash
    ) {
      return '';
    }
    return url.origin;
  } catch {
    return '';
  }
}

function pythonString(value: string): string {
  return JSON.stringify(value)
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029');
}

export function generateEmbedCode(
  input: EmbedCodeInput,
): EmbedCodeOutput {
  const agentOrigin = normalizeHttpOrigin(input.agentOrigin);
  const parentOrigin = normalizeHttpOrigin(input.parentOrigin);
  if (!agentOrigin) throw new Error('Agent URL 必须是精确的 http/https Origin。');
  if (!parentOrigin) throw new Error('请选择有效的允许 Origin。');
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(input.appId)) {
    throw new Error('app_id 格式无效。');
  }
  const allowedSourceIds = [...new Set(input.allowedSourceIds)];
  if (!allowedSourceIds.length) {
    throw new Error('应用没有当前有效的数据源授权。');
  }
  if (
    !Number.isInteger(input.tokenTtlSeconds)
    || input.tokenTtlSeconds < 30
  ) {
    throw new Error('Token TTL 配置无效。');
  }

  const browserHtml = `<script
  src=${JSON.stringify(`${agentOrigin}/water-agent-widget.js`)}
  data-auto-init="false"
></script>
<script>
  WaterAgentWidget.init({
    agentUrl: ${JSON.stringify(agentOrigin)},
    getToken: async function () {
      const response = await fetch("/api/water-agent/embed-token", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Accept": "application/json"
        }
      });
      if (!response.ok) {
        throw new Error("Token 获取失败");
      }
      return response.json();
    }
  });
</script>`;

  const sourceLiteral = `[${allowedSourceIds
    .map(pythonString)
    .join(', ')}]`;
  const pythonFastApi = `import os
import time
import uuid

import jwt
from fastapi import APIRouter, Depends, Response
from your_app.auth import require_current_user

router = APIRouter()

APP_ID = os.environ["WATER_AGENT_APP_ID"]
APP_SECRET = os.environ["WATER_AGENT_APP_SECRET"]
PARENT_ORIGIN = ${pythonString(parentOrigin)}
ALLOWED_SOURCE_IDS = ${sourceLiteral}
TOKEN_TTL_SECONDS = ${input.tokenTtlSeconds}


@router.post("/api/water-agent/embed-token")
def issue_embed_token(
    response: Response,
    current_user=Depends(require_current_user),
):
    now = int(time.time())
    token = jwt.encode(
        {
            "aud": "water-agent-embed",
            "app_id": APP_ID,
            "sub": str(current_user.id),
            "parent_origin": PARENT_ORIGIN,
            "allowed_source_ids": ALLOWED_SOURCE_IDS,
            "iat": now,
            "exp": now + TOKEN_TTL_SECONDS,
            "jti": uuid.uuid4().hex,
        },
        APP_SECRET,
        algorithm="HS256",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {"token": token, "expires_at": now + TOKEN_TTL_SECONDS}
`;

  const environment = `WATER_AGENT_APP_ID=${input.appId}
WATER_AGENT_APP_SECRET=<paste_saved_application_secret_here>
WATER_AGENT_PARENT_ORIGIN=${parentOrigin}
WATER_AGENT_ALLOWED_SOURCE_IDS=${allowedSourceIds.join(',')}
WATER_AGENT_TOKEN_TTL_SECONDS=${input.tokenTtlSeconds}`;

  return { browserHtml, pythonFastApi, environment };
}
