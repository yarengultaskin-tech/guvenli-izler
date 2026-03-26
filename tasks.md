# Güvenli İzler — Geliştirme Görev Listesi

Bu liste [prd.md](./prd.md) ürün gereksinim dokümanına göre hazırlanmıştır. Görevleri sırayla veya paralel ekiplerle ilerletebilirsiniz; bağımlılıklar alt başlıklarda belirtilmiştir.

---

## 0. Proje kurulumu (tüm fazlar için temel)

- [x] **0.1** Monorepo veya ayrı `frontend` / `backend` klasör yapısını oluştur; sürüm kontrolü (Git) ve `.env` örnek dosyalarını tanımla.
- [x] **0.2** PostgreSQL + PostGIS kurulumu (yerel veya Docker); veritabanı bağlantı bilgilerini dokümante et.
- [x] **0.3** Python sanal ortamı, `FastAPI` tabanlı API iskeleti (health endpoint, CORS, temel proje yapısı).
- [ ] **0.4** React uygulaması iskeleti; harita sayfası için boş layout ve yönlendirme. *(MVP’de proje skill’i gereği Streamlit kullanılıyor — 1.x eşdeğeri `app.py`.)*
- [x] **0.5** Güvenlik skoru formülü (`G_s`) için yapılandırılabilir ağırlıklar (`w_1`–`w_4`) ve birim test stratejisi kararı.

**Bağımlılık:** 0.2 → 0.3; 0.4 bağımsız başlayabilir.

---

## Faz 1 — Hazırlık (MVP)

- [x] **1.1** Çankaya odaklı harita görünümü: OpenStreetMap altlığı + Leaflet entegrasyonu; başlangıç merkezi ve zoom (Çankaya). *(Uygulama: Streamlit + Folium + OSM.)*
- [x] **1.2** PostGIS’te **Kullanıcı İzleri** şeması: konum (geometry), etiket türü (`Güvenli`, `Az Işıklı`, `Issız` vb.), zaman damgası, isteğe bağlı anonim kullanıcı kimliği; indeksler (coğrafi + zaman).
- [x] **1.3** FastAPI: iz ekleme (POST) ve listeleyen (GET, bbox veya yakınlık) uç noktaları; giriş doğrulama (Pydantic).
- [x] **1.4** React: haritada pin bırakma akışı (tıklama veya “mevcut konum”) ve etiket seçimi; API’ye kayıt ve başarı/hata geri bildirimi. *(Streamlit: harita tıklaması + `app.py`.)*
- [x] **1.5** Haritada mevcut izleri katman olarak gösterme (marker/cluster); basit filtre (etiket türü).
- [x] **1.6** Çankaya poligon/bbox iyileştirmesi: bbox ile haritayı sınırlayıp dışarı taşmayı azaltıyor; iz görüntüleme bu sınır odaklı çalışıyor.

**Bağımlılık:** 1.2 → 1.3 → 1.4 → 1.5; 1.1 paralel.

---

## Faz 2 — Coğrafi veri entegrasyonu

- [x] **2.1** Açık veri kaynaklarını netleştir: OSM Overpass üzerinden `police_stations`, `street_lamps`, `parks` kaynakları tanımlandı.
- [x] **2.2** Ham veriyi ETL ile çekme: backend'de `backend/osm_layers.py` eklendi; JSON cache (`data/osm_cache`) + bbox sorgusu ile katman verileri üretiliyor.
- [ ] **2.3** GeoServer kurulumu; WMS/WFS katmanları (aydınlatma, güvenlik); stil (SLD) ve Çankaya clip/maskeleme.
- [x] **2.4** Harita katmanlarını uygulamada aç/kapa: Streamlit + Folium üzerinde OSM katmanları (karakol/aydınlatma/park) toggle ile gösteriliyor.
- [ ] **2.5** (İsteğe bağlı MVP+) **Anlık hareketlilik** için topluluk girişi: “kalabalık” / “tenha” etiketleri veya kısa ömürlü durum güncellemesi — PRD 4.1 ile uyumlu veri modeli ve API.
- [x] **2.6** Faz 2 ara çıkış kriteri: En az iki anlamlı katman (karakollar + parklar/aydınlatma) haritada servis ediliyor. *(GeoServer adımı 2.3 ile ayrı kalemde devam ediyor.)*

**Bağımlılık:** 1.x tamam veya en az 1.1 + 1.2; 2.3, 2.2’ye bağlı.

---

## Faz 3 — Güvenli rota ve algoritma

- [ ] **3.1** Sokak ağı verisi: OSM veya belediye yol geometrileri; graf oluşturma (düğümler/kenarlar) ve PostGIS ile hizalama.
- [ ] **3.2** Her kenar için bileşen skorları: `A` (aydınlatma), `H` (hareketlilik), `G` (güvenlik yakınlığı), `R` (kullanıcı risk bildirimleri) — 0–1 normalize; PRD’deki `G_s` ile birleştirme.
- [ ] **3.3** Kenar “maliyetini” güvenlik skorundan türetme (ör. yüksek güvenlik → düşük ağırlık); Dijkstra veya A* ile A→B en kısa yol yerine güvenlik ağırlıklı en iyi yol.
- [ ] **3.4** FastAPI: rota isteği (iki nokta veya adres → koordinat geocode aşaması ayrı görev); yanıtta geometri ve özet metrikler.
- [ ] **3.5** React: başlangıç/bitiş seçimi, rota çizimi, alternatif “en kısa” ile karşılaştırma (isteğe bağlı).
- [ ] **3.6** Performans: büyük graf için ön hesaplama, önbellek veya bölge bazlı alt graf stratejisi değerlendirmesi.
- [ ] **3.7** Faz 3 çıkış kriteri: Seçilen iki nokta arasında güvenlik ağırlıklı rota üretiliyor ve haritada gösteriliyor.

**Bağımlılık:** Faz 2 veri katmanları ve 1.x iz verisi `R` için faydalı; 3.1 graf olmadan 3.3 yapılamaz.

---

## Faz 4 — Test ve lansman

- [ ] **4.1** Birim ve entegrasyon testleri (API, skor hesaplama, kritik harita akışları).
- [ ] **4.2** Gizlilik ve güvenlik: konum verisi, KVKK metni, kötüye kullanım önleme (rate limit, moderasyon ihtiyacı değerlendirmesi).
- [ ] **4.3** Kapalı beta: Hacettepe, ODTÜ vb. hedef kitle ile geri bildirim formu ve ölçüm (hata, kullanılabilirlik).
- [ ] **4.4** Üretim dağıtımı: barındırma, HTTPS, yedekleme, PostGIS ve GeoServer operasyon runbook’u.
- [ ] **4.5** Planlamacı persona: risk/ısı haritası görünümü (aggregasyon, gizlilik korumalı) — PRD 3. bölüm ile hizalama; Faz 4 veya sonraki sürüm.

**Bağımlılık:** MVP + tercihen Faz 3 rota.

---

## Persona kontrol listesi (PRD ile hizalama)

| Persona        | İlgili görevler                                      |
|----------------|------------------------------------------------------|
| Aktif kullanıcı | 1.1, 1.4–1.5, 3.5, 4.2                              |
| Veri gönüllüsü  | 1.4–1.5, 2.5                                        |
| Planlamacı      | 2.3–2.4, 4.5, ısı haritası için ek analitik görevler |

---

## Notlar

- **Öncelik sırası önerisi:** 0 → 1 → 2 → 3 → 4.
- PRD’deki denklem sabitleri (`w` değerleri) ürün ve hukuk/etik incelemesiyle birlikte ayarlanmalı; başlangıçta varsayılanlar ve A/B veya uzman görüşü ile iterasyon planlanabilir.
- GeoServer, Docker ile standartlaştırılabilir; geliştirme ve üretim ortamlarını eşitlemek Faz 2’yi kolaylaştırır.
