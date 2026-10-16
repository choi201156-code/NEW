"""
키워드별 오늘 뉴스 수집 + 섹션 분류 스크립트
- 네이버 API HUB 뉴스 검색 API 사용
- 결과는 docs/data/ 에 JSON으로 저장 → docs/index.html 이 읽어서 표시

실행:
  NAVER_CLIENT_ID=... NAVER_CLIENT_SECRET=... python fetch_news.py
  python fetch_news.py --mock    # API 키 없이 가짜 데이터로 화면 테스트
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "docs" / "data"
KST = timezone(timedelta(hours=9))
API_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"

# 네이버 뉴스 섹션 번호(sid) → 섹션 이름
SID_MAP = {
    "100": "정치", "101": "경제", "102": "사회",
    "103": "생활/문화", "104": "세계", "105": "IT/과학",
    "106": "연예", "107": "스포츠",
}

# 네이버 링크가 없는 기사용 보조 분류 규칙 (자유롭게 단어 추가하세요)
RULES = {
    "정치": ["대통령", "청와대", "대통령실", "국회", "여당", "야당", "의원", "장관", "총리",
             "선거", "정당", "민주당", "국민의힘", "외교", "국방", "북한", "탄핵", "법안"],
    "경제": ["CPI", "물가", "금리", "환율", "증시", "코스피", "코스닥", "주가", "한국은행",
             "기준금리", "인플레", "GDP", "수출", "무역", "부동산", "아파트값", "예산",
             "재정", "세수", "기업", "실적", "투자", "고용", "실업", "구직급여", "채권"],
    "사회": ["경찰", "검찰", "법원", "사고", "화재", "재판", "판결", "교육", "학교",
             "노동", "복지", "의료", "병원", "날씨", "지진"],
    "세계": ["미국", "중국", "일본", "러시아", "우크라이나", "유럽", "EU", "트럼프",
             "연준", "Fed", "중동", "이스라엘", "백악관"],
    "IT/과학": ["AI", "인공지능", "반도체", "삼성전자", "SK하이닉스", "네이버", "카카오",
                "스마트폰", "앱", "플랫폼", "우주", "연구진"],
    "생활/문화": ["여행", "공연", "전시", "영화", "음식", "건강", "책", "축제"],
}


def clean(text: str) -> str:
    """<b> 태그와 &quot; 같은 HTML 엔티티 제거"""
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def section_from_link(link: str):
    """네이버 뉴스 링크의 ?sid=101 같은 값에서 섹션 추출"""
    if "naver.com" not in link:
        return None
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
    sid = (qs.get("sid") or qs.get("sid1") or [None])[0]
    return SID_MAP.get(sid)


def section_from_rules(text: str) -> str:
    """제목+요약에서 규칙 단어가 가장 많이 나온 섹션 선택"""
    scores = {sec: sum(text.count(w) for w in words) for sec, words in RULES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "기타"


def press_from_url(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc
    return host.removeprefix("www.").removeprefix("m.")


def call_api(query: str, start: int, cid: str, secret: str) -> dict:
    params = urllib.parse.urlencode(
        {"query": query, "display": 100, "start": start, "sort": "date", "format": "json"}
    )
    req = urllib.request.Request(f"{API_URL}?{params}")
    req.add_header("X-NCP-APIGW-API-KEY-ID", cid)
    req.add_header("X-NCP-APIGW-API-KEY", secret)
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_keyword(keyword: str, since: datetime, cid: str, secret: str) -> list:
    """최신순으로 100건씩 넘기다가 기준 시각보다 오래된 기사가 나오면 멈춤"""
    articles, seen = [], set()
    for start in range(1, 1001, 100):
        data = call_api(keyword, start, cid, secret)
        items = data.get("items", [])
        if not items:
            break
        too_old = False
        for it in items:
            pub = parsedate_to_datetime(it["pubDate"])
            if pub < since:
                too_old = True
                break
            link = it.get("link") or it.get("originallink")
            if link in seen:
                continue
            seen.add(link)
            title, desc = clean(it["title"]), clean(it["description"])
            section = section_from_link(link)
            articles.append({
                "title": title,
                "description": desc,
                "url": it.get("originallink") or link,
                "naver_url": link if "naver.com" in link else None,
                "press": press_from_url(it.get("originallink") or link),
                "published": pub.astimezone(KST).isoformat(),
                "section": section or section_from_rules(f"{title} {desc}"),
                "classified_by": "naver" if section else "rule",
            })
        if too_old:
            break
        time.sleep(0.2)  # 과도한 호출 방지
    return articles


def mock_articles(keyword: str) -> list:
    now = datetime.now(KST)
    samples = [
        ("정치", "https://n.news.naver.com/mnews/article/001/0000000001?sid=100", "naver"),
        ("경제", "https://n.news.naver.com/mnews/article/015/0000000002?sid=101", "naver"),
        ("경제", "https://www.example-news.co.kr/article/3", "rule"),
        ("세계", "https://n.news.naver.com/mnews/article/018/0000000004?sid=104", "naver"),
        ("사회", "https://www.example-daily.com/news/5", "rule"),
    ]
    return [{
        "title": f"[테스트] {keyword} 관련 {sec} 기사 {i + 1}",
        "description": f"{keyword}에 대한 테스트용 요약문입니다. 실제 API 키를 넣으면 진짜 기사가 표시됩니다.",
        "url": url, "naver_url": url if "naver" in url else None,
        "press": press_from_url(url),
        "published": (now - timedelta(minutes=37 * i)).isoformat(),
        "section": sec, "classified_by": by,
    } for i, (sec, url, by) in enumerate(samples)]


def main():
    mock = "--mock" in sys.argv
    config = json.loads((ROOT / "keywords.json").read_text(encoding="utf-8"))
    cid = os.environ.get("NAVER_CLIENT_ID", "")
    secret = os.environ.get("NAVER_CLIENT_SECRET", "")
    if not mock and not (cid and secret):
        sys.exit("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 없습니다.")

    now = datetime.now(KST)
    since = now - timedelta(hours=config.get("lookback_hours", 24))
    result = {"date": now.strftime("%Y-%m-%d"), "generated_at": now.isoformat(), "keywords": {}}

    for kw in config["keywords"]:
        try:
            arts = mock_articles(kw) if mock else fetch_keyword(kw, since, cid, secret)
            result["keywords"][kw] = arts
            print(f"{kw}: {len(arts)}건")
        except Exception as e:  # 한 키워드가 실패해도 나머지는 계속
            print(f"{kw}: 실패 - {e}")
            result["keywords"][kw] = []

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"{result['date']}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    # 오래된 파일 정리 + 날짜 목록 갱신
    keep_from = (now - timedelta(days=config.get("keep_days", 30))).strftime("%Y-%m-%d")
    dates = []
    for f in DATA_DIR.glob("????-??-??.json"):
        if f.stem < keep_from:
            f.unlink()
        else:
            dates.append(f.stem)
    (DATA_DIR / "index.json").write_text(
        json.dumps({"dates": sorted(dates, reverse=True)}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
