"""설정 상수: API URL, 헤더, 거래 유형, 기본값."""

import os

# 2026-07: new.land → neo.land 로 이전.
# 2026-10-28: 네이버가 neo.land/api 를 닫고 fin.land.naver.com/front-api/v1 로 다시 옮김(옛 경로 404).
# 새 API 는 진짜 브라우저에서 부른 요청만 받아 줘서 fin_browser.py 가 화면 없는 Chromium 안에서 부른다.
FIN_ORIGIN = "https://fin.land.naver.com"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36"
)

# Rate limiting
REQUEST_DELAY_SEC = 0.5       # 요청 간 최소 딜레이

# 스냅샷 저장 경로 (변동 감지용)
SNAPSHOT_DIR = os.path.expanduser("~/.naver-land")
SNAPSHOT_PATH = os.path.join(SNAPSHOT_DIR, "snapshot.json")

# MCP 타임아웃(60초) 에 맞춘 기본 한도.
# crawl_district 한 번에 처리할 최대 단지 수. dealCount 내림차순으로 선택.
DEFAULT_MAX_COMPLEXES = 5

# search_apartments 응답 매물 수 기본/최대치.
# 무제한 반환 시 대형 지역(개포동 2,500건+)에서 응답이 수백만 자가 되어
# MCP 클라이언트 토큰 한도를 넘기므로 반드시 상한을 둔다.
DEFAULT_SEARCH_LIMIT = 30
MAX_SEARCH_LIMIT = 100

# crawl_district 전체 수집 시간 예산(초). MCP 클라이언트 타임아웃(60초)보다
# 여유 있게 짧아야 한다 — 초과 시 그때까지 모은 부분 결과를 반환한다.
CRAWL_TIME_BUDGET_SEC = 35.0

# 가격 기본 범위 (만원 단위) — 가격 명시 안 한 호출 시 전 범위 검색
DEFAULT_PRICE_MIN = 0
DEFAULT_PRICE_MAX = 999999

TRADE_TYPES = {
    "A1": "매매",
    "B1": "전세",
    "B2": "월세",
}

# 지역코드는 네이버 검색(/search/autocomplete/legalDivisions)으로 동적 조회 — 하드코딩 없음.
# naver_land.resolve_region() / crawl_district() 참조.
