import sys, json, requests, uuid, base64
sys.stdout.reconfigure(encoding="utf-8")

TOKEN3 = "iZsTNHSgJI5V4C6hK6Q4pxrg8RD8lXMT4v386ye7vFkOK9//+zYIw+2xHFFsp65B"

# Verify
r = requests.get("https://chat.deepseek.com/api/v0/users/current",
    headers={"Authorization": f"Bearer {TOKEN3}", "x-client-platform": "web", "x-client-bundle-id": "com.deepseek.chat"})

print("Status:", r.status_code)
if r.status_code == 200:
    b = r.json()["data"]["biz_data"]
    email = b.get("email", "?")
    uid = b.get("id", "?")
    print("Email:", email)
    print("ID:", uid[:16])

    # Add to accounts.json
    accs = json.loads(open("accounts.json").read())
    if not any(a["id"] == 3 for a in accs):
        accs.append({
            "id": 3,
            "email": email,
            "token": TOKEN3,
            "device_id": str(uuid.uuid4()),
            "session_id": None,
            "session_created_at": 0,
            "healthy": True,
            "last_checked": 0,
            "last_used": 0,
            "requests_today": 0,
            "note": "acc3"
        })
        open("accounts.json", "w").write(json.dumps(accs, indent=2))
        print("\nAdded as account #3!")

    accs = json.loads(open("accounts.json").read())
    print("\nAll accounts:")
    for a in accs:
        print(f"  [{a['id']}] {a['email']}")

    encoded = base64.b64encode(json.dumps(accs).encode()).decode()
    print("\nACCOUNTS_JSON (updated):")
    print(encoded)
else:
    print("FAILED:", r.text[:200])
