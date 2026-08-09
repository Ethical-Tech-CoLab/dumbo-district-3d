"""
Author the demonstration tours by routing them on the real DUMBO walk network.

This script exists to make a point about the contract. `tour-script.schema.json` is deliberately
shaped like a routing provider's directions response, so a tour should be *producible by a router*
rather than hand-written. This is that router: A* over the OpenStreetMap-derived pedestrian graph,
split into maneuver steps at turns and street changes, with instructions phrased the way Google, Bing
and Apple phrase them. The experience layer (dwell times, look-at targets, photo moments, narration,
the inspect handoff) is layered on top of the route, exactly as the schema intends.

Swapping in a real directions API later means replacing `route_leg` and nothing else.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from district_control import DistrictControl

REPO_ROOT = Path(__file__).resolve().parent.parent
DISTRICT = REPO_ROOT / "viewer" / "public" / "district"
TOURS = REPO_ROOT / "viewer" / "public" / "tours"

CONTRACT_VERSION = "1.0.0"
MODULE_ID = "dumbo-district"
BRIDGE_MODULE = "manhattan-bridge"

COMPASS = [
    (0, "north"), (45, "northeast"), (90, "east"), (135, "southeast"),
    (180, "south"), (225, "southwest"), (270, "west"), (315, "northwest"),
]


def bearing_name(deg: float) -> str:
    best = min(COMPASS, key=lambda c: abs(((deg - c[0] + 180) % 360) - 180))
    return best[1]


def maneuver_for(turn_deg: float, first: bool) -> str:
    if first:
        return "depart"
    if turn_deg > 160:
        return "uturn"
    if turn_deg > 110:
        return "turn-sharp-right"
    if turn_deg > 55:
        return "turn-right"
    if turn_deg > 20:
        return "turn-slight-right"
    if turn_deg < -160:
        return "uturn"
    if turn_deg < -110:
        return "turn-sharp-left"
    if turn_deg < -55:
        return "turn-left"
    if turn_deg < -20:
        return "turn-slight-left"
    return "continue"


class Graph:
    """Pedestrian graph loaded from walk-network.json."""

    def __init__(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self.nodes: list[tuple[float, float]] = [(p[0], p[1]) for p in data["nodes"]]
        self.adjacency: list[list[tuple[int, float, str | None]]] = [[] for _ in self.nodes]
        for edge in data["edges"]:
            a, b = edge["a"], edge["b"]
            weight = edge["len"] * (1.0 if edge["kind"] == "footway" else 1.25)
            name = edge.get("name")
            self.adjacency[a].append((b, weight, name))
            self.adjacency[b].append((a, weight, name))

    def nearest(self, x: float, y: float) -> int:
        return min(
            range(len(self.nodes)),
            key=lambda i: (self.nodes[i][0] - x) ** 2 + (self.nodes[i][1] - y) ** 2,
        )

    def route(self, start: int, goal: int) -> list[tuple[int, str | None]] | None:
        """A* returning [(node_index, name_of_edge_used_to_get_here), ...]."""
        import heapq

        def h(i: int) -> float:
            return math.dist(self.nodes[i], self.nodes[goal])

        open_heap = [(h(start), start)]
        came: dict[int, tuple[int, str | None]] = {}
        g = {start: 0.0}
        closed: set[int] = set()

        while open_heap:
            _, current = heapq.heappop(open_heap)
            if current == goal:
                break
            if current in closed:
                continue
            closed.add(current)
            for neighbour, weight, name in self.adjacency[current]:
                if neighbour in closed:
                    continue
                tentative = g[current] + weight
                if tentative >= g.get(neighbour, math.inf):
                    continue
                g[neighbour] = tentative
                came[neighbour] = (current, name)
                heapq.heappush(open_heap, (tentative + h(neighbour), neighbour))

        if goal not in came and goal != start:
            return None

        path: list[tuple[int, str | None]] = [(goal, None)]
        cursor = goal
        while cursor != start:
            previous, name = came[cursor]
            path[-1] = (cursor, name)
            path.append((previous, None))
            cursor = previous
        path.reverse()
        return path


def route_leg(
    control: DistrictControl,
    graph: Graph,
    a_lonlat: tuple[float, float],
    b_lonlat: tuple[float, float],
    pace: float,
    from_stop: str,
    to_stop: str,
    destination_name: str,
) -> dict:
    ax, ay, _ = control.geodetic_to_enu(*a_lonlat)
    bx, by, _ = control.geodetic_to_enu(*b_lonlat)
    path = graph.route(graph.nearest(ax, ay), graph.nearest(bx, by))
    if not path:
        raise SystemExit(f"no pedestrian route from {from_stop} to {to_stop}")

    routed: list[tuple[float, float]] = []
    routed_names: list[str | None] = []
    for index, name in path:
        node = graph.nodes[index]
        if routed and math.dist(node, routed[-1]) < 0.5:
            continue
        routed.append(node)
        routed_names.append(name)

    # Trim overshoot at both ends. The nearest graph node to a stop can sit past it, which would
    # otherwise show up as a spurious doubling-back manoeuvre right before arrival.
    if routed:
        head = min(range(min(3, len(routed))), key=lambda i: math.dist(routed[i], (ax, ay)))
        routed = routed[head:]
        routed_names = routed_names[head:]
    if routed:
        tail = min(range(len(routed)), key=lambda i: math.dist(routed[i], (bx, by)))
        routed = routed[: tail + 1]
        routed_names = routed_names[: tail + 1]

    points: list[tuple[float, float]] = [(ax, ay), *routed, (bx, by)]
    names: list[str | None] = [None, *routed_names, routed_names[-1] if routed_names else None]

    # Carry the last known street name *forward* across unnamed connector segments, which is what a
    # routing provider does: an unnamed kerb cut between two blocks of Water Street is still Water
    # Street. Deliberately not propagated backwards, which would claim a park path is a street.
    last_named: str | None = None
    for i, name in enumerate(names):
        if name:
            last_named = name
        else:
            names[i] = last_named

    # Split into maneuver steps at street-name changes and at real turns.
    steps: list[dict] = []
    current: list[tuple[float, float]] = [points[0]]
    current_name = names[1] if len(names) > 1 else None
    previous_bearing: float | None = None
    first = True

    def bearing(p: tuple[float, float], q: tuple[float, float]) -> float:
        return math.degrees(math.atan2(q[0] - p[0], q[1] - p[1])) % 360.0

    def flush(next_name: str | None, turn: float) -> None:
        nonlocal current, first
        if len(current) < 2:
            return
        distance = sum(math.dist(current[i], current[i + 1]) for i in range(len(current) - 1))
        if distance < 0.5:
            # Negligible stub, usually the stitch between a stop and its nearest network node.
            # Dropping it keeps `first` true so the next real segment becomes the depart step.
            current = [current[-1]]
            return
        if distance < 6 and steps:
            # Absorb slivers into the previous step rather than emitting noise a walker would
            # never be told out loud.
            previous_step = steps[-1]
            previous_step["path"]["geodetic"].extend(
                [list(control.enu_to_geodetic(p[0], p[1])[:2]) for p in current[1:]]
            )
            previous_step["distance_m"] = round(previous_step["distance_m"] + distance, 1)
            previous_step["duration_s"] = round(previous_step["distance_m"] / pace, 1)
            current = [current[-1]]
            return

        head = bearing(current[0], current[1])
        street = current_name or "the walkway"
        if first:
            instruction = f"Head {bearing_name(head)} on {street}"
        else:
            move = maneuver_for(turn, False)
            verb = {
                "turn-left": "Turn left onto",
                "turn-right": "Turn right onto",
                "turn-slight-left": "Bear left onto",
                "turn-slight-right": "Bear right onto",
                "turn-sharp-left": "Turn sharply left onto",
                "turn-sharp-right": "Turn sharply right onto",
                "uturn": "Turn around onto",
                "continue": "Continue on",
            }[move]
            instruction = f"{verb} {street}"

        steps.append(
            {
                "step_id": f"{from_stop}_{to_stop}_s{len(steps) + 1}",
                "maneuver": maneuver_for(turn, first),
                "instruction": instruction,
                **({"street_name": current_name} if current_name else {}),
                "distance_m": round(distance, 1),
                "duration_s": round(distance / pace, 1),
                "path": {
                    "geodetic": [
                        [round(v, 7) for v in control.enu_to_geodetic(p[0], p[1])[:2]]
                        for p in current
                    ]
                },
            }
        )
        first = False
        current = [current[-1]]

    for i in range(1, len(points)):
        current.append(points[i])
        if i + 1 >= len(points):
            break
        b1 = bearing(points[i - 1], points[i])
        b2 = bearing(points[i], points[i + 1])
        turn = ((b2 - b1 + 180) % 360) - 180
        next_name = names[i + 1]
        if next_name != current_name or abs(turn) > 35:
            flush(next_name, turn if previous_bearing is not None else 0.0)
            current_name = next_name
        previous_bearing = b2

    flush(None, 0.0)

    total_distance = sum(s["distance_m"] for s in steps)
    steps.append(
        {
            "step_id": f"{from_stop}_{to_stop}_arrive",
            "maneuver": "arrive",
            "instruction": f"Arrive at {destination_name}",
            "distance_m": 0.0,
            "duration_s": 0.0,
        }
    )

    return {
        "leg_id": f"{from_stop}__{to_stop}",
        "from_stop": from_stop,
        "to_stop": to_stop,
        "distance_m": round(total_distance, 1),
        "duration_s": round(total_distance / pace, 1),
        "transition": {"kind": "walk"},
        "steps": steps,
    }


# --------------------------------------------------------------------- tours


def family_of_four(control: DistrictControl, graph: Graph) -> dict:
    """
    'A family of 4 takes a tour starting at Brooklyn Bridge and hitting A, B, C, D stops,
    dwelling at each spot and taking pics.'

    Every stop coordinate is a real position: three came from OpenStreetMap named features and one
    is the surveyed Washington/Water intersection derived from the street network.
    """
    pace = control.value("DCTL-053")  # family pace, slower than DCTL-052

    stops_spec = [
        {
            "stop_id": "a_fulton_ferry",
            "name": "A · Fulton Ferry Landing, under the Brooklyn Bridge",
            "lonlat": (-73.995089, 40.703347),
            "dwell_s": 55,
            "heading_deg": 25,
            "on_arrive": [
                {
                    "type": "narrate",
                    "text": (
                        "We start where most people do: the old Fulton Ferry landing, directly under "
                        "the Brooklyn Bridge. Before 1883 this was the only way across."
                    ),
                    "duration_s": 11,
                },
                {
                    "type": "narrate",
                    "text": (
                        "Look east along the waterfront and you can see the whole of DUMBO, with the "
                        "Manhattan Bridge closing off the far end."
                    ),
                    "duration_s": 8,
                },
                {"type": "pan", "heading_deg": 72, "pitch_deg": 4, "duration_s": 4},
                {
                    "type": "capture_photo",
                    "framing": "wide",
                    "label": "DUMBO waterfront from Fulton Ferry",
                    "duration_s": 3,
                },
                {
                    "type": "narrate",
                    "text": "Everyone in? Good. Let's walk east along the waterfront.",
                    "duration_s": 5,
                },
            ],
        },
        {
            "stop_id": "b_janes_carousel",
            "name": "B · Jane's Carousel, Brooklyn Bridge Park",
            "lonlat": (-73.992385, 40.704434),
            "dwell_s": 60,
            "on_arrive": [
                {
                    "type": "narrate",
                    "text": (
                        "Jane's Carousel, built in 1922, sits in a glass pavilion right on the water "
                        "between the two bridges. The kids get one ride."
                    ),
                    "duration_s": 10,
                },
                {
                    "type": "look_at",
                    "target": {"asset": f"urn:d3d:{MODULE_ID}:landmark_janes_carousel"},
                    "duration_s": 7,
                },
                {"type": "group_photo", "framing": "normal", "label": "The family at the carousel", "duration_s": 3},
                {
                    "type": "look_at",
                    "target": {"asset": f"urn:d3d:{BRIDGE_MODULE}:bridge_proxy"},
                    "duration_s": 8,
                },
                {
                    "type": "narrate",
                    "text": "That is the Manhattan Bridge, and it is where we are heading next.",
                    "duration_s": 6,
                },
                {"type": "capture_photo", "framing": "tele", "label": "Manhattan Bridge from the park", "duration_s": 3},
            ],
        },
        {
            "stop_id": "c_washington_water",
            "name": "C · Washington Street at Water Street",
            "lonlat": (-73.989580, 40.703201),
            "dwell_s": 70,
            "heading_deg": 355,
            "on_arrive": [
                {
                    "type": "narrate",
                    "text": (
                        "This is the shot everybody comes for. Stand in the middle of Washington "
                        "Street, look north, and the Manhattan Bridge frames the Empire State "
                        "Building. Watch for cars."
                    ),
                    "duration_s": 13,
                },
                {"type": "pan", "heading_deg": 352, "pitch_deg": 16, "duration_s": 5},
                {
                    "type": "capture_photo",
                    "framing": "portrait",
                    "label": "The Washington Street view",
                    "capture": {"width": 1000, "height": 1400},
                    "duration_s": 4,
                },
                {"type": "group_photo", "framing": "portrait", "label": "Family on Washington Street", "duration_s": 3},
                {"type": "set_time_of_day", "time_of_day": "18:40", "duration_s": 2},
                {
                    "type": "narrate",
                    "text": "Golden hour. One more, then we walk under the bridge itself.",
                    "duration_s": 6,
                },
                {"type": "capture_photo", "framing": "wide", "label": "Washington Street at golden hour", "duration_s": 3},
            ],
        },
        {
            "stop_id": "d_anchorage",
            "name": "D · Anchorage Place, beneath the Manhattan Bridge",
            "lonlat": (-73.988012, 40.703296),
            "dwell_s": 75,
            "on_arrive": [
                {
                    "type": "narrate",
                    "text": (
                        "Anchorage Place runs beneath the Brooklyn anchorage: the masonry block that "
                        "holds the ends of all four main cables. Look up."
                    ),
                    "duration_s": 11,
                },
                {
                    "type": "look_at",
                    "target": {"asset": f"urn:d3d:{BRIDGE_MODULE}:bridge_proxy"},
                    "duration_s": 6,
                },
                {"type": "capture_photo", "framing": "wide", "label": "Under the Brooklyn anchorage", "duration_s": 3},
                {
                    "type": "narrate",
                    "text": (
                        "The neighbourhood model stops at the kerb here. The bridge itself belongs to "
                        "another team's model, so we hand over to their viewer to look at it properly."
                    ),
                    "duration_s": 12,
                },
                {
                    "type": "enter_inspect",
                    "module_id": BRIDGE_MODULE,
                    "entry_id": "brooklyn_anchorage",
                    "target": {"asset": f"urn:d3d:{BRIDGE_MODULE}:anchorage_brooklyn"},
                    "duration_s": 10,
                },
                {"type": "exit_inspect", "module_id": BRIDGE_MODULE, "duration_s": 2},
                {
                    "type": "wait_for_user",
                    "label": "Look around · press continue when you are ready",
                },
                {
                    "type": "narrate",
                    "text": "That is the tour. Thanks for walking with us.",
                    "duration_s": 5,
                },
            ],
        },
    ]

    stops = []
    for spec in stops_spec:
        lon, lat = spec["lonlat"]
        stop = {
            "stop_id": spec["stop_id"],
            "name": spec["name"],
            "position": {"lon": lon, "lat": lat, "height_m": 0, "vertical_datum": "NAVD88"},
            "dwell_s": spec["dwell_s"],
            "on_arrive": spec["on_arrive"],
            "tags": ["photo_spot"],
        }
        if "heading_deg" in spec:
            stop["heading_deg"] = spec["heading_deg"]
        stops.append(stop)

    legs = []
    for i in range(len(stops_spec) - 1):
        legs.append(
            route_leg(
                control, graph,
                stops_spec[i]["lonlat"], stops_spec[i + 1]["lonlat"],
                pace,
                stops_spec[i]["stop_id"], stops_spec[i + 1]["stop_id"],
                stops_spec[i + 1]["name"],
            )
        )

    walking = sum(leg["duration_s"] for leg in legs)
    distance = sum(leg["distance_m"] for leg in legs)
    dwell = sum(stop["dwell_s"] for stop in stops)

    return {
        "contract_version": CONTRACT_VERSION,
        "tour_id": "dumbo-family-of-four",
        "title": "DUMBO in an hour — a family of four",
        "description": (
            "A four-stop walking tour from the Brooklyn Bridge to the Manhattan Bridge, dwelling at "
            "each stop and taking photographs. Routed on the OpenStreetMap pedestrian network at a "
            "family walking pace, then dressed with narration, look-at targets, photo moments and a "
            "hand-off into the Manhattan Bridge team's inspect mode."
        ),
        "locale": "en-US",
        "requires_modules": [MODULE_ID],
        "party": {
            "size": 4,
            "label": "Family of 4",
            "members": [
                {"member_id": "adult_1", "name": "Parent", "role": "adult", "eye_height_m": control.value("DCTL-050")},
                {"member_id": "adult_2", "name": "Parent", "role": "adult", "eye_height_m": 1.72},
                {"member_id": "child_1", "name": "Older child", "role": "child", "eye_height_m": 1.35},
                {"member_id": "child_2", "name": "Younger child", "role": "child", "eye_height_m": control.value("DCTL-051")},
            ],
            "point_of_view": "adult_1",
            "pace_mps": pace,
            "accessibility": {"avoid_stairs": True},
        },
        "defaults": {
            "dwell_s": 45,
            "eye_height_m": control.value("DCTL-050"),
            "camera": {"rig": "first_person", "look": "forward", "bob": True, "fov_deg": 62},
            "transition": {"kind": "walk", "easing": "ease_in_out"},
            "viewer_mode": "walk",
            "speed_multiplier": 4,
            "time_of_day": "17:10",
            "weather": "clear",
        },
        "route_source": {
            "provider": "internal_router",
            "profile": "walking",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "attribution_text": "Route computed on OpenStreetMap data © OpenStreetMap contributors, ODbL",
            "notes": (
                "A* over the district walk network, split into maneuver steps at turns over 35 "
                "degrees and at street-name changes. Replace with a Google, Bing or Apple "
                "directions response by rewriting scripts/build_tour.py::route_leg and nothing else."
            ),
        },
        "stops": stops,
        "legs": legs,
        "totals": {
            "distance_m": round(distance, 1),
            "walking_duration_s": round(walking, 1),
            "dwell_duration_s": round(dwell, 1),
            "estimated_duration_s": round(walking + dwell, 1),
        },
    }


def accessible_short(control: DistrictControl, graph: Graph) -> dict:
    """A second, deliberately different tour, to prove the player is not built around one script."""
    pace = 0.9
    stops_spec = [
        {
            "stop_id": "s1_main_street",
            "name": "Main Street lawn",
            "lonlat": (-73.993345, 40.703529),
            "dwell_s": 35,
            "on_arrive": [
                {"type": "narrate", "text": "A short, step-free loop. We keep to the level ground.", "duration_s": 6},
                {"type": "capture_photo", "framing": "wide", "label": "Main Street lawn", "duration_s": 3},
            ],
        },
        {
            "stop_id": "s2_empire_stores",
            "name": "Empire Fulton Ferry",
            "lonlat": (-73.992222, 40.704272),
            "dwell_s": 40,
            "on_arrive": [
                {"type": "look_at", "target": {"asset": f"urn:d3d:{BRIDGE_MODULE}:bridge_proxy"}, "duration_s": 6},
                {"type": "capture_photo", "framing": "normal", "label": "Empire Fulton Ferry", "duration_s": 3},
            ],
        },
    ]

    stops = [
        {
            "stop_id": spec["stop_id"],
            "name": spec["name"],
            "position": {"lon": spec["lonlat"][0], "lat": spec["lonlat"][1], "height_m": 0, "vertical_datum": "NAVD88"},
            "dwell_s": spec["dwell_s"],
            "on_arrive": spec["on_arrive"],
        }
        for spec in stops_spec
    ]

    legs = [
        route_leg(
            control, graph,
            stops_spec[0]["lonlat"], stops_spec[1]["lonlat"], pace,
            stops_spec[0]["stop_id"], stops_spec[1]["stop_id"], stops_spec[1]["name"],
        )
    ]

    return {
        "contract_version": CONTRACT_VERSION,
        "tour_id": "dumbo-step-free-short",
        "title": "Step-free waterfront short loop",
        "description": "A brief, level, wheelchair-friendly walk along the Brooklyn Bridge Park edge.",
        "requires_modules": [MODULE_ID],
        "party": {
            "size": 2,
            "label": "Two visitors, step-free",
            "pace_mps": pace,
            "accessibility": {"avoid_stairs": True, "avoid_steep_grades": True, "max_grade_percent": 5},
        },
        "defaults": {
            "dwell_s": 35,
            "camera": {"rig": "first_person", "look": "forward", "bob": False},
            "speed_multiplier": 3,
            "time_of_day": "11:00",
            "viewer_mode": "walk",
        },
        "route_source": {"provider": "internal_router", "profile": "wheelchair"},
        "stops": stops,
        "legs": legs,
        "totals": {
            "distance_m": legs[0]["distance_m"],
            "walking_duration_s": legs[0]["duration_s"],
            "dwell_duration_s": sum(s["dwell_s"] for s in stops),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    control = DistrictControl()
    network_path = DISTRICT / "walk-network.json"
    if not network_path.is_file():
        raise SystemExit("walk-network.json is missing; run build_district_assets.py first")
    graph = Graph(network_path)
    print(f"walk graph      : {len(graph.nodes)} nodes")

    TOURS.mkdir(parents=True, exist_ok=True)
    index = []

    for builder in (family_of_four, accessible_short):
        tour = builder(control, graph)
        name = f"{tour['tour_id']}.json"
        (TOURS / name).write_text(json.dumps(tour, indent=1), encoding="utf-8")
        steps = sum(len(leg["steps"]) for leg in tour["legs"])
        print(
            f"  {tour['tour_id']:28s} {len(tour['stops'])} stops, "
            f"{len(tour['legs'])} legs, {steps} steps, "
            f"{tour['totals']['distance_m']:.0f} m"
        )
        index.append(
            {
                "id": tour["tour_id"],
                "title": tour["title"],
                "url": f"tours/{name}",
                "description": tour.get("description", ""),
            }
        )

    (TOURS / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"wrote {len(index)} tour(s) and tours/index.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
