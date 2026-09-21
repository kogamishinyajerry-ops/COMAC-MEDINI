"""domain.ed25519 — 纯标准库 Ed25519 签名（RFC 8032 §5.1）。

**为什么自己写而不装 `cryptography`**：本仓 `pyproject.toml` 声明
``dependencies = []``。审批签名是 P3 的信任边界，不能因为"装个库更省事"就给
整个仓引入依赖 —— 那样在内网/离线部署时会出现「审批功能依赖某个未必装了的包」
这类隐性失败，且一个安全关键路径被第三方库的版本漂移牵着走。

**实现范围**：Ed25519-SHA-512（PureEdDSA），即 RFC 8032 §5.1。
正确性由 RFC 8032 §7.1 的官方测试向量保证（见 `tests/unit/test_ed25519.py`），
不用"看起来能跑"当证据。

**性能**：用扩展坐标（X:Y:Z:T）+ 双倍-加点迭代，避免每次点加做模逆 ——
仿射版每次标量乘要 ~500 次 ``pow(x, p-2, p)``（每次签名上百毫秒），
扩展坐标版把模逆压缩到只出现在最终编码，实测签名/验签各在毫秒级。

**不承诺**：常量时间与侧信道防护。Python 解释器自身的时间特性就不受控，
且本模块服务的是本机审批面板，不是网络服务端。若将来要暴露给不可信调用方，
必须换成专门的密码学库。
"""
from __future__ import annotations

import hashlib

__all__ = [
    "SEED_SIZE", "PUBLIC_KEY_SIZE", "SIGNATURE_SIZE",
    "public_key", "sign", "verify", "ed25519_available",
]

SEED_SIZE = 32
PUBLIC_KEY_SIZE = 32
SIGNATURE_SIZE = 64

# ---------------------------------------------------------------- 曲线常数
_P = 2 ** 255 - 19                       # 素域模数
_L = 2 ** 252 + 27742317777372353535851937790883648493   # 基点阶
_D = -121665 * pow(121666, _P - 2, _P) % _P               # 扭曲 Edwards d
_I = pow(2, (_P - 1) // 4, _P)                            # sqrt(-1)
_BY = 4 * pow(5, _P - 2, _P) % _P                         # 基点 y = 4/5


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _xrecover(y: int) -> int:
    """由 y 恢复曲线点 x（取偶的根，标准做法）。"""
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = x * _I % _P
    if x % 2 != 0:
        x = _P - x
    return x


# ------------------------------------------------- 扩展坐标（避免模逆）
_ExtPoint = tuple[int, int, int, int]   # (X, Y, Z, T)，仿射 x=X/Z, y=Y/Z，T=XY/Z


def _to_ext(x: int, y: int) -> _ExtPoint:
    return (x % _P, y % _P, 1, x * y % _P)


def _identity() -> _ExtPoint:
    return (0, 1, 1, 0)


def _ext_add(p: _ExtPoint, q: _ExtPoint) -> _ExtPoint:
    """统一加法公式（a=-1 的扭曲 Edwards 曲线，RFC 8032 §5.1.4）。"""
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _ext_double(p: _ExtPoint) -> _ExtPoint:
    return _ext_add(p, p)


def _scalarmult(base: _ExtPoint, e: int) -> _ExtPoint:
    """从左到右双倍-加。e < 2^255，迭代 255 次。"""
    if e == 0:
        return _identity()
    q = _identity()
    for bit in bin(e)[2:]:
        q = _ext_double(q)
        if bit == "1":
            q = _ext_add(q, base)
    return q


def _encode_point(p: _ExtPoint) -> bytes:
    """压缩编码：低 255 位是 y，最高位是 x 的奇偶。"""
    x, y, z, _ = p
    zi = pow(z, _P - 2, _P)
    x, y = x * zi % _P, y * zi % _P
    return ((y & ((1 << 255) - 1)) | ((x & 1) << 255)).to_bytes(32, "little")


def _decode_point(s: bytes) -> _ExtPoint:
    """解压并校验点在曲线上；不合法直接抛 ValueError（调用方须捕获）。"""
    if len(s) != 32:
        raise ValueError("point: 必须是 32 字节")
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    if y >= _P:
        raise ValueError("point: y 非规范编码")
    x = _xrecover(y)
    if x & 1 != sign:
        x = _P - x
    # 曲线方程校验：-x² + y² - 1 - d·x²·y² ≡ 0  (mod p)
    if (-x * x + y * y - 1 - _D * x * x * y * y) % _P != 0:
        raise ValueError("point: 不在曲线上")
    return _to_ext(x, y)


_BASE = _to_ext(_xrecover(_BY), _BY)


def _clamp(seed: bytes) -> tuple[int, bytes]:
    """seed → (标量 a, 前缀 prefix)。clamp 按 RFC 8032 §5.1.5。"""
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8       # 清低 3 位
    a |= 1 << 254             # 置位 254，且不改动位 255（已由掩码清零）
    return a, h[32:]


# ---------------------------------------------------------------- 公开 API
def ed25519_available() -> bool:
    """自检：用 RFC 8032 §7.1 TEST 1 向量验一遍本实现。"""
    seed = bytes.fromhex(
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    want_pk = bytes.fromhex(
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    want_sig = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    try:
        pk = public_key(seed)
        sig = sign(seed, b"")
    except Exception:
        return False
    return pk == want_pk and sig == want_sig and verify(pk, sig, b"")


def public_key(seed: bytes) -> bytes:
    """32 字节 seed → 32 字节公钥。"""
    if len(seed) != SEED_SIZE:
        raise ValueError(f"seed 必须是 {SEED_SIZE} 字节，收到 {len(seed)}")
    a, _ = _clamp(seed)
    return _encode_point(_scalarmult(_BASE, a))


def sign(seed: bytes, message: bytes) -> bytes:
    """Ed25519 签名，返回 64 字节（R ‖ S）。"""
    if len(seed) != SEED_SIZE:
        raise ValueError(f"seed 必须是 {SEED_SIZE} 字节，收到 {len(seed)}")
    a, prefix = _clamp(seed)
    a_pub = _encode_point(_scalarmult(_BASE, a))
    r = int.from_bytes(_sha512(prefix + message), "little") % _L
    r_enc = _encode_point(_scalarmult(_BASE, r))
    k = int.from_bytes(_sha512(r_enc + a_pub + message), "little") % _L
    s = (r + k * a) % _L
    return r_enc + s.to_bytes(32, "little")


def verify(public_key_bytes: bytes, signature: bytes, message: bytes) -> bool:
    """验签。任何畸形输入一律返回 False（不抛异常 —— 调用方按"校验失败"处理）。"""
    if len(public_key_bytes) != PUBLIC_KEY_SIZE or len(signature) != SIGNATURE_SIZE:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:                      # 非规范 S，拒绝（防延展性）
        return False
    try:
        a = _decode_point(public_key_bytes)
        r = _decode_point(signature[:32])
    except ValueError:
        return False
    k = int.from_bytes(
        _sha512(signature[:32] + public_key_bytes + message), "little") % _L
    return _encode_point(_scalarmult(_BASE, s)) == \
        _encode_point(_ext_add(r, _scalarmult(a, k)))
