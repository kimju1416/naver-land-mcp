"""네이버 부동산 API 호출 모듈.

2026-10-28 네이버가 neo.land/api 를 닫고 fin.land.naver.com/front-api/v1 로 옮겼다.
호출은 fin_browser.fin(화면 없는 브라우저 안 fetch)이 하고, 여기서는 새 응답을
예전 응답 모양(complexNo·cortarNo·dealOrWarrantPrc·area2…)으로 맞춰 돌려준다
— filter.py·report.py·snapshot.py·server.py 는 그대로 쓴다.
"""

from __future__ import annotations

import re
import time
from datetime import date, timedelta
from typing import Any, Optional

from config import CRAWL_TIME_BUDGET_SEC, DEFAULT_MAX_COMPLEXES, FIN_ORIGIN, TRADE_TYPES
from filter import parse_price
from fin_browser import fin

PAGE = 30  # 목록 한 번에 받는 최대 건수(넘기면 400)
FILTER = {"roomCount": [], "bathRoomCount": [], "optionTypes": [], "oneRoomShapeTypes": [], "moveInTypes": [],
          "filtersExclusiveSpace": False, "floorTypes": [], "directionTypes": [], "hasArticlePhoto": False,
          "isAuthorizedByOwner": False, "parkingTypes": [], "entranceTypes": [], "hasArticle": False}
LEVEL_OLD = {"SI": "city", "GUN": "dvsn", "EUP": "sec"}
_DIR = {"S": "남", "N": "북", "E": "동", "W": "서"}


# ───────── 값 변환 ─────────

def _man(won) -> Optional[int]:
    """원 → 만원."""
    return int(round(won / 10000)) if isinstance(won, (int, float)) and won else None


def _price_text(won) -> str:
    """594330000원 → '5억 9,433' (예전 dealOrWarrantPrc 모양)."""
    man = _man(won) or 0
    eok, rest = divmod(man, 10000)
    if eok and rest:
        return f"{eok}억 {rest:,}"
    return f"{eok}억" if eok else (f"{rest:,}" if rest else "")


def _direction(code) -> str:
    """'SS'→남향, 'WS'→남서향."""
    c = set((code or "").upper()) & set("SNEW")
    if not c:
        return ""
    if len(c) == 1:
        return _DIR[c.pop()] + "향"
    return ("남" if "S" in c else "북" if "N" in c else "") + ("동" if "E" in c else "서" if "W" in c else "") + "향"


def _region_old(r: dict) -> dict:
    co = r.get("coordinates") or {}
    return {"cortarNo": r.get("legalDivisionNumber"), "cortarName": r.get("legalDivisionName"),
            "cortarType": LEVEL_OLD.get(r.get("legalDivisionLevelType"), r.get("legalDivisionLevelType")),
            "centerLat": co.get("yCoordinate"), "centerLon": co.get("xCoordinate")}


def _pages(path: str, body: dict, key: str, max_pages: int, deadline: Optional[float] = None) -> list[dict]:
    out: list[dict] = []
    last: list = []
    seed = None
    for _ in range(max_pages):
        req = {**body[key], "lastInfo": last}
        if seed:
            req["seed"] = seed
        res = fin.call("POST", path, body={**body, key: req}) or {}
        lst = res.get("list") or []
        out += lst
        seed = res.get("seed") or seed
        if not res.get("hasNextPage") or not lst or (deadline and time.monotonic() > deadline):
            break
        last = res.get("lastInfo") or []
    return out


class NaverLandClient:
    """네이버 부동산(fin.land) 클라이언트 — 예전 메서드 이름·반환 모양 유지."""

    # ---- 지역 ----

    def resolve_region(self, query: str) -> Optional[dict]:
        """지역명 → cortarNo/이름/타입(city·dvsn·sec)/좌표. 이름 끝이 정확히 같은 것 우선."""
        res = fin.call("GET", "/search/autocomplete/legalDivisions", params={"keyword": query.strip(), "page": 0}) or {}
        regions = res.get("list") or []
        if not regions:
            return None
        q = query.strip()
        for r in regions:
            name = r.get("legalDivisionName", "")
            if name == q or name.split()[-1] == q or name.endswith(" " + q):
                return _region_old(r)
        return _region_old(regions[0])

    def get_dong_list(self, cortar_no: str) -> list[dict]:
        """'0000000000' → 시·도 목록, 시·도/구 코드 → 바로 아래 목록."""
        if cortar_no == "0000000000":
            rows = fin.call("GET", "/legalDivision/infoListByLevel", params={"regionLevelType": "SI"}) or []
        else:
            level = "GUN" if cortar_no.endswith("00000000") else "EUP"
            rows = fin.call("GET", "/legalDivision/subInfoList", params={"legalDivisionLevelType": level, "legalDivisionNumber": cortar_no}) or []
        return [_region_old(r) for r in rows]

    def get_complexes(self, cortar_no: str, level: str = "EUP", max_pages: int = 40) -> list[dict]:
        """동(EUP) 또는 구(GUN) 코드 → 아파트 단지 목록(예전 complexList 모양)."""
        body = {"filter": {**FILTER, "tradeTypes": ["A1", "B1"], "realEstateTypes": ["A01", "A04"],
                           "legalDivisionNumbers": [cortar_no], "legalDivisionType": level},
                "complexPagingRequest": {"size": PAGE, "userChannelType": "PC", "complexSortType": "POPULARITY_DESC"}}
        out = []
        for it in _pages("/complex/legalDivisionComplexList", body, "complexPagingRequest", max_pages):
            cx = it.get("complex") or {}
            ci = cx.get("complexInfo") or {}
            if not ci.get("complexNumber"):
                continue
            ad, cnt = ci.get("address") or {}, cx.get("articleCountInfoDto") or {}
            co = ad.get("coordinates") or {}
            out.append({
                "complexNo": str(ci["complexNumber"]), "complexName": ci.get("name", ""),
                "cortarAddress": " ".join(x for x in (ad.get("city"), ad.get("division"), ad.get("sector")) if x),
                "detailAddress": ad.get("jibun", ""), "_dongName": ad.get("sector", ""),
                "realEstateTypeCode": {"A01": "APT", "A04": "JGC"}.get(ci.get("type"), ci.get("type")),
                "totalHouseholdCount": ci.get("totalHouseholdNumber"), "useApproveYmd": ci.get("useApprovalDate"),
                "latitude": co.get("yCoordinate"), "longitude": co.get("xCoordinate"),
                "dealCount": cnt.get("dealCount") or 0, "leaseCount": cnt.get("leaseDepositCount") or 0,
                "rentCount": cnt.get("leaseMonthlyCount") or 0,
            })
        return out

    # ---- 매물 ----

    def get_articles(
        self,
        complex_no: str,
        trade_type: str = "A1",
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
        max_pages: int = 20,
        same_address_group: bool = False,
    ) -> list[dict]:
        """단지 번호 → 매물 목록(예전 articleList 모양).

        새 API 는 같은 물건을 여러 중개사가 올린 것을 이미 한 줄로 묶어 준다(대표 매물 + 중개사 수)
        — same_address_group 은 이름만 남는다. 가격 범위는 서버가 안 받아서 받은 뒤 거른다(filter.py).
        """
        body = {"size": PAGE, "complexNumber": int(complex_no), "tradeTypes": [trade_type], "pyeongTypes": [],
                "dongNumbers": [], "userChannelType": "PC", "articleSortType": "RANKING_DESC"}
        rows = self._article_pages(body, max_pages)
        out = []
        for it in rows:
            a = it.get("representativeArticleInfo") or {}
            if not a.get("articleNumber"):
                continue
            sp, det, pr = a.get("spaceInfo") or {}, a.get("articleDetail") or {}, a.get("priceInfo") or {}
            tt = a.get("tradeType") or trade_type
            out.append({
                "articleNo": a["articleNumber"], "articleName": a.get("articleName", ""),
                "buildingName": a.get("dongName", ""), "floorInfo": det.get("floorInfo", ""),
                "area1": sp.get("supplySpace"), "area2": sp.get("exclusiveSpace"),
                "direction": _direction(det.get("direction")),
                "tradeTypeCode": tt, "tradeTypeName": TRADE_TYPES.get(tt, tt),
                "dealOrWarrantPrc": _price_text(pr.get("dealPrice") if tt == "A1" else pr.get("warrantyPrice")),
                "rentPrc": _man(pr.get("rentPrice")) if tt == "B2" else None,
                "tagList": [], "articleFeatureDesc": det.get("articleFeatureDescription", ""),
                "articleConfirmYmd": ((a.get("verificationInfo") or {}).get("articleConfirmDate") or "").replace("-", ""),
                "sameAddrCnt": (it.get("duplicatedArticleInfo") or {}).get("realtorCount") or 1,
                "cpPcArticleBridgeUrl": f"{FIN_ORIGIN}/articles/{a['articleNumber']}",
            })
        return out

    def _article_pages(self, body: dict, max_pages: int) -> list[dict]:
        out: list[dict] = []
        last: list = []
        seed = None
        for _ in range(max_pages):
            req = {**body, "lastInfo": last}
            if seed:
                req["seed"] = seed
            res = fin.call("POST", "/complex/article/list", body=req) or {}
            lst = res.get("list") or []
            out += lst
            seed = res.get("seed") or seed
            if not res.get("hasNextPage") or not lst:
                break
            last = res.get("lastInfo") or []
        return out

    # ---- 단지 ----

    def get_complex_detail(self, complex_no: str) -> dict:
        """단지 상세 + 평형 목록(예전 complexDetail 모양 + pyeongList)."""
        d = fin.call("GET", "/complex", params={"complexNumber": str(complex_no)}) or {}
        ad, co = d.get("address") or {}, d.get("coordinates") or {}
        br, pk = d.get("buildingRatioInfo") or {}, d.get("parkingInfo") or {}
        pys = fin.call("GET", "/complex/pyeongList", params={"complexNumber": str(complex_no)}) or []
        stats = {}
        try:
            stats = (fin.call("GET", "/complex/article/stats", params={"complexNumber": str(complex_no)}) or {}).get("count") or {}
        except RuntimeError:
            pass
        return {
            "complexNo": str(complex_no), "complexName": d.get("name", ""),
            "address": " ".join(x for x in (ad.get("city"), ad.get("division"), ad.get("sector"), ad.get("jibun")) if x),
            "roadAddress": ad.get("roadName", ""), "latitude": co.get("yCoordinate"), "longitude": co.get("xCoordinate"),
            "totalHouseholdCount": d.get("totalHouseholdNumber"), "totalDongCount": d.get("dongCount"),
            "highFloor": d.get("highestDongFloor"), "lowFloor": d.get("lowestDongFloor"),
            "useApproveYmd": d.get("useApprovalDate"), "constructionCompanyName": d.get("constructionCompany"),
            "batlRatio": br.get("floorAreaRatio"), "btlRatio": br.get("buildingCoverageRatio"),
            "parkingPossibleCount": pk.get("totalParkingCount"), "parkingCountByHousehold": pk.get("parkingCountPerHousehold"),
            "heatMethodTypeCode": (d.get("heatingAndCoolingInfo") or {}).get("heatingAndCoolingSystemType"),
            "dealCount": stats.get("dealCount"), "leaseCount": stats.get("leaseDepositCount"), "rentCount": stats.get("leaseMonthlyCount"),
            "url": f"{FIN_ORIGIN}/complexes/{complex_no}",
            "pyeongList": [{
                "pyeongNo": str(p.get("number")), "pyeongName": p.get("name", ""),
                "exclusiveArea": p.get("exclusiveArea"), "supplyArea": p.get("supplyArea"),
                "householdCountByPyeong": p.get("householdCount"), "roomCnt": p.get("roomCount"),
                "bathroomCnt": p.get("bathRoomCount"), "direction": _direction(p.get("direction")),
            } for p in pys if p.get("number")],
        }

    def pyeong_prices(self, complex_no: str, area_no: str, deals: int = 5) -> dict:
        """한 평형의 시세(한국부동산원·KB) + 최근 매매 실거래(만원)."""
        entry: dict[str, Any] = {}
        for cp, key, basis in (("kab", "marketPrice", "marketPriceBasis"), ("kbstar", "kbMarketPrice", "kbPriceBasis")):
            try:
                rows = fin.call("GET", "/complex/marketPrice/recent", params={
                    "complexNumber": str(complex_no), "pyeongTypeNumber": str(area_no), "realEstateType": "A01", "cpList": [cp]}) or []
            except RuntimeError:
                continue
            if rows:
                p = rows[0]
                dr, lr = p.get("dealPriceRange") or {}, p.get("depositPriceRange") or {}
                entry[key] = {"dealLow": _man(dr.get("floor")), "dealHigh": _man(dr.get("ceiling")), "dealAvg": _man(p.get("dealAveragePrice")),
                              "leaseLow": _man(lr.get("floor")), "leaseHigh": _man(lr.get("ceiling")), "leaseAvg": _man(p.get("leaseAveragePrice"))}
                entry[basis] = (p.get("baseDate") or "").replace("-", "")
        try:
            res = fin.call("GET", "/complex/v2/pyeong/realPrice", params={
                "complexNumber": str(complex_no), "pyeongTypeNumber": str(area_no), "tradeType": "A1", "page": 0, "size": deals}) or {}
            rd = [{"date": r.get("tradeDate"), "price": _man(r.get("dealPrice")), "floor": r.get("floor")}
                  for r in res.get("list") or [] if not r.get("isDelete")]
            if rd:
                entry["realDeals"] = rd[:deals]
        except RuntimeError:
            pass
        return entry

    def get_complex_prices(self, complex_no: str) -> dict:
        """단지 평형별 시세 + 실거래가."""
        detail = self.get_complex_detail(complex_no)
        result: dict[str, Any] = {"complexNo": complex_no, "complexName": detail.get("complexName", ""),
                                  "address": detail.get("address", ""), "pyeongs": []}
        for py in detail.get("pyeongList", []):
            entry = {"pyeongName": py.get("pyeongName", ""), "exclusiveArea": py.get("exclusiveArea", ""),
                     "supplyArea": py.get("supplyArea", ""), "householdCount": py.get("householdCountByPyeong", ""),
                     "roomCnt": py.get("roomCnt", ""), "bathroomCnt": py.get("bathroomCnt", "")}
            entry.update(self.pyeong_prices(complex_no, py["pyeongNo"], deals=5))
            result["pyeongs"].append(entry)
        return result

    def search_complex_by_name(self, name: str) -> Optional[str]:
        """단지명 → complexNo (이름이 정확히 같은 것 우선)."""
        res = fin.call("GET", "/search/autocomplete/complexes", params={"keyword": name.strip(), "size": 10, "page": 0}) or {}
        lst = res.get("list") or []
        if not lst:
            return None
        key = re.sub(r"\s", "", name)
        for c in lst:
            if re.sub(r"\s", "", c.get("complexName", "")) == key:
                return str(c["complexNumber"])
        return str(lst[0]["complexNumber"])


# 모듈 레벨 싱글톤 (FastMCP 도구에서 재사용)
_client = NaverLandClient()


def get_dong_list(cortar_no: str) -> list[dict]:
    return _client.get_dong_list(cortar_no)


def get_complexes(dong_code: str) -> list[dict]:
    return _client.get_complexes(dong_code)


def get_articles(complex_no: str, trade_type: str = "A1", **kwargs) -> list[dict]:
    return _client.get_articles(complex_no, trade_type, **kwargs)


def get_complex_detail(complex_no: str) -> dict:
    return _client.get_complex_detail(complex_no)


def search_complex_by_name(name: str) -> Optional[str]:
    return _client.search_complex_by_name(name)


def get_complex_prices(complex_no: str) -> dict:
    return _client.get_complex_prices(complex_no)


def watch_complexes_data(
    complex_names: list[str],
    price_min: int,
    price_max: int,
    trade_type: str = "A1",
) -> dict[str, Any]:
    """관심 단지 목록의 매물 + 시세 + 실거래가를 한번에 조회.

    Returns:
        {"complexes": [...], "all_articles": [...]}
        - complexes: 단지별 상세 (시세, 실거래가 포함)
        - all_articles: 전체 매물 flat list (스냅샷 비교용)
    """
    from filter import filter_and_rank

    results: list[dict] = []
    all_articles: list[dict] = []

    for name in complex_names:
        complex_no = _client.search_complex_by_name(name)
        if not complex_no:
            results.append({"name": name, "error": f"단지를 찾을 수 없음: {name}"})
            continue

        articles = _client.get_articles(complex_no, trade_type, price_min=price_min, price_max=price_max, same_address_group=True)
        for a in articles:
            a["_complexNo"] = complex_no
            a["_complexName"] = name

        filtered = filter_and_rank(articles, price_min=price_min, price_max=price_max)
        all_articles.extend(filtered)

        detail = _client.get_complex_detail(complex_no)

        # 대표 평형 1개만 시세+실거래가 조회 (속도 최적화)
        representative_pyeong = []
        pyeong_list = detail.get("pyeongList", [])
        if pyeong_list:
            py = pyeong_list[0]
            entry: dict[str, Any] = {"pyeongName": py.get("pyeongName", ""), "exclusiveArea": py.get("exclusiveArea", "")}
            entry.update(_client.pyeong_prices(complex_no, py["pyeongNo"], deals=3))
            representative_pyeong.append(entry)

        results.append({
            "name": detail.get("complexName", name),
            "complexNo": complex_no,
            "address": detail.get("address", ""),
            "articleCount": len(filtered),
            "articles": filtered,
            "pyeongs": representative_pyeong,
        })

    return {"complexes": results, "all_articles": all_articles}


def resolve_region(query: str) -> Optional[dict]:
    """지역명 → cortarNo/이름/타입 조회 (전국 지원)."""
    return _client.resolve_region(query)


def crawl_district(
    district: str,
    price_min: int,
    price_max: int,
    trade_type: str = "A1",
    limit: Optional[int] = None,
) -> tuple[list[dict], dict]:
    """지역 내 매물 수집. 동/구/시 단위 자동 감지.

    지원 형식:
    - 동 단위: "관평동", "개포동" → 해당 동 전체 단지
    - 구/군 단위: "강남구", "유성구", "성남시 분당구" → 구 전체 단지(새 API 는 구 단위로 한 번에 받는다)
    - 시/도 단위: "서울시", "경기도" → 거부 (범위 너무 넓음)

    매물 수 많은 단지부터 받고, limit 충족 또는 시간 예산(CRAWL_TIME_BUDGET_SEC) 소진 시 조기 종료.

    Returns:
        (articles, meta) — meta에 수집 범위/조기종료 여부가 담긴다.
    """
    deadline = time.monotonic() + CRAWL_TIME_BUDGET_SEC

    region = resolve_region(district)
    if not region:
        raise ValueError(f"지역을 찾을 수 없음: {district}")
    cortar_no, cortar_type = region["cortarNo"], region.get("cortarType")
    region_name = region.get("cortarName", district)
    if cortar_type == "city":
        raise ValueError(f"{region_name}은 범위가 너무 넓습니다. 구/군/동 단위로 지정해주세요.")

    deal_key = {"A1": "dealCount", "B1": "leaseCount", "B2": "rentCount"}.get(trade_type, "dealCount")
    complexes = _client.get_complexes(cortar_no, level="EUP" if cortar_type == "sec" else "GUN", max_pages=15)
    all_complexes = sorted((c for c in complexes if (c.get(deal_key) or 0) > 0), key=lambda c: c.get(deal_key, 0), reverse=True)

    results: list[dict] = []
    scanned = 0
    time_exceeded = False
    for cx in all_complexes[:DEFAULT_MAX_COMPLEXES * 5]:
        if time.monotonic() > deadline:
            time_exceeded = True
            break
        if limit is not None and len(results) >= limit:
            break
        try:
            # 새 API 는 가격으로 걸러 주지 않는다 — 받아서 범위 안의 것만 센다
            articles = _client.get_articles(cx["complexNo"], trade_type, max_pages=6 if limit is not None else 20)
        except RuntimeError:
            continue
        scanned += 1
        for a in articles:
            p = parse_price(a.get("dealOrWarrantPrc", ""))
            if p is None or (price_min and p < price_min) or (price_max is not None and p > price_max):
                continue
            a["_complexNo"] = cx["complexNo"]
            a["_complexName"] = cx.get("complexName")
            a["_dongName"] = cx.get("_dongName")
            a["_cortarAddress"] = cx.get("cortarAddress")
            results.append(a)

    meta = {"regionName": region_name, "complexesTotal": len(all_complexes), "complexesScanned": scanned, "timeExceeded": time_exceeded}
    return results, meta
