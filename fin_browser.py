"""네이버 부동산 새 주소(fin.land.naver.com/front-api/v1) 호출기.

2026-10-28 네이버가 neo.land.naver.com/api 를 닫고 fin.land 로 옮겼다(옛 경로 404).
새 API 는 일반 HTTP 요청엔 429 를 주고, 진짜 브라우저(텔레메트리 통과)에서 부른 요청만 받아 준다.
→ 화면 없는 Chromium 을 하나 띄워 그 페이지 안에서 fetch 로 부른다.

FastMCP 는 도구를 여러 스레드에서 부르는데 playwright sync 객체는 만든 스레드에서만 써야 한다
→ 브라우저 전용 스레드 하나가 큐로 요청을 받아 처리한다.

주의: 네이버는 데이터센터 IP(GitHub Actions·Render 등) 접속을 끊는다. 집·학교 PC 에서만 된다.
"""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future
from typing import Any, Optional

from config import FIN_ORIGIN, REQUEST_DELAY_SEC, USER_AGENT

_JS = """async ([m, path, body, params]) => {
  const u = new URL('/front-api/v1' + path, location.origin);
  if (params) for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) v.forEach(x => u.searchParams.append(k + '[]', x)); else u.searchParams.set(k, v);
  }
  const r = await fetch(u, { method: m, credentials: 'include', headers: body ? { 'content-type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch (_) {}
  return { st: r.status, j, t: j ? '' : t.slice(0, 200) };
}"""

_WAITS = (3, 8, 15, 30)  # 실패 뒤 기다리는 초 — 인터넷이 잠깐 끊겨도 버티게


class FinBrowser:
    def __init__(self) -> None:
        self._q: "queue.Queue[tuple[Future, tuple]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ── 공개 ──
    def call(self, method: str, path: str, params: Optional[dict] = None, body: Any = None, timeout: float = 90) -> Any:
        """front-api 호출 → result 부분. 실패하면 RuntimeError."""
        self._start()
        fut: Future = Future()
        self._q.put((fut, (method, path, params, body)))
        return fut.result(timeout=timeout)

    # ── 브라우저 스레드 ──
    def _start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="fin-browser", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = ctx = page = None

        def open_page():
            nonlocal browser, ctx, page
            if browser is None:
                browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
                ctx = browser.new_context(user_agent=USER_AGENT, locale="ko-KR", viewport={"width": 1400, "height": 900})
                ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            page = ctx.new_page()
            page.goto(FIN_ORIGIN + "/map", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)

        last_call = 0.0
        while True:
            fut, (m, path, params, body) = self._q.get()
            if fut.set_running_or_notify_cancel() is False:
                continue
            err = None
            for i in range(len(_WAITS) + 1):
                try:
                    if page is None:
                        open_page()
                    gap = REQUEST_DELAY_SEC - (time.monotonic() - last_call)
                    if gap > 0:
                        time.sleep(gap)
                    r = page.evaluate(_JS, [m, path, body, params])
                    last_call = time.monotonic()
                except Exception as e:  # 페이지가 죽었거나 인터넷이 끊김
                    r, err = None, f"{type(e).__name__}: {str(e)[:160]}"
                if r and r["st"] == 200 and r["j"] is not None:
                    fut.set_result(r["j"].get("result"))
                    break
                if r:
                    err = f'{r["st"]} {(r["j"] or {}).get("detailCode") or r["t"]}'
                    if r["st"] in (400, 404):
                        break  # 요청 모양이 틀림 — 다시 해도 같다
                if i < len(_WAITS):
                    time.sleep(_WAITS[i])
                    if not r or r["st"] in (401, 403, 429):
                        try:
                            open_page()  # 쿠키·텔레메트리 다시 받기
                        except Exception as e:
                            err = f"{err} / 페이지 다시 열기 실패: {str(e)[:120]}"
            if not fut.done():
                fut.set_exception(RuntimeError(f"네이버 {path} 요청 실패: {err}"))


fin = FinBrowser()
