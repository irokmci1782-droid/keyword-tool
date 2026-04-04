@app.post("/api/theme")
def theme_search(req: ThemeRequest):
    today   = datetime.now()
    month   = today.month
    season  = next((v for k,v in SEASON.items() if month in k), "")

    # Claude로 씨앗 키워드 생성
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    cat_hint= f"\n카테고리 힌트: {req.category}" if req.category else ""

    prompt  = (
        f"오늘: {today.strftime('%Y년 %m월 %d일')} ({season} 시즌)\n"
        f"테마: {req.theme}{cat_hint}\n\n"
        "이 시기에 네이버 블로그/콘텐츠 검색량이 높을 씨앗 키워드 8개를 추천해줘.\n"
        "계절, 시즌 이벤트, 트렌드를 최대한 반영해.\n\n"
        "반드시 아래 JSON 형식으로만 출력:\n"
        "{\n"
        "  \"keywords\": [\"키워드1\", \"키워드2\", \"키워드3\"]\n"
        "}\n"
        "설명 절대 하지 말고 JSON만 출력해."
    )

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )

    text = msg.content[0].text.strip()

    try:
        data = json.loads(text)
        seeds = data.get("keywords", [])
    except:
        seeds = []

    # 카테고리 기본 씨앗 추가
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
    return {"results": dedup(results)[:60], "seeds_used": seeds}
