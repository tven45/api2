import json, uuid
from pathlib import Path

acc_file = Path("accounts.json")
accounts = json.loads(acc_file.read_text())

acc2 = {
    "id": 2,
    "email": "qw****46@gmail.com",
    "token": "Hku+wOHa/ezCtxzhyGwCQM+VdsAtC3giMJQQosDknY/I8V6pHCBiJxWYL9GE8tAT",
    "device_id": "1e13b9af-a86e-4086-bed0-3ac7ab09c964",
    "session_id": None,
    "session_created_at": 0,
    "healthy": True,
    "last_checked": 0,
    "last_used": 0,
    "requests_today": 0,
    "note": "acc2"
}

# Only add if not already there
if not any(a["id"] == 2 for a in accounts):
    accounts.append(acc2)
    acc_file.write_text(json.dumps(accounts, indent=2))
    print("Added account 2!")
else:
    print("Account 2 already exists, updating token...")
    for a in accounts:
        if a["id"] == 2:
            a["token"] = acc2["token"]
            a["device_id"] = acc2["device_id"]
    acc_file.write_text(json.dumps(accounts, indent=2))

print("\nCurrent accounts:")
accounts = json.loads(acc_file.read_text())
for a in accounts:
    print(f"  [{a['id']}] {a['email']} — token={a['token'][:15]}...")

# Generate new ACCOUNTS_JSON for Render
import base64
encoded = base64.b64encode(json.dumps(accounts).encode()).decode()
print("\nACCOUNTS_JSON for Render:")
print(encoded)
