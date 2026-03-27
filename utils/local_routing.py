from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import networkx as nx


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2.0) ** 2
    return float(r * (2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))))


def _safe_name(p: dict[str, Any], fallback: str) -> str:
    name = p.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    tags = p.get("tags")
    if isinstance(tags, dict):
        tag_name = tags.get("name")
        if isinstance(tag_name, str) and tag_name.strip():
            return tag_name.strip()
    return fallback


def _load_geojson_points(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    features = payload.get("features")
    if not isinstance(features, list):
        return []
    rows: list[dict[str, Any]] = []
    for ft in features:
        if not isinstance(ft, dict):
            continue
        geom = ft.get("geometry")
        props = ft.get("properties") if isinstance(ft.get("properties"), dict) else {}
        if not isinstance(geom, dict) or str(geom.get("type")) != "Point":
            continue
        coords = geom.get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        try:
            lon = float(coords[0])
            lat = float(coords[1])
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "lat": lat,
                "lon": lon,
                "name": props.get("name"),
                "tags": props.get("tags") if isinstance(props.get("tags"), dict) else {},
            }
        )
    return rows


def _nearest_distance(lat: float, lon: float, points: list[dict[str, Any]]) -> float:
    best = float("inf")
    for p in points:
        try:
            d = _haversine_m(lat, lon, float(p["lat"]), float(p["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if d < best:
            best = d
    return best


def _nearest_detail(lat: float, lon: float, points: list[dict[str, Any]], fallback: str) -> dict[str, Any]:
    best_p: dict[str, Any] | None = None
    best_d = float("inf")
    for p in points:
        try:
            d = _haversine_m(lat, lon, float(p["lat"]), float(p["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if d < best_d:
            best_d = d
            best_p = p
    if best_p is None:
        return {"name": fallback, "distance_m": None, "lat": None, "lon": None}
    return {
        "name": _safe_name(best_p, fallback),
        "distance_m": float(round(best_d, 1)),
        "lat": float(best_p["lat"]),
        "lon": float(best_p["lon"]),
    }


def _segment_score(mid_lat: float, mid_lon: float, lamps: list[dict[str, Any]], police: list[dict[str, Any]], metro: list[dict[str, Any]], *, time_mode: str) -> tuple[float, bool]:
    d_l = _nearest_distance(mid_lat, mid_lon, lamps)
    d_p = _nearest_distance(mid_lat, mid_lon, police)
    d_m = _nearest_distance(mid_lat, mid_lon, metro)

    unknown = (d_l == float("inf")) and (d_p == float("inf"))
    if time_mode == "night":
        lamp_part = max(0.0, 60.0 - (0.35 * d_l if d_l != float("inf") else 60.0))
        police_part = max(0.0, 40.0 - (0.05 * d_p if d_p != float("inf") else 40.0))
    else:
        lamp_part = max(0.0, 50.0 - (0.25 * d_l if d_l != float("inf") else 50.0))
        police_part = max(0.0, 35.0 - (0.04 * d_p if d_p != float("inf") else 35.0))
    metro_bonus = 0.0
    if d_m != float("inf") and d_m < 450.0:
        metro_bonus = max(0.0, 20.0 - (d_m / 25.0))
    score = lamp_part + police_part + metro_bonus + 15.0
    score = max(5.0, min(98.0, score))
    return float(score), bool(unknown)


def compute_local_route_payload(
    *,
    data_dir: Path,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    time_mode: str,
) -> dict[str, Any]:
    police = _load_geojson_points(data_dir / "police_stations.geojson")
    lamps = _load_geojson_points(data_dir / "street_lamps.geojson")
    metro = _load_geojson_points(data_dir / "metro_stations.geojson")
    transit = _load_geojson_points(data_dir / "transit.geojson")
    traces = _load_geojson_points(data_dir / "traces.geojson")

    # Transit verisinde metro benzeri noktalar varsa metro listesine ekle.
    for p in transit:
        if p not in metro:
            metro.append(p)

    graph_nodes: list[dict[str, float]] = [{"lat": start_lat, "lon": start_lon}, {"lat": end_lat, "lon": end_lon}]
    for src in (traces, police, lamps, metro):
        for p in src[:120]:
            try:
                graph_nodes.append({"lat": float(p["lat"]), "lon": float(p["lon"])})
            except (KeyError, TypeError, ValueError):
                continue

    # Yinelenenleri kaba şekilde kırp.
    dedup: dict[tuple[int, int], dict[str, float]] = {}
    for p in graph_nodes:
        key = (round(float(p["lat"]) * 1e5), round(float(p["lon"]) * 1e5))
        dedup[key] = p
    graph_nodes = list(dedup.values())[:220]

    G = nx.Graph()
    for i, p in enumerate(graph_nodes):
        G.add_node(i, lat=float(p["lat"]), lon=float(p["lon"]))

    k = 7
    for i, p in enumerate(graph_nodes):
        dists: list[tuple[float, int]] = []
        for j, q in enumerate(graph_nodes):
            if i == j:
                continue
            d = _haversine_m(float(p["lat"]), float(p["lon"]), float(q["lat"]), float(q["lon"]))
            dists.append((d, j))
        dists.sort(key=lambda x: x[0])
        for d, j in dists[:k]:
            mid_lat = (float(p["lat"]) + float(graph_nodes[j]["lat"])) / 2.0
            mid_lon = (float(p["lon"]) + float(graph_nodes[j]["lon"])) / 2.0
            score, _ = _segment_score(mid_lat, mid_lon, lamps, police, metro, time_mode=time_mode)
            # Güvenli kenarlar daha düşük maliyetli olsun.
            edge_cost = float(d * (1.9 - (score / 100.0)))
            G.add_edge(i, j, distance_m=float(d), safety_score=score, cost=edge_cost)

    try:
        path = nx.shortest_path(G, source=0, target=1, weight="cost")
    except Exception:
        path = [0, 1]

    polyline: list[dict[str, float]] = []
    segments: list[dict[str, Any]] = []
    total_len = 0.0
    weighted_score = 0.0
    unknown_len = 0.0

    for idx, node_id in enumerate(path):
        node = G.nodes[node_id]
        polyline.append({"lat": float(node["lat"]), "lon": float(node["lon"])})
        if idx == 0:
            continue
        prev_id = path[idx - 1]
        prev = G.nodes[prev_id]
        edge = G.get_edge_data(prev_id, node_id) or {}
        seg_len = float(edge.get("distance_m", _haversine_m(prev["lat"], prev["lon"], node["lat"], node["lon"])))
        score, unknown = _segment_score(
            (float(prev["lat"]) + float(node["lat"])) / 2.0,
            (float(prev["lon"]) + float(node["lon"])) / 2.0,
            lamps,
            police,
            metro,
            time_mode=time_mode,
        )
        total_len += seg_len
        weighted_score += score * seg_len
        if unknown:
            unknown_len += seg_len

        if score > 80:
            category = "high"
            popup = "Çok Güvenli"
        elif score >= 50:
            category = "medium"
            popup = "Orta Güvenli"
        else:
            category = "low"
            popup = "Düşük Güvenli"

        pd = _nearest_distance((prev["lat"] + node["lat"]) / 2.0, (prev["lon"] + node["lon"]) / 2.0, police)
        md = _nearest_distance((prev["lat"] + node["lat"]) / 2.0, (prev["lon"] + node["lon"]) / 2.0, metro)
        segments.append(
            {
                "points": [{"lat": float(prev["lat"]), "lon": float(prev["lon"])}, {"lat": float(node["lat"]), "lon": float(node["lon"])}],
                "safety_score": float(score),
                "category": category,
                "unknown": bool(unknown),
                "popup": popup,
                "nearest_metro_dist": None if md == float("inf") else float(round(md, 1)),
                "nearest_police_dist": None if pd == float("inf") else float(round(pd, 1)),
            }
        )

    safety_score = float((weighted_score / total_len) if total_len > 0 else 55.0)
    unknown_ratio = float((unknown_len / total_len) if total_len > 0 else 0.0)
    seg_scores = [float(s.get("safety_score", 0.0)) for s in segments]
    seg_min = min(seg_scores) if seg_scores else None
    seg_max = max(seg_scores) if seg_scores else None

    if safety_score > 80:
        label = "Güvenli rota"
    elif safety_score >= 50:
        label = "Orta Güvenli"
    else:
        label = "Düşük Güvenli"

    nearby_stations: list[dict[str, Any]] = []
    for m in metro[:200]:
        try:
            mlat = float(m["lat"])
            mlon = float(m["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        best_d = float("inf")
        for p in polyline:
            d = _haversine_m(mlat, mlon, float(p["lat"]), float(p["lon"]))
            if d < best_d:
                best_d = d
        if best_d <= 900.0:
            nearby_stations.append({"lat": mlat, "lon": mlon, "name": _safe_name(m, "Metro İstasyonu")})

    advisor_segments: list[dict[str, Any]] = []
    distance_cursor = 0.0
    for seg in segments:
        pts = seg.get("points") or []
        if not isinstance(pts, list) or len(pts) < 2:
            continue
        s = pts[0]
        e = pts[-1]
        try:
            slat = float(s["lat"])
            slon = float(s["lon"])
            elat = float(e["lat"])
            elon = float(e["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        seg_len = _haversine_m(slat, slon, elat, elon)
        mid_lat = (slat + elat) / 2.0
        mid_lon = (slon + elon) / 2.0
        advisor_segments.append(
            {
                "segment_length_m": float(seg_len),
                "along_route_start_m": float(distance_cursor),
                "along_route_end_m": float(distance_cursor + seg_len),
                "along_route_mid_m": float(distance_cursor + (seg_len / 2.0)),
                "start": {"lat": slat, "lon": slon},
                "end": {"lat": elat, "lon": elon},
                "midpoint": {"lat": mid_lat, "lon": mid_lon},
                "nearest_police": _nearest_detail(mid_lat, mid_lon, police, "Karakol"),
                "nearest_pharmacy": {"name": None, "distance_m": None, "lat": None, "lon": None},
                "nearest_metro": _nearest_detail(mid_lat, mid_lon, metro, "Metro"),
                "nearest_taxi": {"name": None, "distance_m": None, "lat": None, "lon": None},
                "lighting": {
                    "nearest_lamp_distance_m": _nearest_detail(mid_lat, mid_lon, lamps, "Lamba").get("distance_m"),
                    "lighting_available": bool(lamps),
                    "lat": None,
                    "lon": None,
                },
            }
        )
        distance_cursor += seg_len

    return {
        "polyline": polyline,
        "total_cost": float(total_len),
        "edge_count": int(max(0, len(polyline) - 1)),
        "average_safety_score": float(safety_score / 100.0),
        "safety_score": float(safety_score),
        "unknown_ratio": float(unknown_ratio),
        "label": str(label),
        "segment_score_min": seg_min,
        "segment_score_max": seg_max,
        "segments": segments,
        "nearby_stations": nearby_stations,
        "advisor_segments": advisor_segments,
        "time_mode": "night" if str(time_mode).lower() == "night" else "day",
        "night_analysis": bool(str(time_mode).lower() == "night"),
    }
