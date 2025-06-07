import time
import re
import os
import json
import random
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- AYARLAR ---
MAX_WORKERS = 2
REQUEST_TIMEOUT = 30
MIN_DELAY_BETWEEN_URLS = 1.0
MAX_DELAY_BETWEEN_URLS = 3.0

REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9,tr-TR;q=0.8,tr;q=0.7',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
}

# ==============================================================================
# --- FONKSİYON TANIMLAMALARI (TEK VE DOĞRU YERDE) ---
# ==============================================================================

def create_session_with_retries():
    """
    HTTP 429 (Too Many Requests) gibi geçici hatalarda otomatik olarak bekleyip
    yeniden deneme yapan bir 'requests.Session' nesnesi oluşturur.
    """
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        status_forcelist=[429, 500, 502, 503, 504],
        backoff_factor=1,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(REQUEST_HEADERS)
    return session

def get_element_text_bs4(soup, spec_info, default_value="Bilgi Yok"):
    """BeautifulSoup nesnesi üzerinden CSS seçicileri veya özel mantıkla veri çeker."""
    selector = spec_info["value"]
    try:
        element = None
        if "find_sibling_after_text" not in spec_info:
            element = soup.select_one(selector)
        else:
            find_text = spec_info["find_sibling_after_text"]
            # 'a' etiketi yerine herhangi bir etiketi bulmak için genel bir arama yapıldı
            anchor = soup.find(lambda tag: tag.name == 'td' and find_text.lower() in tag.get_text(strip=True).lower())
            if anchor:
                sibling_td = anchor.find_next_sibling('td', class_='nfo')
                if sibling_td:
                    element = sibling_td

        if not element: return default_value
        if spec_info.get("attribute"):
            attr_content = element.get(spec_info["attribute"])
            return attr_content.strip() if attr_content else default_value
        if spec_info.get("process_as_html"):
            return element.get_text(separator='\n', strip=True) or default_value
        return element.get_text(strip=True) or default_value
    except Exception as e:
        print(f"Uyarı: get_element_text_bs4'te '{spec_info['label']}' için hata: {e}")
        return default_value

def fetch_review_text_bs4(session, base_url):
    """Verilen session'ı kullanarak tüm inceleme sayfalarını gezer ve metni toplar."""
    print("-> Ham inceleme metni çekiliyor...")
    try:
        main_page_response = session.get(base_url, timeout=REQUEST_TIMEOUT)
        main_page_response.raise_for_status()
        soup = BeautifulSoup(main_page_response.content, 'html.parser')
        review_link_element = soup.select_one("li.article-info-meta-link-review a[href]")
        if not review_link_element:
            print("-> İnceleme (Review) linki bulunamadı.")
            return "İnceleme Metni Yok (Review butonu bulunamadı)"
    except requests.RequestException as e:
        print(f"Hata: Ana sayfa ({base_url}) açılamadı: {e}")
        return f"İnceleme Metni Yok (Ana sayfa açılamadı: {e})"

    review_url = urljoin(base_url, review_link_element['href'])
    all_review_texts = []
    MAX_REVIEW_PAGES = 15
    for page_num in range(1, MAX_REVIEW_PAGES + 1):
        if not review_url: break
        print(f"  - İnceleme sayfası {page_num} işleniyor...")
        try:
            response = session.get(review_url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            review_soup = BeautifulSoup(response.content, 'html.parser')
            review_body = review_soup.select_one("#review-body")
            if not review_body: break
            paragraphs = review_body.find_all('p', string=True)
            page_text = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]
            all_review_texts.extend(page_text)
            next_page_link = review_soup.select_one("a.pages-next:not(.disabled)[href]")
            review_url = urljoin(base_url, next_page_link['href']) if next_page_link else None
        except requests.RequestException as e:
            print(f"Hata: İnceleme sayfası {page_num} çekilirken: {e}")
            break
    if not all_review_texts: return "İnceleme Metni Bulunamadı (Sayfalar gezildi ama paragraf çekilemedi)"
    return "\n\n".join(all_review_texts)

def fetch_phone_data_bs4(session, url, specs_definitions_list):
    """Tek bir URL için tüm verileri çeker."""
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        print("-> Telefon özellikleri çekiliyor...")
        specs_data_dict = {}
        for spec_def in specs_definitions_list:
            specs_data_dict[spec_def["label"]] = get_element_text_bs4(soup, spec_def, default_value=spec_def.get("default_value", "Bilgi Yok"))

        raw_review_content = fetch_review_text_bs4(session, url)
        review_status_text = "Review Var"
        if "İnceleme Metni Yok" in raw_review_content or "İnceleme Metni Bulunamadı" in raw_review_content:
            review_status_text = "Review Yok"

        processed_review_content = "Gemini API HTTP" # Placeholder
        return specs_data_dict, review_status_text, processed_review_content, raw_review_content
    except requests.exceptions.RequestException as e:
        print(f"Hata: Ağ/Bağlantı hatası ({url}): {e}")
        error_status = f"Ağ Hatası ({e})"
    except Exception as e:
        print(f"Hata: fetch_phone_data_bs4 içinde genel hata oluştu ({url}): {e}")
        error_status = f"Genel Veri Çekme Hatası ({e})"

    initial_specs = {spec_def["label"]: "Veri Çekme Başarısız" for spec_def in specs_definitions_list}
    return initial_specs, error_status, "Hata oluştu", "Hata oluştu"

def save_data_to_php(phone_data_dict, php_url):
    """Veri setini JSON olarak PHP servisine POST eder."""
    try:
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        json_payload = json.dumps(phone_data_dict, ensure_ascii=False).encode('utf-8')
        response = requests.post(php_url, data=json_payload, headers=headers, timeout=90)
        response.raise_for_status()
        response_json = response.json()
        if response_json.get("status") == "success":
            print(f"-> Veritabanı işlemi başarılı: {phone_data_dict.get('model_adi')}")
            return True
        else:
            print(f"Hata: Veritabanı sunucu mesajı: {response_json.get('message', 'Bilinmeyen PHP hatası')}")
            return False
    except json.JSONDecodeError:
        print(f"Hata: PHP yanıtı JSON formatında değil! Yanıt: {response.text[:500]}")
    except requests.exceptions.RequestException as e:
        print(f"Hata: PHP servisine bağlanılamadı: {e}")
    return False

def process_url_wrapper(url, specs_definitions, php_save_url):
    """Her bir URL için tüm iş akışını yöneten fonksiyon."""
    print(f"\n--- İŞLEM BAŞLIYOR: {url} ---")
    delay = random.uniform(10, 15)
    print(f"{delay:.1f} SN bekletiliyor.")
    time.sleep(delay)
    
    current_url = url.strip()
    if not current_url.startswith(('http://', 'https://')):
        current_url = 'https://' + current_url

    session = create_session_with_retries()
    try:
        specs_dict, review_status, gemini_review, raw_review = fetch_phone_data_bs4(session, current_url, specs_definitions)
        model_adi = specs_dict.get("Model Adı", "Bilinmeyen Model")
        if "Veri Çekme Başarısız" in model_adi or not model_adi or model_adi == "Model Adı Yok":
            print(f"Kritik hata: Model adı çekilemedi. İşlem sonlandırıldı. ({current_url})")
            return False, current_url

        marka = model_adi.split(' ')[0] if "Bilinmeyen Model" not in model_adi else "Marka Yok"
        data_to_save = {
            "url": current_url, "model_adi": model_adi, "marka": marka,
            "resim_url": specs_dict.get("Resim URL", "Resim Yok"),
            "review_status": review_status,
            "processed_review_content": gemini_review,
            "raw_review_content": raw_review,
            "specs": [{"label": label, "value": value} for label, value in specs_dict.items() if label not in ["Model Adı", "Resim URL"]]
        }
        if save_data_to_php(data_to_save, php_save_url):
            return True, current_url
        else:
            return False, current_url
    except Exception as e:
        print(f"!!! process_url_wrapper içinde beklenmedik ana hata ({current_url}): {e}")
        return False, current_url
    finally:
        session.close()

# ==============================================================================
# --- ANA ÇALIŞTIRMA BLOĞU ---
# ==============================================================================
if __name__ == "__main__":
    # 1. PHP URL'sini ortam değişkeninden oku (GitHub Secrets için)
    PHP_SAVE_URL = os.environ.get("PHP_SAVE_URL")
    if not PHP_SAVE_URL:
        print("Kritik Hata: 'PHP_SAVE_URL' ortam değişkeni bulunamadı. Program durduruluyor.")
        exit(1)

    # 2. Dosya yollarını göreceli olarak ayarla
    base_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(base_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    url_list_file_path = os.path.join(base_dir, "linkler.txt")
    failed_urls_filename = os.path.join(output_dir, "hatali_GSMArena_linkleri.txt")
    processed_log_filename = os.path.join(output_dir, "islenen_GSMArena_linkleri_log.txt")

    # 3. Spec tanımlamaları listesi (TAM HALİ)
    phone_specs_definitions_bs4 = [
        {"label": "Model Adı", "value": "h1.specs-phone-name-title[data-spec='modelname']", "default_value": "Model Adı Yok"},
        {"label": "Resim URL", "value": "div.specs-photo-main > a > img", "attribute": "src", "default_value": "Resim Yok"},
        {"label": "Network Teknolojisi", "value": "[data-spec='nettech']"},
        {"label": "Duyurulma Tarihi", "value": "[data-spec='year']"},
        {"label": "Piyasaya Çıkış Durumu", "value": "[data-spec='status']"},
        {"label": "Boyutlar", "value": "[data-spec='dimensions']"},
        {"label": "Ağırlık", "value": "[data-spec='weight']"},
        {"label": "Gövde Malzemesi", "value": "[data-spec='build']"},
        {"label": "Sim", "value": "[data-spec='sim']", "process_as_html": True},
        {"label": "Gövde Diğer (IP vb.)", "value": "[data-spec='bodyother']", "process_as_html": True},
        {"label": "Ekran Tipi", "value": "a[href*='glossary.php3?term=display-type'] ~ .nfo"},
        {"label": "Ekran Boyutu", "value": "[data-spec='displaysize']"},
        {"label": "Ekran Çözünürlüğü", "value": "[data-spec='displayresolution']"},
        {"label": "Ekran Koruması", "value": "[data-spec='displayprotection']"},
        {"label": "Ekran Diğer Özellikler", "value": "[data-spec='displayother']", "process_as_html": True},
        {"label": "İşletim Sistemi", "value": "[data-spec='os']"},
        {"label": "Yonga Seti", "value": "[data-spec='chipset']"},
        {"label": "CPU", "value": "[data-spec='cpu']"},
        {"label": "GPU", "value": "[data-spec='gpu']"},
        {"label": "Hafıza Kartı Yuvası", "value": "[data-spec='memoryslot']"},
        {"label": "Dahili Hafıza", "value": "[data-spec='internalmemory']", "process_as_html": True},
        {"label": "Ana Kamera Modülleri", "value": "[data-spec='cam1modules']", "process_as_html": True},
        {"label": "Ana Kamera Özellikleri", "value": "[data-spec='cam1features']"},
        {"label": "Ana Kamera Video", "value": "[data-spec='cam1video']", "process_as_html": True},
        {"label": "Ön Kamera Modülleri", "value": "[data-spec='cam2modules']", "process_as_html": True},
        {"label": "Ön Kamera Özellikleri", "value": "[data-spec='cam2features']"},
        {"label": "Ön Kamera Video", "value": "[data-spec='cam2video']", "process_as_html": True},
        {"label": "Hoparlör", "find_sibling_after_text": "Loudspeaker", "value": ""},
        {"label": "3.5mm Jack", "find_sibling_after_text": "3.5mm jack", "value": ""},
        {"label": "Ses Diğer Özellikler", "value": "[data-spec='optionalother']", "process_as_html": True},
        {"label": "WLAN", "value": "[data-spec='wlan']"},
        {"label": "Bluetooth", "value": "[data-spec='bluetooth']"},
        {"label": "Konumlandırma (GPS)", "value": "[data-spec='gps']", "process_as_html": True},
        {"label": "NFC", "value": "[data-spec='nfc']"},
        {"label": "Kızılötesi Portu", "find_sibling_after_text": "Infrared port", "value": ""},
        {"label": "Radyo", "value": "[data-spec='radio']"},
        {"label": "USB", "value": "[data-spec='usb']"},
        {"label": "Sensörler", "value": "[data-spec='sensors']", "process_as_html": True},
        {"label": "Batarya Tipi", "value": "[data-spec='batdescription1']", "process_as_html": True},
        {"label": "Şarj Özellikleri", "find_sibling_after_text": "Charging", "value": ""},
        {"label": "Batarya Diğer (Wireless, Reverse)", "value": "[data-spec='battstandby']", "process_as_html": True},
        {"label": "Renkler", "value": "[data-spec='colors']"},
        {"label": "Model Varyantları", "value": "[data-spec='models']", "process_as_html": True},
        {"label": "SAR (AB)", "value": "[data-spec='sar-eu']"},
        {"label": "SAR (ABD)", "value": "[data-spec='sar-us']"},
        {"label": "Fiyat", "value": "[data-spec='price']"},
        {"label": "Performans Testleri", "value": "[data-spec='tbench']", "process_as_html": True},
        {"label": "Testler > Ekran", "find_sibling_after_text": "Display", "value": ""},
        {"label": "Testler > Kamera", "value": "a[href*='piccmp.php3']"},
        {"label": "Testler > Hoparlör", "value": "a[href*='review.php3?sReview=studio']"},
        {"label": "Testler > Batarya Ömrü", "value": "[data-spec='batlife']"},
        {"label": "Testler > Batarya Aktif Kullanım Skoru", "value": "[data-spec='batlife2'] a"},
    ]

    try:
        with open(url_list_file_path, 'r', encoding='utf-8') as f:
            urls_to_process = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    except FileNotFoundError:
        print(f"Hata: '{url_list_file_path}' dosyası bulunamadı. İşlem durduruluyor.")
        exit(1)

    if not urls_to_process:
        print(f"'{url_list_file_path}' dosyasında işlenecek URL bulunamadı.")
        exit(0)

    total_urls = len(urls_to_process)
    print(f"\nToplam {total_urls} URL, {MAX_WORKERS} iş parçacığı ile işlenecek.")

    failed_urls = []
    successful_urls = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {executor.submit(process_url_wrapper, url, phone_specs_definitions_bs4, PHP_SAVE_URL): url for url in urls_to_process}
        for i, future in enumerate(as_completed(future_to_url)):
            original_url = future_to_url[future]
            print(f"\n--- [{i + 1}/{total_urls}] TAMAMLANDI: {original_url} ---")
            try:
                success, processed_url = future.result()
                if success:
                    print(f"SONUÇ: BAŞARILI -> {processed_url}")
                    successful_urls.append(processed_url)
                else:
                    print(f"SONUÇ: BAŞARISIZ -> {processed_url}")
                    failed_urls.append(processed_url)
            except Exception as e:
                print(f"SONUÇ: KRİTİK HATA -> URL {original_url} işlenirken BEKLENMEDİK HATA: {e}")
                failed_urls.append(original_url)

    print("\n" + "="*50)
    print("--- TÜM İŞLEMLER BİTTİ, SONUÇLAR DOSYALARA YAZILIYOR ---")
    print("="*50)

    try:
        with open(url_list_file_path, 'w', encoding='utf-8') as f:
            if failed_urls:
                print(f"'{url_list_file_path}' dosyası güncelleniyor, {len(failed_urls)} başarısız URL bırakılıyor.")
                f.write("\n".join(failed_urls) + "\n")
            else:
                print(f"Tüm URL'ler başarıyla işlendi. '{url_list_file_path}' boşaltılıyor.")
                f.write("")
    except Exception as e:
        print(f"Hata: '{url_list_file_path}' dosyası güncellenirken hata: {e}")

    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    if failed_urls:
        print(f"Başarısız {len(failed_urls)} URL '{failed_urls_filename}' dosyasına ekleniyor.")
        try:
            with open(failed_urls_filename, 'a', encoding='utf-8') as f_failed:
                f_failed.write(f"\n--- Hata Oluşan Linkler ({timestamp}) ---\n" + "\n".join(failed_urls) + "\n")
        except Exception as e:
            print(f"Hata: '{failed_urls_filename}' dosyasına yazılırken hata: {e}")

    if successful_urls:
        print(f"Başarıyla işlenen {len(successful_urls)} URL '{processed_log_filename}' dosyasına loglanıyor.")
        try:
            with open(processed_log_filename, 'a', encoding='utf-8') as f_log:
                f_log.write(f"\n--- Başarıyla İşlenen Linkler ({timestamp}) ---\n" + "\n".join(successful_urls) + "\n")
        except Exception as e:
            print(f"Hata: '{processed_log_filename}' dosyasına yazılırken hata: {e}")

    print("\n--- DOSYA İŞLEMLERİ TAMAMLANDI ---")
    print(f"Başarılı URL Sayısı: {len(successful_urls)}")
    print(f"Başarısız URL Sayısı: {len(failed_urls)}")
