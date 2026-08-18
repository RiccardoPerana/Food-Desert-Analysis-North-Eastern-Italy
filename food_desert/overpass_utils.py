"""
overpass_utils.py
-----------------
Reliable querying of the public Overpass API.

Used ONLY by diagnostics.py. The analysis pipeline reads everything from the
local .osm.pbf extract, deliberately -- public Overpass mirrors return
technically-successful but silently incomplete responses under load, which is
the one failure mode this project cannot tolerate. The diagnostic script
queries the live API precisely because an independent second opinion is the
point of a diagnostic.

Mirrors also go through genuine outages. Retrying the same broken mirror five
times accomplishes nothing, so each attempt rotates to the next mirror in
config.OVERPASS_MIRRORS.
"""

import time

import overpy

from . import config


def query_with_retry(query, max_retries=6, base_delay_sec=6):
    """
    Runs an Overpass query, rotating across config.OVERPASS_MIRRORS on each
    attempt and backing off progressively between them.

    Raises immediately on OverpassBadRequest, which means the query syntax
    itself is invalid -- retrying a malformed query against five more mirrors
    only wastes time and obscures the real error. Any other exception is
    treated as transient; the last one is re-raised if every retry is exhausted.
    """
    mirrors = config.OVERPASS_MIRRORS
    last_exception = None

    for attempt in range(1, max_retries + 1):
        mirror = mirrors[(attempt - 1) % len(mirrors)]
        api = overpy.Overpass(url=mirror)
        try:
            return api.query(query)
        except overpy.exception.OverpassBadRequest:
            raise  # a real bug in our query -- surface it immediately
        except Exception as e:
            last_exception = e
            wait = base_delay_sec * attempt
            print(f"[RETRY {attempt}/{max_retries}] Mirror {mirror} failed "
                  f"({type(e).__name__}: {e}), waiting {wait}s, trying next mirror...")
            time.sleep(wait)

    print(f"[FAILED] Overpass query exhausted all {max_retries} retries "
          f"across every mirror.")
    raise last_exception
