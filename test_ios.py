"""Test if iOS headers bypass PoW requirement entirely"""
import sys, json, requests
sys.stdout.reconfigure(encoding='utf-8')

TOKEN = "duDuXK66sHDmYMETfbm8NeHBASg3mGIlXuSyazTigvdrCGpGxe38UqG4XjgF16Wr"

# iOS headers (what aiodeepseek uses internally)
IOS_HEADERS = {
    "User-Agent": "DeepSeek/2 CFNetwork/1568.100.1 Darwin/24.0.0",
    "Authorization": f"Bearer {TOKEN}",
    "x-client-bundle-id": "com.deepseek.chat",
    "x-client-locale": "en_US",
    "x-client-platform": "ios",
    "x-client-version": "2.0.4",
}

sess = requests.Session()

# 1. Does users/current work with iOS headers?
r = sess.get("https://chat.deepseek.com/api/v0/users/current", headers=IOS_HEADERS)
print(f"[Auth] {r.status_code}: {r.text[:100]}")

# 2. Can we create session without web headers?
r = sess.post("https://chat.deepseek.com/api/v0/chat_session/create",
    json={}, headers={**IOS_HEADERS, "Content-Type": "application/json"})
print(f"[Session] {r.status_code}: {r.text[:150]}")
if r.status_code == 200:
    sid = r.json()["data"]["biz_data"]["chat_session"]["id"]
    print(f"  sid: {sid}")

    # 3. Try chat WITHOUT any PoW header
    print("\n[Chat] Trying WITHOUT PoW...")
    r2 = sess.post("https://chat.deepseek.com/api/v0/chat/completion",
        json={
            "chat_session_id": sid,
            "parent_message_id": None,
            "model_type": "default",
            "prompt": "say hi",
            "ref_file_ids": [],
            "thinking_enabled": False,
            "search_enabled": False,
            "action": None,
            "preempt": False,
        },
        headers={**IOS_HEADERS, "Content-Type": "application/json"},
        stream=True, timeout=15)
    print(f"  Status: {r2.status_code}")
    for line in r2.iter_lines():
        if line:
            decoded = line.decode('utf-8','replace')
            print(f"  {decoded[:200]}")
            if "FINISHED" in decoded or "close" in decoded:
                break
