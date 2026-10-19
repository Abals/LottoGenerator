"""로또 6/45 데이터 관리, 번호 생성, 당첨 이력 검사, 통계 (화면과 무관한 핵심 로직)."""

import json
import os
import random
import sys
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

from lotto_seed import SEED

KST = timezone(timedelta(hours=9))
FIRST_DRAW_DATE = date(2002, 12, 7)      # 1회 추첨일 (토요일)
DRAW_OPEN_HOUR = 20                     # 토요일 오후 8시 이후부터 이번 회차 결과를 찾기 시작
NUMBERS = range(1, 46)

# 동행복권 회차별 당첨결과 조회 주소 (2026년 개편 이후 사이트가 쓰는 주소)
API_URL = "https://www.dhlottery.co.kr/lt645/selectPstLt645Info.do?srchLtEpsd={round}"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


# ---------------------------------------------------------------- 저장 위치
def data_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "LottoGenerator")
    else:
        path = os.path.join(os.path.expanduser("~"), ".lotto_generator")
    os.makedirs(path, exist_ok=True)
    return path


def read_json(name, default):
    try:
        with open(os.path.join(data_dir(), name), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(name, obj):
    path = os.path.join(data_dir(), name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 회차 계산
def draw_date(round_no):
    return FIRST_DRAW_DATE + timedelta(weeks=round_no - 1)


def expected_latest_round(now=None):
    """지금 시각 기준으로 결과가 나왔어야 할 마지막 회차.
    토요일 오후 8시(KST)가 지나면 그 주 회차를 포함한다."""
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    first = datetime(2002, 12, 7, DRAW_OPEN_HOUR, 0, tzinfo=KST)
    if now < first:
        return 0
    return (now - first) // timedelta(weeks=1) + 1


def rank_of(nums, draw):
    """nums(6개)가 draw에서 몇 등인지. 낙첨이면 0."""
    hit = len(set(nums) & set(draw["numbers"]))
    if hit == 6:
        return 1
    if hit == 5:
        return 2 if draw["bonus"] in nums else 3
    if hit == 4:
        return 4
    if hit == 3:
        return 5
    return 0


# ---------------------------------------------------------------- 데이터 저장소
class FetchError(Exception):
    pass


def fetch_round(round_no, timeout=15):
    """동행복권에서 한 회차를 받아온다. 아직 추첨 결과가 없으면 None."""
    req = urllib.request.Request(
        API_URL.format(round=round_no),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                 "Referer": "https://www.dhlottery.co.kr/"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            text = res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise FetchError("동행복권 서버 응답 오류 (HTTP %s)" % e.code)
    except (urllib.error.URLError, OSError) as e:
        raise FetchError("동행복권에 연결할 수 없습니다: %s" % getattr(e, "reason", e))
    try:
        data = json.loads(text)
        items = data["data"]["list"]
    except (ValueError, KeyError, TypeError):
        raise FetchError("동행복권 응답 형식이 예상과 다릅니다 (점검 중이거나 사이트가 바뀌었을 수 있음)")
    if not items:
        return None
    x = items[0]
    try:
        if int(x["ltEpsd"]) != round_no:
            raise FetchError("%d회를 요청했는데 %s회가 왔습니다" % (round_no, x["ltEpsd"]))
        ymd = str(x["ltRflYmd"])
        nums = sorted(int(x["tm%dWnNo" % i]) for i in range(1, 7))
        bonus = int(x["bnsWnNo"])
    except (KeyError, ValueError, TypeError):
        raise FetchError("동행복권 응답에서 당첨번호를 읽지 못했습니다")
    if len(set(nums)) != 6 or not all(1 <= n <= 45 for n in nums + [bonus]):
        raise FetchError("%d회 당첨번호가 올바르지 않습니다" % round_no)
    return {"round": round_no, "date": "%s-%s-%s" % (ymd[:4], ymd[4:6], ymd[6:8]),
            "numbers": nums, "bonus": bonus}


class LottoData:
    SAVE_FILE = "draws.json"

    def __init__(self):
        self.lock = threading.Lock()
        self.draws = {}
        for line in SEED.strip().splitlines():
            r, ymd, nums, bonus = line.split("|")
            self.draws[int(r)] = {"round": int(r), "date": "%s-%s-%s" % (ymd[:4], ymd[4:6], ymd[6:8]),
                                  "numbers": [int(n) for n in nums.split(",")], "bonus": int(bonus)}
        for d in read_json(self.SAVE_FILE, []):
            try:
                self.draws[int(d["round"])] = {"round": int(d["round"]), "date": d["date"],
                                               "numbers": sorted(int(n) for n in d["numbers"]),
                                               "bonus": int(d["bonus"])}
            except (KeyError, ValueError, TypeError):
                continue
        self._reindex()

    def _reindex(self):
        self.combos = {tuple(sorted(d["numbers"])): r for r, d in self.draws.items()}

    @property
    def latest_round(self):
        return max(self.draws) if self.draws else 0

    def latest_draw(self):
        return self.draws.get(self.latest_round)

    def next_round(self):
        return self.latest_round + 1

    def is_update_due(self, now=None):
        return expected_latest_round(now) > self.latest_round

    def update(self, fetch=fetch_round):
        """빠진 회차를 받아 저장한다. (새로 받은 회차 목록, 아직 결과 대기 중인지) 반환."""
        with self.lock:
            target = expected_latest_round()
            new = []
            r = self.latest_round + 1
            while r <= target:
                d = fetch(r)
                if d is None:
                    break
                self.draws[r] = d
                new.append(d)
                r += 1
            if new:
                self._reindex()
                seed_max = int(SEED.strip().splitlines()[-1].split("|")[0])
                write_json(self.SAVE_FILE, [d for k, d in sorted(self.draws.items()) if k > seed_max])
            return new, self.latest_round < target

    # ------------------------------------------------------------ 통계
    def top_numbers(self, k=6):
        counts = {n: 0 for n in NUMBERS}
        for d in self.draws.values():
            for n in d["numbers"]:
                counts[n] += 1
        total = len(self.draws)
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [(n, c, (c * 100.0 / total) if total else 0.0) for n, c in ranked]

    # ------------------------------------------------------------ 검사
    def check(self, nums):
        """6개 번호의 과거 이력. exact: 1등으로 나온 회차 번호(없으면 None)."""
        nums = sorted(nums)
        exact = self.combos.get(tuple(nums))
        ranks = {1: [], 2: [], 3: [], 4: 0, 5: 0}
        for r, d in self.draws.items():
            k = rank_of(nums, d)
            if k in (1, 2, 3):
                ranks[k].append(r)
            elif k in (4, 5):
                ranks[k] += 1
        for k in (1, 2, 3):
            ranks[k].sort()
        return {"exact": exact, "ranks": ranks, "total": len(self.draws)}

    # ------------------------------------------------------------ 생성
    def generate(self, fixed=(), games=1, rng=None):
        """과거 1등 조합과 겹치지 않는 번호 games개. fixed 번호는 모든 게임에 포함.
        fixed가 6개면 조합이 하나뿐이므로 그 1게임만 돌려준다."""
        rng = rng or random.SystemRandom()
        fixed = sorted(set(int(n) for n in fixed))
        if len(fixed) > 6 or any(n not in NUMBERS for n in fixed):
            raise ValueError("포함할 번호는 1~45 중 최대 6개까지 고를 수 있습니다.")
        if not 1 <= games <= 5:
            raise ValueError("게임 수는 1~5개입니다.")
        if len(fixed) == 6:
            past = self.combos.get(tuple(fixed))
            if past:
                raise ValueError("고른 6개 번호는 %d회(%s) 1등 당첨번호라서 만들 수 없어요.\n"
                                 "번호를 하나 이상 바꿔 주세요." % (past, self.draws[past]["date"]))
            return [fixed]
        pool = [n for n in NUMBERS if n not in fixed]
        need = 6 - len(fixed)
        results, seen = [], set()
        for _ in range(games * 2000):
            combo = tuple(sorted(fixed + rng.sample(pool, need)))
            if combo in self.combos or combo in seen:
                continue
            seen.add(combo)
            results.append(list(combo))
            if len(results) == games:
                return results
        raise ValueError("조건에 맞는 새 조합을 충분히 만들지 못했습니다. 포함할 번호를 줄여 주세요.")


def fmt_nums(nums):
    return " ".join("%02d" % n for n in nums)
