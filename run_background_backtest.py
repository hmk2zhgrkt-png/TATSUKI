"""Run historical backtest on GitHub Actions without keeping iPhone open."""
import argparse
import ast
import csv
import json
import re
from datetime import date, timedelta
from pathlib import Path
from app_backtest import backtest
from history_data import VENUE_CODES
from backtest_cache import cached_result
from official_verification import verify_official_trifecta

def course_rates():
    text=Path("app.py").read_text(encoding="utf-8")
    match=re.search(r"VENUE_COURSE_HISTORICAL = dict\(zip\(VENUES, (\[\[.*?\]\])\)\)",text)
    if not match:
        raise RuntimeError("コース統計を読み取れません")
    return dict(zip(VENUE_CODES,ast.literal_eval(match.group(1))))

def lookup(day,venue,race):
    actual,source=cached_result(day,VENUE_CODES[venue],race)
    if actual:
        key=f"{day:%Y%m%d}-{VENUE_CODES[venue]:02d}-{race:02d}"
        verify_official_trifecta(key,actual)
    return actual,source

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--end",required=True)
    parser.add_argument("--days",type=int,default=2)
    parser.add_argument("--venues",default="下関")
    args=parser.parse_args()
    if not 1<=args.days<=7:
        parser.error("days must be 1-7")
    end=date.fromisoformat(args.end)
    if end>=date.today():
        parser.error("end date must be in the past")
    venues=[x.strip() for x in args.venues.split(",") if x.strip()]
    if not venues or len(venues)>3 or any(v not in VENUE_CODES for v in venues):
        parser.error("select 1-3 valid venues")
    rates=course_rates()
    records=[]
    for offset in range(args.days-1,-1,-1):
        day=end-timedelta(days=offset)
        for venue in venues:
            print(f"Checking {day} {venue}",flush=True)
            records.extend(backtest(day,venue,12,rates,result_lookup=lookup))
    Path("output").mkdir(exist_ok=True)
    fields=["日付","開催場","レース","状態","実際の3連単","旧順位","新順位","旧Top5","新Top5","旧Top10","新Top10"]
    with open("output/backtest.csv","w",encoding="utf-8-sig",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=fields,extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    verified=[r for r in records if r.get("状態")=="照合済み"]
    summary={"checked":len(records),"verified":len(verified)}
    if verified:
        for model in ("旧","新"):
            summary[model+"top5"]=sum(r[model+"Top5"] for r in verified)/len(verified)
            summary[model+"top10"]=sum(r[model+"Top10"] for r in verified)/len(verified)
    Path("output/summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(summary,flush=True)

if __name__=="__main__":
    main()
