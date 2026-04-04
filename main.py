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
NAVER_API_KEY    = os.environ.get("NAVER_API_KEY", "")
NAVER_SECRET_KEY = os.environ.get("NAVER_SECRET_KEY", "")
NAVER_CUSTOMER_ID= os.environ.get("NAVER_CUSTOMER_ID", "")
ANTHROPIC_API_KEY= os.environ.get("ANTHROPIC_API_KEY", "")

BASE_URL = "https://api.searchad.naver.com"

# ── Naver 서명 ──────────────────────────────────────────────
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

def fetch_keywords(seed: str) -> list:
    path = "/keywordstool"
    res = requests.get(
        BASE_URL + path,
        headers=naver_headers(path),
        params={"hintKeywords": seed, "showDetail": 1},
        timeout=10,
    )
    if res.status_code != 200:
        return []
    return res.json().get("keywordList", [])

# ── 점수 계산 ──────────────────────────────────────────────
COMP = {"낮음": 1, "중간": 2, "높음": 3}
COMP_ALLOW = {
    "낮음":  {"낮음"},
    "중간":  {"낮음", "중간"},
    "높음":  {"낮음", "중간", "높음"},
}

def to_int(v):
    return v if isinstance(v, int) else 0

def score_kw(kw, min_search, max_comp):
    pc   = to_int(kw.get("monthlyPcQcCnt", 0))
    mo   = to_int(kw.get("monthlyMobileQcCnt", 0))
    total= pc + mo
    comp = kw.get("compIdx", "높음")

    if total < min_search or comp not in COMP_ALLOW.get(max_comp, {"낮음"}):
        return None

    score = round(total / COMP.get(comp, 3), 1)
    return {
        "keyword": kw.get("relKeyword", ""),
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
    min_search: int = 300
    max_competition: str = "낮음"

@app.post("/api/keywords")
def keyword_search(req: SeedRequest):
    results = []
    for seed in req.seeds[:5]:
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
    "건강": ["건강","다이어트","운동"],
    "요리": ["요리","레시피","음식"],
    "여행": ["여행","국내여행","여행지"],
    "재테크": ["재테크","투자","부업"],
    "뷰티": ["뷰티","화장품","스킨케어"],
    "반려동물":["강아지","고양이","반려동물"],
    "IT기기":["스마트폰","노트북","IT"],
}

class ThemeRequest(BaseModel):
    theme: str
    category: str = ""
    min_search: int = 300
    max_competition: str = "중간"

@app.post("/api/theme")
def theme_search(req: ThemeRequest):
    today = datetime.now()
    month = today.month
    season = next((v for k,v in SEASON.items() if month in k), "")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    cat_hint = f"\n카테고리: {req.category}" if req.category else ""

    prompt = f"""
오늘: {today.strftime('%Y-%m-%d')} ({season})
테마: {req.theme}{cat_hint}

조건:
- 블로그/쇼츠에서 잘 먹히는 키워드
- 검색량 있는 키워드
- 트렌드 반영

반드시 JSON만 출력:
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

    if req.category in CATEGORY_SEEDS:
        seeds += CATEGORY_SEEDS[req.category]

    results = []
    for seed in seeds[:10]:
        for kw in fetch_keywords(seed):
            r = score_kw(kw, req.min_search, req.max_competition)
            if r:
                r["seed"] = seed
                results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    return {"results": dedup(results)[:50]}

# ── 정적 페이지 ────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    with open("static/index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())
