# 🗺️ Güvenli İzler - Kullanıcı Akışı (User Flow)

Bu döküman, **Güvenli İzler** uygulamasını kullanan bir kullanıcının (özellikle gece geç saatte eve dönen bir öğrencinin) uygulama içindeki yolculuğunu tanımlar.

---

## 🚀 1. Uygulamaya Giriş ve Karşılama
* **Adım:** Kullanıcı `streamlit` arayüzünü açar.
* **Görünüm:** Ankara (Çankaya odaklı) interaktif haritası ve yan paneldeki (sidebar) kontrol menüsü.
* **Eylem:** Kullanıcı harita üzerindeki mevcut konumu ve metro durakları gibi güvenli noktaları inceler.

## 📍 2. Rota Belirleme
* **Adım:** Kullanıcı gitmek istediği hedefi seçer.
* **Eylem:** * Arama çubuğuna varış noktasını yazar veya harita üzerinden işaretler.
    * "En Güvenli Rotayı Hesapla" butonuna tıklar.

## 🧠 3. Güvenlik Analizi ve Hesaplama
* **Arka Plan (Backend):**
    * FastAPI (Port 8001) üzerinden OSMnx verileri çekilir.
    * Algoritma; aydınlatma direkleri, ana yollar ve bilinen güvenli noktaları (metro, karakol vb.) baz alarak ağırlıklandırma yapar.
* **Görünüm:** Ekranda "Güvenli rotanız hesaplanıyor..." uyarısı (spinner) görünür.

## 🤖 4. Sanal Refakatçi (Gemini AI) Devreye Girer
* **Adım:** Rota çizildikten sonra Gemini AI, rotanın neden "güvenli" olduğunu analiz eder.
* **Eylem:** Kullanıcıya şu tarz bir geri bildirim verilir:
    * *"Seçtiğin rota üzerindeki ışıklandırma %80 oranında yeterli ve yol üzerinde 2 adet metro istasyonu bulunuyor. İyi yolculuklar!"*

## 🗺️ 5. Görselleştirme ve Takip
* **Adım:** Harita üzerinde en güvenli yol **koyu yeşil** veya belirgin bir renkle çizilir.
* **Eylem:** Kullanıcı rotayı takip ederek varış noktasına ulaşır.

---

## ⚠️ Hata Durumları (Edge Cases)
* **Bağlantı Hatası:** Eğer Backend (8001 portu) çalışmıyorsa, kullanıcıya "Sunucuya bağlanılamadı" uyarısı gösterilir.
* **Rota Bulunamadı:** Eğer girilen noktalar arasında bir yol bağlantısı yoksa, kullanıcıdan farklı bir nokta seçmesi istenir.
