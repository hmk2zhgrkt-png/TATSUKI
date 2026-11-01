"""Boat-race analysis MVP. Probabilities are heuristic, NOT calibrated predictions."""
import csv
import io
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

DB = Path(__file__).with_name("predictions.db")
VENUES = ["桐生","戸田","江戸川","平和島","多摩川","浜名湖","蒲郡","常滑","津","三国","びわこ","住之江","尼崎","鳴門","丸亀","児島","宮島","徳山","下関","若松","芦屋","福岡","唐津","大村"]
VENUE_INNER_WIN = dict(zip(VENUES, [51,43.2,47.5,44,54.6,55,57.8,57.6,58.1,52.6,53.7,57,60.3,48.8,55,54.8,56.4,61.6,62.4,59.7,60.4,59.3,57.2,60.9]))
VENUE_COURSE_HISTORICAL = dict(zip(VENUES, [[39.3,15.9,15.2,16.2,9.6,3.9],[33.8,17.6,17.2,17.8,9.6,4.2],[41.5,19.8,14.7,12.9,7.5,4.1],[34.6,18,16.1,16.3,10.1,5.1],[38.1,17.3,16.1,16,8.8,3.7],[41.9,17.1,15.5,13.9,8.3,3.3],[44.4,15,14.5,14.8,8.4,3.1],[45.1,15.9,13.9,13.7,8.1,3.5],[45.2,17.6,14,12.3,7.4,3.7],[42.9,18.8,15,13.1,7.5,2.8],[38,17.4,15.7,15.5,9.5,4.1],[46.2,16.8,13.5,13,7.5,3.1],[44.1,15.9,14,13.8,8.6,3.6],[38.9,18.2,16,14.6,9.1,3.4],[44.4,16.8,14.2,13.2,8.1,3.4],[43.7,16.4,14.1,13.9,8.5,3.6],[44.5,15.3,14.7,13.3,8.4,3.9],[47.5,16.3,13,12.8,7.7,2.9],[45.7,15.8,13.9,13.6,7.7,3.4],[43.9,16.8,14.8,13.5,7.9,3.3],[47.9,15.2,13.1,13.4,7.5,3],[39.5,18.6,17.6,14,7.5,2.9],[42.4,18,14.7,13.7,7.8,3.4],[50.8,15.6,13.1,11.4,6.5,2.7]]))
VENUE_COURSE_SOURCE = "https://kyoteidb.com/stadium"
VENUE_STATS_SOURCE = "https://boatrace-db.jp/stadium/acourse/"
# Reference: 1-course first-place percentages, approximately 2025-10-01 to 2026-09-30.
# Historical course statistics are not a validated per-race probability.
FIELDS = ["race_id", "boat", "win_rate", "avg_st", "motor_rate", "exhibition_time", "odds"]

def database():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS picks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        race_id TEXT NOT NULL,
        boat INTEGER NOT NULL,
        odds REAL NOT NULL,
        probability REAL NOT NULL,
        stake INTEGER NOT NULL,
        actual_winner INTEGER
    )""")
    conn.commit()
    return conn

def parse_rows(upload):
    text = upload.getvalue().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    missing = [field for field in FIELDS if field not in (reader.fieldnames or [])]
    if missing:
        raise ValueError("不足している列: " + ", ".join(missing))
    rows = []
    for line, item in enumerate(reader, 2):
        try:
            row = {"race_id": item["race_id"].strip(), "boat": int(item["boat"]),
                   "win_rate": float(item["win_rate"]), "avg_st": float(item["avg_st"]),
                   "motor_rate": float(item["motor_rate"]),
                   "exhibition_time": float(item["exhibition_time"]),
                   "odds": float(item["odds"])}
            if not row["race_id"] or not 1 <= row["boat"] <= 6:
                raise ValueError("race_id または boat が不正")
            if row["odds"] <= 1 or row["win_rate"] < 0 or not 0 <= row["avg_st"] <= 1 or not 0 <= row["motor_rate"] <= 100 or row["exhibition_time"] <= 0:
                raise ValueError("数値が範囲外")
            rows.append(row)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"{line}行目: {exc}") from exc
    if not rows:
        raise ValueError("データがありません")
    groups = {}
    for row in rows:
        groups.setdefault(row["race_id"], []).append(row)
    for race, boats in groups.items():
        nums = [x["boat"] for x in boats]
        if len(nums) != 6 or set(nums) != set(range(1, 7)):
            raise ValueError(f"{race}: 1〜6号艇の6行が必要です")
    return groups

def evaluate(groups):
    picks = []
    for race, boats in groups.items():
        # Transparent heuristic, not a fitted statistical model.
        scores = []
        for b in boats:
            score = (0.28 * (b["win_rate"] / 10) +
                     0.15 * (1 - b["avg_st"]) +
                     0.15 * (b["motor_rate"] / 100) +
                     0.12 * (1 / b["exhibition_time"]) +
                     0.30 * (1 / b["boat"]))
            scores.append(max(score, 0.000001))
        total = sum(scores)
        for b, score in zip(boats, scores):
            p = score / total
            picks.append({"レース": race, "号艇": b["boat"], "推定確率": round(p * 100, 1),
                          "単勝オッズ": b["odds"], "期待回収倍率": round(p * b["odds"], 2),
                          "_probability": p})
    return sorted(picks, key=lambda x: x["期待回収倍率"], reverse=True)

st.set_page_config(page_title="競艇AI分析 | TATSUKI", layout="centered")
st.title("🚤 競艇AI分析 TATSUKI")
st.caption("試作版｜実データの自動取得なし・予測精度は未検証・自動投票なし")
st.warning("表示する確率は簡易計算による参考値で、実際の的中確率ではありません。利益を保証しません。")
tab1, tab2, tab3 = st.tabs(["レース分析", "結果記録", "成績"])
conn = database()

with tab1:
    st.subheader("レース条件")
    venue = st.selectbox("競艇場（全国24場）", VENUES, index=None, placeholder="競艇場を選択")
    if venue:
        inner = VENUE_INNER_WIN[venue]
        st.metric(f"{venue}・1コースの過去1着率", f"{inner:.1f}%")
        st.caption("参考統計：概ね2025/10〜2026/9の進入1コース実績。今日の1号艇の勝率ではありません。")
        st.write("傾向の目安：" + ("全国の中でもインが強め" if inner >= 58 else "インが比較的弱め" if inner < 50 else "中間的な水準"))
        st.write("**1〜6コースの過去1着率（長期統計）**")
        st.dataframe([{"進入コース": f"{i}コース", "過去1着率": f"{rate:.1f}%"} for i, rate in enumerate(VENUE_COURSE_HISTORICAL[venue], 1)], hide_index=True, use_container_width=True)
        st.caption("長期統計：1997/5/1〜2026/10/31。上の直近約1年の数値とは集計期間が異なります。進入コース別であり、号艇別ではありません。")
        st.link_button("長期コース別データの出典", VENUE_COURSE_SOURCE)
        st.link_button("直近1年のデータの出典", VENUE_STATS_SOURCE)
    else:
        st.caption("競艇場を選ぶと過去の1コース1着率を表示します。")
    weather = st.selectbox("天候", ["未取得", "晴れ", "くもり", "雨", "雪", "その他"])
    wind_direction = st.selectbox("風向", ["未取得", "無風", "北", "北東", "東", "南東", "南", "南西", "西", "北西"])
    wind_speed = st.number_input("風速（m/s）", min_value=0.0, max_value=40.0, value=None, step=0.5, placeholder="未取得")
    wave_height = st.number_input("波高（cm）", min_value=0, max_value=300, value=None, step=1, placeholder="未取得")
    st.caption("気象条件は未入力のままでもOK。実測値がなければ未取得として扱います。")
    st.caption("風向の有利不利は場の水面・風向・風速・進入コースによって変わります。現時点では自動補正しません。")
    st.divider()
    st.subheader("CSVから分析")
    st.write("実際に取得したレース情報をCSVでアップロードしてください。未取得データを架空の値で補いません。")
    st.code(",".join(FIELDS), language="text")
    uploaded = st.file_uploader("レースデータ（CSV / UTF-8）", type=["csv"])
    if uploaded is None:
        st.info("未取得：CSVを読み込むと分析候補を表示します。")
    else:
        try:
            groups = parse_rows(uploaded)
            picks = evaluate(groups)
            st.write("**分析対象の条件（参考情報）**")
            st.write(f"競艇場: {venue or '未取得'} / 天候: {weather} / 風向: {wind_direction} / 風速: {str(wind_speed) + ' m/s' if wind_speed is not None else '未取得'} / 波高: {str(wave_height) + ' cm' if wave_height is not None else '未取得'}")
            st.info("現バージョンでは気象条件・競艇場特性は予測計算に反映していません。根拠のない補正を避けるため、まず実測値の記録・表示に対応しました。")
            top = picks[:10]
            st.dataframe([{k: v for k, v in row.items() if not k.startswith("_")} for row in top], use_container_width=True, hide_index=True)
            st.caption("期待回収倍率＝簡易推定確率×単勝オッズ。1倍超でも利益は保証されません。")
            with st.form("save_pick"):
                options = list(range(len(top)))
                chosen = st.selectbox("記録する候補", options, format_func=lambda i: f'{top[i]["レース"]} / {top[i]["号艇"]}号艇')
                stake = st.number_input("仮想購入額（円）", min_value=0, max_value=1000, value=100, step=100)
                if st.form_submit_button("予想を記録"):
                    p = top[chosen]
                    conn.execute("INSERT INTO picks(created_at,race_id,boat,odds,probability,stake) VALUES (?,?,?,?,?,?)",
                                 (datetime.now(timezone.utc).isoformat(), p["レース"], p["号艇"], p["単勝オッズ"], p["_probability"], stake))
                    conn.commit()
                    st.success("予想を保存しました")
        except (UnicodeDecodeError, ValueError) as exc:
            st.error(str(exc))

with tab2:
    pending = conn.execute("SELECT id,race_id,boat FROM picks WHERE actual_winner IS NULL ORDER BY id DESC").fetchall()
    if not pending:
        st.info("結果待ちの予想はありません。")
    else:
        with st.form("record_result"):
            idx = st.selectbox("結果を入力する予想", range(len(pending)), format_func=lambda i: f'#{pending[i][0]} {pending[i][1]} / 予想{pending[i][2]}号艇')
            winner = st.selectbox("実際の1着", range(1, 7))
            if st.form_submit_button("結果を保存"):
                conn.execute("UPDATE picks SET actual_winner=? WHERE id=?", (winner, pending[idx][0]))
                conn.commit()
                st.success("結果を保存しました。画面を更新すると成績に反映されます。")

with tab3:
    records = conn.execute("SELECT boat,odds,stake,actual_winner FROM picks WHERE actual_winner IS NOT NULL").fetchall()
    if not records:
        st.info("結果がまだ記録されていません。")
    else:
        total = sum(r[2] for r in records)
        returns = sum(round(r[1] * r[2]) for r in records if r[0] == r[3])
        hits = sum(r[0] == r[3] for r in records)
        a, b = st.columns(2)
        a.metric("的中率", f"{hits / len(records) * 100:.1f}%")
        b.metric("回収率", f"{returns / total * 100:.1f}%" if total else "未算出")
        st.metric("累計収支（仮想）", f"{returns - total:+,}円")
        st.caption(f"結果記録済み {len(records)}件。仮想購入額0円の記録は回収率の分母に含まれません。")
conn.close()
