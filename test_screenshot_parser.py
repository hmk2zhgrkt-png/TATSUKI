"""Regression tests using the OCR text supplied from three BOATRACE screenshots."""
import unittest
from screenshot_parser import extract

BASIC = """基本情報 勝率 今節成 支部／出身地 登番/級别 平均ST
山口／山口 5231/B1 0.17 9R 寺田空詩 23/45.5
愛知/愛知 3528/B1 2 0.20 笠野友紀惠 55/47.3
愛知／琦玉 4183/B1 3 0.13 6R 宇野弥生 40/48.4
静网/愛知 3518/B1 4 0.19 10R 食田郁美 58/47.8
静网/静网 4246/B1 5 0.18 8R 喜多那由夏 44/47.1
岛／岛 5442/B2 6 0.20 池田美優 19/46.5"""
STATS = """基本情報 勝率 全国 当地 2連率 3連率
5.38 5.58 49 59 5231/B1 1 32.89 37.61 36.07 33.33 寺田空詩 51.32 57.80 49.18 42.11
3.93 3.71 42 53 3528/B1 2 20.99 0.00 32.26 32.79 笠野友紀惠 28.40 28.57 47.58 50.00
3.54 7.00 16 20 4183/B1 3 23.08 52.50 33.33 26.89 宇野弥生 26.92 70.00 51.28 54.62
4.37 5.30 29 11 3518/B1 4 21.43 30.00 27.97 31.15 田郁美 41.43 60.00 49.15 44.26
3.67 3.24 59 41 4246/B1 5 14.29 6.90 36.80 43.59 喜多那由夏 30.95 27.59 54.40 61.54
1.88 1.20 35 12 5442/B2 6 1.52 0.00 28.69 22.69 池田美優 6.06 0.00 47.54 38.66"""
SERIES = "今節成續 1日目 2日目 3日目 4日目 5日目 6日目 7日目"

def detections(text):
    return [([[0, 0], [1, 0], [1, 1], [0, 1]], token, 0.99)
            for token in text.split()]

class TestScreenshotParser(unittest.TestCase):
    def test_three_screenshots(self):
        rows, warnings = extract([
            ("IMG_9382.png", detections(BASIC)),
            ("IMG_9383.png", detections(STATS)),
            ("IMG_9384.png", detections(SERIES)),
        ])
        expected = [
            ("5231", "寺田空詩", .17, 5.38, 5.58, 36.07, 33.33),
            ("3528", "笠野友紀惠", .20, 3.93, 3.71, 32.26, 32.79),
            ("4183", "宇野弥生", .13, 3.54, 7.00, 33.33, 26.89),
            ("3518", "食田郁美", .19, 4.37, 5.30, 27.97, 31.15),
            ("4246", "喜多那由夏", .18, 3.67, 3.24, 36.80, 43.59),
            ("5442", "池田美優", .20, 1.88, 1.20, 28.69, 22.69),
        ]
        for row, (reg, name, st, national, local, motor, boat) in zip(rows, expected):
            with self.subTest(reg=reg):
                self.assertEqual(row["登録番号"], reg)
                self.assertEqual(row["選手名"], name)
                for key, value in [("平均ST", st), ("全国勝率", national),
                                   ("当地勝率", local), ("モーター2連対率", motor),
                                   ("ボート2連対率", boat)]:
                    self.assertAlmostEqual(row[key], value, msg=key)

if __name__ == "__main__":
    unittest.main()
