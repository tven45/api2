"""
Account Manager — 5-account round-robin rotation with health checks

Persistence strategy:
  - Local:  reads/writes accounts.json
  - Render: reads ACCOUNTS_JSON env var (base64 JSON) on startup;
            pushes updates back via Render API so tokens survive restarts
"""
import json, time, threading, os, requests, base64, uuid
from pathlib import Path
from datetime import datetime
from pow_solver import solve_pow

# ─── Config ──────────────────────────────────────────────────────────────────
ACCOUNTS_FILE   = Path(__file__).parent / "accounts.json"
NOTIFY_WEBHOOK  = os.getenv("NOTIFY_WEBHOOK", "")
CHECK_INTERVAL  = 3600   # health check every 1 hour
SESSION_TTL     = 86400  # recreate session after 24h

# Render API — for persisting token updates across restarts
RENDER_API_KEY     = os.getenv("RENDER_API_KEY", "")
RENDER_SERVICE_ID  = os.getenv("RENDER_SERVICE_ID", "")

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 16; Pixel 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Mobile Safari/537.36",
    "x-client-bundle-id": "com.deepseek.chat",
    "x-client-locale": "en_US",
    "x-client-platform": "web",
    "x-client-timezone-offset": "19800",
    "x-client-version": "2.5.0",
    "x-device-model": "",
    "Origin": "https://chat.deepseek.com",
    "Referer": "https://chat.deepseek.com/",
}

# ─── Account Schema ───────────────────────────────────────────────────────────
# Each account:
# { id, email, token, device_id, session_id, session_created_at,
#   healthy, last_checked, last_used, requests_today, note }
# ─────────────────────────────────────────────────────────────────────────────

class AccountManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._accounts: list = []
        self._rr_index = 0
        self._load()
        self._start_health_monitor()

    # ── Persistence ───────────────────────────────────────────────────────
    def _load(self):
        """Load accounts from: env var ACCOUNTS_JSON > accounts.json file"""
        env_json = os.getenv("ACCOUNTS_JSON", "")
        if env_json:
            try:
                decoded = base64.b64decode(env_json).decode()
                self._accounts = json.loads(decoded)
                # Also write to disk so other code can read it
                ACCOUNTS_FILE.write_text(json.dumps(self._accounts, indent=2))
                print(f"[AccountManager] Loaded {len(self._accounts)} accounts from ACCOUNTS_JSON env var")
                return
            except Exception as e:
                print(f"[AccountManager] ACCOUNTS_JSON parse error: {e}")

        if ACCOUNTS_FILE.exists():
            self._accounts = json.loads(ACCOUNTS_FILE.read_text())
            print(f"[AccountManager] Loaded {len(self._accounts)} accounts from accounts.json")
        else:
            self._accounts = []
            print("[AccountManager] ⚠️  No accounts found — add via /admin/accounts")

    def _save(self):
        """Save to disk AND push to Render env var for persistence across restarts."""
        ACCOUNTS_FILE.write_text(json.dumps(self._accounts, indent=2))
        self._push_to_render()

    def _push_to_render(self):
        """Update ACCOUNTS_JSON env var on Render so tokens survive restarts."""
        if not RENDER_API_KEY or not RENDER_SERVICE_ID:
            return
        try:
            encoded = base64.b64encode(
                json.dumps(self._accounts).encode()
            ).decode()
            resp = requests.put(
                f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars",
                headers={
                    "Authorization": f"Bearer {RENDER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=[{"key": "ACCOUNTS_JSON", "value": encoded}],
                timeout=10,
            )
            if resp.status_code in (200, 201):
                print("[AccountManager] Tokens persisted to Render env var ✅")
            else:
                print(f"[AccountManager] Render API: {resp.status_code} {resp.text[:100]}")
        except Exception as e:
            print(f"[AccountManager] Render persist error: {e}")

    # ── Account CRUD ──────────────────────────────────────────────────────
    def add_account(self, email: str, token: str, note: str = "") -> dict:
        with self._lock:
            acc = {
                "id": max((a["id"] for a in self._accounts), default=0) + 1,
                "email": email,
                "token": token,
                "device_id": str(uuid.uuid4()),
                "session_id": None,
                "session_created_at": 0,
                "healthy": True,
                "last_checked": 0,
                "last_used": 0,
                "requests_today": 0,
                "note": note,
            }
            self._accounts.append(acc)
            self._save()
            return acc

    def update_token(self, account_id: int, new_token: str) -> bool:
        with self._lock:
            for acc in self._accounts:
                if acc["id"] == account_id:
                    acc["token"] = new_token
                    acc["healthy"] = True
                    acc["session_id"] = None
                    acc["session_created_at"] = 0
                    self._save()
                    print(f"[AccountManager] Token updated for account {account_id}")
                    return True
        return False

    def get_status(self) -> list:
        with self._lock:
            return [
                {
                    "id": a["id"],
                    "email": a["email"],
                    "healthy": a["healthy"],
                    "last_checked": datetime.fromtimestamp(a["last_checked"]).isoformat() if a["last_checked"] else "never",
                    "last_used": datetime.fromtimestamp(a["last_used"]).isoformat() if a["last_used"] else "never",
                    "requests_today": a["requests_today"],
                    "note": a["note"],
                }
                for a in self._accounts
            ]

    def reset_daily_counts(self):
        with self._lock:
            for acc in self._accounts:
                acc["requests_today"] = 0
            self._save()

    # ── Request headers ───────────────────────────────────────────────────
    def _make_headers(self, acc: dict) -> dict:
        return {
            **BASE_HEADERS,
            "Authorization": f"Bearer {acc['token']}",
            "x-device-id": acc["device_id"],
        }

    # ── Session Management ─────────────────────────────────────────────────
    def _ensure_session(self, acc: dict) -> str | None:
        now = time.time()
        if acc.get("session_id") and (now - acc["session_created_at"]) < SESSION_TTL:
            return acc["session_id"]
        try:
            r = requests.post(
                "https://chat.deepseek.com/api/v0/chat_session/create",
                json={},
                headers={**self._make_headers(acc), "Content-Type": "application/json"},
                timeout=15,
            )
            if r.status_code == 200:
                sid = r.json()["data"]["biz_data"]["chat_session"]["id"]
                acc["session_id"] = sid
                acc["session_created_at"] = now
                return sid
            elif r.status_code == 401:
                self._mark_unhealthy(acc, "401 on session create")
        except Exception as e:
            print(f"[AccountManager] Session create error acc#{acc['id']}: {e}")
        return None

    # ── PoW ───────────────────────────────────────────────────────────────
    def _get_pow_header(self, acc: dict, target="/api/v0/chat/completion") -> str | None:
        try:
            r = requests.post(
                "https://chat.deepseek.com/api/v0/chat/create_pow_challenge",
                json={"target_path": target},
                headers={**self._make_headers(acc), "Content-Type": "application/json"},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            ch = r.json()["data"]["biz_data"]["challenge"]
            nonce = solve_pow(ch["salt"], ch["expire_at"], ch["challenge"], ch["difficulty"])
            if nonce < 0:
                return None
            payload = json.dumps({
                "algorithm": ch["algorithm"],
                "challenge": ch["challenge"],
                "salt": ch["salt"],
                "answer": nonce,
                "signature": ch["signature"],
                "target_path": target,
            }, separators=(",", ":")).encode()
            return base64.b64encode(payload).decode()
        except Exception as e:
            print(f"[AccountManager] PoW error acc#{acc['id']}: {e}")
            return None

    # ── Health ────────────────────────────────────────────────────────────
    def _mark_unhealthy(self, acc: dict, reason: str):
        was_healthy = acc["healthy"]
        acc["healthy"] = False
        self._save()
        if was_healthy:
            self._notify(
                f"⚠️ DeepSeek account #{acc['id']} ({acc['email']}) is DOWN\n"
                f"Reason: {reason}\n"
                f"Run: `python grab_token.py --account-id {acc['id']}`"
            )

    def _check_account(self, acc: dict) -> bool:
        try:
            r = requests.get(
                "https://chat.deepseek.com/api/v0/users/current",
                headers=self._make_headers(acc),
                timeout=10,
            )
            healthy = r.status_code == 200
            was_healthy = acc["healthy"]
            acc["healthy"] = healthy
            acc["last_checked"] = time.time()
            self._save()
            if was_healthy and not healthy:
                self._notify(f"⚠️ Account #{acc['id']} ({acc['email']}) token expired!\nRun: python grab_token.py --account-id {acc['id']}")
            elif not was_healthy and healthy:
                self._notify(f"✅ Account #{acc['id']} ({acc['email']}) is healthy again!")
            return healthy
        except Exception as e:
            print(f"[AccountManager] Health check error acc#{acc['id']}: {e}")
            return acc["healthy"]

    def _health_monitor_loop(self):
        while True:
            time.sleep(CHECK_INTERVAL)
            with self._lock:
                accounts_copy = list(self._accounts)
            for acc in accounts_copy:
                self._check_account(acc)

    def _start_health_monitor(self):
        t = threading.Thread(target=self._health_monitor_loop, daemon=True)
        t.start()

    def _notify(self, message: str):
        print(f"[NOTIFY] {message}")
        if not NOTIFY_WEBHOOK:
            return
        try:
            if "discord.com/api/webhooks" in NOTIFY_WEBHOOK:
                requests.post(NOTIFY_WEBHOOK, json={"content": f"🤖 **DeepSeek Proxy**\n{message}"}, timeout=5)
            elif "api.telegram.org" in NOTIFY_WEBHOOK:
                requests.post(NOTIFY_WEBHOOK, json={"text": message}, timeout=5)
        except Exception:
            pass

    # ── Round-Robin Stream ────────────────────────────────────────────────
    def stream_chat(
        self,
        prompt: str,
        parent_message_id=None,
        thinking: bool = False,
        search: bool = False,
        model: str = "default",
    ):
        """
        Yields raw SSE lines from DeepSeek.
        Rotates across healthy accounts, retries on failure.
        """
        with self._lock:
            healthy = [a for a in self._accounts if a["healthy"]]

        if not healthy:
            raise RuntimeError("No healthy accounts available")

        tried = set()
        for _ in range(len(healthy)):
            with self._lock:
                avail = [a for a in self._accounts if a["healthy"] and a["id"] not in tried]
            if not avail:
                break

            acc = avail[self._rr_index % len(avail)]
            self._rr_index = (self._rr_index + 1) % max(len(avail), 1)
            tried.add(acc["id"])

            session_id = self._ensure_session(acc)
            if not session_id:
                continue

            pow_header = self._get_pow_header(acc)
            if not pow_header:
                continue

            try:
                r = requests.post(
                    "https://chat.deepseek.com/api/v0/chat/completion",
                    json={
                        "chat_session_id": session_id,
                        "parent_message_id": parent_message_id,
                        "model_type": model,
                        "prompt": prompt,
                        "ref_file_ids": [],
                        "thinking_enabled": thinking,
                        "search_enabled": search,
                        "action": None,
                        "preempt": False,
                    },
                    headers={
                        **self._make_headers(acc),
                        "Content-Type": "application/json",
                        "x-ds-pow-response": pow_header,
                    },
                    stream=True,
                    timeout=120,
                )

                if r.status_code == 401:
                    self._mark_unhealthy(acc, "401 Unauthorized")
                    continue

                if r.status_code != 200:
                    print(f"[AccountManager] HTTP {r.status_code} acc#{acc['id']}: {r.text[:100]}")
                    continue

                with self._lock:
                    acc["last_used"] = time.time()
                    acc["requests_today"] = acc.get("requests_today", 0) + 1

                yield from r.iter_lines()
                return

            except requests.exceptions.Timeout:
                print(f"[AccountManager] Timeout on acc#{acc['id']}")
            except Exception as e:
                print(f"[AccountManager] Stream error acc#{acc['id']}: {e}")

        raise RuntimeError("All accounts failed — check token health")


# ── Singleton ─────────────────────────────────────────────────────────────────
_manager: AccountManager | None = None

def get_manager() -> AccountManager:
    global _manager
    if _manager is None:
        _manager = AccountManager()
    return _manager
