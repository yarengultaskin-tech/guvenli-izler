# 🗺️ Güvenli İzler (Safe Tracks)

**Ankara Odaklı, GIS ve Yapay Zeka Destekli Akıllı Güvenlik Rota Planlayıcı**

> Özellikle gece geç saatlerde seyahat eden yayalar ve öğrenciler için tasarlanmış, güvenlik odaklı bir rota asistanı.

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-Frontend-FF4B4B?style=flat&logo=streamlit&logoColor=white)](https://streamlit.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## 📖 Proje Hakkında

**Güvenli İzler**, OpenStreetMap verilerini, gelişmiş coğrafi analiz yöntemlerini (GIS) ve Google Gemini AI'yı bir araya getirerek kullanıcıya sadece "en kısa" değil, **"en güvenli"** yolu sunan akıllı bir rota planlayıcısıdır.

---

## ✨ Özellikler

- 🔦 **Akıllı Güvenlik Puanlaması** — Aydınlatma direkleri, ana yollar ve güvenli noktalar (metro, karakol vb.) üzerinden dinamik ağırlıklandırma
- 🤖 **AI Sanal Refakatçi** — Google Gemini AI entegrasyonu ile rotanın neden güvenli olduğuna dair metin analizi ve tavsiyeler
- 🗺️ **İnteraktif Harita** — Streamlit tabanlı gerçek zamanlı görselleştirme ve kullanıcı dostu kontrol paneli
- 🧩 **Modüler Mimari** — `features/` klasörü altında toplanmış, temiz ve sürdürülebilir kod yapısı

---

## 🛠️ Teknoloji Yığını

| Katman | Teknoloji |
|---|---|
| **Dil** | Python 3.10+ |
| **Frontend** | Streamlit |
| **Backend** | FastAPI (Uvicorn) |
| **Yapay Zeka** | Google Gemini API |
| **GIS / Harita** | OSMnx, NetworkX, Folium, OpenStreetMap |

---

## 📁 Proje Yapısı
```
guvenli_izler/
├── backend/            # FastAPI uç noktaları (API routes)
├── features/           # Temel algoritmalar (rota hesaplama, güvenlik puanlaması)
├── app.py              # Streamlit arayüz dosyası
├── requirements.txt    # Bağımlılıklar
└── README.md           # Proje dokümantasyonu
```

---

## 🚀 Kurulum ve Çalıştırma

### 1. Depoyu Klonlayın
```bash
git clone https://github.com/yarengultaskin-tech/guvenli_izler.git
cd guvenli_izler
```

### 2. Sanal Ortam Oluşturun ve Aktif Edin
```bash
python -m venv venv

# Windows
.\venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Bağımlılıkları Yükleyin
```bash
pip install -r requirements.txt
```

### 4. Ortam Değişkenlerini Ayarlayın

Proje kök dizininde `.env` dosyası oluşturun:
```env
GEMINI_API_KEY=buraya_kendi_anahtarinizi_yazin
```

> 🔑 Google Gemini API anahtarı almak için [Google AI Studio](https://aistudio.google.com/) adresini ziyaret edin.

### 5. Backend'i Başlatın
```bash
uvicorn backend.main:app --reload --port 8001
```

### 6. Frontend'i Başlatın
```bash
streamlit run app.py
```

Uygulama varsayılan olarak `http://localhost:8501` adresinde açılır.

---

## 🤝 Katkıda Bulunma

Katkılarınızı memnuniyetle karşılıyoruz! Lütfen bir `issue` açın veya `pull request` gönderin.

1. Projeyi fork'layın
2. Yeni bir branch oluşturun (`git checkout -b feature/yeni-ozellik`)
3. Değişikliklerinizi commit'leyin (`git commit -m 'Yeni özellik eklendi'`)
4. Branch'inizi push'layın (`git push origin feature/yeni-ozellik`)
5. Pull Request açın

---

## 📄 Lisans

Bu proje [MIT Lisansı](LICENSE) kapsamında lisanslanmıştır.

---

<p align="center">
  Güvenli yolculuklar 🌙
</p>
