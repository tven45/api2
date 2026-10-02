"""
PoW Solver — DeepSeekHashV1
Priority:
  1. keccak_pow.so  — our portable C build (Render/Linux, no AVX2 needed) ✅
  2. aiodeepseek    — Windows C++ AVX2 solver ✅
  3. Pure Python    — slow fallback, last resort
"""
import ctypes, os, struct, sys
from pathlib import Path

# ─── 1. Try our compiled C solver (keccak_pow.so / keccak_pow.dll) ─────────
_c_lib = None
_SO_PATHS = [
    Path(__file__).parent / "keccak_pow.so",   # Linux (Render)
    Path(__file__).parent / "keccak_pow.dll",  # Windows (if compiled)
]
for _p in _SO_PATHS:
    if _p.exists():
        try:
            _c_lib = ctypes.CDLL(str(_p))
            _c_lib.solve_pow_c.restype  = ctypes.c_longlong
            _c_lib.solve_pow_c.argtypes = [
                ctypes.c_char_p, ctypes.c_int,
                ctypes.c_char_p, ctypes.c_int,
            ]
            print(f"[PoW] Using C solver: {_p.name}")
            break
        except Exception as e:
            print(f"[PoW] C solver load failed ({_p.name}): {e}")

# ─── 2. Try aiodeepseek AVX2 solver (Windows) ──────────────────────────────
_cpp_solve = None
if _c_lib is None:
    # Only try on non-Linux (Render is Linux and SIGILL-crashes on AVX2)
    if sys.platform != "linux":
        try:
            from aiodeepseek.pow._pow import solve as _raw
            _cpp_solve = _raw
            print("[PoW] Using aiodeepseek C++ AVX2 solver")
        except Exception:
            pass

# ─── 3. Pure Python 23-round Keccak fallback ───────────────────────────────
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
_ROT = [0,36,3,41,18,1,44,10,45,2,62,6,43,15,61,28,55,25,21,56,27,20,39,8,14]
_PI  = [0,10,20,5,15,1,11,21,6,16,2,12,22,7,17,3,13,23,8,18,4,14,24,9,19]

def _rol64(x, n): return ((x << n) | (x >> (64 - n))) & 0xFFFFFFFFFFFFFFFF

def _keccak_f_23(A):
    for i in range(1, 24):
        C = [A[x]^A[x+5]^A[x+10]^A[x+15]^A[x+20] for x in range(5)]
        D = [C[(x-1)%5]^_rol64(C[(x+1)%5],1) for x in range(5)]
        A = [A[x]^D[x%5] for x in range(25)]
        B = [0]*25
        for x in range(25): B[_PI[x]] = _rol64(A[x], _ROT[x])
        A = [B[x]^((~B[(x//5)*5+(x%5+1)%5])&B[(x//5)*5+(x%5+2)%5]) for x in range(25)]
        A[0] ^= _RC[i]
    return A

def _keccak256_23(data: bytes) -> bytes:
    RATE = 136
    msg = bytearray(data)
    msg.append(0x06)
    while len(msg) % RATE != RATE - 1: msg.append(0x00)
    msg.append(0x80)
    state = [0]*25
    for off in range(0, len(msg), RATE):
        blk = msg[off:off+RATE]
        for i in range(RATE//8):
            state[i] ^= struct.unpack_from('<Q', blk, i*8)[0]
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
        return _c_lib.solve_pow_c(
            base.encode(), len(base.encode()),
            challenge.encode(), difficulty
        )

    if _cpp_solve is not None:
        return _cpp_solve(base, challenge, difficulty)

    print("[PoW] WARNING: using slow pure-Python solver")
    return _py_solve(base, challenge, difficulty)


if __name__ == "__main__":
    import time
    print(f"C solver:   {'yes' if _c_lib else 'no'}")
    print(f"C++ solver: {'yes' if _cpp_solve else 'no'}")
    print("Benchmarking pure Python (1000 hashes)...")
    t = time.time()
    for _ in range(1000): _keccak256_23(b"bench_test_123")
    e = time.time() - t
    rate = 1000/e
    print(f"  {rate:.0f} hash/s → difficulty 144000 ≈ {144000/rate:.1f}s")
