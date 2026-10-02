"""
grab_token.py — Auto-extract DeepSeek userToken from your Chrome browser

Usage:
  python grab_token.py                        # grab for account 1
  python grab_token.py --account-id 2         # grab for account 2
  python grab_token.py --account-id 1 --push  # push to Render server

Requires:
  pip install playwright
  playwright install chrome
"""
import sys, os, json, argparse, requests, base64
from pathlib import Path

SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")
ADMIN_KEY  = os.getenv("ADMIN_KEY", "sk-deepseek-proxy-change-me")

# Your existing Chrome User Data dir — already logged into DeepSeek via Google
CHROME_PROFILE = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")

def grab_token_from_chrome(account_id: int, profile_name: str = "Default") -> str:
    from playwright.sync_api import sync_playwright

    print(f"\n[grab_token] Opening Chrome profile: {profile_name}")
    print(f"[grab_token] (Your Google session is still active — no login needed)\n")

    with sync_playwright() as p:
        token = None

        ctx = p.chromium.launch_persistent_context(
            user_data_dir=CHROME_PROFILE,
            channel="chrome",
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.new_page()

        def on_response(response):
            nonlocal token
            if "users/current" in response.url and response.status == 200:
                try:
                    data = response.json()
                    t = data.get("data", {}).get("biz_data", {}).get("token")
                    if t:
                        token = t
                        print(f"[grab_token] ✅ Token captured from API response!")
                except Exception:
                    pass

        page.on("response", on_response)
        page.goto("https://chat.deepseek.com", wait_until="domcontentloaded", timeout=30000)

        # Wait up to 10s for token to appear in API response
        page.wait_for_timeout(3000)
        for _ in range(70):  # up to 7 more seconds
            if token:
                break
            page.wait_for_timeout(100)

        # Fallback: trigger an API call by reloading
        if not token:
            print("[grab_token] Triggering reload to capture token...")
            page.reload(wait_until="domcontentloaded")
            for _ in range(50):
                if token:
                    break
                page.wait_for_timeout(100)

        ctx.close()

    if not token:
        print("\n[grab_token] ❌ Could not auto-capture token.")
        print("  Manual fallback:")
        print("  1. Open Chrome → chat.deepseek.com")
        print("  2. F12 → Network → filter 'users/current'")
        print("  3. Click that request → Response → copy 'token' value")
        token = input("\n  Paste token: ").strip()

    if not token:
        print("No token provided. Exiting.")
        sys.exit(1)

    return token

def push_to_server(account_id: int, token: str) -> bool:
    """Push the new token to the running proxy server."""
    try:
        resp = requests.post(
            f"{SERVER_URL}/admin/token",
            json={"account_id": account_id, "token": token},
            headers={"Authorization": f"Bearer {ADMIN_KEY}"},
            timeout=10,
        )
        if resp.status_code == 200:
            print(f"[grab_token] ✅ Token pushed to server")
            return True
        else:
            print(f"[grab_token] Server returned {resp.status_code}: {resp.text}")
    except requests.exceptions.ConnectionError:
        print(f"[grab_token] Server not reachable at {SERVER_URL}")
    return False

def save_locally(account_id: int, token: str):
    """Directly update local accounts.json."""
    acc_file = Path(__file__).parent / "accounts.json"
    if acc_file.exists():
        accounts = json.loads(acc_file.read_text())
        for acc in accounts:
            if acc["id"] == account_id:
                acc["token"] = token
                acc["healthy"] = True
                acc["session_id"] = None
                acc["session_created_at"] = 0
                acc_file.write_text(json.dumps(accounts, indent=2))
                print(f"[grab_token] ✅ accounts.json updated (account #{account_id})")
                return
        print(f"[grab_token] Account #{account_id} not found in accounts.json")
    # Also print ACCOUNTS_JSON for pasting into Render env var
    if acc_file.exists():
        accounts = json.loads(acc_file.read_text())
        encoded = base64.b64encode(json.dumps(accounts).encode()).decode()
        print(f"\n📋 ACCOUNTS_JSON for Render env var:\n{encoded}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grab DeepSeek userToken from Chrome")
    parser.add_argument("--account-id", type=int, default=1)
    parser.add_argument("--profile", default="Default", help="Chrome profile folder name")
    parser.add_argument("--push", action="store_true", help="Push to Render server via API")
    parser.add_argument("--manual", action="store_true", help="Skip Chrome, paste token manually")
    args = parser.parse_args()

    print("╔══════════════════════════════════╗")
    print("║  DeepSeek Token Refresher        ║")
    print(f"║  Account #{args.account_id:<26}║")
    print("╚══════════════════════════════════╝")

    if args.manual:
        token = input("\nPaste token: ").strip()
    else:
        token = grab_token_from_chrome(args.account_id, args.profile)

    print(f"\nToken: {token[:20]}...{token[-6:]}")

    # Try to push to server first
    pushed = False
    if args.push or SERVER_URL != "http://localhost:8000":
        pushed = push_to_server(args.account_id, token)

    # Always update locally too
    save_locally(args.account_id, token)

    if not pushed:
        print(f"\n📋 Manual steps if server isn't running:")
        print(f"  1. Go to Render dashboard → deepseek-proxy → Environment")
        print(f"  2. Update ACCOUNTS_JSON with the value printed above")
        print(f"  OR restart server — it will pick up from ACCOUNTS_JSON env var")

    print("\n✅ Done!")
