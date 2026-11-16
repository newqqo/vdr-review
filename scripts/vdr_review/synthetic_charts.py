"""Write small synthetic chart layers (shapefile ZIPs) for tests; no real chart data.

Coordinates are WGS84 longitude/latitude like the public nautical layers the tool
reads, and file names follow their pattern (kind_geometry_date), but every
feature, name and value here is made up.
"""
from __future__ import annotations

import io
import struct
import zipfile

POINT, LINE, POLYGON = 1, 3, 5


def shp(shape_type: int, shapes: list) -> bytes:
    """shapes: (lon, lat) for points, or a list of parts of (lon, lat) for lines and polygons."""
    body, xs, ys = b"", [], []
    for number, shape in enumerate(shapes, 1):
        if shape is None:
            content = struct.pack("<i", 0)
        elif shape_type == POINT:
            content = struct.pack("<i2d", POINT, *shape)
            xs.append(shape[0]); ys.append(shape[1])
        else:
            points = [p for part in shape for p in part]
            starts, at = [], 0
            for part in shape:
                starts.append(at); at += len(part)
            px, py = [p[0] for p in points], [p[1] for p in points]
            xs += px; ys += py
            content = (struct.pack("<i4d2i", shape_type, min(px), min(py), max(px), max(py), len(shape), len(points))
                       + struct.pack(f"<{len(starts)}i", *starts) + b"".join(struct.pack("<2d", *p) for p in points))
        body += struct.pack(">2i", number, len(content) // 2) + content
    box = (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)
    header = struct.pack(">7i", 9994, 0, 0, 0, 0, 0, (100 + len(body)) // 2) + struct.pack("<2i8d", 1000, shape_type, *box, 0, 0, 0, 0)
    return header + body


def dbf(fields: list, rows: list, encoding: str = "utf-8", deleted: tuple = ()) -> bytes:
    """fields: (name, 'C' or 'N', length); rows: one tuple of values per shape."""
    header_length = 32 + 32 * len(fields) + 1
    record_length = 1 + sum(f[2] for f in fields)
    out = struct.pack("<B3BIHH20x", 3, 126, 1, 1, len(rows), header_length, record_length)
    for name, kind, length in fields:
        out += struct.pack("<11sc4xBB14x", name.encode("ascii"), kind.encode("ascii"), length, 0)
    out += b"\r"
    for index, row in enumerate(rows):
        out += b"*" if index in deleted else b" "
        for (name, kind, length), value in zip(fields, row):
            raw = ("" if value is None else str(value)).encode(encoding)[:length]
            out += raw.rjust(length) if kind == "N" else raw.ljust(length)
    return out + b"\x1a"


WGS84 = 'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'


def chart_zip(layers: dict) -> bytes:
    """layers: {stem: {"shp": bytes, "dbf": bytes, "cpg": str | None, "prj": str | None}} -> ZIP bytes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for stem, parts in layers.items():
            for ext, value in parts.items():
                if value is not None:
                    zf.writestr(f"{stem}.{ext}", value)
    return buffer.getvalue()


def sample_layers() -> dict:
    """Three synthetic layers near 22.5N 120.3E: aids (UTF-8), depth contours, fairways (Big5, no .cpg)."""
    lights = {"shp": shp(POINT, [(120.30, 22.53), (120.31, 22.54), (120.32, 22.55)]),
              "dbf": dbf([("NOBJNM", "C", 40), ("OBJNAM", "C", 40)],
                         [("合成燈塔甲", "SYNTHETIC Light A"), ("合成燈塔乙", "SYNTHETIC Light B"), ("已刪除", "Deleted")], deleted=(2,)),
              "cpg": "UTF-8", "prj": WGS84}
    depth = {"shp": shp(LINE, [[[(120.29, 22.52), (120.30, 22.52), (120.31, 22.53)]],
                               [[(120.29, 22.50), (120.30, 22.50)], [(120.32, 22.50), (120.33, 22.51)]]]),
             "dbf": dbf([("VALDCO", "N", 19)], [("5.00000000000e+00",), ("1.00000000000e+01",)]),
             "cpg": "UTF-8", "prj": WGS84}
    fairway = {"shp": shp(POLYGON, [[[(120.28, 22.54), (120.29, 22.54), (120.29, 22.55), (120.28, 22.54)]]]),
               "dbf": dbf([("NOBJNM", "C", 40), ("RESTRN", "C", 40)], [("合成航道", "anchoring prohibited")], encoding="big5"),
               "cpg": None, "prj": None}
    return {"light_P_1150918": lights, "DEPCNT_L_20260918": depth, "channel_A_1150918": fairway}
