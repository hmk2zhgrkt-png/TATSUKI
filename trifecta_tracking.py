"""Local verification snapshots; Streamlit Cloud disk is ephemeral. Export JSON backups."""
import json
import sqlite3
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

def setup(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS trifecta_checks (
      race_key TEXT PRIMARY KEY, saved_at TEXT NOT NULL, category TEXT NOT NULL,
      old_ranking TEXT NOT NULL, new_ranking TEXT NOT NULL,
      actual TEXT, checked_at TEXT
    )""")
    conn.commit()

def save(conn, race_key, race_date, old_rank, new_rank):
    now = datetime.now(JST)
    category = "事前登録（日付基準）" if race_date > now.date() else "当日・事後（未検証）"
    old_order = [k for k,v in sorted(old_rank.items(),key=lambda x:x[1])]
    new_order = [k for k,v in sorted(new_rank.items(),key=lambda x:x[1])]
    if len(old_order)!=120 or len(new_order)!=120 or set(old_order)!=set(new_order):
        raise ValueError("120通りの順位が不完全です")
    existing = conn.execute("SELECT race_key FROM trifecta_checks WHERE race_key=?", (race_key,)).fetchone()
    if existing:
        raise ValueError("このレースは保存済みです。上書きはできません。")
    conn.execute("INSERT INTO trifecta_checks(race_key,saved_at,category,old_ranking,new_ranking) VALUES(?,?,?,?,?)",
                 (race_key,now.isoformat(),category,json.dumps(old_order),json.dumps(new_order)))
    conn.commit()
    return category

def verify(conn, race_key, actual):
    row = conn.execute("SELECT actual FROM trifecta_checks WHERE race_key=?", (race_key,)).fetchone()
    if not row:
        raise ValueError("先に予想を保存してください")
    if row[0] is not None:
        raise ValueError("このレースの結果はすでに確定済みです")
    if len(actual.split("-"))!=3 or set(actual.split("-"))-set("123456") or len(set(actual.split("-")))!=3:
        raise ValueError("着順が不正です")
    conn.execute("UPDATE trifecta_checks SET actual=?,checked_at=? WHERE race_key=?",
                 (actual,datetime.now(JST).isoformat(),race_key))
    conn.commit()

def all_rows(conn):
    return conn.execute("SELECT race_key,saved_at,category,old_ranking,new_ranking,actual,checked_at FROM trifecta_checks ORDER BY saved_at DESC").fetchall()

def report(conn):
    rows=all_rows(conn)
    results=[]
    for key, saved, category, old_json, new_json, actual, checked in rows:
        old=json.loads(old_json)
        new=json.loads(new_json)
        old_pos=old.index(actual)+1 if actual in old else None
        new_pos=new.index(actual)+1 if actual in new else None
        results.append({"レース":key,"区分":category,"保存日時":saved,"実際の着順":actual or "未入力",
                        "旧順位":old_pos,"新順位":new_pos,
                        "旧Top5":bool(old_pos and old_pos<=5),"新Top5":bool(new_pos and new_pos<=5),
                        "旧Top10":bool(old_pos and old_pos<=10),"新Top10":bool(new_pos and new_pos<=10)})
    return results

def export_json(conn):
    data=[]
    for key,saved,category,old,new,actual,checked in all_rows(conn):
        data.append({"race_key":key,"saved_at":saved,"category":category,
                     "old_ranking":json.loads(old),"new_ranking":json.loads(new),
                     "actual":actual,"checked_at":checked})
    return json.dumps({"format":"tatsuki-trifecta-v1","records":data},ensure_ascii=False,indent=2)

def import_json(conn, payload):
    data=json.loads(payload)
    if data.get("format")!="tatsuki-trifecta-v1" or not isinstance(data.get("records"),list):
        raise ValueError("TATSUKIバックアップ形式ではありません")
    count=0
    for item in data["records"]:
        old=item["old_ranking"];new=item["new_ranking"]
        if len(old)!=120 or len(new)!=120 or len(set(old))!=120 or set(old)!=set(new):
            raise ValueError("順位データが不正です")
        conn.execute("INSERT OR IGNORE INTO trifecta_checks VALUES(?,?,?,?,?,?,?)",
                     (item["race_key"],item["saved_at"],item["category"],
                      json.dumps(old),json.dumps(new),item.get("actual"),item.get("checked_at")))
        count+=1
    conn.commit()
    return count
