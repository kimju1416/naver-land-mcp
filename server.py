"""naver-land-mcp FastMCP 서버.

도구 6개 (네이버 부동산 fin.land API 기반, 전국 지원 — 집·학교 PC에서만, 데이터센터 IP는 네이버가 막음):
- watch_complexes: 관심 단지 매물+시세(한국부동산원+KB)+실거래가 일괄 조회
- search_apartments: 동/구/군 + 가격 범위로 매물 검색 (매매/전세/월세)
- get_complex_info: 단지 상세 정보
- get_complex_price_info: 단지 평형별 시세(한국부동산원+KB) + 실거래가
- resolve_district: 지역명 → cortarNo 조회
- list_districts: 전국 시/도 17개 목록
"""

from __future__ import annotations

import json
import os

from fastmcp import FastMCP

from config import (
    DEFAULT_PRICE_MAX,
    DEFAULT_PRICE_MIN,
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
)
from filter import filter_and_rank
from naver_land import (
    _client,
    crawl_district,
    get_complex_detail,
    get_complex_prices,
    resolve_region,
    search_complex_by_name,
    watch_complexes_data,
)
from report import format_report
from snapshot import compare_with_previous

mcp = FastMCP("naver-land")


@mcp.tool
def watch_complexes(
    complex_names: list[str],
    price_min: int = DEFAULT_PRICE_MIN,
    price_max: int = DEFAULT_PRICE_MAX,
) -> str:
    """관심 단지들의 매물 + 시세 + 실거래가를 한번에 조회합니다.

    각 단지별로 현재 매물, 평형별 시세, 최근 실거래가를 반환합니다.
    이전 스냅샷과 비교하여 신규/삭제/가격변동 매물도 함께 반환합니다.

    Args:
        complex_names: 관심 단지명 목록 (예: ["가천대역두산위브", "광교해모로"])
        price_min: 최소 가격 (만원). 기본 0 (무제한)
        price_max: 최대 가격 (만원). 기본 999999 (무제한)
    """
    data = watch_complexes_data(complex_names, price_min, price_max)

    # 이전 스냅샷 대비 변동 감지
    diff = compare_with_previous(data["all_articles"])

    # 포맷된 마크다운 리포트 생성
    report = format_report(
        data["complexes"],
        {**diff, "total_current": diff["total_current"]},
    )
    return report


@mcp.tool
def search_apartments(
    district: str,
    price_min: int = DEFAULT_PRICE_MIN,
    price_max: int = DEFAULT_PRICE_MAX,
    trade_type: str = "A1",
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> str:
    """지역 + 가격 범위로 아파트 매물을 검색합니다. 전국 + 매매/전세/월세 모두 지원.

    지역 지정 방식 (네이버 부동산 기준 동적 조회):
    - 동 단위: "관평동", "개포동", "반포동"
    - 구/군 단위: "강남구", "유성구", "성남시 분당구"
    - 시/도 단위(예: "서울시")는 범위 과대로 거부됨

    매물이 많은 지역은 매물 수가 많은 단지부터 우선 수집하며,
    limit 충족 시 조기 종료합니다. 특정 단지의 매물만 보려면
    가격 범위를 좁히거나 단지명으로 get_complex_price_info를 사용하세요.

    Args:
        district: 조회할 지역명 (동/구/군). 예: "관평동", "강남구", "성남시 분당구"
        price_min: 최소 가격 (만원). 매매=매매가, 전세=보증금, 월세=보증금 기준
        price_max: 최대 가격 (만원)
        trade_type: 거래 유형.
            A1 = 매매 (기본)
            B1 = 전세
            B2 = 월세 (응답에 rentPrice 포함)
        limit: 반환할 최대 매물 수 (기본 30, 최대 100)

    반환 JSON의 각 매물에는 tradeType, tradeTypeName, price, rentPrice 필드가 포함됩니다.
    월세의 경우 price는 보증금, rentPrice는 월세(만원/월)입니다.
    같은 물건을 여러 중개사가 올린 중복 매물은 하나로 묶여 반환됩니다.
    """
    limit = max(1, min(limit, MAX_SEARCH_LIMIT))
    raw, meta = crawl_district(district, price_min, price_max, trade_type, limit=limit)
    items = filter_and_rank(raw, price_min=price_min, price_max=price_max)
    truncated = len(items) > limit or meta["timeExceeded"] or (
        meta["complexesScanned"] < meta["complexesTotal"]
    )
    payload = {
        "district": meta.get("regionName", district),
        "returned": min(len(items), limit),
        "collected": len(items),
        "complexesScanned": meta["complexesScanned"],
        "complexesWithListings": meta["complexesTotal"],
        "truncated": truncated,
        "items": items[:limit],
    }
    if truncated:
        payload["note"] = (
            "매물 수 상위 단지부터 수집하다 한도에 도달해 일부만 반환했습니다. "
            "가격 범위를 좁히거나 동 단위로 지역을 좁혀 다시 검색하세요."
        )
    return json.dumps(payload, ensure_ascii=False, indent=1)


@mcp.tool
def get_complex_info(
    complex_id: str | None = None,
    complex_name: str | None = None,
) -> str:
    """아파트 단지 상세 정보를 조회합니다.

    Args:
        complex_id: 단지 번호 (complexNo)
        complex_name: 단지명 (예: "래미안강남")

    complex_id 또는 complex_name 중 하나는 필수입니다.
    """
    if not complex_id and complex_name:
        complex_id = search_complex_by_name(complex_name)
        if not complex_id:
            return json.dumps(
                {"error": f"단지를 찾을 수 없음: {complex_name} — 정확한 단지명 또는 complex_id로 다시 시도하세요."},
                ensure_ascii=False,
            )
    if not complex_id:
        return json.dumps(
            {"error": "complex_id 또는 complex_name을 입력하세요."},
            ensure_ascii=False,
        )
    detail = get_complex_detail(complex_id)
    return json.dumps(detail, ensure_ascii=False, indent=2)


@mcp.tool
def get_complex_price_info(
    complex_id: str | None = None,
    complex_name: str | None = None,
) -> str:
    """단지 평형별 시세(네이버) + 최근 실거래가를 조회합니다.

    평형별로 매매/전세 시세, 현재 호가 범위, 최근 실거래 내역을 반환합니다.

    Args:
        complex_id: 단지 번호 (complexNo)
        complex_name: 단지명 (예: "가천대역두산위브")

    complex_id 또는 complex_name 중 하나는 필수입니다.
    """
    if not complex_id and complex_name:
        complex_id = search_complex_by_name(complex_name)
        if not complex_id:
            return json.dumps(
                {"error": f"단지를 찾을 수 없음: {complex_name} — 정확한 단지명 또는 complex_id로 다시 시도하세요."},
                ensure_ascii=False,
            )
    if not complex_id:
        return json.dumps(
            {"error": "complex_id 또는 complex_name을 입력하세요."},
            ensure_ascii=False,
        )
    data = get_complex_prices(complex_id)
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool
def list_districts() -> str:
    """전국 시/도 17개 목록을 반환합니다.

    더 구체적인 지역(구/동)은 `search_apartments`의 district 파라미터에
    직접 입력하면 네이버 검색 API로 자동 조회됩니다.
    예: "관평동", "강남구", "성남시 분당구"
    """
    regions = _client.get_dong_list("0000000000")
    simplified = [
        {"name": r.get("cortarName"), "cortarNo": r.get("cortarNo")}
        for r in regions
    ]
    return json.dumps(
        {"sido": simplified, "note": "구/동은 search_apartments에 직접 입력하세요."},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool
def resolve_district(query: str) -> str:
    """지역명으로 네이버 cortarNo를 조회합니다.

    Args:
        query: 검색할 지역명 ("관평동", "강남구", "대전 유성구" 등)

    반환:
        cortarNo, cortarName, cortarType (city/dvsn/sec), 좌표
    """
    region = resolve_region(query)
    if not region:
        return json.dumps(
            {"error": f"지역을 찾을 수 없음: {query}"}, ensure_ascii=False
        )
    return json.dumps(region, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    # PORT가 있으면 원격 HTTP(unified-school-mcp 등에서 프록시하기 위함),
    # 없으면 기존처럼 로컬 stdio(Claude Desktop 등 직접 연결용).
    port = os.environ.get("PORT")
    if port:
        mcp.run(
            transport="http",
            host="0.0.0.0",
            port=int(port),
            path="/mcp",
            stateless_http=True,
        )
    else:
        mcp.run()
