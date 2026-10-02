import sys, json, requests, uuid, base64
sys.stdout.reconfigure(encoding="utf-8")

TOKEN5 = "QOgG1IqNA+OTvt4rQPUSmSZLlyMrTteOAs2Ak1DvnyZxO6X7lTAKp6xdk0Ic5kbo"

r = requests.get("https://chat.deepseek.com/api/v0/users/current",
    headers={"Authorization": f"Bearer {TOKEN5}", "x-client-platform": "web", "x-client-bundle-id": "com.deepseek.chat"})

print("Status:", r.status_code)
if r.status_code == 200:
    b = r.json()["data"]["biz_data"]
    email = b.get("email", "?")
    print("Email:", email)

    accs = json.loads(open("accounts.json").read())
    if not any(a["id"] == 5 for a in accs):
        accs.append({
            "id": 5, "email": email, "token": TOKEN5,
            "device_id": str(uuid.uuid4()), "session_id": None,
            "session_created_at": 0, "healthy": True,
            "last_checked": 0, "last_used": 0, "requests_today": 0, "note": "acc5"
        })
        open("accounts.json", "w").write(json.dumps(accs, indent=2))
        print("Added as account #5!")

    accs = json.loads(open("accounts.json").read())
    print("\nAll accounts:")
    for a in accs:
        print(f"  [{a['id']}] {a['email']}")

    encoded = base64.b64encode(json.dumps(accs).encode()).decode()
    print("\nFINAL ACCOUNTS_JSON for Render:")
    print(encoded)
else:
    print("FAILED:", r.text[:200])
