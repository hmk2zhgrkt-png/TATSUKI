"""Read public BoatraceCSV snapshots for one race. No guessed values."""
import csv
import io
from functools import lru_cache
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://boatracecsv.github.io/data/programs"
VENUE_CODES = {"桐生":1,"戸田":2,"江戸川":3,"平和島":4,"多摩川":5,"浜名湖":6,"蒲郡":7,"常滑":8,"津":9,"三国":10,"びわこ":11,"住之江":12,"尼崎":13,"鳴門":14,"丸亀":15,"児島":16,"宮島":17,"徳山":18,"下関":19,"若松":20,"芦屋":21,"福岡":22,"唐津":23,"大村":24}

@lru_cache(maxsize=48)
def _day_rows(kind, day):
    url = f"{BASE}/{kind}/{day:%Y/%m/%d}.csv"
    req = Request(url, headers={"User-Agent":"TATSUKI-research/0.1"})
    with urlopen(req, timeout=12) as response:
        body = response.read().decode("utf-8-sig")
    return {row.get("レースコード", "").strip(): row
            for row in csv.DictReader(io.StringIO(body))}, url

def _get_row(kind, day, race_code):
    rows, url = _day_rows(kind, day)
    return rows.get(race_code), url

def load_race(day, venue, race_number):
    if not isinstance(day, date) or venue not in VENUE_CODES or not 1 <= race_number <= 12:
        raise ValueError("開催日・競艇場・レース番号を確認してください")
    code = f"{day:%Y%m%d}{VENUE_CODES[venue]:02d}{race_number:02d}"
    # The daily CSV is generated retrospectively. For upcoming races, use
    # the same original boatcast per-race TSV directly instead of waiting.
    try:
        cards, cards_url = _get_row("race_cards", day, code)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        cards, cards_url = None, None
    if cards is None:
        return _load_live_race(day, venue, race_number)
    try:
        waku, waku_url = _get_row("waku10", day, code)
    except (HTTPError, URLError, TimeoutError):
        waku, waku_url = None, None
    result = []
    for n in range(1, 7):
        p = f"艇{n}_"
        def number(source, key):
            raw = (source or {}).get(p + key, "")
            try:
                return float(raw) if str(raw).strip() else None
            except ValueError:
                return None
        history = []
        if waku:
            for i in range(1, 11):
                val = waku.get(f"{p}過去{i}走_着順", "").strip()
                if val:
                    history.append(val)
        result.append({
            "号艇":n, "選手":cards.get(p+"選手名","").strip(),
            "登録番号":cards.get(p+"登録番号","").strip(),
            "全国勝率":number(cards,"全国勝率"),
            "当地勝率":number(cards,"当地勝率"),
            "平均ST":number(cards,"全国平均ST"),
            "モーター2連対率":number(cards,"モーター2連対率"),
            "モーター3連対率":number(cards,"モーター3連対率"),
            "枠番別勝率":number(waku,"枠番別勝率"),
            "枠番別平均ST":number(waku,"枠番別平均ST"),
            "過去10走": " ".join(history) if history else "未取得",
        })
    return result, [u for u in (cards_url,waku_url) if u]

def _fetch_boatcast_tsv(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; TATSUKI/1.0)"})
    try:
        with urlopen(req, timeout=12) as response:
            body = response.read().decode("utf-8-sig")
    except HTTPError as exc:
        if exc.code in (403, 404):
            return None
        raise
    if not body.lstrip().startswith("data="):
        return None
    lines = body.splitlines()
    if len(lines) < 8 or lines[1].split("\t")[0].strip() != "1":
        return None
    return [line.split("\t") for line in lines[2:8]]


def _load_live_race(day, venue, race_number):
    jo = VENUE_CODES[venue]
    prefix = f"https://race.boatcast.jp/hp_txt/{jo:02d}/"
    suffix = f"{day:%Y%m%d}_{jo:02d}_{race_number:02d}.txt"
    card_url = prefix + "bc_j_str3_" + suffix
    waku_url = prefix + "bc_j_waku10_" + suffix
    boats = _fetch_boatcast_tsv(card_url)
    if not boats or len(boats) != 6 or any(len(b) < 25 for b in boats):
        return _load_official_race(day, venue, race_number)
    try:
        waku = _fetch_boatcast_tsv(waku_url)
    except (HTTPError, URLError, TimeoutError):
        waku = None
    def num(value):
        try:
            return float(value.strip()) if value.strip() not in ("", "-") else None
        except (ValueError, AttributeError):
            return None
    rows = []
    for i, b in enumerate(boats):
        w = waku[i] if waku and len(waku) == 6 and len(waku[i]) >= 4 else None
        history = []
        if w:
            for j in range(10):
                pos = 4 + j * 3
                if pos < len(w) and w[pos].strip():
                    history.append(w[pos].strip().translate(str.maketrans("１２３４５６", "123456")))
        rows.append({
            "号艇": i + 1, "選手": b[1].replace("　", "").strip(),
            "登録番号": b[0].strip(), "全国勝率": num(b[10]),
            "当地勝率": num(b[13]), "平均ST": num(b[9]),
            "モーター2連対率": num(b[18]), "モーター3連対率": num(b[19]),
            "枠番別勝率": num(w[1]) if w else None,
            "枠番別平均ST": num(w[2]) if w else None,
            "過去10走": " ".join(history) if history else "未取得",
        })
    return rows, [card_url] + ([waku_url] if waku else [])


def _load_official_race(day, venue, race_number):
    """Third fallback: BOAT RACE official race-list page, no fabricated values."""
    import re
    from bs4 import BeautifulSoup
    jo = VENUE_CODES[venue]
    url = (f"https://www.boatrace.jp/owpc/pc/race/racelist"
           f"?hd={day:%Y%m%d}&jcd={jo:02d}&rno={race_number}")
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept-Language": "ja,en;q=0.9", "Referer": "https://www.boatrace.jp/"})
    try:
        with urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise LookupError(f"公開CSV・BOATCAST・公式出走表の取得に失敗しました。公式サイトへの接続: {type(exc).__name__}") from exc
    soup = BeautifulSoup(html, "html.parser")
    def values(cell):
        return [x.strip() for x in cell.stripped_strings if x.strip()]
    def numeric(value):
        try:
            return float(value.replace("%", "").replace(",", "").strip())
        except (ValueError, AttributeError):
            return None
    found = {}
    for table in soup.select("table"):
        for tr in table.select("tbody"):
            cells = tr.select("td")
            if len(cells) < 7:
                continue
            first = "".join(cells[0].stripped_strings).translate(str.maketrans("１２３４５６", "123456"))
            if first not in ("1", "2", "3", "4", "5", "6"):
                continue
            # Official race list: lane, photo, racer, F/L/ST, national,
            # local, motor, boat, session history.
            racer = cells[2] if len(cells) > 2 else None
            if racer is None:
                continue
            registration = re.search(r"(?<!\d)(\d{4})(?!\d)", racer.get_text(" ", strip=True))
            name_link = racer.select_one("a")
            name = name_link.get_text(" ", strip=True).replace(" ", "").replace("　", "") if name_link else ""
            if not registration or not name:
                continue
            def cell_num(index, position):
                if index >= len(cells):
                    return None
                nums = [numeric(v) for v in values(cells[index])]
                nums = [n for n in nums if n is not None]
                return nums[position] if len(nums) > position else None
            st = None
            if len(cells) > 3:
                for value in values(cells[3]):
                    n = numeric(value)
                    if n is not None and 0 <= n <= 1:
                        st = n
                        break
            found[int(first)] = {
                "号艇": int(first), "選手": name, "登録番号": registration.group(1),
                "全国勝率": cell_num(4, 0), "当地勝率": cell_num(5, 0),
                "平均ST": st, "モーター2連対率": cell_num(6, 1),
                "モーター3連対率": None, "枠番別勝率": None,
                "枠番別平均ST": None, "過去10走": "未取得",
            }
        if len(found) == 6:
            break
    if len(found) != 6 or any(not found[n]["選手"] for n in range(1, 7)):
        raise LookupError(f"公式出走表の解析結果: {len(found)}/6艇、HTML長: {len(html)}文字、table数: {len(soup.select('table'))}。未公開・アクセス制限・ページ構造変更の可能性があります。")
    return [found[n] for n in range(1, 7)], [url]


def fetch_actual_result(race_key):
    """Return confirmed 3-ren-tan finish or None if the public snapshot is not yet present."""
    import re
    match = re.fullmatch(r"(\d{8})-(\d{2})-(\d{2})", race_key)
    if not match:
        raise ValueError("保存されたレースIDが不正です")
    ymd, venue, race = match.groups()
    day = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
    code = ymd + venue + race
    url = f"https://boatracecsv.github.io/data/results/realtime/{day:%Y/%m/%d}.csv"
    req = Request(url, headers={"User-Agent":"TATSUKI-research/0.1"})
    try:
        with urlopen(req, timeout=12) as response:
            body = response.read().decode("utf-8-sig")
    except HTTPError as exc:
        if exc.code == 404:
            return None, url
        raise
    for row in csv.DictReader(io.StringIO(body)):
        if row.get("レースコード", "").strip() != code:
            continue
        places = [str(row.get(f"{n}着_艇番", "")).strip() for n in (1, 2, 3)]
        if any(v not in ("1", "2", "3", "4", "5", "6") for v in places):
            raise ValueError("確定した上位3艇を取得できません。欠場・同着などを確認してください")
        if len(set(places)) != 3:
            raise ValueError("着順に重複があります。自動登録は中止しました")
        return "-".join(places), url
    return None, url
