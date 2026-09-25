from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"EG"
PROTOCOL_VERSION = 1
DATA = 1
ACK = 2
_DATA_BODY = struct.Struct("<2sBBBHHBBBIhHHH")
_ACK_BODY = struct.Struct("<2sBBBHHBB")
DATA_FRAME_SIZE = _DATA_BODY.size + 2
ACK_FRAME_SIZE = _ACK_BODY.size + 2


class ProtocolError(ValueError):
    pass


def crc16_ccitt(data: bytes, initial: int = 0xFFFF) -> int:
    crc = initial
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _finish(body: bytes) -> bytes:
    return body + struct.pack("<H", crc16_ccitt(body))


def _check(frame: bytes, expected: int, size: int) -> bytes:
    if len(frame) != size:
        raise ProtocolError(f"invalid frame length {len(frame)} (expected {size})")
    body, wire_crc = frame[:-2], struct.unpack("<H", frame[-2:])[0]
    if crc16_ccitt(body) != wire_crc:
        raise ProtocolError("CRC mismatch")
    if body[:2] != MAGIC:
        raise ProtocolError("bad magic")
    if body[2] != PROTOCOL_VERSION or body[3] != expected:
        raise ProtocolError("unsupported version or frame type")
    return body


@dataclass(frozen=True)
class DataPacket:
    node_id: int
    sequence: int
    sample_id: int
    importance: int
    copy_index: int
    copy_count: int
    uptime_ms: int
    temperature_centi: int
    humidity_centi: int
    light_lux: int
    soil_percent_tenths: int

    def encode(self) -> bytes:
        body = _DATA_BODY.pack(MAGIC, PROTOCOL_VERSION, DATA, self.node_id, self.sequence, self.sample_id,
                               self.importance, self.copy_index, self.copy_count, self.uptime_ms,
                               self.temperature_centi, self.humidity_centi, self.light_lux,
                               self.soil_percent_tenths)
        return _finish(body)

    @classmethod
    def decode(cls, frame: bytes) -> "DataPacket":
        v = _DATA_BODY.unpack(_check(frame, DATA, DATA_FRAME_SIZE))
        return cls(node_id=v[3], sequence=v[4], sample_id=v[5], importance=v[6], copy_index=v[7],
                   copy_count=v[8], uptime_ms=v[9], temperature_centi=v[10], humidity_centi=v[11],
                   light_lux=v[12], soil_percent_tenths=v[13])


@dataclass(frozen=True)
class AckPacket:
    node_id: int
    sequence: int
    sample_id: int
    status: int
    copy_index: int

    def encode(self) -> bytes:
        return _finish(_ACK_BODY.pack(MAGIC, PROTOCOL_VERSION, ACK, self.node_id, self.sequence,
                                      self.sample_id, self.status, self.copy_index))

    @classmethod
    def decode(cls, frame: bytes) -> "AckPacket":
        v = _ACK_BODY.unpack(_check(frame, ACK, ACK_FRAME_SIZE))
        return cls(node_id=v[3], sequence=v[4], sample_id=v[5], status=v[6], copy_index=v[7])


class DuplicateTracker:
    """Tracks unique deliveries and physical duplicate copies separately."""
    def __init__(self):
        self.delivered: set[tuple[int, int]] = set()
        self.duplicates = 0
        self.out_of_order = 0
        self.last_sequence: dict[int, int] = {}

    def accept(self, node_id: int, sequence: int) -> bool:
        key = (node_id, sequence)
        if key in self.delivered:
            self.duplicates += 1
            return False
        last = self.last_sequence.get(node_id)
        if last is not None and sequence < last:
            self.out_of_order += 1
        self.last_sequence[node_id] = max(sequence, last if last is not None else sequence)
        self.delivered.add(key)
        return True
