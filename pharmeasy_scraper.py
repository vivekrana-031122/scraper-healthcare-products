import os
import sys
import json
import re
import time
import asyncio
import hashlib
import logging
from datetime import datetime, timezone
import random
import urllib.parse
import httpx
import pandas as pd

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scrape_run.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("pharmeasy_scraper")

BRAND_LIST = ["Aptagrow", "Aptamil", "Dexogrow", "Dexolac", "Easum", "Protinex", "Horlicks", "Nestle", "Ensure", "Similac", "Amul"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

CACHE_DIR = "./cache"
os.makedirs(CACHE_DIR, exist_ok=True)

def get_cache_path(url, post_data=None):
    key = url
    if post_data:
        key += "_" + json.dumps(post_data, sort_keys=True)
    h = hashlib.md5(key.encode('utf-8')).hexdigest()
    return os.path.join(CACHE_DIR, f"{h}.json")

def read_cache(url, post_data=None):
    path = get_cache_path(url, post_data)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None

def write_cache(url, data, post_data=None):
    path = get_cache_path(url, post_data)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Failed to write cache: {e}")

async def fetch_with_retry(client, url, method="POST", json_body=None, params=None, retries=3, backoff_factor=2.0):
    cache_data = read_cache(url, json_body or params)
    if cache_data:
        return cache_data

    headers = {
        'user-agent': random.choice(USER_AGENTS),
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'en-US,en;q=0.9',
    }
    
    current_backoff = 1.0
    for attempt in range(1, retries + 2):
        try:
            await asyncio.sleep(random.uniform(0.1, 0.3)) # low jitter for speed
            res = await client.post(url, json=json_body, params=params, headers=headers, timeout=15.0)
            if res.status_code == 200:
                data = res.json()
                write_cache(url, data, json_body or params)
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

def get_brand_category(name, brand):
    name_lower = name.lower()
    if any(x in name_lower for x in ["protein", "whey", "amino", "bcaa", "mass gainer"]):
        return "Sports Nutrition"
    elif any(x in name_lower for x in ["formula", "stage 1", "stage 2", "stage 3", "stage 4", "infant", "baby food", "cerelac", "nangrow", "smulac"]):
        return "Mother and Baby Care"
    elif any(x in name_lower for x in ["diabetic", "diabetes", "sugar free"]):
        return "Diabetes Essentials"
    else:
        return "Health Food and Drinks"

def belongs_to_brand(product, brand):
    brand_lower = brand.lower()
    name_lower = (product.get("name") or "").lower()
    mfg_lower = (product.get("manufacturer") or "").lower()
    
    if brand_lower in name_lower or brand_lower in mfg_lower:
        return True
        
    if brand_lower == "nestle":
        nestle_keywords = ["nan", "lactogen", "cerelac", "nestum", "gerber", "resource", "everyday", "nescafe", "nido", "milo"]
        if any(kw in name_lower for kw in nestle_keywords) or any(kw in mfg_lower for kw in nestle_keywords):
            return True
            
    return False

async def crawl_brand_products(client, brand, semaphore):
    async with semaphore:
        logger.info(f"Retrieving products for brand: {brand}")
        url = "https://pharmeasy.in/api/search/postSearch/"
        page = 1
        brand_products = []
        
        while True:
            params = {"q": brand, "page": page}
            content = await fetch_with_retry(client, url, method="POST", json_body=[], params=params)
            
            if not content:
                break
                
            prods = content.get("data", {}).get("products", [])
            has_more = content.get("hasMorePages", False)
            
            if not prods:
                break
                
            for p in prods:
                brand_products.append(p)
                
            logger.info(f"Page {page} for brand {brand} retrieved {len(prods)} products (Total: {len(brand_products)})")
            
            if not has_more:
                break
            page += 1
            
        return brand, brand_products

async def run_crawler():
    start_time = time.time()
    logger.info("Initializing fast PharmEasy competitor crawler...")
    
    # Discovery semaphore of 8
    discovery_semaphore = asyncio.Semaphore(8)
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        tasks = [crawl_brand_products(client, brand, discovery_semaphore) for brand in BRAND_LIST]
        results = await asyncio.gather(*tasks)
        
        # Gather and de-duplicate by page_url
        rows = []
        seen_urls = set()
        timestamp_str = datetime.now(timezone.utc).isoformat()
        
        total_products_by_brand = {brand: 0 for brand in BRAND_LIST}
        
        for brand, products in results:
            for p in products:
                # Filter out products not belonging to the brand
                if not belongs_to_brand(p, brand):
                    continue
                slug = p.get("slug")
                if not slug:
                    continue
                    
                product_type = p.get("productType")
                deeplink = p.get("deeplink") or ""
                
                # Construct page_url
                if "healthcare_product_details" in deeplink or product_type == 2:
                    page_url = f"https://pharmeasy.in/health-care/products/{slug}"
                else:
                    page_url = f"https://pharmeasy.in/online-medicine-order/{slug}"
                    
                # De-duplicate
                if page_url in seen_urls:
                    continue
                seen_urls.add(page_url)
                
                name = p.get("name") or ""
                mrp = p.get("mrpDecimal") or ""
                price_range = p.get("salePriceDecimal") or ""
                
                # Validate minimum fields
                if not name or not page_url or (not mrp and not price_range):
                    logger.warning(f"Validation failed for slug {slug}: name={name}, url={page_url}, mrp={mrp}, price={price_range}")
                
                subtitle_text = p.get("subtitleText") or p.get("measurementUnit") or ""
                gram, qty = parse_pack_size_and_qty(name, subtitle_text)
                
                # Extract brand
                brand_name = p.get("manufacturer") or brand
                
                # Category
                brand_category = get_brand_category(name, brand_name)
                
                # Flavor (sub_brand)
                sub_brand = ""
                variant_info = p.get("variantInfo") or {}
                if isinstance(variant_info, dict):
                    sub_brand = variant_info.get("Flavour") or ""
                if not sub_brand:
                    flavors = ["chocolate", "vanilla", "mango", "kesar badam", "orange", "cardamom", "strawberry", "kesar", "badam"]
                    for fl in flavors:
                        if fl in name.lower():
                            sub_brand = fl.title()
                            break
                            
                # Availability / status
                is_available = p.get("productAvailabilityFlags", {}).get("isAvailable", True)
                status = "Active" if is_available else "Inactive"
                
                # Image with fallback to damImages and default logo placeholder
                image_url = p.get("image") or ""
                if not image_url:
                    dam_images = p.get("damImages") or []
                    if isinstance(dam_images, list) and len(dam_images) > 0:
                        first_img = dam_images[0]
                        if isinstance(first_img, dict):
                            image_url = first_img.get("url") or ""
                if not image_url:
                    image_url = "https://assets.pharmeasy.in/web-assets/dist/fca22bc9.png"
                            
                # Extract web_pid from page_url
                web_pid = ""
                match = re.search(r'-(\d+)(?:\?|$)', page_url)
                if match:
                    web_pid = match.group(1)
                else:
                    web_pid = str(p.get("productId") or "")
                
                row = {
                    "rb_sku_platform_id": "",
                    "pf_id": "",
                    "platform_name": "PharmEasy",
                    "reseller_id": "",
                    "sku_id": "",
                    "web_pid": web_pid,
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
                    "item_code": str(p.get("productId") or ""),
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
        base_filename = f"pharmeasy_competitor_crawl_{date_str}"
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
        logger.info(f"Total rows written to Excel: {len(rows)}")
        logger.info(f"Elapsed time: {time.time() - start_time:.2f}s")

if __name__ == "__main__":
    asyncio.run(run_crawler())
