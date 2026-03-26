"""Metro istasyonları: SQLite önbelleği (bbox sorgusu; Overpass ile doldurulur)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def _db_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "guvenli_izler.db"


def _connect() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_metro_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metro_station_cache (
            lat_i INTEGER NOT NULL,
            lon_i INTEGER NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            name TEXT,
            tags_json TEXT,
            layer TEXT DEFAULT 'metro_stations',
            updated_at REAL NOT NULL,
            PRIMARY KEY (lat_i, lon_i)
        );
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_metro_bbox ON metro_station_cache(latitude, longitude);"
    )
    conn.commit()


def fetch_metro_in_bbox(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
) -> list[dict[str, Any]]:
    try:
        conn = _connect()
        ensure_metro_table(conn)
        rows = conn.execute(
            """
            SELECT latitude, longitude, name, tags_json, layer
            FROM metro_station_cache
            WHERE latitude BETWEEN ? AND ?
              AND longitude BETWEEN ? AND ?
            ORDER BY updated_at DESC
            LIMIT 800;
            """,
            (min_lat, max_lat, min_lon, max_lon),
        ).fetchall()
        conn.close()
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            tags = json.loads(r["tags_json"] or "{}")
            if not isinstance(tags, dict):
                tags = {}
        except Exception:
            tags = {}
        nm = r["name"] or tags.get("name")
        out.append(
            {
                "id": f"db/{round(float(r['latitude']), 5)}_{round(float(r['longitude']), 5)}",
                "layer": str(r["layer"] or "metro_stations"),
                "latitude": float(r["latitude"]),
                "longitude": float(r["longitude"]),
                "name": nm,
                "tags": tags,
                "transit_type": "metro",
                "source": "sqlite",
            }
        )
    return out


def _row_to_upsert(p: dict[str, Any]) -> tuple[int, int, float, float, str | None, str, str, float] | None:
    try:
        lat = float(p.get("latitude", p.get("lat")))
        lon = float(p.get("longitude", p.get("lon")))
    except (TypeError, ValueError):
        return None
    lat_i = int(round(lat * 1e5))
    lon_i = int(round(lon * 1e5))
    tags = p.get("tags") if isinstance(p.get("tags"), dict) else {}
    name = p.get("name") or tags.get("name")
    layer = str(p.get("layer") or "metro_stations")
    return lat_i, lon_i, lat, lon, name, json.dumps(tags, ensure_ascii=False), layer, time.time()


def upsert_metro_elements(elements: list[dict[str, Any]]) -> int:
    if not elements:
        return 0
    n = 0
    try:
        conn = _connect()
        ensure_metro_table(conn)
        for p in elements:
            row = _row_to_upsert(p)
            if row is None:
                continue
            lat_i, lon_i, lat, lon, name, tags_json, layer, ts = row
            conn.execute(
                """
                INSERT OR REPLACE INTO metro_station_cache
                (lat_i, lon_i, latitude, longitude, name, tags_json, layer, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (lat_i, lon_i, lat, lon, name, tags_json, layer, ts),
            )
            n += 1
        conn.commit()
        conn.close()
    except Exception:
        return n
    return n
