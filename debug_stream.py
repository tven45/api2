import sys
sys.path.insert(0, r"g:\project\deepseek-proxy")
from account_manager import get_manager

mgr = get_manager()
print("Streaming chat...")
lines = mgr.stream_chat("say just the word hello")
count = 0
for line in lines:
    if line:
        decoded = line.decode("utf-8", "replace") if isinstance(line, bytes) else line
        print(repr(decoded))  # full line
        count += 1
        if count > 20:
            break
print(f"\nTotal lines: {count}")
