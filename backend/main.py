from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:  # type: ignore[misc,no-redef]
        return False


# Proje kökü .env + isteğe bağlı cwd
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)
load_dotenv()

import json
import os
import time

import httpx
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

try:
    from backend.schemas import (
        RouteRequest,
        RouteResponse,
        RouteStation,
        SecurityAdvisorRequest,
        SecurityAdvisorResponse,
    )
except ModuleNotFoundError:  # pragma: no cover
    from schemas import (  # type: ignore
        RouteRequest,
        RouteResponse,
        RouteStation,
        SecurityAdvisorRequest,
        SecurityAdvisorResponse,
    )

TRACES_AVAILABLE = False
# NOTE: On some Windows setups, importing SQLAlchemy can hang inside WMI queries
# during `platform.machine()` calls. To keep the MVP usable (layers + routing),
# traces are disabled unless we later swap to stdlib `sqlite3` implementation.

app = FastAPI(title="Güvenli İzler GIS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _configure_gemini_client_from_env() -> None:
    """Gemini istemcisini Streamlit secrets'tan yapılandır."""
    key = ""
    try:
        if st is not None and "GEMINI_API_KEY" in st.secrets:
            key = str(st.secrets["GEMINI_API_KEY"]).strip()
    except Exception:
        key = ""
    key = key.lstrip("\ufeff").strip('"').strip("'")
    if not key:
        return
    try:
        import google.generativeai as genai

        genai.configure(api_key=key)
    except Exception:
        pass


@app.on_event("startup")
def _startup_init() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(_PROJECT_ROOT / ".env", override=True)
        load_dotenv()
    except Exception:
        pass
    _configure_gemini_client_from_env()


_configure_gemini_client_from_env()

# Keep bbox query compatible with app.py params (min_latitude/min_longitude/...)
DEFAULT_BBOX = (39.895, 32.82, 39.97, 32.93)

POLICE_QUERY = """
    [out:json][timeout:35];
    (
      node["amenity"="police"]({bbox});
      way["amenity"="police"]({bbox});
      relation["amenity"="police"]({bbox});
    );
    out center tags;
"""

PARKS_QUERY = """
    [out:json][timeout:35];
    (
      node["leisure"="park"]({bbox});
      way["leisure"="park"]({bbox});
      relation["leisure"="park"]({bbox});
    );
    out center tags;
"""

STREET_LAMPS_QUERY = """
    [out:json][timeout:40];
    (
      node["highway"="street_lamp"]({bbox});
      node["man_made"="street_lamp"]({bbox});
      way["highway"="street_lamp"]({bbox});
      way["man_made"="street_lamp"]({bbox});
    );
    out center tags;
"""

METRO_STATIONS_QUERY = """
    [out:json][timeout:90];
    (
      node["railway"="station"]({bbox});
      node["railway"="subway_entrance"]({bbox});
      node["amenity"="subway_entrance"]({bbox});
      node["public_transport"="station"]({bbox});
      node["station"="subway"]({bbox});

      way["railway"="station"]({bbox});
      way["public_transport"="station"]({bbox});
      relation["railway"="station"]({bbox});
      relation["public_transport"="station"]({bbox});
    );
    out center tags;
"""

TRANSIT_QUERY = """
    [out:json][timeout:60];
    (
      // Metro / tren istasyonları (node + way + relation; center ile)
      node["railway"="station"]({bbox});
      node["railway"="subway_entrance"]({bbox});
      node["station"="subway"]({bbox});
      node["public_transport"="station"]({bbox});

      way["railway"="station"]({bbox});
      relation["railway"="station"]({bbox});
      way["public_transport"="station"]({bbox});
      relation["public_transport"="station"]({bbox});
      way["station"="subway"]({bbox});
      relation["station"="subway"]({bbox});

      // Otobüs durakları (node only)
      node["highway"="bus_stop"]({bbox});
      node["public_transport"="platform"]({bbox});
      node["amenity"="subway_entrance"]({bbox});
    );
    out center tags;
"""

LAYER_MAPPING = {
    # Aliases
    "police": POLICE_QUERY,
    "police_stations": POLICE_QUERY,
    # Main layers
    "parks": PARKS_QUERY,
    "street_lamps": STREET_LAMPS_QUERY,
    "transit": TRANSIT_QUERY,
    "metro_stations": METRO_STATIONS_QUERY,
}

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
)

# Simple stale-cache fallback for Overpass failures (Faz 2).
LAYER_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours
LAYER_CACHE_VERSION = 2
MAX_POINTS_RESPONSE = 400

def _cache_dir() -> Path:
    root = Path(__file__).resolve().parent.parent
    path = root / "data" / "osm_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(layer_name: str) -> Path:
    return _cache_dir() / f"{layer_name}.json"


def _load_cached_layer(layer_name: str) -> dict[str, Any] | None:
    path = _cache_path(layer_name)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return None
    return None


def _write_cached_layer(layer_name: str, payload: dict[str, Any]) -> None:
    path = _cache_path(layer_name)
    try:
        payload = dict(payload)
        payload["cached_at"] = time.time()
        payload["cache_version"] = LAYER_CACHE_VERSION
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _get_bbox_from_request(request: Request) -> tuple[float, float, float, float]:
    p = request.query_params
    min_lat = float(p.get("min_latitude") or p.get("min_lat") or DEFAULT_BBOX[0])
    min_lon = float(p.get("min_longitude") or p.get("min_lon") or DEFAULT_BBOX[1])
    max_lat = float(p.get("max_latitude") or p.get("max_lat") or DEFAULT_BBOX[2])
    max_lon = float(p.get("max_longitude") or p.get("max_lon") or DEFAULT_BBOX[3])
    if min_lat > max_lat or min_lon > max_lon:
        raise ValueError("min_* must be <= max_* for bbox")
    return min_lat, min_lon, max_lat, max_lon


def _normalize_overpass_elements(layer_name: str, elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for el in elements:
        try:
            lat = el.get("lat")
            lon = el.get("lon")
            if lat is None or lon is None:
                center = el.get("center") or {}
                lat = center.get("lat")
                lon = center.get("lon")
            if lat is None or lon is None:
                continue
            tags = el.get("tags") or {}
            name = tags.get("name")
            transit_type: str | None = None
            if layer_name == "metro_stations":
                transit_type = "metro"
            elif layer_name == "transit":
                railway = tags.get("railway")
                station = tags.get("station")
                pt = tags.get("public_transport")
                highway = tags.get("highway")
                if (
                    railway in {"station", "subway_entrance"}
                    or station == "subway"
                    or pt == "station"
                    or tags.get("amenity") == "subway_entrance"
                ):
                    transit_type = "metro"
                elif highway == "bus_stop" or pt == "platform":
                    transit_type = "bus"
                else:
                    transit_type = "other"
            points.append(
                {
                    "id": f"{el.get('type','obj')}/{el.get('id','na')}",
                    "layer": layer_name,
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "name": name,
                    "tags": tags,
                    "transit_type": transit_type,
                    "source": "OpenStreetMap",
                }
            )
        except (TypeError, ValueError):
            continue
    return points


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/layers")
def list_layers() -> dict[str, list[str]]:
    return {"layers": list(LAYER_MAPPING.keys())}


async def _get_layer_payload(
    layer_name: str,
    request: Request,
    *,
    refresh: bool,
    compact: bool,
) -> dict[str, Any]:
    if layer_name not in LAYER_MAPPING:
        raise HTTPException(status_code=404, detail=f"Unknown layer: {layer_name}")

    try:
        min_lat, min_lon, max_lat, max_lon = _get_bbox_from_request(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    query = LAYER_MAPPING[layer_name].replace("{bbox}", bbox)
    should_refresh = bool(refresh)

    # Fast path: return cached data without calling Overpass (Faz 2 optimization).
    if (not should_refresh) and (not compact):
        cached = _load_cached_layer(layer_name)
        if cached and cached.get("cache_version") != LAYER_CACHE_VERSION:
            cached = None
        if cached:
            cached_at = float(cached.get("cached_at", 0.0) or 0.0)
            if cached_at > 0 and (time.time() - cached_at) < LAYER_CACHE_TTL_SECONDS:
                # Backward compatible cache upgrade: data -> elements
                if isinstance(cached.get("data"), list) and not isinstance(cached.get("elements"), list):
                    cached["elements"] = cached["data"]
                    cached.pop("data", None)
                return cached

    last_error: Exception | None = None
    for endpoint in OVERPASS_URLS:
        try:
            async with httpx.AsyncClient(timeout=75.0) as client:
                response = await client.post(endpoint, data={"data": query})
                response.raise_for_status()
                raw = response.json()
            elements = raw.get("elements", [])
            if not isinstance(elements, list):
                elements = []
            data_points = _normalize_overpass_elements(layer_name, elements)
            if len(data_points) > MAX_POINTS_RESPONSE:
                data_points = data_points[:MAX_POINTS_RESPONSE]
            payload: dict[str, Any] = {
                "layer": layer_name,
                "bbox": {
                    "min_latitude": min_lat,
                    "min_longitude": min_lon,
                    "max_latitude": max_lat,
                    "max_longitude": max_lon,
                },
                "count": len(data_points),
                "elements": data_points,
                "source_endpoint": endpoint,
            }
            # Cache only when not explicitly refreshing. This keeps the API stable
            # when Overpass is temporarily down.
            if compact:
                payload["elements"] = []
                return payload

            if (not should_refresh):
                _write_cached_layer(layer_name, payload)
            return payload
        except Exception as exc:
            last_error = exc
            continue

    if not should_refresh:
        cached = _load_cached_layer(layer_name)
        if cached:
            if cached.get("cache_version") != LAYER_CACHE_VERSION:
                cached = None
        if cached:
            if isinstance(cached.get("data"), list) and not isinstance(cached.get("elements"), list):
                cached["elements"] = cached["data"]
                cached.pop("data", None)
            if isinstance(cached.get("elements"), list):
                cached["stale"] = True
                return cached

    raise HTTPException(status_code=502, detail=f"Overpass failed: {last_error}")


@app.get("/layers/metro_stations")
async def get_metro_stations_layer(
    request: Request,
    refresh: bool = Query(default=False),
    compact: bool = Query(default=False),
) -> dict[str, Any]:
    """
    Metro istasyonları: önce SQLite (`metro_station_cache`), yetersizse Overpass;
    Overpass sonucu veritabanına yazılır.
    """
    try:
        min_lat, min_lon, max_lat, max_lon = _get_bbox_from_request(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if (not refresh) and (not compact):
        try:
            try:
                from backend.metro_db import fetch_metro_in_bbox
            except ModuleNotFoundError:
                from metro_db import fetch_metro_in_bbox  # type: ignore

            db_rows = fetch_metro_in_bbox(min_lat, min_lon, max_lat, max_lon)
            if len(db_rows) >= 3:
                if len(db_rows) > MAX_POINTS_RESPONSE:
                    db_rows = db_rows[:MAX_POINTS_RESPONSE]
                return {
                    "layer": "metro_stations",
                    "bbox": {
                        "min_latitude": min_lat,
                        "min_longitude": min_lon,
                        "max_latitude": max_lat,
                        "max_longitude": max_lon,
                    },
                    "count": len(db_rows),
                    "elements": db_rows,
                    "source_endpoint": "sqlite://metro_station_cache",
                    "data_source": "database",
                }
        except Exception:
            pass

    payload = await _get_layer_payload("metro_stations", request, refresh=refresh, compact=compact)
    if (not compact) and isinstance(payload.get("elements"), list) and payload["elements"]:
        try:
            try:
                from backend.metro_db import upsert_metro_elements
            except ModuleNotFoundError:
                from metro_db import upsert_metro_elements  # type: ignore

            upsert_metro_elements(payload["elements"])
            payload["data_source"] = payload.get("data_source") or "overpass+database"
        except Exception:
            pass
    return payload


@app.get("/layers/{layer_name}")
async def get_layer(
    layer_name: str,
    request: Request,
    refresh: bool = Query(default=False),
    compact: bool = Query(default=False),
) -> dict[str, Any]:
    return await _get_layer_payload(layer_name, request, refresh=refresh, compact=compact)


@app.post("/route", response_model=RouteResponse, response_model_exclude_none=False)
async def compute_safe_route(payload: RouteRequest) -> RouteResponse:
    """Faz 3: Güvenlik ağırlıklı en güvenli rota (Dijkstra, MVP)."""
    try:
        from starlette.concurrency import run_in_threadpool

        # Import lazily to avoid impacting startup if osmnx isn't available.
        try:
            from backend.routing import compute_safe_route as compute_route
        except ModuleNotFoundError:
            from routing import compute_safe_route as compute_route  # type: ignore

        bbox = {
            "min_latitude": float(DEFAULT_BBOX[0]),
            "min_longitude": float(DEFAULT_BBOX[1]),
            "max_latitude": float(DEFAULT_BBOX[2]),
            "max_longitude": float(DEFAULT_BBOX[3]),
        }
        result = await run_in_threadpool(
            compute_route,
            start_lat=float(payload.start_latitude),
            start_lon=float(payload.start_longitude),
            end_lat=float(payload.end_latitude),
            end_lon=float(payload.end_longitude),
            bbox=bbox,
            refresh_graph=bool(payload.refresh_graph),
            time_mode=str(payload.time_mode),
        )
        ns = getattr(result, "nearby_stations", ()) or ()
        nearby = [
            RouteStation(
                lat=float(s["lat"]),
                lon=float(s["lon"]),
                name=str(s.get("name") or "Metro İstasyonu"),
            )
            for s in ns
            if isinstance(s, dict) and s.get("lat") is not None and s.get("lon") is not None
        ]
        return RouteResponse(
            polyline=[{"lat": p["lat"], "lon": p["lon"]} for p in result.polyline],
            total_cost=result.total_cost,
            edge_count=result.edge_count,
            average_safety_score=result.average_safety_score,
            safety_score=float(getattr(result, "safety_score", 0.0)),
            unknown_ratio=float(getattr(result, "unknown_ratio", 0.0)),
            label=str(getattr(result, "label", "Düşük Güvenli")),
            segment_score_min=getattr(result, "segment_score_min", None),
            segment_score_max=getattr(result, "segment_score_max", None),
            segments=list(getattr(result, "segments", []) or []),
            nearby_stations=nearby,
            advisor_segments=list(getattr(result, "advisor_segments", []) or []),
            time_mode=payload.time_mode,
            night_analysis=bool(getattr(result, "night_analysis", False)),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not compute safe route: {exc}") from exc


@app.post("/advisor", response_model=SecurityAdvisorResponse)
async def security_advisor(payload: SecurityAdvisorRequest) -> SecurityAdvisorResponse:
    """Gemini danışman — sistem talimatı: `backend/ai_advisor.py` → `_ADVISOR_SYSTEM` (bu dosyada prompt yok)."""
    from starlette.concurrency import run_in_threadpool

    # Sanal Refakatçi (kız kardeşlik rehberi) için dinamik sistem mesajı.
    # Not: Gerçek sistem prompt işleyicisi `backend/ai_advisor.py` içinde yapılıyor; burada override gönderiyoruz.
    system_override = """
Ton: Biz dili kullanan, samimi ve korumacı bir abla/kız kardeş gibi konuş. Robot gibi konuşma.
Kapsam: Kullanıcının o anki durumuna göre (user_status) rotayı yorumla ve güvenli noktalar için sıcak tavsiyeler ver.

Somut mesafe (kullanıcı mesajındaki veri — zorunlu):
- Mesajda “ROTA PARÇALARI (250 m — ölçülmüş uzaklıklar)” ve “Özet — en yakın güvenli noktalar” bölümlerindeki tüm metre (m) değerlerini kullan.
- Genel laflar yerine sayılara dayan: Örn. “İlk kısım karanlık” demek yerine “Rotada ~500 m civarındayken en yakın metro çıkışı ~310 m; içini rahatlatmazsa oraya yönel 🫂.”
- Markdown tavsiyede (💡 ve 👣 kısımlarında özellikle) en az iki kez somut metre geçsin (metro/karakol/eczane/lamba).

Çıktı Formatı (kesin — sıra çok önemli, token kesilirse önce JSON bitsin):
1) ÖNCE ve TAMAMEN bitmiş tek bir JSON bloğu yaz (```json kod çiti içinde). Bu blokta safe_point_popups dolu olmalı; yarım bırakma.
2) JSON bittikten SONRA samimi Markdown tavsiyeni yaz (⚠️ 💡 👣 başlıkları ve kapanış cümlen dahil).
3) Zorunlu kapanış (Markdown’ın en sonunda): mutlaka şunu yaz ve bitir:
“Ben senin için her adımı kontrol ettim, adımların güçlü olsun kız kardeşim 🫂✨”

JSON şablonu (içeriği kendi verin — örnek yapı):
```json
{
  "safe_point_popups": [
    {
      "id": "p1",
      "type": "Karakol|Eczane|Metro|Taksi|Aydınlık",
      "name": "Güvenli nokta adı",
      "lat": 0.0,
      "lon": 0.0,
      "popup_advice": "Bu nokta için sıcak tavsiye (1-2 cümle)."
    }
  ]
}
```

Kurallar (safe_point_popups — kritik):
- JSON’daki her güvenli nokta için popup_advice ZORUNLU ve anlamlı olsun; boş string veya sadece “tamam” yazma.
- popup_advice: Samimi “kız kardeşim / biz” dili; o noktanın türüne göre üret:
  • Karakol → yakında resmi yardım, güven veren bir cümle
  • Eczane → ışık/yardım istasyonu gibi sıcak ifade
  • Metro → giriş/çıkış, kalabalık, güvenli buluşma
  • Taksi → hızlıca yol üstüne çıkma
  • Aydınlık → sokak ışığı, görünür olma
- Her popup_advice içinde kullanıcıya olan mesafeyi metre ile ver (örn. “buraya rotadan yaklaşık ~280 m kaldı”); bu mesafeleri yalnız kullanıcı mesajındaki ölçülerden al.
- popup_advice içinde asla ham koordinat yazma (enlem/boylam sayıları, virgüllü çift, “lat/lon” yok). İstasyon/eczane adı ve “~Xm” kullan.
- Harita popup’ında sadece bu metin okunacak; teknik tabir kullanma.
- İsim ve lat/lon alanları: yalnız kullanıcı mesajındaki segment verisinden kopyala; uydurma yok.
- popup_advice kısa olsun (1–3 cümle); user_status’a göre (🫂 Yalnızım / 🍼 Bebek Arabam/Bavulum Var / 🏃‍♀️ Acelem Var) tonu ayarla.
- “segment”, “analiz”, “parametre”, “JSON” gibi kelimeleri popup_advice veya markdown içinde kullanma.
- safe_point_popups mümkünse 3–6 nokta; türleri çeşitlendir (metro + karakol + eczane + lamba vb. mümkünse).
"""

    try:
        from dotenv import load_dotenv

        load_dotenv(_PROJECT_ROOT / ".env", override=True)
        load_dotenv()
    except Exception:
        pass
    _configure_gemini_client_from_env()

    try:
        try:
            from backend.ai_advisor import generate_security_advice
        except ModuleNotFoundError:
            from ai_advisor import generate_security_advice  # type: ignore

        ctx = payload.model_dump()
        ctx["system_instruction_override"] = system_override
        advice_text, safe_point_popups, json_ok = await run_in_threadpool(
            generate_security_advice,
            ctx,
        )
        return SecurityAdvisorResponse(
            advice=str(advice_text),
            safe_point_popups=safe_point_popups,
            advisor_json_ok=bool(json_ok),
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Güvenlik danışmanı yanıt üretemedi: {exc}",
        ) from exc


@app.get("/route")
def route_help() -> dict[str, Any]:
    # Browser hits /route with GET; provide a helpful message instead of 405.
    return {
        "detail": "Use POST /route with JSON body.",
        "example": {
            "start_latitude": 39.91,
            "start_longitude": 32.88,
            "end_latitude": 39.92,
            "end_longitude": 32.89,
            "refresh_graph": False,
        },
    }

@app.get("/traces")
def get_traces_unavailable() -> list[dict[str, Any]]:
    # Traces disabled (see TRACES_AVAILABLE note above).
    return []


@app.post("/traces", status_code=503)
def create_trace_unavailable() -> dict[str, Any]:
    raise HTTPException(status_code=503, detail="Traces storage is disabled in this build.")






  