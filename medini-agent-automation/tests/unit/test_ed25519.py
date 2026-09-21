"""domain/ed25519 的回归测试。

**判据是 RFC 8032 §7.1 的官方测试向量**，不是"看起来能跑"：
自研密码学实现最危险的失败模式是"能签名也能验签，但两者都错"——
自洽的错误实现能骗过所有自洽的往返测试。

向量覆盖空消息、1 字节、2 字节三种长度；负例覆盖篡改、畸形编码与
S 非规范（可延展性）。
"""
from __future__ import annotations

import time

import pytest

from medini_automation.domain import ed25519 as E

# RFC 8032 §7.1 官方向量：(名称, 私钥, 公钥, 消息, 签名)
RFC8032_VECTORS = [
    ("TEST 1 (empty msg)",
     "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
     "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("TEST 2 (1 byte)",
     "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
     "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("TEST 3 (2 bytes)",
     "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
     "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
     "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]

_L = 2 ** 252 + 27742317777372353535851937790883648493


@pytest.mark.parametrize("name,sk,pk,msg,sig", RFC8032_VECTORS,
                         ids=[v[0] for v in RFC8032_VECTORS])
def test_public_key_matches_rfc8032(name, sk, pk, msg, sig):
    assert E.public_key(bytes.fromhex(sk)).hex() == pk


@pytest.mark.parametrize("name,sk,pk,msg,sig", RFC8032_VECTORS,
                         ids=[v[0] for v in RFC8032_VECTORS])
def test_signature_matches_rfc8032(name, sk, pk, msg, sig):
    assert E.sign(bytes.fromhex(sk), bytes.fromhex(msg)).hex() == sig


@pytest.mark.parametrize("name,sk,pk,msg,sig", RFC8032_VECTORS,
                         ids=[v[0] for v in RFC8032_VECTORS])
def test_verify_accepts_official_vector(name, sk, pk, msg, sig):
    assert E.verify(bytes.fromhex(pk), bytes.fromhex(sig),
                    bytes.fromhex(msg)) is True


def test_selfcheck_passes():
    """`ed25519_available()` 供 cli trust 做在线自检。"""
    assert E.ed25519_available() is True


# ------------------------------------------------------------------ 负例
def test_verify_rejects_tampered_message():
    seed = bytes(32)
    pk, sig = E.public_key(seed), E.sign(seed, b"hello")
    assert E.verify(pk, sig, b"hellp") is False


def test_verify_rejects_tampered_signature():
    seed = bytes(32)
    pk, sig = E.public_key(seed), E.sign(seed, b"hello")
    bad = bytes([sig[0] ^ 0x01]) + sig[1:]
    assert E.verify(pk, bad, b"hello") is False


def test_verify_rejects_wrong_public_key():
    sig = E.sign(bytes(32), b"hello")
    assert E.verify(E.public_key(bytes(31) + b"\x01"), sig, b"hello") is False


def test_verify_rejects_malformed_sizes():
    seed = bytes(32)
    pk, sig = E.public_key(seed), E.sign(seed, b"m")
    assert E.verify(pk, sig[:32], b"m") is False        # 签名太短
    assert E.verify(pk[:16], sig, b"m") is False        # 公钥太短
    assert E.verify(b"", b"", b"m") is False


def test_verify_rejects_non_canonical_s():
    """S ≥ L 必须拒 —— 否则同一签名的等价变形会破坏"一次性"语义。"""
    seed = bytes(32)
    pk, sig = E.public_key(seed), E.sign(seed, b"m")
    assert E.verify(pk, sig[:32] + _L.to_bytes(32, "little"), b"m") is False


def test_verify_rejects_point_not_on_curve():
    """伪造 R 为一个不在曲线上的编码 → 必须 False 而非抛异常。"""
    pk, sig = E.public_key(bytes(32)), E.sign(bytes(32), b"m")
    assert E.verify(pk, b"\xff" * 32 + sig[32:], b"m") is False


def test_sign_rejects_bad_seed_length():
    with pytest.raises(ValueError):
        E.sign(b"short", b"m")
    with pytest.raises(ValueError):
        E.public_key(b"short")


# ------------------------------------------------------------------ 性质
def test_sign_is_deterministic():
    """Ed25519 是确定性签名（无需随机数）—— 同密钥同消息必得同签名。"""
    seed = bytes(range(32))
    assert E.sign(seed, b"abc") == E.sign(seed, b"abc")


def test_different_messages_give_different_signatures():
    seed = bytes(range(32))
    assert E.sign(seed, b"abc") != E.sign(seed, b"abd")


def test_signature_is_64_bytes_and_verifies_for_long_payload():
    """审批载荷是几百字节 JSON —— 长消息也要能签能验。"""
    seed = bytes(range(32))
    payload = ('{"approver":"E12345","patch_hash":"' + "a" * 64 + '"}').encode()
    sig = E.sign(seed, payload)
    assert len(sig) == 64
    assert E.verify(E.public_key(seed), sig, payload) is True


def test_performance_is_acceptable():
    """审批是低频操作，但也不能慢到让 CLI 难用（阈值放宽到 1s，只挡灾难性退化）。"""
    seed = bytes(range(32))
    payload = b"x" * 400
    t0 = time.perf_counter()
    sig = E.sign(seed, payload)
    E.verify(E.public_key(seed), sig, payload)
    assert time.perf_counter() - t0 < 1.0
