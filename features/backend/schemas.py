"""Pydantic models for trace API (task 1.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

RouteTimeMode = Literal["day", "night"]

from pydantic import BaseModel, Field, field_validator, model_validator

ALLOWED_TAG_TYPES = ("Güvenli", "Az Işıklı", "Issız")
TraceTagType = Literal["Güvenli", "Az Işıklı", "Issız"]


class TraceCreate(BaseModel):
    latitude: float = Field(..., ge=-90, le=90, description="WGS84 latitude")
    longitude: float = Field(..., ge=-180, le=180, description="WGS84 longitude")
    tag_type: str
    user_fingerprint: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Optional anonymous identifier",
    )

    @field_validator("tag_type")
    @classmethod
    def validate_tag_type(cls, value: str) -> str:
        if value not in ALLOWED_TAG_TYPES:
            allowed = ", ".join(ALLOWED_TAG_TYPES)
            raise ValueError(f"tag_type must be one of: {allowed}")
        return value


class TraceRead(BaseModel):
    id: int
    latitude: float
    longitude: float
    tag_type: TraceTagType
    created_at: datetime


class TraceQuery(BaseModel):
    min_latitude: float = Field(..., ge=-90, le=90)
    min_longitude: float = Field(..., ge=-180, le=180)
    max_latitude: float = Field(..., ge=-90, le=90)
    max_longitude: float = Field(..., ge=-180, le=180)
    tag_type: Optional[str] = None

    @field_validator("tag_type")
    @classmethod
    def validate_optional_tag(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value not in ALLOWED_TAG_TYPES:
            allowed = ", ".join(ALLOWED_TAG_TYPES)
            raise ValueError(f"tag_type must be one of: {allowed}")
        return value

    @model_validator(mode="after")
    def check_bbox_order(self) -> "TraceQuery":
        if self.min_latitude > self.max_latitude or self.min_longitude > self.max_longitude:
            raise ValueError("min_* must be less than or equal to max_* for bbox")
        return self


class RouteRequest(BaseModel):
    start_latitude: float = Field(..., ge=-90, le=90)
    start_longitude: float = Field(..., ge=-180, le=180)
    end_latitude: float = Field(..., ge=-90, le=90)
    end_longitude: float = Field(..., ge=-180, le=180)
    refresh_graph: bool = Field(default=False, description="Force rebuilding OSM graph + safety weights")
    time_mode: RouteTimeMode = Field(
        default="day",
        description="day = mesafe öncelikli; night = aydınlatma/karakol ağırlıklı rota",
    )

    @field_validator("time_mode", mode="before")
    @classmethod
    def normalize_time_mode(cls, value: Any) -> str:
        if value is None:
            return "day"
        s = str(value).strip().lower()
        if s in ("night", "gece", "n") or "gece" in s:
            return "night"
        if s in ("day", "gündüz", "gunduz", "d", "günlük", "gunluk"):
            return "day"
        return "day"


class RoutePoint(BaseModel):
    lat: float
    lon: float


class RouteSegment(BaseModel):
    points: list[RoutePoint]
    safety_score: float = Field(..., ge=0, le=100)
    category: Literal["high", "medium", "low", "unknown"]
    unknown: bool = False
    popup: str | None = None
    nearest_metro_dist: float | None = Field(
        default=None,
        description="Segment ortasından en yakın metro istasyonu/giriş (m)",
    )
    nearest_police_dist: float | None = Field(
        default=None,
        description="Segment ortasından en yakın karakol (m)",
    )
    debug_popup: str | None = Field(
        default=None,
        description="Eski/alternatif açıklama metni",
    )


class RouteStation(BaseModel):
    lat: float
    lon: float
    name: str = Field(..., description="İstasyon veya giriş adı (OSM name)")


class SecurityAdvisorRequest(BaseModel):
    """Gemini danışmanına giden rota özeti (API’den JSON)."""

    time_mode: RouteTimeMode = "day"
    night_analysis: bool = False
    safety_score: float = Field(..., ge=0, le=100)
    unknown_ratio: float = Field(default=0.0, ge=0, le=1)
    label: str = Field(default="")
    segment_score_min: Optional[float] = Field(default=None, ge=0, le=100)
    segment_score_max: Optional[float] = Field(default=None, ge=0, le=100)
    metro_proximity_summary: str = Field(
        default="",
        description="Yakın metro istasyonu isimleri veya kısa metin özeti",
    )
    total_segment_count: int = Field(default=0, ge=0)
    unknown_light_segment_count: int = Field(default=0, ge=0)
    low_light_segment_count: int = Field(default=0, ge=0)
    edge_count: Optional[int] = Field(default=None, ge=0)
    # 250m sabit parçalara ayrılmış, her parça için yakın POI/aydınlatma bağlamı.
    advisor_segments: list[dict[str, Any]] = Field(default_factory=list)
    user_status: str = Field(default="🫂 Yalnızım", description="Kullanıcının anlık durumu (AI kişiselleştirme için).")


class SecurityAdvisorResponse(BaseModel):
    advice: str = Field(..., description="Gemini’nin Türkçe güvenlik notu")
    safe_point_popups: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Harita üzerinde işaretlenecek güvenli noktalar ve her biri için kısa tavsiye.",
    )
    advisor_json_ok: bool = Field(
        default=True,
        description="False ise mor işaret JSON’u eksik/hatalı (kesilmiş yanıt)",
    )


class RouteResponse(BaseModel):
    polyline: list[RoutePoint]
    total_cost: float
    edge_count: int
    average_safety_score: float
    safety_score: float = Field(..., ge=0, le=100, description="Route safety score (0-100)")
    unknown_ratio: float = Field(default=0.0, ge=0, le=1, description="Fraction of route with unknown data")
    label: str = Field(default="Düşük Güvenli", description="Human-readable label for route")
    segment_score_min: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Segmentler arası en düşük güvenlik puanı",
    )
    segment_score_max: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Segmentler arası en yüksek güvenlik puanı",
    )
    segments: list[RouteSegment] = Field(default_factory=list)
    nearby_stations: list[RouteStation] = Field(
        default_factory=list,
        description="Rota çizgisi veya segment geometrisine yakın metro istasyonları / girişleri",
    )
    time_mode: RouteTimeMode = Field(default="day", description="İstek zaman modu (yanıtta tekrarlanır)")
    night_analysis: bool = Field(
        default=False,
        description="True ise gece güvenlik ağırlıklı rota seçimi uygulandı",
    )
    # Rota üzerinde 250m aralıklarla üretilen danışman bağlamı (POI+aydınlatma).
    advisor_segments: list[dict[str, Any]] = Field(default_factory=list)
