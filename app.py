"""Boat-race analysis MVP. Probabilities are heuristic, NOT calibrated predictions."""
import csv
import io
import math
import itertools
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
import importlib
import history_data
import trifecta_tracking as tracking
from durable_storage import open_database, RemoteStorageError

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
    conn, persistent = open_database(DB, st.secrets)
    st.session_state["supabase_persistent"] = persistent
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


def fetch_actual_result(race_key):
    """Read publicly available results, returning None if not yet available."""
    import re
    from datetime import date
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from history_data import VENUE_CODES
    match = re.fullmatch(r"(\d{8})-(\d{2})-(\d{2})", race_key)
    if match is None:
        raise ValueError("レースIDが不正です")
    ymd, venue, race = match.groups()
    day = date.fromisoformat(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}")
    url = f"https://boatracecsv.github.io/data/results/realtime/{day:%Y/%m/%d}.csv"
    try:
        with urlopen(Request(url, headers={"User-Agent": "TATSUKI-research/0.1"}), timeout=12) as response:
            content = response.read().decode("utf-8-sig")
    except HTTPError as exc:
        if exc.code == 404:
            return None, url
        raise
    code = ymd + venue + race
    for row in csv.DictReader(io.StringIO(content)):
        if row.get("レースコード", "").strip() != code:
            continue
        places = [str(row.get(f"{n}着_艇番", "")).strip() for n in (1, 2, 3)]
        if len(set(places)) != 3 or any(p not in "123456" or len(p) != 1 for p in places):
            raise ValueError("着順の形式が不正です。手入力で確認してください。")
        return "-".join(places), url
    return None, url

st.set_page_config(page_title="競艇AI分析 | TATSUKI", layout="centered")
st.title("🚤 競艇AI分析 TATSUKI")
st.caption("試作版｜実データの自動取得なし・予測精度は未検証・自動投票なし")
st.caption("取得処理診断バージョン：2026-11-09-E（取得モジュール再読み込み）")
st.warning("表示する確率は簡易計算による参考値で、実際の的中確率ではありません。利益を保証しません。")
tab4, tab1, tab2, tab3 = st.tabs(["3連単予想（試作）", "旧CSV分析", "旧結果記録", "旧成績"])
try:
    conn = database()
    tracking.setup(conn)
except RemoteStorageError as exc:
    st.error(str(exc))
    st.info("接続診断バージョン：2026-11-06-B。表示されたHTTP番号・読込/保存の区分だけ確認してください。")
    st.stop()
if st.session_state.get("supabase_persistent"):
    st.success("☁️ Supabase永続保存に接続済み（再デプロイ後もデータを復元）")
else:
    st.warning("⚠️ Supabase未接続：保存データは再デプロイで消える可能性があります。")

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
    st.subheader("スタート展示・モーター情報（任意）")
    st.caption("出走表や展示情報から確認できた数値だけ入力。空欄は未取得として扱います。")
    detail_rows = []
    for lane in range(1, 7):
        with st.expander(f"{lane}号艇の追加情報"):
            entry = st.selectbox("スタート展示の進入コース", ["未取得", "1", "2", "3", "4", "5", "6"], key=f"entry_{lane}")
            exhibit_st = st.number_input("展示ST（秒）", min_value=-1.0, max_value=1.0, value=None, step=0.01, format="%.2f", key=f"exst_{lane}", placeholder="未取得")
            motor_number = st.number_input("モーター番号", min_value=1, max_value=999, value=None, step=1, key=f"motorno_{lane}", placeholder="未取得")
            motor_2 = st.number_input("モーター2連対率（%）", min_value=0.0, max_value=100.0, value=None, step=0.1, key=f"motor2_{lane}", placeholder="未取得")
            motor_3 = st.number_input("モーター3連対率（%）", min_value=0.0, max_value=100.0, value=None, step=0.1, key=f"motor3_{lane}", placeholder="未取得")
            boat_number = st.number_input("ボート番号", min_value=1, max_value=999, value=None, step=1, key=f"boatno_{lane}", placeholder="未取得")
            boat_2 = st.number_input("ボート2連対率（%）", min_value=0.0, max_value=100.0, value=None, step=0.1, key=f"boat2_{lane}", placeholder="未取得")
            tilt = st.number_input("チルト角度（度）", min_value=-2.0, max_value=3.0, value=None, step=0.5, key=f"tilt_{lane}", placeholder="未取得")
            detail_rows.append({"号艇": lane, "展示進入": entry, "展示ST": exhibit_st, "モーター番号": motor_number, "モーター2連対率": motor_2, "モーター3連対率": motor_3, "ボート番号": boat_number, "ボート2連対率": boat_2, "チルト": tilt})
    st.caption("追加情報は現在表示・確認用で、まだ予測スコアに反映していません。展示進入は本番の進入を保証しません。")
    with st.expander("追加情報の確認"):
        st.dataframe(detail_rows, use_container_width=True, hide_index=True)
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

with tab4:
    st.subheader("過去戦績の自動取得（新機能）")
    st.write("日付・競艇場・レース番号を選ぶだけ。CSVの作成は不要です。")
    auto_date = st.date_input("開催日", key="auto_date")
    auto_venue = st.selectbox("開催場を選択", VENUES, index=18, key="auto_venue")
    auto_race = st.selectbox("レース番号", list(range(1, 13)), index=3, format_func=lambda n: f"{n}R", key="auto_race")
    if st.button("選手の過去戦績を取得", key="fetch_history"):
        try:
            with st.spinner("公開データを取得中..."):
                history_rows, source_urls = load_race(auto_date, auto_venue, auto_race)
            st.session_state["loaded_history"] = (auto_date, auto_venue, auto_race, history_rows, source_urls)
        except Exception as exc:
            st.error(f"取得できませんでした：{exc}")
    saved = st.session_state.get("loaded_history")
    if saved:
        day, place, race_no, history_rows, source_urls = saved
        st.success(f"{day} {place}{race_no}R：6艇のデータを取得しました")
        st.dataframe(history_rows, hide_index=True, use_container_width=True)
        for source_url in source_urls:
            st.caption(f"データ出典：{source_url}")
        st.caption("過去10走は今回と同じ枠番での直近10走。未取得は未取得と表示します。")
        st.caption("展示タイム未取得時は7.0秒の仮置き値で試算します。展示後に更新してください。")
        st.subheader("取得データで3連単を自動計算")
        exhibition_inputs = {}
        cols = st.columns(3)
        for boat in range(1, 7):
            with cols[(boat - 1) % 3]:
                exhibition_inputs[boat] = st.number_input(f"{boat}号艇 展示タイム（秒・任意）", min_value=5.0, max_value=10.0, value=7.0, step=0.01, key=f"exhibit_{boat}")
        if st.button("取得データで120通りを予想", key="auto_predict"):
            try:
                scores_auto = {}
                for row in history_rows:
                    boat = int(row["号艇"])
                    rate = row["全国勝率"]
                    avg_st = row["平均ST"]
                    motor = row["モーター2連対率"]
                    if any(x is None for x in (rate, avg_st, motor)):
                        raise ValueError(f"{boat}号艇の必須データが欠けています。予想は実行しません。")
                    if not (0 <= rate <= 10 and 0 <= avg_st <= 1 and 0 <= motor <= 100):
                        raise ValueError(f"{boat}号艇の数値が範囲外です。")
                    historical = VENUE_COURSE_HISTORICAL[place][boat - 1]
                    score = (0.35 * rate / 10 + 0.15 * (1 - avg_st)
                             + 0.20 * motor / 100
                             + 0.10 * 7.0 / exhibition_inputs[boat]
                             + 0.20 * historical / 50)
                    scores_auto[boat] = max(0.001, score)
                baseline_scores_auto = scores_auto.copy()
                # Recent finishing positions are an experimental, capped adjustment.
                # Non-numeric finishes (F/L/欠場 etc.) are excluded, never treated as 6th.
                recent_details = []
                finish_distribution = []
                for row in history_rows:
                    boat = int(row["号艇"])
                    raw = str(row.get("過去10走", ""))
                    finishes = [int(token) for token in raw.split() if token in ("1", "2", "3", "4", "5", "6")]
                    counts = {str(i) + "着": finishes.count(i) for i in range(1, 7)}
                    finish_distribution.append({"号艇": boat, "選手": row.get("選手", ""), "有効走数": len(finishes), **counts})
                    if len(finishes) >= 5:
                        top3_rate = sum(v <= 3 for v in finishes) / len(finishes)
                        # Baseline 50% top-three; cap effect to ±6%.
                        factor = 1 + max(-0.06, min(0.06, 0.12 * (top3_rate - 0.5)))
                        scores_auto[boat] *= factor
                        recent_details.append({"号艇": boat, "有効走数": len(finishes), "3着以内率": f"{top3_rate*100:.1f}%", "スコア補正": f"{(factor-1)*100:+.1f}%"})
                    else:
                        recent_details.append({"号艇": boat, "有効走数": len(finishes), "3着以内率": "不足", "スコア補正": "なし"})
                st.session_state["recent_details"] = recent_details
                st.session_state["finish_distribution"] = finish_distribution
                total_auto = sum(scores_auto.values())
                outcomes_auto = []
                for a, b, c_ in itertools.permutations(range(1, 7), 3):
                    p = (scores_auto[a] / total_auto
                         * scores_auto[b] / (total_auto - scores_auto[a])
                         * scores_auto[c_] / (total_auto - scores_auto[a] - scores_auto[b]))
                    outcomes_auto.append({"3連単": f"{a}-{b}-{c_}", "モデル内の割合": p})
                outcomes_auto.sort(key=lambda x: x["モデル内の割合"], reverse=True)
                baseline_total = sum(baseline_scores_auto.values())
                baseline_outcomes = []
                for a, b, c_ in itertools.permutations(range(1, 7), 3):
                    p0 = (baseline_scores_auto[a] / baseline_total
                          * baseline_scores_auto[b] / (baseline_total - baseline_scores_auto[a])
                          * baseline_scores_auto[c_] / (baseline_total - baseline_scores_auto[a] - baseline_scores_auto[b]))
                    baseline_outcomes.append((f"{a}-{b}-{c_}", p0))
                baseline_outcomes.sort(key=lambda x: x[1], reverse=True)
                st.session_state["baseline_rank"] = {name: rank for rank, (name, _) in enumerate(baseline_outcomes, 1)}
                st.session_state["auto_prediction"] = (day, place, race_no, outcomes_auto)
            except (ValueError, TypeError, ZeroDivisionError) as exc:
                st.error(f"計算できませんでした：{exc}")
        predicted = st.session_state.get("auto_prediction")
        if predicted and predicted[:3] == (day, place, race_no):
            results_auto = predicted[3]
            if st.session_state.get("finish_distribution"):
                st.subheader("枠番別・過去10走の着順分布")
                st.dataframe(st.session_state["finish_distribution"], hide_index=True, use_container_width=True)
                st.caption("取得した同枠番の着順だけを集計。サンプルが少ないため参考値です。")
            st.subheader("旧モデルと新モデルの順位比較")
            baseline_rank = st.session_state.get("baseline_rank", {})
            st.dataframe([{"3連単": x["3連単"], "旧順位（直近補正なし）": baseline_rank.get(x["3連単"], "—"), "新順位": i,
                           "順位変化": (baseline_rank[x["3連単"]] - i) if x["3連単"] in baseline_rank else None}
                          for i, x in enumerate(results_auto[:20], 1)], hide_index=True, use_container_width=True)
            st.caption("これは同一レースでの順位変化の比較です。的中率の改善を証明するものではありません。")
            st.info("スタートのばらつき（標準偏差）は過去各走のST実測値が必要です。現行の取得データでは確認できないため未計算です。")
            if st.session_state.get("recent_details"):
                st.caption("過去10走の3着以内率による試験的な補正（有効5走以上・最大±6%）")
                st.dataframe(st.session_state["recent_details"], hide_index=True, use_container_width=True)
            st.dataframe([{"順位": i, "3連単": x["3連単"], "モデル内の割合": f'{x["モデル内の割合"] * 100:.2f}%'}
                          for i, x in enumerate(results_auto[:20], 1)], hide_index=True, use_container_width=True)
            output_auto = io.StringIO()
            writer_auto = csv.writer(output_auto)
            writer_auto.writerow(["rank", "combination", "model_share"])
            for i, x in enumerate(results_auto, 1):
                writer_auto.writerow([i, x["3連単"], f'{x["モデル内の割合"]:.8f}'])
            st.download_button("自動計算120通りをCSV保存", output_auto.getvalue().encode("utf-8-sig"),
                               file_name="tatsuki_auto_predictions.csv", mime="text/csv", key="auto_csv")
            st.caption("全国勝率・平均ST・モーター2連対率・開催場の長期コース統計・展示タイムを計算に使用。過去10走の着順が5走以上取得できた艇は3着以内率で最大±6%の試験的補正。データ不足の艇は補正なし。枠番別データの定義や効果は今後検証が必要です。モデル内の割合は実際の的中確率ではありません。")
            st.subheader("予想を保存して検証")
            race_key = f"{day:%Y%m%d}-{VENUES.index(place)+1:02d}-{race_no:02d}"
            st.caption("予想の順位120通りを保存します。レース当日・過去の日付の予想は事前的中実績として扱いません。")
            if st.button("このレースの予想を保存", key="save_trifecta_snapshot"):
                try:
                    new_rank = {x["3連単"]: i for i, x in enumerate(results_auto, 1)}
                    category = tracking.save(conn, race_key, day, baseline_rank, new_rank)
                    st.success(f"予想を保存しました（{category}）。")
                except (ValueError, KeyError) as exc:
                    st.error(str(exc))
            st.subheader("実際の結果と予想順位を照合")
            st.caption("レース終了後に着順を入力してください。結果を見てからの検証は、事前予想の的中実績には含められません。")
            result_cols = st.columns(3)
            actual_order = []
            for idx, label in enumerate(("1着", "2着", "3着")):
                with result_cols[idx]:
                    actual_order.append(st.selectbox(label, list(range(1, 7)), index=(0, 2, 5)[idx],
                                                     key=f"actual_{idx}"))
            if st.button("実際の着順で照合", key="verify_auto_prediction"):
                if len(set(actual_order)) != 3:
                    st.error("1着・2着・3着は異なる号艇を選んでください。")
                else:
                    actual = "-".join(str(x) for x in actual_order)
                    new_ranks = {x["3連単"]: i for i, x in enumerate(results_auto, 1)}
                    old_rank = baseline_rank.get(actual)
                    new_rank = new_ranks.get(actual)
                    if old_rank is None or new_rank is None:
                        st.error("予想データを照合できませんでした。")
                    else:
                        st.session_state["last_verified"] = (day, place, race_no, actual, old_rank, new_rank)
            verified = st.session_state.get("last_verified")
            if verified and verified[:3] == (day, place, race_no):
                _, _, _, actual, old_rank, new_rank = verified
                st.success(f"実際の結果：{actual}")
                st.metric("旧モデルの予想順位", f"{old_rank} / 120")
                st.metric("新モデルの予想順位", f"{new_rank} / 120",
                          delta=f"{old_rank-new_rank:+d}位（プラスが改善）")
                st.write(f"上位5点に含まれたか：旧 {'○' if old_rank <= 5 else '×'} ／ 新 {'○' if new_rank <= 5 else '×'}")
                st.write(f"上位10点に含まれたか：旧 {'○' if old_rank <= 10 else '×'} ／ 新 {'○' if new_rank <= 10 else '×'}")
                st.caption("上記は現在の画面の予想との照合。保存済み予想の結果登録は下の「検証成績」で行ってください。")

    st.divider()
    st.subheader("3連単予想エンジン v0.1")
    st.warning("固定ルールによる試作モデルです。表示する確率は未校正で、実際の的中確率や利益を保証しません。")
    st.write("CSV不要。6艇の数値を表に入力すると、120通りの3連単を同じ計算式で順位付けします。")
    race_venue = st.selectbox("開催場", VENUES, index=18, key="prediction_venue")
    initial = [{"号艇": i, "全国勝率": None, "平均ST": None, "モーター2連対率": None, "展示タイム": None, "展示ST": None, "展示進入コース": i} for i in range(1, 7)]
    with st.form("three_way_form"):
        edited = st.data_editor(initial, hide_index=True, num_rows="fixed", use_container_width=True,
                                disabled=["号艇"], key="three_way_data")
        submitted = st.form_submit_button("3連単120通りを計算")
    if submitted:
        try:
            if len(edited) != 6 or [int(x["号艇"]) for x in edited] != list(range(1, 7)):
                raise ValueError("1〜6号艇の6行が必要です。")
            required = ["全国勝率", "平均ST", "モーター2連対率", "展示タイム"]
            for row in edited:
                if any(row.get(k) is None or row.get(k) == "" for k in required):
                    raise ValueError("全国勝率・平均ST・モーター2連対率・展示タイムを全6艇分入力してください。")
                if not (0 <= float(row["全国勝率"]) <= 10 and 0 <= float(row["平均ST"]) <= 1
                        and 0 <= float(row["モーター2連対率"]) <= 100
                        and 5 <= float(row["展示タイム"]) <= 10):
                    raise ValueError("入力値の範囲を確認してください。")
            courses = [int(row["展示進入コース"]) for row in edited]
            if sorted(courses) != list(range(1, 7)):
                raise ValueError("展示進入コースは1〜6を重複なしで入力してください。")
            scores = {}
            for row in edited:
                boat = int(row["号艇"])
                course = int(row["展示進入コース"])
                historical = VENUE_COURSE_HISTORICAL[race_venue][course - 1]
                score = (0.35 * float(row["全国勝率"]) / 10
                         + 0.15 * (1 - float(row["平均ST"]))
                         + 0.20 * float(row["モーター2連対率"]) / 100
                         + 0.10 * (7.0 / float(row["展示タイム"]))
                         + 0.20 * historical / 50)
                # 展示STは本番STの予測に直結しないため現時点では補正しない。
                scores[boat] = max(0.001, score)
            outcomes = []
            total = sum(scores.values())
            for first, second, third in itertools.permutations(range(1, 7), 3):
                p = (scores[first] / total
                     * scores[second] / (total - scores[first])
                     * scores[third] / (total - scores[first] - scores[second]))
                outcomes.append({"3連単": f"{first}-{second}-{third}", "モデル内の割合": p})
            outcomes.sort(key=lambda x: x["モデル内の割合"], reverse=True)
            st.success("120通りの計算が完了しました。")
            st.dataframe([{"順位": i, "3連単": r["3連単"], "モデル内の割合": f'{r["モデル内の割合"] * 100:.2f}%'}
                          for i, r in enumerate(outcomes[:20], 1)], hide_index=True, use_container_width=True)
            st.caption("割合は120通りの合計が100%になるようにしたモデル上の値。実際の的中確率ではありません。")
            st.caption("全国勝率・平均ST・モーター2連対率・展示タイム・開催場の長期コース別1着率を反映。風・波・展示ST・オッズは未反映。着順の条件付き確率はPlackett–Luce方式で算出。")
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["rank", "combination", "model_share"])
            for i, row in enumerate(outcomes, 1):
                writer.writerow([i, row["3連単"], f'{row["モデル内の割合"]:.8f}'])
            st.download_button("120通りの予想をCSVで保存", output.getvalue().encode("utf-8-sig"),
                               file_name="tatsuki_3ren_predictions.csv", mime="text/csv")
        except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
            st.error(f"入力を確認してください：{exc}")

st.divider()
st.header("🧪 過去レースの予想精度を検証")
st.caption("過去の出走表に記録されたレース前情報だけを使い、旧・新モデルの予想順位を比較します。展示タイムは両モデルとも7.0秒固定。公式結果との照合ができないレースは集計対象外です。")
bt_day = st.date_input("検証する過去の日付", key="bt_day")
bt_venue = st.selectbox("検証する開催場", VENUES, index=18, key="bt_venue")
bt_count = st.selectbox("検証レース数（1Rから順番）", list(range(1,13)), index=2, key="bt_count")
if st.button("過去レースを検証", key="run_backtest"):
    from datetime import datetime, timedelta, timezone
    if bt_day >= datetime.now(timezone(timedelta(hours=9))).date():
        st.error("今日・未来の日付は対象外です。終了済みの過去の日付を選択してください。")
    else:
        import importlib
        import app_backtest
        importlib.reload(app_backtest)
        backtest = app_backtest.backtest
        try:
            progress_bt = st.progress(0)
            with st.spinner("出走表と公式結果を照合しています。数分かかる場合があります。"):
                def checked_lookup(day, venue, race):
                    from history_data import VENUE_CODES
                    from official_verification import verify_official_trifecta
                    key = f"{day:%Y%m%d}-{VENUE_CODES[venue]:02d}-{race:02d}"
                    actual, source = fetch_actual_result(key)
                    if actual:
                        verify_official_trifecta(key, actual)
                    return actual, source
                def update_progress(value):
                    progress_bt.progress(min(100, max(0, int(value * 100))))
                rows_bt = backtest(bt_day, bt_venue, bt_count, VENUE_COURSE_HISTORICAL,
                                   update_progress, checked_lookup)
                st.session_state["backtest_rows"] = rows_bt
                st.session_state["backtest_meta"] = (bt_day, bt_venue, bt_count)
        except Exception as exc:
            st.error(f"検証処理でエラー：{type(exc).__name__}: {exc}")
            st.info("処理は途中で停止しました。成績は更新していません。")

st.subheader("複数日・複数場の一括検証")
st.caption("まずは最大120レースまで。日別・場別の出走表を再利用して通信量を減らします。")
batch_end = st.date_input("検証期間の最終日", key="batch_end")
batch_days = st.selectbox("過去何日分を調べるか", [2, 3, 5], index=1, key="batch_days")
batch_venues = st.multiselect("検証する開催場（最大2場）", VENUES, default=["下関"], max_selections=2, key="batch_venues")
st.caption(f"最大 {batch_days * len(batch_venues) * 12}レース（非開催日は対象外）")
if st.button("複数日・複数場をまとめて検証", key="run_batch_backtest"):
    from datetime import datetime, timedelta, timezone
    if not batch_venues:
        st.error("開催場を1〜2場選んでください。")
    elif batch_end >= datetime.now(timezone(timedelta(hours=9))).date():
        st.error("最終日は昨日以前を選択してください。")
    else:
        try:
            import importlib
            import app_backtest
            importlib.reload(app_backtest)
            from history_data import VENUE_CODES
            from official_verification import verify_official_trifecta
            progress_batch = st.progress(0)
            combinations = [(batch_end - timedelta(days=offset), place)
                            for offset in range(batch_days - 1, -1, -1)
                            for place in batch_venues]
            def checked_batch_result(day, venue, race):
                key = f"{day:%Y%m%d}-{VENUE_CODES[venue]:02d}-{race:02d}"
                from backtest_cache import cached_result
                actual, source = cached_result(day, VENUE_CODES[venue], race)
                if actual:
                    verify_official_trifecta(key, actual)
                return actual, source
            all_rows = []
            with st.spinner("過去レースを検証中。処理に数分かかることがあります。"):
                for idx, (day, place) in enumerate(combinations):
                    def advance(fraction, idx=idx):
                        progress_batch.progress(min(100, int(100 * (idx + fraction) / len(combinations))))
                    rows = app_backtest.backtest(day, place, 12, VENUE_COURSE_HISTORICAL,
                                                 advance, checked_batch_result)
                    all_rows.extend(rows)
            st.session_state["backtest_rows"] = all_rows
            st.session_state["backtest_meta"] = ("一括", batch_end, batch_days, tuple(batch_venues))
            st.success(f"一括検証終了：{len(all_rows)}レースを確認。照合成功数と成績は下に表示します。")
        except Exception as exc:
            st.error(f"一括検証エラー：{type(exc).__name__}: {exc}")
            st.info("途中までのデータは集計に反映していません。")

st.subheader("長期検証")
st.caption("30〜90日を対象に全12Rまたは抽出3Rを検証します。全12Rは通信量が増えます。処理中は画面を開いたままにしてください。")
long_end = st.date_input("長期検証の最終日", key="long_end")
long_days = st.selectbox("長期検証の期間", [30, 60, 90], key="long_days")
long_venue = st.selectbox("長期検証の競艇場", VENUES, index=18, key="long_venue")
long_scope = st.radio("検証レース", ["1R・6R・12R（高速）", "全12レース"], horizontal=True, key="long_scope")
if st.button("長期検証を開始", key="run_long_backtest"):
    from datetime import datetime, timedelta, timezone
    if long_end >= datetime.now(timezone(timedelta(hours=9))).date():
        st.error("最終日は昨日以前にしてください。")
    else:
        try:
            import importlib
            import app_backtest
            importlib.reload(app_backtest)
            from history_data import VENUE_CODES
            from backtest_cache import cached_result
            from official_verification import verify_official_trifecta
            def lookup_long(day, venue, race):
                from official_verification import official_trifecta
                key = f"{day:%Y%m%d}-{VENUE_CODES[venue]:02d}-{race:02d}"
                actual, source = cached_result(day, VENUE_CODES[venue], race)
                if actual:
                    # Keep CSV-based historical verification available independently.
                    # Official lookup is a fallback only for missing CSV entries.
                    return actual, source, "結果CSV"
                try:
                    finish, official_url = official_trifecta(key)
                    return finish, official_url, "公式のみ"
                except (ValueError, OSError, TimeoutError) as exc:
                    raise LookupError(f"結果CSVなし・公式結果も未確認：{str(exc)[:70]}") from exc
            from concurrent.futures import ThreadPoolExecutor, as_completed
            long_progress = st.progress(0)
            long_rows = []
            dates = [long_end - timedelta(days=offset) for offset in range(long_days-1,-1,-1)]
            def evaluate_day(day):
                from history_data import _day_rows
                try:
                    cards, _ = _day_rows("race_cards", day)
                    prefix = f"{day:%Y%m%d}{VENUE_CODES[long_venue]:02d}"
                    selected = range(1, 13) if long_scope == "全12レース" else (1, 6, 12)
                    numbers = [race for race in selected if f"{prefix}{race:02d}" in cards]
                    if not numbers:
                        return [{"日付":str(day),"開催場":long_venue,"レース":"—",
                                 "状態":"出走表なし（非開催・データ欠落の可能性）"}]
                    return app_backtest.backtest(day, long_venue, len(numbers),
                                                 VENUE_COURSE_HISTORICAL,
                                                 result_lookup=lookup_long, race_numbers=numbers)
                except Exception as exc:
                    return [{"日付":str(day),"開催場":long_venue,"レース":"—",
                             "状態":f"日別データ取得エラー：{str(exc)[:90]}"}]
            with st.spinner("長期検証中。日付ごとに最大4件を並列処理します。"):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = {pool.submit(evaluate_day, day):day for day in dates}
                    for completed, future in enumerate(as_completed(futures),1):
                        long_rows.extend(future.result())
                        long_progress.progress(int(100 * completed / len(dates)))
            long_rows.sort(key=lambda r:(r["日付"],str(r["レース"])))
            st.session_state["backtest_rows"] = long_rows
            st.session_state["backtest_meta"] = ("長期", long_end, long_days, long_venue, long_scope)
            st.session_state.pop("model_comparison_result", None)
            st.success("長期検証が終了しました。照合済みレースの成績を下に表示します。")
        except Exception as exc:
            st.error(f"長期検証エラー：{type(exc).__name__}: {exc}")

bt_rows = st.session_state.get("backtest_rows")
if bt_rows:
    public_bt = [{k:v for k,v in r.items() if not k.startswith("_")} for r in bt_rows]
    st.dataframe(public_bt,hide_index=True,use_container_width=True)
    valid_bt = [r for r in bt_rows if r.get("状態")=="照合済み"]
    st.write(f"照合成功：{len(valid_bt)} / {len(bt_rows)}レース")
    errors_bt = [r for r in bt_rows if r.get("状態") != "照合済み"]
    if errors_bt:
        with st.expander(f"検証できなかった理由を確認（{len(errors_bt)}件）"):
            from collections import Counter
            reasons = Counter(str(r.get("状態", "不明")) for r in errors_bt)
            for reason, count in reasons.most_common(12):
                st.write(f"{count}件：{reason}")
            st.caption("以下はエラー全文を確認するための表示です。")
            st.dataframe([{"日付":r.get("日付"),"レース":r.get("レース"),"理由":str(r.get("状態"))} for r in errors_bt[:12]], hide_index=True, use_container_width=True)

    if valid_bt:
        n_bt=len(valid_bt)
        st.write(f"上位1点：旧 {sum(r['旧順位']==1 for r in valid_bt)/n_bt:.1%} ／ 新 {sum(r['新順位']==1 for r in valid_bt)/n_bt:.1%}")
        for label in ("Top5","Top10"):
            st.write(f"{label}：旧 {sum(r['旧'+label] for r in valid_bt)/n_bt:.1%} ／ 新 {sum(r['新'+label] for r in valid_bt)/n_bt:.1%}")
        st.write(f"平均予想順位：旧 {sum(r['旧順位'] for r in valid_bt)/n_bt:.1f} ／ 新 {sum(r['新順位'] for r in valid_bt)/n_bt:.1f}")
        st.write(f"新モデルの順位改善率：{sum(r['新順位']<r['旧順位'] for r in valid_bt)/n_bt:.1%}")
    if valid_bt:
        st.subheader("払戻金・回収率シミュレーション")
        st.caption("予想上位1・5・10点を各100円ずつ購入した仮定。払戻金は100円当たり。払戻データが確認できたレースのみ集計し、未取得分は除外します。")
        from datetime import date as _bt_date
        from backtest_cache import daily_results
        from payout_cache import trifecta_payout
        from history_data import VENUE_CODES as _bt_codes
        from concurrent.futures import ThreadPoolExecutor as _PayoutPool
        def _payout_one(r):
            day = _bt_date.fromisoformat(r["日付"])
            amount, _ = trifecta_payout(day, _bt_codes[r["開催場"]], int(r["レース"]), r["実際の3連単"])
            return amount
        if st.button("払戻金を取得して回収率を計算", key="calc_backtest_roi"):
            with st.spinner("払戻金を照合しています"):
                with _PayoutPool(max_workers=6) as pool:
                    payouts = list(pool.map(_payout_one, valid_bt))
            st.session_state["bt_roi"] = {"meta":st.session_state.get("backtest_meta"), "keys":[(r["日付"],r["開催場"],r["レース"],r["実際の3連単"]) for r in valid_bt], "payouts":payouts}
        roi_cache = st.session_state.get("bt_roi")
        keys_now = [(r["日付"],r["開催場"],r["レース"],r["実際の3連単"]) for r in valid_bt]
        if roi_cache and roi_cache["keys"] == keys_now:
            paid = [(r,v) for r,v in zip(valid_bt,roi_cache["payouts"]) if v is not None]
            st.write(f"払戻金確認済み：{len(paid)} / {len(valid_bt)}レース")
            if paid:
                roi_rows=[]
                for model in ("旧","新"):
                    for picks in (1,5,10):
                        stake = len(paid)*picks*100
                        returns = sum(value for r,value in paid if r[model+"順位"]<=picks)
                        roi_rows.append({"モデル":model,"購入点数":picks,"購入額（円）":stake,
                                         "払戻額（円）":returns,"損益（円）":returns-stake,
                                         "回収率（%）":round(100*returns/stake,1)})
                st.dataframe(roi_rows,hide_index=True,use_container_width=True)
                st.caption("これは過去レースの事後検証です。データの時点や選択バイアスが未検証で、将来の収益性を示しません。")
            if len(paid)<len(valid_bt):
                st.warning("払戻データが欠けたレースを除外しています。全レースの回収率ではありません。")
    st.subheader("期間別・競艇場別の成績")
    if valid_bt:
        import pandas as pd
        summary_bt = pd.DataFrame(valid_bt).copy()
        summary_bt["月"] = summary_bt["日付"].str.slice(0,7)
        for field in ("月", "開催場"):
            st.write("**" + ("月別" if field == "月" else "開催場別") + "**")
            grouped = summary_bt.groupby(field).agg(
                件数=("新順位","size"),
                Top1=("新順位",lambda x: (x==1).mean()),
                Top5=("新順位",lambda x: (x<=5).mean()),
                Top10=("新順位",lambda x: (x<=10).mean()),
                平均順位=("新順位","mean")).reset_index()
            st.dataframe(grouped, hide_index=True, use_container_width=True)

    st.caption("少数レースの試験的な過去検証です。過去成績の定義やデータの時点は今後も確認が必要です。将来の的中率・収益を保証しません。")
    st.download_button("検証結果をCSV保存", __import__("pandas").DataFrame(public_bt).to_csv(index=False).encode("utf-8-sig"),file_name="tatsuki_backtest.csv",mime="text/csv",key="bt_csv")

    st.subheader("🧠 予想モデルの重みを比較")
    st.caption("異なる日付のレースを時系列で分割。古い約70%で5候補を選び、新しい約30%は最後の評価にのみ使います。")
    if st.button("学習用・評価用に分けて比較", key="compare_model_weights"):
        try:
            import importlib
            import model_comparison
            importlib.reload(model_comparison)
            st.session_state["model_comparison_result"] = model_comparison.compare(bt_rows,VENUE_COURSE_HISTORICAL)
        except Exception as exc:
            st.error(f"比較できません：{exc}")
    comparison = st.session_state.get("model_comparison_result")
    if comparison:
        st.write(f"学習 {comparison['学習件数']}レース（{comparison['学習期間']}）／評価 {comparison['評価件数']}レース（{comparison['評価期間']}）")
        st.write(f"学習データで選ばれた候補：**{comparison['選択モデル']}**")
        st.dataframe([{"モデル":name,**stats} for name,stats in comparison["学習比較"]],hide_index=True,use_container_width=True)
        st.write("**未使用の新しい日付で評価**")
        st.dataframe([{"モデル":"現行モデル",**comparison["現行モデル評価"]},
                      {"モデル":"選択候補",**comparison["選択モデル評価"]}],hide_index=True,use_container_width=True)
        st.caption("候補を自動採用する機能ではありません。将来の的中率・収益は保証できません。長期コース統計の集計期間が検証対象日より新しい場合、厳密な過去時点の検証にはなりません。")

st.divider()
st.header("📊 3連単・検証成績")
st.caption("保存済みの予想と実際の結果を照合。データはStreamlitの一時的なSQLiteに保存されるため、再起動・再デプロイで消える可能性があります。必ずJSONをダウンロードして保管してください。")
saved_checks = tracking.report(conn)
if saved_checks:
    st.dataframe(saved_checks, hide_index=True, use_container_width=True)
    completed = [x for x in saved_checks if x["新順位"] is not None]
    prospective = [x for x in completed if x["区分"] == "事前登録（日付基準）"]
    st.write(f"保存 {len(saved_checks)}件／結果登録 {len(completed)}件／前日以前に保存した予想の結果登録 {len(prospective)}件")
    if completed:
        st.subheader("結果登録済みレースの集計（事後を含む）")
        n = len(completed)
        for label, key in [("上位5点","Top5"),("上位10点","Top10")]:
            old_rate = sum(x[f"旧{key}"] for x in completed)/n
            new_rate = sum(x[f"新{key}"] for x in completed)/n
            st.write(f"{label}：旧 {old_rate:.1%} ／ 新 {new_rate:.1%}")
        st.write(f"平均予想順位：旧 {sum(x['旧順位'] for x in completed)/n:.1f} ／ 新 {sum(x['新順位'] for x in completed)/n:.1f}")
        st.write(f"新モデルが改善した割合：{sum(x['新順位']<x['旧順位'] for x in completed)/n:.1%}")
        st.caption("当日・事後の照合も含まれます。事前予測の成績として解釈しないでください。")
    pending = [x for x in saved_checks if x["新順位"] is None]
    if pending:
        st.subheader("保存済み予想に結果を登録")

        st.caption("未登録のレースをまとめて確認できます。公式サイトとの照合が通った結果だけ保存します。")
        if st.button("未登録レースの結果を一括取得・照合", key="batch_verify_results"):
            from official_verification import verify_official_trifecta
            from datetime import datetime, timedelta, timezone
            today_jst = datetime.now(timezone(timedelta(hours=9))).date()
            successes = []
            skipped = []
            failed = []
            for entry in pending[:30]:
                key = entry["レース"]
                try:
                    race_day = datetime.strptime(key[:8], "%Y%m%d").date()
                    if race_day > today_jst:
                        skipped.append(key + "：未来のレース")
                        continue
                    result, source = fetch_actual_result(key)
                    if result is None:
                        skipped.append(key + "：結果未公開")
                        continue
                    verify_official_trifecta(key, result)
                    tracking.verify(conn, key, result)
                    successes.append(f"{key}：{result}")
                except Exception as exc:
                    failed.append(f"{key}：{exc}")
            st.success(f"照合・保存成功 {len(successes)}件／未公開など {len(skipped)}件／要確認 {len(failed)}件")
            if successes:
                st.write("保存成功：", "、".join(successes))
            if skipped:
                st.write("保留：", "、".join(skipped))
            if failed:
                st.warning("自動登録を中止したレース：" + " ／ ".join(failed))
            st.caption("一度に最大30レース。結果の集計表示は画面再読み込みで更新されます。")
        selected_key = st.selectbox("レース", [x["レース"] for x in pending], key="check_race")
        if st.button("公開データから結果を自動取得・照合", key="auto_actual_result"):
            try:
                with st.spinner("確定着順を確認中..."):
                    actual_auto, source_auto = fetch_actual_result(selected_key)
                if actual_auto is None:
                    st.warning("結果はまだ公開データにありません。少し時間をおいて再実行してください。")
                else:
                    from official_verification import verify_official_trifecta
                    official_url = verify_official_trifecta(selected_key, actual_auto)
                    tracking.verify(conn, selected_key, actual_auto)
                    st.success(f"公式サイトとCSVが一致：{actual_auto}。予想と照合しました。")
                    st.caption(f"公式照合元：{official_url}")
                    st.caption(f"結果データ：{source_auto}")
            except Exception as exc:
                st.error(f"自動取得・照合できませんでした：{exc}")
        st.warning("公式照合ができない・結果が不一致の場合、自動登録しません。手入力は公式結果を確認した場合だけ使用してください。")
        actual_boats = []
        check_cols = st.columns(3)
        for idx, label in enumerate(("1着", "2着", "3着")):
            with check_cols[idx]:
                actual_boats.append(st.selectbox(label, list(range(1,7)), index=idx, key=f"saved_actual_{idx}"))
        if st.button("保存済み予想と照合・結果を確定", key="commit_actual"):
            try:
                tracking.verify(conn, selected_key, "-".join(str(x) for x in actual_boats))
                st.success("結果を登録しました。成績を更新するには画面を再読み込みしてください。")
            except ValueError as exc:
                st.error(str(exc))
else:
    st.info("まだ保存済みの3連単予想はありません。")
st.download_button("検証記録をJSONでバックアップ", tracking.export_json(conn).encode("utf-8"),
                   file_name="tatsuki_trifecta_backup.json", mime="application/json", key="backup_trifecta")
with st.expander("バックアップから復元"):
    backup_file = st.file_uploader("以前保存したTATSUKI JSON", type=["json"], key="restore_trifecta")
    if backup_file is not None and st.button("バックアップを取り込む", key="restore_trifecta_button"):
        try:
            count = tracking.import_json(conn, backup_file.getvalue().decode("utf-8"))
            st.success(f"{count}件を確認して取り込みました（重複レースはスキップ）。再読み込みで反映されます。")
        except (ValueError, KeyError, TypeError) as exc:
            st.error(f"復元に失敗しました：{exc}")


st.divider()
st.header("📸 レース前データの時点保存")
st.caption("取得した出走表・過去10走を取得日時（日本時間）付きで保存します。翌日以降のレースは「前日以前」、当日は「締切前未確認」と区別。過去レースを事前保存扱いにはしません。")
import race_snapshots as snapshots
snapshots.setup(conn)
snapshot_day = st.date_input("保存するレース日", key="snapshot_day")
snapshot_venue = st.selectbox("保存する開催場", VENUES, index=18, key="snapshot_venue")
snapshot_race = st.selectbox("保存するレース", list(range(1,13)), key="snapshot_race")
if st.button("出走表を取得して日時付きで保存", key="capture_race_snapshot"):
    try:
        from datetime import timedelta
        today_jst = datetime.now(timezone(timedelta(hours=9))).date()
        if snapshot_day < today_jst:
            st.error("過去のレースは事前保存として登録できません。")
        else:
            importlib.invalidate_caches()
            importlib.reload(history_data)
            rows, sources = history_data.load_race(snapshot_day,snapshot_venue,snapshot_race)
            key,timing = snapshots.capture(conn,snapshot_day,snapshot_venue,snapshot_race,rows,sources)
            st.success(f"{key}：保存しました（{timing}）")
    except Exception as exc:
        st.error(f"保存できませんでした：{exc}")
        with st.expander("取得エラーの詳細診断（秘密情報は表示しません）", expanded=True):
            import history_data as _hd
            st.code(f"診断版: 2026-11-09-D\\n日付: {snapshot_day}\\n場: {snapshot_venue}\\nR: {snapshot_race}\\n例外: {type(exc).__name__}\\n詳細: {exc}\\n取得コード: {_hd.__file__}")
with st.expander("📝 自動取得できない場合：6艇を手入力して保存"):
    st.caption("公式出走表を見ながら6艇の情報を入力します。入力値は未検証の手入力データとして記録し、取得済みの公式データとは区別します。")
    manual_rows = []
    for boat in range(1, 7):
        st.markdown(f"**{boat}号艇**")
        name = st.text_input("選手名", key=f"manual_name_{boat}")
        number = st.text_input("登録番号（4桁）", key=f"manual_number_{boat}", max_chars=4)
        win = st.number_input("全国勝率", min_value=0.0, max_value=10.0, value=0.0, step=0.01, key=f"manual_win_{boat}")
        motor = st.number_input("モーター2連対率（%）", min_value=0.0, max_value=100.0, value=0.0, step=0.1, key=f"manual_motor_{boat}")
        manual_rows.append({"号艇": boat, "選手": name.strip(), "登録番号": number.strip(),
            "全国勝率": win if win > 0 else None, "当地勝率": None, "平均ST": None,
            "モーター2連対率": motor if motor > 0 else None, "モーター3連対率": None,
            "枠番別勝率": None, "枠番別平均ST": None, "過去10走": "未取得"})
    if st.button("手入力した6艇を日時付きで保存", key="manual_capture"):
        try:
            if any(not r["選手"] or not (len(r["登録番号"]) == 4 and r["登録番号"].isdigit()) for r in manual_rows):
                raise ValueError("全6艇の選手名と4桁の登録番号を入力してください。")
            if len({r["登録番号"] for r in manual_rows}) != 6:
                raise ValueError("登録番号が重複しています。")
            key, timing = snapshots.capture(conn, snapshot_day, snapshot_venue, snapshot_race, manual_rows, ["manual-entry:unverified"])
            st.success(f"{key}：手入力データを保存しました（{timing}）。数値が未入力の項目は未取得として扱います。")
        except Exception as exc:
            st.error(f"手入力の保存に失敗しました：{exc}")
with st.expander("📷 出走表スクショから6艇を読み取って保存"):
    st.caption("iPhoneのスクリーンショットを選択 → OCRで文字抽出 → 6艇の内容を修正・確認 → 保存。画像は外部AIサービスに送信しません。")
    screenshot = st.file_uploader("出走表のスクショ（PNG/JPG）", type=["png", "jpg", "jpeg"], key="race_card_screenshot")
    if screenshot is not None:
        import re as _ocr_re
        from io import BytesIO as _BytesIO
        from PIL import Image as _Image
        image_bytes = screenshot.getvalue()
        if len(image_bytes) > 10 * 1024 * 1024:
            st.error("画像は10MB以下にしてください。")
        else:
            if st.button("画像から6艇を読み取る", key="run_snapshot_ocr"):
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    import numpy as _np
                    image = _Image.open(_BytesIO(image_bytes)).convert("RGB")
                    if max(image.size) > 2400:
                        image.thumbnail((2400, 2400))
                    ocr_result, _ = RapidOCR()(_np.asarray(image))
                    lines = [str(item[1]).strip() for item in (ocr_result or []) if len(item) > 1]
                    st.session_state["snapshot_ocr_text"] = "\\n".join(lines)
                    candidates = []
                    for idx, line in enumerate(lines):
                        match = _ocr_re.search(r"(?<!\\d)([3-5]\\d{3})(?!\\d)", line)
                        if match:
                            nearby = " ".join(lines[max(0,idx-2):idx+3])
                            candidates.append((match.group(1), nearby))
                    st.session_state["snapshot_ocr_rows"] = [
                        {"号艇": n, "登録番号": candidates[n-1][0] if n <= len(candidates) else "",
                         "選手名": "", "全国勝率": None, "モーター2連対率": None}
                        for n in range(1,7)]
                    st.info(f"OCRで{len(lines)}行、登録番号候補{len(candidates)}件を検出しました。号艇との対応と選手名・数値を確認して入力してください。")
                except Exception as exc:
                    st.error(f"画像読み取りに失敗しました：{type(exc).__name__}: {exc}")
            if "snapshot_ocr_text" in st.session_state:
                with st.expander("OCRで読み取った文字（確認用）"):
                    st.text_area("抽出テキスト", value=st.session_state["snapshot_ocr_text"], height=180, disabled=True)
            if "snapshot_ocr_rows" in st.session_state:
                st.warning("OCRは誤認識します。登録番号候補は号艇順とは限りません。公式出走表と照らし、必ず6艇を修正・確認してください。")
                edited = st.data_editor(st.session_state["snapshot_ocr_rows"], num_rows="fixed", hide_index=True,
                    disabled=["号艇"], use_container_width=True, key="snapshot_ocr_editor")
                confirm = st.checkbox("6艇の号艇・選手名・登録番号・入力数値を公式出走表と照合しました", key="snapshot_ocr_confirm")
                if st.button("確認した6艇を日時付きで保存", key="snapshot_ocr_save", disabled=not confirm):
                    try:
                        rows = []
                        for r in edited:
                            name = str(r["選手名"] or "").strip()
                            number = str(r["登録番号"] or "").strip()
                            if not name or not _ocr_re.fullmatch(r"\\d{4}", number):
                                raise ValueError("6艇すべての選手名と4桁の登録番号が必要です。")
                            def optional_number(value, maximum):
                                if value is None or str(value).strip() == "":
                                    return None
                                x = float(value)
                                if not 0 <= x <= maximum:
                                    raise ValueError("勝率・モーター2連対率の値を確認してください。")
                                return x
                            rows.append({"号艇": int(r["号艇"]), "選手": name, "登録番号": number,
                                "全国勝率": optional_number(r["全国勝率"], 10),
                                "当地勝率": None, "平均ST": None,
                                "モーター2連対率": optional_number(r["モーター2連対率"], 100),
                                "モーター3連対率": None, "枠番別勝率": None,
                                "枠番別平均ST": None, "過去10走": "未取得"})
                        if len({r["登録番号"] for r in rows}) != 6:
                            raise ValueError("登録番号が重複しています。")
                        key, timing = snapshots.capture(conn, snapshot_day, snapshot_venue, snapshot_race, rows, ["screenshot-ocr:user-verified"])
                        st.success(f"{key}：スクショから確認した6艇を保存しました（{timing}）。")
                    except Exception as exc:
                        st.error(f"保存できませんでした：{exc}")
saved_snapshots = snapshots.list_snapshots(conn)
if saved_snapshots:
    st.dataframe(saved_snapshots,hide_index=True,use_container_width=True)
st.info("Supabase接続中は保存・検証記録を自動同期します。JSONの手動バックアップも予備としておすすめです。" if st.session_state.get("supabase_persistent") else "ローカル保存は再起動・再デプロイで消える場合があります。JSONでバックアップしてください。")
st.download_button("時点保存データをJSONでバックアップ",
                   snapshots.export_json(conn).encode("utf-8"),
                   file_name="tatsuki_race_snapshots.json",mime="application/json",
                   key="download_race_snapshots")
with st.expander("時点保存データをJSONから復元"):
    snapshot_upload = st.file_uploader("バックアップJSON",type=["json"],key="snapshot_restore")
    if snapshot_upload is not None and st.button("時点保存データを復元",key="snapshot_restore_btn"):
        try:
            n = snapshots.import_json(conn,snapshot_upload.getvalue().decode("utf-8"))
            st.success(f"{n}件を復元しました。")
        except Exception as exc:
            st.error(f"復元できませんでした：{exc}")
st.caption("保存日時はアプリサーバーで取得します。公開元の元データ作成時刻は保証されません。保存済みの過去10走は、後日のバックテストには自動投入しません。")

st.subheader("保存済みレースの予想と結果照合")
import json as snapshot_json
from datetime import date as snapshot_date, timedelta as snapshot_delta
from history_data import VENUE_CODES as snapshot_codes
from app_backtest import rank_race as snapshot_rank
from backtest_cache import cached_result as snapshot_result
snapshot_records = conn.execute("SELECT race_key,race_date,venue,race_no,rows_json,timing FROM race_snapshots ORDER BY captured_at DESC").fetchall()
if snapshot_records:
    chosen = st.selectbox("保存済みレース", range(len(snapshot_records)), format_func=lambda i: snapshot_records[i][0], key="snapshot_choice")
    snap = snapshot_records[chosen]
    snap_day = snapshot_date.fromisoformat(snap[1])
    snap_key = f"{snap_day:%Y%m%d}-{snapshot_codes[snap[2]]:02d}-{int(snap[3]):02d}"
    existing = conn.execute("SELECT actual FROM trifecta_checks WHERE race_key=?", (snap_key,)).fetchone()
    if existing is None and st.button("保存データから予想を確定", key="snapshot_predict"):
        try:
            old, new = snapshot_rank(snapshot_json.loads(snap[4]), snap[2], {snap[2]: [50.0]*6})
            category = tracking.save(conn, snap_key, snap_day, old, new)
            st.success(f"120通りの予想を保存しました：{category}")
        except Exception as exc:
            st.error(f"予想の保存に失敗：{exc}")
    if existing is not None and existing[0] is None and st.button("終了したレースの結果を照合", key="snapshot_check"):
        try:
            today = datetime.now(timezone(snapshot_delta(hours=9))).date()
            if snap_day >= today:
                st.warning("当日レースは終了時刻を確認できないため、翌日以降に照合してください。")
            else:
                result, source = snapshot_result(snap_day, snapshot_codes[snap[2]], int(snap[3]))
                if result is None:
                    st.warning("結果データがまだありません。")
                else:
                    tracking.verify(conn, snap_key, result)
                    st.success(f"結果 {result} を登録しました。")
        except Exception as exc:
            st.error(f"照合できませんでした：{exc}")
    checked = {r["レース"]: r for r in tracking.report(conn)}
    matched = []
    for record in snapshot_records:
        day = snapshot_date.fromisoformat(record[1])
        key = f"{day:%Y%m%d}-{snapshot_codes[record[2]]:02d}-{int(record[3]):02d}"
        item = checked.get(key)
        if item and item["新順位"] is not None:
            matched.append({"日付":record[1], "開催場":record[2], "R":record[3], "保存区分":record[5],
                            "結果":item["実際の着順"], "予想順位":item["新順位"],
                            "Top1":item["新順位"] == 1, "Top5":item["新Top5"], "Top10":item["新Top10"]})
    st.write(f"保存済み {len(snapshot_records)}件 ／ 照合済み {len(matched)}件")
    if matched:
        st.dataframe(matched, hide_index=True, use_container_width=True)
        earlier = [r for r in matched if r["保存区分"] == "前日以前"]
        if earlier:
            st.write(f"前日以前の保存分：{len(earlier)}件")
            for label in ("Top1","Top5","Top10"):
                st.write(f"{label}：{sum(bool(r[label]) for r in earlier)/len(earlier):.1%}")
else:
    st.info("まずレース前データを保存してください。")


conn.close()
