"""Position-aware extraction for BOATRACE mobile race-card screenshots.

Only fills values when a row and column can be located confidently.
Uncertain fields remain blank for user review.
"""
import re

REG = re.compile(r"(?<!\d)([3-5]\d{3})(?!\d)")
DEC = re.compile(r"(?<!\d)(\d{1,3}\.\d{1,2})(?!\d)")

def extract(images):
    # images: [(filename, [(polygon, text, confidence), ...]), ...]
    rows = [{"号艇": i, "登録番号": "", "選手名": "", "平均ST": None,
             "全国勝率": None, "当地勝率": None, "モーター2連対率": None,
             "モーター3連対率": None, "ボート2連対率": None, "今節成績": ""}
            for i in range(1, 7)]
    warnings = []
    for filename, detections in images:
        items = []
        for entry in detections:
            try:
                poly, txt, conf = entry
                xs = [float(p[0]) for p in poly]
                ys = [float(p[1]) for p in poly]
                items.append((sum(xs)/len(xs), sum(ys)/len(ys), str(txt).strip()))
            except (ValueError, TypeError, IndexError, ZeroDivisionError):
                continue
        if not items:
            continue
        full = " ".join(x[2] for x in items)
        is_stats = ("モーター" in full or "全国" in full) and ("2連率" in full or "勝率" in full)
        is_basic = "平均ST" in full or "年齢" in full or "出身地" in full
        if not (is_stats or is_basic):
            continue
        # Registration numbers are positioned in the same horizontal band as each racer.
        reg_items = []
        for x,y,t in items:
            m = REG.search(t.replace(" ", ""))
            if m and x < 750:
                reg_items.append((y,x,m.group(1)))
        # OCR can detect repeated numbers in other columns. Only process when six
        # distinct registration numbers appear in vertical order.
        reg_items.sort()
        distinct = []
        for y,x,num in reg_items:
            if num not in [v[2] for v in distinct] and (not distinct or y-distinct[-1][0]>30):
                distinct.append((y,x,num))
        if len(distinct) != 6:
            # Other screenshots may omit registration numbers entirely.
            markers = {}
            for xx, yy, txt in items:
                clean = txt.strip().translate(str.maketrans("１２３４５６", "123456"))
                if clean in ("1", "2", "3", "4", "5", "6") and xx < 160:
                    markers.setdefault(int(clean), []).append(yy)
            centers = [min(markers[i]) for i in range(1, 7)] if all(markers.get(i) for i in range(1, 7)) else []
            if not centers or any(centers[i] >= centers[i+1] for i in range(5)):
                warnings.append(f"{filename}: 6艇の行位置を特定できません（登録番号{len(distinct)}件）。OCR文字を確認してください。")
                continue
            distinct = [(centers[i], 0, "") for i in range(6)]
        else:
            centers = [r[0] for r in distinct]
        bounds = [float("-inf")] + [(centers[i]+centers[i+1])/2 for i in range(5)] + [float("inf")]
        for i, (y, x, number) in enumerate(distinct):
            row = rows[i]
            if number and row["登録番号"] and row["登録番号"] != number:
                warnings.append(f"{filename}: {i+1}号艇の登録番号が他画像と不一致")
                continue
            if number:
                row["登録番号"] = number
            band = [(xx,yy,t) for xx,yy,t in items if bounds[i] <= yy < bounds[i+1]]
            if is_basic:
                # Japanese racer names are typically directly below registration numbers.
                names = [(yy,t) for xx,yy,t in band if xx < 520 and
                         re.fullmatch(r"[一-龯々ぁ-んァ-ヶー・\s]{3,12}",t) and
                         not any(v in t for v in ("愛知","山口","静岡","広島","埼玉","東京","福岡","大阪","兵庫"))]
                names.sort()
                if names:
                    row["選手名"] = names[-1][1].replace(" ","")
                st_vals = [(xx,float(m.group(1))) for xx,yy,t in band
                           if 520 < xx < 680 for m in [DEC.search(t)] if m and float(m.group(1)) <= 0.40]
                if len(st_vals)==1:
                    row["平均ST"] = st_vals[0][1]
            if is_stats:
                # Values are assigned by x coordinate, not OCR reading order.
                # Each column contains win rate, 2-place %, 3-place % vertically.
                columns = [(360,465,"全国勝率"),(465,570,"当地勝率"),
                           (570,705,"モーター2連対率"),(705,840,"ボート2連対率")]
                for lo,hi,field in columns:
                    vals = sorted([(yy,float(m.group(1))) for xx,yy,t in band
                                  if lo <= xx < hi for m in [DEC.search(t)] if m], key=lambda a:a[0])
                    target = 1 if "2連対率" in field else 0
                    if len(vals)>=target+1:
                        v=vals[target][1]
                        if 0 <= v <= (100 if target else 10):
                            row[field] = v
    return rows, warnings
