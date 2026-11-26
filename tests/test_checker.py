"""checker.py 의 날짜·정원 파싱 로직 테스트 (브라우저 없이 실행)."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checker  # noqa: E402


class ParseTest(unittest.TestCase):
    def test_date_formats(self):
        target = date(2026, 11, 27)  # 금요일
        for text in ["11/27(금) 출석 예약", "11.27 예약", "11월 27일 예약", "27일(금) 예약"]:
            self.assertTrue(checker.text_mentions_date(text, target), text)
        self.assertFalse(checker.text_mentions_date("11/26(목) 출석 예약", target))
        self.assertFalse(checker.text_mentions_date("", target))

    def test_spot_count(self):
        self.assertEqual(checker.parse_spot_count("셋째타임 : 저녁 6시30분 (8/11)"), (8, 11))
        self.assertEqual(checker.parse_spot_count("11명 참여"), (11, None))
        self.assertEqual(checker.parse_spot_count("아무 숫자 없음"), (None, None))

    def test_weekday_label(self):
        self.assertEqual(checker.WEEKDAY_KO[date(2026, 11, 27).weekday()], "금")


if __name__ == "__main__":
    unittest.main()
