"""Cached daily race results for historical validation."""
import csv
import io
from datetime import date
from functools import lru_cache
from urllib.error import HTTPError
from urllib.request import Request, urlopen

@lru_cache(maxsize=60)
def daily_results(day):
    url=f"https://boatracecsv.github.io/data/results/realtime/{day:%Y/%m/%d}.csv"
    try:
        with urlopen(Request(url,headers={"User-Agent":"TATSUKI-research/0.1"}),timeout=12) as response:
            content=response.read().decode("utf-8-sig")
    except HTTPError as exc:
        if exc.code==404:
            return {},url
        raise
    rows={r.get("レースコード","").strip():r for r in csv.DictReader(io.StringIO(content))}
    return rows,url

def cached_result(day,venue_code,race):
    rows,url=daily_results(day)
    code=f"{day:%Y%m%d}{venue_code:02d}{race:02d}"
    row=rows.get(code)
    if row is None:
        return None,url
    places=[str(row.get(f"{n}着_艇番","")).strip() for n in (1,2,3)]
    if any(p not in ("1","2","3","4","5","6") for p in places) or len(set(places))!=3:
        raise ValueError("着順の形式が不正（欠場・同着等の可能性）")
    return "-".join(places),url
