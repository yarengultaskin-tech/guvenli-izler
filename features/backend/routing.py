"""Faz 3: Safety-weighted routing using OSM street graph (OSMnx).

MVP yaklaşımı:
- Grafı sadece mevcut Çankaya bbox'u içinde kur.
- Lambalara (street_lamps) ve karakollara (police_stations) göre kenar maliyeti üret.
- Güvenli rota için düşük maliyet = daha güvenli (cost = 1 - safety_score).

Not: Mobilite (H) ve kullanıcı risk bildirimi (R) bu ilk MVP'de 0 kabul edilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any, Iterable, Optional
import time

import networkx as nx
import numpy as np
import osmnx as ox
import httpx

from utils.scoring_config import SafetyWeights, compute_segment_score

from backend.osm_layers import fetch_layer_points


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    radius_m = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_m * c


def _bbox_to_nsew(bbox: dict[str, float]) -> tuple[float, float, float, float]:
    north = float(bbox["max_latitude"])
    south = float(bbox["min_latitude"])
    east = float(bbox["max_longitude"])
    west = float(bbox["min_longitude"])
    return north, south, east, west


def _graph_key(bbox: dict[str, float]) -> str:
    return (
        f"{bbox['min_latitude']},{bbox['min_longitude']},"
        f"{bbox['max_latitude']},{bbox['max_longitude']}"
    )


def _extract_edge_cost_and_score(
    *,
    edge_midpoint: tuple[float, float] | None,
    nearest_lamp_distance_m: float,
    nearest_police_distance_m: float,
    nearest_transit_distance_m: float,
    weights: SafetyWeights,
    lamp_cutoff_m: float,
    police_cutoff_m: float,
    transit_cutoff_m: float,
) -> tuple[float, float]:
    # illumination (A): daha yakın lamba => daha yüksek A
    illumination = 1.0 - (nearest_lamp_distance_m / lamp_cutoff_m)
    official_security_proximity = 1.0 - (nearest_police_distance_m / police_cutoff_m)
    mobility = 1.0 - (nearest_transit_distance_m / transit_cutoff_m)

    illumination = _clamp(illumination)
    official_security_proximity = _clamp(official_security_proximity)
    mobility = _clamp(mobility)

    safety_score = compute_segment_score(
        illumination=illumination,
        mobility=mobility,
        official_security_proximity=official_security_proximity,
        user_reported_risk=0.0,
        weights=weights,
    )
    safety_score = _clamp(safety_score)
    cost = 1.0 - safety_score
    cost = _clamp(cost)
    _ = edge_midpoint
    return cost, safety_score


# Metro / giriş (railway=station, amenity=subway_entrance) mesafe bonusları
METRO_BONUS_INNER_M = 200.0
METRO_BONUS_OUTER_M = 400.0
METRO_BONUS_INNER_PTS = 25.0
METRO_BONUS_OUTER_PTS = 10.0
# nearby_stations listesi: rotaya en çok bu kadar yakın istasyonları döndür
ROUTE_STATION_DISPLAY_MAX_M = 900.0
# POI çekimi: segment popup (karakol/metro mesafe) için yeterli kapsama
POI_ROUTE_BUFFER_M = 10_000.0
CANKAYA_FALLBACK_BBOX: dict[str, float] = {
    "min_latitude": 39.895,
    "min_longitude": 32.82,
    "max_latitude": 39.97,
    "max_longitude": 32.93,
}


@dataclass(frozen=True)
class SafetyRoutingResult:
    polyline: list[dict[str, float]]
    total_cost: float
    edge_count: int
    average_safety_score: float
    safety_score: float
    unknown_ratio: float
    label: str
    segments: list[dict[str, Any]]
    segment_score_min: float | None = None
    segment_score_max: float | None = None
    time_mode: str = "day"
    night_analysis: bool = False
    nearby_stations: tuple[dict[str, Any], ...] = ()
    # 250m sabit parçalara ayrılmış, her parça için yakın POI/aydınlatma bağlamı.
    advisor_segments: list[dict[str, Any]] = field(default_factory=list)


def _normalize_overpass_lat_lon(el: dict[str, Any]) -> tuple[float, float] | None:
    lat = el.get("lat")
    lon = el.get("lon")
    if lat is None or lon is None:
        center = el.get("center") or {}
        lat = center.get("lat")
        lon = center.get("lon")
    if lat is None or lon is None:
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def _overpass_points_in_bbox(
    *,
    bbox: dict[str, float],
    query: str,
    max_points: int = 800,
) -> list[dict[str, Any]]:
    """Overpass ile bbox içinde POI noktalarını çek (soft-fail)."""
    s = float(bbox["min_latitude"])
    w = float(bbox["min_longitude"])
    n = float(bbox["max_latitude"])
    e = float(bbox["max_longitude"])
    bbox_str = f"{s},{w},{n},{e}"
    q = query.replace("{bbox}", bbox_str)

    last_error: Optional[Exception] = None
    for endpoint in (
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
    ):
        try:
            # Synchronous client: routing.py zaten sync/threads içinde çalışıyor.
            with httpx.Client(timeout=80.0) as client:
                response = client.post(endpoint, data={"data": q})
                response.raise_for_status()
                raw = response.json()
            elements = raw.get("elements", [])
            if not isinstance(elements, list):
                return []
            out: list[dict[str, Any]] = []
            for el in elements:
                ll = _normalize_overpass_lat_lon(el)
                if ll is None:
                    continue
                lat, lon = ll
                tags = el.get("tags") or {}
                if not isinstance(tags, dict):
                    tags = {}
                out.append(
                    {
                        "latitude": lat,
                        "longitude": lon,
                        "name": tags.get("name"),
                        "tags": tags,
                        "layer": "overpass",
                        "source": "OpenStreetMap",
                    }
                )
                if len(out) >= max_points:
                    break
            return out
        except Exception as exc:
            last_error = exc
            continue
    _ = last_error
    return []


def _interpolate_point_at_distance(
    points: list[dict[str, float]],
    cum_dist_m: list[float],
    distance_m: float,
) -> tuple[dict[str, float], int]:
    """points üzerinde distance_m noktasını yaklaşık lineer enterpolasyonla bulur."""
    if not points:
        return {"lat": 0.0, "lon": 0.0}, 0
    if distance_m <= 0.0:
        return {"lat": points[0]["lat"], "lon": points[0]["lon"]}, 0
    total = cum_dist_m[-1]
    if distance_m >= total:
        last = points[-1]
        return {"lat": last["lat"], "lon": last["lon"]}, len(points) - 2

    # cum_dist_m[i] <= d <= cum_dist_m[i+1]
    idx = 0
    while idx < len(cum_dist_m) - 1 and cum_dist_m[idx + 1] < distance_m:
        idx += 1
    d0 = cum_dist_m[idx]
    d1 = cum_dist_m[idx + 1]
    if d1 <= d0:
        p = points[idx]
        return {"lat": p["lat"], "lon": p["lon"]}, idx
    t = (distance_m - d0) / (d1 - d0)
    p0 = points[idx]
    p1 = points[idx + 1]
    return {"lat": p0["lat"] + t * (p1["lat"] - p0["lat"]), "lon": p0["lon"] + t * (p1["lon"] - p0["lon"])}, idx


def _resample_polyline_fixed_step(
    polyline: list[dict[str, float]],
    *,
    step_m: float = 250.0,
) -> list[dict[str, Any]]:
    """PolyLine'ı step_m aralıklarla segmentlere böler (start/end/midpoint + kısa points)."""
    if len(polyline) < 2:
        return []

    # cumulative distances between vertices
    cum_dist_m: list[float] = [0.0]
    for i in range(1, len(polyline)):
        prev = polyline[i - 1]
        cur = polyline[i]
        cum_dist_m.append(
            cum_dist_m[-1]
            + _haversine_distance_m(
                float(prev["lat"]),
                float(prev["lon"]),
                float(cur["lat"]),
                float(cur["lon"]),
            )
        )
    total = cum_dist_m[-1]
    if total <= 0.0:
        return []

    out: list[dict[str, Any]] = []
    n_segments = int(math.ceil(total / float(step_m)))
    for si in range(n_segments):
        start_d = float(si) * float(step_m)
        end_d = min(float(si + 1) * float(step_m), total)
        mid_d = (start_d + end_d) / 2.0

        start_pt, _ = _interpolate_point_at_distance(polyline, cum_dist_m, start_d)
        end_pt, end_idx = _interpolate_point_at_distance(polyline, cum_dist_m, end_d)
        mid_pt, _ = _interpolate_point_at_distance(polyline, cum_dist_m, mid_d)

        # segment çizimi için mevcut polyline'dan o aralığı seç
        seg_points: list[dict[str, float]] = [start_pt]
        for i in range(len(polyline)):
            if cum_dist_m[i] > start_d and cum_dist_m[i] < end_d:
                seg_points.append({"lat": polyline[i]["lat"], "lon": polyline[i]["lon"]})
        seg_points.append(end_pt)

        seg_len = max(0.0, end_d - start_d)
        out.append(
            {
                "points": seg_points,
                "segment_length_m": seg_len,
                "along_route_start_m": float(start_d),
                "along_route_end_m": float(end_d),
                "along_route_mid_m": float(mid_d),
                "start": start_pt,
                "end": end_pt,
                "midpoint": mid_pt,
            }
        )
    return out


def _nearest_point_with_details(
    points: list[dict[str, Any]],
    lat: float,
    lon: float,
) -> tuple[float, Optional[str], Optional[float], Optional[float]]:
    """En yakın POI'yi mesafe + isim + koordinat ile döndür."""
    best_d = float("inf")
    best_name: Optional[str] = None
    best_lat: Optional[float] = None
    best_lon: Optional[float] = None
    for p in points:
        p_lat = p.get("latitude", p.get("lat"))
        p_lon = p.get("longitude", p.get("lon"))
        if p_lat is None or p_lon is None:
            continue
        try:
            p_lat_f = float(p_lat)
            p_lon_f = float(p_lon)
        except (TypeError, ValueError):
            continue
        d = _haversine_distance_m(lat, lon, p_lat_f, p_lon_f)
        if d < best_d:
            best_d = d
            name = p.get("name")
            best_name = str(name) if name else None
            best_lat = p_lat_f
            best_lon = p_lon_f
    return best_d, best_name, best_lat, best_lon


_SAFE_GRAPH_CACHE: dict[str, dict[str, Any]] = {}


def _local_bbox_from_points(
    *,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    buffer_m: float,
) -> dict[str, float]:
    # Rough meters->degrees conversion. Good enough for ~500m buffers.
    mid_lat = (float(start_lat) + float(end_lat)) / 2.0
    lat_deg_per_m = 1.0 / 111_320.0
    lon_deg_per_m = 1.0 / (111_320.0 * max(0.2, math.cos(math.radians(mid_lat))))
    lat_buf = float(buffer_m) * lat_deg_per_m
    lon_buf = float(buffer_m) * lon_deg_per_m

    min_lat = min(float(start_lat), float(end_lat)) - lat_buf
    max_lat = max(float(start_lat), float(end_lat)) + lat_buf
    min_lon = min(float(start_lon), float(end_lon)) - lon_buf
    max_lon = max(float(start_lon), float(end_lon)) + lon_buf
    return {
        "min_latitude": min_lat,
        "min_longitude": min_lon,
        "max_latitude": max_lat,
        "max_longitude": max_lon,
    }


def _bbox_from_path_nodes(
    graph: nx.DiGraph | nx.MultiDiGraph,
    path_nodes: list[Any],
    buffer_m: float,
) -> dict[str, float]:
    """Rota üzerindeki tüm düğümleri kapsayan bbox (+ buffer). Eğri rotalarda metro POI kaybını önler."""
    if not path_nodes:
        return _local_bbox_from_points(
            start_lat=39.92,
            start_lon=32.86,
            end_lat=39.92,
            end_lon=32.86,
            buffer_m=buffer_m,
        )
    lats: list[float] = []
    lons: list[float] = []
    for n in path_nodes:
        data = graph.nodes[n]
        lats.append(float(data["y"]))
        lons.append(float(data["x"]))
    mid_lat = sum(lats) / len(lats)
    lat_deg_per_m = 1.0 / 111_320.0
    lon_deg_per_m = 1.0 / (111_320.0 * max(0.2, math.cos(math.radians(mid_lat))))
    lat_buf = float(buffer_m) * lat_deg_per_m
    lon_buf = float(buffer_m) * lon_deg_per_m
    return {
        "min_latitude": min(lats) - lat_buf,
        "max_latitude": max(lats) + lat_buf,
        "min_longitude": min(lons) - lon_buf,
        "max_longitude": max(lons) + lon_buf,
    }


def _is_metro_like_osm_point(p: dict[str, Any]) -> bool:
    """transit katmanından metro-adayı (OSM etiketleri; map_view ile uyumlu)."""
    tags = p.get("tags") or {}
    if not isinstance(tags, dict):
        return False
    railway = tags.get("railway")
    station = tags.get("station")
    pt = tags.get("public_transport")
    amenity = tags.get("amenity")
    return (
        railway in {"station", "subway_entrance"}
        or station == "subway"
        or pt == "station"
        or amenity == "subway_entrance"
    )


def _dedupe_osm_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[int, int]] = set()
    out: list[dict[str, Any]] = []
    for p in points:
        ll = _poi_lat_lon(p)
        if ll is None:
            continue
        key = (round(ll[0] * 1e5), round(ll[1] * 1e5))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _poi_lat_lon(p: dict[str, Any]) -> tuple[float, float] | None:
    """OSM noktası: latitude/longitude veya lat/lon (eski önbellek / farklı kaynak)."""
    plat = p.get("latitude", p.get("lat"))
    plon = p.get("longitude", p.get("lon"))
    if plat is None or plon is None:
        return None
    try:
        return float(plat), float(plon)
    except (TypeError, ValueError):
        return None


def _poi_lat_lon_arrays(points: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    lats: list[float] = []
    lons: list[float] = []
    for p in points:
        ll = _poi_lat_lon(p)
        if ll is None:
            continue
        lats.append(ll[0])
        lons.append(ll[1])
    if not lats:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    return np.asarray(lats, dtype=np.float64), np.asarray(lons, dtype=np.float64)


def _min_haversine_m_np(lat_arr: np.ndarray, lon_arr: np.ndarray, qlat: float, qlon: float) -> float:
    """(qlat, qlon) noktasından POI dizisine vektörel kuş uçuşu en kısa mesafe (m)."""
    if lat_arr.size == 0:
        return float("inf")
    r = 6371000.0
    phi1 = math.radians(qlat)
    phi2 = np.radians(lat_arr)
    dphi = np.radians(lat_arr - qlat)
    dlamb = np.radians(lon_arr - qlon)
    a = np.sin(dphi / 2.0) ** 2 + math.cos(phi1) * np.cos(phi2) * np.sin(dlamb / 2.0) ** 2
    c = 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))
    return float(r * np.min(c))


def _edge_midpoint_lat_lon(graph: nx.DiGraph, u: Any, v: Any, data: dict[str, Any]) -> tuple[float, float]:
    geom = data.get("geometry")
    if geom is not None:
        try:
            m = geom.interpolate(0.5, normalized=True)
            return float(m.y), float(m.x)
        except Exception:
            pass
    return (
        (float(graph.nodes[u]["y"]) + float(graph.nodes[v]["y"])) / 2.0,
        (float(graph.nodes[u]["x"]) + float(graph.nodes[v]["x"])) / 2.0,
    )


def _normalize_route_time_mode(raw: str | None) -> str:
    """API/UI: night | Gece | gece → night; diğerleri day."""
    if raw is None:
        return "day"
    t = str(raw).strip().lower()
    if t in ("night", "gece", "n") or "gece" in t:
        return "night"
    return "day"


def _build_night_routing_graph(
    length_graph: nx.DiGraph,
    lamps: list[dict[str, Any]],
    police: list[dict[str, Any]],
) -> nx.DiGraph:
    """Gece: aydınlatması zayıf kenarların maliyeti en fazla 10× uzunluk (karanlık sokaktan kaçın)."""
    lamp_lat, lamp_lon = _poi_lat_lon_arrays(lamps)
    pol_lat, pol_lon = _poi_lat_lon_arrays(police)
    G = nx.DiGraph()
    G.add_nodes_from(length_graph.nodes(data=True))
    for u, v, data in length_graph.edges(data=True):
        length = float(data.get("length", 0.0) or 0.0)
        base_len = max(length, 0.01)
        mlat, mlon = _edge_midpoint_lat_lon(length_graph, u, v, data)
        d_l = _min_haversine_m_np(lamp_lat, lamp_lon, mlat, mlon)
        d_p = _min_haversine_m_np(pol_lat, pol_lon, mlat, mlon)
        if d_l == float("inf"):
            lamp_q = 0.0
        else:
            lamp_q = _clamp(1.0 - (d_l / 130.0))
        if d_p == float("inf"):
            police_q = 0.12
        else:
            police_q = _clamp(1.0 - (d_p / 850.0))
        if not lamps:
            lamp_q = 0.0
        safety_blend = 0.62 * lamp_q + 0.38 * police_q
        darkness = _clamp(1.0 - safety_blend, 0.0, 1.0)
        # Ana arterler genelde daha iyi aydınlık varsayımı → gece cezasını hafiflet
        hw = data.get("highway")
        if isinstance(hw, list) and hw:
            hw = hw[0]
        hw_s = str(hw or "")
        if hw_s in {"primary", "secondary", "tertiary", "trunk", "unclassified"}:
            darkness *= 0.65
            darkness = _clamp(darkness, 0.0, 1.0)
        # lamp_q düşük ⇒ darkness yüksek ⇒ maliyet en fazla 10× (1 + 9·darkness)
        mult = 1.0 + 9.0 * darkness
        routing_cost = float(base_len * mult)
        ed = dict(data)
        ed["routing_cost"] = routing_cost
        G.add_edge(u, v, **ed)
    return G


def _shortest_path_nodes_for_mode(
    length_graph: nx.DiGraph,
    start_node: Any,
    end_node: Any,
    time_mode: str,
    lamps: list[dict[str, Any]],
    police: list[dict[str, Any]],
) -> list[Any]:
    if _normalize_route_time_mode(time_mode) == "night":
        ng = _build_night_routing_graph(length_graph, lamps, police)
        return nx.shortest_path(ng, start_node, end_node, weight="routing_cost")
    return nx.shortest_path(length_graph, start_node, end_node, weight="length")


def nearest_poi_distance_m(points: list[dict[str, Any]], lat: float, lon: float) -> float:
    """Tüm OSM POI listesi üzerinde kuş uçuşu en kısa mesafe (limit yok; lamba/karakol/metro)."""
    lat_arr, lon_arr = _poi_lat_lon_arrays(points)
    return _min_haversine_m_np(lat_arr, lon_arr, lat, lon)


def _route_midpoint_and_dist_m(
    *,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    buffer_m: float,
) -> tuple[tuple[float, float], float]:
    mid_lat = (float(start_lat) + float(end_lat)) / 2.0
    mid_lon = (float(start_lon) + float(end_lon)) / 2.0
    direct_m = _haversine_distance_m(float(start_lat), float(start_lon), float(end_lat), float(end_lon))
    # Radius that covers both endpoints + buffer.
    radius_m = (direct_m / 2.0) + float(buffer_m)
    return (mid_lat, mid_lon), max(200.0, radius_m)


def _graph_key_for_route(
    *,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    buffer_m: float,
) -> str:
    # Round to avoid cache misses due to tiny float diffs.
    return (
        f"{round(start_lat, 5)},{round(start_lon, 5)}->"
        f"{round(end_lat, 5)},{round(end_lon, 5)}"
        f"|buf={int(buffer_m)}"
    )


def _to_simple_digraph_by_length(graph: nx.MultiDiGraph) -> nx.DiGraph:
    """Convert MultiDiGraph to DiGraph selecting the shortest edge per (u,v)."""
    g = nx.DiGraph()
    g.add_nodes_from(graph.nodes(data=True))
    for u, v, data in graph.edges(data=True):
        length = float(data.get("length", 0.0) or 0.0)
        existing = g.get_edge_data(u, v)
        if existing is None or length < float(existing.get("length", float("inf"))):
            attrs = dict(data)
            # Avoid passing duplicate `length` keyword.
            attrs["length"] = length
            g.add_edge(u, v, **attrs)
    return g


def _nearest_node_bruteforce(graph: nx.MultiDiGraph, *, lat: float, lon: float) -> int:
    best_node: int | None = None
    best_dist = float("inf")
    for n, nd in graph.nodes(data=True):
        try:
            nlat = float(nd["y"])
            nlon = float(nd["x"])
        except Exception:
            continue
        d = _haversine_distance_m(lat, lon, nlat, nlon)
        if d < best_dist:
            best_dist = d
            best_node = n
    if best_node is None:
        raise RuntimeError("Could not find any nodes in routing graph.")
    return int(best_node)


def _build_street_graph(bbox: dict[str, float], *, network_type: str = "walk") -> nx.MultiDiGraph:
    north, south, east, west = _bbox_to_nsew(bbox)

    # Cache within the project to avoid large downloads each restart.
    cache_dir = Path(__file__).resolve().parent.parent / "data" / "osmnx_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache_dir)
    ox.settings.log_console = False
    # Prevent aggressive polygon subdivision in OSMnx/Overpass.
    # NOTE: OSMnx expects a finite numeric area in m^2. Extremely huge integers
    # (e.g. 10000**10000) are not practical and can overflow/slow down.
    ox.settings.max_query_area_size = 1e12
    # Keep routing requests fast: do not sleep for Overpass rate-limit.
    try:
        ox.settings.overpass_rate_limit = False
    except Exception:
        pass
    # Keep a bounded timeout so API returns quickly on overload.
    try:
        ox.settings.requests_timeout = 25
    except Exception:
        pass
    try:
        ox.settings.overpass_settings = "[out:json][timeout:25]"
    except Exception:
        pass
    # Prefer a usually faster/less rate-limited endpoint than overpass-api.de.
    try:
        ox.settings.overpass_url = "https://lz4.overpass-api.de/api/interpreter"
    except Exception:
        pass

    # Download street graph (walking-friendly for "women safety route" MVP).
    # OSMnx v2.x expects a single bbox tuple: (north, south, east, west)
    graph = ox.graph_from_bbox(
        (north, south, east, west),
        network_type=network_type,
        simplify=True,
        retain_all=False,
        truncate_by_edge=True,
    )
    return graph


def _build_route_graph_fast(
    *,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    buffer_m: float,
    network_type: str,
) -> nx.MultiDiGraph:
    # Build around midpoint to reduce Overpass area and avoid bbox subdivision.
    (mid_lat, mid_lon), radius_m = _route_midpoint_and_dist_m(
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        buffer_m=buffer_m,
    )
    # Keep this tight for speed; we can retry with larger radius if needed.
    graph = ox.graph_from_point(
        (mid_lat, mid_lon),
        dist=float(radius_m),
        dist_type="bbox",
        network_type=network_type,
        simplify=True,
        retain_all=False,
        truncate_by_edge=True,
    )
    return graph


def _compute_safe_costs_on_graph(
    graph: nx.MultiDiGraph,
    bbox: dict[str, float],
    *,
    max_points_for_weights: int = 250,
) -> tuple[nx.DiGraph, dict[str, Any]]:
    """Attach `cost` and `safety_score` to edges and return simplified DiGraph."""
    weights = SafetyWeights.from_env()

    # Distances in meters.
    lamp_cutoff_m = 250.0
    police_cutoff_m = 500.0
    transit_cutoff_m = 300.0

    lamps_payload = fetch_layer_points(
        "street_lamps",
        bbox,
        force_refresh=False,
        soft_fail=True,
    )
    time.sleep(1.0)
    police_payload = fetch_layer_points(
        "police_stations",
        bbox,
        force_refresh=False,
        soft_fail=True,
    )
    time.sleep(1.0)
    transit_payload = fetch_layer_points(
        "transit",
        bbox,
        force_refresh=False,
        soft_fail=True,
    )

    lamps_points: list[dict[str, Any]] = lamps_payload.get("data", []) or []
    police_points: list[dict[str, Any]] = police_payload.get("data", []) or []
    transit_points: list[dict[str, Any]] = transit_payload.get("data", []) or []

    # Limit to keep O(E * P) manageable for MVP.
    lamps_points = lamps_points[:max_points_for_weights]
    police_points = police_points[:max_points_for_weights]
    transit_points = transit_points[:max_points_for_weights]

    safe_h = nx.DiGraph()
    safe_h.add_nodes_from(graph.nodes(data=True))

    total_edges = 0
    total_cost = 0.0
    total_safety_score = 0.0

    for u, v, key, data in graph.edges(keys=True, data=True):
        total_edges += 1

        midpoint_lat, midpoint_lon = _edge_midpoint_lat_lon(graph, u, v, data)

        nearest_lamp_distance_m = float("inf")
        for lamp in lamps_points:
            d = _haversine_distance_m(
                midpoint_lat,
                midpoint_lon,
                float(lamp["latitude"]),
                float(lamp["longitude"]),
            )
            if d < nearest_lamp_distance_m:
                nearest_lamp_distance_m = d

        nearest_police_distance_m = float("inf")
        for station in police_points:
            d = _haversine_distance_m(
                midpoint_lat,
                midpoint_lon,
                float(station["latitude"]),
                float(station["longitude"]),
            )
            if d < nearest_police_distance_m:
                nearest_police_distance_m = d

        nearest_transit_distance_m = float("inf")
        for stop in transit_points:
            d = _haversine_distance_m(
                midpoint_lat,
                midpoint_lon,
                float(stop["latitude"]),
                float(stop["longitude"]),
            )
            if d < nearest_transit_distance_m:
                nearest_transit_distance_m = d

        if nearest_lamp_distance_m == float("inf"):
            nearest_lamp_distance_m = lamp_cutoff_m
        if nearest_police_distance_m == float("inf"):
            nearest_police_distance_m = police_cutoff_m
        if nearest_transit_distance_m == float("inf"):
            nearest_transit_distance_m = transit_cutoff_m

        cost, safety_score = _extract_edge_cost_and_score(
            edge_midpoint=midpoint,
            nearest_lamp_distance_m=nearest_lamp_distance_m,
            nearest_police_distance_m=nearest_police_distance_m,
            nearest_transit_distance_m=nearest_transit_distance_m,
            weights=weights,
            lamp_cutoff_m=lamp_cutoff_m,
            police_cutoff_m=police_cutoff_m,
            transit_cutoff_m=transit_cutoff_m,
        )
        safety_weight = safety_score
        safety_cost = cost

        existing = safe_h.get_edge_data(u, v)
        if existing is None or safety_cost < existing.get("safety_cost", float("inf")):
            # Store requested attributes on edge
            safe_h.add_edge(
                u,
                v,
                safety_weight=safety_weight,  # high = safer
                safety_cost=safety_cost,      # low = safer (use for shortest_path)
                length=float(data.get("length", 0.0) or 0.0),
                nearest_lamp_distance_m=float(nearest_lamp_distance_m),
                nearest_police_distance_m=float(nearest_police_distance_m),
                nearest_transit_distance_m=float(nearest_transit_distance_m),
                geometry=data.get("geometry"),
            )

        total_cost += safety_cost
        total_safety_score += safety_weight

    meta = {
        "edge_count": total_edges,
        "weights": {"w1": weights.w1, "w2": weights.w2, "w3": weights.w3, "w4": weights.w4},
        "lamps_points_used": len(lamps_points),
        "police_points_used": len(police_points),
        "transit_points_used": len(transit_points),
    }
    return safe_h, meta


def _nodes_to_polyline(graph: nx.DiGraph, nodes: list[int]) -> list[dict[str, float]]:
    if len(nodes) < 2:
        return []

    polyline: list[dict[str, float]] = []
    for i in range(len(nodes) - 1):
        u = nodes[i]
        v = nodes[i + 1]
        edge_data = graph.get_edge_data(u, v) or {}

        geom = edge_data.get("geometry")
        if geom is not None:
            try:
                coords = list(geom.coords)  # (lon, lat)
                lat_lon = [{"lat": float(lat), "lon": float(lon)} for lon, lat in coords]
            except Exception:
                lat_lon = []
        else:
            lat_u = float(graph.nodes[u]["y"])
            lon_u = float(graph.nodes[u]["x"])
            lat_v = float(graph.nodes[v]["y"])
            lon_v = float(graph.nodes[v]["x"])
            lat_lon = [{"lat": lat_u, "lon": lon_u}, {"lat": lat_v, "lon": lon_v}]

        if not lat_lon:
            continue
        # De-duplicate join points.
        if polyline and lat_lon:
            if abs(polyline[-1]["lat"] - lat_lon[0]["lat"]) < 1e-9 and abs(polyline[-1]["lon"] - lat_lon[0]["lon"]) < 1e-9:
                polyline.extend(lat_lon[1:])
            else:
                polyline.extend(lat_lon)
        else:
            polyline.extend(lat_lon)

    return polyline


def build_or_get_safe_routing_graph(bbox: dict[str, float], *, refresh_graph: bool = False) -> dict[str, Any]:
    key = _graph_key(bbox)
    if not refresh_graph and key in _SAFE_GRAPH_CACHE:
        return _SAFE_GRAPH_CACHE[key]

    graph = _build_street_graph(bbox)
    # Build a lightweight length-only graph first for speed.
    length_graph = _to_simple_digraph_by_length(graph)
    meta = {"edge_count": int(graph.number_of_edges()), "node_count": int(graph.number_of_nodes())}
    payload = {
        "key": key,
        "raw_graph": graph,
        "length_graph": length_graph,
        "meta": meta,
    }
    _SAFE_GRAPH_CACHE[key] = payload
    return payload


def compute_safe_route(
    *,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    bbox: dict[str, float],
    refresh_graph: bool = False,
    time_mode: str = "day",
) -> SafetyRoutingResult:
    _ = bbox
    t0 = time.perf_counter()
    tm = _normalize_route_time_mode(time_mode)

    print(f"Gelen Koordinatlar: {start_lat}, {start_lon} -> {end_lat}, {end_lon} [{tm}]", flush=True)

    cache_key = _graph_key_for_route(
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        buffer_m=250.0,
    )

    bundle = _SAFE_GRAPH_CACHE.get(cache_key)
    if refresh_graph or bundle is None:
        # Try fast local routing graph first.
        try:
            raw_graph = _build_route_graph_fast(
                start_lat=start_lat,
                start_lon=start_lon,
                end_lat=end_lat,
                end_lon=end_lon,
                buffer_m=250.0,
                network_type="walk",
            )
        except Exception:
            # If Overpass is slow/unavailable, fail fast.
            raise RuntimeError("Sokak ağı indirilemedi (Overpass yavaş). Lütfen tekrar deneyin.")

        length_graph = _to_simple_digraph_by_length(raw_graph)
        bundle = {"raw_graph": raw_graph, "length_graph": length_graph, "meta": {"source": "graph_from_point"}}
        _SAFE_GRAPH_CACHE[cache_key] = bundle

    raw_graph: nx.MultiDiGraph = bundle.get("raw_graph")
    length_graph: nx.DiGraph = bundle.get("length_graph") or _to_simple_digraph_by_length(raw_graph)

    # Fast nearest node lookup (avoids O(N) brute force).
    try:
        # Use current OSMnx API: ox.distance.nearest_nodes(G, X=[...], Y=[...]) -> list
        start_node = ox.distance.nearest_nodes(raw_graph, X=[float(start_lon)], Y=[float(start_lat)])[0]
        end_node = ox.distance.nearest_nodes(raw_graph, X=[float(end_lon)], Y=[float(end_lat)])[0]
    except Exception:
        # Fallback when optional deps (e.g. scikit-learn) are missing.
        start_node = _nearest_node_bruteforce(raw_graph, lat=float(start_lat), lon=float(start_lon))
        end_node = _nearest_node_bruteforce(raw_graph, lat=float(end_lat), lon=float(end_lon))

    # Lamba/karakol: rota seçiminden önce (gece modunda ağırlık için gerekli)
    poi_pre_bbox = _local_bbox_from_points(
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        buffer_m=POI_ROUTE_BUFFER_M,
    )
    lamps = (
        fetch_layer_points("street_lamps", poi_pre_bbox, force_refresh=False, soft_fail=True) or {}
    ).get("data", []) or []
    time.sleep(1.2)
    police = (
        fetch_layer_points("police_stations", poi_pre_bbox, force_refresh=False, soft_fail=True) or {}
    ).get("data", []) or []
    if len(police) < 4:
        time.sleep(1.0)
        police_fb = (
            fetch_layer_points("police_stations", CANKAYA_FALLBACK_BBOX, force_refresh=False, soft_fail=True) or {}
        ).get("data", []) or []
        police = _dedupe_osm_points(list(police) + list(police_fb))

    # Gündüz: mesafe ağırlıklı en kısa yol; gece: aydınlatma/karakol maliyeti ile ağırlıklı en kısa yol
    try:
        length_path_nodes = _shortest_path_nodes_for_mode(
            length_graph, start_node, end_node, tm, lamps, police
        )
    except nx.NetworkXNoPath as exc:
        # B planı: daha geniş alan + daha esnek network_type ile tekrar dene.
        raw_graph2 = _build_route_graph_fast(
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            buffer_m=1200.0,
            network_type="all",
        )
        length_graph2 = _to_simple_digraph_by_length(raw_graph2)
        try:
            start_node2 = ox.distance.nearest_nodes(raw_graph2, X=[float(start_lon)], Y=[float(start_lat)])[0]
            end_node2 = ox.distance.nearest_nodes(raw_graph2, X=[float(end_lon)], Y=[float(end_lat)])[0]
            length_path_nodes = _shortest_path_nodes_for_mode(
                length_graph2, start_node2, end_node2, tm, lamps, police
            )
            length_graph = length_graph2
        except nx.NetworkXNoPath as exc2:
            raise RuntimeError("Bu iki nokta arasında yürünebilir bir yol bulunamadı") from exc2

    polyline = _nodes_to_polyline(length_graph, length_path_nodes)
    path_nodes = length_path_nodes
    _ = time.perf_counter() - t0

    # Metro: rotaya yakın istasyonlar (segment puanı + harita M işaretleri)
    poi_fetch_bbox = _bbox_from_path_nodes(length_graph, path_nodes, buffer_m=POI_ROUTE_BUFFER_M)
    time.sleep(1.2)
    metro_points = (
        fetch_layer_points("metro_stations", poi_fetch_bbox, force_refresh=False, soft_fail=True) or {}
    ).get("data", []) or []
    if len(metro_points) < 6:
        time.sleep(1.0)
        transit_pts = (
            fetch_layer_points("transit", poi_fetch_bbox, force_refresh=False, soft_fail=True) or {}
        ).get("data", []) or []
        metro_like = [p for p in transit_pts if _is_metro_like_osm_point(p)]
        metro_points = _dedupe_osm_points(list(metro_points) + list(metro_like))
    if len(metro_points) < 4:
        time.sleep(1.0)
        metro_fb = (
            fetch_layer_points("metro_stations", CANKAYA_FALLBACK_BBOX, force_refresh=False, soft_fail=True) or {}
        ).get("data", []) or []
        metro_points = _dedupe_osm_points(list(metro_points) + list(metro_fb))

    lamp_lat, lamp_lon = _poi_lat_lon_arrays(lamps)
    pol_lat, pol_lon = _poi_lat_lon_arrays(police)
    metro_lat, metro_lon = _poi_lat_lon_arrays(metro_points)

    # Advisor için: her 250m parçada yakın “güvenli noktaları” + aydınlatma.
    # Karakol/metro/lamba verisi mevcut layer'lardan geliyor; eczane/taksi için Overpass doğrudan sorgulanır.
    PHARMACY_QUERY = """
        [out:json][timeout:45];
        (
          node["amenity"="pharmacy"]({bbox});
          way["amenity"="pharmacy"]({bbox});
          relation["amenity"="pharmacy"]({bbox});
        );
        out center tags;
    """
    TAXI_QUERY = """
        [out:json][timeout:45];
        (
          node["amenity"="taxi"]({bbox});
          way["amenity"="taxi"]({bbox});
          relation["amenity"="taxi"]({bbox});
        );
        out center tags;
    """

    MAX_ADVISOR_POINTS = 650
    pharmacy_points = _overpass_points_in_bbox(
        bbox=poi_fetch_bbox,
        query=PHARMACY_QUERY,
        max_points=MAX_ADVISOR_POINTS,
    )
    taxi_points = _overpass_points_in_bbox(
        bbox=poi_fetch_bbox,
        query=TAXI_QUERY,
        max_points=MAX_ADVISOR_POINTS,
    )

    lamps_advisor = lamps[:MAX_ADVISOR_POINTS]
    police_advisor = police[:MAX_ADVISOR_POINTS]
    metro_advisor = metro_points[:MAX_ADVISOR_POINTS]
    pharmacy_advisor = pharmacy_points[:MAX_ADVISOR_POINTS]
    taxi_advisor = taxi_points[:MAX_ADVISOR_POINTS]

    lamp_lat_ad, lamp_lon_ad = _poi_lat_lon_arrays(lamps_advisor)
    # metro/police için mesafe hesapları için numpy dizisi hızlı; isim için nokta araması yapıyoruz.
    pol_lat_ad, pol_lon_ad = _poi_lat_lon_arrays(police_advisor)
    metro_lat_ad, metro_lon_ad = _poi_lat_lon_arrays(metro_advisor)

    advisor_segments: list[dict[str, Any]] = []
    resampled = _resample_polyline_fixed_step(polyline, step_m=250.0)
    for seg in resampled:
        mid = seg.get("midpoint") or {}
        mid_lat = float(mid.get("lat", 0.0))
        mid_lon = float(mid.get("lon", 0.0))

        d_lamp = _min_haversine_m_np(lamp_lat_ad, lamp_lon_ad, mid_lat, mid_lon)

        # İsim + koordinat için isimli en yakını arıyoruz.
        pol_d, pol_name, pol_lat_pt, pol_lon_pt = _nearest_point_with_details(police_advisor, mid_lat, mid_lon)
        met_d, met_name, met_lat_pt, met_lon_pt = _nearest_point_with_details(metro_advisor, mid_lat, mid_lon)
        ph_d, ph_name, ph_lat_pt, ph_lon_pt = _nearest_point_with_details(pharmacy_advisor, mid_lat, mid_lon)
        tx_d, tx_name, tx_lat_pt, tx_lon_pt = _nearest_point_with_details(taxi_advisor, mid_lat, mid_lon)

        # Mesafe isabeti için (nokta koordinatı bulunmazsa) fallback mesafe kullan.
        d_pol = pol_d if pol_d != float("inf") else _min_haversine_m_np(pol_lat_ad, pol_lon_ad, mid_lat, mid_lon)
        d_metro = met_d if met_d != float("inf") else _min_haversine_m_np(metro_lat_ad, metro_lon_ad, mid_lat, mid_lon)
        d_pharm = ph_d if ph_d != float("inf") else float("inf")
        d_taxi = tx_d if tx_d != float("inf") else float("inf")

        def _dist_or_none(d: float) -> float | None:
            if d == float("inf"):
                return None
            return float(round(d, 1))

        # En yakın lamba koordinatı + mesafe: kısa brute arama (max 650 nokta).
        lamp_best_d = float("inf")
        lamp_best_lat: float | None = None
        lamp_best_lon: float | None = None
        for lp in lamps_advisor:
            try:
                la = float(lp.get("latitude", lp.get("lat")))
                lo = float(lp.get("longitude", lp.get("lon")))
            except (TypeError, ValueError):
                continue
            d = _haversine_distance_m(mid_lat, mid_lon, la, lo)
            if d < lamp_best_d:
                lamp_best_d = d
                lamp_best_lat = la
                lamp_best_lon = lo

        advisor_segments.append(
            {
                "segment_length_m": float(seg.get("segment_length_m") or 0.0),
                "along_route_start_m": float(seg.get("along_route_start_m") or 0.0),
                "along_route_end_m": float(seg.get("along_route_end_m") or 0.0),
                "along_route_mid_m": float(seg.get("along_route_mid_m") or 0.0),
                "start": seg.get("start"),
                "end": seg.get("end"),
                "midpoint": seg.get("midpoint"),
                "nearest_police": {
                    "name": pol_name,
                    "distance_m": _dist_or_none(d_pol),
                    "lat": pol_lat_pt,
                    "lon": pol_lon_pt,
                },
                "nearest_pharmacy": {
                    "name": ph_name,
                    "distance_m": _dist_or_none(d_pharm),
                    "lat": ph_lat_pt,
                    "lon": ph_lon_pt,
                },
                "nearest_metro": {
                    "name": met_name,
                    "distance_m": _dist_or_none(d_metro),
                    "lat": met_lat_pt,
                    "lon": met_lon_pt,
                },
                "nearest_taxi": {
                    "name": tx_name,
                    "distance_m": _dist_or_none(d_taxi),
                    "lat": tx_lat_pt,
                    "lon": tx_lon_pt,
                },
                "lighting": {
                    "nearest_lamp_distance_m": _dist_or_none(lamp_best_d),
                    "lighting_available": bool(lamps_advisor) and lamp_best_lat is not None,
                    "lat": lamp_best_lat,
                    "lon": lamp_best_lon,
                },
            }
        )

    def _road_class_adjust(edge_attrs: dict[str, Any]) -> float:
        hw = edge_attrs.get("highway")
        if isinstance(hw, list) and hw:
            hw = hw[0]
        hw = str(hw) if hw is not None else ""
        if hw in {"primary", "secondary"}:
            return 15.0
        if hw in {"residential", "living_street"}:
            return -10.0
        return 0.0

    segments: list[dict[str, Any]] = []
    total_len = 0.0
    unknown_len = 0.0
    weighted_score_sum = 0.0

    for i in range(len(path_nodes) - 1):
        u = path_nodes[i]
        v = path_nodes[i + 1]
        edge = length_graph.get_edge_data(u, v) or {}
        seg_len = float(edge.get("length", 0.0) or 0.0)
        total_len += seg_len

        geom = edge.get("geometry")
        if geom is not None:
            try:
                coords = list(geom.coords)  # (lon, lat)
                pts = [{"lat": float(lat), "lon": float(lon)} for lon, lat in coords]
            except Exception:
                pts = []
        else:
            pts = [
                {"lat": float(length_graph.nodes[u]["y"]), "lon": float(length_graph.nodes[u]["x"])},
                {"lat": float(length_graph.nodes[v]["y"]), "lon": float(length_graph.nodes[v]["x"])},
            ]

        mid_lat = float(pts[len(pts) // 2]["lat"]) if pts else float(length_graph.nodes[u]["y"])
        mid_lon = float(pts[len(pts) // 2]["lon"]) if pts else float(length_graph.nodes[u]["x"])

        d_lamp = _min_haversine_m_np(lamp_lat, lamp_lon, mid_lat, mid_lon)
        d_police = _min_haversine_m_np(pol_lat, pol_lon, mid_lat, mid_lon)
        d_metro = _min_haversine_m_np(metro_lat, metro_lon, mid_lat, mid_lon)

        nearest_police_dist: float | None = None if d_police == float("inf") else float(round(d_police, 1))
        nearest_metro_dist: float | None = None if d_metro == float("inf") else float(round(d_metro, 1))

        metro_bonus = 0.0
        if d_metro <= METRO_BONUS_INNER_M:
            metro_bonus = METRO_BONUS_INNER_PTS
        elif d_metro <= METRO_BONUS_OUTER_M:
            metro_bonus = METRO_BONUS_OUTER_PTS

        # Unknown data handling: if both far away or lists are empty, keep neutral 50.
        unknown = False
        if (not lamps) and (not police):
            unknown = True
        if (d_lamp == float("inf") and d_police == float("inf")):
            unknown = True
        if (d_lamp > 200.0) and (d_police > 1500.0):
            # No nearby evidence -> treat as unknown / data gap.
            unknown = True

        lighting_unknown = unknown
        if lighting_unknown:
            # Gündüz: veri boşluğuna düşük ceza (mesafe önceliğiyle uyumlu); gece: daha sıkı nötr taban
            seg_score = 64.0 if tm == "day" else 48.0
        else:
            # Gece: aydınlatma ve karakol mesafesi puana daha güçlü yansır (zaman modu senkronu).
            if tm == "night":
                lamp_component = _clamp(1.0 - (d_lamp / 95.0)) * 60.0
                police_component = _clamp(1.0 - (d_police / 650.0)) * 40.0
            else:
                lamp_component = _clamp(1.0 - (d_lamp / 120.0)) * 60.0
                police_component = _clamp(1.0 - (d_police / 800.0)) * 40.0
            seg_score = lamp_component + police_component

        seg_score += metro_bonus

        road_adj = _road_class_adjust(edge)
        seg_score += road_adj
        seg_score = _clamp(seg_score / 100.0) * 100.0

        if lighting_unknown:
            unknown_len += seg_len * (0.28 if tm == "day" else 1.0)

        if lighting_unknown and d_metro > METRO_BONUS_OUTER_M:
            category = "unknown"
            popup = "Yetersiz aydınlatma verisi"
        elif seg_score > 80.0:
            category = "high"
            popup = "Çok Güvenli"
        elif seg_score >= 50.0:
            category = "medium"
            popup = "Orta Güvenli"
        else:
            category = "low"
            popup = "Düşük Güvenli"

        if lighting_unknown and d_metro <= METRO_BONUS_OUTER_M:
            popup = "Yetersiz lamba verisi · metro bonusu uygulandı"

        weighted_score_sum += seg_score * seg_len
        segments.append(
            {
                "points": pts,
                "safety_score": float(seg_score),
                "category": category,
                "unknown": bool(lighting_unknown),
                "popup": popup,
                "nearest_metro_dist": nearest_metro_dist,
                "nearest_police_dist": nearest_police_dist,
            }
        )

    route_geom_coords: list[tuple[float, float]] = []
    for seg_dict in segments:
        for p in seg_dict.get("points") or []:
            try:
                route_geom_coords.append((float(p["lat"]), float(p["lon"])))
            except (KeyError, TypeError, ValueError):
                continue

    def _min_dist_to_route_points(s_lat: float, s_lon: float) -> float:
        if not route_geom_coords:
            return float("inf")
        best = float("inf")
        for rlat, rlon in route_geom_coords:
            d = _haversine_distance_m(s_lat, s_lon, rlat, rlon)
            if d < best:
                best = d
        return best

    nearby_list: list[dict[str, Any]] = []
    seen_station: set[tuple[float, float]] = set()
    for mp in metro_points:
        try:
            mlat = float(mp["latitude"])
            mlon = float(mp["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        key = (round(mlat, 5), round(mlon, 5))
        if key in seen_station:
            continue
        if _min_dist_to_route_points(mlat, mlon) > ROUTE_STATION_DISPLAY_MAX_M:
            continue
        seen_station.add(key)
        tags = mp.get("tags") or {}
        title = mp.get("name") or tags.get("name") or "Metro İstasyonu"
        nearby_list.append({"lat": mlat, "lon": mlon, "name": str(title)})

    safety_score_0_100 = float((weighted_score_sum / total_len) if total_len > 0 else 50.0)
    safety_score_0_100 = float(min(100.0, max(0.0, safety_score_0_100)))

    unknown_ratio = (unknown_len / total_len) if total_len > 0 else 1.0
    if safety_score_0_100 > 80.0:
        label = "Güvenli rota"
    elif safety_score_0_100 >= 50.0:
        label = "Orta Güvenli"
    else:
        label = "Düşük Güvenli"

    total_cost = 0.0
    total_safety_score = 0.0
    edge_count = 0
    for i in range(len(path_nodes) - 1):
        u = path_nodes[i]
        v = path_nodes[i + 1]
        edge = length_graph.get_edge_data(u, v) or {}
        edge_count += 1
        total_cost += float(edge.get("length", 0.0))
        total_safety_score += 0.0

    average_safety_score = total_safety_score / edge_count if edge_count else 0.0

    seg_scores: list[float] = []
    for s in segments:
        try:
            seg_scores.append(float(s["safety_score"]))
        except (KeyError, TypeError, ValueError):
            continue
    seg_min = min(seg_scores) if seg_scores else None
    seg_max = max(seg_scores) if seg_scores else None

    return SafetyRoutingResult(
        polyline=polyline,
        total_cost=total_cost,
        edge_count=edge_count,
        average_safety_score=average_safety_score,
        safety_score=float(safety_score_0_100),
        unknown_ratio=float(unknown_ratio),
        label=str(label),
        segments=segments,
        segment_score_min=seg_min,
        segment_score_max=seg_max,
        time_mode=str(tm),
        night_analysis=bool(tm == "night"),
        nearby_stations=tuple(nearby_list),
        advisor_segments=advisor_segments,
    )

