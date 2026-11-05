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

def backtest(day, venue, count, course_rates, progress=None, result_lookup=None):
    from app_backtest_results import result_for_race
    from concurrent.futures import ThreadPoolExecutor, as_completed
    records=[]
    def evaluate(race):
        item={"日付":str(day),"開催場":venue,"レース":race}
        try:
            rows,_=load_race(day,venue,race)
            old,new=rank_race(rows,venue,course_rates)
            actual,source=(result_lookup(day,venue,race) if result_lookup else result_for_race(day,venue,race))
            if not actual:
                item["状態"]="結果未公開"
            else:
                item.update({"状態":"照合済み","実際の3連単":actual,
                             "旧順位":old[actual],"新順位":new[actual],
                             "旧Top5":old[actual]<=5,"新Top5":new[actual]<=5,
                             "旧Top10":old[actual]<=10,"新Top10":new[actual]<=10})
        except Exception as exc:
            item["状態"]=f"対象外：{str(exc)[:90]}"
        return race, item
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(evaluate, race) for race in range(1, count+1)]
        for finished, future in enumerate(as_completed(futures), 1):
            records.append(future.result())
            if progress:
                progress(finished / count)
    return [item for race, item in sorted(records)]
