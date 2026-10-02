import sys, json, requests
sys.stdout.reconfigure(encoding="utf-8")

accounts = json.load(open("accounts.json"))
BASE = {
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36",
    "x-client-bundle-id": "com.deepseek.chat",
    "x-client-locale": "en_US",
    "x-client-platform": "web",
    "x-client-version": "2.5.0",
    "x-device-model": "",
    "Origin": "https://chat.deepseek.com",
    "Referer": "https://chat.deepseek.com/",
}

print("Checking all accounts...")
for acc in accounts:
    h = dict(BASE)
    h["Authorization"] = "Bearer " + acc["token"]
    h["x-device-id"] = acc["device_id"]
    r = requests.get("https://chat.deepseek.com/api/v0/users/current", headers=h)
    ok = r.status_code == 200
    print(f"  [{acc['id']}] {acc['email']} -> {'OK' if ok else 'FAIL ' + str(r.status_code)}")
