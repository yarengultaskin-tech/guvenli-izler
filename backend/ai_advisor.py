"""Kişiselleştirilmiş güvenlik tavsiyesi — Google Gemini (Buildathon)."""

from __future__ import annotations

import math
import os
import re
import json
from pathlib import Path
from typing import Any

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None  # type: ignore[assignment]


def strip_safe_point_json_from_advice_markdown(text: str) -> str:
    """
    Kullanıcı arayüzünde gösterilecek danışman metninden safe_point_popups JSON'unu çıkarır.
    Model ```json bloğunu hatalı kapatırsa veya regex kaçırırsa yine de ham kod sızmasın.
    """
    if not text or not str(text).strip():
        return (text or "").strip()
    s = str(text)

    # 1) ``` … ``` çitleri: yalnızca safe_point içeren blokları sil; diğer kod bloklarına dokunma.
    parts: list[str] = []
    pos = 0
    while True:
        start = s.find("```", pos)
        if start < 0:
            parts.append(s[pos:])
            break
        parts.append(s[pos:start])
        line_end = s.find("\n", start)
        if line_end < 0:
            parts.append(s[start:])
            break
        close = s.find("```", line_end + 1)
        if close < 0:
            # Kapanmamış ```json: kalanı gösterme (ham yapı taşmasın)
            break
        body = s[line_end + 1 : close].strip()
        if "safe_point_popups" in body or "safePoints" in body:
            pos = close + 3
            continue
        parts.append(s[start : close + 3])
        pos = close + 3
    s = "".join(parts).strip()

    # 2) Çitsiz sondaki { "safe_point_popups": ... } objesi
    for key in ('"safe_point_popups"', "'safe_point_popups'"):
        k = s.rfind(key)
        if k < 0:
            continue
        brace = s.rfind("{", 0, k)
        if brace < 0:
            continue
        depth = 0
        cut_end = -1
        for j in range(brace, len(s)):
            ch = s[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    cut_end = j + 1
                    break
        if cut_end > brace:
            s = (s[:brace] + s[cut_end:]).strip()

    # 3) Tek satırlık kalan ```json veya { ile başlayan artıklar
    s = re.sub(r"\n```(?:json)?\s*$", "", s, flags=re.IGNORECASE).strip()
    return s


def extract_safe_point_popups_from_model_output(raw_text: str) -> tuple[list[dict[str, Any]], str, bool]:
    """
    Model çıktısından ```json ... safe_point_popups ... ``` bloklarını çıkarır.
    JSON önce veya sonra gelebilir; mor kalp verisi kesilmeden önce üretildiyse yakalanır.
    Dönüş: (popups, samimi_metin, json_parse_tamam_mı)
    """
    if not raw_text or not str(raw_text).strip():
        return [], "", True
    text = str(raw_text)
    popups: list[dict[str, Any]] = []
    parts: list[str] = []
    pos = 0
    json_ok = True
    saw_target_fence = False
    while True:
        start = text.find("```", pos)
        if start < 0:
            parts.append(text[pos:])
            break
        parts.append(text[pos:start])
        line_end = text.find("\n", start)
        if line_end < 0:
            parts.append(text[start:])
            json_ok = False
            break
        close = text.find("```", line_end + 1)
        if close < 0:
            parts.append(text[start:])
            json_ok = False
            break
        body = text[line_end + 1 : close].strip()
        consumed = False
        if "safe_point_popups" in body or "safePoints" in body:
            saw_target_fence = True
            try:
                payload = json.loads(body)
                if isinstance(payload, dict):
                    sp = payload.get("safe_point_popups") or payload.get("safePoints") or []
                    if isinstance(sp, list):
                        popups.extend([p for p in sp if isinstance(p, dict)])
                        consumed = True
            except json.JSONDecodeError:
                json_ok = False
        if not consumed:
            parts.append(text[start : close + 3])
        pos = close + 3
    advice = "".join(parts).strip()
    advice = strip_safe_point_json_from_advice_markdown(advice)
    if saw_target_fence and not popups:
        json_ok = False
    return popups, advice, json_ok


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """İki koordinat arası metre (POI eşleştirmesi için)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))

# Proje kökündeki .env (backend çalışma dizininden bağımsız)
_ENV_CANDIDATES = (
    Path(__file__).resolve().parent.parent / ".env",
    Path(__file__).resolve().parent / ".env",
)

_ADVISOR_SYSTEM = """Sen Güvenli İzler uygulamasının AI refakatçisisin.
Kullanıcıyla ilişkin: büyük kız kardeş - küçük kardeş.
Sıcak, koruyucu, gerçekçi ve pratik konuş. Aşırı dramatik olma.

KİŞİLİK:
- Resmi dil kullanma, doğal ve yakın konuş.
- "Kız kardeşim", "canım", "tatlım" gibi hitapları dozunda kullan (her cümlede değil).
- Endişe pompalama; güçlendir ve net aksiyon ver.
- Emoji ölçülü kullan: 💜 🚶‍♀️ 👀 🎧 ✅

ÇIKTI FORMATI (zorunlu sıra):
1) Kısa selamlama + genel durum (1-2 cümle; puanı "iyi/orta/dikkat gerektiriyor" diye yorumla)
2) Rota boyunca ne beklemeli (metre bazlı anlat; düşük puanlı kısımların yaklaşık nerede başladığını söyle)
3) Pratik anlık tavsiyeler (duruma göre seç; geceyse çevreyi dinleme, ıssız kısımda hazırlık, yakın güvenli nokta referansı, puan düşükse başlangıç/bitiş kaydırma)
4) Kapanış (kısa, güçlendirici, abla tonu)

YASAK:
- "Veri belirsizliği oranı" ifadesini kullanıcıya söyleme
- Ham teknik döküm yapma (ör. "yüksek 0 orta 0 düşük 1")
- "Bu rota biraz zorlayıcı görünüyor" gibi muğlak cümleler kurma
- Uzun paragraflar yazma (her bölüm en fazla 4-5 cümle)

MESAFE KURALI:
- Verilen metrelere dayan, sayı uydurma.
- Mümkün olduğunda yakın metro/karakol/eczane/lamba bilgisini metre ile söyle.

ÇIKTI BÜTÜNLÜĞÜ:
- Önce kullanıcıya okunur danışman metnini ver.
- Ardından safe_point_popups için tek bir ```json bloğu üret.
- JSON formatı:
{
  "safe_point_popups": [
    {"name": "...", "type": "Metro|Karakol|Eczane|Taksi|Aydınlık", "lat": 0.0, "lon": 0.0, "popup_advice": "..."}
  ]
}
- Koordinatları yalnız verilen veriden al, uydurma nokta üretme."""


def _ensure_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for p in _ENV_CANDIDATES:
        if p.is_file():
            load_dotenv(p, override=True)
            return
    load_dotenv(override=True)


def _get_secret_value(key: str, default: str = "") -> str:
    """Read from Streamlit secrets first, then environment."""
    try:
        if st is not None and key in st.secrets:
            value = st.secrets[key]
            if value is not None:
                return str(value).strip()
    except Exception:
        pass
    return str(os.getenv(key, default)).strip()


def _first_section_heading_line(score: float) -> str:
    """Düşük puanda yeşil tik verme — kullanıcı arayüzüyle uyum."""
    if score > 80.0:
        return "**✅ Genel Güvenlik Durumu:**"
    if score >= 50.0:
        return "**📊 Genel Güvenlik Durumu:**"
    return "**⚠️ Genel Güvenlik Durumu:**"


def _fix_first_heading_emoji(text: str, score: float) -> str:
    if score > 80.0:
        emoji = "✅"
    elif score >= 50.0:
        emoji = "📊"
    else:
        emoji = "⚠️"
    return re.sub(
        r"^(\s*\*\*)(?:✅|📊|⚠️)?\s*(Genel Güvenlik Durumu:\*\*)",
        rf"\1{emoji} \2",
        text.strip(),
        count=1,
        flags=re.MULTILINE,
    )


_POPUP_TYPE_TO_SEG_KEY: dict[str, str] = {
    "Karakol": "nearest_police",
    "Eczane": "nearest_pharmacy",
    "Metro": "nearest_metro",
    "Taksi": "nearest_taxi",
    "Aydınlık": "lighting",
}


def _poi_dist_line(kind_tr: str, poi: Any) -> str:
    if not isinstance(poi, dict):
        return f"{kind_tr}: bu parçada kayıt yok"
    name = poi.get("name")
    dm = poi.get("distance_m")
    if name and dm is not None:
        try:
            d_int = int(round(float(dm)))
            return f"{kind_tr}: {name} (~{d_int} m; rota parçasının orta noktasından)"
        except (TypeError, ValueError):
            pass
    if name:
        return f"{kind_tr}: {name} (mesafe net değil)"
    return f"{kind_tr}: yakın kayıt yok"


def _lighting_line(lit: Any) -> str:
    if not isinstance(lit, dict):
        return "Sokak lambası: kayıt yok"
    dm = lit.get("nearest_lamp_distance_m")
    if dm is None:
        return "Sokak lambası: uzak veya veri yok"
    try:
        d_int = int(round(float(dm)))
    except (TypeError, ValueError):
        return "Sokak lambası: veri yok"
    return f"En yakın sokak lambası ~{d_int} m (rota parçası ortasından)"


def _format_advisor_segments_for_prompt(segments: list[Any], *, max_rows: int = 40) -> str:
    """Gemini için zorunlu mesafe girdisi — her 250 m parça için ölçülmüş uzaklıklar."""
    lines: list[str] = []
    slice_seg = segments[:max_rows]
    for i, seg in enumerate(slice_seg, start=1):
        if not isinstance(seg, dict):
            continue
        s0, s1, smid = seg.get("along_route_start_m"), seg.get("along_route_end_m"), seg.get("along_route_mid_m")
        try:
            a0 = int(round(float(s0))) if s0 is not None else None
            a1 = int(round(float(s1))) if s1 is not None else None
            mid = int(round(float(smid))) if smid is not None else None
        except (TypeError, ValueError):
            a0 = a1 = mid = None
        if a0 is not None and a1 is not None and mid is not None:
            pos = f"Rota boyunca {a0}–{a1} m aralığı (orta nokta ~{mid} m)"
        elif mid is not None:
            pos = f"Rota üzerinde ortalama konum ~{mid} m"
        else:
            pos = f"Parça {i} (konum metrajı yok)"
        pol = _poi_dist_line("Karakol", seg.get("nearest_police"))
        ph = _poi_dist_line("Eczane", seg.get("nearest_pharmacy"))
        mx = _poi_dist_line("Metro", seg.get("nearest_metro"))
        tx = _poi_dist_line("Taksi", seg.get("nearest_taxi"))
        li = _lighting_line(seg.get("lighting"))
        lines.append(
            f"{i}) {pos}\n"
            f"   • {pol}\n"
            f"   • {ph}\n"
            f"   • {mx}\n"
            f"   • {tx}\n"
            f"   • {li}"
        )
    if not lines:
        return "(Bu rota için 250 m parça / mesafe listesi boş.)"
    more = len(segments) - len(slice_seg)
    footer = ""
    if more > 0:
        footer = f"\n(Not: Toplam {len(segments)} parça var; kısaltma için ilk {max_rows} parça gönderildi; kalan {more} parça için de benzer veri vardır.)"
    return "ROTA PARÇALARI (250 m — ölçülmüş uzaklıklar; tavsiyende bunları kullan):\n" + "\n\n".join(lines) + footer


def _closest_poi_summary_lines(segments: list[Any], *, limit: int = 10) -> str:
    rows: list[tuple[float, str]] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        mid = seg.get("along_route_mid_m")
        try:
            mid_i = int(round(float(mid))) if mid is not None else None
        except (TypeError, ValueError):
            mid_i = None
        loc = f"rotada ~{mid_i} m" if mid_i is not None else "rotada bir bölümde"
        for label, key in (
            ("Karakol", "nearest_police"),
            ("Eczane", "nearest_pharmacy"),
            ("Metro", "nearest_metro"),
            ("Taksi", "nearest_taxi"),
        ):
            poi = seg.get(key)
            if not isinstance(poi, dict):
                continue
            dm, name = poi.get("distance_m"), poi.get("name")
            if dm is None or not name:
                continue
            try:
                d = float(dm)
            except (TypeError, ValueError):
                continue
            rows.append((d, f"- {label} «{str(name).strip()}»: ~{int(round(d))} m ({loc})"))
    rows.sort(key=lambda x: x[0])
    seen: set[str] = set()
    out: list[str] = []
    for _, txt in rows:
        if txt in seen:
            continue
        seen.add(txt)
        out.append(txt)
        if len(out) >= limit:
            break
    return "\n".join(out) if out else "- (Yakın güvenli nokta mesafesi bu listede çok sınırlı.)"


def _enrich_safe_point_popups_with_distances(
    popups: list[dict[str, Any]],
    segments: list[Any],
) -> list[dict[str, Any]]:
    """Model mesafe atladıysa popup_advice içine ölçülmüş m değerini ekle."""
    if not popups or not segments:
        return popups
    enriched: list[dict[str, Any]] = []
    for raw in popups:
        if not isinstance(raw, dict):
            continue
        p = dict(raw)
        try:
            plat = float(p.get("lat"))
            plon = float(p.get("lon"))
        except (TypeError, ValueError):
            enriched.append(p)
            continue
        ptype = str(p.get("type") or "").strip()
        seg_key = _POPUP_TYPE_TO_SEG_KEY.get(ptype)
        advice = str(p.get("popup_advice") or "")
        best_dm: float | None = None
        best_mid: float | None = None
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            if seg_key == "lighting":
                lit = seg.get("lighting") if isinstance(seg.get("lighting"), dict) else {}
                la, lo = lit.get("lat"), lit.get("lon")
                if la is None or lo is None:
                    continue
                if _haversine_m(plat, plon, float(la), float(lo)) > 80.0:
                    continue
                dm = lit.get("nearest_lamp_distance_m")
            elif seg_key:
                poi = seg.get(seg_key)
                if not isinstance(poi, dict):
                    continue
                la, lo = poi.get("lat"), poi.get("lon")
                if la is None or lo is None:
                    continue
                if _haversine_m(plat, plon, float(la), float(lo)) > 80.0:
                    continue
                dm = poi.get("distance_m")
            else:
                continue
            if dm is None:
                continue
            try:
                dmf = float(dm)
            except (TypeError, ValueError):
                continue
            if best_dm is None or dmf < best_dm:
                best_dm = dmf
                sm = seg.get("along_route_mid_m")
                try:
                    best_mid = float(sm) if sm is not None else None
                except (TypeError, ValueError):
                    best_mid = None
        if best_dm is not None:
            has_m = bool(re.search(r"\d{2,}\s*m", advice)) or bool(re.search(r"~\s*\d+", advice))
            if not has_m:
                tail = f"Bu rota diliminde buraya yaklaşık ~{int(round(best_dm))} m mesafedesin"
                if best_mid is not None:
                    tail += f" (rota üzerinde ~{int(round(best_mid))} m)"
                tail += " 🫂"
                advice = (advice.rstrip() + " " + tail).strip()
                p["popup_advice"] = advice
        enriched.append(p)
    return enriched


def _patch_incomplete_advisor(text: str, ctx: dict[str, Any]) -> str:
    """Model tek bölümde keserse eksik başlıkları halk dilinde tamamla."""
    t = text.strip()
    if not t:
        return t
    score = float(ctx.get("safety_score") or 0.0)
    unk = float(ctx.get("unknown_ratio") or 0.0)
    time_human = "Gece" if str(ctx.get("time_mode", "day")).lower() == "night" else "Gündüz"
    metro = str(ctx.get("metro_proximity_summary") or "").strip()
    night = time_human == "Gece"

    has_light = ("💡" in t) and ("Neye" in t or "Dikkat" in t)
    has_feet = ("👣" in t) and ("Tavsiye" in t or "Kısa" in t)

    if has_light and has_feet:
        return t

    parts: list[str] = [t.rstrip()]
    if not has_light:
        mshort = metro[:200] + ("…" if len(metro) > 200 else "") if metro else "Metro tarafını haritadan bir daha gözden geçirmen iyi olur."
        parts.append(
            "💡 Neye Dikkat Etmelisin:\n"
            f"Uygulamaya göre güzergâhın bir bölümünde aydınlık veya çevre bilgisi tam oturmayabilir (kabaca yüzde {unk * 100:.0f} belirsiz sayılıyor). "
            f"Toplu taşıma özeti: {mshort}.\n"
            "Kendi gözünle de hangi kısımların daha aydınlık olduğunu not etmek faydalı."
        )
    if not has_feet:
        # Kısa tavsiyeyi “son görsel” stilinde, önce “Bak,” ile başlat.
        metro_short = metro[:140] + ("…" if len(metro) > 140 else "") if metro else "yakınlarda metro çıkışları"
        if night:
            tip = (
                f"Bak, az ileride metro çıkışları / girişleri var: {metro_short}. "
                "Geceyse adımlarını biraz seri tutup ana caddelerden çok uzaklaşmamak iyi olabilir."
            )
        else:
            tip = (
                f"Bak, az ileride metro çıkışları / girişleri var: {metro_short}. "
                "Gündüzde bile puan düşükse rotayı mümkün olduğunca ana cadde civarında tutmak iyi olur."
            )
        parts.append(f"👣 Kısa Tavsiye:\n{tip}")

    return "\n\n".join(parts)


def _build_user_message(ctx: dict[str, Any]) -> str:
    time_human = "Gece" if str(ctx.get("time_mode", "day")).lower() == "night" else "Gündüz"
    score = float(ctx.get("safety_score") or 0.0)
    user_status = str(ctx.get("user_status") or "🫂 Yalnızım")
    metro_summary = str(ctx.get("metro_proximity_summary") or "").strip()

    metro_names = metro_summary
    if ":" in metro_summary:
        metro_names = metro_summary.split(":", 1)[1].strip()
    advisor_segments = ctx.get("advisor_segments") or []
    if not isinstance(advisor_segments, list):
        advisor_segments = []

    segment_block = _format_advisor_segments_for_prompt(advisor_segments)
    closest_block = _closest_poi_summary_lines(advisor_segments, limit=10)

    return (
        "Kullanıcı verisi (ham):\n"
        f"- overall_score: {score:.0f}\n"
        f"- distance_meters: rota parçalarındaki metrajdan yorumla\n"
        f"- nearby_safe_points: {metro_names or '—'}\n"
        f"- user_status: {user_status}\n"
        f"- time_mode: {time_human}\n\n"
        f"{segment_block}\n\n"
        "En yakın güvenli nokta özeti (mesafe ile):\n"
        f"{closest_block}\n\n"
        "Görev:\n"
        "- Çıktıyı 4 bölümlü formatta ver: genel durum, rota boyunca beklenenler, pratik tavsiye, kapanış.\n"
        "- Teknik jargon kullanma; samimi ama net ol.\n"
        "- Mesafeleri somut kullan; mümkünse 'X metre ileride ...' şeklinde yaz.\n"
        "- Gece ise çevreyi dinleme ve dikkat tavsiyesi ekle.\n"
        "- Puan düşükse başlangıç/bitiş kaydırma önerisini kısa ve net ver.\n"
        "- JSON bloğunda safe_point_popups üret ve popup_advice alanlarını da bu üslupla yaz.\n"
        "- Güvenli nokta adı/koordinatlarını yalnız verilen veriden al; uydurma nokta üretme.\n"
    )


def generate_security_advice(context: dict[str, Any]) -> tuple[str, list[dict[str, Any]], bool]:
    """
    Gemini SDK ile danışman yanıtı üretir.

    GEMINI_API_KEY st.secrets içinde zorunludur. İsteğe bağlı: GEMINI_MODEL, GEMINI_MAX_OUTPUT_TOKENS.
    Dönüş: (advice_metni, safe_point_popups, json_ve_harita_verisi_tam_mı).
    """
    _ensure_dotenv()
    try:
        api_key = str(st.secrets["GEMINI_API_KEY"]).strip() if st is not None else ""
    except Exception:
        api_key = ""
    api_key = api_key.lstrip("\ufeff").strip('"').strip("'")
    if not api_key:
        raise ValueError("GEMINI_API_KEY tanımlı değil. `.streamlit/secrets.toml` veya Streamlit Cloud secrets bölümünü kontrol edin.")

    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise RuntimeError("google-generativeai paketi kurulu değil.") from exc

    # gemini-2.0-flash bazı hesaplarda 429 kota verir; flash-latest genelde AI Studio ücretsiz kotada çalışır
    model_name = (os.getenv("GEMINI_MODEL") or "gemini-flash-latest").strip()
    genai.configure(api_key=api_key)

    try:
        max_out = int((os.getenv("GEMINI_MAX_OUTPUT_TOKENS") or "4096").strip())
    except ValueError:
        max_out = 4096
    max_out = max(2048, min(8192, max_out))

    system_override = context.get("system_instruction_override")
    system_instruction = str(system_override) if system_override else _ADVISOR_SYSTEM

    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=system_instruction,
    )
    prompt = _build_user_message(context)
    response = model.generate_content(
        prompt,
        generation_config={
            "temperature": 0.45,
            "max_output_tokens": max_out,
        },
    )

    raw_text = ""
    try:
        raw_text = (response.text or "").strip()
    except Exception:
        # Güvenlik filtresi veya boş adayda .text erişilemeyebilir
        if getattr(response, "candidates", None):
            parts = getattr(response.candidates[0], "content", None)
            if parts and getattr(parts, "parts", None):
                raw_text = "".join(getattr(p, "text", "") for p in parts.parts).strip()

    if not raw_text:
        advice_fallback = (
            "Şu an kişiselleştirilmiş bir öneri üretilemedi. "
            "Rota puanını ve çevreyi kendin değerlendirip tedbirli ilerlemek iyi olur."
        )
        return advice_fallback, [], False

    safe_point_popups, advice_text, json_ok = extract_safe_point_popups_from_model_output(raw_text)

    # Sanal Refakatçı: rota parçası varken boş harita JSON'u genelde kesilme demektir
    if context.get("system_instruction_override"):
        adv_segs = context.get("advisor_segments") or []
        if isinstance(adv_segs, list) and len(adv_segs) > 0 and not safe_point_popups:
            json_ok = False

    # Override akışında (Sanal Refakatçi) metin son cümle kurallarına göre üretileceği için
    # ek patch'leme yapmıyoruz.
    if not context.get("system_instruction_override"):
        advice_text = _patch_incomplete_advisor(advice_text, context)

    segs = context.get("advisor_segments") or []
    if isinstance(segs, list) and safe_point_popups:
        safe_point_popups = _enrich_safe_point_popups_with_distances(
            [p for p in safe_point_popups if isinstance(p, dict)],
            segs,
        )
    return advice_text, safe_point_popups, json_ok
