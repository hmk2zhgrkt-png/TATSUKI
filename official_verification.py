"""Conservative official result cross-check: no automatic write unless confirmed."""
import re
from urllib.request import Request, urlopen
from bs4 import BeautifulSoup

from functools import lru_cache

@lru_cache(maxsize=1024)
def official_trifecta(race_key):
    """Extract exactly one official trifecta; return (finish, URL)."""
    match = re.fullmatch(r"(\\d{8})-(\\d{2})-(\\d{2})", race_key)
    if not match:
        raise ValueError("レースIDが不正です")
    day, venue, race = match.groups()
    url = ("https://www.boatrace.jp/owpc/pc/race/raceresult"
           f"?rno={int(race)}&jcd={venue}&hd={day}")
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=12) as response:
        html = response.read()
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    sections = re.findall(r"3連単(.{0,180}?)(?=3連複|2連単|2連複|拡連複|単勝|複勝|$)", text)
    candidates = set()
    for section in sections:
        for a,b,c in re.findall(r"(?<!\\d)([1-6])\\s*[-－→]\\s*([1-6])\\s*[-－→]\\s*([1-6])(?!\\d)", section):
            if len({a,b,c}) == 3:
                candidates.add(f"{a}-{b}-{c}")
    if len(candidates) != 1:
        raise ValueError("公式サイトの3連単を確実に読み取れません")
    return next(iter(candidates)), url

def verify_official_trifecta(race_key, csv_result):
    official, url = official_trifecta(race_key)
    if official != csv_result:
        raise ValueError(f"結果不一致：CSV {csv_result}／公式 {official}。登録しません")
    return url
