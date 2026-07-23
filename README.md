# Healthcare Medicine Catalog Scraper Suite

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tech: HTTPX](https://img.shields.io/badge/Tech-HTTPX-brightgreen.svg)](#)

> [!NOTE]
> This scraper is fully functional but not scheduled to run automatically. Run manually with `python one_mg_scraper.py`.


Consolidated suite of asynchronous scrapers targeting medical product data, catalogs, and pricing from Tata 1mg, Apollo Pharmacy, and PharmEasy.

---

## 🚀 Features

* Asynchronous data harvesting using HTTPX
* Complies with robots.txt rules using urllib.robotparser check validations
* Rotates request user-agents and introduces low jitter delays to minimize rate blocks
* Parses complex pack sizes, brand names, unit prices, and stock indicators
* Maintains JSON cache directories for resume-safe execution paths

---

## 🛠️ Tech Stack & Libraries
* **Language:** Python 3.8+
* **Libraries:** HTTPX, Asyncio, pandas, BeautifulSoup4, Python

---

## 📦 Installation & Setup

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/vivekrana-031122/scraper-healthcare-products.git
   cd scraper-healthcare-products
   ```

2. **Create and Activate a Virtual Environment:**
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Additional Setup (if applicable):**
   * If using Playwright:
     ```bash
     playwright install chromium
     ```

---

## 💻 Usage Example

Run the main scraper entry point:
```bash
python one_mg_scraper.py  # Or apollo_scraper.py, pharmeasy_scraper.py
```

---

## 🛡️ Disclaimer & Robots.txt Compliance

This project is created for educational and professional demonstration purposes. By using this tool, you agree to:
* Respect the target website's `robots.txt` directives.
* Avoid making aggressive requests that could disrupt target servers (configure appropriate sleep intervals/throttling).
* Comply with local web data protection regulations and the platform's terms of service.
