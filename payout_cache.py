"""Historical trifecta payouts (yen per 100-yen ticket)."""
import csv
import io
from functools import lru_cache
from urllib.error import HTTPError
from urllib.request import Request, urlopen

@lru_cache(maxsize=120)
def daily_payouts(day):
    url = f"https://boatracecsv.github.io/data/results/payouts/{day:%Y/%m/%d}.csv"
    try:
        with urlopen(Request(url, headers={"User-Agent":"TATSUKI-research/0.1"}), timeout=12) as response:
            data = response.read().decode("utf-8-sig")
    except HTTPError as exc:
        if exc.code == 404:
            return {}, url
        raise
    return {row.get("レースコード","").strip():row for row in csv.DictReader(io.StringIO(data))}, url

def trifecta_payout(day, venue_code, race, actual):
    rows, url = daily_payouts(day)
    code = f"{day:%Y%m%d}{venue_code:02d}{race:02d}"
    row = rows.get(code)
    if not row:
        return None, url
    combo = str(row.get("3連単_組番","")).strip()
    raw = str(row.get("3連単_払戻金","")).replace(",","").replace("円","").strip()
    if combo != actual or not raw.isdigit() or int(raw) <= 0:
        return None, url
    return int(raw), url
