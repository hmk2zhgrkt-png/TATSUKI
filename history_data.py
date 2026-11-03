"""Read public BoatraceCSV snapshots for one race. No guessed values."""
import csv
import io
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://boatracecsv.github.io/data/programs"
VENUE_CODES = {"桐生":1,"戸田":2,"江戸川":3,"平和島":4,"多摩川":5,"浜名湖":6,"蒲郡":7,"常滑":8,"津":9,"三国":10,"びわこ":11,"住之江":12,"尼崎":13,"鳴門":14,"丸亀":15,"児島":16,"宮島":17,"徳山":18,"下関":19,"若松":20,"芦屋":21,"福岡":22,"唐津":23,"大村":24}

def _get_row(kind, day, race_code):
    url = f"{BASE}/{kind}/{day:%Y/%m/%d}.csv"
    req = Request(url, headers={"User-Agent":"TATSUKI-research/0.1"})
    with urlopen(req, timeout=12) as response:
        body = response.read().decode("utf-8-sig")
    for row in csv.DictReader(io.StringIO(body)):
        if row.get("レースコード", "").strip() == race_code:
            return row, url
    return None, url

def load_race(day, venue, race_number):
    if not isinstance(day, date) or venue not in VENUE_CODES or not 1 <= race_number <= 12:
        raise ValueError("開催日・競艇場・レース番号を確認してください")
    code = f"{day:%Y%m%d}{VENUE_CODES[venue]:02d}{race_number:02d}"
    cards, cards_url = _get_row("race_cards", day, code)
    if cards is None:
        raise LookupError("この日の出走表データが見つかりません。データの公開待ち、または非開催の可能性があります。")
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

def fetch_actual_result(race_key):
    """Return confirmed 3-ren-tan finish or None if the public snapshot is not yet present."""
    import re
    match = re.fullmatch(r"(\\d{8})-(\\d{2})-(\\d{2})", race_key)
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
