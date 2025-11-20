import asyncio
import json
import csv
import re
from dataclasses import dataclass, asdict
from typing import List, Optional
from urllib.parse import quote_plus

from playwright.async_api import async_playwright, Page, BrowserContext

# ================= CONFIG =================
BASE_URL = "https://www.myagedcare.gov.au/find-a-provider/search/results"
SEARCH_QUERY = "Sydney NSW 2000"  # Change this as needed
SEARCH_TYPE = "aged-care-homes"
DISTANCE = "10"  # Distance in km - set to empty string "" for no distance filter
LINK_PER_SEARCH = 2  # Set to None or 0 to scrape all links, or set a specific number
HEADLESS = False
SLOW_MO_MS = 300
NAV_RETRIES = 3
# ==========================================

@dataclass
class ProviderData:
    company_name: str
    address: str
    suburb: str
    state: str
    postcode: str
    telephone: str
    email: str
    website: str
    result_url: str

class MyAgedCareScraper:
    def __init__(self):
        self.results = []
    
    async def setup_browser(self):
        """Stealth browser setup with all anti-detection techniques"""
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=HEADLESS,
            slow_mo=SLOW_MO_MS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-http2",
                "--start-maximized",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=VizDisplayCompositor"
            ]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1400, "height": 900},
            locale="en-AU",
            timezone_id="Australia/Sydney",
            java_script_enabled=True,
        )
        
        # Add stealth scripts to avoid detection
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
        """)
        
        return playwright, browser, context

    async def handle_popup(self, page: Page):
        """Handle the 'Got it' popup that appears on the results page"""
        popup_selectors = [
            "button:has-text('Got it')",
            "button:has-text('GOT IT')",
            "[aria-label*='Got it']",
            ".popover button:has-text('Got it')",
            "button >> text=Got it"
        ]
        
        for selector in popup_selectors:
            try:
                popup_button = page.locator(selector)
                if await popup_button.count() > 0 and await popup_button.is_visible():
                    print("Found 'Got it' popup - closing it...")
                    await popup_button.click()
                    await page.wait_for_timeout(1500)
                    print("Popup closed successfully")
                    return True
            except Exception as e:
                continue
        
        return False

    def construct_search_url(self):
        """Construct the exact search URL with proper parameters including distance"""
        base_url = f"{BASE_URL}?searchType={SEARCH_TYPE}&location={quote_plus(SEARCH_QUERY.upper())}&sort=relevance"
        
        if DISTANCE and DISTANCE.strip():
            base_url += f"&distance={DISTANCE}"
            
        return base_url

    async def goto_with_retry(self, page: Page, url: str):
        """Retry navigation with exponential backoff"""
        for attempt in range(NAV_RETRIES):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2000)
                
                # Handle any popups that appear
                await self.handle_popup(page)
                
                return True
                    
            except Exception as e:
                print(f"Navigation attempt {attempt + 1} failed: {str(e)}")
                if attempt == NAV_RETRIES - 1:
                    raise e
                await page.wait_for_timeout(1000 * (attempt + 1))
        return False

    async def wait_for_results(self, page: Page):
        """Flexible waiting for results with multiple detection strategies"""
        await self.handle_popup(page)
        
        selectors = [
            "article",
            "li[role='article']",
            "[data-testid*='result']",
            "[class*='result']",
            "[class*='card']",
            "h2",
        ]
        
        for attempt in range(30):
            await self.handle_popup(page)
            
            for selector in selectors:
                try:
                    locator = page.locator(selector)
                    count = await locator.count()
                    if count > 0:
                        if count >= 1 and await locator.first.is_visible():
                            print(f"Found {count} result elements")
                            return True
                except Exception as e:
                    continue
            await page.wait_for_timeout(500)
        return False

    async def extract_all_links_from_cards(self, page: Page) -> List[str]:
        """Extract all provider detail links from result cards"""
        print("🔍 Extracting links from result cards...")
        
        links = []
        
        # First, let's find all the card containers
        card_selectors = [
            "div.flex.w-full.content-center.bg-neutral-00",  # The main card container from your HTML
            "article",
            "li[role='article']",
            "[class*='card']",
            "div[class*='flex'][class*='w-full'][class*='bg-neutral-00']"
        ]
        
        for card_selector in card_selectors:
            try:
                cards = page.locator(card_selector)
                card_count = await cards.count()
                print(f"Found {card_count} cards with selector: {card_selector}")
                
                if card_count > 0:
                    for i in range(card_count):
                        try:
                            card = cards.nth(i)
                            
                            # Look for links within each card - multiple strategies
                            link_selectors = [
                                "a[href*='/find-a-provider/']",
                                "a[href*='search/']",
                                "a:has-text('Show details')",
                                "h3 a",  # The company name link
                                "a"  # Any link as fallback
                            ]
                            
                            for link_selector in link_selectors:
                                try:
                                    link_elements = card.locator(link_selector)
                                    link_count = await link_elements.count()
                                    
                                    if link_count > 0:
                                        for j in range(link_count):
                                            try:
                                                href = await link_elements.nth(j).get_attribute("href")
                                                if href:
                                                    # Convert to full URL if relative
                                                    full_url = href if href.startswith('http') else f"https://www.myagedcare.gov.au{href}"
                                                    
                                                    # Filter for actual provider detail pages
                                                    if '/find-a-provider/' in full_url and full_url not in links:
                                                        links.append(full_url)
                                                        print(f"  📎 Link {len(links)}: {full_url}")
                                                        break  # Found a link for this card, move to next card
                                            except:
                                                continue
                                            
                                    if links and len(links) > i:  # If we found a link for this card
                                        break
                                        
                                except:
                                    continue
                                    
                        except Exception as e:
                            print(f"Error processing card {i}: {e}")
                            continue
                            
            except Exception as e:
                continue
        
        # If we still haven't found links, try a broader search
        if not links:
            print("Trying broader link search...")
            all_links = page.locator("a[href*='/find-a-provider/']")
            link_count = await all_links.count()
            print(f"Found {link_count} provider links on page")
            
            for i in range(link_count):
                try:
                    href = await all_links.nth(i).get_attribute("href")
                    if href:
                        full_url = href if href.startswith('http') else f"https://www.myagedcare.gov.au{href}"
                        if full_url not in links:
                            links.append(full_url)
                            print(f"  📎 Link {len(links)}: {full_url}")
                except:
                    continue
        
        # Apply LINK_PER_SEARCH limit
        if LINK_PER_SEARCH and LINK_PER_SEARCH > 0:
            original_count = len(links)
            links = links[:LINK_PER_SEARCH]
            print(f"🔢 Limited links from {original_count} to {len(links)} based on LINK_PER_SEARCH setting")
        
        print(f"✅ Total links extracted: {len(links)}")
        return links

    async def scrape_detail_page(self, page: Page, url: str) -> Optional[ProviderData]:
        """Scrape all text and parse specific fields from detail page"""
        print(f"🌐 Navigating to: {url}")
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3000)
            
            # Check if page is valid (not 404)
            page_title = await page.title()
            if "Sorry, we can't find" in page_title or "404" in page_title or "Page not found" in await page.content():
                print(f"❌ Broken link (404 page): {url}")
                return None
            
            # Extract ALL text from the page
            raw_text = await self.extract_all_text_from_page(page)
            
            # Check if we got meaningful content
            if len(raw_text.strip()) < 100 or "Sorry, we can't find" in raw_text:
                print(f"❌ Page has no meaningful content: {url}")
                return None
            
            # Parse specific fields from the raw text
            company_name = await self._extract_company_name_from_elements(page)
            telephone = self._extract_telephone(raw_text)
            email = self._extract_email(raw_text)
            website = self._extract_website(raw_text)
            address = self._extract_address(raw_text)
            
            # If company name is still empty, try from raw text
            if not company_name:
                company_name = self._extract_company_name_from_text(raw_text)
            
            # Extract suburb, state, postcode from search query
            suburb, state, postcode = self._parse_search_query()
            
            provider_data = ProviderData(
                company_name=company_name,
                address=address,
                suburb=suburb,
                state=state,
                postcode=postcode,
                telephone=telephone,
                email=email,
                website=website,
                result_url=url
            )
            
            # Print to terminal
            print("\n" + "="*80)
            print(f"CONTENT FROM: {url}")
            print("="*80)
            print(raw_text[:1500] + "..." if len(raw_text) > 1500 else raw_text)
            print("="*80)
            print(f"EXTRACTED DATA:")
            print(f"Company: {company_name}")
            print(f"Address: {address}")
            print(f"Suburb: {suburb}, State: {state}, Postcode: {postcode}")
            print(f"Telephone: {telephone}")
            print(f"Email: {email}")
            print(f"Website: {website}")
            print("="*80 + "\n")
            
            return provider_data
            
        except Exception as e:
            print(f"❌ Failed to scrape {url}: {str(e)}")
            return None

    async def extract_all_text_from_page(self, page: Page) -> str:
        """Extract all visible text from the page"""
        try:
            # Get main content area or fallback to body
            content_selectors = [
                "main",
                "[role='main']",
                ".main-content",
                "article",
                ".content",
                "#content",
                ".provider-details",
                "[data-testid*='details']",
                "body"
            ]
            
            for selector in content_selectors:
                try:
                    element = page.locator(selector)
                    if await element.count() > 0:
                        text = await element.inner_text()
                        if text and len(text.strip()) > 100:
                            return text.strip()
                except:
                    continue
            
            # Final fallback to body
            body_text = await page.locator("body").inner_text()
            return body_text.strip()
            
        except Exception as e:
            return f"Error extracting text: {str(e)}"

    async def _extract_company_name_from_elements(self, page: Page) -> str:
        """Extract company name from page elements (more reliable)"""
        # Try multiple selectors for company name
        name_selectors = [
            "h1",
            "h1[data-testid*='name']",
            ".provider-name",
            "header h1",
            "[data-testid*='provider-name']",
            "h2:first-of-type"
        ]
        
        for selector in name_selectors:
            try:
                element = page.locator(selector).first
                if await element.count() > 0:
                    text = await element.inner_text()
                    if text and len(text.strip()) > 0:
                        # Clean up the text - take only the first line if multiple lines
                        clean_text = text.strip().split('\n')[0]
                        if len(clean_text) < 100:  # Reasonable company name length
                            return clean_text
            except:
                continue
        
        return ""

    def _extract_company_name_from_text(self, text: str) -> str:
        """Extract company name from raw text as fallback"""
        # Look for the main heading pattern
        lines = text.split('\n')
        for i, line in enumerate(lines):
            line = line.strip()
            if len(line) > 3 and len(line) < 100:  # Reasonable name length
                # Check if this looks like a company name (not a menu item, etc.)
                if (line.isupper() or any(word[0].isupper() for word in line.split() if word)) and \
                   not any(keyword in line.lower() for keyword in ['home', 'find a provider', 'search', 'print', 'share']):
                    return line
        return ""

    def _extract_telephone(self, text: str) -> str:
        """Extract telephone number from text"""
        # Australian phone number patterns
        patterns = [
            r'\b\d{2} \d{4} \d{4}\b',  # 02 8388 8000
            r'\b\d{4} \d{3} \d{3}\b',  # 1800 864 846
            r'\b\d{2}-\d{4}-\d{4}\b',  # 02-8388-8000
            r'\b\d{4}-\d{3}-\d{3}\b',  # 1800-864-846
            r'\b\d{8}\b',  # 0283888000
            r'\b\d{10}\b',  # 021800864846
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text)
            if matches:
                # Return the first match that looks like a phone number
                for match in matches:
                    if not match.startswith('2025') and not match.startswith('2024'):  # Avoid dates
                        return match.strip()
        
        return ""

    def _extract_email(self, text: str) -> str:
        """Extract email from text"""
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        match = re.search(email_pattern, text)
        if match:
            return match.group(0).strip()
        return ""

    def _extract_website(self, text: str) -> str:
        """Extract website from text"""
        website_pattern = r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\.-]*\??[/\w\.-=&%]*'
        matches = re.findall(website_pattern, text)
        if matches:
            # Filter out myagedcare links and return external websites
            for match in matches:
                if 'myagedcare.gov.au' not in match and 'bot.sannysoft.com' not in match:
                    return match.strip()
        return ""

    def _extract_address(self, text: str) -> str:
        """Extract address from text"""
        # Look for address patterns like "1 Cranbrook Road, ROSE BAY 2029 NSW"
        address_pattern = r'(\d+[\sA-Za-z]+,?\s+[A-Z\s]+\s+\d{4}\s+(?:ACT|NSW|NT|QLD|SA|TAS|VIC|WA))'
        match = re.search(address_pattern, text.upper())
        if match:
            return match.group(1).strip()
        
        # Alternative pattern for addresses without commas
        address_pattern2 = r'(\d+[\sA-Za-z]+\s+[A-Z\s]+\s+\d{4}\s+(?:ACT|NSW|NT|QLD|SA|TAS|VIC|WA))'
        match = re.search(address_pattern2, text.upper())
        if match:
            return match.group(1).strip()
        
        return ""

    def _parse_search_query(self) -> tuple[str, str, str]:
        """Parse suburb, state, and postcode from search query"""
        # Extract from SEARCH_QUERY like "Sydney NSW 2000"
        parts = SEARCH_QUERY.upper().split()
        if len(parts) >= 3:
            suburb = parts[0]  # "SYDNEY"
            state = parts[1]   # "NSW" 
            postcode = parts[2] # "2000"
            return suburb, state, postcode
        return "", "", ""

    async def run(self):
        """Main scraping workflow"""
        playwright, browser, context = await self.setup_browser()
        
        try:
            page = await context.new_page()
            
            # Navigate to search results with distance parameter
            search_url = self.construct_search_url()
            print(f"🚀 Navigating to: {search_url}")
            
            await self.goto_with_retry(page, search_url)
            await self.wait_for_results(page)
            
            # Extract links from result cards (respecting LINK_PER_SEARCH limit)
            detail_links = await self.extract_all_links_from_cards(page)
            
            if not detail_links:
                print("❌ No links found on the results page")
                return
            
            print(f"\n🎯 Found {len(detail_links)} detail pages to scrape")
            
            # Scrape each detail page one by one
            successful_scrapes = 0
            for i, link in enumerate(detail_links, 1):
                print(f"\n📖 Scraping page {i} of {len(detail_links)}")
                
                provider_data = await self.scrape_detail_page(page, link)
                
                if provider_data:
                    self.results.append(asdict(provider_data))
                    successful_scrapes += 1
                    print(f"✅ Successfully scraped: {provider_data.company_name}")
                else:
                    print(f"❌ Failed to scrape: {link}")
                
                # Brief pause between requests
                await page.wait_for_timeout(1000)
            
            # Save results to CSV
            self.save_to_csv()
            
            print(f"\n🎉 Scraping completed! Successfully scraped {successful_scrapes} out of {len(detail_links)} results to output.csv")
            
        except Exception as e:
            print(f"💥 Scraping failed: {str(e)}")
            import traceback
            traceback.print_exc()
        finally:
            await browser.close()
            await playwright.stop()

    def save_to_csv(self):
        """Save results to CSV file"""
        if not self.results:
            print("No data to save")
            return
            
        fieldnames = ["company_name", "address", "suburb", "state", "postcode", 
                     "telephone", "email", "website", "result_url"]
        
        with open('output.csv', 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in self.results:
                # Clean company name - remove extra text
                company_name = row['company_name']
                if '\n' in company_name:
                    company_name = company_name.split('\n')[0].strip()
                row['company_name'] = company_name
                writer.writerow(row)

async def main():
    scraper = MyAgedCareScraper()
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
