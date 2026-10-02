"""
DeepSeek PoW Solver — DeepSeekHashV1
23-round Keccak (skips round 0), base = f"{salt}_{expire_at}_"
Uses C++ AVX2 solver if available (Windows/Linux via aiodeepseek), pure Python fallback.
"""
import ctypes, struct, os

# ─── Try fast C++ solver first ─────────────────────────────────────────────
_cpp_solve = None
try:
    from aiodeepseek.pow._pow import solve as _cpp_solve_raw
    def _cpp_solve(base: str, challenge: str, difficulty: int) -> int:
        return _cpp_solve_raw(base, challenge, difficulty)
except Exception:
    pass

# ─── Pure Python 23-round Keccak fallback ──────────────────────────────────
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
_ROT = [
     0, 36,  3, 41, 18,  1, 44, 10, 45,  2, 62,  6, 43,
    15, 61, 28, 55, 25, 21, 56, 27, 20, 39,  8, 14,
]
_PI = [
     0, 10, 20,  5, 15,  1, 11, 21,  6, 16,  2, 12, 22,
     7, 17,  3, 13, 23,  8, 18,  4, 14, 24,  9, 19,
]

def _rot64(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & 0xFFFFFFFFFFFFFFFF

def _keccak_f_23(state: list) -> list:
    """Keccak-f[1600] with 23 rounds (skips round index 0)."""
    A = list(state)
    # Rounds 1..23 (skip RC[0])
    for i in range(1, 24):
        # θ
        C = [A[x] ^ A[x+5] ^ A[x+10] ^ A[x+15] ^ A[x+20] for x in range(5)]
        D = [C[(x-1)%5] ^ _rot64(C[(x+1)%5], 1) for x in range(5)]
        A = [A[x] ^ D[x % 5] for x in range(25)]
        # ρ + π
        B = [0] * 25
        for x in range(25):
            B[_PI[x]] = _rot64(A[x], _ROT[x])
        # χ
        A = [B[x] ^ ((~B[(x//5)*5 + (x%5+1)%5]) & B[(x//5)*5 + (x%5+2)%5]) for x in range(25)]
        # ι
        A[0] ^= _RC[i]
    return A

def _keccak256_23(data: bytes) -> str:
    """23-round Keccak-256, returns hex string."""
    RATE = 136  # 1088 bits
    # Pad: append 0x06, then zeros, then 0x80 at end of rate block
    msg = bytearray(data)
    msg.append(0x06)
    while len(msg) % RATE != (RATE - 1):
        msg.append(0x00)
    msg.append(0x80)

    state = [0] * 25
    for block_start in range(0, len(msg), RATE):
        block = msg[block_start:block_start + RATE]
        for i in range(RATE // 8):
            state[i] ^= struct.unpack_from('<Q', block, i * 8)[0]
        state = _keccak_f_23(state)

    # Extract 32 bytes
    out = b''
    for i in range(4):
        out += struct.pack('<Q', state[i])
    return out.hex()

def _py_solve(base: str, challenge: str, difficulty: int) -> int:
    base_bytes = base.encode()
    for nonce in range(difficulty):
        h = _keccak256_23(base_bytes + str(nonce).encode())
        if h == challenge:
            return nonce
    return -1

# ─── Public API ────────────────────────────────────────────────────────────
def solve_pow(salt: str, expire_at: int, challenge: str, difficulty: int) -> int:
    """
    Solve DeepSeekHashV1 PoW.
    Returns nonce >= 0 on success, -1 on failure.
    """
    base = f"{salt}_{expire_at}_"
    if _cpp_solve is not None:
        nonce = _cpp_solve(base, challenge, difficulty)
    else:
        nonce = _py_solve(base, challenge, difficulty)
    return nonce

def verify(salt: str, expire_at: int, challenge: str, nonce: int) -> bool:
    base = f"{salt}_{expire_at}_"
    return _keccak256_23((base + str(nonce)).encode()) == challenge

if __name__ == "__main__":
    # Quick self-test
    import time
    print(f"C++ solver available: {_cpp_solve is not None}")
    # Can't verify without live challenge, just benchmark
    print("Pure Python 23-round Keccak: ", end="")
    t = time.time()
    for _ in range(1000):
        _keccak256_23(b"test_salt_12345_1")
    elapsed = time.time() - t
    print(f"1000 hashes in {elapsed:.3f}s = {1000/elapsed:.0f} hash/s")
    print(f"Estimated time for difficulty=144000: {144000/(1000/elapsed):.1f}s")
