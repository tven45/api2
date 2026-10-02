"""
PoW Solver — DeepSeekHashV1
Priority:
  1. keccak_pow.so  — portable C build (Render/Linux, no AVX2)
  2. aiodeepseek    — Windows C++ AVX2 solver
  3. Pure Python    — correct but slow, last resort
"""
import ctypes, os, struct, sys, subprocess
from pathlib import Path

# ─── 1. Try compiled C solver — compile at runtime if not found ────────────
_c_lib = None
_HERE  = Path(__file__).resolve().parent
_CWD   = Path(os.getcwd()).resolve()

def _try_load_so(p: Path) -> bool:
    global _c_lib
    try:
        lib = ctypes.CDLL(str(p))
        lib.solve_pow_c.restype  = ctypes.c_longlong
        lib.solve_pow_c.argtypes = [ctypes.c_char_p, ctypes.c_int,
                                     ctypes.c_char_p, ctypes.c_int]
        _c_lib = lib
        print(f"[PoW] C solver loaded: {p}", flush=True)
        return True
    except Exception as e:
        print(f"[PoW] Load failed ({p}): {e}", flush=True)
        return False

def _compile_c_solver() -> bool:
    """Compile keccak_pow.c at runtime — fallback if build.sh didn't persist the .so"""
    for c_src in [_HERE / "keccak_pow.c", _CWD / "keccak_pow.c"]:
        if c_src.exists():
            out = _HERE / "keccak_pow.so"
            print(f"[PoW] Compiling {c_src.name} -> {out.name} ...", flush=True)
            try:
                r = subprocess.run(
                    ["gcc", "-O3", "-shared", "-fPIC", "-o", str(out), str(c_src)],
                    capture_output=True, text=True, timeout=60
                )
                if r.returncode == 0:
                    print("[PoW] Compiled OK", flush=True)
                    return _try_load_so(out)
                else:
                    print(f"[PoW] gcc failed: {r.stderr[:200]}", flush=True)
            except Exception as e:
                print(f"[PoW] Compile error: {e}", flush=True)
    return False

# Search all candidate paths
_candidates = [
    _HERE / "keccak_pow.so",
    _CWD  / "keccak_pow.so",
    Path("/opt/render/project/src/keccak_pow.so"),
    _HERE / "keccak_pow.dll",
    _CWD  / "keccak_pow.dll",
]
for _p in _candidates:
    if _p.exists() and _try_load_so(_p):
        break

# On Linux: compile from source if .so still not loaded
if _c_lib is None and sys.platform == "linux":
    _compile_c_solver()

if _c_lib is None:
    print(f"[PoW] WARNING: C solver not available. Searched: {[str(p) for p in _candidates]}", flush=True)

# ─── 2. Try aiodeepseek AVX2 solver (Windows only — SIGILL on Linux) ───────
_cpp_solve = None
if _c_lib is None and sys.platform != "linux":
    try:
        from aiodeepseek.pow._pow import solve as _raw
        _cpp_solve = _raw
        print("[PoW] Using aiodeepseek C++ AVX2 solver", flush=True)
    except Exception:
        pass

# ─── 3. Pure Python 23-round Keccak (verified sequential rho+pi) ───────────
_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_PILN = [10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4,
         15, 23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1]
_ROTC = [1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14,
         27, 41, 56, 8, 25, 43, 62, 18, 39, 61, 20, 44]
M64 = 0xFFFFFFFFFFFFFFFF

def _rol64(x, n):
    return ((x << n) | (x >> (64 - n))) & M64

def _keccak_f_23(A):
    A = list(A)
    for r in range(1, 24):
        C = [A[x]^A[x+5]^A[x+10]^A[x+15]^A[x+20] for x in range(5)]
        D = [C[(x+4)%5] ^ _rol64(C[(x+1)%5], 1) for x in range(5)]
        for i in range(25): A[i] ^= D[i % 5]
        t = A[1]
        for i in range(24):
            j = _PILN[i]
            A[j], t = _rol64(t, _ROTC[i]), A[j]
        for y in range(0, 25, 5):
            C0,C1,C2,C3,C4 = A[y],A[y+1],A[y+2],A[y+3],A[y+4]
            A[y]   = C0 ^ ((~C1) & C2)
            A[y+1] = C1 ^ ((~C2) & C3)
            A[y+2] = C2 ^ ((~C3) & C4)
            A[y+3] = C3 ^ ((~C4) & C0)
            A[y+4] = C4 ^ ((~C0) & C1)
        for i in range(25): A[i] &= M64
        A[0] ^= _RC[r]
    return A

def _keccak256_23(data: bytes) -> bytes:
    RATE = 136
    msg = bytearray(data)
    msg.append(0x06)
    while len(msg) % RATE != RATE - 1:
        msg.append(0x00)
    msg.append(0x80)
    state = [0] * 25
    for off in range(0, len(msg), RATE):
        blk = msg[off:off + RATE]
        for i in range(RATE // 8):
            state[i] ^= struct.unpack_from('<Q', blk, i * 8)[0]
        state = _keccak_f_23(state)
    return b''.join(struct.pack('<Q', state[i]) for i in range(4))

def _py_solve(base: str, challenge: str, difficulty: int) -> int:
    target = bytes.fromhex(challenge)
    base_b = base.encode()
    for nonce in range(difficulty):
        if _keccak256_23(base_b + str(nonce).encode()) == target:
            return nonce
    return -1

# ─── Public API ────────────────────────────────────────────────────────────
def solve_pow(salt: str, expire_at: int, challenge: str, difficulty: int) -> int:
    base = f"{salt}_{expire_at}_"
    if _c_lib is not None:
        b = base.encode()
        return _c_lib.solve_pow_c(b, len(b), challenge.encode(), difficulty)
    if _cpp_solve is not None:
        return _cpp_solve(base, challenge, difficulty)
    print("[PoW] WARNING: using slow pure-Python solver", flush=True)
    return _py_solve(base, challenge, difficulty)


if __name__ == "__main__":
    import time, requests
    print(f"C solver:   {'yes' if _c_lib else 'no'}")
    print(f"C++ solver: {'yes' if _cpp_solve else 'no'}")
    print("\nLive PoW test...")
    TOKEN = "duDuXK66sHDmYMETfbm8NeHBASg3mGIlXuSyazTigvdrCGpGxe38UqG4XjgF16Wr"
    H = {"Authorization": f"Bearer {TOKEN}", "x-client-platform": "web",
         "x-client-bundle-id": "com.deepseek.chat", "Content-Type": "application/json"}
    r = requests.post("https://chat.deepseek.com/api/v0/chat/create_pow_challenge",
                      json={"target_path": "/api/v0/chat/completion"}, headers=H)
    ch = r.json()["data"]["biz_data"]["challenge"]
    t0 = time.time()
    nonce = solve_pow(ch["salt"], ch["expire_at"], ch["challenge"], ch["difficulty"])
    elapsed = time.time() - t0
    print(f"Nonce: {nonce} in {elapsed*1000:.1f}ms")
    if nonce >= 0:
        got = _keccak256_23(f"{ch['salt']}_{ch['expire_at']}_{nonce}".encode())
        print(f"Verify: {'PASS' if got.hex() == ch['challenge'] else 'FAIL'}")
