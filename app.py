"""Güvenli İzler — Streamlit UI (tasks 1.1, 1.4, 1.5)."""

from __future__ import annotations

import copy
import json
import math
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

DATA_DIR = Path(__file__).resolve().parent / "data"
_WARNED_MESSAGES: set[str] = set()


def _warn_once(message: str) -> None:
    if message in _WARNED_MESSAGES:
        return
    _WARNED_MESSAGES.add(message)
    st.warning(message)


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
        if not isinstance(geom, dict):
            continue
        if str(geom.get("type")) != "Point":
            continue
        coords = geom.get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        try:
            lon = float(coords[0])
            lat = float(coords[1])
        except (TypeError, ValueError):
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
        for label, key in (
            ("Karakol", "nearest_police"),
            ("Eczane", "nearest_pharmacy"),
            ("Metro", "nearest_metro"),
            ("Taksi", "nearest_taxi"),
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
    tag_filter: Optional[str],
) -> list[dict[str, Any]]:
    candidates = [DATA_DIR / "traces.geojson", DATA_DIR / "traces.json"]
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
        if tag_filter and tag_filter != "Tümü" and tag != tag_filter:
            continue
        out.append({"latitude": lat, "longitude": lon, "tag_type": tag, "id": row.get("id")})
    return out


def post_trace(latitude: float, longitude: float, tag_type: str) -> bool:
    # Streamlit Cloud'da localhost API yok; iz ekleme yalnız dosya tabanlı kullanımda desteklenir.
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
    file_path = DATA_DIR / f"{layer_name}.geojson"
    payload = _load_json_file(
        file_path,
        missing_message=f"Veri dosyası bulunamadı: {file_path.as_posix()}",
        parse_error_message=f"{layer_name} katmanı okunamadı",
    )
    if payload is None:
        return []
    rows = _geojson_points_to_rows(payload, layer_name=layer_name, bbox=bbox)
    if len(rows) > max_points:
        return rows[:max_points]
    return rows


def _load_route_payload_from_file(time_mode_api: str) -> dict[str, Any] | None:
    candidates = [
        DATA_DIR / f"route_{time_mode_api}.json",
        DATA_DIR / "route.json",
    ]
    for path in candidates:
        payload = _load_json_file(
            path,
            missing_message=f"Veri dosyası bulunamadı: {path.as_posix()}",
            parse_error_message="Rota verisi okunamadı",
        )
        if isinstance(payload, dict):
            return payload
    return None


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
    st.session_state.route_advisor_segments = payload.get("advisor_segments") or []
    adv, safe_point_popups, adv_err, adv_json_ok = _fetch_security_advisor(payload, time_mode_api)
    try:
        from backend.ai_advisor import strip_safe_point_json_from_advice_markdown as _strip_adv_json

        st.session_state.route_advisor_text = _strip_adv_json(adv) if adv else None
    except Exception:
        st.session_state.route_advisor_text = adv
    st.session_state.route_advisor_error = adv_err
    st.session_state.route_advisor_json_ok = bool(adv_json_ok) if adv_err is None else True
    st.session_state.route_safe_point_popups = _parse_safe_point_popups(safe_point_popups)


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


def _route_post_json_body(
    *,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    time_mode: str,
    refresh_graph: bool,
) -> dict[str, Any]:
    """Backend RouteRequest ile birebir: time_mode ∈ {day, night}."""
    tm = "night" if str(time_mode).lower().strip() in ("night", "gece", "n") or "gece" in str(time_mode).lower() else "day"
    return {
        "start_latitude": start_lat,
        "start_longitude": start_lon,
        "end_latitude": end_lat,
        "end_longitude": end_lon,
        "refresh_graph": bool(refresh_graph),
        "time_mode": tm,
    }


def _fetch_security_advisor(
    route_payload: dict[str, Any],
    time_mode_api: str,
) -> tuple[Optional[str], list[dict[str, Any]], Optional[str], bool]:
    """Dosyadan danışman verisi oku → (tavsiye_metni, safe_point_popups, hata_metni, advisor_json_ok)."""
    segments = route_payload.get("segments")
    total, unk_c, low_c = _route_segment_light_stats(segments)
    _ = total, unk_c, low_c  # Hesaplar metin fallback'inde ileride kullanılabilir.
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
        return (text or None), safe_points, None, json_ok

    fallback_text = (
        "Kişisel not dosyası bulunamadı. `data/advisor.json` dosyasını ekleyebilir veya "
        "`data/advisor_day.json` / `data/advisor_night.json` kullanabilirsin."
    )
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

    st.title("Güvenli İzler")
    st.caption("Çankaya odaklı MVP — OpenStreetMap altlığı, topluluk izleri (yeşil / sarı / kırmızı).")

    ui_mode = st.sidebar.selectbox(
        "Mod",
        ["Katman Görüntüleme", "Güvenli Rota"],
        index=0,
    )

    bbox = default_bbox()

    # --- Sidebar controls depend on mode ---
    tag_filter: Optional[str] = None
    extra_layers: dict[str, list[dict[str, Any]]] = {}
    route_time_mode_api: str = "day"
    basemap_for_route: str = "light"

    if ui_mode == "Katman Görüntüleme":
        st.sidebar.subheader("Topluluk izleri")
        tag_options = ["Tümü", "Güvenli", "Az Işıklı", "Issız"]
        tag_filter = st.sidebar.selectbox("Haritada göster", tag_options, index=0)

        st.sidebar.subheader("OSM katmanları")
        show_police = st.sidebar.checkbox("Karakollar (police stations)", value=True)
        show_lamps = st.sidebar.checkbox("Aydınlatma direkleri (street_lamps)", value=False)
        show_parks = st.sidebar.checkbox("Parklar (parks)", value=True)
        show_transit = st.sidebar.checkbox("Metro + Otobüs durakları (transit)", value=False)
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
        st.sidebar.caption(f"Dosyadan okunan rota modu: **{route_time_mode_api}** (gündüz=`day`, gece=`night`)")

        st.sidebar.subheader("O Anki Durumum")
        status_options = ["🫂 Yalnızım", "🍼 Bebek Arabam/Bavulum Var", "🏃‍♀️ Acelem Var"]
        default_status = st.session_state.get("user_status") if st.session_state.get("user_status") in status_options else status_options[0]
        st.session_state.user_status = st.sidebar.pills(
            "Durum seç",
            options=status_options,
            default=default_status,
        )
        st.sidebar.caption("Seçimine göre kız kardeşin rotayı senin için özel olarak yorumlayacak 💜")

        if (
            st.session_state.get("route_polyline")
            and isinstance(st.session_state.get("start_point"), dict)
            and isinstance(st.session_state.get("end_point"), dict)
            and st.session_state.get("route_time_mode") != route_time_mode_api
        ):
            try:
                _ = float(st.session_state.start_point["lat"])
                _ = float(st.session_state.start_point["lon"])
                _ = float(st.session_state.end_point["lat"])
                _ = float(st.session_state.end_point["lon"])
                with st.spinner("Zaman modu değişti; rota ve puanlar yeniden hesaplanıyor…"):
                    payload = _load_route_payload_from_file(route_time_mode_api)
                    if not payload:
                        st.sidebar.warning("Rota verisi dosyadan okunamadı. `data/route.json` ekleyin.")
                    else:
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
                        _ = start_lat, start_lon, end_lat, end_lon
                        payload = _load_route_payload_from_file(route_time_mode_api)
                        if not payload:
                            st.sidebar.error(
                                "Rota dosyası okunamadı. `data/route.json` veya "
                                f"`data/route_{route_time_mode_api}.json` dosyasını ekleyin."
                            )
                        else:
                            _apply_route_from_api_payload(payload, route_time_mode_api)
                            st.sidebar.success("Rota dosyadan yüklendi.")
                            st.rerun()
                except Exception as exc:
                    st.sidebar.error(f"Rota hesaplanamadı: {exc}")

    # Performance: only fetch traces when user is viewing layers.
    traces: list[dict[str, Any]] = []
    if ui_mode == "Katman Görüntüleme":
        traces = fetch_traces(bbox, tag_filter if tag_filter and tag_filter != "Tümü" else None)

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
        try:
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
        except Exception as exc:
            # Streamlit Cloud'da component asset'i erişilemezse haritayı HTML olarak yine göster.
            st.warning(
                "Harita bileşeni yüklenemedi; yedek görüntüleme moduna geçildi. "
                "Bu modda harita tıklama etkileşimi sınırlı olabilir."
            )
            st.components.v1.html(folium_map._repr_html_(), height=520, scrolling=False)
            map_output = {}

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
                            "Kişisel not şu an oluşmadı. Proje kökündeki `.env` içinde API anahtarını "
                            "kontrol edip FastAPI sunucusunu yeniden başlat; ardından rotayı tekrar çiz."
                        )
                else:
                    with st.chat_message("assistant", avatar="⏳"):
                        st.info("Rota çizilince burada kısa bir güvenlik notu belirir.")
            else:
                st.caption("Rota hesaplanınca özet burada otomatik belirir.")

    # Yol Günlüğü: mesafe dilimleri (200 m) + AI nokta notları
    if ui_mode == "Güvenli Rota":
        segments_raw = st.session_state.get("route_advisor_segments") or []
        segments = [s for s in segments_raw if isinstance(s, dict)] if isinstance(segments_raw, list) else []
        safe_points = _parse_safe_point_popups(st.session_state.get("route_safe_point_popups"))

        if segments:
            segment_advice = _map_popup_advice_to_segments(segments, safe_points)
            timeline = _timeline_cards_from_route(segments, segment_advice, bucket_m=200.0)

            st.subheader("Yol Günlüğü")
            st.caption("Rotayı **200 m** mesafe aralıklarına böldüm; her kartta o bölgeye düşen rehber notu var.")

            use_expanders = len(timeline) > 8
            for card_i, card in enumerate(timeline):
                lo = int(card["lo"])
                hi = int(card["hi"])
                advices = list(card.get("advices") or [])
                seg_ix = list(card.get("segment_indices") or [])
                poi_line = _bucket_poi_summary(seg_ix, segments)
                title = f"👣 {lo}–{hi} m"

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

