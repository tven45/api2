import sys, json, requests, uuid, base64
sys.stdout.reconfigure(encoding="utf-8")

TOKEN4 = "FGOyZpPSIqG79EY+IN4iaCtVXHspanzk6tWj+Id7/LjtGPrd8vRYuzJUsq0euUKd"

r = requests.get("https://chat.deepseek.com/api/v0/users/current",
    headers={"Authorization": f"Bearer {TOKEN4}", "x-client-platform": "web", "x-client-bundle-id": "com.deepseek.chat"})

print("Status:", r.status_code)
if r.status_code == 200:
    b = r.json()["data"]["biz_data"]
    email = b.get("email", "?")
    print("Email:", email)

    accs = json.loads(open("accounts.json").read())
    if not any(a["id"] == 4 for a in accs):
        accs.append({
            "id": 4, "email": email, "token": TOKEN4,
            "device_id": str(uuid.uuid4()), "session_id": None,
            "session_created_at": 0, "healthy": True,
            "last_checked": 0, "last_used": 0, "requests_today": 0, "note": "acc4"
        })
        open("accounts.json", "w").write(json.dumps(accs, indent=2))
        print("Added as account #4!")

    accs = json.loads(open("accounts.json").read())
    print("\nAll accounts:")
    for a in accs:
        print(f"  [{a['id']}] {a['email']}")

    encoded = base64.b64encode(json.dumps(accs).encode()).decode()
    print("\nACCOUNTS_JSON:", encoded)
else:
    print("FAILED:", r.text[:200])
