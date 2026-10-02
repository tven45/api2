"""
DeepSeek Web Proxy — OpenAI-compatible API
POST /v1/chat/completions  → streams DeepSeek web responses
GET  /v1/models            → returns available models
POST /admin/token          → update a token (from grab_token.py)
GET  /admin/status         → health of all accounts
POST /admin/accounts       → add a new account
"""
import json, os, time, uuid
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, List
from account_manager import get_manager

app = FastAPI(title="DeepSeek Web Proxy", version="1.0.0")
security = HTTPBearer()

API_KEY = os.getenv("API_KEY", "sk-deepseek-proxy-change-me")

# ─── Auth ────────────────────────────────────────────────────────────────────
def verify_key(creds: HTTPAuthorizationCredentials = Depends(security)):
    if creds.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return creds.credentials

# ─── Models ──────────────────────────────────────────────────────────────────
from typing import Optional, List, Any

# ─── Models ──────────────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str
    content: Any  # Accept string or list of dicts (OpenAI Vision standard)

class ChatCompletionRequest(BaseModel):
    model: str = "deepseek-chat"
    messages: List[ChatMessage]
    stream: bool = True
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    thinking: Optional[bool] = False    # DeepSeek R1 thinking mode
    search: Optional[bool] = False      # DeepSeek web search


class TokenUpdate(BaseModel):
    account_id: int
    token: str

class NewAccount(BaseModel):
    email: str
    token: str
    note: str = ""

# ─── SSE Helpers ─────────────────────────────────────────────────────────────
def _openai_chunk(content: str, model: str, finish: bool = False) -> str:
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {} if finish else {"content": content},
            "finish_reason": "stop" if finish else None,
        }],
    }
    return f"data: {json.dumps(chunk)}\n\n"

def _parse_deepseek_sse(lines_iter):
    """
    Generator that yields text chunks from DeepSeek SSE stream.
    DeepSeek SSE formats observed:
      data: {"v": " text"}                              ← delta token (most common)
      data: {"p": "response/fragments/-1/content",      ← first token append
              "o": "APPEND", "v": "!"}
      data: {"v": {"response": {..., "fragments": [...]}}} ← full init object
      data: {"p": "response/status", "o": "SET", "v": "FINISHED"}  ← done
    """
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

        # ── Delta string token: {"v": " hello"} ──────────────────────────
        if isinstance(v, str) and v and not p:
            yield v

        # ── Patch append: {"p": "...", "o": "APPEND", "v": "text"} ──────
        elif o == "APPEND" and isinstance(v, str) and v:
            yield v

        # ── Full response init: {"v": {"response": {...}}} ───────────────
        elif isinstance(v, dict) and "response" in v:
            resp = v["response"]
            for frag in resp.get("fragments", []):
                c = frag.get("content", "")
                ftype = frag.get("type", "")
                # Accept TEXT, RESPONSE, and any non-THINK type
                if c and ftype != "THINK":
                    yield c

        # ── Finished ─────────────────────────────────────────────────────
        if p == "response/status" and v == "FINISHED":
            return
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and item.get("p") == "quasi_status" and item.get("v") == "FINISHED":
                    return

# ─── Routes ──────────────────────────────────────────────────────────────────
@app.get("/v1/models", dependencies=[Depends(verify_key)])
def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "deepseek-chat", "object": "model", "owned_by": "deepseek"},
            {"id": "deepseek-reasoner", "object": "model", "owned_by": "deepseek"},
        ],
    }

@app.post("/v1/chat/completions", dependencies=[Depends(verify_key)])
async def chat_completions(req: Request):
    mgr = get_manager()
    try:
        body = await req.json()
    except Exception:
        body = {}

    model = body.get("model", "deepseek-chat")
    messages = body.get("messages", [])
    stream = body.get("stream", True)
    thinking = body.get("thinking", model == "deepseek-reasoner")
    search = body.get("search", False)

    # Convert OpenAI messages → single prompt (web API is single-turn per message)
    def _extract_text(content: Any) -> str:
        if isinstance(content, str): return content
        if isinstance(content, list):
            return " ".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
        return str(content)

    system_parts = [_extract_text(m.get("content", "")) for m in messages if m.get("role") == "system"]
    user_parts   = [_extract_text(m.get("content", "")) for m in messages if m.get("role") == "user"]

    if not user_parts or not any(user_parts):
        raise HTTPException(status_code=400, detail="No user message provided")

    # Build prompt: system context + conversation history + latest user message
    prompt_parts = []
    if system_parts:
        prompt_parts.append(f"System: {' '.join(system_parts)}")
    for m in messages[:-1]:  # history
        role = "User" if m.get("role") == "user" else "Assistant"
        prompt_parts.append(f"{role}: {_extract_text(m.get('content', ''))}")
    
    prompt_parts.append(_extract_text(messages[-1].get("content", "")))  # latest user message
    prompt = "\n\n".join(prompt_parts)

    def stream_generator():
        try:
            # Send SSE keepalive comment every ~5s to prevent Render 30s timeout
            # during PoW solve and session creation
            yield ": keep-alive\n\n"
            lines = mgr.stream_chat(prompt, thinking=thinking, search=search)
            for text in _parse_deepseek_sse(lines):
                yield _openai_chunk(text, model)
            yield _openai_chunk("", model, finish=True)
            yield "data: [DONE]\n\n"
        except Exception as e:
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
        # Non-streaming: collect all chunks but use streaming internally
        # to avoid Render's 30-second response timeout
        full_text = ""
        try:
            lines = mgr.stream_chat(prompt, thinking=thinking, search=search)
            for text in _parse_deepseek_sse(lines):
                full_text += text
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": full_text},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

# ─── Admin Routes ─────────────────────────────────────────────────────────────
ADMIN_KEY = os.getenv("ADMIN_KEY", API_KEY)  # use same key or set separate

def verify_admin(creds: HTTPAuthorizationCredentials = Depends(security)):
    if creds.credentials != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return creds.credentials

@app.get("/admin/status", dependencies=[Depends(verify_admin)])
def admin_status():
    return {"accounts": get_manager().get_status()}

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

@app.get("/")
def root():
    return {"service": "deepseek-proxy", "status": "running", "version": "1.0.0"}

@app.get("/health")
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

