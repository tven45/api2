"""
DeepSeek Web Proxy — OpenAI-compatible API
POST /v1/chat/completions  → streams DeepSeek web responses (with thinking & tool calls)
GET  /v1/models            → returns available models
POST /admin/token          → update a token (from grab_token.py)
GET  /admin/status         → health of all accounts
GET  /admin/logs           → recent request diagnostic logs
POST /admin/accounts       → add a new account
"""
import json, os, re, time, uuid
from collections import deque
from datetime import datetime
from typing import Optional, List, Any
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from account_manager import get_manager

app = FastAPI(title="DeepSeek Web Proxy", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)
API_KEY = os.getenv("API_KEY", "sk-deepseek-proxy-change-me")
RECENT_LOGS = deque(maxlen=30)

# ─── Auth ────────────────────────────────────────────────────────────────────
def verify_key(
    req: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    token = None
    if creds and creds.credentials:
        token = creds.credentials
    else:
        auth = req.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        elif auth:
            token = auth.strip()
        else:
            token = req.headers.get("x-api-key") or req.headers.get("api-key")

    if token != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return token

# ─── Models ──────────────────────────────────────────────────────────────────
class TokenUpdate(BaseModel):
    account_id: int
    token: str

class NewAccount(BaseModel):
    email: str
    token: str
    note: str = ""

# ─── SSE & Prompt Helpers ────────────────────────────────────────────────────
def _openai_chunk(
    chunk_id: str,
    created: int,
    model: str,
    delta: dict,
    finish_reason: Optional[str] = None,
) -> str:
    chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }
    return f"data: {json.dumps(chunk)}\n\n"

def _parse_deepseek_sse(lines_iter):
    """
    Stateful generator yielding (is_thinking: bool, text: str) from DeepSeek SSE stream.
    """
    current_type = "RESPONSE"
    for line in lines_iter:
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            d = json.loads(raw)
        except Exception:
            continue

        v = d.get("v")
        p = d.get("p", "")
        o = d.get("o", "")

        # ── Finished ─────────────────────────────────────────────────────
        if p == "response/status" and v == "FINISHED":
            return
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and item.get("p") in ("response/status", "quasi_status") and item.get("v") == "FINISHED":
                    return

        # ── Full response init: {"v": {"response": {...}}} ───────────────
        if isinstance(v, dict) and "response" in v:
            for frag in v["response"].get("fragments", []):
                if isinstance(frag, dict):
                    current_type = frag.get("type", "RESPONSE")
                    c = frag.get("content", "")
                    if c:
                        yield (current_type == "THINK", c)

        # ── New fragment appended: {"p": "response/fragments", "o": "APPEND", "v": [{...}]}
        elif p == "response/fragments" and o == "APPEND" and isinstance(v, list):
            for frag in v:
                if isinstance(frag, dict):
                    current_type = frag.get("type", "RESPONSE")
                    c = frag.get("content", "")
                    if c:
                        yield (current_type == "THINK", c)

        # ── Fragment set: {"p": "response/fragments/...", "o": "SET", "v": {...}}
        elif o == "SET" and p.startswith("response/fragments/") and isinstance(v, dict):
            current_type = v.get("type", current_type)
            c = v.get("content", "")
            if c:
                yield (current_type == "THINK", c)

        # ── Append string patch: {"p": "response/fragments/.../content", "o": "APPEND", "v": "..."}
        elif o == "APPEND" and isinstance(v, str) and v:
            yield (current_type == "THINK", v)

        # ── Delta string token: {"v": " hello"} (no path, no op) ─────────
        elif isinstance(v, str) and v and not p and not o:
            yield (current_type == "THINK", v)

def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if "text" in part and isinstance(part["text"], str):
                    parts.append(part["text"])
                elif "content" in part and isinstance(part["content"], str):
                    parts.append(part["content"])
        return "\n".join(p for p in parts if p)
    return str(content)

def _build_prompt(messages: list, tools: Optional[list] = None) -> str:
    system_parts = []
    conv_parts = []

    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "user")
        text = _extract_text(m.get("content"))

        if role in ("system", "developer"):
            if text:
                system_parts.append(text)
        elif role == "user":
            if text:
                conv_parts.append(f"User: {text}")
        elif role == "assistant":
            tcalls = m.get("tool_calls")
            tc_str = ""
            if isinstance(tcalls, list) and tcalls:
                tc_items = []
                for tc in tcalls:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    tc_items.append(
                        f"<tool_call>{json.dumps({'name': fn.get('name'), 'arguments': fn.get('arguments')})}</tool_call>"
                    )
                tc_str = "\n".join(tc_items)
            combined = "\n".join(p for p in [text, tc_str] if p)
            if combined:
                conv_parts.append(f"Assistant: {combined}")
        elif role == "tool":
            tc_id = m.get("tool_call_id", "")
            conv_parts.append(f"Tool Result ({tc_id}): {text}")

    prompt_sections = []
    if system_parts:
        prompt_sections.append("System Instructions:\n" + "\n\n".join(system_parts))

    if tools and isinstance(tools, list):
        tool_defs = []
        for t in tools:
            if isinstance(t, dict) and t.get("type") == "function":
                fn = t.get("function", {})
                tool_defs.append({
                    "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                })
        if tool_defs:
            tool_prompt = (
                "Available Tools:\n"
                + json.dumps(tool_defs, ensure_ascii=False)
                + "\n\nWhen you need to call a tool, output ONLY a tool call block in this exact format:\n"
                + '<tool_call>{"name": "tool_name", "arguments": {"arg1": "val1"}}</tool_call>\n'
                + "Do not wrap <tool_call> in markdown code fences. If you do not need a tool, respond normally."
            )
            prompt_sections.append(tool_prompt)

    if len(conv_parts) == 1 and conv_parts[0].startswith("User: ") and not system_parts and not tools:
        prompt_sections.append(conv_parts[0][6:])
    elif conv_parts:
        prompt_sections.append("\n\n".join(conv_parts))

    return "\n\n".join(prompt_sections).strip()

def _parse_tool_calls(raw_text: str):
    """
    Extracts <tool_call>...</tool_call> blocks from raw_text.
    Returns (cleaned_text, tool_calls_list).
    """
    pattern = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
    tool_calls = []
    for idx, match in enumerate(pattern.finditer(raw_text)):
        try:
            obj = json.loads(match.group(1))
            name = obj.get("name", "")
            args = obj.get("arguments", {})
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            if name:
                tool_calls.append({
                    "index": idx,
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {"name": name, "arguments": args},
                })
        except Exception:
            pass
    cleaned = pattern.sub("", raw_text).strip()
    return cleaned, tool_calls

# ─── Routes ──────────────────────────────────────────────────────────────────
MODEL_LIST = [
    {"id": "deepseek-chat", "object": "model", "owned_by": "deepseek"},
    {"id": "deepseek-reasoner", "object": "model", "owned_by": "deepseek"},
]

@app.get("/models", dependencies=[Depends(verify_key)])
@app.post("/models", dependencies=[Depends(verify_key)])
@app.get("/v1/models", dependencies=[Depends(verify_key)])
@app.post("/v1/models", dependencies=[Depends(verify_key)])
@app.get("/v1/v1/models", dependencies=[Depends(verify_key)])
def list_models():
    return {"object": "list", "data": MODEL_LIST}

@app.get("/models/{model_id}", dependencies=[Depends(verify_key)])
@app.get("/v1/models/{model_id}", dependencies=[Depends(verify_key)])
def get_model(model_id: str):
    return {"id": model_id, "object": "model", "owned_by": "deepseek"}

@app.post("/chat/completions", dependencies=[Depends(verify_key)])
@app.post("/v1/chat/completions", dependencies=[Depends(verify_key)])
@app.post("/v1/v1/chat/completions", dependencies=[Depends(verify_key)])
async def chat_completions(req: Request):
    mgr = get_manager()
    try:
        body = await req.json()
    except Exception:
        body = {}

    model = body.get("model", "deepseek-chat")
    messages = body.get("messages", [])
    tools = body.get("tools")
    stream = bool(body.get("stream", True))

    # Parse thinking flag safely (handles bool or dict like {"type": "enabled"})
    raw_thinking = body.get("thinking")
    if isinstance(raw_thinking, dict):
        thinking = raw_thinking.get("type") == "enabled"
    elif isinstance(raw_thinking, bool):
        thinking = raw_thinking
    elif isinstance(body.get("enable_thinking"), bool):
        thinking = body.get("enable_thinking")
    else:
        thinking = model == "deepseek-reasoner" or "reasoner" in str(model).lower() or "thinking" in str(model).lower()
    thinking = bool(thinking)

    # Parse search flag safely
    raw_search = body.get("search", False)
    if isinstance(raw_search, dict):
        search = raw_search.get("type") == "enabled"
    else:
        search = bool(raw_search)

    prompt = _build_prompt(messages, tools=tools)
    if not prompt:
        raise HTTPException(status_code=400, detail="No message content provided")

    log_entry = {
        "time": datetime.utcnow().isoformat() + "Z",
        "model": model,
        "thinking": thinking,
        "stream": stream,
        "msg_count": len(messages),
        "tools_count": len(tools) if isinstance(tools, list) else 0,
        "prompt_len": len(prompt),
        "status": "started",
    }
    RECENT_LOGS.append(log_entry)

    def stream_generator():
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created = int(time.time())
        yield _openai_chunk(chunk_id, created, model, {"role": "assistant", "content": ""})
        try:
            lines = mgr.stream_chat(prompt, thinking=thinking, search=search)
            has_tools = bool(tools and isinstance(tools, list))
            text_buf = ""
            emitted_tool_calls = False

            for is_thinking, text in _parse_deepseek_sse(lines):
                if is_thinking:
                    yield _openai_chunk(chunk_id, created, model, {"reasoning_content": text})
                else:
                    if not has_tools:
                        yield _openai_chunk(chunk_id, created, model, {"content": text})
                    else:
                        text_buf += text
                        # If buffer has no '<', flush immediately for zero-latency streaming
                        if "<" not in text_buf:
                            yield _openai_chunk(chunk_id, created, model, {"content": text_buf})
                            text_buf = ""
                        else:
                            idx = text_buf.find("<")
                            if idx > 0:
                                yield _openai_chunk(chunk_id, created, model, {"content": text_buf[:idx]})
                                text_buf = text_buf[idx:]
                            # Check if '<' cannot possibly be '<tool_call>'
                            tag = "<tool_call>"
                            if len(text_buf) < len(tag):
                                if not tag.startswith(text_buf):
                                    yield _openai_chunk(chunk_id, created, model, {"content": text_buf})
                                    text_buf = ""
                            else:
                                if not text_buf.startswith(tag):
                                    yield _openai_chunk(chunk_id, created, model, {"content": text_buf[0]})
                                    text_buf = text_buf[1:]
                                elif "</tool_call>" in text_buf:
                                    cleaned, tcalls = _parse_tool_calls(text_buf)
                                    if cleaned:
                                        yield _openai_chunk(chunk_id, created, model, {"content": cleaned})
                                    if tcalls:
                                        emitted_tool_calls = True
                                        yield _openai_chunk(chunk_id, created, model, {"tool_calls": tcalls})
                                    text_buf = ""

            if text_buf:
                cleaned, tcalls = _parse_tool_calls(text_buf)
                if cleaned:
                    yield _openai_chunk(chunk_id, created, model, {"content": cleaned})
                if tcalls:
                    emitted_tool_calls = True
                    yield _openai_chunk(chunk_id, created, model, {"tool_calls": tcalls})

            finish_reason = "tool_calls" if emitted_tool_calls else "stop"
            yield _openai_chunk(chunk_id, created, model, {}, finish_reason=finish_reason)
            yield "data: [DONE]\n\n"
            log_entry["status"] = f"completed ({finish_reason})"
        except Exception as e:
            log_entry["status"] = f"error: {e}"
            err = {"error": {"message": str(e), "type": "server_error"}}
            yield f"data: {json.dumps(err)}\n\n"

    if stream:
        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        full_text = ""
        reasoning_text = ""
        try:
            lines = mgr.stream_chat(prompt, thinking=thinking, search=search)
            for is_thinking, text in _parse_deepseek_sse(lines):
                if is_thinking:
                    reasoning_text += text
                else:
                    full_text += text
        except Exception as e:
            log_entry["status"] = f"error: {e}"
            raise HTTPException(status_code=500, detail=str(e))

        cleaned_text, tcalls = _parse_tool_calls(full_text) if tools else (full_text, [])
        msg: dict = {"role": "assistant", "content": cleaned_text if not tcalls or cleaned_text else None}
        if reasoning_text:
            msg["reasoning_content"] = reasoning_text
        if tcalls:
            msg["tool_calls"] = [
                {"id": tc["id"], "type": "function", "function": tc["function"]}
                for tc in tcalls
            ]

        finish_reason = "tool_calls" if tcalls else "stop"
        log_entry["status"] = f"completed ({finish_reason})"
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": msg,
                "finish_reason": finish_reason,
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

# ─── Admin Routes ─────────────────────────────────────────────────────────────
ADMIN_KEY = os.getenv("ADMIN_KEY", API_KEY)

def verify_admin(
    req: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    token = creds.credentials if (creds and creds.credentials) else req.headers.get("x-api-key")
    if token != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return token

@app.get("/admin/status", dependencies=[Depends(verify_admin)])
def admin_status():
    return {"accounts": get_manager().get_status()}

@app.get("/admin/logs", dependencies=[Depends(verify_admin)])
def admin_logs():
    return {"logs": list(RECENT_LOGS)}

@app.post("/admin/token", dependencies=[Depends(verify_admin)])
def admin_update_token(body: TokenUpdate):
    ok = get_manager().update_token(body.account_id, body.token)
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True, "message": f"Token updated for account {body.account_id}"}

@app.post("/admin/accounts", dependencies=[Depends(verify_admin)])
def admin_add_account(body: NewAccount):
    acc = get_manager().add_account(body.email, body.token, body.note)
    return {"ok": True, "account": acc}

@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"service": "deepseek-proxy", "status": "running", "version": "1.1.0"}

@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    mgr = get_manager()
    accs = mgr.get_status()
    healthy_count = sum(1 for a in accs if a["healthy"])
    return {
        "status": "ok" if healthy_count > 0 else "degraded",
        "healthy_accounts": healthy_count,
        "total_accounts": len(accs),
    }

import sys as _sys
print(f"[startup] Python {_sys.version}", flush=True)
print(f"[startup] API_KEY set: {bool(os.getenv('API_KEY'))}", flush=True)
print(f"[startup] ACCOUNTS_JSON set: {bool(os.getenv('ACCOUNTS_JSON'))}", flush=True)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
