"""
Parse DUMBO-GEOSPATIAL-CONTROL.md and expose every control value to the build scripts.

The markdown file is the source of truth. This module carries no dimensional constants of its own; if a
value is not in a control table it raises rather than guessing. Mirrors the method used by
manhattan-bridge-3d/scripts/control_model.py so that a reviewer moving between the two repositories does
not have to relearn anything.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROL_DOC = REPO_ROOT / "DUMBO-GEOSPATIAL-CONTROL.md"

AGENT_ID = "dumbo-district-3d/scripts@1.0.0"

_UNIT_TO_M = {
    "m": 1.0,
    "mm": 0.001,
    "km": 1000.0,
}


class ControlError(RuntimeError):
    """Raised when the control document is missing or internally inconsistent."""


@dataclass(frozen=True)
class Control:
    control_id: str
    key: str
    value: float
    unit: str
    source_ids: tuple[str, ...]
    confidence: str
    notes: str

    @property
    def value_m(self) -> float:
        if self.unit not in _UNIT_TO_M:
            raise ControlError(
                f"{self.control_id} has unit {self.unit!r}, which is not a length; "
                "value_m is not meaningful"
            )
        return self.value * _UNIT_TO_M[self.unit]


@dataclass(frozen=True)
class BoundaryVertex:
    vertex_id: str
    lon: float
    lat: float
    along: str


@dataclass(frozen=True)
class HeroCenterline:
    hero_id: str
    name: str
    a: tuple[float, float]
    b: tuple[float, float]


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return cells


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(set(cell) <= set("-: ") and "-" in cell for cell in cells)


def _iter_rows(text: str, expected_width: int) -> Iterator[list[str]]:
    for line in text.splitlines():
        cells = _split_row(line)
        if len(cells) != expected_width or _is_separator(cells):
            continue
        yield cells


class DistrictControl:
    """Loaded, validated view of DUMBO-GEOSPATIAL-CONTROL.md."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or CONTROL_DOC
        if not self.path.is_file():
            raise ControlError(f"control document not found: {self.path}")
        self.text = self.path.read_text(encoding="utf-8")
        self.sha256 = hashlib.sha256(self.text.encode("utf-8")).hexdigest()

        self.controls = self._parse_controls()
        self.boundary = self._parse_boundary()
        self.hero_lines = self._parse_hero_lines()
        self._validate()

    # ------------------------------------------------------------------ parsing

    def _parse_controls(self) -> dict[str, Control]:
        controls: dict[str, Control] = {}
        for cells in _iter_rows(self.text, 7):
            control_id = cells[0]
            if not re.fullmatch(r"DCTL-\d{3}", control_id):
                continue
            raw_value = cells[2].replace(",", "")
            try:
                value = float(raw_value)
            except ValueError as exc:
                raise ControlError(
                    f"{control_id}: value {cells[2]!r} is not a bare decimal number"
                ) from exc
            if control_id in controls:
                raise ControlError(f"{control_id} is defined more than once")
            confidence = cells[5]
            if confidence not in {"A", "B", "C", "D"}:
                raise ControlError(f"{control_id}: confidence {confidence!r} is not A-D")
            controls[control_id] = Control(
                control_id=control_id,
                key=cells[1],
                value=value,
                unit=cells[3],
                source_ids=tuple(s.strip() for s in cells[4].split(",") if s.strip()),
                confidence=confidence,
                notes=cells[6],
            )
        if not controls:
            raise ControlError("no DCTL control rows found; is the document intact?")
        return controls

    def _parse_boundary(self) -> list[BoundaryVertex]:
        vertices: list[BoundaryVertex] = []
        for cells in _iter_rows(self.text, 4):
            if not re.fullmatch(r"DBV-\d{2}", cells[0]):
                continue
            vertices.append(
                BoundaryVertex(
                    vertex_id=cells[0],
                    lon=float(cells[1]),
                    lat=float(cells[2]),
                    along=cells[3],
                )
            )
        if len(vertices) < 3:
            raise ControlError("district boundary needs at least 3 vertices")
        return vertices

    def _parse_hero_lines(self) -> list[HeroCenterline]:
        lines: list[HeroCenterline] = []
        for cells in _iter_rows(self.text, 6):
            if not re.fullmatch(r"DHZ-\d{2}", cells[0]):
                continue
            lines.append(
                HeroCenterline(
                    hero_id=cells[0],
                    name=cells[1],
                    a=(float(cells[2]), float(cells[3])),
                    b=(float(cells[4]), float(cells[5])),
                )
            )
        if not lines:
            raise ControlError("no hero centerlines found")
        return lines

    # --------------------------------------------------------------- validation

    def _validate(self) -> None:
        required = [
            "DCTL-001", "DCTL-002", "DCTL-003", "DCTL-004", "DCTL-005",
            "DCTL-010", "DCTL-020", "DCTL-021", "DCTL-022", "DCTL-023",
            "DCTL-030", "DCTL-040", "DCTL-041", "DCTL-042", "DCTL-043",
            "DCTL-050", "DCTL-051", "DCTL-052", "DCTL-053", "DCTL-054", "DCTL-055",
            "DCTL-060", "DCTL-061", "DCTL-062", "DCTL-063",
            "DCTL-070", "DCTL-071", "DCTL-072", "DCTL-073",
            "DCTL-074", "DCTL-075", "DCTL-076",
        ]
        missing = [cid for cid in required if cid not in self.controls]
        if missing:
            raise ControlError(f"control document is missing required values: {missing}")

        west, east = self.value("DCTL-020"), self.value("DCTL-021")
        south, north = self.value("DCTL-022"), self.value("DCTL-023")
        if west >= east:
            raise ControlError("DCTL-020 (west) must be less than DCTL-021 (east)")
        if south >= north:
            raise ControlError("DCTL-022 (south) must be less than DCTL-023 (north)")

        for vertex in self.boundary:
            if not (west <= vertex.lon <= east and south <= vertex.lat <= north):
                raise ControlError(
                    f"{vertex.vertex_id} at ({vertex.lon}, {vertex.lat}) falls outside the "
                    "declared bounding box DCTL-020..023"
                )

        if self.value("DCTL-041") >= self.value("DCTL-042"):
            raise ControlError("DCTL-041 (load radius) must be less than DCTL-042 (unload radius)")

    # ------------------------------------------------------------------ accessors

    def value(self, control_id: str) -> float:
        try:
            return self.controls[control_id].value
        except KeyError as exc:
            raise ControlError(f"{control_id} is not defined in {self.path.name}") from exc

    def value_m(self, control_id: str) -> float:
        try:
            return self.controls[control_id].value_m
        except KeyError as exc:
            raise ControlError(f"{control_id} is not defined in {self.path.name}") from exc

    def by_key(self, key: str) -> Control:
        for control in self.controls.values():
            if control.key == key:
                return control
        raise ControlError(f"no control with key {key!r}")

    # ------------------------------------------------------- frame and geometry

    @property
    def anchor(self) -> tuple[float, float, float]:
        return (self.value("DCTL-001"), self.value("DCTL-002"), self.value("DCTL-003"))

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(west, south, east, north) in degrees."""
        return (
            self.value("DCTL-020"),
            self.value("DCTL-022"),
            self.value("DCTL-021"),
            self.value("DCTL-023"),
        )

    @property
    def boundary_ring(self) -> list[tuple[float, float]]:
        """Closed lon/lat ring for the district boundary."""
        ring = [(v.lon, v.lat) for v in self.boundary]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        return ring

    @property
    def tile_extent(self) -> tuple[float, float, float, float]:
        """(origin_x, origin_y, span_x, span_y) of the tile grid, in scene ENU metres.

        The tile scheme, the ground grid and the DEM sampling grid must agree exactly or the terrain
        lands offset from the buildings standing on it. Deriving it here, from the control document,
        means every generator gets the same answer without one of them having to read another's
        output — and without the definition being copied into three files that can drift apart.
        """
        tile_size = self.value_m("DCTL-040")
        ring = [self.geodetic_to_enu(lon, lat)[:2] for lon, lat in self.boundary_ring]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        # One tile of padding so context tiles exist around the edge.
        min_x = (min(xs) // tile_size - 1) * tile_size
        min_y = (min(ys) // tile_size - 1) * tile_size
        max_x = (max(xs) // tile_size + 2) * tile_size
        max_y = (max(ys) // tile_size + 2) * tile_size
        return (min_x, min_y, max_x - min_x, max_y - min_y)

    # WGS84 ellipsoid. Declared here rather than in the markdown because it is a
    # universal constant, not a project decision.
    _A = 6378137.0
    _F = 1.0 / 298.257223563
    _E2 = _F * (2.0 - _F)
    _B = _A * (1.0 - _F)

    @classmethod
    def _geodetic_to_ecef(cls, lon: float, lat: float, h: float) -> tuple[float, float, float]:
        lam = math.radians(lon)
        phi = math.radians(lat)
        s, c = math.sin(phi), math.cos(phi)
        n = cls._A / math.sqrt(1.0 - cls._E2 * s * s)
        return (
            (n + h) * c * math.cos(lam),
            (n + h) * c * math.sin(lam),
            (n * (1.0 - cls._E2) + h) * s,
        )

    @classmethod
    def _ecef_to_geodetic(cls, x: float, y: float, z: float) -> tuple[float, float, float]:
        """Bowring's closed-form inversion. Sub-millimetre for terrestrial heights."""
        lam = math.atan2(y, x)
        p = math.hypot(x, y)
        if p == 0.0:
            lat = math.copysign(math.pi / 2.0, z)
            return (math.degrees(lam), math.degrees(lat), abs(z) - cls._B)
        ep2 = (cls._A**2 - cls._B**2) / cls._B**2
        theta = math.atan2(z * cls._A, p * cls._B)
        phi = math.atan2(
            z + ep2 * cls._B * math.sin(theta) ** 3,
            p - cls._E2 * cls._A * math.cos(theta) ** 3,
        )
        s = math.sin(phi)
        n = cls._A / math.sqrt(1.0 - cls._E2 * s * s)
        h = p / math.cos(phi) - n
        return (math.degrees(lam), math.degrees(phi), h)

    def _enu_basis(self) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
        lon0, lat0, _ = self.anchor
        lam, phi = math.radians(lon0), math.radians(lat0)
        sl, cl = math.sin(lam), math.cos(lam)
        sp, cp = math.sin(phi), math.cos(phi)
        east = (-sl, cl, 0.0)
        north = (-sp * cl, -sp * sl, cp)
        up = (cp * cl, cp * sl, sp)
        return east, north, up

    def geodetic_to_enu(self, lon: float, lat: float, height_m: float = 0.0) -> tuple[float, float, float]:
        """
        Rigorous WGS84 geodetic to local ENU, via ECEF.

        Not a small-angle approximation: the only departure from truth over the district is that the
        ellipsoid surface curves away from the frame's z = 0 plane, which is what DCTL-005 quantifies.
        """
        lon0, lat0, h0 = self.anchor
        x0, y0, z0 = self._geodetic_to_ecef(lon0, lat0, h0)
        x, y, z = self._geodetic_to_ecef(lon, lat, height_m)
        dx, dy, dz = x - x0, y - y0, z - z0
        east, north, up = self._enu_basis()
        return (
            east[0] * dx + east[1] * dy + east[2] * dz,
            north[0] * dx + north[1] * dy + north[2] * dz,
            up[0] * dx + up[1] * dy + up[2] * dz,
        )

    def enu_to_geodetic(self, x: float, y: float, z: float = 0.0) -> tuple[float, float, float]:
        lon0, lat0, h0 = self.anchor
        x0, y0, z0 = self._geodetic_to_ecef(lon0, lat0, h0)
        east, north, up = self._enu_basis()
        ex = x0 + east[0] * x + north[0] * y + up[0] * z
        ey = y0 + east[1] * x + north[1] * y + up[1] * z
        ez = z0 + east[2] * x + north[2] * y + up[2] * z
        return self._ecef_to_geodetic(ex, ey, ez)

    def mhw_to_navd88(self, z_mhw: float) -> float:
        """Convert a Manhattan Bridge elevation (MHW datum) into the district frame."""
        return z_mhw + self.value_m("DCTL-010")

    def vertical_datum_offsets(self) -> dict[str, float]:
        return {
            "NAVD88": 0.0,
            "MHW": self.value_m("DCTL-010"),
            "MSL": self.value_m("DCTL-011"),
            "MLLW": self.value_m("DCTL-012"),
        }

    def frame_planar_error_m(self) -> float:
        """
        Worst-case error from treating this frame as flat Cartesian, at DCTL-004.

        The ENU transform itself is rigorous, so there is no horizontal distortion to report. What a
        flat-earth scene actually loses is that the ellipsoid surface falls away from the frame's
        z = 0 plane with distance. This returns that drop at the validity radius, sampled around the
        full azimuth circle because it is slightly azimuth-dependent on an ellipsoid.
        """
        radius = self.value_m("DCTL-004")
        worst = 0.0
        for step in range(72):
            azimuth = math.radians(step * 5.0)
            # Walk out along the tangent plane, then ask where that point sits on the ellipsoid.
            x = radius * math.sin(azimuth)
            y = radius * math.cos(azimuth)
            lon, lat, _ = self.enu_to_geodetic(x, y, 0.0)
            _, _, z = self.geodetic_to_enu(lon, lat, 0.0)
            worst = max(worst, abs(z))
        return worst

    def frame_roundtrip_error_m(self) -> float:
        """Largest geodetic round-trip error over a grid spanning the validity radius."""
        radius = self.value_m("DCTL-004")
        worst = 0.0
        steps = 9
        for i in range(steps):
            for j in range(steps):
                x = -radius + 2.0 * radius * i / (steps - 1)
                y = -radius + 2.0 * radius * j / (steps - 1)
                lon, lat, h = self.enu_to_geodetic(x, y, 0.0)
                bx, by, bz = self.geodetic_to_enu(lon, lat, h)
                worst = max(worst, math.dist((x, y, 0.0), (bx, by, bz)))
        return worst


def point_in_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon for a closed lon/lat ring."""
    x, y = point
    inside = False
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > y) != (y2 > y):
            t = (y - y1) / (y2 - y1)
            if x < x1 + t * (x2 - x1):
                inside = not inside
    return inside


def distance_point_to_segment(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Planar distance from p to segment ab. Inputs must already be in meters."""
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _check_sources() -> int:
    """Every source the build actually used must be described in the human-readable register.

    The generated register is what the build consumed; the markdown is what a reader is told. When
    those drift, the project is quietly claiming a provenance discipline it is no longer keeping —
    which is worse than having no register at all. So this is a build gate, not a lint.
    """
    import json
    import re

    root = Path(__file__).resolve().parent.parent
    generated = root / "viewer" / "public" / "district" / "source-register.json"
    prose = root / "DUMBO-SOURCE-REGISTER.md"
    if not generated.exists():
        print(f"SKIP: {generated.name} not built yet")
        return 0

    shipped = {s["source_id"] for s in json.loads(generated.read_text("utf-8"))["sources"]}
    documented = set(re.findall(r"^### (DSRC-\d+)", prose.read_text("utf-8"), re.M))

    undocumented = sorted(shipped - documented)
    phantom = sorted(documented - shipped)
    print(f"sources shipped  : {len(shipped)}")
    print(f"sources documented: {len(documented)}")
    if undocumented:
        print(f"FAIL: used by the build but absent from the register: {', '.join(undocumented)}")
    for sid in phantom:
        # Registering a source before ingesting it is deliberate: it makes a gap visible.
        print(f"note: {sid} is documented but not yet ingested")
    if undocumented:
        return 1
    print("OK: every shipped source is documented")
    return 0


def _main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Inspect the DUMBO geospatial control document.")
    parser.add_argument("--check-frame", action="store_true", help="Report tangent-plane error.")
    parser.add_argument(
        "--check-sources",
        action="store_true",
        help="Verify every source in the generated register is documented in the markdown register.",
    )
    parser.add_argument("--json", action="store_true", help="Dump all controls as JSON.")
    args = parser.parse_args()

    control = DistrictControl()
    print(f"control document : {control.path.name}")
    print(f"sha256           : {control.sha256[:16]}")
    print(f"controls         : {len(control.controls)}")
    print(f"boundary vertices: {len(control.boundary)}")
    print(f"hero centerlines : {len(control.hero_lines)}")
    print(f"anchor           : {control.anchor}")

    if args.check_frame:
        err = control.frame_planar_error_m()
        declared = control.value_m("DCTL-005")
        rt = control.frame_roundtrip_error_m()
        print(f"ENU round-trip error     : {rt * 1000:.4f} mm")
        print(f"flat-plane drop at DCTL-004 : {err:.3f} m (declared {declared:.3f} m)")
        if rt > 0.001:
            print("FAIL: geodetic round-trip error exceeds 1 mm")
            return 1
        if err > declared:
            print("FAIL: measured flat-plane drop exceeds the declared DCTL-005 value")
            return 1
        print("OK: frame is within its declared bounds")

    if args.check_sources:
        rc = _check_sources()
        if rc:
            return rc

    if args.json:
        print(json.dumps(
            {cid: vars(c) for cid, c in sorted(control.controls.items())},
            indent=2, default=list,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
