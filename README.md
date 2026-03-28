# 🗺️ Güvenli İzler (Safe Tracks)

## 🚩 Problem

Günümüzde pek çok kadın, özellikle akşam saatlerinde sokakta yalnız yürürken kendini ciddi anlamda güvensiz hissetmektedir. Mevcut navigasyon araçları kullanıcıyı sadece "en kısa" mesafeye odaklı ara sokaklara yönlendirirken; aydınlatma yetersizliği, ıssız alan,karakol yakınlığı ve geçmiş güvenlik verileri gibi hayati parametreleri tamamen göz ardı etmektedir. Bu durum, şehir içi hareketliliği kadınlar için bir mesafe probleminden ziyade, bir güvenlik bariyerine dönüştürmektedir.


 ## 💡 Çözüm
 
Güvenli İzler, Geomatik Mühendisliği prensiplerini modern yapay zeka teknolojileriyle birleştirerek "en kısa" yolu değil, "en güvenli" yolu sunan dinamik bir web harita uygulamasıdır. Uygulamanın kalbinde yer alan Google Gemini AI, OpenStreetMap verileri üzerinden çekilen aydınlatma, işletme yoğunluğu ve çevresel faktörleri gerçek zamanlı olarak analiz eder. Sadece bir rota çizmekle kalmaz, kullanıcıya o güzergahın neden güvenli olduğunu açıklayan (örneğin: "Bu rota ana cadde üzerindedir ve ışıklandırması yüksektir") mantıksal bir güvenlik analizi sunarak bilinçli ve güvenli bir ulaşım deneyimi sağlar.


---

 ## ✨ Özellikler

- 🔦 **Akıllı Güvenlik Puanlaması** — Aydınlatma direkleri, ana yollar ve güvenli noktalar (metro, karakol vb.) üzerinden dinamik ağırlıklandırma
- 🤖 **AI Sanal Refakatçi** — Google Gemini AI entegrasyonu ile rotanın neden güvenli olduğuna dair metin analizi ve tavsiyeler
- 🗺️ **İnteraktif Harita** — Streamlit tabanlı gerçek zamanlı görselleştirme ve kullanıcı dostu kontrol paneli
- 🧩 **Modüler Mimari** — `features/` klasörü altında toplanmış, temiz ve sürdürülebilir kod yapısı
 
---

## Canlı Demo
Yayın Linki:https://guvenli-izlerv2.streamlit.app/

Demo Video: https://www.loom.com/share/ac4d95d383944023bce1f7b30c524cd2

Notion Proje Portfolyosu: https://giddy-limit-c8d.notion.site/G-venli-zler-Proje-Portfolyosu-330422358ce9806cab40f80d5db76387?source=copy_link

 
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
git clone https://github.com/yarengultaskin-tech/guvenli-izler.git
cd guvenli-izler
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

### 4. Streamlit Secrets Ayarlayın

Proje kök dizininde `.streamlit/secrets.toml` dosyası oluşturun:
```toml
GEMINI_API_KEY="buraya_kendi_anahtarinizi_yazin"
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


---

<p align="center">
  Güvenli yolculuklar 🌙
</p>
