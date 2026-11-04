"""Backtest result lookup with independent official confirmation."""
from datetime import date
from history_data import VENUE_CODES, fetch_actual_result
from official_verification import verify_official_trifecta

def result_for_race(day,venue,race):
    key=f"{day:%Y%m%d}-{VENUE_CODES[venue]:02d}-{race:02d}"
    actual,url=fetch_actual_result(key)
    if actual is None:
        return None,url
    verify_official_trifecta(key,actual)
    return actual,url
