import os
import sys
import json
import time
import random
import re
import hashlib
import logging
import asyncio
from datetime import datetime, timezone
import urllib.parse
import urllib.robotparser
import httpx
import pandas as pd

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scrape_1mg_run.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("1mg_scraper")

BRAND_LIST = ["Aptagrow", "Aptamil", "Dexogrow", "Dexolac", "Easum", "Protinex", "Horlicks", "Nestle", "Ensure", "Similac", "Amul"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

CACHE_DIR = "./cache_1mg"
os.makedirs(CACHE_DIR, exist_ok=True)

_ROBOTS_PARSER = None

def get_cache_path(url, params=None):
    key = url
    if params:
        key += "_" + json.dumps(params, sort_keys=True)
    h = hashlib.md5(key.encode('utf-8')).hexdigest()
    return os.path.join(CACHE_DIR, f"{h}.json")

def read_cache(url, params=None):
    path = get_cache_path(url, params)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None

def write_cache(url, data, params=None):
    path = get_cache_path(url, params)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Failed to write cache: {e}")

async def init_robots_parser(client):
    global _ROBOTS_PARSER
    if _ROBOTS_PARSER is not None:
        return
    _ROBOTS_PARSER = urllib.robotparser.RobotFileParser()
    try:
        res = await client.get("https://www.1mg.com/robots.txt", timeout=10.0)
        if res.status_code == 200:
            _ROBOTS_PARSER.parse(res.text.splitlines())
            logger.info("Successfully loaded robots.txt for 1mg.com")
        else:
            _ROBOTS_PARSER.allow_all = True
    except Exception as e:
        logger.warning(f"Failed to fetch/parse robots.txt: {e}")
        _ROBOTS_PARSER.allow_all = True

def is_allowed_by_robots(url, user_agent="*"):
    global _ROBOTS_PARSER
    if _ROBOTS_PARSER is None:
        return True
    return _ROBOTS_PARSER.can_fetch(user_agent, url)

async def fetch_with_retry(client, url, params=None, retries=3, backoff_factor=2.0):
    cache_data = read_cache(url, params)
    if cache_data:
        return cache_data

    # Respect robots.txt compliance
    if not is_allowed_by_robots(url):
        logger.warning(f"URL disallowed by robots.txt: {url}")
        return None

    # Rotating user agent
    user_agent = random.choice(USER_AGENTS)
    headers = {
        "accept": "application/vnd.healthkartplus.v4+json",
        "accept-language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        "hkp-platform": "Healthkartplus-0.0.1-desktopweb",
        "locale": "en",
        "referer": "https://www.1mg.com/",
        "user-agent": user_agent,
        "x-1mglabs-platform": "dWeb",
        "x-access-key": "1mg_client_access_key",
        "x-platform": "desktop-0.0.1",
        "x-city": "Gurgaon",
        "x-visitor-id": "00000000-0000-0000-0000-000000000000_static_0000",
        "visitor-id": "00000000-0000-0000-0000-000000000000_static_0000",
    }
    
    current_backoff = 1.0
    for attempt in range(1, retries + 2):
        try:
            # Low jitter delay (0.1 - 0.3s)
            await asyncio.sleep(random.uniform(0.1, 0.3))
            res = await client.get(url, params=params, headers=headers, timeout=15.0)
            if res.status_code == 200:
                data = res.json()
                write_cache(url, data, params)
                return data
            elif res.status_code in [429, 500, 502, 503, 504]:
                logger.warning(f"HTTP {res.status_code} for {url} (attempt {attempt}/{retries+1})")
            else:
                logger.error(f"HTTP {res.status_code} for {url} (no retry)")
                return None
        except Exception as e:
            logger.warning(f"Request error for {url}: {e} (attempt {attempt}/{retries+1})")
            
        if attempt <= retries:
            sleep_time = current_backoff * backoff_factor + random.uniform(0.1, 0.3)
            await asyncio.sleep(sleep_time)
            current_backoff *= backoff_factor
            
    return None

def parse_pack_size_and_qty(title, subtitle_text):
    gram = ""
    qty = ""
    text_to_search = f"{title} | {subtitle_text or ''}"
    
    pack_match = re.search(r'pack\s+of\s+(\d+)', text_to_search, re.I)
    if pack_match:
        qty = pack_match.group(1)
    else:
        strip_match = re.search(r'(\d+)\s+Tablet\(s\)', text_to_search, re.I)
        if strip_match:
            qty = strip_match.group(1)
            
    weight_match = re.search(r'(\d+(?:\.\d+)?)\s*(g|gm|gms|gram|grams|kg|kgs|kilogram|kilograms|ml|ltr|litre|litres)\b', text_to_search, re.I)
    if weight_match:
        value = weight_match.group(1)
        unit = weight_match.group(2).lower()
        if unit in ['g', 'gm', 'gms', 'gram', 'grams']:
            gram = f"{value} g"
        elif unit in ['kg', 'kgs', 'kilogram', 'kilograms']:
            gram = f"{value} kg"
        elif unit in ['ml']:
            gram = f"{value} ml"
        elif unit in ['ltr', 'litre', 'litres']:
            gram = f"{value} L"
    
    return gram, qty

def get_brand_category(name, brand_name):
    name_lower = str(name).lower()
    if brand_name == "Easum" or any(x in name_lower for x in ["cerelac", "nestum", "cereal"]):
        return "Cereals"
    if any(x in name_lower for x in ["diabetes", "diabetic"]) or \
       any(x in name_lower for x in ["women's plus", "womens plus", "mother's plus", "mothers plus"]):
        return "Health Care"
    if brand_name in ["Aptamil", "Dexolac", "Dexogrow", "Similac"] or \
       (brand_name == "Nestle" and any(x in name_lower for x in ["nan pro", "nan excellapro", "nan lo-lac", "lactogrow", "preterm", "infant formula", "resource", "lactogen", "baby food", "gerber"])):
        return "Baby Care"
    return "Nutrition & Food"

def belongs_to_brand(product, brand):
    brand_lower = brand.lower()
    name_lower = (product.get("name") or "").lower()
    
    if brand_lower in name_lower:
        return True
        
    if brand_lower == "nestle":
        nestle_keywords = ["nan", "lactogen", "cerelac", "nestum", "gerber", "resource", "everyday", "lactogrow"]
        if any(kw in name_lower for kw in nestle_keywords):
            return True
            
    if brand_lower == "similac":
        if "isomil" in name_lower:
            return True
            
    return False

def clean_price(price_str):
    if not price_str:
        return ""
    # Strip currency symbol and spaces
    cleaned = re.sub(r'[^\d.]', '', str(price_str))
    return cleaned.strip()

async def crawl_brand_products(client, brand, semaphore):
    async with semaphore:
        logger.info(f"Retrieving products for brand: {brand}")
        url = "https://www.1mg.com/pwa-dweb-api/api/v4/search/all"
        page = 0
        scroll_id = ""
        brand_products = []
        seen_ids = set()
        
        while True:
            params = {
                "q": brand,
                "city": "Gurgaon",
                "filter": "",
                "page_number": page,
                "scroll_id": scroll_id,
                "per_page": 20,
                "types": "sku,allopathy",
                "sort": "relevance",
                "fetch_eta": "true",
                "is_city_serviceable": "true"
            }
            content = await fetch_with_retry(client, url, params=params)
            
            if not content:
                break
                
            prods = content.get("data", {}).get("search_results", [])
            next_scroll_id = content.get("data", {}).get("scroll_id", "")
            
            if not prods:
                break
                
            new_count = 0
            for p in prods:
                pid = p.get("id")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    brand_products.append(p)
                    new_count += 1
                    
            logger.info(f"Page {page} for brand {brand} retrieved {len(prods)} products (New: {new_count}, Total Unique: {len(brand_products)})")
            
            if new_count == 0:
                logger.info(f"No new products found on Page {page} for brand {brand}. Terminating search.")
                break
                
            if not next_scroll_id:
                logger.info(f"No next scroll ID returned on Page {page} for brand {brand}. Terminating search.")
                break
                
            scroll_id = next_scroll_id
            page += 1
            await asyncio.sleep(random.uniform(0.1, 0.2))
            
        return brand, brand_products

async def run_crawler(smoke_test=False):
    start_time = time.time()
    logger.info("Initializing fast 1mg competitor crawler...")
    
    # Discovery semaphore of 8
    discovery_semaphore = asyncio.Semaphore(8)
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # Initialize robots.txt compliance parser
        await init_robots_parser(client)
        
        brands_to_run = ["Protinex"] if smoke_test else BRAND_LIST
        logger.info(f"Running crawl for brands: {brands_to_run}")
        
        tasks = [crawl_brand_products(client, brand, discovery_semaphore) for brand in brands_to_run]
        results = await asyncio.gather(*tasks)
        
        rows = []
        seen_urls = set()
        timestamp_str = datetime.now(timezone.utc).isoformat()
        
        total_products_by_brand = {brand: 0 for brand in brands_to_run}
        
        for brand, products in results:
            for p in products:
                # Enforce brand-matching filter
                if not belongs_to_brand(p, brand):
                    continue
                    
                relative_url = p.get("url") or ""
                if not relative_url:
                    continue
                    
                page_url = f"https://www.1mg.com{relative_url}"
                
                # De-duplicate globally by page_url
                if page_url in seen_urls:
                    continue
                seen_urls.add(page_url)
                
                name = p.get("name") or ""
                prices_dict = p.get("prices") or {}
                mrp_raw = prices_dict.get("mrp") or ""
                price_raw = prices_dict.get("discounted_price") or ""
                
                mrp = clean_price(mrp_raw)
                price_range = clean_price(price_raw)
                
                # Validate minimum fields
                if not name or not page_url or (not mrp and not price_range):
                    logger.warning(f"Validation failed for SKU: name={name}, url={page_url}, mrp={mrp}, price={price_range}")
                    
                subtitle_text = p.get("label") or ""
                gram, qty = parse_pack_size_and_qty(name, subtitle_text)
                
                brand_name = brand
                brand_category = get_brand_category(name, brand_name)
                
                # Flavor (sub_brand)
                sub_brand = ""
                flavors = ["chocolate", "vanilla", "mango", "kesar badam", "orange", "cardamom", "strawberry", "kesar", "badam"]
                for fl in flavors:
                    if fl in name.lower():
                        sub_brand = fl.title()
                        break
                        
                # Availability / status
                is_available = p.get("available", True)
                status = "Active" if is_available else "Inactive"
                
                # Image
                image_url = p.get("image") or ""
                
                row = {
                    "rb_sku_platform_id": "",
                    "pf_id": "",
                    "platform_name": "1mg",
                    "reseller_id": "",
                    "sku_id": "",
                    "web_pid": str(p.get("id") or ""),
                    "group_id": "",
                    "brand_id": "",
                    "brand_category_id": "",
                    "msl": "",
                    "cluster": "",
                    "ean_code": "",
                    "rb_code": "",
                    "pantry_code": "",
                    "created_by": "scraper_bot",
                    "created_on": timestamp_str,
                    "modified_by": "scraper_bot",
                    "modified_on": timestamp_str,
                    "status": status,
                    "page_url": page_url,
                    "sku_name": name,
                    "is_competitor": True,
                    "sku_title": name,
                    "comp_mapp": "",
                    "brand_name": brand_name,
                    "brand_category": brand_category,
                    "item_code": str(p.get("id") or ""),
                    "sub_brand": sub_brand,
                    "mrp": mrp,
                    "gram": gram,
                    "price_range": price_range,
                    "qty": qty,
                    "image_url": image_url,
                    "guardrail": "",
                    "best_seller_category": "",
                    "best_seller_category_id": "",
                    "platform_uuid": "",
                    "platform_account_id": "",
                    "company_id": ""
                }
                rows.append(row)
                total_products_by_brand[brand] += 1
                
        # Write to Excel
        date_str = datetime.now().strftime("%Y%m%d")
        base_filename = "1mg_competitor_crawl_test" if smoke_test else f"1mg_competitor_crawl_{date_str}"
        filename = f"{base_filename}.xlsx"
        
        cols = [
            "rb_sku_platform_id", "pf_id", "platform_name", "reseller_id", "sku_id", "web_pid", "group_id", 
            "brand_id", "brand_category_id", "msl", "cluster", "ean_code", "rb_code", "pantry_code", 
            "created_by", "created_on", "modified_by", "modified_on", "status", "page_url", "sku_name", 
            "is_competitor", "sku_title", "comp_mapp", "brand_name", "brand_category", "item_code", 
            "sub_brand", "mrp", "gram", "price_range", "qty", "image_url", "guardrail", 
            "best_seller_category", "best_seller_category_id", "platform_uuid", "platform_account_id", "company_id"
        ]
        
        df = pd.DataFrame(rows, columns=cols)
        
        counter = 1
        while True:
            try:
                with pd.ExcelWriter(filename, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name="Competitor Crawl")
                    worksheet = writer.sheets["Competitor Crawl"]
                    worksheet.freeze_panes = "A2"
                    
                    for col in worksheet.columns:
                        max_len = max(len(str(cell.value or '')) for cell in col)
                        col_letter = col[0].column_letter
                        worksheet.column_dimensions[col_letter].width = min(max(max_len + 3, 10), 50)
                logger.info(f"Excel file created successfully: {filename}")
                break
            except PermissionError:
                filename = f"{base_filename}_{counter}.xlsx"
                logger.warning(f"Target file is locked/open. Retrying save as: {filename}")
                counter += 1
                
        # Print summary
        logger.info("\n" + "="*40 + "\nFAST CRAWL SUMMARY\n" + "="*40)
        for brand, count in total_products_by_brand.items():
            logger.info(f"  Brand '{brand}': {count} products found")
        logger.info(f"Total rows written to Excel: {len(df)}")
        logger.info(f"Elapsed time: {time.time() - start_time:.2f}s")
        
        # Print sample rows for smoke test verification
        if smoke_test and not df.empty:
            print("\n" + "="*40 + "\nSMOKE TEST SAMPLE ROWS (First 5)\n" + "="*40)
            sample_df = df[["web_pid", "sku_name", "brand_name", "mrp", "price_range", "gram", "qty", "status", "image_url"]].head(5)
            print(sample_df.to_string(index=False))

if __name__ == "__main__":
    smoke = "--smoke" in sys.argv
    asyncio.run(run_crawler(smoke_test=smoke))
