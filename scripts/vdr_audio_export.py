"""Read validated JRC AAC records and remux review copies without re-encoding.

Supports only the observed VoyageDtRec single-record layout. Originals stay
unchanged. Folder labels and recording timestamps are retained separately from
relative playback time; this is not a synchronized forensic timeline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

CHANNELS = {**{f"A{i}": f"MIC{2*i-1}-{2*i}" for i in range(1, 7)},
            "C1": "VHF1-2", "C2": "VHF3-4"}
RATES = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050,
         16000, 12000, 11025, 8000, 7350]
TRAILER = b"\x00\x00\r\n\x00\x00\r\n"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_record(data: bytes) -> tuple[bytes, dict]:
    match = re.match(rb"VoyageDtRec (\d{16})", data[:44])
    if len(data) < 87 or not match or data[44:48] not in (b"\xff\xff\x01\x00", b"\xff\xff\x02\x00"):
        raise ValueError("Unsupported JRC audio record header")
    if not data.endswith(TRAILER) or int.from_bytes(data[48:52], "big") != len(data)-80:
        raise ValueError("Unsupported record length or trailer")
    stamp = data[52:68].decode("ascii")
    datetime.strptime(stamp, "%Y%m%d%H%M%S%f")
    if match[1].decode("ascii") != stamp:
        raise ValueError("Header timestamps disagree")
    channel = data[68:70].decode("ascii")
    if channel not in CHANNELS or data[70:72] != b"\0\0":
        raise ValueError("Unsupported audio channel")
    if data[46] != (1 if channel.startswith("A") else 2):
        raise ValueError("Record type and audio channel disagree")
    payload = data[72:-8]
    pos = frames = 0
    rate = channels = None
    while pos < len(payload):
        h = payload[pos:pos+7]
        if len(h) < 7 or h[0] != 0xff or h[1] & 0xf6 != 0xf0:
            raise ValueError(f"Invalid ADTS frame at payload offset {pos}")
        index = h[2] >> 2 & 15
        if index >= len(RATES):
            raise ValueError("Unsupported AAC sample rate")
        current_rate = RATES[index]
        current_channels = (h[2] & 1) << 2 | h[3] >> 6
        size = (h[3] & 3) << 11 | h[4] << 3 | h[5] >> 5
        header_size = 7 if h[1] & 1 else 9
        if size < header_size or pos+size > len(payload) or h[6] & 3:
            raise ValueError("Invalid ADTS length or multiple raw blocks")
        if rate is not None and (rate, channels) != (current_rate, current_channels):
            raise ValueError("AAC format changes within a record")
        rate, channels = current_rate, current_channels
        pos += size
        frames += 1
    if not frames or rate != (16000 if channel.startswith("A") else 8000) or channels != 1:
        raise ValueError("Only validated MIC 16 kHz / VHF 8 kHz mono AAC is supported")
    return payload, {"channel": channel, "recorded_timestamp": stamp,
                     "sample_rate": rate, "channels": channels, "aac_frames": frames,
                     "samples": frames*1024, "duration_seconds": frames*1024/rate,
                     "source_sha256": digest(data), "aac_payload_sha256": digest(payload)}


def export_audio(source: Path, output: Path, ffmpeg: str, ffprobe: str,
                 verification_manifest: Path | None = None) -> dict:
    source, output = source.resolve(), output.resolve()
    if not source.is_dir() or source == output or output.is_relative_to(source):
        raise ValueError("Use an existing audio source and a separate output folder")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output folder must be new or empty")
    expected, original_root = {}, None
    if verification_manifest:
        saved = json.loads(verification_manifest.read_text(encoding="utf-8-sig"))
        if saved.get("status") != "verified":
            raise ValueError("Source copy manifest is not verified")
        original_root = Path(saved["destination"]).resolve()
        expected = {r["path"].replace("\\", "/"): r["sha256"] for r in saved["records"]}
    groups = defaultdict(list)
    # Validate the entire selected source set before producing audio.
    for path in sorted(source.glob("*/*.dat")):
        if path.is_symlink() or path.parent.is_symlink() or not re.fullmatch(r"\d{12}", path.parent.name):
            raise ValueError("Unexpected source path")
        data = path.read_bytes()
        _, info = parse_record(data)
        if not path.name.startswith(info["channel"]+"_"):
            raise ValueError("Filename and recorded channel disagree")
        if original_root and expected.get(path.relative_to(original_root).as_posix()) != info["source_sha256"]:
            raise ValueError("Source differs from verified copy manifest")
        info["source"] = path.relative_to(source).as_posix()
        groups[(path.parent.name, info["channel"])].append((path, info))
    if not groups:
        raise ValueError("No supported audio records found")
    output.mkdir(parents=True, exist_ok=True)
    reports = []
    for (group, channel), records in sorted(groups.items()):
        directory = output/f"{channel}_{CHANNELS[channel]}"
        directory.mkdir(exist_ok=True)
        destination = directory/f"{group}_{channel}.m4a"
        temporary = directory/f"{group}_{channel}.aac"
        offset_samples, segments = 0, []
        sample_rate = records[0][1]["sample_rate"]
        with temporary.open("xb") as stream:
            for path, previous in sorted(records, key=lambda row: row[1]["recorded_timestamp"]):
                payload, current = parse_record(path.read_bytes())
                if current["source_sha256"] != previous["source_sha256"]:
                    raise ValueError("Source changed after validation")
                stream.write(payload)
                segments.append({**previous, "relative_start_seconds": offset_samples/sample_rate})
                offset_samples += current["samples"]
        subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-n", "-f", "aac",
                        "-i", str(temporary), "-map", "0:a:0", "-c:a", "copy", "-movflags", "+faststart",
                        str(destination)], check=True, capture_output=True)
        probe = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-count_packets",
                            "-show_entries", "stream=codec_name,sample_rate,channels,nb_read_packets:format=duration",
                            "-of", "json", str(destination)], text=True))
        sound = probe["streams"][0]
        if (sound["codec_name"], int(sound["sample_rate"]), sound["channels"]) != ("aac", sample_rate, 1):
            raise ValueError("Remuxed format differs from source")
        if int(sound["nb_read_packets"]) != sum(s["aac_frames"] for s in segments):
            raise ValueError("Remuxed AAC packet count differs from source")
        if abs(float(probe["format"]["duration"])-offset_samples/sample_rate) > .1:
            raise ValueError("Remuxed duration differs from source frame count")
        # Decode to the null sink to detect damaged or undecodable audio.
        subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", str(destination),
                        "-f", "null", "-"], check=True, capture_output=True)
        temporary.unlink()
        report = {"file": destination.relative_to(output).as_posix(), "sha256": digest(destination.read_bytes()),
                  "bytes": destination.stat().st_size, "source_folder_label_utc": group,
                  "channel": channel, "channel_description": CHANNELS[channel],
                  "duration_seconds": float(probe["format"]["duration"]), "source_segments": segments,
                  "decode_check": "passed", "audio_reencoded": False}
        reports.append(report)
        print(json.dumps({"completed": len(reports), "total": len(groups), "file": report["file"]}), flush=True)
    result = {"status": "verified", "source": str(source), "source_files": sum(len(v) for v in groups.values()),
              "files": len(reports), "source_copy_manifest_verified": bool(verification_manifest),
              "audio_reencoded": False, "reports": reports,
              "timeline_boundary": "Folder labels and original record timestamps are retained. Relative playback offsets include all source AAC frames and encoder padding; do not use them as an exact wall-clock timeline. No padding, silence or gaps were edited. No speaker identity or transcript inferred."}
    (output/"audio_export_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--verification-manifest", type=Path)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe"))
    args = parser.parse_args()
    if not args.ffmpeg or not args.ffprobe:
        parser.error("ffmpeg and ffprobe are required")
    try:
        result = export_audio(args.source, args.out, args.ffmpeg, args.ffprobe, args.verification_manifest)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Audio export stopped: {exc}\n")
    print(json.dumps({k: v for k, v in result.items() if k != "reports"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
