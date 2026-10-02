import requests, json, time

# Test non-streaming first
resp = requests.post(
    "http://localhost:8000/v1/chat/completions",
    headers={"Authorization": "Bearer sk-test-123", "Content-Type": "application/json"},
    json={
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "say just the word: hello"}
        ],
        "stream": False
    },
    timeout=30
)

print(f"Status: {resp.status_code}")
if resp.status_code == 200:
    data = resp.json()
    print(f"Response: {data['choices'][0]['message']['content']}")
    print(f"\nFull response:\n{json.dumps(data, indent=2)}")
else:
    print(f"Error: {resp.text}")
