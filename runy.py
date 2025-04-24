import base64
import datetime
import io
import json
import os
import random
import threading
import time
import queue
from contextlib import contextmanager
from typing import Dict, Optional, Any
import redis
import httpx
import requests
import asyncio
# --- Global configuration ---
NGROK_BASE = "http://127.0.0.1:8000"
NGROK_REDIS = '127.0.0.1'
NGROK_PORT = 6379
MAX_CONCURRENT_SCRAPERS = 5  # Maximum concurrent API clients
MAX_RETRIES = 3
TWEET_LIMIT = 500
query_queue = queue.Queue()
active_query = []

# GraphQL endpoint and headers
TWITTER_API_URL = "https://api.twitter.com/graphql/"
TWITTER_SEARCH_ENDPOINT = "PE1rbi7nIaYbf6Wtv4Y_TQ/SearchTimeline"

# Thread control
shutdown_event = threading.Event()
port_lock = threading.Lock()

# --- Helper Functions ---

def get_random_port():
    """Get a random available port with thread safety."""
    with port_lock:
        return random.randint(10000, 65000)

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

def read_json_file(file_path, max_retries=3):
    """Read JSON file from remote URL with retry logic."""
    url = f"{NGROK_BASE}/{file_path}"
    retry_count = 0
    headers = {}
    headers["ngrok-skip-browser-warning"] = "69420"
    while retry_count < max_retries:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            print(f"[WARNING] Error reading {file_path} (attempt {retry_count+1}/{max_retries}): {e}")
            retry_count += 1
            if retry_count < max_retries:
                time.sleep(5)

    print(f"[ERROR] Failed to read {file_path} after {max_retries} attempts")
    return []

def is_same_minute(t1, t2):
    return t1.replace(second=0, microsecond=0) == t2.replace(second=0, microsecond=0)

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

# --- Twitter GraphQL API Functions ---

class TwitterCookieAuth:
    """
    Twitter/X authentication client that uses only cookies to authenticate.
    This approach skips the login flow and directly uses stored cookies for authentication.
    """
    
    BASE_URL = "https://x.com"
    API_URL = "https://api.twitter.com"
    GQL_URL = "https://x.com/i/api/graphql"
    
    def __init__(self, debug: bool = False, proxy: Optional[str] = None,userAgent: Optional[str]="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"):
        """
        Initialize Twitter cookie authentication client.
        
        Args:
            debug: Enable debug mode to see detailed logs
            proxy: Optional proxy to route requests through
        """
        self.debug = debug
        self.proxy = proxy
        self.client = httpx.AsyncClient(
            follow_redirects=True,
            proxy=proxy,
            timeout=30.0
        )
        self.cookies = {}
        self.headers = {
            "User-Agent":userAgent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Referer": "https://x.com/",
            "Origin": "https://x.com"
        }
        
    def _log(self, message: str) -> None:
        """Log message if debug mode is enabled"""
        if self.debug:
            print(f"[DEBUG] {message}")
    
    def set_cookies(self, cookies: Dict[str, str]) -> None:
        """
        Set cookies for authentication
        
        Args:
            cookies: Dictionary of cookie name-value pairs
        """
        self.cookies = cookies
        
        # Extract the csrf token from cookies if present
        if "ct0" in cookies:
            self.headers["x-csrf-token"] = cookies["ct0"]

        self.headers.update({
        "authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        })
            
        self._log(f"Set cookies: {cookies}")
    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()
    async def verify_auth(self) -> bool:
        """
        Verify if the provided cookies are valid for authentication
        
        Returns:
            True if authentication is valid, False otherwise
        """
        # Try to access the home timeline, which requires authentication
        url = f"{self.GQL_URL}/ci_OQZ2k0rG0Ax_lXRiWVA/HomeTimeline"
        
        data = {
            "variables": {
                "count": 1,
                "includePromotedContent": True,
                "latestControlAvailable": True,
                "requestContext": "launch",
                "withCommunity": True
            },
            "features": {
                "rweb_video_screen_enabled": False,
                "profile_label_improvements_pcf_label_in_post_enabled": True,
                "rweb_tipjar_consumption_enabled": True,
                "responsive_web_graphql_exclude_directive_enabled": True,
                "verified_phone_label_enabled": False,
                "creator_subscriptions_tweet_preview_api_enabled": True,
                "responsive_web_graphql_timeline_navigation_enabled": True,
                "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                "premium_content_api_read_enabled": False,
                "communities_web_enable_tweet_community_results_fetch": True,
                "c9s_tweet_anatomy_moderator_badge_enabled": True,
                "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
                "responsive_web_grok_analyze_post_followups_enabled": True,
                "responsive_web_jetfuel_frame": False,
                "responsive_web_grok_share_attachment_enabled": True,
                "articles_preview_enabled": True,
                "responsive_web_edit_tweet_api_enabled": True,
                "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
                "view_counts_everywhere_api_enabled": True,
                "longform_notetweets_consumption_enabled": True,
                "responsive_web_twitter_article_tweet_consumption_enabled": True,
                "tweet_awards_web_tipping_enabled": False,
                "responsive_web_grok_show_grok_translated_post": False,
                "responsive_web_grok_analysis_button_from_backend": True,
                "creator_subscriptions_quote_tweet_preview_enabled": False,
                "freedom_of_speech_not_reach_fetch_enabled": True,
                "standardized_nudges_misinfo": True,
                "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
                "longform_notetweets_rich_text_read_enabled": True,
                "longform_notetweets_inline_media_enabled": True,
                "responsive_web_grok_image_annotation_enabled": True,
                "responsive_web_enhance_cards_enabled": False
            }
        }

        
        try:
            response = await self.client.post(
                url, 
                headers=self.headers,
                cookies=self.cookies,
                json=data
            )
            
            if response.status_code != 200:
                self._log(f"Auth verification failed with status code: {response.status_code}")
                return False
                
            # Check if we got a valid home timeline response
            data = response.json()
            if "data" in data and "home" in data["data"]:
                self._log("Authentication verified successfully!")
                return True
            
            self._log(f"Auth verification failed with response: {data}")
            return False
            
        except Exception as e:
            self._log(f"Auth verification error: {str(e)}")
            return False
    
    def check_auth(self):
        """Check if the client is authenticated."""
        try:
            resp = self.session.get("https://twitter.com/i/api/1.1/account/verify_credentials.json", timeout=10)
            return resp.status_code == 200
        except Exception:
            return False
    ''' 
    async def search_tweets(self, query, index, since_date=None,count: int = 100):
        """Search tweets using GraphQL API."""
        if since_date is None:
            # Get yesterday's date
            since_date = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
        print(f"[INFO] [Client {index}] Searching for tweets: {query} since {since_date}")
        
        search_query = f"{query} since:{since_date}"
        
        if not await self.verify_auth():
            return None

        # GraphQL operation ID + name for SearchTimeline
        url = f"{self.GQL_URL}/fL2MBiqXPk5pSrOS5ACLdA/SearchTimeline"

        # Mirror exactly what the browser sends as query params
        params = {
            "variables": json.dumps({
                "rawQuery": search_query,
                "count": count,
                "querySource": "recent_search_click",
                "product": "Latest"
            }),
            "features": json.dumps({
                "rweb_video_screen_enabled": False,
                "profile_label_improvements_pcf_label_in_post_enabled": True,
                "rweb_tipjar_consumption_enabled": True,
                "responsive_web_graphql_exclude_directive_enabled": True,
                "verified_phone_label_enabled": False,
                "creator_subscriptions_tweet_preview_api_enabled": True,
                "responsive_web_graphql_timeline_navigation_enabled": True,
                "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                "premium_content_api_read_enabled": False,
                "communities_web_enable_tweet_community_results_fetch": True,
                "c9s_tweet_anatomy_moderator_badge_enabled": True,
                "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
                "responsive_web_grok_analyze_post_followups_enabled": True,
                "responsive_web_jetfuel_frame": False,
                "responsive_web_grok_share_attachment_enabled": True,
                "articles_preview_enabled": True,
                "responsive_web_edit_tweet_api_enabled": True,
                "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
                "view_counts_everywhere_api_enabled": True,
                "longform_notetweets_consumption_enabled": True,
                "responsive_web_twitter_article_tweet_consumption_enabled": True,
                "tweet_awards_web_tipping_enabled": False,
                "responsive_web_grok_show_grok_translated_post": False,
                "responsive_web_grok_analysis_button_from_backend": True,
                "creator_subscriptions_quote_tweet_preview_enabled": False,
                "freedom_of_speech_not_reach_fetch_enabled": True,
                "standardized_nudges_misinfo": True,
                "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
                "longform_notetweets_rich_text_read_enabled": True,
                "longform_notetweets_inline_media_enabled": True,
                "responsive_web_grok_image_annotation_enabled": True,
                "responsive_web_enhance_cards_enabled": False
            })
        }

        try:
            # Use GET + params (not POST)
            response = await self.client.get(
                url,
                headers=self.headers,
                cookies=self.cookies,
                params=params
            )
            if response.status_code != 200:
                self._log(f"Failed to search tweets: {response.status_code}")
                return None

            data = response.json()
            timeline = (
                data
                .get("data", {})
                .get("search_by_raw_query", {})             # snscrape shows this wrapper :contentReference[oaicite:0]{index=0}
                .get("search_timeline")                     # then your actual timeline object :contentReference[oaicite:1]{index=1}
                .get("timeline")                     # then your actual timeline object :contentReference[oaicite:1]{index=1}
            )
            
            instructions = timeline.get("instructions", [])
            if not instructions:
                return []
            #print("Instructions",instructions)
            entries = instructions[0].get("entries", [])
            tweet_objs = []
            for entry in entries:
                # guard against non‑tweet entries
                if entry.get("content", {}).get("itemContent", {}).get("tweet_results"):
                    tweet_objs.append(self.map_entry_to_tweet_obj(entry))

            return tweet_objs
        except Exception as e:
            self._log(f"Error during search: {e}")
            return None
    '''       


    async def search_tweets(
        self,
        address_query,
        query: str,
        iteration:int,
        redis_client,
        index: int,
        since_date: str | None = None,
        per_page: int = 20,
        max_tweets: int = 50,
        pause_seconds: float = 5.0
    ) -> list[dict] | None:
        """Search tweets using GraphQL API with cursor‑based pagination.
        
        - per_page: number of tweets to request per page (server may cap it).
        - max_tweets: total tweets to accumulate before stopping.
        - pause_seconds: throttle between calls to avoid rate limits.
        """
        # 1) Determine since_date
        if since_date is None:
            since_date = (
                datetime.datetime.now() - datetime.timedelta(days=1)
            ).strftime("%Y-%m-%d")
        print(f"[INFO] [Client {index}] Searching for tweets: {query} since {since_date}")
        search_query = f"{query} since:{since_date}"
        
        # 2) Ensure authentication
        if not await self.verify_auth():
            return None

        url = f"{self.GQL_URL}/fL2MBiqXPk5pSrOS5ACLdA/SearchTimeline"
        all_tweets: list[dict] = []
        cursor: str | None = None
        time_count = 2 * (iteration + 1)
        plot_time = datetime.datetime.utcnow()
        # 3) Build the constant part of variables & features
        base_vars = {
            "rawQuery": search_query,
            "count": per_page,
            "querySource": "recent_search_click",
            "product": "Latest"
        }
        features = {
            "rweb_video_screen_enabled": False,
                "profile_label_improvements_pcf_label_in_post_enabled": True,
                "rweb_tipjar_consumption_enabled": True,
                "responsive_web_graphql_exclude_directive_enabled": True,
                "verified_phone_label_enabled": False,
                "creator_subscriptions_tweet_preview_api_enabled": True,
                "responsive_web_graphql_timeline_navigation_enabled": True,
                "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                "premium_content_api_read_enabled": False,
                "communities_web_enable_tweet_community_results_fetch": True,
                "c9s_tweet_anatomy_moderator_badge_enabled": True,
                "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
                "responsive_web_grok_analyze_post_followups_enabled": True,
                "responsive_web_jetfuel_frame": False,
                "responsive_web_grok_share_attachment_enabled": True,
                "articles_preview_enabled": True,
                "responsive_web_edit_tweet_api_enabled": True,
                "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
                "view_counts_everywhere_api_enabled": True,
                "longform_notetweets_consumption_enabled": True,
                "responsive_web_twitter_article_tweet_consumption_enabled": True,
                "tweet_awards_web_tipping_enabled": False,
                "responsive_web_grok_show_grok_translated_post": False,
                "responsive_web_grok_analysis_button_from_backend": True,
                "creator_subscriptions_quote_tweet_preview_enabled": False,
                "freedom_of_speech_not_reach_fetch_enabled": True,
                "standardized_nudges_misinfo": True,
                "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
                "longform_notetweets_rich_text_read_enabled": True,
                "longform_notetweets_inline_media_enabled": True,
                "responsive_web_grok_image_annotation_enabled": True,
                "responsive_web_enhance_cards_enabled": False
        }

        # 4) Loop pages until we hit max_tweets or run out
        while len(all_tweets) < max_tweets:
            vars_payload = base_vars.copy()
            if cursor:
                vars_payload["cursor"] = cursor
                print("Variables",cursor)

            params = {
                "variables": json.dumps(vars_payload),
                "features": json.dumps(features),
            }
            
            try:
                resp = await self.client.get(
                    url,
                    headers=self.headers,
                    cookies=self.cookies,
                    params=params
                )
                if resp.status_code != 200:
                    self._log(f"Failed to search tweets: {resp.status_code}")
                    if resp.status_code == 429:
                        reset_ts = int(resp.headers.get("x-rate-limit-reset", time.time() + pause_seconds))
                        wait_seconds = max(reset_ts - time.time(), pause_seconds)
                        self._log(f"Rate limit hit; sleeping for {wait_seconds:.1f}s")
                        await asyncio.sleep(wait_seconds)
                        pause_seconds = min(pause_seconds * 2, 300.0)  # cap at 5 minutes
                        continue
                    break

                data = resp.json()
                timeline = (
                    data.get("data", {})
                        .get("search_by_raw_query", {})
                        .get("search_timeline", {})
                        .get("timeline", {})
                )
                #print(f"Search fortimeline:{json.dumps(timeline, indent=2)}")
                instructions = timeline.get("instructions", [])
                if not instructions:
                    break

                # 4a) Extract tweets from all instruction entries
                page_tweets = []
                for instr in instructions:
                    for entry in instr.get("entries", []):
                        item = entry.get("content", {}).get("itemContent", {})
                        if item.get("tweet_results"):
                            page_tweets.append(self.map_entry_to_tweet_obj(entry))
                
                count = self.save_tweets_to_redis(address_query,index,page_tweets,time_count,plot_time,redis_client)
                if not page_tweets:
                    break
                all_tweets.extend(page_tweets)
                data_entries_count = 0
                for instr in instructions:
                    if instr.get("type") == "TimelineAddEntries":
                        entries = instr.get("entries", [])
                        for entry in entries:
                            content = entry.get("content", {})
                            if content and content.get("entryType") != "TimelineTimelineCursor":
                                data_entries_count += 1
                print("[ Paginantion ] Moving next [Returns]",data_entries_count)
                
                next_cursor = None
                for instr in instructions:
                    # Handle TimelineAddEntries type
                    if instr.get("type") == "TimelineAddEntries":
                        entries = instr.get("entries", [])
                        for entry_ in entries:
                            content = entry_.get("content", {})
                            entry_type = entry_.get("entryId", "")
                            
                            # Look for cursor entry
                            if "cursor-bottom" in entry_type or content.get("cursorType") == "Bottom":
                                value = content.get("value")
                                print("[Got Next Page Cursor]")
                                # Sometimes the cursor is nested deeper
                                if not value and isinstance(content.get("cursor"), dict):
                                    value = content.get("cursor", {}).get("value")
                                
                                if value:
                                    next_cursor = value
                                    break
                    
                    # Handle TimelineReplaceEntry type
                    elif instr.get("type") == "TimelineReplaceEntry":
                        entry_id = instr.get("entry_id_to_replace", "")
                        entry_ = instr.get("entry", {})
                        content = entry_.get("content", {})
                        
                        # Look for cursor entry
                        if "cursor-bottom" in entry_id or content.get("cursorType") == "Bottom":
                            value = content.get("value")
                            print("[Got Next Page Cursor]")
                            # Sometimes the cursor is nested deeper
                            if not value and isinstance(content.get("cursor"), dict):
                                value = content.get("cursor", {}).get("value")
                            
                            if value:
                                next_cursor = value
                                break
                    
                    if next_cursor:
                        break
                is_last_page = False
                if not next_cursor or data_entries_count == 0 :
                    self._log("====================No next page cursor found. Reached end of results.========================")
                    break  # no more pages
                
                # Only update cursor if we found a valid one
                cursor = next_cursor
                self._log(f"[Browser {index}] Next cursor: {cursor[:20]}... Search: {address_query}")

                # 4c) Throttle
                if pause_seconds:
                    await asyncio.sleep(pause_seconds)
                
                # Break if we've reached max tweets
                if len(all_tweets) >= max_tweets:
                    self._log(f"Reached max_tweets limit ({max_tweets})")
                    break
                    
            except Exception as e:
                self._log(f"Error during search: {str(e)}")
                # Continue with next attempt rather than breaking completely
                await asyncio.sleep(pause_seconds * 10)  # Wait longer after an error
                continue

        # 5) Return only up to max_tweets
        return all_tweets[:max_tweets]
    def map_entry_to_tweet_obj(self, entry):
        """
        Normalize a TimelineTweet entry into a flat dict.
        Supports two structures:
        1. result → tweet → legacy/core/user_results
        2. result → legacy/core/user_results (no intermediate "tweet" key)
        """
        # drill down to the “result” object
        result = entry["content"]["itemContent"]["tweet_results"]["result"]

        # if there’s an intermediate "tweet" layer, unwrap it
        tweet = result.get("tweet", result)

        # legacy fields (text, counts, timestamps) live under tweet["legacy"]
        legacy = tweet.get("legacy", {})

        # user fields always under tweet["core"]["user_results"]["result"]
        user = tweet.get("core", {}) \
                    .get("user_results", {}) \
                    .get("result", {})
        uleg = user.get("legacy", {})

        # normalize view count (new variants report it under result["views"]["count"])
        if result.get("views", {}).get("count") is not None:
            try:
                views = int(result["views"]["count"])
            except (ValueError, TypeError):
                views = 0
        else:
            views = legacy.get("view_count", 0)

        # parse the created_at timestamp
        created_at_str = legacy.get("created_at", "")
        try:
            dt = datetime.datetime.strptime(created_at_str, "%a %b %d %H:%M:%S %z %Y")
            post_time_iso = dt.isoformat()
        except (ValueError, TypeError):
            post_time_iso = created_at_str

        screen_name = uleg.get("screen_name", "")
        rest_id     = tweet.get("rest_id", "")

        return {
            "id":            rest_id,
            "username":      screen_name,
            "name":          uleg.get("name", ""),
            "tweet":         legacy.get("full_text", ""),
            "status":        f"https://x.com/{screen_name}/status/{rest_id}",
            "post_time":     created_at_str,
            "post_datetime": post_time_iso,
            "profile_image": uleg.get("profile_image_url_https", ""),
            "followers":     uleg.get("followers_count", 0),
            "friends":       uleg.get("friends_count", 0),
            "metrics": {
                "likes":   legacy.get("favorite_count", 0),
                "retweet": legacy.get("retweet_count", 0),
                "comment": legacy.get("reply_count",  0),
                "views":   views
            }
        }
    
    def save_tweets_to_redis(self, query,index, tweets, time_count, plot_time, redis_client):
        """Save tweets to Redis with the same format as the Selenium version."""
        if not redis_client:
            print(f"[ERROR] [Client {index}] No Redis client available")
            return
        
        redis_key_prefix = f"spltoken:{query}:"
        saved_count = 0
        
        for tweet in tweets:
            tweet_id = tweet.get("id")
            status_url = tweet.get("status")
            redis_key = f"{redis_key_prefix}{hash(status_url)}"
            
            try:
                existing_data = redis_client.get(redis_key)
            except Exception as e:
                print(f"[ERROR] [Client {index}] Redis error while getting key: {e}")
                continue
                
            # If tweet does not exist in Redis, store new data
            if existing_data is None:
                new_data = {
                    "tweet": tweet.get("tweet"),
                    "status": status_url,
                    "post_time": tweet.get("post_datetime"),
                    "profile_image": tweet.get("profile_image"),
                    "followers": tweet.get("followers"),
                    "friends": tweet.get("friends"),
                    "params": {
                        "likes": [str(tweet.get("metrics", {}).get("likes", 0))],
                        "retweet": [str(tweet.get("metrics", {}).get("retweet", 0))],
                        "comment": [str(tweet.get("metrics", {}).get("comment", 0))],
                        "views": [str(tweet.get("metrics", {}).get("views", 0))],
                        "time": [time_count],
                        "plot_time": [plot_time.isoformat() if isinstance(plot_time, datetime.datetime) else plot_time]
                    }
                }
                
                try:
                    redis_client.set(redis_key, json.dumps(new_data))
                    saved_count += 1
                except Exception as e:
                    print(f"[ERROR] [Client {index}] Redis error while saving new tweet: {e}")
            
            else:
                # If tweet exists, update its metrics if not in the same minute
                try:
                    existing_entry = json.loads(existing_data)
                except Exception as e:
                    print(f"[ERROR] [Client {index}] Error parsing existing Redis data: {e}")
                    continue
                    
                params = existing_entry.get("params", {})
                plot_time_list = params.get("plot_time", [])
                last_plot_time = None
                
                if plot_time_list:
                    try:
                        last_plot_time = datetime.datetime.fromisoformat(plot_time_list[-1])
                    except Exception:
                        last_plot_time = None
                        
                if not last_plot_time or not is_same_minute(last_plot_time, plot_time):
                    params["likes"] = safe_append_string(params.get("likes", []), str(tweet.get("metrics", {}).get("likes", 0)))
                    params["retweet"] = safe_append_string(params.get("retweet", []), str(tweet.get("metrics", {}).get("retweet", 0)))
                    params["comment"] = safe_append_string(params.get("comment", []), str(tweet.get("metrics", {}).get("comment", 0)))
                    params["views"] = safe_append_string(params.get("views", []), str(tweet.get("metrics", {}).get("views", 0)))
                    params["time"] = safe_append_int(params.get("time", []), time_count)
                    params["plot_time"] = safe_append_time(params.get("plot_time", []), plot_time)
                    existing_entry["params"] = params
                    
                    try:
                        redis_client.set(redis_key, json.dumps(existing_entry))
                        saved_count += 1
                    except Exception as e:
                        print(f"[ERROR] [Client {index}] Redis error while updating tweet: {e}")
        
        print(f"[INFO] [Client {index}] Saved/updated {saved_count} tweets in Redis")
        return saved_count

# --- Thread Functions ---

async def process_tweets(index, cookies, result_queue,ip_address,port_str,proxy_username,proxy_password,random_user_agent ):
    """Process tweets using the GraphQL API instead of Selenium."""
    port = get_random_port()
    update_session_status(index, port, "initializing")
    
    try:
        # Initialize GraphQL client
        proxy_url = f"http://{proxy_username}:{proxy_password}@{ip_address}:{port_str}"
        print(f"[INFO] Starting thread for browser {index} with UA: {random_user_agent[:30]}...On {proxy_url}")
        client = TwitterCookieAuth(debug=True,proxy=proxy_url,userAgent=random_user_agent)
        
        # Check if login works
        try:
            client.set_cookies(cookies)
            # Verify authentication
            is_authenticated = await client.verify_auth()
            print(f"Authentication status: {'Success' if is_authenticated else 'Failed'}")
        except Exception as e:
            print(f"Error: {str(e)}")
        ''' 
        if not client.check_auth():
            print(f"[ERROR] [Client {index}] Authentication failed")
            result_queue.put({"index": index, "success": False, "error": "Authentication failed"})
            update_session_status(index, port, "failed")
            return
        '''    
        print(f"[INFO] [Client {index}] Authentication successful")
        update_session_status(index, port, "active")
        
        # Initialize Redis client
        redis_client = init_redis_client()
        if not redis_client:
            print(f"[ERROR] [Client {index}] Failed to initialize Redis client")
            result_queue.put({"index": index, "success": False, "error": "Redis initialization failed"})
            update_session_status(index, port, "failed")
            return
        
        # Process queries
        global query_queue
        global active_query
        unmatched_queries = []
        query_array = []
        iteration = 0
        itt = 0
        
        while not shutdown_event.is_set():
            print(f"[INFO] [Client {index}], [ROUND]: {iteration}")
            
            # Get queries from queue
            try:
                size_x = query_queue.qsize()
                if size_x > 0:
                    for _ in range(size_x):
                        query_ = query_queue.get()
                        
                        if shutdown_event.is_set():
                            print(f"[INFO] [Client {index}] Received shutdown signal, exiting")
                            break
                            
                        # Handle removal requests
                        address_field = query_.get('address', '')
                        if address_field.startswith("remove_"):
                            addr_to_remove = address_field[len("remove_"):]
                            original_length = len(query_array)
                            query_array = [q for q in query_array if q.get('address', '') != addr_to_remove]
                            if len(query_array) < original_length:
                                print(f"[INFO] [Client {index}] Removed query with address: {addr_to_remove}")
                            else:
                                print(f"[INFO] [Client {index}] No query found with address: {addr_to_remove} to remove")
                            continue
                            
                        # Check if query is for this client
                        if query_.get('index') == index:
                            new_addr = query_.get('address', '')
                            # Avoid duplicates
                            if any(q.get('address', '') == new_addr for q in query_array):
                                print(f"[INFO] [Client {index}] Duplicate address detected, skipping: {new_addr}")
                            else:
                                if len(query_array) < 6:
                                    print(f"[INFO] [Client {index}] Found matching query: {query_}")
                                    query_array.append(query_)
                                    iteration = len(query_array)-1
                        else:
                            print(f"[INFO] [Client {index}] Non-matching query: {query_} Index {query_.get('index')}")
                            unmatched_queries.append(query_)
                    
                    # Put unmatched queries back in the queue
                    for item in unmatched_queries:
                        query_queue.put(item)
                    unmatched_queries.clear()
            
                # Prune stale: held >3h AND no tweets in last 1h
                
                for e in query_array:
                    print(f"[Client {index}] Tweet Latest Time B4 Prune: {query_array[iteration]['last_activity']} {query_array[iteration]['address']}")
                    if isinstance(e['added_at'], str):
                        e['added_at'] = datetime.datetime.fromisoformat(e['added_at'].replace('Z', '+00:00'))
                    if isinstance(e['last_activity'], str):
                        e['last_activity'] = datetime.datetime.fromisoformat(e['last_activity'].replace('Z', '+00:00'))

                now = datetime.datetime.now(datetime.timezone.utc)
                before = len(query_array)
                query_array[:] = [e for e in query_array if not (
                    #now - e['added_at'] > datetime.timedelta(hours=3) and
                     now - e['last_activity'] > datetime.timedelta(hours=1)
                )]
                if len(query_array) < before:
                    print(f"[INFO][Client {index}] Pruned {before-len(query_array)} Interation value {iteration} stale entries at {now.isoformat()}")
                    iteration = len(query_array)-1
                    
                # Process queries
                if len(query_array) > 0:
                    print(f"[INFO] [Client {index}] Full Query Array: {query_array} Processing")
                    now = datetime.datetime.now()
                    
                    # Get yesterday's date as "YYYY-MM-DD"
                    yesterday = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
                    
                    update_session_status(index, port, f"Searching Count: {iteration} of ArraySize {len(query_array)} Total Iteration {itt}")
                    
                    # Alternate between "Top" and "Latest" modes
                    #search_mode = (itt + iteration) % 2
                    #if search_mode == 0:
                    update_session_status(index, port, f"Searching Count: {iteration} of ArraySize {len(query_array)} Total Iteration {itt} [Top]")
                    search_query = f"{query_array[iteration]['address']} OR ${query_array[iteration]['symbol']}"
                    
                    print(f"[INFO] [Client {index}] Starting search for: {search_query}")
                    
                    # Search for tweets
                    search_results = await client.search_tweets(query_array[iteration]['address'],search_query,iteration,redis_client, index,since_date=yesterday)
                    print(f"Search for Meme coin: {json.dumps(search_results, indent=2)}")
                    if search_results:
                        # Save tweets to Redis
                        post_time_str = search_results[0].get('post_time')
                        if post_time_str:
                            try:
                                post_time = datetime.datetime.strptime(post_time_str, '%a %b %d %H:%M:%S %z %Y')
                                print(f"[Client {index}] Tweet Latest Time is: {post_time} ")
                                query_array[iteration]['last_activity'] = post_time
                            except ValueError as e:
                                print(f"Error parsing post_time: {e}")
                                #entry['last_activity'] = datetime.datetime.utcnow()
                        
                        print(f"[Browser {index}]: Search for {query_array[iteration]['address']} Completed!!")
                        time_count = 2 * (iteration + 1)
                        plot_time = datetime.datetime.utcnow()
                        saved_count = client.save_tweets_to_redis(
                            query_array[iteration]['address'], 
                            index,
                            search_results,
                            time_count,
                            plot_time,
                            redis_client
                        )
                        
                        print(f"[INFO] [Client {index}] Search completed successfully. Saved {saved_count} tweets.")
                        result_queue.put({"index": index, "success": True, "error": ""})
                    else:
                        print(f"[WARNING] [Client {index}] No tweets found or search failed")
                        result_queue.put({"index": index, "success": False, "error": "No tweets found"})
                else:
                    print(f"[Client {index}] Empty Array")
                # Increment iteration counters
                iteration += 1
                itt += 1
                if iteration > (len(query_array) - 1) or iteration > 5:
                    print(f"[INFO] [Client {index}] Reached max iterations. Resetting.")
                    iteration = 0
                
                # Sleep between iterations
                time.sleep(30)
                
            except Exception as e:
                print(f"[ERROR] [Client {index}] Error in processing loop: {e}")
                time.sleep(5)
    
    except Exception as e:
        print(f"[ERROR] [Client {index}] Unhandled exception: {e}")
        result_queue.put({"index": index, "success": False, "error": str(e)})
    
    finally:
        # Cleanup
        await client.close()
        update_session_status(index, port, "inactive")

def run_scraper(cookies, index, result_queue,ip_address,port_str,proxy_username,proxy_password,random_user_agent,  semaphore):
    """Run a scraper with proper resource management."""
    try:
        print(f"[INFO] [Client {index}] Starting scraper")
        
        asyncio.run(process_tweets(index, cookies, result_queue,ip_address,port_str,proxy_username,proxy_password,random_user_agent, ))
    except Exception as e:
        print(f"[ERROR] [Client {index}] Error in run_scraper: {e}")
        result_queue.put({"index": index, "success": False, "error": str(e)})
    finally:
        semaphore.release()
        update_session_status(index, 0, "END")
        print(f"[INFO] [Client {index}] Released semaphore")

def send_query_to_thread(index, query_data):
    """Send data to a specific thread by tagging it with the thread index."""
    global query_queue
    # Tag the query with the target thread index
    tagged_query = query_data.copy()
    
    # Add to the queue
    query_queue.put(tagged_query)
    print(f"[INFO] Sent query to thread {index}: {tagged_query}")

def kill_all_active_threads():
    """Signal all threads to shut down and clean up resources."""
    # Signal threads to shutdown
    shutdown_event.set()
    
    # Give threads time to notice the shutdown signal
    time.sleep(1)
    
    current = threading.current_thread()
    for t in threading.enumerate():
        if t is not current and t.daemon:
            print(f"[INFO] Waiting for thread {t.name} to finish...")
            t.join(timeout=5)
    
    # Reset shutdown event for future threads
    shutdown_event.clear()
    
    print("[INFO] All threads have been terminated")

# --- Main Function ---

def main():
    """Main function that orchestrates the Twitter scraping process using GraphQL API."""
    
    kill_all_active_threads()
    print("\n[INFO] ===== Starting Twitter GraphQL scraper =====\n")
    
    # Initialize variables
    addr_array = []
    cook_array = []
    prx_array = []
    global query_queue
    result_queue = queue.Queue()
    
    # Limit concurrent scrapers
    semaphore = threading.Semaphore(MAX_CONCURRENT_SCRAPERS)
    threads = []
    active_threads = 0
    
    # Reset session status
    update_session_status(0, 0, "NEW")
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
                cookies_array = read_json_file("datacenter/cookies.json")
                proxies_array = read_json_file("datacenter/proxies.json")
                
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
                    cookie_dict = {
                        "auth_token": entry.get("auth_token"),
                        "ct0": entry.get("ct0")
                    }
                    if cookie_dict not in cook_array:
                        cook_array.append(cookie_dict)
                        print(f"[INFO] Added new cookie: {{...auth_token: {cookie_dict['auth_token'][:8]}..., ct0: {cookie_dict['ct0'][:8]}...}}")
                
                # Update proxies list
                for entry in proxies_array:
                    if entry["proxies"] not in prx_array:
                        prx_array.append(entry["proxies"])
                        print("[INFO] Added new proxy")
                # Print current status
                print(f"[INFO] Status - Addresses: {len(addr_array)}, Cookies: {len(cook_array)}, Active threads: {active_threads}")
                
                # Start new threads if we have available cookies and semaphores
                if index < len(cook_array) and active_threads < MAX_CONCURRENT_SCRAPERS:
                    # Get cookie
                    random_user_agent = user_agents[index % len(user_agents)]
                    the_cookie = cook_array[index % len(cook_array)]
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

                    # Acquire semaphore and start thread
                    print(f"[INFO] Acquiring semaphore for client {index}...")
                    semaphore.acquire()
                    active_threads += 1
                    
                    print(f"[INFO] Starting thread for client {index}...")
                    t = threading.Thread(
                        target=run_scraper,
                        args=(the_cookie, index, result_queue,ip_address,port_str,proxy_username,proxy_password,random_user_agent, semaphore)
                    )
                    t.daemon = True
                    t.start()
                    threads.append(t)
                    
                    print(f"[INFO] Started thread for client {index}")
                    index += 1
                    time.sleep(5)  # Stagger thread starts
                
                # Check for thread results
                try:
                    result = result_queue.get(timeout=1)
                    
                    if result["success"]:
                        print(f"[INFO] Client {result['index']} completed successfully")
                    else:
                        print(f"[WARNING] Client {result['index']} failed: {result['error']}")
                
                except queue.Empty:
                    pass
                
                # Exit condition - adjust as needed
                if index >= 15:  # For testing, just run 15 clients
                    print("[INFO] Test limit reached, stopping after 15 clients")
                    break
                
                time.sleep(1)  # Prevent CPU hogging
            
            except Exception as e:
                print(f"[ERROR] Error in main loop: {e}")
                time.sleep(10)  # Wait before retrying
        
        # Wait for all threads to finish
        print("[INFO] Waiting for all threads to complete...")
        for t in threads:
            t.join(timeout=30)
        
        print("[INFO] All threads have completed or timed out")
        
    except KeyboardInterrupt:
        print("\n[WARNING] Keyboard interrupt received. Shutting down gracefully...")
        kill_all_active_threads()
        
    except Exception as e:
        print(f"[ERROR] Unhandled exception in main: {e}")
        
    finally:
        # Final cleanup
        print("[INFO] Cleanup resources...")
        kill_all_active_threads()
        
        # Update final status
        update_session_status(0, 0, "SHUTDOWN")
        print("[INFO] ===== Twitter GraphQL scraper finished =====")


if __name__ == "__main__":
    main()