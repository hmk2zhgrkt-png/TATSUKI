"""Extract six boat-racing entries from mobile BOATRACE OCR results.

The official mobile page's OCR reading order is much more stable than its
absolute pixel coordinates. Match registration IDs first, then nearby fields.
"""
import re

REG = re.compile(r"(?<!\d)([3-5]\d{3})\s*/\s*[AB]\d", re.I)
DEC = re.compile(r"^\d{1,3}\.\d{1,2}$")
NAME = re.compile(r"^[一-龯々ぁ-んァ-ヶー]{2,9}$")
KNOWN = ("5231", "3528", "4183", "3518", "4246", "5442")

def _number(s):
    s = s.strip()
    return float(s) if DEC.fullmatch(s) else None

def _name(tokens, start, end):
    for t in tokens[start:end]:
        if NAME.fullmatch(t) and not any(x in t for x in (
            "基本情報", "勝率", "今節成績", "投票", "通知", "全国", "当地",
            "愛知", "静岡", "広島", "山口", "埼玉", "福岡", "大阪",
            "出走表", "選手名", "早見", "平均")):
            return t
    return ""

def extract(images):
    rows = [{"号艇": i, "登録番号": "", "選手名": "", "平均ST": None,
             "全国勝率": None, "当地勝率": None, "モーター2連対率": None,
             "モーター3連対率": None, "ボート2連対率": None, "今節成績": ""}
            for i in range(1, 7)]
    warnings = []
    for filename, detections in images:
        # Preserve OCR's natural reading order, which includes registration
        # numbers and values even if bounding boxes vary by device.
        tokens = [str(d[1]).strip().replace(" ", "") for d in detections
                  if len(d) > 1 and str(d[1]).strip()]
        text = " ".join(tokens)
        is_basic = "平均ST" in text or "年齢" in text or "体重" in text
        is_stats = "勝率" in text and ("2連率" in text or "3連率" in text)
        matches = [(i, REG.search(t).group(1)) for i, t in enumerate(tokens)
                   if REG.search(t)]
        if len(matches) != 6:
            warnings.append(f"{filename}: 登録番号を{len(matches)}件検出。6艇揃う画像のみ選手別の数値を抽出します。")
            continue
        for boat, (pos, number) in enumerate(matches, 1):
            row = rows[boat-1]
            if row["登録番号"] and row["登録番号"] != number:
                warnings.append(f"{filename}: {boat}号艇の登録番号が別画像と一致しません")
                continue
            row["登録番号"] = number
            next_pos = matches[boat][0] if boat < 6 else len(tokens)
            prev_pos = matches[boat-2][0] if boat > 1 else 0
            if is_basic:
                # OCR output: registration / average ST / name / age-weight.
                nearby = tokens[pos+1:min(pos+6, next_pos)]
                st = next((_number(t) for t in nearby
                           if _number(t) is not None and 0 <= _number(t) <= .4), None)
                if st is not None:
                    row["平均ST"] = st
                name = _name(tokens, pos+1, min(pos+9, next_pos))
                if name:
                    row["選手名"] = name
            if is_stats:
                # OCR output for each racer:
                # nationwide win rate, local win rate, motor No, boat No,
                # registration, nationwide 2-place, local 2-place,
                # motor 2-place, boat 2-place, name, four 3-place rates.
                before = [_number(t) for t in tokens[prev_pos+1:pos]
                          if _number(t) is not None]
                if len(before) >= 2:
                    national, local = before[-2:]
                    if national <= 10 and local <= 10:
                        row["全国勝率"], row["当地勝率"] = national, local
                after = [_number(t) for t in tokens[pos+1:next_pos]
                         if _number(t) is not None]
                if len(after) >= 4:
                    row["モーター2連対率"] = after[2]
                    row["ボート2連対率"] = after[3]
                if len(after) >= 8:
                    row["モーター3連対率"] = after[6]
                name = _name(tokens, pos+1, min(pos+9, next_pos))
                if name and not row["選手名"]:
                    row["選手名"] = name
        if not (is_basic or is_stats):
            warnings.append(f"{filename}: 今節成績の画像はOCR抽出済みですが、着順の自動割当は未対応です。")
    if not any(r["登録番号"] for r in rows):
        warnings.append("登録番号を取得できません。OCRで読み取った文字を確認してください。")
    return rows, warnings
