"""
overpass_utils.py
------------------
Shared helper for querying the public Overpass API reliably.

Public Overpass mirrors go through periods of real outages/instability,
not just "busy" -- producing 500s, 404s, dropped connections, or timeouts
in no particular pattern. Rather than retrying the SAME mirror five times
(which just wastes time if that specific server is down), this rotates
through config.OVERPASS_MIRRORS on each retry attempt, so a temporarily
broken mirror gets skipped in favor of a working one.
"""

import time
import overpy

import config


def query_with_retry(query, max_retries=6, base_delay_sec=6):
    """
    Runs an Overpass query, rotating across config.OVERPASS_MIRRORS on
    each retry attempt. Raises immediately (no retry) only on
    OverpassBadRequest, which means the query syntax itself is invalid.
    Raises the last transient exception if all retries are exhausted.
    """
    mirrors = config.OVERPASS_MIRRORS
    last_exception = None

    for attempt in range(1, max_retries + 1):
        mirror = mirrors[(attempt - 1) % len(mirrors)]
        api = overpy.Overpass(url=mirror)
        try:
            return api.query(query)
        except overpy.exception.OverpassBadRequest:
            raise  # real bug in our query -- don't retry, surface it immediately
        except Exception as e:
            last_exception = e
            wait = base_delay_sec * attempt
            print(f"[RETRY {attempt}/{max_retries}] Mirror {mirror} failed "
                  f"({type(e).__name__}: {e}), waiting {wait}s, trying next mirror...")
            time.sleep(wait)

    print(f"[FAILED] Overpass query exhausted all {max_retries} retries across all mirrors.")
    raise last_exception
