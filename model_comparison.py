"""Small predefined model comparison with chronological holdout; no holdout tuning."""
from itertools import permutations

CANDIDATES = {
    "現行モデル": (.35,.15,.20,.10,.20),
    "全国勝率重視": (.45,.15,.15,.10,.15),
    "モーター重視": (.25,.15,.35,.10,.15),
    "ST重視": (.30,.30,.15,.10,.15),
    "コース重視": (.25,.10,.15,.10,.40),
}
def predict(rows, venue, rates, weights):
    scores={}
    for row in rows:
        boat=int(row["号艇"])
        rate,st,motor=(row.get(k) for k in ("全国勝率","平均ST","モーター2連対率"))
        if any(x is None for x in (rate,st,motor)):
            raise ValueError("入力データ不足")
        if not (0<=rate<=10 and 0<=st<=1 and 0<=motor<=100):
            raise ValueError("データ範囲外")
        values=(rate/10,1-st,motor/100,1.0,rates[venue][boat-1]/50)
        score=max(.001,sum(a*b for a,b in zip(weights,values)))
        finishes=[int(x) for x in str(row.get("過去10走","")).split() if x in ("1","2","3","4","5","6")]
        if len(finishes)>=5:
            score*=1+max(-.06,min(.06,.12*(sum(x<=3 for x in finishes)/len(finishes)-.5)))
        scores[boat]=score
    if set(scores)!=set(range(1,7)):
        raise ValueError("6艇不足")
    total=sum(scores.values())
    ordered=[]
    for a,b,c in permutations(range(1,7),3):
        probability=(scores[a]/total * scores[b]/(total-scores[a]) *
                     scores[c]/(total-scores[a]-scores[b]))
        ordered.append((f"{a}-{b}-{c}",probability))
    ordered.sort(key=lambda x:(-x[1],x[0]))
    return {name:i for i,(name,_) in enumerate(ordered,1)}

def compare(records,rates):
    valid=sorted((r for r in records if r.get("状態")=="照合済み" and r.get("_入力データ")),
                 key=lambda r:(r["日付"],r["開催場"],r["レース"]))
    dates=sorted({r["日付"] for r in valid})
    if len(dates)<3 or len(valid)<30:
        raise ValueError("評価には照合済み30レース以上・異なる3日以上が必要です。")
    split=max(1,min(len(dates)-1,int(len(dates)*.7)))
    train=[r for r in valid if r["日付"] in set(dates[:split])]
    test=[r for r in valid if r["日付"] in set(dates[split:])]
    def stats(rows,weights):
        # Match historical backtest: no future-dated course statistics.
        def race_rates(r):
            return {v:[50.0]*6 for v in rates} if r["日付"] <= "2026-10-31" else rates
        ranks=[predict(r["_入力データ"],r["開催場"],race_rates(r),weights)[r["実際の3連単"]] for r in rows]
        return {"Top1":sum(x==1 for x in ranks)/len(ranks),
                "Top5":sum(x<=5 for x in ranks)/len(ranks),
                "Top10":sum(x<=10 for x in ranks)/len(ranks),
                "平均順位":sum(ranks)/len(ranks)}
    training=[(name,stats(train,weights)) for name,weights in CANDIDATES.items()]
    # Optimize train Top10, then Top5, then mean rank; fixed tie-break keeps baseline.
    best=max(training,key=lambda item:(item[1]["Top10"],item[1]["Top5"],-item[1]["平均順位"]))
    baseline=stats(test,CANDIDATES["現行モデル"])
    selected=stats(test,CANDIDATES[best[0]])
    return {"学習件数":len(train),"評価件数":len(test),
            "学習期間":f"{dates[0]} ～ {dates[split-1]}",
            "評価期間":f"{dates[split]} ～ {dates[-1]}",
            "選択モデル":best[0],"学習比較":training,
            "現行モデル評価":baseline,"選択モデル評価":selected}
