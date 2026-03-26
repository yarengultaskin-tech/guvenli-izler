"""OSM ETL helpers for map layers (police, street lamps, parks)."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
)
CACHE_TTL_MINUTES = 60
LAYER_NAMES = ("police_stations", "street_lamps", "parks", "transit", "metro_stations")


def _cache_dir() -> Path:
    root = Path(__file__).resolve().parent.parent
    path = root / "data" / "osm_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(layer_name: str, bbox: dict[str, float]) -> Path:
    """Bbox’a göre ayrı önbellek; yanlış bölgedeki POI listesinin dönmesini önler."""
    key = "|".join(
        f"{k}={round(float(bbox[k]), 5)}"
        for k in ("min_latitude", "min_longitude", "max_latitude", "max_longitude")
    )
    digest = hashlib.sha256(f"{layer_name}\n{key}".encode("utf-8")).hexdigest()[:20]
    return _cache_dir() / f"{layer_name}_{digest}.json"


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return datetime.now(tz=timezone.utc) - modified < timedelta(minutes=CACHE_TTL_MINUTES)


def _load_cache(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        # Cache yazımı başarısız olsa da API yanıtını kırma.
        pass


def _overpass_query(layer_name: str, bbox: dict[str, float]) -> str:
    s = bbox["min_latitude"]
    w = bbox["min_longitude"]
    n = bbox["max_latitude"]
    e = bbox["max_longitude"]
    if layer_name == "police_stations":
        return f"""
        [out:json][timeout:45];
        (
          node["amenity"="police"]({s},{w},{n},{e});
          way["amenity"="police"]({s},{w},{n},{e});
          relation["amenity"="police"]({s},{w},{n},{e});
        );
        out center tags;
        """
    if layer_name == "street_lamps":
        return f"""
        [out:json][timeout:45];
        (
          node["highway"="street_lamp"]({s},{w},{n},{e});
          node["man_made"="street_lamp"]({s},{w},{n},{e});
        );
        out tags;
        """
    if layer_name == "parks":
        return f"""
        [out:json][timeout:45];
        (
          node["leisure"="park"]({s},{w},{n},{e});
          way["leisure"="park"]({s},{w},{n},{e});
          relation["leisure"="park"]({s},{w},{n},{e});
        );
        out center tags;
        """
    if layer_name == "transit":
        return f"""
        [out:json][timeout:60];
        (
          node["railway"="station"]({s},{w},{n},{e});
          node["railway"="subway_entrance"]({s},{w},{n},{e});
          node["station"="subway"]({s},{w},{n},{e});
          node["public_transport"="station"]({s},{w},{n},{e});

          way["railway"="station"]({s},{w},{n},{e});
          relation["railway"="station"]({s},{w},{n},{e});
          way["public_transport"="station"]({s},{w},{n},{e});
          relation["public_transport"="station"]({s},{w},{n},{e});
          way["station"="subway"]({s},{w},{n},{e});
          relation["station"="subway"]({s},{w},{n},{e});

          node["highway"="bus_stop"]({s},{w},{n},{e});
          node["public_transport"="platform"]({s},{w},{n},{e});
          node["amenity"="subway_entrance"]({s},{w},{n},{e});
        );
        out center tags;
        """
    if layer_name == "metro_stations":
        # Metro / Ankaray: istasyon bina + giriş + public_transport (Kurtuluş vb. tek etiketli noktalar)
        return f"""
        [out:json][timeout:90];
        (
          node["railway"="station"]({s},{w},{n},{e});
          node["railway"="subway_entrance"]({s},{w},{n},{e});
          node["amenity"="subway_entrance"]({s},{w},{n},{e});
          node["public_transport"="station"]({s},{w},{n},{e});
          node["station"="subway"]({s},{w},{n},{e});

          way["railway"="station"]({s},{w},{n},{e});
          way["public_transport"="station"]({s},{w},{n},{e});
          relation["railway"="station"]({s},{w},{n},{e});
          relation["public_transport"="station"]({s},{w},{n},{e});
        );
        out center tags;
        """
    raise ValueError(f"Unsupported layer: {layer_name}")


def _normalize_elements(layer_name: str, elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for element in elements:
        try:
            lat = element.get("lat")
            lon = element.get("lon")
            if lat is None or lon is None:
                center = element.get("center") or {}
                lat = center.get("lat")
                lon = center.get("lon")
            if lat is None or lon is None:
                continue
            tags = element.get("tags") or {}
            normalized.append(
                {
                    "id": f"{element.get('type', 'obj')}/{element.get('id', 'na')}",
                    "layer": layer_name,
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "name": tags.get("name"),
                    "source": "OpenStreetMap",
                    "tags": tags,
                }
            )
        except (TypeError, ValueError):
            continue
    return normalized


def fetch_layer_points(
    layer_name: str,
    bbox: dict[str, float],
    *,
    force_refresh: bool = False,
    soft_fail: bool = False,
) -> dict[str, Any]:
    if layer_name not in LAYER_NAMES:
        raise ValueError(f"layer_name must be one of: {', '.join(LAYER_NAMES)}")

    cache_file = _cache_path(layer_name, bbox)
    if not force_refresh and _is_cache_fresh(cache_file):
        cached = _load_cache(cache_file)
        if cached is not None:
            return cached

    query = _overpass_query(layer_name, bbox)
    last_error: Exception | None = None
    for attempt_idx, endpoint in enumerate(OVERPASS_URLS):
        try:
            if attempt_idx > 0:
                time.sleep(1.1)
            response = requests.post(
                endpoint,
                data={"data": query},
                timeout=90,
            )
            if response.status_code == 429:
                time.sleep(4.0)
                response = requests.post(
                    endpoint,
                    data={"data": query},
                    timeout=90,
                )
            response.raise_for_status()
            payload = response.json()
            elements = payload.get("elements", [])
            if not isinstance(elements, list):
                elements = []
            points = _normalize_elements(layer_name, elements)
            result = {
                "layer": layer_name,
                "bbox": bbox,
                "count": len(points),
                "updated_at": datetime.now(tz=timezone.utc).isoformat(),
                "source_endpoint": endpoint,
                "data": points,
            }
            _write_cache(cache_file, result)
            return result
        except Exception as exc:
            last_error = exc
            continue

    # Tüm endpointler başarısızsa elde varsa stale cache döndür.
    cached = _load_cache(cache_file)
    if cached is not None:
        cached["stale"] = True
        return cached
    if soft_fail:
        return {
            "layer": layer_name,
            "bbox": bbox,
            "count": 0,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            "data": [],
            "source_endpoint": None,
            "overpass_unavailable": True,
            "overpass_error": str(last_error) if last_error else None,
        }
    raise RuntimeError(f"OSM layer fetch failed for {layer_name}: {last_error}")
