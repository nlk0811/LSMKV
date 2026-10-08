import hashlib
import math
import struct


class BloomFilter:
    """
    Space-efficient probabilistic membership test.
    False positives are possible; false negatives are impossible.

    Used per SSTable so we can skip disk reads for keys that definitely
    don't exist in that SSTable.

    Optimal parameters derived from target capacity and false-positive rate:
        m = -n * ln(p) / (ln 2)^2   (bit array size)
        k = (m/n) * ln 2              (number of hash functions)
    """

    def __init__(self, capacity: int, false_positive_rate: float = 0.01):
        self.capacity = capacity
        self.fpr = false_positive_rate
        self.bit_count = math.ceil(
            -capacity * math.log(false_positive_rate) / (math.log(2) ** 2)
        )
        self.hash_count = max(1, round((self.bit_count / capacity) * math.log(2)))
        self.bits = bytearray(math.ceil(self.bit_count / 8))

    # Double-hashing with a single SHA-256 call: h_i(x) = h1(x) + i*h2(x) mod m
    def _hashes(self, key: bytes):
        digest = hashlib.sha256(key).digest()
        h1, h2 = struct.unpack_from('>QQ', digest, 0)
        for i in range(self.hash_count):
            yield (h1 + i * h2) % self.bit_count

    def add(self, key: bytes):
        for pos in self._hashes(key):
            self.bits[pos >> 3] |= 1 << (pos & 7)

    def may_contain(self, key: bytes) -> bool:
        return all(self.bits[pos >> 3] & (1 << (pos & 7)) for pos in self._hashes(key))

    # ── serialization ──────────────────────────────────────────────────────────

    def serialize(self) -> bytes:
        # header: capacity(4) hash_count(4) fpr(4f)
        header = struct.pack('>IIf', self.capacity, self.hash_count, self.fpr)
        return header + bytes(self.bits)

    @classmethod
    def deserialize(cls, data: bytes) -> 'BloomFilter':
        capacity, hash_count, fpr = struct.unpack('>IIf', data[:12])
        bf = cls.__new__(cls)
        bf.capacity = capacity
        bf.fpr = fpr
        bf.hash_count = hash_count
        bf.bits = bytearray(data[12:])
        bf.bit_count = len(bf.bits) * 8
        return bf
