"""Historical race backtest using only pre-race race-card and same-frame history fields."""
import itertools
from datetime import timedelta
from history_data import load_race
from official_verification import verify_official_trifecta

def rank_race(rows, venue, course_rates):
    base = {}
    adjusted = {}
    for row in rows:
        boat = int(row["号艇"])
        rate, st, motor = (row.get(k) for k in ("全国勝率","平均ST","モーター2連対率"))
        if any(x is None for x in (rate, st, motor)):
            raise ValueError("予想時点の必須データ不足")
        if not (0<=rate<=10 and 0<=st<=1 and 0<=motor<=100):
            raise ValueError("入力データ範囲外")
        score = max(.001,.35*rate/10+.15*(1-st)+.20*motor/100+.10+.20*course_rates[venue][boat-1]/50)
        base[boat] = score
        finishes = [int(x) for x in str(row.get("過去10走","")).split() if x in ("1","2","3","4","5","6")]
        factor = 1 + max(-.06,min(.06,.12*(sum(x<=3 for x in finishes)/len(finishes)-.5))) if len(finishes)>=5 else 1
        adjusted[boat] = score*factor
    if set(base)!=set(range(1,7)):
        raise ValueError("6艇データ不足")
    def ranks(scores):
        total = sum(scores.values())
        values=[]
        for a,b,c in itertools.permutations(range(1,7),3):
            p = scores[a]/total*scores[b]/(total-scores[a])*scores[c]/(total-scores[a]-scores[b])
            values.append((f"{a}-{b}-{c}",p))
        values.sort(key=lambda item:(-item[1],item[0]))
        return {name:i for i,(name,_) in enumerate(values,1)}
    return ranks(base),ranks(adjusted)

def backtest(day, venue, count, course_rates, progress=None, result_lookup=None, race_numbers=None):
    from app_backtest_results import result_for_race
    from concurrent.futures import ThreadPoolExecutor, as_completed
    numbers = list(race_numbers) if race_numbers is not None else list(range(1,count+1))
    if not numbers or any(not 1 <= n <= 12 for n in numbers):
        raise ValueError("検証レース番号が不正です")
    # Prevent future-dated venue statistics from entering historical evaluations.
    # The supplied venue rates include results through 2026-10-31.
    from datetime import date
    if True:
        course_rates = {v:[50.0]*6 for v in course_rates}
    records=[]
    def evaluate(race):
        item={"日付":str(day),"開催場":venue,"レース":race}
        phase="出走表の読み込み"
        try:
            rows,_=load_race(day,venue,race)
            phase="予想順位の計算"
            # waku10 historical snapshots lack a verified as-of timestamp.
            # Keep original rows for display, but exclude that feature from ranking.
            safe_rows = [{**r, "過去10走":"未取得"} for r in rows]
            old,new=rank_race(safe_rows,venue,course_rates)
            phase="結果の取得"
            outcome=(result_lookup(day,venue,race) if result_lookup else result_for_race(day,venue,race))
            phase="結果の解釈"
            actual, source = outcome[:2]
            if len(outcome) > 2:
                item["照合元"] = outcome[2]
            if not actual:
                item["状態"]="結果CSVに該当レースなし"
            else:
                phase="着順と予想の照合"
                item.update({"状態":"照合済み","実際の3連単":actual,"_入力データ":safe_rows,
                             "旧順位":old[actual],"新順位":new[actual],
                             "旧Top5":old[actual]<=5,"新Top5":new[actual]<=5,
                             "旧Top10":old[actual]<=10,"新Top10":new[actual]<=10})
        except LookupError as exc:
            item["状態"]=f"{phase}：{type(exc).__name__}: {exc}"
        except Exception as exc:
            item["状態"]=f"{phase}：{type(exc).__name__}: {exc}"
        return race, item
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(evaluate, race) for race in numbers]
        for finished, future in enumerate(as_completed(futures), 1):
            records.append(future.result())
            if progress:
                progress(finished / len(numbers))
    return [item for race, item in sorted(records)]
