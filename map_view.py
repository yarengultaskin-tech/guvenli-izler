"""Folium map helpers: Çankaya basemap and trace markers (tasks 1.1, 1.5)."""
from __future__ import annotations

import re
from html import escape as html_escape
from typing import Any, Iterable, Literal

import folium

# Popup’ta ham koordinat çiftlerini göstermeyi engellemek için (39.xx, 32.xx gibi)
_COORD_PAIR_IN_TEXT_RE = re.compile(
    r"-?\d{1,2}\.\d{4,10}\s*[,;]\s*-?\d{1,2}\.\d{4,10}",
    re.UNICODE,
)

CANKAYA_BBOX = {
    "min_latitude": 39.895,
    "min_longitude": 32.82,
    "max_latitude": 39.97,
    "max_longitude": 32.93,
}

CANKAYA_CENTER_LATITUDE = 39.917
CANKAYA_CENTER_LONGITUDE = 32.863
DEFAULT_ZOOM = 13

METRO_CLOSE_M = 200.0
METRO_IMMEDIATE_M = 10.0


def _popup_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _segment_to_dict(seg: Any) -> dict[str, Any]:
    if isinstance(seg, dict):
        return dict(seg)
    if hasattr(seg, "model_dump"):
        return seg.model_dump()  # type: ignore[no-any-return, no-untyped-call]
    if hasattr(seg, "dict"):
        return seg.dict()  # type: ignore[no-any-return, no-untyped-call]
    return {}


def format_route_segment_popup_html(seg: dict[str, Any]) -> str:
    """Rota parçası tıklanınca: karakol/metro mesafesi ve puan — asla ham koordinat yok."""
    d_pol = _popup_float(seg.get("nearest_police_dist"))
    d_metro = _popup_float(seg.get("nearest_metro_dist"))
    try:
        score = float(seg.get("safety_score", 0.0))
    except (TypeError, ValueError):
        score = 0.0

    if d_pol is None:
        pol_line = "En yakın karakol: veri yok"
    else:
        pol_line = f"En yakın karakol: ≈{d_pol:.0f} m"

    if d_metro is None:
        metro_line = "En yakın metro: veri yok"
    elif d_metro < METRO_IMMEDIATE_M:
        dm = max(0.0, d_metro)
        metro_line = f"En yakın metro: ≈{dm:.0f} m (hemen yanında)"
    else:
        metro_line = f"En yakın metro: ≈{d_metro:.0f} m"

    category = str(seg.get("category") or "")
    if category == "high":
        seg_note = "yüksek güven"
    elif category == "medium":
        seg_note = "orta güven"
    elif category == "low":
        seg_note = "dikkat: düşük güven"
    elif category == "unknown":
        seg_note = "aydınlık verisi sınırlı"
    else:
        seg_note = ""
    score_line = (
        f"Bu segment puanı: {score:.0f} / 100 — {seg_note}" if seg_note else f"Bu segment puanı: {score:.0f} / 100"
    )

    lines = [
        html_escape(pol_line),
        html_escape(metro_line),
        html_escape(score_line),
    ]
    parts_html = "<br/>".join(lines)

    # Düşük puanlı segmentte "güvenli bağlantı" iddiası çelişki yaratmasın
    if (
        d_metro is not None
        and d_metro < METRO_CLOSE_M
        and score >= 50.0
        and category != "low"
    ):
        parts_html += (
            '<br/><br/><span style="color:#15803d;font-weight:700;font-size:0.95rem;">'
            "🚇 Metroya çok yakın — güvenli bağlantı 💜</span>"
            '<br/><span style="color:#4b5563;font-size:0.85rem;">Kız kardeşim, buradan toplu taşımaya hızlı sığınabilirsin.</span>'
        )
    elif d_metro is not None and d_metro < METRO_CLOSE_M and (category == "low" or score < 50.0):
        parts_html += (
            '<br/><br/><span style="color:#92400e;font-weight:600;">'
            "🚇 Metro yakın ama bu parça puanı düşük — başını dik tut, ana caddeye yakın kal 🫂"
            "</span>"
        )

    return (
        '<div style="font-family:system-ui,-apple-system,sans-serif;line-height:1.45;'
        'padding:6px 4px;min-width:220px;max-width:320px;">'
        f"{parts_html}"
        "</div>"
    )


def _segment_dict_from_advisor_chunk(chunk: dict[str, Any]) -> dict[str, Any] | None:
    """250 m advisor parçasını (start/end + POI mesafeleri) harita segment popup formatına çevirir."""
    start = chunk.get("start") if isinstance(chunk.get("start"), dict) else {}
    end = chunk.get("end") if isinstance(chunk.get("end"), dict) else {}
    try:
        sla, slo = float(start["lat"]), float(start["lon"])
        ela, elo = float(end["lat"]), float(end["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    pol = chunk.get("nearest_police") if isinstance(chunk.get("nearest_police"), dict) else {}
    met = chunk.get("nearest_metro") if isinstance(chunk.get("nearest_metro"), dict) else {}
    d_pol = pol.get("distance_m")
    d_metro = met.get("distance_m")
    try:
        d_pol_f = float(d_pol) if d_pol is not None else None
    except (TypeError, ValueError):
        d_pol_f = None
    try:
        d_metro_f = float(d_metro) if d_metro is not None else None
    except (TypeError, ValueError):
        d_metro_f = None
    score = 55.0
    if d_pol_f is not None:
        score = min(100.0, score + max(0.0, 35.0 - d_pol_f / 25.0))
    if d_metro_f is not None:
        score = min(100.0, score + max(0.0, 25.0 - d_metro_f / 30.0))
    if score >= 80.0:
        cat = "high"
    elif score >= 50.0:
        cat = "medium"
    else:
        cat = "low"
    return {
        "points": [{"lat": sla, "lon": slo}, {"lat": ela, "lon": elo}],
        "safety_score": float(score),
        "category": cat,
        "unknown": False,
        "nearest_police_dist": d_pol_f,
        "nearest_metro_dist": d_metro_f,
    }


def _polyline_tooltip_click_hint() -> folium.Tooltip:
    return folium.Tooltip("📍 Tıkla: karakol & metro kaç metre", sticky=True)


def _draw_scored_route_polyline(
    folium_map: folium.Map,
    seg: dict[str, Any],
    *,
    color: str,
    dash: str | None,
) -> bool:
    """Tek güvenlik segmenti polyline + tıklanabilir popup (karakol/metro m)."""
    pts = seg.get("points") or []
    coords: list[tuple[float, float]] = []
    for p in pts:
        if not isinstance(p, dict):
            continue
        lat, lon = p.get("lat"), p.get("lon")
        if lon is None:
            lon = p.get("lng") or p.get("longitude")
        if lat is None or lon is None:
            continue
        try:
            coords.append((float(lat), float(lon)))
        except (TypeError, ValueError):
            continue
    if len(coords) < 2:
        return False
    try:
        popup_html = format_route_segment_popup_html(seg)
    except Exception:
        popup_html = (
            "<div style=\"padding:8px;font-family:system-ui,sans-serif;\">"
            "Bu parça için mesafe bilgisi şu an gösterilemiyor.</div>"
        )
    # parse_html=True Folium'da içeriği kaçırır (branca Html script=False → |e); ham HTML için False.
    popup = folium.Popup(popup_html, max_width=360, parse_html=False)
    pl_kw: dict[str, Any] = {
        "color": color,
        "weight": 14,
        "opacity": 0.9,
        "popup": popup,
        "tooltip": _polyline_tooltip_click_hint(),
    }
    if dash:
        pl_kw["dash_array"] = dash
    folium.PolyLine(coords, **pl_kw).add_to(folium_map)
    return True


def tag_type_to_marker_html(tag_type: str) -> str:
    if tag_type == "Güvenli":
        color = "#16a34a"
    elif tag_type == "Az Işıklı":
        color = "#ca8a04"
    elif tag_type == "Issız":
        color = "#dc2626"
    else:
        color = "#6b7280"
    return (
        f'<div style="background-color:{color}; width:14px; height:14px; '
        f"border-radius:50%; border:2px solid #ffffff; box-shadow:0 0 2px #0003;"
        f'"></div>'
    )


def add_route_metro_markers(
    folium_map: folium.Map,
    stations: Iterable[dict[str, Any]],
) -> folium.Map:
    """Rota yakınındaki metro istasyonları: mavi 'M' DivIcon + isim popup."""
    group = folium.FeatureGroup(name="Metro istasyonları (M)", show=True)
    html = (
        '<div style="background:#2563eb;color:#fff;font-weight:700;font-size:11px;'
        "width:20px;height:20px;border-radius:4px;border:2px solid #fff;"
        "box-shadow:0 1px 4px rgba(0,0,0,.35);display:flex;align-items:center;"
        'justify-content:center;line-height:1;">M</div>'
    )
    icon = folium.DivIcon(html=html, icon_size=(24, 24), icon_anchor=(12, 12))
    for st_row in stations:
        try:
            lat = float(st_row["lat"])
            lon = float(st_row["lon"])
            name = str(st_row.get("name") or "Metro İstasyonu")
            folium.Marker(
                location=[lat, lon],
                icon=icon,
                popup=folium.Popup(html_escape(name), max_width=300),
                tooltip=name[:72] + ("…" if len(name) > 72 else ""),
            ).add_to(group)
        except (KeyError, TypeError, ValueError):
            continue
    group.add_to(folium_map)
    return folium_map


def add_trace_markers(
    folium_map: folium.Map,
    traces: Iterable[dict[str, Any]],
) -> folium.Map:
    for trace in traces:
        try:
            lat = float(trace["latitude"])
            lon = float(trace["longitude"])
            tag = str(trace.get("tag_type", ""))
            html = tag_type_to_marker_html(tag)
            icon = folium.DivIcon(html=html, icon_size=(18, 18), icon_anchor=(9, 9))
            folium.Marker(
                location=[lat, lon],
                icon=icon,
                tooltip=f"{tag} · #{trace.get('id', '')}",
            ).add_to(folium_map)
        except (KeyError, TypeError, ValueError):
            continue
    return folium_map


def add_osm_layer_markers(
    folium_map: folium.Map,
    *,
    layer_name: str,
    points: Iterable[dict[str, Any]],
) -> folium.Map:
    if layer_name == "police_stations":
        group = folium.FeatureGroup(name="Karakollar", show=True)
        color = "#2563eb"
    elif layer_name == "street_lamps":
        group = folium.FeatureGroup(name="Aydınlatma Direkleri", show=True)
        color = "#ca8a04"
    elif layer_name == "parks":
        group = folium.FeatureGroup(name="Parklar", show=True)
        color = "#16a34a"
    elif layer_name == "transit":
        # Transit için iki ayrı renk: metro/istasyon vs otobüs durağı
        metro_group = folium.FeatureGroup(name="Metro / İstasyonlar", show=True)
        bus_group = folium.FeatureGroup(name="Otobüs Durakları", show=True)
        metro_color = "#7c3aed"  # mor
        bus_color = "#f97316"  # turuncu
    else:
        group = folium.FeatureGroup(name=layer_name, show=True)
        color = "#6b7280"

    for point in points:
        try:
            lat = float(point.get("lat") or point.get("latitude"))
            lon = float(point.get("lon") or point.get("longitude"))
            tags = point.get("tags") or {}
            title = point.get("name") or tags.get("name") or layer_name
            if layer_name == "transit":
                transit_type = point.get("transit_type")
                if transit_type is None:
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
                if transit_type == "bus":
                    target_group = bus_group
                    target_color = bus_color
                else:
                    target_group = metro_group
                    target_color = metro_color
            else:
                target_group = group
                target_color = color
            tooltip = title
            if layer_name == "transit":
                if transit_type == "bus":
                    tooltip = f"Otobüs: {title}"
                elif transit_type == "metro":
                    tooltip = f"Metro: {title}"
            folium.CircleMarker(
                location=[lat, lon],
                radius=6,
                color=target_color,
                fill=True,
                fill_opacity=0.8,
                tooltip=str(tooltip),
            ).add_to(target_group)
        except (KeyError, TypeError, ValueError):
            continue

    if layer_name == "transit":
        metro_group.add_to(folium_map)
        bus_group.add_to(folium_map)
    else:
        group.add_to(folium_map)
    return folium_map


def build_cankaya_map(
    traces: Iterable[dict[str, Any]] | None = None,
    extra_layers: dict[str, list[dict[str, Any]]] | None = None,
    *,
    center: tuple[float, float] | None = None,
    zoom: int = DEFAULT_ZOOM,
    route_polyline: list[dict[str, float]] | None = None,
    route_segments: list[dict[str, Any]] | None = None,
    advisor_segments: list[dict[str, Any]] | None = None,
    route_metro_stations: list[dict[str, Any]] | None = None,
    advisor_safe_points: list[dict[str, Any]] | None = None,
    start_point: dict[str, float] | None = None,
    end_point: dict[str, float] | None = None,
    route_color: str = "#ef4444",
    route_label: str = "Güvenli rota",
    basemap_style: Literal["light", "dark"] = "light",
) -> folium.Map:
    bbox = default_bbox()
    lat, lon = center or (CANKAYA_CENTER_LATITUDE, CANKAYA_CENTER_LONGITUDE)
    folium_map = folium.Map(
        location=(lat, lon),
        zoom_start=zoom,
        tiles=None,
    )
    try:
        if basemap_style == "dark":
            folium.TileLayer(
                "CartoDB dark_matter",
                name="CartoDB Dark Matter",
                max_zoom=19,
                attr='© <a href="https://www.openstreetmap.org/copyright">OSM</a> © CARTO',
            ).add_to(folium_map)
        else:
            folium.TileLayer(
                "OpenStreetMap",
                name="OpenStreetMap",
                max_zoom=19,
                attr="© OpenStreetMap contributors",
            ).add_to(folium_map)
    except Exception:
        if basemap_style == "dark":
            folium_map.add_child(folium.TileLayer("CartoDB dark_matter"))
        else:
            folium_map.add_child(folium.TileLayer("OpenStreetMap"))

    # LatLngPopup kaldırıldı: her tıklamada latitude/longitude gösteriyordu; kullanıcı bunu istemiyor.
    # ClickForMarker kaldırıldı: her tıklamada ek(marker) üretip haritayı kalabalıklaştırıyordu.
    # Başlangıç/bitiş seçimi streamlit-folium `last_clicked` ile çalışır; rota segmenti popup'ı PolyLine üzerinde.

    bounds = [
        [bbox["min_latitude"], bbox["min_longitude"]],
        [bbox["max_latitude"], bbox["max_longitude"]],
    ]
    try:
        folium.Rectangle(
            bounds=bounds,
            color="#2563eb",
            weight=2,
            fill=True,
            fill_opacity=0.08,
            popup="Çankaya (bbox)",
        ).add_to(folium_map)
    except Exception:
        pass

    route_line_drawn = False

    if route_segments:
        try:
            for seg_raw in route_segments:
                seg = _segment_to_dict(seg_raw)
                category = str(seg.get("category") or "")
                unknown = bool(seg.get("unknown"))
                if unknown:
                    color = "#f59e0b"
                    dash = "6, 10"
                elif category == "high":
                    color = "#16a34a"
                    dash = None
                elif category == "medium":
                    color = "#f59e0b"
                    dash = None
                else:
                    color = "#dc2626"
                    dash = None
                if _draw_scored_route_polyline(folium_map, seg, color=color, dash=dash):
                    route_line_drawn = True
        except Exception:
            pass

    if not route_line_drawn and advisor_segments:
        try:
            for chunk in advisor_segments:
                if not isinstance(chunk, dict):
                    continue
                adv_seg = _segment_dict_from_advisor_chunk(chunk)
                if not adv_seg:
                    continue
                cat = str(adv_seg.get("category") or "medium")
                if cat == "high":
                    clr = "#16a34a"
                elif cat == "medium":
                    clr = "#f59e0b"
                else:
                    clr = "#dc2626"
                if _draw_scored_route_polyline(folium_map, adv_seg, color=clr, dash=None):
                    route_line_drawn = True
        except Exception:
            pass

    if route_line_drawn:
        try:
            if start_point and start_point.get("lat") is not None and start_point.get("lon") is not None:
                folium.CircleMarker(
                    (float(start_point["lat"]), float(start_point["lon"])),
                    radius=6,
                    color="#16a34a",
                    fill=True,
                    fill_opacity=0.95,
                    tooltip="A — başlangıç",
                ).add_to(folium_map)
            if end_point and end_point.get("lat") is not None and end_point.get("lon") is not None:
                folium.CircleMarker(
                    (float(end_point["lat"]), float(end_point["lon"])),
                    radius=6,
                    color="#dc2626",
                    fill=True,
                    fill_opacity=0.95,
                    tooltip="B — varış",
                ).add_to(folium_map)
        except Exception:
            pass
        if route_metro_stations:
            add_route_metro_markers(folium_map, route_metro_stations)

    if advisor_safe_points:
        _add_advisor_safe_points(folium_map, advisor_safe_points)

    if not route_line_drawn and route_polyline:
        try:
            coords: list[tuple[float, float]] = []
            for p in route_polyline:
                try:
                    la = float(p["lat"])
                    lo = float(p.get("lon", p.get("lng", p.get("longitude"))))
                    coords.append((la, lo))
                except (KeyError, TypeError, ValueError):
                    continue
            if len(coords) >= 2:
                popup_html = (
                    "<div style=\"padding:10px;font-family:system-ui,sans-serif;\">"
                    "<b>Rota çizildi</b><br/>"
                    "Parça parça mesafe için <b>rotayı yeniden çiz</b> veya güncelle."
                    "</div>"
                )
                if advisor_segments:
                    first = advisor_segments[0] if isinstance(advisor_segments[0], dict) else None
                    if first:
                        adv0 = _segment_dict_from_advisor_chunk(first)
                        if adv0:
                            try:
                                popup_html = format_route_segment_popup_html(adv0)
                            except Exception:
                                pass
                folium.PolyLine(
                    coords,
                    color=str(route_color),
                    weight=14,
                    opacity=0.9,
                    popup=folium.Popup(popup_html, max_width=360, parse_html=False),
                    tooltip=_polyline_tooltip_click_hint(),
                ).add_to(folium_map)
                start = coords[0]
                end = coords[-1]
                folium.CircleMarker(
                    start, radius=6, color="#16a34a", fill=True, fill_opacity=0.95, tooltip="A — başlangıç"
                ).add_to(folium_map)
                folium.CircleMarker(
                    end, radius=6, color="#dc2626", fill=True, fill_opacity=0.95, tooltip="B — varış"
                ).add_to(folium_map)
                if route_metro_stations:
                    add_route_metro_markers(folium_map, route_metro_stations)
        except Exception:
            pass
    else:
        # If user picked points but route not computed yet, still show markers.
        try:
            if start_point and start_point.get("lat") is not None and start_point.get("lon") is not None:
                folium.CircleMarker(
                    (float(start_point["lat"]), float(start_point["lon"])),
                    radius=6,
                    color="#16a34a",
                    fill=True,
                    fill_opacity=0.95,
                    tooltip="A",
                ).add_to(folium_map)
            if end_point and end_point.get("lat") is not None and end_point.get("lon") is not None:
                folium.CircleMarker(
                    (float(end_point["lat"]), float(end_point["lon"])),
                    radius=6,
                    color="#dc2626",
                    fill=True,
                    fill_opacity=0.95,
                    tooltip="B",
                ).add_to(folium_map)
        except Exception:
            pass

    if traces:
        filtered_traces: list[dict[str, Any]] = []
        for trace in traces:
            try:
                trace_lat = float(trace["latitude"])
                trace_lon = float(trace["longitude"])
                if (
                    bbox["min_latitude"] <= trace_lat <= bbox["max_latitude"]
                    and bbox["min_longitude"] <= trace_lon <= bbox["max_longitude"]
                ):
                    filtered_traces.append(trace)
            except (KeyError, TypeError, ValueError):
                continue
        add_trace_markers(folium_map, filtered_traces)

    osm_groups_added = False
    if extra_layers:
        for layer_name, points in extra_layers.items():
            if points:
                add_osm_layer_markers(folium_map, layer_name=layer_name, points=points)
                osm_groups_added = True

    if osm_groups_added or bool(route_metro_stations):
        try:
            folium.LayerControl(collapsed=False).add_to(folium_map)
        except Exception:
            pass

    return folium_map


def _sanitize_advisor_popup_text(text: str) -> str:
    """Tavsiye metninde kazara kalan koordinat çiftlerini çıkar; kalan metni tekilleştir."""
    t = (text or "").strip()
    if not t:
        return ""
    t = _COORD_PAIR_IN_TEXT_RE.sub(" ", t)
    return " ".join(t.split())


def _add_advisor_safe_points(
    folium_map: folium.Map,
    advisor_safe_points: list[dict[str, Any]],
) -> None:
    """
    AI'nın bahsettiği güvenli noktaları mor ikonlarla işaretler.
    Popup içinde yalnızca popup_advice (ve kısa tür/isim etiketi); lat/lon gösterilmez.
    """
    group = folium.FeatureGroup(name="Güvenli Noktalar (AI)", show=True)

    for sp in advisor_safe_points:
        try:
            lat = float(sp.get("lat"))
            lon = float(sp.get("lon"))
        except (TypeError, ValueError):
            continue
        sp_type = str(sp.get("type") or "Güvenli Nokta").strip() or "Güvenli Nokta"
        name = str(sp.get("name") or "").strip()
        advice_clean = _sanitize_advisor_popup_text(str(sp.get("popup_advice") or ""))

        if not advice_clean:
            advice_clean = (
                f"Kız kardeşim, burası senin için işaretli bir {sp_type} durağı. "
                f"Aynı noktanın ayrıntılı notu Yol Günlüğü kartlarında 🫂"
            )

        # Mor kalp / mor yıldız seçimi
        if "Aydın" in sp_type or "aydın" in sp_type.lower():
            emoji = "✨"
        else:
            emoji = "💜"

        icon_html = (
            f"<div style='font-size:22px;line-height:22px;'>"
            f"{emoji}</div>"
        )
        icon = folium.DivIcon(html=icon_html, icon_size=(24, 24))
        label_line = (
            f"<div style='font-size:0.82rem;color:#6b7280;margin-bottom:10px;'>"
            f"{html_escape(sp_type)}"
            + (f" · {html_escape(name)}" if name else "")
            + "</div>"
        )
        body_html = f"<div style='color:#111827;'>{html_escape(advice_clean)}</div>"
        popup_html = (
            f"<div style='max-width:320px;font-size:0.95rem;line-height:1.55;'>"
            f"{label_line}{body_html}</div>"
        )

        tip_src = advice_clean.replace("\n", " ").strip()
        if len(tip_src) > 90:
            tip_src = tip_src[:87].rstrip() + "…"
        tooltip_txt = tip_src if tip_src else f"{emoji} {sp_type}"

        folium.Marker(
            location=[lat, lon],
            icon=icon,
            tooltip=folium.Tooltip(tooltip_txt),
            popup=folium.Popup(popup_html, max_width=340),
        ).add_to(group)

    group.add_to(folium_map)


def default_bbox() -> dict[str, float]:
    return dict(CANKAYA_BBOX) 

