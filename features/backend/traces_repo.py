"""Persist user traces in SQLite (latitude/longitude columns; bbox listing)."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from backend.schemas import TraceCreate


def insert_trace(engine: Engine, payload: TraceCreate) -> dict[str, Any]:
    sql = text(
        """
        INSERT INTO user_traces (latitude, longitude, tag_type, user_fingerprint)
        VALUES (:latitude, :longitude, :tag_type, :user_fingerprint)
        RETURNING id, latitude, longitude, tag_type, created_at
        """
    )
    try:
        with engine.begin() as conn:
            row = conn.execute(
                sql,
                {
                    "latitude": payload.latitude,
                    "longitude": payload.longitude,
                    "tag_type": payload.tag_type,
                    "user_fingerprint": payload.user_fingerprint,
                },
            ).mappings().one()
        return dict(row)
    except Exception:
        raise


def list_traces_in_bbox(
    engine: Engine,
    *,
    min_latitude: float,
    min_longitude: float,
    max_latitude: float,
    max_longitude: float,
    tag_type: Optional[str] = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    if tag_type:
        sql = text(
            """
            SELECT id, latitude, longitude, tag_type, created_at
            FROM user_traces
            WHERE latitude BETWEEN :min_lat AND :max_lat
              AND longitude BETWEEN :min_lon AND :max_lon
              AND tag_type = :tag_type
            ORDER BY created_at DESC
            LIMIT :limit
            """
        )
        params: dict[str, Any] = {
            "min_lat": min_latitude,
            "max_lat": max_latitude,
            "min_lon": min_longitude,
            "max_lon": max_longitude,
            "tag_type": tag_type,
            "limit": limit,
        }
    else:
        sql = text(
            """
            SELECT id, latitude, longitude, tag_type, created_at
            FROM user_traces
            WHERE latitude BETWEEN :min_lat AND :max_lat
              AND longitude BETWEEN :min_lon AND :max_lon
            ORDER BY created_at DESC
            LIMIT :limit
            """
        )
        params = {
            "min_lat": min_latitude,
            "max_lat": max_latitude,
            "min_lon": min_longitude,
            "max_lon": max_longitude,
            "limit": limit,
        }

    try:
        with engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        raise
