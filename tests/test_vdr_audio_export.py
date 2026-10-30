import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from vdr_audio_export import parse_record, TRAILER


def recording(payload=b"\xff\xf1\x60\x40\x01\x1f\xfc\0", channel=b"A1"):
    stamp = b"2026010100000000"
    header = (b"VoyageDtRec " + stamp).ljust(44, b"\0")
    kind = b"\x01" if channel.startswith(b"A") else b"\x02"
    return header + b"\xff\xff" + kind + b"\0" + len(payload).to_bytes(4, "big") + stamp + channel + b"\0\0" + payload + TRAILER


def test_record_preserves_payload_and_frame_duration():
    raw = recording()
    payload, info = parse_record(raw)
    assert payload == raw[72:-8]
    assert info["aac_frames"] == 1
    assert info["duration_seconds"] == .064
    assert info["recorded_timestamp"] == "2026010100000000"


def test_vhf_records_preserve_lower_sample_rate():
    _, info = parse_record(recording(b"\xff\xf1\x6c\x40\x01\x1f\xfc\0", b"C1"))
    assert info["sample_rate"] == 8000
    assert info["duration_seconds"] == .128


@pytest.mark.parametrize("change", [lambda b:b[:-1], lambda b:b[:72]+b"invalid"+b[79:],
                                    lambda b:b[:52]+b"2026010200000000"+b[68:]])
def test_corrupt_record_is_rejected(change):
    with pytest.raises(ValueError):
        parse_record(change(recording()))
