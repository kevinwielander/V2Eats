import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Dict, Optional
import os
import time
import base64

# Try to import selenium
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

RESTAURANTS = [
    {
        "name": "WU Mensa",
        "url": "https://www.wumensa.at/menueplan-english/",
        "type": "restaurant",
        "use_claude_vision": True
    },
    {
        "name": "DELI Glashaus",
        "url": "https://www.dasglashaus.at/deli",
        "type": "restaurant",
        "default_price": "€7.90",
        "use_claude_vision": False
    },
    {
        "name": "Pinsa Food Truck",
        "url": "https://www.pinsalino.at",
        "type": "food_truck",
        "schedule": {
            "days": ["thursday"]
        },
        "default_price": "€8-12",
        "menu_description": "Roman-style pizza with various toppings"
    },
    {
        "name": "Pasta Food Truck",
        "url": "https://pastaheld.at",
        "type": "food_truck",
        "schedule": {
            "days": ["tuesday"]
        },
        "default_price": "€8-13",
        "menu_description": "Pasta dishes with fresh ingredients"
    },
    {
        "name": "Fat Monk Bowls",
        "url": "https://www.fatmonk.com/files/PressFiles/file/XX-Speisekarte-Online-Print-DE-08-25.pdf",
        "type": "restaurant",
        "default_price": "€9-13",
        "use_claude_vision": False  # Don't scrape, use static menu
    },
    {
        "name": "Topf und Deckel",
        "url": "https://www.topfdeckel.at/",
        "type": "restaurant",
        "use_selenium": True  # JavaScript-rendered site
    }
]

class LLMMenuScraper:
    def __init__(self, llm_provider: str = "claude"):
        self.llm_provider = llm_provider
        self.menus = []
        self.errors = []
        
        # Get API keys with hardcoded fallback
        self.claude_key = os.getenv('ANTHROPIC_API_KEY')
        self.groq_key = os.getenv('GROQ_API_KEY')
        
        if llm_provider == "claude" and not self.claude_key:
            print("⚠️  No Claude API key found. Set ANTHROPIC_API_KEY or CLAUDE_API_KEY")
        if llm_provider == "groq" and not self.groq_key:
            print("⚠️  No Groq API key found. Set GROQ_API_KEY")
    
    def extract_menu_with_claude_vision(self, pdf_url: str, restaurant_name: str, simulate_day: str = None) -> List[Dict]:
        """Use Claude's vision API to read PDF menu"""
        
        if not self.claude_key:
            print("⚠️  No Claude API key found.")
            return []
        
        try:
            # Download the PDF
            print(f"  📄 Downloading PDF...")
            pdf_response = requests.get(pdf_url, timeout=20)
            pdf_response.raise_for_status()
            
            # Convert PDF to base64
            pdf_base64 = base64.b64encode(pdf_response.content).decode('utf-8')
            
            # Get today's day
            if simulate_day:
                today = simulate_day.upper()
                print(f"  🎭 Simulating day: {today}")
            else:
                today = datetime.now().strftime('%A').upper()
            
            print(f"  👁️  Asking Claude Vision to read {today}'s menu from PDF...")
            
            url = "https://api.anthropic.com/v1/messages"
            
            headers = {
                "x-api-key": self.claude_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            
            prompt = f"""You are looking at a weekly restaurant menu PDF for {restaurant_name}.

TODAY IS: {today}

This PDF shows a weekly menu table with 5 columns (Monday through Friday).

YOUR TASK: Extract ONLY the main dish items from the {today} column.

Instructions:
1. Look at the table and identify the {today} column
2. Read down that column to find all MAIN DISH menu items for {today}
3. Extract the dish name, price, and description for each item
4. Do NOT include items from other days (Monday, Tuesday, Wednesday, Friday if today is Thursday)

IMPORTANT - ONLY EXTRACT MAIN DISHES:
- Include: Veggie dishes, Meaty dishes, Pasta dishes, Global & Grill specials
- EXCLUDE: Sides (salads, fries, potatoes by themselves)
- EXCLUDE: Combo deals (e.g., "Deal of the Day", "SMART MENU", "Soup Deal")
- EXCLUDE: Add-ons (e.g., "soup add on", "side salad", "extra topping")
- EXCLUDE: Generic items that appear daily (like "Homemade Pasta" if it's the same every day)

The menu typically has these MAIN DISH sections:
- Powered by plants / Veggie option (€5.60-6.20) - INCLUDE
- Meaty option (€6.20) - INCLUDE
- Global & Grill special (€9.40) - INCLUDE

DO NOT include:
- Soup of the day (it's a side)
- Pasta & Co if it's the same daily item
- Any "Deal" or "Menu" combinations

Return the results as a JSON array in this exact format:
[
  {{"name": "Dish name", "price": "€X.XX", "description": "Brief description"}},
  {{"name": "Another dish", "price": "€X.XX", "description": "Description"}}
]

CRITICAL: Only extract MAIN DISHES from the {today} column. No sides, no combos, no deals.

Return ONLY the JSON array, no other text."""

            payload = {
                "model": "claude-sonnet-4-5",
                "max_tokens": 2000,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": pdf_base64
                                }
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ]
                    }
                ]
            }
            
            response = requests.post(url, headers=headers, json=payload, timeout=90)
            
            if response.status_code != 200:
                error_detail = response.text
                print(f"  ❌ Claude API error {response.status_code}: {error_detail[:300]}")
                return []
            
            result = response.json()
            
            # Extract text from Claude's response
            content_blocks = result.get('content', [])
            generated_text = ""
            for block in content_blocks:
                if block.get('type') == 'text':
                    generated_text += block.get('text', '')
            
            if not generated_text:
                print(f"  ❌ No text in Claude's response")
                return []
            
            return self.parse_llm_response(generated_text)
            
        except Exception as e:
            print(f"  ❌ Claude vision extraction failed: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def translate_menu_items(self, items: List[Dict], restaurant_name: str) -> List[Dict]:
        """Translate menu items to both German and English using LLM and categorize diet type"""
        
        if not items or not self.groq_key:
            return items
        
        try:
            print(f"  🌐 Translating and categorizing menu items...")
            
            # Prepare items for translation
            items_text = json.dumps(items, ensure_ascii=False, indent=2)
            
            prompt = f"""You are translating a restaurant menu for {restaurant_name} and categorizing each dish.

Given menu items in JSON format, add translations AND diet category for each item.

Rules:
1. Detect the original language of each item
2. Translate to the other language (if German → add English, if English → add German)
3. Keep dish names authentic (don't translate proper names like "Erdäpfel Gratin" or "Boeuff Stroganoff")
4. Translate descriptions naturally
5. Keep prices unchanged
6. Categorize each dish as one of: "vegan", "vegetarian", or "meat"
   - "vegan": No animal products at all (no meat, dairy, eggs, honey)
   - "vegetarian": No meat/fish, but may contain dairy, eggs
   - "meat": Contains meat, poultry, or fish

Input menu items:
{items_text}

Return the menu items in this EXACT format:
[
  {{
    "name": {{
      "de": "German name",
      "en": "English name"
    }},
    "price": "€X.XX",
    "description": {{
      "de": "German description",
      "en": "English description"
    }},
    "dietary": "vegan" | "vegetarian" | "meat"
  }}
]

IMPORTANT: Analyze the ingredients carefully:
- "Chicken", "Beef", "Pork", "Fish", "Schnitzel", "Stroganoff" → "meat"
- "Cheese", "Feta", "Mozzarella", "Egg", "Cream" → "vegetarian" (not vegan)
- "Tofu", "Vegetables only", "Vegan Bowl", "Plant-based" → "vegan"

Return ONLY the JSON array, no other text."""

            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.groq_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 3000
                },
                timeout=60
            )
            
            if response.status_code != 200:
                print(f"  ⚠️  Translation failed, keeping original")
                return items
            
            result = response.json()
            translated_text = result['choices'][0]['message']['content']
            
            # Parse the translated items
            translated_items = self.parse_llm_response(translated_text)
            
            if translated_items:
                print(f"  ✓ Translated and categorized {len(translated_items)} items")
                return translated_items
            else:
                print(f"  ⚠️  Translation parsing failed, keeping original")
                return items
                
        except Exception as e:
            print(f"  ⚠️  Translation error: {e}")
            return items
    
    def parse_llm_response(self, text: str) -> List[Dict]:
        """Parse LLM response to extract menu items"""
        try:
            # Remove markdown code blocks if present
            text = text.replace('```json', '').replace('```', '').strip()
            
            # Find JSON array
            start = text.find('[')
            end = text.rfind(']') + 1
            
            if start == -1 or end == 0:
                print(f"  ⚠️  No JSON array found in response")
                return []
            
            json_str = text[start:end]
            items = json.loads(json_str)
            
            return items
            
        except json.JSONDecodeError as e:
            print(f"  ⚠️  Failed to parse JSON: {e}")
            print(f"  Response was: {text[:200]}")
            return []
    
    def clean_html_for_llm(self, soup: BeautifulSoup) -> str:
        """Extract clean text from HTML for LLM processing"""
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
        
        # Get text
        text = soup.get_text()
        
        # Clean up whitespace
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = ' '.join(chunk for chunk in chunks if chunk)
        
        # Preserve important characters
        text = text.replace('\u2013', '-')
        text = text.replace('\u2014', '-')
        text = text.replace('\u2018', "'")
        text = text.replace('\u2019', "'")
        text = text.replace('\u201c', '"')
        text = text.replace('\u201d', '"')
        text = text.replace('\u2022', '-')
        
        # Limit length
        if len(text) > 4000:
            text = text[:4000] + "\n... (content truncated)"
        
        return text
    
    def extract_menu_with_groq(self, website_text: str, restaurant_name: str) -> List[Dict]:
        """Use Groq to extract menu from website text"""
        
        if not self.groq_key:
            return []
        
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            
            headers = {
                "Authorization": f"Bearer {self.groq_key}",
                "Content-Type": "application/json"
            }
            
            prompt = f"""Extract the menu items from this restaurant website for {restaurant_name}.

Website content:
{website_text}

Extract all menu items with their names, prices, and descriptions.

Return as a JSON array:
[
  {{"name": "Dish name", "price": "€X.XX", "description": "Brief description"}},
  {{"name": "Another dish", "price": "€X.XX", "description": "Description"}}
]

Return ONLY the JSON array, no other text."""

            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.1,
                "max_tokens": 2000
            }
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code != 200:
                print(f"  ❌ Groq API error {response.status_code}")
                return []
            
            result = response.json()
            generated_text = result['choices'][0]['message']['content']
            
            return self.parse_llm_response(generated_text)
            
        except Exception as e:
            print(f"  ❌ Groq extraction failed: {e}")
            return []
    
    def scrape_with_selenium(self, url: str) -> str:
        """Scrape JavaScript-rendered page with Selenium"""
        if not SELENIUM_AVAILABLE:
            print("  ⚠️  Selenium not available, falling back to requests")
            return None
        
        try:
            # Setup Chrome options
            chrome_options = Options()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            
            # Initialize driver
            print(f"  🌐 Loading page with Selenium...")
            driver = webdriver.Chrome(options=chrome_options)
            driver.get(url)
            
            # Wait for content to load (adjust timeout as needed)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            # Give extra time for JavaScript to render
            time.sleep(3)
            
            # Get rendered HTML
            html_content = driver.page_source
            driver.quit()
            
            return html_content
            
        except Exception as e:
            print(f"  ⚠️  Selenium failed: {e}")
            try:
                driver.quit()
            except:
                pass
            return None
    
    def scrape_restaurant(self, name: str, url: str, default_price: str = None, use_claude_vision: bool = False, use_selenium: bool = False, simulate_day: str = None, debug: bool = False) -> Optional[Dict]:
        """Scrape a restaurant using LLM"""
        try:
            print(f"📡 Fetching {name}...")
            
            # Use Selenium for JavaScript-rendered sites
            if use_selenium:
                html_content = self.scrape_with_selenium(url)
                if html_content:
                    soup = BeautifulSoup(html_content, 'html.parser')
                    website_text = self.clean_html_for_llm(soup)
                    
                    if debug:
                        print(f"\n{'='*60}")
                        print(f"DEBUG: Selenium extracted text (first 500 chars):")
                        print(website_text[:500])
                        print(f"{'='*60}\n")
                    
                    print(f"🤖 Extracting menu with LLM...")
                    menu_items = self.extract_menu_with_groq(website_text, name)
                    
                    # Apply default price if specified
                    if default_price and menu_items:
                        for item in menu_items:
                            if not item.get('price') or item.get('price') == '':
                                item['price'] = default_price
                    
                    # Filter Topf und Deckel items
                    if name in ["Topf und Deckel"]:
                        menu_items = [
                            item for item in menu_items
                            if item.get('price') and
                            any(price_indicator in item.get('price', '') for price_indicator in ['€7.50', '€8.90', '€11.90', '€10.90']) and
                            not any(keyword in item.get('name', '').lower() for keyword in [
                                'crunchbox', 'base', 'top', 'topping', 'crumble', 'tempeh', 
                                'avocado', 'kimchi', 'halloumi', 'kl.', 'gr.',
                                'kombi', 'tagessuppe', 'gemischter salat', 'schokoladen'
                            ]) and
                            not any(keyword in item.get('description', '').lower() for keyword in [
                                'crunchbox', 'kombination'
                            ]) and
                            item.get('price') not in ['€2.90', '€4.50', '€4.90', '€6.90', '€13.90']
                        ]
                    
                    print(f"✓ Found {len(menu_items)} items for {name}")
                    
                    # Translate menu items to have both languages
                    menu_items = self.translate_menu_items(menu_items, name)
                    
                    return {
                        "name": name,
                        "url": url,
                        "items": menu_items,
                        "scraped_at": datetime.now().isoformat(),
                        "available": True,
                        "extraction_method": "Selenium + LLM (groq)"
                    }
                else:
                    raise Exception("Selenium scraping failed")
            
            # Regular requests for non-JS sites
            response = requests.get(url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            response.raise_for_status()
            
            menu_items = []
            
            if use_claude_vision:
                # Check if the response itself is a PDF
                content_type = response.headers.get('Content-Type', '').lower()
                
                if 'pdf' in content_type or response.content[:4] == b'%PDF':
                    print(f"  📄 Page itself is a PDF!")
                    menu_items = self.extract_menu_with_claude_vision(url, name, simulate_day)
                else:
                    # Look for embedded PDF in HTML
                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    pdf_links = []
                    
                    # Method 1: Look in links, embeds, iframes
                    for link in soup.find_all(['a', 'embed', 'iframe', 'object']):
                        href = link.get('href') or link.get('src') or link.get('data', '')
                        if href and '.pdf' in href.lower():
                            if href.startswith('http'):
                                pdf_links.append(href)
                            elif href.startswith('/'):
                                from urllib.parse import urljoin
                                pdf_links.append(urljoin(url, href))
                    
                    # Method 2: Look in page source for PDF URLs
                    if not pdf_links:
                        import re
                        pdf_pattern = r'(https?://[^\s"\'<>]+\.pdf|/[^\s"\'<>]+\.pdf)'
                        matches = re.findall(pdf_pattern, response.text, re.IGNORECASE)
                        for match in matches:
                            if match.startswith('http'):
                                pdf_links.append(match)
                            elif match.startswith('/'):
                                from urllib.parse import urljoin
                                pdf_links.append(urljoin(url, match))
                    
                    # Method 3: Common PDF paths
                    if not pdf_links:
                        from urllib.parse import urljoin
                        test_urls = [
                            urljoin(url, 'menu.pdf'),
                            urljoin(url, 'menuplan.pdf'),
                            urljoin(url, 'current-menu.pdf'),
                        ]
                        for test_url in test_urls:
                            try:
                                test_resp = requests.head(test_url, timeout=5)
                                if test_resp.status_code == 200 and 'pdf' in test_resp.headers.get('Content-Type', '').lower():
                                    pdf_links.append(test_url)
                                    break
                            except:
                                pass
                    
                    if pdf_links:
                        print(f"  📄 Found PDF: {pdf_links[0]}")
                        menu_items = self.extract_menu_with_claude_vision(pdf_links[0], name, simulate_day)
                    else:
                        print(f"  ⚠️  No PDF found, using HTML extraction with Groq")
                        website_text = self.clean_html_for_llm(soup)
                        menu_items = self.extract_menu_with_groq(website_text, name)
            else:
                # Regular HTML extraction with Groq
                soup = BeautifulSoup(response.content, 'html.parser')
                website_text = self.clean_html_for_llm(soup)
                
                if debug:
                    print(f"\n{'='*60}")
                    print(f"DEBUG: Extracted text (first 500 chars):")
                    print(website_text[:500])
                    print(f"{'='*60}\n")
                
                print(f"🤖 Extracting menu with LLM...")
                menu_items = self.extract_menu_with_groq(website_text, name)
            
            # Apply default price if specified
            if default_price and menu_items:
                for item in menu_items:
                    if not item.get('price') or item.get('price') == '':
                        item['price'] = default_price
            
            # Filter out toppings and invalid items for certain restaurants
            if name in ["Topf und Deckel"]:
                # Only keep main dishes - exclude salads, sides, toppings, combos, desserts
                menu_items = [
                    item for item in menu_items
                    if item.get('price') and
                    # Keep only items with these price ranges (main dishes)
                    any(price_indicator in item.get('price', '') for price_indicator in ['€8.90', '€11.90']) and
                    # Exclude crunchbox items, toppings, combos, desserts
                    not any(keyword in item.get('name', '').lower() for keyword in [
                        'crunchbox', 'base', 'top', 'topping', 'crumble', 'tempeh', 
                        'avocado', 'kimchi', 'halloumi', 'dessert', 'kl.', 'gr.',
                        'kombi', 'suppe', 'grüner salat mit'
                    ]) and
                    not any(keyword in item.get('description', '').lower() for keyword in [
                        'crunchbox', 'kombination'
                    ]) and
                    item.get('price') not in ['€2.90', '€4.50', '€7.90', '€10.90', '€13.90']
                ]
            
            print(f"✓ Found {len(menu_items)} items for {name}")
            
            # Translate menu items to have both languages
            menu_items = self.translate_menu_items(menu_items, name)
            
            # Rate limiting
            time.sleep(2)
            
            return {
                "name": name,
                "url": url,
                "items": menu_items,
                "scraped_at": datetime.now().isoformat(),
                "available": True,
                "extraction_method": f"LLM ({self.llm_provider})"
            }
            
        except Exception as e:
            error_msg = f"Error scraping {name}: {str(e)}"
            print(f"❌ {error_msg}")
            self.errors.append(error_msg)
            
            return {
                "name": name,
                "url": url,
                "items": [],
                "scraped_at": datetime.now().isoformat(),
                "available": False,
                "error": str(e)
            }
    
    def scrape_all(self, restaurants: List[Dict], simulate_day: str = None, debug: bool = False):
        """Scrape all configured restaurants"""
        print(f"\n🚀 Starting V2Eats scraper ({self.llm_provider})...")
        
        if simulate_day:
            print(f"🎭 SIMULATION MODE: Pretending today is {simulate_day.upper()}")
        
        print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        for i, restaurant in enumerate(restaurants):
            print(f"[{i+1}/{len(restaurants)}] Processing {restaurant['name']}...")
            
            if restaurant.get('type') == 'food_truck':
                # Handle food trucks with schedules
                schedule = restaurant.get('schedule', {})
                days = schedule.get('days', [])
                
                # Check if truck is available today
                if simulate_day:
                    current_day = simulate_day.lower()
                else:
                    current_day = datetime.now().strftime('%A').lower()
                
                if current_day in days:
                    print(f"✓ Food truck scheduled for today")
                    
                    static_items = [{
                        "name": restaurant.get('menu_description', 'Food truck special'),
                        "price": restaurant.get('default_price', '€8-12'),
                        "description": f"Available on {', '.join(days)}"
                    }]
                    
                    # Translate food truck items
                    translated_items = self.translate_menu_items(static_items, restaurant['name'])
                    
                    menu = {
                        "name": restaurant['name'],
                        "url": restaurant['url'],
                        "items": translated_items,
                        "scraped_at": datetime.now().isoformat(),
                        "available": True,
                        "type": "food_truck"
                    }
                    self.menus.append(menu)
                    print(f"✓ Found 1 items for {restaurant['name']}")
                else:
                    print(f"⏭️  Skipping - not scheduled for today")
            else:
                # Regular restaurant
                default_price = restaurant.get('default_price')
                use_claude_vision = restaurant.get('use_claude_vision', False)
                
                # Special case: Fat Monk Bowls - use static menu
                if restaurant['name'] == "Fat Monk Bowls":
                    print(f"✓ Using static menu for {restaurant['name']}")
                    static_items = [
                        {"name": "Bowl", "price": "€9-11", "description": "Prebuilt or custom bowls"},
                    ]
                    
                    # Translate static items
                    translated_items = self.translate_menu_items(static_items, restaurant['name'])
                    
                    menu = {
                        "name": restaurant['name'],
                        "url": restaurant['url'],
                        "items": translated_items,
                        "scraped_at": datetime.now().isoformat(),
                        "available": True,
                        "extraction_method": "Static menu"
                    }
                    self.menus.append(menu)
                    print(f"✓ Found 3 items for {restaurant['name']}")
                else:
                    # Regular scraping
                    menu = self.scrape_restaurant(
                        restaurant['name'], 
                        restaurant['url'], 
                        default_price=default_price,
                        use_claude_vision=use_claude_vision,
                        use_selenium=restaurant.get('use_selenium', False),
                        simulate_day=simulate_day,
                        debug=debug
                    )
                    if menu:
                        self.menus.append(menu)
            
            print()
        
        print(f"✅ Scraping complete!")
        print(f"   Restaurants processed: {len(self.menus)}")
        print(f"   Total menu items: {sum(len(m.get('items', [])) for m in self.menus)}")
        if self.errors:
            print(f"   Errors: {len(self.errors)}")
    
    def save_to_json(self, filename: str = "docs/menus.json"):
        """Save scraped menus to JSON file"""
        
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        
        output = {
            "last_updated": datetime.now().isoformat(),
            "llm_provider": self.llm_provider,
            "restaurants": self.menus,
            "errors": self.errors,
            "stats": {
                "total_restaurants": len(self.menus),
                "successful": len([m for m in self.menus if m.get('available', False)]),
                "failed": len([m for m in self.menus if not m.get('available', False)]),
                "total_items": sum(len(m.get('items', [])) for m in self.menus)
            }
        }
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        
        print(f"\n💾 Saved to {filename}")

if __name__ == "__main__":
    import sys
    
    debug_mode = '--debug' in sys.argv
    
    simulate_day = None
    for i, arg in enumerate(sys.argv):
        if arg == '--day' and i + 1 < len(sys.argv):
            simulate_day = sys.argv[i + 1]
            break
    
    scraper = LLMMenuScraper(llm_provider="claude")
    scraper.scrape_all(RESTAURANTS, simulate_day=simulate_day, debug=debug_mode)
    scraper.save_to_json()