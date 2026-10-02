#!/usr/bin/env python3
"""
setup_accounts.py — Add your 5 DeepSeek accounts interactively

Run once:  python setup_accounts.py

For each account, it will either:
  A) Auto-grab token from Chrome (if you're logged in)
  B) Let you paste the token manually from browser DevTools
"""
import json, sys
from pathlib import Path

ACCOUNTS_FILE = Path(__file__).parent / "accounts.json"

def get_token_from_devtools():
    print("\n  Manual token extraction steps:")
    print("  1. Open Chrome → chat.deepseek.com → make sure you're logged in")
    print("  2. Press F12 → Network tab → send any message")
    print("  3. Find 'users/current' request → Response tab")
    print("  4. Copy the 'token' field value")
    print()
    token = input("  Paste token here: ").strip()
    return token

def add_account(accounts: list, acc_num: int) -> dict:
    print(f"\n{'─'*50}")
    print(f"  Account #{acc_num}")
    print(f"{'─'*50}")
    email = input(f"  Email (Google account): ").strip()
    note  = input(f"  Label/note (optional): ").strip()

    print(f"\n  How to get token for {email}?")
    print("  [1] Auto-grab from Chrome (browser must be open & logged in)")
    print("  [2] Paste manually from DevTools")
    choice = input("  Choice [1/2]: ").strip()

    token = None
    if choice == "1":
        try:
            from grab_token import grab_token
            profile = input("  Chrome profile name [Default]: ").strip() or "Default"
            token = grab_token(acc_num, profile)
        except Exception as e:
            print(f"  Auto-grab failed: {e}")
            print("  Falling back to manual...")
            token = get_token_from_devtools()
    else:
        token = get_token_from_devtools()

    if not token:
        print("  ❌ No token provided, skipping")
        return None

    acc = {
        "id": acc_num,
        "email": email,
        "token": token,
        "device_id": __import__("uuid").uuid4().__str__(),
        "session_id": None,
        "session_created_at": 0,
        "healthy": True,
        "last_checked": 0,
        "last_used": 0,
        "requests_today": 0,
        "note": note,
    }
    print(f"  ✅ Account #{acc_num} ({email}) added!")
    return acc

def main():
    print("╔══════════════════════════════════════════╗")
    print("║   DeepSeek Proxy — Account Setup         ║")
    print("╚══════════════════════════════════════════╝\n")

    if ACCOUNTS_FILE.exists():
        accounts = json.loads(ACCOUNTS_FILE.read_text())
        print(f"Found existing accounts.json with {len(accounts)} account(s)")
        print("[1] Add more accounts")
        print("[2] Replace all accounts")
        print("[3] Cancel")
        choice = input("Choice: ").strip()
        if choice == "3":
            sys.exit(0)
        if choice == "2":
            accounts = []
        start_id = max((a["id"] for a in accounts), default=0) + 1
    else:
        accounts = []
        start_id = 1

    num_accounts = int(input(f"\nHow many accounts to add? [5]: ").strip() or "5")

    for i in range(num_accounts):
        acc = add_account(accounts, start_id + i)
        if acc:
            accounts.append(acc)

    ACCOUNTS_FILE.write_text(json.dumps(accounts, indent=2))
    print(f"\n✅ Saved {len(accounts)} accounts to accounts.json")
    print("\nNext: run the server with:")
    print("  API_KEY=your-secret python main.py")

if __name__ == "__main__":
    main()
