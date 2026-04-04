import os, time, hmac, hashlib, base64, json
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import requests
import anthropic

app = FastAPI()

# ── 환경 변수 ──────────────────────────────────────────────
NAVER_API_KEY     = os.environ.get("NAVER_API_KEY", "")
NAVER_SECRET_KEY  = os.environ.get("NAVER_SECRET_KEY", "")
NAVER_CUSTOMER_ID = os.environ.get("NAVER_CUSTOMER_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

BASE_URL = "https://api.searchad.naver.com"

# ── 네이버 API 서명 ─────────────────────────────────────────
def sign(timestamp, method, path):
    msg = f"{timestamp}.{method}.{path}".encode()
    raw = hmac.new(NAVER_SECRET_KEY.encode(), msg, hashlib.sha256).digest()
    return base64.b64encode(raw).decode()

def naver_headers(path, method="GET"):
    ts = str(round(time.time() * 1000))
    return {
        "X-Timestamp": ts,
        "X-API-KEY": NAVER_API_KEY,
        "X-Customer": NAVER_CUSTOMER_ID,
        "X-Signature": sign(ts, method, path),
    }

# ── 키워드 조회 ────────────────────────────────────────────
def fetch_keywords(seed: str):
    path = "/keywordstool"
    try:
        res = requests.get(
            BASE_URL + path,
            headers=naver_headers(path),
            params={"hintKeywords": seed, "showDetail": 1},
            timeout=5,
        )
        if res.status_code != 200:
            return []
        return res.json().get("keywordList", [])
    except:
        return []

# ── 필터 & 블로그 패턴 ─────────────────────────────────────
FILTER_WORDS = [
    "주가","시세","환율","지수","ETF","종목",
    "코스피","나스닥","S&P","금리","선물",
    "차트","실시간","가격"
]

BLOG_PATTERNS = [
    "방법","추천","후기","정리","꿀팁","비교",
    "TOP","순위","리뷰","가이드","신청","조건"
]

# ── 씨앗 확장 ─────────────────────────────────────────────
def expand_seed(seed):
    base = [
        "방법","추천","후기","정리","꿀팁",
        "비용","조건","신청","총정리"
    ]
    return [f"{seed} {b}" for b in base]

# ── 점수 계산 ──────────────────────────────────────────────
COMP = {"낮음": 1, "중간": 2, "높음": 3}

def to_int(v):
    return v if isinstance(v, int) else 0

def score_kw(kw, min_search, max_comp):
    keyword = kw.get("relKeyword", "")

    # 조회형 제거
    if any(f in keyword for f in FILTER_WORDS):
        return None

    pc = to_int(kw.get("monthlyPcQcCnt", 0))
    mo = to_int(kw.get("monthlyMobileQcCnt", 0))
    total = pc + mo
    comp = kw.get("compIdx", "높음")

    # 최소 검색량 완화
    if total < 30:
        return None

    # 블로그형 가중치
    blog_score = 1.5 if any(p in keyword for p in BLOG_PATTERNS) else 1

    score = round((total / COMP.get(comp, 3)) * blog_score, 1)

    return {
        "keyword": keyword,
        "pc": pc,
        "mobile": mo,
        "total": total,
        "competition": comp,
        "score": score,
    }

def dedup(items):
    seen, out = set(), []
    for r in items:
        if r["keyword"] not in seen:
            seen.add(r["keyword"])
            out.append(r)
    return out

# ── 씨앗 키워드 모드 ────────────────────────────────────────
class SeedRequest(BaseModel):
    seeds: list[str]
    min_search: int = 100
    max_competition: str = "중간"

@app.post("/api/keywords")
def keyword_search(req: SeedRequest):
    results = []

    expanded = []
    for seed in req.seeds:
        expanded.append(seed)
        expanded += expand_seed(seed)

    for seed in expanded[:20]:
        for kw in fetch_keywords(seed):
            r = score_kw(kw, req.min_search, req.max_competition)
            if r:
                r["seed"] = seed
                results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    return {"results": dedup(results)[:60]}

# ── 테마 모드 ──────────────────────────────────────────────
SEASON = {
    (3,4,5): "봄", (6,7,8): "여름",
    (9,10,11): "가을", (12,1,2): "겨울"
}

CATEGORY_SEEDS = {
    "육아": ["아기","유아","어린이"],
    "건강": ["건강관리","다이어트","운동"],
    "요리": ["레시피","요리방법","집밥"],
    "여행": ["국내여행","여행코스","맛집"],
    "재테크": ["부업","절약","지원금"],
    "뷰티": ["스킨케어","화장품","관리법"],
    "반려동물":["강아지","고양이","관리"],
    "IT기기":["노트북","스마트폰","추천"],
}

class ThemeRequest(BaseModel):
    theme: str
    category: str = ""
    min_search: int = 100
    max_competition: str = "중간"

@app.post("/api/theme")
def theme_search(req: ThemeRequest):
    today = datetime.now()
    month = today.month
    season = next((v for k,v in SEASON.items() if month in k), "")

    seeds = []

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        prompt = f"""
오늘: {today.strftime('%Y-%m-%d')} ({season})
테마: {req.theme}

조건:
- 블로그 글 작성용 키워드
- 실생활 / 복지 / 꿀팁 중심
- 수익화 가능 키워드
- 조회형 키워드 금지

JSON만 출력:
{{ "keywords": ["키워드1","키워드2","키워드3"] }}
"""

        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role":"user","content":prompt}]
        )

        text = msg.content[0].text.strip()

        try:
            seeds = json.loads(text).get("keywords", [])
        except:
            seeds = []

    except:
        seeds = []

    # 카테고리 보완
    if req.category in CATEGORY_SEEDS:
        seeds += CATEGORY_SEEDS[req.category]

    if not seeds:
        seeds = ["지원금", "부업", "절약"]

    results = []

    expanded = []
    for s in seeds:
        expanded.append(s)
        expanded += expand_seed(s)

    for seed in expanded[:20]:
        for kw in fetch_keywords(seed):
            r = score_kw(kw, req.min_search, req.max_competition)
            if r:
                r["seed"] = seed
                results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)

    return {
        "results": dedup(results)[:60],
        "seeds_used": seeds
    }

# ── 정적 페이지 ────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    with open("static/index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())
