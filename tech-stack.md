# 🛠️ Güvenli İzler - Teknoloji Yığını (Tech Stack)

"Güvenli İzler" projesi, modern web teknolojileri, coğrafi bilgi sistemleri (CBS) ve yapay zekayı bir araya getiren hibrit bir mimariyle geliştirilmiştir.

---

## 💻 Yazılım Dili ve Ortam
* **Dil:** Python 3.10+
* **Ortam Yönetimi:** `venv` (Virtual Environment)
* **Bağımlılık Yönetimi:** `requirements.txt`
* **Güvenlik:** `python-dotenv` (API anahtarlarının gizlenmesi için)

---

## 🎨 Frontend (Arayüz)
* **Framework:** [Streamlit](https://streamlit.io/)
* **Görselleştirme:** Interaktif harita katmanları ve gerçek zamanlı kullanıcı paneli.
* **Harita Kütüphanesi:** Folium / Leaflet (Python entegrasyonu ile).

---

## ⚙️ Backend (API & Sunucu)
* **Framework:** [FastAPI](https://fastapi.tiangolo.com/)
* **Sunucu:** Uvicorn (Asynchronous Server Gateway Interface)
* **Mimari:** RESTful API (Frontend ve Backend port bazlı iletişim kurar; 8501 & 8001).

---

## 🧠 Yapay Zeka Entegrasyonu
* **Model:** [Google Gemini AI](https://deepmind.google/technologies/gemini/)
* **Görev:** Kullanıcıya özel üretilen rotanın güvenlik analizini yapmak ve "Sanal Refakatçi" olarak metinsel geri bildirim sağlamak.

---

## 🗺️ Coğrafi Veri ve Algoritma (GIS)
* **Veri Kaynağı:** [OpenStreetMap (OSM)](https://www.openstreetmap.org/)
* **Ağ Analizi:** [OSMnx](https://osmnx.readthedocs.io/) (Sokak ağlarını indirme ve modelleme).
* **Algoritma:** NetworkX (En kısa ve en güvenli rota ağırlıklandırma hesaplamaları).
* **CBS Konseptleri:** Geomatics Engineering temelli "Güvenlik Puanlama" (Aydınlatma direkleri, ana yol yakınlığı vb.).

---

## 🚀 Versiyon Kontrol ve Dağıtım
* **Version Control:** Git
* **Hosting/Depo:** GitHub
