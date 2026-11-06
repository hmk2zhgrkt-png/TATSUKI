"""Timestamped race-card snapshots. Local SQLite is ephemeral: export JSON for durable backup."""
import json
import sqlite3
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

def setup(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS race_snapshots (
        race_key TEXT PRIMARY KEY,
        captured_at TEXT NOT NULL,
        race_date TEXT NOT NULL,
        venue TEXT NOT NULL,
        race_no INTEGER NOT NULL,
        rows_json TEXT NOT NULL,
        sources_json TEXT NOT NULL,
        timing TEXT NOT NULL
    )""")
    conn.commit()

def capture(conn, day, venue, race_no, rows, sources):
    now = datetime.now(JST)
    # A race scheduled for tomorrow or later is provably not completed at capture.
    # Same-day snapshots require actual race start-time evidence.
    if day < now.date():
        raise ValueError("過去のレースは事前スナップショットとして保存できません")
    if len(rows) != 6 or {int(r["号艇"]) for r in rows} != set(range(1,7)):
        raise ValueError("6艇のデータが必要です")
    key = f"{day:%Y%m%d}-{venue}-{race_no:02d}"
    timing = "前日以前" if day > now.date() else "当日（締切前未確認）"
    conn.execute("""INSERT INTO race_snapshots
       (race_key,captured_at,race_date,venue,race_no,rows_json,sources_json,timing)
       VALUES(?,?,?,?,?,?,?,?)
       ON CONFLICT(race_key) DO NOTHING""",
       (key,now.isoformat(),day.isoformat(),venue,race_no,
        json.dumps(rows,ensure_ascii=False),json.dumps(sources,ensure_ascii=False),timing))
    conn.commit()
    return key,timing

def list_snapshots(conn):
    return [dict(zip(("レースID","取得日時","対象日","開催場","R","保存区分"),r))
            for r in conn.execute("""SELECT race_key,captured_at,race_date,venue,race_no,timing
              FROM race_snapshots ORDER BY captured_at DESC""").fetchall()]

def export_json(conn):
    rows=conn.execute("""SELECT race_key,captured_at,race_date,venue,race_no,rows_json,sources_json,timing
                         FROM race_snapshots ORDER BY captured_at""").fetchall()
    keys=("race_key","captured_at","race_date","venue","race_no","rows_json","sources_json","timing")
    return json.dumps({"schema":1,"snapshots":[dict(zip(keys,r)) for r in rows]},ensure_ascii=False,indent=2)

def import_json(conn,content):
    obj=json.loads(content)
    if obj.get("schema")!=1 or not isinstance(obj.get("snapshots"),list):
        raise ValueError("スナップショットJSONの形式が違います")
    count=0
    for r in obj["snapshots"]:
        fields=("race_key","captured_at","race_date","venue","race_no","rows_json","sources_json","timing")
        if any(k not in r for k in fields):
            raise ValueError("保存データに必須項目がありません")
        dt=datetime.fromisoformat(r["captured_at"])
        day=datetime.fromisoformat(r["race_date"]).date()
        if dt.tzinfo is None or day < dt.astimezone(JST).date():
            raise ValueError("取得日時とレース日が矛盾しています")
        rows=json.loads(r["rows_json"])
        if len(rows)!=6 or {int(x["号艇"]) for x in rows}!=set(range(1,7)):
            raise ValueError("艇数が不正です")
        cur=conn.execute("""INSERT OR IGNORE INTO race_snapshots
            (race_key,captured_at,race_date,venue,race_no,rows_json,sources_json,timing)
            VALUES(?,?,?,?,?,?,?,?)""",tuple(r[k] for k in fields))
        count+=cur.rowcount
    conn.commit()
    return count
