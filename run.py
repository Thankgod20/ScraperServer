import base64
import datetime
import io
import ast
import json
import os
import random
import socket
import threading
import time
import zipfile
import queue
from contextlib import contextmanager

import redis
import requests

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service as ChromeService
#How can i send data to active thread in python. (Note: Edit only the relevant part of the code)
# --- Global configuration ---
NGROK_BASE = "http://127.0.0.1:8000"
NGROK_REDIS = '127.0.0.1'
NGROK_PORT = 6379
MAX_CONCURRENT_BROWSERS = 5  # Reduced from 10 to prevent overloading
MAX_RETRIES = 3
DRIVER_INIT_TIMEOUT = 30
TWEET_LIMIT = 500
SCROLL_LIMIT = 100
SCROLL_WAIT = 5
query_queue = queue.Queue()
active_query =[]
# @title Default title text
#How can i send data to active thread in python. (Note: Edit only the relevant part of the code)

# ----------------------------
# Helper Functions and Classes
# ----------------------------

class BrowserManager:
    """Manages browser instances and ensures proper cleanup"""

    def __init__(self):
        self.active_drivers = {}
        self.lock = threading.Lock()

    def register(self, index, driver):
        with self.lock:
            self.active_drivers[index] = driver

    def unregister(self, index):
        with self.lock:
            if index in self.active_drivers:
                try:
                    self.active_drivers[index].quit()
                except Exception as e:
                    print(f"[WARNING] Error closing driver {index}: {e}")
                del self.active_drivers[index]

    def cleanup_all(self):
        with self.lock:
            for index, driver in list(self.active_drivers.items()):
                try:
                    driver.quit()
                except Exception:
                    pass
            self.active_drivers.clear()

# Create a global browser manager
browser_manager = BrowserManager()

def generate_unique_fingerprint(proxy_extension, user_agent, index):
    """Set up Chrome options with a custom user agent and proxy extension."""
    print(f"[Browser {index}] Setting up browser with unique fingerprint")
    options = webdriver.ChromeOptions()

    # Set the binary location (for Colab use chromium-browser)
    #options.binary_location = "/usr/bin/chromium-browser"

    # Common Chrome args
    args = [
        #"--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        f"--remote-debugging-port={get_random_port()}",  # Unique debugging port
        f"--user-agent={user_agent}",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--disable-notifications",
        "--mute-audio",
        "--no-first-run",
        "--start-maximized",
        f"--user-data-dir=/tmp/chrome_profile_{index}"  # Use /tmp for temporary data
    ]

    for arg in args:
        options.add_argument(arg)

    # Add proxy extension
    ext_file = f"/tmp/proxy_extension_{index}.zip"
    with open(ext_file, "wb") as f:
        f.write(base64.b64decode(proxy_extension))
    options.add_extension(ext_file)

    # Set page load timeout
    options.page_load_strategy = 'eager'  # 'eager' loads DOM but not all resources

    return options

def scroll_to_bottom(driver):
    """Scroll to the bottom of the page with error handling."""
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        return True
    except Exception as e:
        print(f"[ERROR] Error scrolling: {e}")
        return False

def wait_for_page_load(driver, timeout=30):
    """Wait for page to load with proper error handling."""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        # Also wait for articles to appear
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "article"))
        )

        # Wait for progress bar to disappear
        WebDriverWait(driver, timeout).until_not(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[role="progressbar"]'))
        )

        return True
    except TimeoutException:
        print("[WARNING] Page load timed out")
        return False
    except Exception as e:
        print(f"[ERROR] Error waiting for page load: {e}")
        return False

def is_port_available(port):
    """Check if a port is available for use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("", port))
            return True
        except OSError:
            return False

def get_random_port():
    """Get a random available port."""
    for _ in range(10):  # Try up to 10 times
        port = random.randint(10000, 65000)
        if is_port_available(port):
            return port
    return 0  # Return 0 if no port found

def update_session_status(id, port, status):
    """Update the status of a session in the remote file."""
    url = f"{NGROK_BASE}/datacenter/activesession.json"
    try:
        if status == "NEW":
            sessions = []
        else:
            try:
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                sessions = resp.json()
            except Exception as e:
                print(f"[WARNING] Could not load existing sessions: {e}")
                sessions = []

        now = datetime.datetime.now().isoformat()
        found = False

        for s in sessions:
            if s.get("id") == id:
                s["port"] = port
                s["status"] = status
                s["updated"] = now
                found = True
                break

        if not found:
            sessions.append({
                "id": id,
                "port": port,
                "status": status,
                "updated": now
            })

        put_resp = requests.put(url, json=sessions, timeout=10)
        if put_resp.status_code != 200:
            print(f"[ERROR] Failed to update session file, status code: {put_resp.status_code}")

    except Exception as e:
        print(f"[ERROR] Error updating session file: {e}")

def init_redis_client():
    """Initialize Redis client with retry logic."""
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        try:
            client = redis.Redis(host=NGROK_REDIS, port=NGROK_PORT, socket_timeout=5)
            client.ping()  # Test connection
            return client
        except redis.exceptions.ConnectionError as e:
            print(f"[WARNING] Redis connection failed (attempt {retry_count+1}/{max_retries}): {e}")
            retry_count += 1
            if retry_count < max_retries:
                time.sleep(2)

    print("[ERROR] Failed to connect to Redis after multiple attempts")
    return None

def get_attr(elem, selector, attr):
    """Safely get an attribute from an element."""
    try:
        child = elem.find_element(By.CSS_SELECTOR, selector)
        return child.get_attribute(attr)
    except Exception:
        return ""

def get_text(elem, selector):
    """Safely get text from an element."""
    try:
        child = elem.find_element(By.CSS_SELECTOR, selector)
        return child.text
    except Exception:
        return ""
def is_same_minute(t1, t2):
    return t1.replace(second=0, microsecond=0) == t2.replace(second=0, microsecond=0)

# Helper: Safely get text from a tweet element.
def get_text(tweet, selector):
    try:
        element = tweet.find_element(By.CSS_SELECTOR, selector)
        return element.text
    except Exception:
        return ""

# Helper: Safely append to a list.
def safe_append_string(lst, value):
    lst.append(value)
    return lst

def safe_append_int(lst, value):
    lst.append(value)
    return lst

def safe_append_time(lst, value):
    if isinstance(value, datetime.datetime):
        lst.append(value.isoformat())
    else:
        lst.append(value)
    return lst

# This function processes tweet elements and stores/updates tweet data in Redis.
def get_element_data(query, tweets, time_count, plot_time, redis_client, index):
    redis_key_prefix = f"spltoken:{query}:"
    for tweet in tweets:
        try:
            content = tweet.text
        except Exception as e:
            print(f"[DEBUG] [Browser {index}] Error getting tweet text: {e}")
            continue

        try:
            link_element = tweet.find_element(By.CSS_SELECTOR, "a[role='link'][href*='/status/']")
            status_url = link_element.get_attribute("href")
        except Exception as e:
            print(f"[DEBUG] [Browser {index}] Error retrieving status URL: {e}")
            continue

        redis_key = f"{redis_key_prefix}{hash(status_url)}"
        try:
            existing_data = redis_client.get(redis_key)
        except Exception as e:
            print(f"[ERROR] [Browser {index}] Redis error while getting key: {e}")
            continue

        # If tweet does not exist in Redis, extract details and store new data.
        if existing_data is None:
            try:
                datetime_element = tweet.find_element(By.CSS_SELECTOR, "time")
                datetime_value = datetime_element.get_attribute("datetime")
            except Exception as e:
                datetime_value = ""
                print(f"[DEBUG] [Browser {index}] Error getting datetime attribute: {e}")

            try:
                profile_img_element = tweet.find_element(By.CSS_SELECTOR, 'div[data-testid="Tweet-User-Avatar"] img')
                profile_img_url = profile_img_element.get_attribute("src")
            except Exception as e:
                profile_img_url = ""
                print(f"[DEBUG] [Browser {index}] Error getting profile image: {e}")

            new_data = {
                "tweet": content,
                "status": status_url,
                "post_time": datetime_value,
                "profile_image": profile_img_url,
                "params": {
                    "likes": [get_text(tweet, "[data-testid='like']")],
                    "retweet": [get_text(tweet, "[data-testid='retweet']")],
                    "comment": [get_text(tweet, "[data-testid='reply']")],
                    "views": [get_text(tweet, "a[aria-label*='views']")],
                    "time": [time_count],
                    "plot_time": [plot_time.isoformat() if isinstance(plot_time, datetime.datetime) else plot_time]
                }
            }
            try:
                redis_client.set(redis_key, json.dumps(new_data))
            except Exception as e:
                print(f"[ERROR] [Browser {index}] Redis error while saving new tweet: {e}")

        else:
            # If tweet exists, update its metrics if the last update wasn’t in the same minute.
            try:
                existing_entry = json.loads(existing_data)
            except Exception as e:
                print(f"[ERROR] [Browser {index}] Error parsing existing Redis data: {e}")
                continue

            params = existing_entry.get("params", {})
            plot_time_list = params.get("plot_time", [])
            last_plot_time = None
            if plot_time_list:
                try:
                    last_plot_time = datetime.datetime.fromisoformat(plot_time_list[-1])
                except Exception as e:
                    last_plot_time = None

            if not last_plot_time or not is_same_minute(last_plot_time, plot_time):
                params["likes"] = safe_append_string(params.get("likes", []), get_text(tweet, "[data-testid='like']"))
                params["retweet"] = safe_append_string(params.get("retweet", []), get_text(tweet, "[data-testid='retweet']"))
                params["comment"] = safe_append_string(params.get("comment", []), get_text(tweet, "[data-testid='reply']"))
                params["views"] = safe_append_string(params.get("views", []), get_text(tweet, "a[aria-label*='views']"))
                params["time"] = safe_append_int(params.get("time", []), time_count)
                params["plot_time"] = safe_append_time(params.get("plot_time", []), plot_time)
                existing_entry["params"] = params
                try:
                    redis_client.set(redis_key, json.dumps(existing_entry))
                except Exception as e:
                    print(f"[ERROR] [Browser {index}] Redis error while updating tweet: {e}")

def scrape_and_save_tweet(query, driver, index,time_count, plot_time):
    """Scrape tweets and save them to Redis."""
    all_tweets = set()
    all_status = []
    previous_tweet_count = 0
    number_scroll = 0

    redis_client = init_redis_client()
    if not redis_client:
        print(f"[ERROR] [Browser {index}] Failed to initialize Redis client")
        return False

    start_time = time.time()

    try:
        print(f"[INFO] [Browser {index}] Starting tweet scraping for query: {query}")

        while True:
            try:
                tweets = driver.find_elements(By.CSS_SELECTOR, "article")

                for tweet in tweets:
                    # Use the tweet element's ID to check if we've seen it
                    tweet_id = tweet.get_attribute("id") or str(hash(tweet.text))

                    if tweet_id in all_tweets:
                        continue

                    all_tweets.add(tweet_id)

                    try:
                        link_element = tweet.find_element(By.CSS_SELECTOR, "a[role='link'][href*='/status/']")
                        status_url = link_element.get_attribute("href")

                        if status_url and status_url not in all_status:
                            print(f"[INFO] [Browser {index}] Found tweet URL: {status_url}")
                            all_status.append(status_url)

                            print(f"=== Tweet Exist for Browser:- {index} Query:- {query} Scrolls {number_scroll}")
                            # Process all tweets (mimicking the Go routine that processes tweets concurrently)
                            get_element_data(query, tweets, time_count, plot_time, redis_client, index)
                            # Store in Redis
                            '''
                            if redis_client:
                                key = f"tweet:{query}:{hash(status_url)}"
                                data = {
                                    "url": status_url,
                                    "query": query,
                                    "timestamp": datetime.datetime.now().isoformat(),
                                    "scraper_index": index
                                }
                                try:
                                    redis_client.set(key, json.dumps(data))
                                except Exception as e:
                                    print(f"[ERROR] [Browser {index}] Redis error: {e}")
                            '''
                    except Exception as e:
                        print(f"[DEBUG] [Browser {index}] Error processing tweet: {e}")
                        continue

                current_count = len(all_status)
                print(f"[INFO] [Browser {index}] Tweet Count: {current_count}, Previous: {previous_tweet_count}")

                # Check termination conditions
                if (current_count == previous_tweet_count or
                    current_count >= TWEET_LIMIT or
                    number_scroll >= SCROLL_LIMIT or
                    time.time() - start_time > 300):  # 5 minute timeout
                    print(f"[INFO] [Browser {index}] Scraping complete: {len(all_status)} tweets found")
                    break

                previous_tweet_count = current_count

                # Scroll and wait
                if not scroll_to_bottom(driver):
                    print(f"[WARNING] [Browser {index}] Failed to scroll, stopping")
                    break

                number_scroll += 1
                time.sleep(SCROLL_WAIT)

            except InvalidSessionIdException:
                print(f"[ERROR] [Browser {index}] Session ID is invalid")
                return False
            except Exception as e:
                print(f"[ERROR] [Browser {index}] Error during scraping loop: {e}")
                return False

        return True

    except Exception as e:
        print(f"[ERROR] [Browser {index}] Scraping failed: {e}")
        return False

def create_proxy_extension(ip, port, username, password):
    """Create a Manifest V3 Chrome extension for proxy authentication with a proactive beacon."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        manifest = r'''{
  "manifest_version": 3,
  "name": "Chrome Proxy with Auth",
  "version": "1.0.0",
  "permissions": [
    "proxy",
    "webRequest",
    "webRequestAuthProvider",
    "storage"
  ],
  "host_permissions": [
    "<all_urls>"
  ],
  "background": {
    "service_worker": "background.js"
  }
}'''
        zipf.writestr("manifest.json", manifest)
        
        # The background service worker sets the proxy and listens for auth requests.
        # It also sends a proactive beacon request (from the service worker via fetch)
        # to a known URL through the proxy. This minimizes the chance of an authentication popup.
        background = f'''// Set up the proxy configuration
chrome.proxy.settings.set({{
  value: {{
    mode: "fixed_servers",
    rules: {{
      singleProxy: {{
        scheme: "http",
        host: "{ip}",
        port: parseInt("{port}")
      }},
      bypassList: ["localhost"]
    }}
  }},
  scope: "regular"
}}, function() {{
  console.log("Proxy settings applied");
}});

// Listen for authentication requests and supply credentials.
chrome.webRequest.onAuthRequired.addListener(
  function(details, asyncCallback) {{
    console.log("Auth required for:", details.url);
    // Supply the credentials asynchronously.
    asyncCallback({{
      authCredentials: {{
        username: "{username}",
        password: "{password}"
      }}
    }});
  }},
  {{ urls: ["<all_urls>"] }},
  ["asyncBlocking"]
);

// Proactive beacon request: trigger a lightweight fetch so that the service worker is active
// and the proxy credentials are pre-cached before normal browsing begins.
fetch("https://example.com/", {{ cache: "no-store" }})
  .then(response => {{
    console.log("Beacon fetch completed:", response.status);
  }})
  .catch(err => {{
    console.error("Beacon fetch error:", err);
  }});
'''
        zipf.writestr("background.js", background)
    return base64.b64encode(buffer.getvalue()).decode()

def read_json_file(file_path, max_retries=3):
    """Read JSON file from remote URL with retry logic."""
    url = f"{NGROK_BASE}/{file_path}"
    retry_count = 0
    headers = {}
    headers["ngrok-skip-browser-warning"] = "69420"
    while retry_count < max_retries:
        try:
            r = requests.get(url,headers=headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            print(f"[WARNING] Error reading {file_path} (attempt {retry_count+1}/{max_retries}): {e}")
            retry_count += 1
            if retry_count < max_retries:
                time.sleep(5)

    print(f"[ERROR] Failed to read {file_path} after {max_retries} attempts")
    return []

def read_json_file_cookie(file_path):
    return read_json_file(file_path)

def read_json_file_proxy(file_path):
    return read_json_file(file_path)

# ----------------------------
# Scraping and Threading Functions
# ----------------------------

def initialize_driver(chrome_options, index):
    """Initialize the Chrome driver with proper error handling."""
    retry_count = 0
    while retry_count < MAX_RETRIES:
        try:
            print(f"[INFO] [Browser {index}] Initializing driver (attempt {retry_count+1})")
            # Set unique port for ChromeDriver
            unique_port = str(get_random_port())
            os.environ["CHROMEDRIVER_PORT"] = unique_port
            print(f"[INFO] Using CHROMEDRIVER_PORT: {unique_port}")

            chrome_service = ChromeService(executable_path='./chromedriver',port=unique_port)
            driver = webdriver.Chrome(options=chrome_options, service=chrome_service)
            driver.set_page_load_timeout(DRIVER_INIT_TIMEOUT)

            # Register with browser manager
            browser_manager.register(index, driver)

            return driver
        except Exception as e:
            print(f"[ERROR] [Browser {index}] Driver initialization failed: {e}")
            retry_count += 1
            if retry_count < MAX_RETRIES:
                time.sleep(5)

    return None

def login_to_twitter(driver, cookie_value, index):
    """Attempt to log in to Twitter using a cookie."""
    try:
        print(f"[INFO] [Browser {index}] Navigating to Twitter homepage")
        driver.get("https://x.com")
        time.sleep(10)
        print(f"[INFO] [Browser {index}] Page loaded")
        driver.execute_script("document.body.style.zoom='25%'")
        # Add authentication cookie
        cookie = {
            "name": "auth_token",
            "value": cookie_value,
            "path": "/",
            "domain": "x.com",
            "expiry": int(time.time() + 24*3600)
        }

        try:
            driver.add_cookie(cookie)
            print(f"[INFO] [Browser {index}] Cookie added successfully")
        except Exception as e:
            print(f"[ERROR] [Browser {index}] Error adding cookie: {e}")
            return False

        time.sleep(3)

        # Refresh and check if login was successful
        print(f"[INFO] [Browser {index}] Refreshing page...")
        driver.refresh()
        time.sleep(5)

        # Check if login succeeded by looking for home element
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='AppTabBar_Home_Link']"))
            )
            print(f"[INFO] [Browser {index}] Login successful")
            return True
        except TimeoutException:
            print(f"[ERROR] [Browser {index}] Login failed - home element not found Cookie: {cookie_value}")
            return False

    except InvalidSessionIdException as e:
        print(f"[ERROR] [Browser {index}] Invalid session during login: {e}")
        return False
    except Exception as e:
        print(f"[ERROR] [Browser {index}] Login error: {e}")
        return False

def scrape_twitter_search( index, chrome_options, cookie_value, result_queue,):
    """Main function to scrape Twitter search results."""
    driver = None
    port = get_random_port()

    update_session_status(index, port, "initializing")

    try:
        # Initialize driver
        driver = initialize_driver(chrome_options, index)
        if not driver:
            result_queue.put({"index": index, "success": False, "error": "Failed to initialize driver"})
            update_session_status(index, port, "failed")
            return

        # Log in to Twitter
        if not login_to_twitter(driver, cookie_value, index):
            result_queue.put({"index": index, "success": False, "error": "Login failed"})
            update_session_status(index, port, "failed")
            return

        # Mark session as active
        update_session_status(index, port, "active")

        # Get query from queue
        global query_queue
        global active_query
        unmatched_queries = []
        query_array = []
        query = None
        try:
            while True:
                time.sleep(10)
                sizex = query_queue.qsize()
                if sizex >0:
                    found_match = False
                    for _ in range(sizex):
                        query_ = query_queue.get()  # Reduced timeout
                        if shutdown_event.is_set():
                            print(f"[INFO] [Browser {index}] Received shutdown signal, exiting")
                            break
                        if query_ in active_query:
                            print(f"[INFO] [Browser {index}] Query already active, skipping: {query_}")
                            continue
                        if query_.get('index') == index:
                            print(f"[INFO] [Browser {index}] Found matching query: {query_}")
                            query = query_
                            active_query.append(query_)
                            found_match = True
                            break

                        else:
                            print(f"[INFO] [Browser {index}] Non-matching query: {query_} Index {query_.get('index')}")
                            unmatched_queries.append(query_)
                    for item in unmatched_queries:
                        query_queue.put(item)
                    unmatched_queries.clear()

                    if found_match:
                        break

        except queue.Empty:
            # No more items in queue
            print(f"[INFO] [Browser {index}] Non-matching query")
            pass

        iteration = 0
        itt =0
        
        while not shutdown_event.is_set():
            Loaded = True
            print(f"[INFO] [Browser {index}], [ROUND]: {iteration} ")
            try:
                size_x = query_queue.qsize()
                if size_x >0:
                    for _ in range(size_x):
                        query_ = query_queue.get()  # Reduced timeout
                        if shutdown_event.is_set():
                            print(f"[INFO] [Browser {index}] Received shutdown signal, exiting")
                            break
                        if query_ in active_query:
                            print(f"[INFO] [Browser {index}] Query already active, skipping: {query_}")
                            continue
                        if query_.get('index') == index:
                            print(f"[INFO] [Browser {index}] Found matching query: {query_}")
                            query = query_
                            active_query.append(query_)


                        else:
                            print(f"[INFO] [Browser {index}] Non-matching query: {query_} Index {query_.get('index')}")
                            unmatched_queries.append(query_)
                    for item in unmatched_queries:
                        query_queue.put(item)
                    unmatched_queries.clear()
                if query is not None and len(query_array)<6:#if query is not None and query.get('index') == index:
                    query_array.append(query)
                    query = None
                    iteration = len(query_array)-1
                if len(query_array)>0:
                    print(f"[INFO] [Browser {index}] Full Query Array: {query_array} Processing")
                    now = datetime.datetime.now()

                    # Subtract 24 hours (1 day) to get yesterday's datetime
                    yesterday = now - datetime.timedelta(days=1)

                    # Format yesterday's date as "YYYY-MM-DD"
                    yesterdate = yesterday.strftime("%Y-%m-%d")
                    # Navigate to search URL
                    update_session_status(index, port, f"Searching Count: {iteration} of ArraySize {len(query_array)} Total Iteration {itt} ")
                    search_url = ""
                    if itt%2==0:
                        search_url = f"https://x.com/search?q={query_array[iteration]['address']}%20OR%20${query_array[iteration]['symbol']}%20since:{yesterdate}&src=typed_query"
                    else:
                        search_url = f"https://x.com/search?q={query_array[iteration]['address']}%20OR%20${query_array[iteration]['symbol']}%20since:{yesterdate}&src=typeahead_click&f=live"
                    
                    print(f"[INFO] [Browser {index}] Navigating to: {search_url}")
                    driver.get(search_url)

                    # Wait for page to load
                    print(f"[INFO] [Browser {index}] Waiting for search page to load...")
                    if not wait_for_page_load(driver):
                        print(f"[ERROR] [Browser {index}] Search page failed to load")
                        result_queue.put({"index": index, "success": False, "error": "Search page failed to load"})
                        iteration = 0
                        Loaded = False
                        time.sleep(30)
                        #return

                    if Loaded:
                        # Scrape tweets
                        print(f"[INFO] [Browser {index}] Starting to scrape tweets")
                        time_count = 2*(iteration+1)
                        plot_time = datetime.datetime.utcnow()
                        if scrape_and_save_tweet(query_array[iteration]['address'], driver, index,time_count, plot_time):
                            print(f"[INFO] [Browser {index}] Scraping completed successfully")
                            result_queue.put({"index": index, "success": True, "error": ""})
                        else:
                            print(f"[ERROR] [Browser {index}] Scraping failed")
                            result_queue.put({"index": index, "success": False, "error": "Scraping failed"})

            except queue.Empty:
                print(f"[INFO] [Browser {index}] No query received within timeout")
                result_queue.put({"index": index, "success": False, "error": "Query timeout"})

            iteration += 1
            itt+=1
            if iteration > (len(query_array)-1) or iteration >5:
                print(f"[INFO] [Browser {index}] Reached 5 iterations. Waiting for a new query to update the address.")
                iteration = 0
            
            time.sleep(10)

    except Exception as e:
        print(f"[ERROR] [Browser {index}] Unhandled exception: {e}")
        result_queue.put({"index": index, "success": False, "error": str(e)})

    finally:
        # Cleanup
        update_session_status(index, port, "inactive")
        browser_manager.unregister(index)

def run_scrape(ip_address, port_val, random_user_agent, cookie_value,
               username, password, index, result_queue, semaphore):
    """Run a scrape operation with proper resource management."""
    try:
        print(f"[INFO] [Browser {index}] Starting scrape operation")
        extension = create_proxy_extension(ip_address, port_val, username, password)
        options = generate_unique_fingerprint(extension, random_user_agent, index)
        scrape_twitter_search( index, options, cookie_value, result_queue)
    except Exception as e:
        print(f"[ERROR] [Browser {index}] Error in run_scrape: {e}")
        result_queue.put({"index": index, "success": False, "error": str(e)})
    finally:
        semaphore.release()
        update_session_status(index, 0, "END")
        print(f"[INFO] [Browser {index}] Released semaphore")
def send_query_to_thread(index, query_data):
    """
    Send data to a specific thread by tagging it with the thread index.

    Args:
        index (int): The index of the thread to send data to
        query_data (dict): The query data to send
    """
    global query_queue
    # Tag the query with the target thread index
    tagged_query = query_data.copy()  # Create a copy to avoid modifying the original    # Add thread index as identifier

    # Add to the queue
    query_queue.put(tagged_query)
    print(f"[INFO] Sent query to thread {index}: {tagged_query}")
shutdown_event = threading.Event()

def worker(thread_id):
    while not shutdown_event.is_set():
        # Your thread work here
        print(f"Thread {thread_id} working...")
        time.sleep(1)
    print(f"Thread {thread_id} shutting down gracefully.")

def kill_all_active_threads():
    # Signal threads to shutdown
    shutdown_event.set()

    # Give threads time to notice the shutdown signal
    time.sleep(1)

    current = threading.current_thread()
    for t in threading.enumerate():
        if t is not current and t.daemon:
            print(f"[INFO] Waiting for thread {t.name} to finish...")
            t.join(timeout=5)  # Increased timeout for cleanup

    # Reset the browser manager to ensure all browser instances are properly closed
    browser_manager.cleanup_all()

    # Reset shutdown event for future threads
    shutdown_event.clear()

    print("[INFO] All threads have been terminated")
# ----------------------------
# Main Function
# ----------------------------

def main():
    """Main function with improved error handling and resource management."""

    kill_all_active_threads()
    print("\n[INFO] ===== Starting Twitter scraper =====\n")

    # Initialize
    addr_array = []
    cook_array = []
    prx_array = []
    previous_length = 0

    # Queues for thread communication
    #query_queue = queue.Queue()
    global query_queue
    result_queue = queue.Queue()

    # Limit concurrent browsers
    semaphore = threading.Semaphore(MAX_CONCURRENT_BROWSERS)
    threads = []
    active_threads = 0

    # Reset session status
    update_session_status(0, 0, "NEW")

    # User agent rotation
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/110.0"
    ]

    try:
        index = 0
        while True:
            try:
                # Load configuration from remote files
                print("[INFO] Loading configuration files...")
                address_array = read_json_file("addresses/address.json")
                cookies_array = read_json_file_cookie("datacenter/cookies.json")
                proxies_array = read_json_file_proxy("datacenter/proxies.json")

                # Update address list
                for entry in address_array:
                    if entry not in addr_array:
                        addr_array.append(entry)
                        query_queue.put(entry)
                        thread_index = entry.get('index', 0)
                        send_query_to_thread(thread_index, entry)
                        print(f"[INFO] Added new address: {entry}")

                # Update cookies list
                for entry in cookies_array:
                    if entry["cookies"] not in cook_array:
                        cook_array.append(entry["cookies"])
                        print("[INFO] Added new cookie")

                # Update proxies list
                for entry in proxies_array:
                    if entry["proxies"] not in prx_array:
                        prx_array.append(entry["proxies"])
                        print("[INFO] Added new proxy")

                # Print current status
                print(f"[INFO] Status - Addresses: {len(addr_array)}, Cookies: {len(cook_array)}, Proxies: {len(prx_array)}, Active threads: {active_threads}")

                # Start new threads if we have available cookies and semaphores
                if index < len(cook_array) and active_threads < MAX_CONCURRENT_BROWSERS:
                    # Get thread-specific data
                    random_user_agent = user_agents[index % len(user_agents)]
                    random_proxy = prx_array[index % len(prx_array)]

                    # Parse proxy details
                    proxy_parts = random_proxy.split(":")
                    if len(proxy_parts) < 4:
                        print(f"[ERROR] Invalid proxy format: {random_proxy}")
                        index += 1
                        continue

                    ip_address = proxy_parts[0]
                    port_str = proxy_parts[1]
                    proxy_username = proxy_parts[2]
                    proxy_password = proxy_parts[3]

                    # Get cookie
                    the_cookie = cook_array[index % len(cook_array)]

                    # Acquire semaphore and start thread
                    print(f"[INFO] Acquiring semaphore for browser {index}...")
                    semaphore.acquire()
                    active_threads += 1

                    print(f"[INFO] Starting thread for browser {index} with UA: {random_user_agent[:30]}...")
                    t = threading.Thread(
                        target=run_scrape,
                        args=(ip_address, port_str, random_user_agent,
                              the_cookie, proxy_username, proxy_password, index,
                              result_queue, semaphore)
                    )
                    t.daemon = True  # Make thread daemon so it exits when main thread exits
                    t.start()
                    threads.append(t)

                    print(f"[INFO] Started thread for browser {index}")
                    index += 1
                    time.sleep(5)  # Stagger thread starts

                # Send queries to threads if we have new addresses
                '''
                if query_queue.qsize() > previous_length:
                    for i in range(min(len(cook_array), active_threads)):
                        #query_queue.put(addr_array[-1])  # Send newest address
                        send_query_to_thread(i, addr_array[-1])
                        print(f"[INFO] Sent query to browser {i}: {addr_array[-1]}")
                    previous_length = len(addr_array)
                '''

                # Check for thread results
                try:
                    result = result_queue.get(timeout=1)
                    #active_threads -= 1

                    if result["success"]:
                        print(f"[INFO] Browser {result['index']} completed successfully")
                    else:
                        print(f"[WARNING] Browser {result['index']} failed: {result['error']}")

                except queue.Empty:
                    pass

                # Exit condition - adjust as needed
                if index >= 15:  # For testing, just run 3 browsers
                    print("[INFO] Test limit reached, stopping after 5 browsers")
                    break

                time.sleep(1)  # Prevent CPU hogging

            except Exception as e:
                print(f"[ERROR] Error in main loop: {e}")
                time.sleep(10)  # Wait before retrying

        # Wait for all threads to complete
        print("[INFO] Waiting for all threads to complete...")
        for t in threads:
            t.join(timeout=30)

    except KeyboardInterrupt:
        print("[INFO] Keyboard interrupt detected, shutting down")
    finally:
        # Cleanup
        print("[INFO] Cleaning up resources...")
        kill_all_active_threads()
        browser_manager.cleanup_all()
        print("[INFO] Scraper shutdown complete")

if __name__ == '__main__':
    main()