BOT_NAME = "ebay_scraper"
SPIDER_MODULES = ["ebay_scraper.spiders"]
NEWSPIDER_MODULE = "ebay_scraper.spiders"

# NOTE: eBay's robots.txt disallows the entire search surface (`Disallow: /sch/`,
# `/sch/i.html?_nkw=`), so there is NO robots-compliant way to crawl search
# results / epid seller-count pages. This override is a deliberate, user-directed
# choice for a low-volume, read-only, throttled research run from the user's own
# residential IP against public listing data. It is NOT a general default.
# We stay polite: real delays, low concurrency, autothrottle (below).
ROBOTSTXT_OBEY = False

# The exact browser-like fingerprint that returned HTTP 200 in manual testing.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",  # not brotli: Scrapy can't decode br without extra deps
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Politeness / anti-block
CONCURRENT_REQUESTS = 4
CONCURRENT_REQUESTS_PER_DOMAIN = 2
DOWNLOAD_DELAY = 1.5
RANDOMIZE_DOWNLOAD_DELAY = True
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 20.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.5
COOKIES_ENABLED = True

RETRY_ENABLED = True
RETRY_HTTP_CODES = [403, 429, 500, 502, 503, 504, 522, 524, 408]
RETRY_TIMES = 3
DOWNLOAD_TIMEOUT = 30

HTTPCACHE_ENABLED = False
LOG_LEVEL = "INFO"
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"
