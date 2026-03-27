"""Güvenli İzler — Streamlit UI (tasks 1.1, 1.4, 1.5)."""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
from typing import Any, Optional

import streamlit as st
from streamlit_folium import st_folium

from map_view import (
    CANKAYA_CENTER_LATITUDE,
    CANKAYA_CENTER_LONGITUDE,
    DEFAULT_ZOOM,
    build_cankaya_map,
    default_bbox,
)
from utils.local_routing import compute_local_route_payload

DATA_DIR = Path(os.path.join(os.getcwd(), "data"))
_WARNED_MESSAGES: set[str] = set()
_ADVISOR_PLACEHOLDER_SNIPPET = "Bu proje dosya tabanli modda calisiyor."


def _warn_once(message: str) -> None:
    if message in _WARNED_MESSAGES:
        return
    _WARNED_MESSAGES.add(message)
    st.warning(message)


def _data_file_candidates(filename: str) -> list[Path]:
    """Bulut/yerel çalışma dizin farklarında data dosyasını güvenli bul."""
    cwd_data = Path(os.path.join(os.getcwd(), "data", filename))
    app_data = Path(__file__).resolve().parent / "data" / filename
    configured = DATA_DIR / filename
    out: list[Path] = []
    for p in (configured, cwd_data, app_data):
        if p not in out:
            out.append(p)
    return out


def _load_open_places_for_bbox(bbox: dict[str, float]) -> list[dict[str, Any]]:
    """Yol Günlüğü için açık eczane/restoran vb. noktaları yükle."""
    payload: Any = None
    for path in _data_file_candidates("open_places.json"):
        payload = _load_json_file(
            path,
            missing_message=f"Veri dosyası bulunamadı: {path.as_posix()}",
            parse_error_message="Açık nokta verisi okunamadı",
        )
        if payload is not None:
            break
    if not isinstance(payload, list):
        return []

    rows: list[dict[str, Any]] = []
    for it in payload:
        if not isinstance(it, dict):
            continue
        try:
            lat = float(it.get("lat"))
            lon = float(it.get("lon"))
        except (TypeError, ValueError):
            continue
        if not _bbox_contains(lat=lat, lon=lon, bbox=bbox):
            continue
        is_open = bool(it.get("is_open", False))
        if not is_open:
            continue
        rows.append(
            {
                "name": str(it.get("name") or "Açık nokta"),
                "type": str(it.get("type") or "Nokta"),
                "lat": lat,
                "lon": lon,
            }
        )
    return rows


def _get_secret_value(key: str, default: str = "") -> str:
    """Read credentials from Streamlit secrets first, env second."""
    try:
        if key in st.secrets:
            val = st.secrets[key]
            if val is not None:
                return str(val).strip()
    except Exception:
        pass
    return str(os.getenv(key, default)).strip()


def _normalize_user_status(raw: Any) -> str:
    """Sidebar seçiminden tek bir kullanıcı durumu üret."""
    allowed = {"🫂 Yalnızım", "🍼 Bebek Arabam/Bavulum Var", "🏃‍♀️ Acelem Var"}
    if isinstance(raw, str) and raw in allowed:
        return raw
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            if isinstance(item, str) and item in allowed:
                return item
    return "🫂 Yalnızım"


def _get_streamlit_secret(key: str) -> str:
    """Read a required secret from Streamlit secrets only."""
    try:
        if key in st.secrets:
            value = st.secrets[key]
            if value is not None:
                return str(value).strip()
    except Exception:
        pass
    return ""


def _load_json_file(path: Path, *, missing_message: str, parse_error_message: str) -> Any:
    try:
        if not path.is_file():
            _warn_once(missing_message)
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _warn_once(f"{parse_error_message}: {exc}")
        return None


def _bbox_contains(
    *,
    lat: float,
    lon: float,
    bbox: dict[str, float],
) -> bool:
    return (
        bbox["min_latitude"] <= lat <= bbox["max_latitude"]
        and bbox["min_longitude"] <= lon <= bbox["max_longitude"]
    )


def _geojson_points_to_rows(
    payload: Any,
    *,
    layer_name: str,
    bbox: dict[str, float],
) -> list[dict[str, Any]]:
    features: list[Any] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("features"), list):
            features = payload.get("features")  # type: ignore[assignment]
        elif isinstance(payload.get("data"), list):
            features = payload.get("data")  # type: ignore[assignment]
    elif isinstance(payload, list):
        features = payload
    if not features:
        return []
    rows: list[dict[str, Any]] = []
    for ft in features:
        if not isinstance(ft, dict):
            continue
        geom = ft.get("geometry") if isinstance(ft.get("geometry"), dict) else {}
        props = ft.get("properties") if isinstance(ft.get("properties"), dict) else {}
        lat: float | None = None
        lon: float | None = None
        if str(geom.get("type")) == "Point":
            coords = geom.get("coordinates")
            if isinstance(coords, list) and len(coords) >= 2:
                try:
                    lon = float(coords[0])
                    lat = float(coords[1])
                except (TypeError, ValueError):
                    lat, lon = None, None
        if lat is None or lon is None:
            raw_lat = props.get("latitude", props.get("lat", ft.get("latitude", ft.get("lat"))))
            raw_lon = props.get("longitude", props.get("lon", ft.get("longitude", ft.get("lon"))))
            try:
                lat = float(raw_lat)
                lon = float(raw_lon)
            except (TypeError, ValueError):
                continue
        # EPSG:4326 güvenliği: bazı kaynaklarda lat/lon ters gelebilir, makul aralıkta otomatik düzelt.
        if abs(lat) > 90.0 and abs(lon) <= 90.0:
            lat, lon = lon, lat
        if abs(lat) > 90.0 or abs(lon) > 180.0:
            continue
        if not _bbox_contains(lat=lat, lon=lon, bbox=bbox):
            continue
        rows.append(
            {
                "id": props.get("id"),
                "layer": layer_name,
                "latitude": lat,
                "longitude": lon,
                "lat": lat,
                "lon": lon,
                "name": props.get("name"),
                "tags": props.get("tags") if isinstance(props.get("tags"), dict) else {},
                "transit_type": props.get("transit_type"),
                "tag_type": props.get("tag_type"),
            }
        )
    return rows


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Kısa mesafe hesabı (metre). UI tarafında yalnızca yaklaştırma için kullanılır."""
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlamb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlamb / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return float(r * c)


def _parse_safe_point_popups(raw: Any) -> list[dict[str, Any]]:
    """API veya oturumdan gelen güvenli nokta listesini her zaman dict listesine çevir (ham JSON string | dict)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            inner = data.get("safe_point_popups") or data.get("safePoints")
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
        return []
    if isinstance(raw, dict):
        inner = raw.get("safe_point_popups") or raw.get("safePoints")
        if isinstance(inner, list):
            return [x for x in inner if isinstance(x, dict)]
    return []


def _advisor_segment_mid_m(seg: dict[str, Any], index: int) -> float:
    """Rota üzerinde segment ortasına yakın metre (backend alanı yoksa kabaca 250 m adım)."""
    sm = seg.get("along_route_mid_m")
    try:
        if sm is not None:
            return float(sm)
    except (TypeError, ValueError):
        pass
    return float(index) * 250.0 + 125.0


def _map_popup_advice_to_segments(
    segments: list[dict[str, Any]],
    safe_points: list[dict[str, Any]],
) -> dict[int, list[str]]:
    """Her AI popup_advice metnini en yakın 250 m segment indeksine bağla."""
    out: dict[int, list[str]] = {}
    for sp in safe_points:
        try:
            sp_lat = float(sp.get("lat"))
            sp_lon = float(sp.get("lon"))
        except (TypeError, ValueError):
            continue
        best_i: int | None = None
        best_d = float("inf")
        for i, seg in enumerate(segments):
            mp = seg.get("midpoint") or {}
            try:
                mlat = float(mp.get("lat"))
                mlon = float(mp.get("lon"))
            except (TypeError, ValueError):
                continue
            d = _haversine_m(sp_lat, sp_lon, mlat, mlon)
            if d < best_d:
                best_d = d
                best_i = i
        if best_i is None:
            continue
        popup_adv = sp.get("popup_advice")
        if isinstance(popup_adv, str) and popup_adv.strip():
            out.setdefault(int(best_i), []).append(popup_adv.strip())
    return out


def _timeline_cards_from_route(
    segments: list[dict[str, Any]],
    segment_advice: dict[int, list[str]],
    *,
    bucket_m: float = 200.0,
) -> list[dict[str, Any]]:
    """Mesafe dilimlerine (örn. 0–200, 200–400 m) göre kart verisi üret."""
    buckets: dict[int, dict[str, Any]] = {}

    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        mid = _advisor_segment_mid_m(seg, i)
        b_id = int(mid // bucket_m) if bucket_m > 0 else 0
        lo = b_id * bucket_m
        hi = lo + bucket_m
        if b_id not in buckets:
            buckets[b_id] = {
                "lo": lo,
                "hi": hi,
                "segment_indices": [],
                "advices": [],
            }
        buckets[b_id]["segment_indices"].append(i)

    # Segment → dilim: aynı AI satırı iki kez gelmesin
    seen_lines: set[tuple[int, str]] = set()
    for seg_i, lines in segment_advice.items():
        seg = segments[seg_i] if 0 <= seg_i < len(segments) else None
        if not isinstance(seg, dict):
            continue
        mid = _advisor_segment_mid_m(seg, seg_i)
        b_id = int(mid // bucket_m) if bucket_m > 0 else 0
        bucket = buckets.get(b_id)
        if not bucket:
            continue
        for line in lines:
            key = (b_id, line)
            if key in seen_lines:
                continue
            seen_lines.add(key)
            bucket["advices"].append(line)

    ordered = sorted(buckets.values(), key=lambda x: float(x["lo"]))
    for b in ordered:
        b["segment_indices"] = sorted(set(int(x) for x in b["segment_indices"]))
    return ordered


def _build_route_journal_rows(
    segments: list[dict[str, Any]],
    safe_points: list[dict[str, Any]],
    segment_advice: dict[int, list[str]],
    open_places: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Yol Günlüğü için tablo satırları: segment puanı + en yakın mesafeler + popup tavsiyesi."""
    rows: list[dict[str, Any]] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        mid_m = _advisor_segment_mid_m(seg, i)
        try:
            score = float(seg.get("safety_score")) if seg.get("safety_score") is not None else None
        except (TypeError, ValueError):
            score = None
        row: dict[str, Any] = {
            "segment": i + 1,
            "rota_metre": int(round(mid_m)),
            "segment_puani": int(round(score)) if score is not None else None,
            "karakol_m": seg.get("nearest_police_dist"),
            "metro_m": seg.get("nearest_metro_dist"),
            "eczane_m": seg.get("nearest_pharmacy_dist"),
            "taksi_m": seg.get("nearest_taxi_dist"),
        }
        if isinstance(seg.get("lighting"), dict):
            row["lamba_m"] = seg.get("lighting", {}).get("nearest_lamp_distance_m")
        adv_lines = segment_advice.get(i) or []
        if adv_lines:
            row["popup_notu"] = " | ".join(adv_lines[:2])
        mp = seg.get("midpoint") if isinstance(seg.get("midpoint"), dict) else {}
        try:
            mlat = float(mp.get("lat"))
            mlon = float(mp.get("lon"))
        except (TypeError, ValueError):
            mlat = mlon = None  # type: ignore[assignment]
        if mlat is not None and mlon is not None and open_places:
            nearest: list[tuple[float, str]] = []
            for p in open_places:
                try:
                    d = _haversine_m(mlat, mlon, float(p["lat"]), float(p["lon"]))
                    nearest.append((d, f"{p.get('type')}: {p.get('name')} (~{int(round(d))} m)"))
                except (KeyError, TypeError, ValueError):
                    continue
            if nearest:
                nearest.sort(key=lambda x: x[0])
                row["acik_noktalar"] = " | ".join(txt for _, txt in nearest[:2])
        rows.append(row)

    # Popup'ta görülen ama segmente düşmeyen notları da kaybetmeyelim.
    for sp in safe_points:
        if not isinstance(sp, dict):
            continue
        adv = str(sp.get("popup_advice") or "").strip()
        if not adv:
            continue
        try:
            lat = float(sp.get("lat"))
            lon = float(sp.get("lon"))
        except (TypeError, ValueError):
            lat = lon = None  # type: ignore[assignment]
        rows.append(
            {
                "segment": "-",
                "rota_metre": None,
                "segment_puani": None,
                "karakol_m": None,
                "metro_m": None,
                "eczane_m": None,
                "taksi_m": None,
                "lamba_m": None,
                "popup_notu": adv,
                "popup_nokta": str(sp.get("name") or sp.get("type") or "Güvenli nokta"),
                "popup_koordinat": f"{lat:.5f}, {lon:.5f}" if lat is not None and lon is not None else "",
            }
        )
    return rows


def _drop_empty_columns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tüm satırlarda boş olan sütunları tablodan kaldır."""
    if not rows:
        return rows
    keys: set[str] = set()
    for r in rows:
        if isinstance(r, dict):
            keys.update(r.keys())
    keep: list[str] = []
    for k in keys:
        has_value = False
        for r in rows:
            v = r.get(k) if isinstance(r, dict) else None
            if v not in (None, "", [], {}):
                has_value = True
                break
        if has_value:
            keep.append(k)

    # Okunurluk için temel kolon sırası
    preferred = [
        "segment",
        "rota_metre",
        "segment_puani",
        "karakol_m",
        "metro_m",
        "eczane_m",
        "taksi_m",
        "lamba_m",
        "acik_noktalar",
        "popup_notu",
        "popup_nokta",
        "popup_koordinat",
    ]
    ordered_keep = [k for k in preferred if k in keep] + [k for k in keep if k not in preferred]
    return [{k: r.get(k) for k in ordered_keep} for r in rows if isinstance(r, dict)]


def _bucket_poi_summary(segment_indices: list[int], segments: list[dict[str, Any]]) -> str:
    """Kart altına kısa güvenli nokta / lamba özeti."""
    bits: list[str] = []
    seen: set[str] = set()
    for i in segment_indices:
        if not (0 <= i < len(segments)):
            continue
        seg = segments[i]
        if not isinstance(seg, dict):
            continue
        for label, key, dist_key in (
            ("Karakol", "nearest_police", "nearest_police_dist"),
            ("Eczane", "nearest_pharmacy", "nearest_pharmacy_dist"),
            ("Metro", "nearest_metro", "nearest_metro_dist"),
            ("Taksi", "nearest_taxi", "nearest_taxi_dist"),
        ):
            poi = seg.get(key)
            if isinstance(poi, dict) and poi.get("name"):
                dm = poi.get("distance_m")
                try:
                    d_txt = f" ~{int(round(float(dm)))} m" if dm is not None else ""
                except (TypeError, ValueError):
                    d_txt = ""
                bit = f"{label}: {poi.get('name')}{d_txt}"
                if bit not in seen:
                    seen.add(bit)
                    bits.append(bit)
            elif seg.get(dist_key) is not None:
                try:
                    d_txt = f" ~{int(round(float(seg.get(dist_key))))} m"
                except (TypeError, ValueError):
                    d_txt = ""
                bit = f"{label}:{d_txt}" if d_txt else ""
                if bit and bit not in seen:
                    seen.add(bit)
                    bits.append(bit)
        lit = seg.get("lighting")
        if isinstance(lit, dict) and lit.get("nearest_lamp_distance_m") is not None:
            try:
                ld = int(round(float(lit.get("nearest_lamp_distance_m"))))
                bit = f"Lamba ~{ld} m"
            except (TypeError, ValueError):
                bit = ""
            if bit and bit not in seen:
                seen.add(bit)
                bits.append(bit)
    return " · ".join(bits[:6]) if bits else ""


def _bucket_remaining_meters(
    card: dict[str, Any],
    segments: list[dict[str, Any]],
) -> int | None:
    """Kart diliminin sonunda varışa yaklaşık kaç metre kaldığını hesapla."""
    seg_ix = list(card.get("segment_indices") or [])
    if not seg_ix:
        return None
    end_m_values: list[float] = []
    total_m_values: list[float] = []
    for i in seg_ix:
        if not (0 <= int(i) < len(segments)):
            continue
        seg = segments[int(i)]
        if not isinstance(seg, dict):
            continue
        for key, bucket in (("along_route_end_m", end_m_values), ("route_total_m", total_m_values)):
            val = seg.get(key)
            try:
                if val is not None:
                    bucket.append(float(val))
            except (TypeError, ValueError):
                continue
    if not end_m_values:
        return None
    end_m = max(end_m_values)
    total_m = max(total_m_values) if total_m_values else max(
        [_advisor_segment_mid_m(s, idx) for idx, s in enumerate(segments)] or [end_m]
    )
    remain = max(0.0, total_m - end_m)
    return int(round(remain))


def _advisor_text_for_display(text: str | None) -> str:
    """Ekranda asla ham JSON / ``` kod çiti gösterme (oturumda eski veri kalsa bile)."""
    if not text:
        return ""
    try:
        from backend.ai_advisor import strip_safe_point_json_from_advice_markdown as _strip

        return _strip(str(text))
    except Exception:
        return str(text)


def _render_safe_points_friendly(safe_points: list[dict[str, Any]]) -> None:
    """safe_point_popups için ham JSON yerine okunur liste (kullanıcı dostu)."""
    if not safe_points:
        return
    st.caption("Haritadaki mor işaretler — kısa notlar")
    for sp in safe_points[:12]:
        nm = str(sp.get("name") or "Güvenli nokta").strip()
        typ = str(sp.get("type") or "").strip()
        adv = str(sp.get("popup_advice") or "").strip()
        header = f"**{nm}**"
        if typ:
            header += f" · _{typ}_"
        st.markdown(header)
        if adv:
            st.markdown(adv)
        st.markdown("")  # boşluk

try:
    from dotenv import load_dotenv

    _env_path = Path(__file__).resolve().parent / ".env"
    if _env_path.is_file():
        load_dotenv(_env_path)
    else:
        load_dotenv()
except Exception:
    pass


def fetch_traces(
    bbox: dict[str, float],
) -> list[dict[str, Any]]:
    candidates = _data_file_candidates("traces.geojson") + _data_file_candidates("traces.json")
    payload: Any = None
    for path in candidates:
        payload = _load_json_file(
            path,
            missing_message=f"Veri dosyası bulunamadı: {path.as_posix()}",
            parse_error_message="İz verisi okunamadı",
        )
        if payload is not None:
            break
    if payload is None:
        return []
    if isinstance(payload, list):
        rows = payload
    else:
        rows = _geojson_points_to_rows(payload, layer_name="traces", bbox=bbox)
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            lat = float(row.get("latitude"))
            lon = float(row.get("longitude"))
        except (TypeError, ValueError):
            continue
        if not _bbox_contains(lat=lat, lon=lon, bbox=bbox):
            continue
        tag = str(row.get("tag_type") or "")
        out.append({"latitude": lat, "longitude": lon, "tag_type": tag, "id": row.get("id")})
    return out


def post_trace(latitude: float, longitude: float, tag_type: str) -> bool:
    # Bu sürümde iz ekleme endpoint'i yok; yalnız yerel veri dosyaları görüntülenir.
    st.info("Bulut modunda yeni iz kaydı kapalı. Yerel veri dosyası kullanılıyor.")
    return False


def fetch_osm_layer(
    *,
    layer_name: str,
    bbox: dict[str, float],
    refresh: bool = False,
) -> list[dict[str, Any]]:
    _ = refresh  # dosya tabanlı modda manuel yenileme yok
    max_points = 350
    payload = None
    tried_paths: list[str] = []
    for file_path in _data_file_candidates(f"{layer_name}.geojson"):
        tried_paths.append(file_path.as_posix())
        payload = _load_json_file(
            file_path,
            missing_message=f"Veri dosyası bulunamadı: {file_path.as_posix()}",
            parse_error_message=f"{layer_name} katmanı okunamadı",
        )
        if payload is not None:
            break
    if payload is None:
        st.error(f"{layer_name} katmanı için dosya bulunamadı. Aranan yollar: " + ", ".join(tried_paths))
        _warn_once(f"{layer_name} katmanı için denenen yollar: " + ", ".join(tried_paths))
        return []
    rows = _geojson_points_to_rows(payload, layer_name=layer_name, bbox=bbox)
    if layer_name == "transit" and not rows:
        # Transit boşsa metro_stations dosyasından metro noktalarını geri kazan.
        for file_path in _data_file_candidates("metro_stations.geojson"):
            payload2 = _load_json_file(
                file_path,
                missing_message=f"Veri dosyası bulunamadı: {file_path.as_posix()}",
                parse_error_message="metro_stations katmanı okunamadı",
            )
            if payload2 is None:
                continue
            fallback_rows = _geojson_points_to_rows(payload2, layer_name=layer_name, bbox=bbox)
            for r in fallback_rows:
                r["transit_type"] = r.get("transit_type") or "metro"
            if fallback_rows:
                rows = fallback_rows
                break
    if len(rows) > max_points:
        return rows[:max_points]
    return rows


def _metro_rows_to_markers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            lat = r.get("latitude", r.get("lat"))
            lon = r.get("longitude", r.get("lon"))
            if lat is None or lon is None:
                continue
            tags = r.get("tags") if isinstance(r.get("tags"), dict) else {}
            name = r.get("name") or tags.get("name") or "Metro İstasyonu"
            out.append({"lat": float(lat), "lon": float(lon), "name": str(name)})
        except (TypeError, ValueError):
            continue
    return out


def _route_segments_for_map(segments: Any) -> list[dict[str, Any]] | None:
    """API segment listesini harita popup'ları için düz dict'lere çevir (Streamlit / Pydantic güvenli)."""
    if not isinstance(segments, list):
        return None
    out: list[dict[str, Any]] = []
    for item in segments:
        if isinstance(item, dict):
            row = dict(item)
        elif hasattr(item, "model_dump"):
            row = item.model_dump()  # type: ignore[no-untyped-call]
        elif hasattr(item, "dict"):
            row = item.dict()  # type: ignore[no-untyped-call]
        else:
            continue
        # JSON bazen tam sayı döndürür; popup float bekler
        # Backend alan adları: nearest_metro_dist, nearest_police_dist (schema ile aynı)
        if (
            row.get("nearest_metro_dist") is None
            and row.get("nearest_station_dist") is not None
        ):
            row["nearest_metro_dist"] = row.get("nearest_station_dist")
        if (
            row.get("nearest_police_dist") is None
            and row.get("nearest_karakol_dist") is not None
        ):
            row["nearest_police_dist"] = row.get("nearest_karakol_dist")
        for key in ("nearest_metro_dist", "nearest_police_dist", "safety_score"):
            if key in row and row[key] is not None:
                try:
                    row[key] = float(row[key])
                except (TypeError, ValueError):
                    pass
        out.append(row)
    return out or None


def _route_segment_light_stats(segments: Any) -> tuple[int, int, int]:
    """(toplam segment, unknown=True sayısı, category==low sayısı)."""
    if not isinstance(segments, list):
        return 0, 0, 0
    unk = 0
    low = 0
    for s in segments:
        if not isinstance(s, dict):
            continue
        if s.get("unknown"):
            unk += 1
        if s.get("category") == "low":
            low += 1
    return len(segments), unk, low


def _metro_proximity_summary(stations: Any) -> str:
    if not isinstance(stations, list) or not stations:
        return "Rota yakınında metro istasyonu listelenmedi veya veri yok."
    names: list[str] = []
    for s in stations[:10]:
        if isinstance(s, dict):
            n = s.get("name")
            if n:
                names.append(str(n))
    if not names:
        return "Yakın metro adları bilinmiyor."
    return "Yakın metro / girişler: " + ", ".join(names)


def _apply_route_from_api_payload(payload: dict[str, Any], time_mode_api: str) -> None:
    """POST /route yanıtını session_state ve Gemini danışmanına işle."""
    st.session_state.route_polyline = payload.get("polyline")
    st.session_state.route_safety_score = payload.get("safety_score")
    st.session_state.route_unknown_ratio = payload.get("unknown_ratio")
    st.session_state.route_label = payload.get("label")
    st.session_state.route_segments = _route_segments_for_map(payload.get("segments"))
    st.session_state.route_nearby_stations = payload.get("nearby_stations")
    st.session_state.route_segment_score_min = payload.get("segment_score_min")
    st.session_state.route_segment_score_max = payload.get("segment_score_max")
    st.session_state.route_time_mode = payload.get("time_mode") or time_mode_api
    st.session_state.route_night_analysis = bool(payload.get("night_analysis"))
    advisor_segments = payload.get("advisor_segments")
    if not isinstance(advisor_segments, list) or not advisor_segments:
        advisor_segments = _build_advisor_segments_from_route_segments(payload.get("segments"))
    st.session_state.route_advisor_segments = advisor_segments or []
    st.session_state.route_journal_segments = list(st.session_state.route_advisor_segments)
    adv, safe_point_popups, adv_err, adv_json_ok = _fetch_security_advisor(payload, time_mode_api)
    try:
        from backend.ai_advisor import strip_safe_point_json_from_advice_markdown as _strip_adv_json

        st.session_state.route_advisor_text = _strip_adv_json(adv) if adv else None
    except Exception:
        st.session_state.route_advisor_text = adv
    st.session_state.route_advisor_error = adv_err
    st.session_state.route_advisor_json_ok = bool(adv_json_ok) if adv_err is None else True
    st.session_state.route_safe_point_popups = _parse_safe_point_popups(safe_point_popups)
    segment_advice = _map_popup_advice_to_segments(
        list(st.session_state.route_journal_segments or []),
        list(st.session_state.route_safe_point_popups or []),
    )
    st.session_state.route_journal_timeline = _timeline_cards_from_route(
        list(st.session_state.route_journal_segments or []),
        segment_advice,
        bucket_m=200.0,
    )
    st.session_state.route_journal_rows = _build_route_journal_rows(
        list(st.session_state.route_journal_segments or []),
        list(st.session_state.route_safe_point_popups or []),
        segment_advice,
        list(st.session_state.get("route_open_places") or []),
    )


def _advisor_hero_emoji(score: float | None) -> tuple[str, str]:
    """Büyük başlık emojisi ve st.chat_message avatar (aynı karakter)."""
    if score is None:
        return "💬", "💬"
    s = float(score)
    if s > 80.0:
        return "🌟", "🌟"
    if s >= 50.0:
        return "🤝", "🤝"
    return "🫂", "🫂"


def _map_polyline_tooltip_from_score(score: float | None) -> str:
    """Harita üzerindeki rota tooltip'i: sidebar renk eşikleriyle aynı (>80 yeşil, 50–80 sarı, <50 kırmızı)."""
    if score is None:
        return "Rota"
    s = float(score)
    if s > 80.0:
        return "Güvenli Rota"
    if s >= 50.0:
        return "Orta Güvenli Rota"
    return "Düşük Güvenli Rota"


def _build_advisor_segments_from_route_segments(raw_segments: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_segments, list):
        return []
    out: list[dict[str, Any]] = []
    acc = 0.0
    for i, seg in enumerate(raw_segments):
        if not isinstance(seg, dict):
            continue
        pts = seg.get("points")
        if not isinstance(pts, list) or len(pts) < 2:
            continue
        s = pts[0] if isinstance(pts[0], dict) else {}
        e = pts[-1] if isinstance(pts[-1], dict) else {}
        try:
            slat = float(s.get("lat"))
            slon = float(s.get("lon"))
            elat = float(e.get("lat"))
            elon = float(e.get("lon"))
        except (TypeError, ValueError):
            continue
        seg_len = _haversine_m(slat, slon, elat, elon)
        mid_lat = (slat + elat) / 2.0
        mid_lon = (slon + elon) / 2.0
        out.append(
            {
                "segment_length_m": seg_len,
                "along_route_start_m": acc,
                "along_route_end_m": acc + seg_len,
                "along_route_mid_m": acc + (seg_len / 2.0),
                "start": {"lat": slat, "lon": slon},
                "end": {"lat": elat, "lon": elon},
                "midpoint": {"lat": mid_lat, "lon": mid_lon},
                "nearest_police": {
                    "name": "Karakol",
                    "distance_m": seg.get("nearest_police_dist"),
                    "lat": None,
                    "lon": None,
                },
                "nearest_pharmacy": {"name": None, "distance_m": None, "lat": None, "lon": None},
                "nearest_metro": {
                    "name": "Metro",
                    "distance_m": seg.get("nearest_metro_dist"),
                    "lat": None,
                    "lon": None,
                },
                "nearest_taxi": {"name": None, "distance_m": None, "lat": None, "lon": None},
                "lighting": {"nearest_lamp_distance_m": None, "lighting_available": True, "lat": None, "lon": None},
                "index": i,
            }
        )
        acc += seg_len
    return out


def _heuristic_advice_text(route_payload: dict[str, Any], user_status: str) -> str:
    score = float(route_payload.get("safety_score") or 0.0)
    segments = route_payload.get("segments") if isinstance(route_payload.get("segments"), list) else []
    low_count = sum(1 for s in segments if isinstance(s, dict) and s.get("category") == "low")
    med_count = sum(1 for s in segments if isinstance(s, dict) and s.get("category") == "medium")
    hi_count = sum(1 for s in segments if isinstance(s, dict) and s.get("category") == "high")
    nearby = route_payload.get("nearby_stations") if isinstance(route_payload.get("nearby_stations"), list) else []
    total_distance = float(route_payload.get("distance_m") or 0.0)

    def _seg_start_m(seg: dict[str, Any], idx: int) -> float:
        v = seg.get("along_route_start_m")
        try:
            if v is not None:
                return float(v)
        except (TypeError, ValueError):
            pass
        return float(idx) * 250.0

    low_starts: list[float] = []
    for i, s in enumerate(segments):
        if isinstance(s, dict) and s.get("category") == "low":
            low_starts.append(_seg_start_m(s, i))
    low_start_txt = f"~{int(round(min(low_starts)))}. metreden sonra" if low_starts else None

    nearest_safe_txt = ""
    if nearby:
        p0 = nearby[0] if isinstance(nearby[0], dict) else {}
        name = str(p0.get("name") or "metro noktası").strip()
        dist = p0.get("distance_m") or p0.get("distance") or p0.get("dist_m")
        try:
            if dist is not None:
                nearest_safe_txt = f"Yaklaşık {int(round(float(dist)))} metre ileride {name} var."
            else:
                nearest_safe_txt = f"Yakında {name} var, onu referans noktan yap."
        except (TypeError, ValueError):
            nearest_safe_txt = f"Yakında {name} var, onu referans noktan yap."

    mood = "🫂 Yalnızım"
    if "Bebek" in user_status or "Bavul" in user_status:
        mood = "🍼 Bebek Arabam/Bavulum Var"
    elif "Acelem" in user_status:
        mood = "🏃‍♀️ Acelem Var"

    if score >= 80:
        level = "iyi"
    elif score >= 50:
        level = "orta"
    else:
        level = "dikkat gerektiriyor"

    lines = [f"Kız kardeşim, rota genel olarak **{level}** görünüyor (puan {score:.0f}/100)."]
    if total_distance > 0:
        lines.append(f"Toplam mesafe yaklaşık {int(round(total_distance))} metre.")

    expect_parts: list[str] = []
    if hi_count > 0:
        expect_parts.append(f"{hi_count} bölüm daha rahat")
    if med_count > 0:
        expect_parts.append(f"{med_count} bölüm orta tempoda")
    if low_count > 0:
        expect_parts.append(f"{low_count} bölümde ekstra dikkat")
    if expect_parts:
        lines.append("Rota boyunca " + ", ".join(expect_parts) + ".")
    if low_start_txt:
        lines.append(f"Daha zayıf kısım {low_start_txt} başlıyor, orada adımlarını biraz hızlandır.")

    tips: list[str] = []
    if mood.startswith("🍼"):
        tips.append("Bebek arabası/bavul ile ana caddede kal, dar geçit yerine bir sokak uzatmak daha güvenli olur.")
    elif mood.startswith("🏃"):
        tips.append("Acelen varsa düşük puanlı kısmı beklemeden geç, ama çapraz yola dalma.")
    else:
        tips.append("Yalnız yürürken düşük puanlı kısma girmeden telefonu cebinde hazır tut, çevreyi kontrol et 👀.")
    if str(route_payload.get("time_mode") or "").lower() == "night":
        tips.append("Geceyse kulaklığı çıkar, çevrenin sesini duyman iyi olur 🎧.")
    if nearest_safe_txt:
        tips.append(nearest_safe_txt)
    if score < 50:
        tips.append("İstersen başlangıç ya da bitişi 1-2 sokak kaydırıp rotayı tekrar deneyelim ✅.")
    lines.append(" ".join(tips))
    lines.append("Yanındayım canım, adım adım bunu güvenli şekilde tamamlarız 🚶‍♀️💜.")
    return "\n\n".join(lines)


def _dynamic_ai_advice(route_payload: dict[str, Any], user_status: str) -> str | None:
    status = _normalize_user_status(user_status)
    if status.startswith("🍼"):
        status_rule = (
            "Kullanıcı bebek arabası/bavul ile yürüyor: kaldırım genişliği, ışıklı ana cadde ve ani yön değişiminden kaçınma "
            "önerileri ver; hız değil güvenli akış öncelikli olsun."
        )
    elif status.startswith("🏃"):
        status_rule = (
            "Kullanıcı acele ediyor: kısa ve uygulanabilir öneriler ver; riskli bölümü hızlı geçme, ana aksı takip etme ve "
            "yakın güvenli noktayı ara durak olarak kullanma önerisi ekle."
        )
    else:
        status_rule = (
            "Kullanıcı yalnız yürüyor: çevre farkındalığı, telefon/çanta kontrolü ve kalabalık-aydınlık hatta yakın kalma "
            "önerilerini somut metre referanslarıyla ver."
        )

    prompt = (
        "Sen Güvenli İzler uygulamasında büyük kız kardeş gibi konuşan AI refakatçisin.\n"
        "4 bölümde yaz: genel durum, rota boyunca ne beklenir, pratik tavsiye, kapanış.\n"
        "Teknik jargon kullanma; somut metre ve yakın güvenli nokta referansı ver.\n"
        f"Kullanıcı durumu: {status}\n"
        f"Duruma özel kural: {status_rule}\n"
        f"Rota verisi: {json.dumps(route_payload, ensure_ascii=False)[:6000]}"
    )

    gemini_key = _get_streamlit_secret("GEMINI_API_KEY")
    if gemini_key:
        try:
            import google.generativeai as genai

            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel(_get_secret_value("GEMINI_MODEL", "gemini-1.5-flash"))
            rsp = model.generate_content(prompt)
            text = str(getattr(rsp, "text", "") or "").strip()
            if text:
                return text
            st.error("Gemini yanıtı boş döndü. Model veya kota ayarlarını kontrol et.")
        except Exception as exc:
            st.error(f"Gemini API çağrısı başarısız: {exc}")
    else:
        st.error("GEMINI_API_KEY bulunamadı. `.streamlit/secrets.toml` dosyasını kontrol et.")

    openai_key = _get_secret_value("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=openai_key)
            rsp = client.chat.completions.create(
                model=_get_secret_value("OPENAI_MODEL", "gpt-4o-mini"),
                temperature=0.4,
                messages=[
                    {"role": "system", "content": "Türkçe, destekleyici, kısa ve somut güvenlik önerileri ver."},
                    {"role": "user", "content": prompt},
                ],
            )
            text = str(rsp.choices[0].message.content or "").strip()
            if text:
                return text
        except Exception:
            pass
    return None


def _fetch_security_advisor(
    route_payload: dict[str, Any],
    time_mode_api: str,
) -> tuple[Optional[str], list[dict[str, Any]], Optional[str], bool]:
    """Dinamik danışman üret; yoksa dosya/heuristic fallback kullan."""
    segments = route_payload.get("segments")
    total, unk_c, low_c = _route_segment_light_stats(segments)
    _ = total, unk_c, low_c

    user_status = _normalize_user_status(st.session_state.get("user_status"))
    dynamic_text = _dynamic_ai_advice(route_payload, str(user_status))
    if dynamic_text:
        return dynamic_text, [], None, True

    candidates = [
        DATA_DIR / f"advisor_{time_mode_api}.json",
        DATA_DIR / "advisor.json",
    ]
    for path in candidates:
        payload = _load_json_file(
            path,
            missing_message=f"Veri dosyası bulunamadı: {path.as_posix()}",
            parse_error_message="Danışman verisi okunamadı",
        )
        if not isinstance(payload, dict):
            continue
        text = str(payload.get("advice") or "").strip()
        safe_points = _parse_safe_point_popups(payload.get("safe_point_popups"))
        json_ok = bool(payload.get("advisor_json_ok", True))
        if _ADVISOR_PLACEHOLDER_SNIPPET in text:
            # Placeholder metni kullanıcıya "AI notu" gibi göstermeyelim.
            continue
        if text:
            return text, safe_points, None, json_ok

    fallback_text = _heuristic_advice_text(route_payload, str(user_status))
    return fallback_text, [], None, True


def _segment_score_range_from_list(segments: Any) -> tuple[float | None, float | None]:
    """Segment listesinden min / max safety_score (API alanları yoksa yedek)."""
    if not isinstance(segments, list) or not segments:
        return None, None
    scores: list[float] = []
    for s in segments:
        if not isinstance(s, dict):
            continue
        v = s.get("safety_score")
        if v is None:
            continue
        try:
            scores.append(float(v))
        except (TypeError, ValueError):
            continue
    if not scores:
        return None, None
    return min(scores), max(scores)


def _merge_metro_markers(*lists: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    seen: set[tuple[float, float]] = set()
    merged: list[dict[str, Any]] = []
    for lst in lists:
        if not lst:
            continue
        for m in lst:
            try:
                key = (round(float(m["lat"]), 5), round(float(m["lon"]), 5))
            except (KeyError, TypeError, ValueError):
                continue
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "lat": float(m["lat"]),
                    "lon": float(m["lon"]),
                    "name": str(m.get("name") or "Metro İstasyonu"),
                }
            )
    return merged


def _init_trace_location_state() -> None:
    if "trace_lat" not in st.session_state:
        st.session_state.trace_lat = float(CANKAYA_CENTER_LATITUDE)
    if "trace_lon" not in st.session_state:
        st.session_state.trace_lon = float(CANKAYA_CENTER_LONGITUDE)


def _init_route_state() -> None:
    # Picking state for safe route endpoints.
    if "start_point" not in st.session_state:
        st.session_state.start_point = None  # {"lat": float, "lon": float} | None
    if "end_point" not in st.session_state:
        st.session_state.end_point = None  # {"lat": float, "lon": float} | None
    if "selecting_mode" not in st.session_state:
        st.session_state.selecting_mode = None  # None | "start" | "end"
    if "selecting" not in st.session_state:
        st.session_state.selecting = None  # None | "start" | "end"
    if "_last_processed_click" not in st.session_state:
        st.session_state._last_processed_click = None  # (lat, lon)
    if "route_safety_score" not in st.session_state:
        st.session_state.route_safety_score = None
    if "route_unknown_ratio" not in st.session_state:
        st.session_state.route_unknown_ratio = None
    if "route_label" not in st.session_state:
        st.session_state.route_label = None
    if "route_segments" not in st.session_state:
        st.session_state.route_segments = None
    if "route_nearby_stations" not in st.session_state:
        st.session_state.route_nearby_stations = None
    if "route_segment_score_min" not in st.session_state:
        st.session_state.route_segment_score_min = None
    if "route_segment_score_max" not in st.session_state:
        st.session_state.route_segment_score_max = None

    # Backward-compatible keys (older UI versions)
    if "route_selection" not in st.session_state:
        st.session_state.route_selection = "start"
    if "route_start" not in st.session_state:
        st.session_state.route_start = None
    if "route_end" not in st.session_state:
        st.session_state.route_end = None
    if "route_polyline" not in st.session_state:
        st.session_state.route_polyline = None
    if "route_time_mode" not in st.session_state:
        st.session_state.route_time_mode = None  # "day" | "night" | None
    if "route_night_analysis" not in st.session_state:
        st.session_state.route_night_analysis = None
    if "route_advisor_text" not in st.session_state:
        st.session_state.route_advisor_text = None
    if "route_advisor_error" not in st.session_state:
        st.session_state.route_advisor_error = None
    if "route_advisor_json_ok" not in st.session_state:
        st.session_state.route_advisor_json_ok = True
    if "route_safe_point_popups" not in st.session_state:
        st.session_state.route_safe_point_popups = []
    if "route_advisor_segments" not in st.session_state:
        st.session_state.route_advisor_segments = []
    if "route_journal_segments" not in st.session_state:
        st.session_state.route_journal_segments = []
    if "route_journal_timeline" not in st.session_state:
        st.session_state.route_journal_timeline = []
    if "route_journal_rows" not in st.session_state:
        st.session_state.route_journal_rows = []
    if "route_open_places" not in st.session_state:
        st.session_state.route_open_places = []

    # Kullanıcının anlık durumu (AI danışmanı kişiselleştirmek için).
    if "user_status" not in st.session_state:
        st.session_state.user_status = "🫂 Yalnızım"


def _extract_click_lat_lon(map_output: Any) -> tuple[float, float] | None:
    if not isinstance(map_output, dict):
        return None
    candidates = [
        map_output.get("last_clicked"),
        map_output.get("last_object_clicked"),
        map_output.get("last_active_drawing"),
    ]
    for c in candidates:
        if isinstance(c, dict):
            lat = c.get("lat")
            lng = c.get("lng")
            if lat is not None and lng is not None:
                try:
                    return float(lat), float(lng)
                except (TypeError, ValueError):
                    continue
    return None


def main() -> None:
    st.set_page_config(page_title="Güvenli İzler", layout="wide")
    _init_trace_location_state()
    _init_route_state()

    st.title("🗺️ Güvenli İzler")
    st.markdown("### Çankaya'da kadınlar için AI destekli güvenli rota analizi.")
    st.markdown("*Yalnız değilsin — her adımda yanındayız. 💜*")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.write("📍 Rota Analizi")
    with c2:
        st.write("🤖 AI Yorumu")
    with c3:
        st.write("🛡️ Güven Skoru")

    ui_mode = st.sidebar.selectbox(
        "Mod",
        ["Katman Görüntüleme", "Güvenli Rota"],
        index=0,
    )

    bbox = default_bbox()

    # --- Sidebar controls depend on mode ---
    extra_layers: dict[str, list[dict[str, Any]]] = {}
    route_time_mode_api: str = "day"
    basemap_for_route: str = "light"

    if ui_mode == "Katman Görüntüleme":
        st.sidebar.subheader("Topluluk izleri")
        tag_options = ["Tümü"]
        st.sidebar.selectbox("Haritada göster", tag_options, index=0)

        st.sidebar.subheader("OSM katmanları")
        show_police = st.sidebar.checkbox("Karakollar (police stations)", value=True)
        show_lamps = st.sidebar.checkbox("Aydınlatma direkleri (street_lamps)", value=False)
        show_parks = st.sidebar.checkbox("Parklar (parks)", value=True)
        show_transit = st.sidebar.checkbox("Metro + Otobüs durakları (transit)", value=True)
        refresh_layers = st.sidebar.checkbox("Katmanları Overpass'tan yenile", value=False)

        with st.spinner("Harita katmanları yükleniyor…"):
            if show_police:
                extra_layers["police_stations"] = fetch_osm_layer(
                    layer_name="police_stations",
                    bbox=bbox,
                    refresh=refresh_layers,
                )
            if show_lamps:
                extra_layers["street_lamps"] = fetch_osm_layer(
                    layer_name="street_lamps",
                    bbox=bbox,
                    refresh=refresh_layers,
                )
            if show_parks:
                extra_layers["parks"] = fetch_osm_layer(
                    layer_name="parks",
                    bbox=bbox,
                    refresh=refresh_layers,
                )
            if show_transit:
                extra_layers["transit"] = fetch_osm_layer(
                    layer_name="transit",
                    bbox=bbox,
                    refresh=refresh_layers,
                )
    else:
        st.sidebar.subheader("Güvenli rota (A -> B)")
        st.sidebar.caption("Günün saatine göre rota, mesafe ile güvenlik arasında otomatik denge kurar.")
        time_pick = st.sidebar.radio(
            "Zaman modu",
            ["☀️ Gündüz Modu", "🌙 Gece Modu"],
            index=0,
            horizontal=True,
            key="guvenli_izler_time_mode_radio",
            help="Gündüz: öncelik mesafe; bilinmeyen aydınlatma cezası düşük. Gece: aydınlatma ve karakol yakınlığı rota seçimine daha fazla girer.",
        )
        route_time_mode_api = "night" if time_pick == "🌙 Gece Modu" else "day"
        basemap_for_route = "dark" if route_time_mode_api == "night" else "light"
        st.sidebar.caption(f"Yerel rota modu: **{route_time_mode_api}** (gündüz=`day`, gece=`night`)")

        st.sidebar.subheader("O Anki Durumum")
        status_options = ["🫂 Yalnızım", "🍼 Bebek Arabam/Bavulum Var", "🏃‍♀️ Acelem Var"]
        default_status = _normalize_user_status(st.session_state.get("user_status"))
        status_pick = st.sidebar.pills(
            "Durum seç",
            options=status_options,
            default=default_status,
        )
        st.session_state.user_status = _normalize_user_status(status_pick)
        st.sidebar.caption("Seçimine göre kız kardeşin rotayı senin için özel olarak yorumlayacak 💜")

        if (
            st.session_state.get("route_polyline")
            and isinstance(st.session_state.get("start_point"), dict)
            and isinstance(st.session_state.get("end_point"), dict)
            and st.session_state.get("route_time_mode") != route_time_mode_api
        ):
            try:
                start_lat = float(st.session_state.start_point["lat"])
                start_lon = float(st.session_state.start_point["lon"])
                end_lat = float(st.session_state.end_point["lat"])
                end_lon = float(st.session_state.end_point["lon"])
                with st.spinner("Zaman modu değişti; rota ve puanlar yeniden hesaplanıyor…"):
                    payload = compute_local_route_payload(
                        data_dir=DATA_DIR,
                        start_lat=start_lat,
                        start_lon=start_lon,
                        end_lat=end_lat,
                        end_lon=end_lon,
                        time_mode=route_time_mode_api,
                    )
                    _apply_route_from_api_payload(payload, route_time_mode_api)
                    st.rerun()
            except Exception as exc:
                st.sidebar.warning(f"Mod değişince otomatik güncelleme başarısız: {exc}. Lütfen **Rota Çiz**'e bas.")

        col_a, col_b = st.sidebar.columns(2)
        with col_a:
            if st.button("Başlangıç Seç", type="secondary"):
                st.session_state.selecting_mode = "start"
                st.session_state.selecting = "start"  # backward compatible
                st.rerun()
        with col_b:
            if st.button("Bitiş Seç", type="secondary"):
                st.session_state.selecting_mode = "end"
                st.session_state.selecting = "end"  # backward compatible
                st.rerun()

        start_ok = isinstance(st.session_state.start_point, dict)
        end_ok = isinstance(st.session_state.end_point, dict)
        st.sidebar.markdown("**Başlangıç**")
        st.sidebar.caption("Haritadan seçildi ✓" if start_ok else "Henüz seçilmedi — haritaya tıkla.")
        st.sidebar.markdown("**Bitiş**")
        st.sidebar.caption("Haritadan seçildi ✓" if end_ok else "Henüz seçilmedi — haritaya tıkla.")
        sm = st.session_state.selecting_mode
        if sm == "start":
            st.sidebar.caption("Şu an **başlangıç** noktasını işaretliyorsun.")
        elif sm == "end":
            st.sidebar.caption("Şu an **bitiş** noktasını işaretliyorsun.")

        st.sidebar.markdown(
            "**Rota güvenlik rengi**\n\n"
            "- <span style='color:#16a34a'>■</span> > 80: Çok güvenli\n"
            "- <span style='color:#f59e0b'>■</span> 50–80: Sarı = Veri Bilinmiyor / Orta Güvenli\n"
            "- <span style='color:#f59e0b'>▭</span> 50 (kesikli): Veri bilinmiyor\n"
            "- <span style='color:#dc2626'>■</span> < 50: Düşük güvenli\n",
            unsafe_allow_html=True,
        )

        if st.sidebar.button("Rota Çiz", type="primary"):
            if not st.session_state.start_point or not st.session_state.end_point:
                st.sidebar.error("Lütfen haritadan başlangıç ve bitiş seçin.")
            else:
                try:
                    start_lat = float(st.session_state.start_point["lat"])
                    start_lon = float(st.session_state.start_point["lon"])
                    end_lat = float(st.session_state.end_point["lat"])
                    end_lon = float(st.session_state.end_point["lon"])

                    with st.spinner("Güvenli yol hesaplanıyor..."):
                        payload = compute_local_route_payload(
                            data_dir=DATA_DIR,
                            start_lat=start_lat,
                            start_lon=start_lon,
                            end_lat=end_lat,
                            end_lon=end_lon,
                            time_mode=route_time_mode_api,
                        )
                        _apply_route_from_api_payload(payload, route_time_mode_api)
                        st.sidebar.success("Rota yerelde hesaplandı.")
                        st.rerun()
                except Exception as exc:
                    st.sidebar.error(f"Rota hesaplanamadı: {exc}")

    # Performance: only fetch traces when user is viewing layers.
    traces: list[dict[str, Any]] = []
    if ui_mode == "Katman Görüntüleme":
        traces = fetch_traces(bbox)

    route_polyline_for_map = st.session_state.route_polyline if ui_mode == "Güvenli Rota" else None
    score = None
    try:
        if ui_mode == "Güvenli Rota" and st.session_state.route_safety_score is not None:
            score = float(st.session_state.route_safety_score)
    except Exception:
        score = None

    unknown_ratio = None
    try:
        if ui_mode == "Güvenli Rota" and st.session_state.route_unknown_ratio is not None:
            unknown_ratio = float(st.session_state.route_unknown_ratio)
    except Exception:
        unknown_ratio = None

    route_color = "#ef4444"
    if score is not None:
        if score > 80.0:
            route_color = "#16a34a"
        elif score >= 50.0:
            route_color = "#f59e0b"
        else:
            route_color = "#dc2626"

    if ui_mode == "Güvenli Rota" and score is not None:
        smin_raw = st.session_state.route_segment_score_min
        smax_raw = st.session_state.route_segment_score_max
        if smin_raw is None or smax_raw is None:
            smin_fb, smax_fb = _segment_score_range_from_list(st.session_state.route_segments)
            smin_raw = smin_raw if smin_raw is not None else smin_fb
            smax_raw = smax_raw if smax_raw is not None else smax_fb
        try:
            smin_v = float(smin_raw) if smin_raw is not None else None
            smax_v = float(smax_raw) if smax_raw is not None else None
        except (TypeError, ValueError):
            smin_v, smax_v = None, None

        st.sidebar.markdown(f"**Genel Rota Güvenliği (Ortalama): %{score:.0f}**")
        if smin_v is not None and smax_v is not None:
            st.sidebar.caption(
                f"En güvenli kısım: %{smax_v:.0f} · En zayıf kısım (en düşük puan): %{smin_v:.0f}"
            )
        st.sidebar.caption(
            "Haritada tıkladığınız çizgi **o kısa parçanın** puanıdır; soldaki sayı tüm rotanın **uzunluk-ağırlıklı ortalamasıdır**."
        )
        if score < 50.0:
            st.sidebar.caption(
                "Bazı kısımlar daha güvenli olsa da genel rota düşük puan almıştır; özellikle uzun veya zayıf puanlı bölümler ortalamayı aşağı çeker."
            )
        if score is not None:
            st.sidebar.caption(f"Harita etiketi (puanla uyumlu): **{_map_polyline_tooltip_from_score(score)}**")
        _tm = str(st.session_state.route_time_mode or route_time_mode_api or "")
        _night = bool(st.session_state.route_night_analysis) or _tm == "night"
        if _night:
            st.sidebar.success("Gece Güvenlik Analizi Uygulandı")
        if (score < 50.0) or (unknown_ratio is not None and unknown_ratio > 0.30):
            st.sidebar.warning("⚠ Not: Bu rotanın bazı kısımlarında aydınlatma verisi eksik olabilir.")
    @st.cache_resource
    def _base_map_template() -> Any:
        # Cache a template (tiles + bbox rectangle). We'll deep-copy per rerun.
        return build_cankaya_map(traces=None, extra_layers=None, route_polyline=None)

    _ = copy.deepcopy(_base_map_template())
    metro_for_route: list[dict[str, Any]] | None = None
    if ui_mode == "Güvenli Rota":
        metro_rows = fetch_osm_layer(
            layer_name="metro_stations",
            bbox=bbox,
            refresh=False,
        )
        static_metro = _metro_rows_to_markers(metro_rows)
        api_metro = st.session_state.route_nearby_stations
        if isinstance(api_metro, list):
            api_norm = _metro_rows_to_markers(api_metro)
        else:
            api_norm = []
        merged = _merge_metro_markers(api_norm, static_metro)
        metro_for_route = merged if merged else None
    st.info(
        "📍 Başlangıç ve bitiş noktalarını harita üzerinden seçin, ardından sol menüden Güvenli Rota moduna geçerek yapay zeka analizini görün."
    )
    folium_map = build_cankaya_map(
        traces=traces,
        extra_layers=extra_layers,
        route_polyline=route_polyline_for_map,
        route_segments=_route_segments_for_map(st.session_state.route_segments)
        if ui_mode == "Güvenli Rota"
        else None,
        advisor_segments=list(st.session_state.get("route_advisor_segments") or [])
        if ui_mode == "Güvenli Rota"
        else None,
        route_metro_stations=metro_for_route,
        advisor_safe_points=st.session_state.route_safe_point_popups if ui_mode == "Güvenli Rota" else None,
        start_point=st.session_state.start_point if ui_mode == "Güvenli Rota" else None,
        end_point=st.session_state.end_point if ui_mode == "Güvenli Rota" else None,
        route_color=route_color,
        route_label=_map_polyline_tooltip_from_score(score),
        center=(float(CANKAYA_CENTER_LATITUDE), float(CANKAYA_CENTER_LONGITUDE)),
        zoom=int(DEFAULT_ZOOM),
        basemap_style="dark" if basemap_for_route == "dark" else "light",
    )

    map_output: Any = None
    if ui_mode == "Güvenli Rota":
        col_map, col_adv = st.columns([3, 1], gap="medium")
    else:
        col_map = st.container()
        col_adv = None

    # IMPORTANT: use a stable key so click events persist.
    with col_map:
        map_output = st_folium(
            folium_map,
            use_container_width=True,
            height=520,
            center=(float(CANKAYA_CENTER_LATITUDE), float(CANKAYA_CENTER_LONGITUDE)),
            zoom=int(DEFAULT_ZOOM),
            returned_objects=[
                "last_clicked",
                "last_object_clicked",
                "last_active_drawing",
                "all_drawings",
                "bounds",
                "zoom",
                "center",
            ],
            key="guvenli_izler_map",
        )

    if col_adv is not None:
        with col_adv:
            st.caption("Güvenlik danışmanı")
            _adv_score: float | None = None
            if st.session_state.get("route_polyline") and st.session_state.get("route_safety_score") is not None:
                try:
                    _adv_score = float(st.session_state.route_safety_score)
                except (TypeError, ValueError):
                    _adv_score = None
            _hero, _av = _advisor_hero_emoji(
                _adv_score if st.session_state.get("route_polyline") else None
            )
            st.markdown(
                f"<div style='text-align:center;font-size:3.1rem;line-height:1;"
                f"margin:0;padding:0.2rem 0 0.4rem 0;filter:drop-shadow(0 1px 2px rgba(0,0,0,.08));'>{_hero}</div>",
                unsafe_allow_html=True,
            )
            if st.session_state.get("route_polyline"):
                adv_text = st.session_state.get("route_advisor_text")
                adv_err = st.session_state.get("route_advisor_error")
                if adv_text:
                    with st.chat_message("assistant", avatar=_av):
                        if st.session_state.get("route_advisor_json_ok") is False:
                            st.warning(
                                "Kız kardeşim, küçük bir bağlantı sorunu oldu, lütfen rotayı tekrar çizer misin? 💜"
                            )
                        if _adv_score is not None and _adv_score > 80.0:
                            st.success("Bu özet sana iyi haber veriyor — rotayı rahat oku.")
                        elif _adv_score is not None and _adv_score >= 50.0:
                            st.info("Şöyle bir özet çıkardım; bir göz atman yeter.")
                        else:
                            st.warning("Kısaca yazdım; yanındaymışım gibi oku, sakin ol.")
                        st.markdown(_advisor_text_for_display(str(adv_text)))
                elif adv_err:
                    with st.chat_message("assistant", avatar="🔧"):
                        st.warning(
                            "Kişisel not şu an oluşmadı. `data/advisor.json` dosyasını kontrol edip "
                            "rotayı tekrar çiz."
                        )
                else:
                    with st.chat_message("assistant", avatar="⏳"):
                        st.info("Rota çizilince burada kısa bir güvenlik notu belirir.")
            else:
                st.caption("Rota hesaplanınca özet burada otomatik belirir.")

    # Yol Günlüğü: mesafe dilimleri (200 m) + AI nokta notları
    if ui_mode == "Güvenli Rota":
        segments_raw = st.session_state.get("route_journal_segments") or st.session_state.get("route_advisor_segments") or []
        segments = [s for s in segments_raw if isinstance(s, dict)] if isinstance(segments_raw, list) else []
        if not segments:
            base_segments = st.session_state.get("route_segments")
            segments = _build_advisor_segments_from_route_segments(base_segments)
            st.session_state.route_journal_segments = list(segments)
        safe_points = _parse_safe_point_popups(st.session_state.get("route_safe_point_popups"))

        if segments:
            open_places = _load_open_places_for_bbox(bbox)
            st.session_state.route_open_places = list(open_places)
            segment_advice = _map_popup_advice_to_segments(segments, safe_points)
            timeline = _timeline_cards_from_route(segments, segment_advice, bucket_m=200.0)
            if not timeline:
                timeline = list(st.session_state.get("route_journal_timeline") or [])
            else:
                st.session_state.route_journal_timeline = list(timeline)
            journal_rows = _build_route_journal_rows(segments, safe_points, segment_advice, open_places)
            if journal_rows:
                st.session_state.route_journal_rows = list(journal_rows)
            else:
                journal_rows = list(st.session_state.get("route_journal_rows") or [])

            st.subheader("Yol Günlüğü")
            st.caption("Rotayı **200 m** mesafe aralıklarına böldüm; her kartta o bölgeye düşen rehber notu var.")
            if journal_rows:
                st.table(_drop_empty_columns(journal_rows[:120]))
            else:
                st.write("Yol günlüğü satırları henüz oluşmadı.")

            use_expanders = len(timeline) > 8
            for card_i, card in enumerate(timeline):
                lo = int(card["lo"])
                hi = int(card["hi"])
                advices = list(card.get("advices") or [])
                seg_ix = list(card.get("segment_indices") or [])
                poi_line = _bucket_poi_summary(seg_ix, segments)
                remain_m = _bucket_remaining_meters(card, segments)
                title = f"👣 {lo}–{hi} m"
                if remain_m is not None:
                    title += f" · varışa ~{remain_m} m kaldı"

                if use_expanders:
                    with st.expander(title, expanded=(card_i < 2)):
                        if poi_line:
                            st.caption(poi_line)
                        if advices:
                            for adv in advices:
                                st.info(adv)
                        else:
                            st.caption(
                                "Bu dilimde AI notu yok; rota verisi yine de üstteki satırlarda."
                            )
                else:
                    with st.container():
                        st.markdown(f"##### {title}")
                        if poi_line:
                            st.caption(poi_line)
                        if advices:
                            for adv in advices:
                                st.info(adv)
                        else:
                            st.caption(
                                "Bu mesafe diliminde henüz özel bir satır yok; yukarıdaki genel özet ve haritaya bakabilirsin 🫂"
                            )
                    st.divider()

            if len(segments) > 60:
                st.caption(f"_(Rota verisi uzun: {len(segments)} parça; zaman çizelgesi tümünü kapsıyor.)_")

            if safe_points:
                with st.expander("Güvenli nokta notları (liste)", expanded=False):
                    _render_safe_points_friendly(safe_points)
        elif st.session_state.get("route_polyline"):
            st.subheader("Yol Günlüğü")
            st.caption("Rota hesaplandı ancak kart üretmek için yeterli segment verisi yok.")

    if ui_mode == "Güvenli Rota" and isinstance(map_output, dict):
        click_obj = map_output.get("last_clicked")
        if not isinstance(click_obj, dict) or click_obj.get("lat") is None or click_obj.get("lng") is None:
            click_obj = map_output.get("last_object_clicked")

        if isinstance(click_obj, dict) and click_obj.get("lat") is not None and click_obj.get("lng") is not None:
            try:
                click_lat = float(click_obj["lat"])
                click_lon = float(click_obj["lng"])
                click_key = (round(click_lat, 7), round(click_lon, 7))
                if st.session_state._last_processed_click != click_key:
                    st.session_state._last_processed_click = click_key
                    if st.session_state.selecting_mode == "start":
                        st.session_state.start_point = {"lat": click_lat, "lon": click_lon}
                        st.session_state.selecting_mode = None
                        st.session_state.selecting = None  # backward compatible
                        st.rerun()
                    elif st.session_state.selecting_mode == "end":
                        st.session_state.end_point = {"lat": click_lat, "lon": click_lon}
                        st.session_state.selecting_mode = None
                        st.session_state.selecting = None  # backward compatible
                        st.rerun()
            except Exception:
                pass

    if ui_mode == "Güvenli Rota":
        st.caption(
            "Haritaya tıklayarak başlangıç ve bitiş seç; sol panelden **Rota Çiz** ile güvenli yolu al."
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        st.error(f"Uygulama hatası: {exc}")

