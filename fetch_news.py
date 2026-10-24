"""
오늘의 키워드 뉴스 — 매일 수집 스크립트

모으는 것
  1) 내 키워드 뉴스 (keywords.json)            → 섹션별 분류
  2) 해외 매체 인용 기사 (블룸버그·로이터 등)   → 섹션·매체별
  3) 사설·칼럼                                 → 오늘의 이슈 분석
  4) 경제지표 (한국은행 ECOS, 미국 FRED)

필요한 환경변수 (GitHub → Settings → Secrets)
  NAVER_CLIENT_ID, NAVER_CLIENT_SECRET   필수
  ECOS_API_KEY                           선택: 한국 경제지표
  FRED_API_KEY                           선택: 미국 경제지표
  ANTHROPIC_API_KEY                      선택: 사설 이슈 AI 분석 + 해외 원문 찾기 + 논문 검색어 번역
  OPENALEX_API_KEY                       선택: 논문(OpenAlex) — 없어도 하루 소량은 됨

실행:
  python fetch_news.py          # 실제 수집
  python fetch_news.py --mock   # API 키 없이 가짜 데이터로 화면 테스트
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "docs" / "data"
KST = timezone(timedelta(hours=9))
NAVER_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"

# ─────────────────────────────── 분류 기준 ───────────────────────────────

# 네이버 뉴스 섹션 번호(sid) → 섹션 이름
SID_MAP = {
    "100": "정치", "101": "경제", "102": "사회", "103": "생활/문화", "104": "세계",
    "105": "IT/과학", "106": "연예", "107": "스포츠", "110": "오피니언",
}

# 네이버 링크가 없는 기사용 보조 분류 규칙
RULES = {
    "정치": ["대통령", "청와대", "대통령실", "국회", "여당", "야당", "의원", "장관", "총리",
             "선거", "정당", "민주당", "국민의힘", "외교", "국방", "북한", "탄핵", "법안"],
    "경제": ["CPI", "물가", "금리", "환율", "증시", "코스피", "코스닥", "주가", "한국은행",
             "기준금리", "인플레", "GDP", "수출", "무역", "부동산", "아파트값", "예산",
             "재정", "세수", "기업", "실적", "투자", "고용", "실업", "구직급여", "채권", "관세"],
    "사회": ["경찰", "검찰", "법원", "사고", "화재", "재판", "판결", "교육", "학교",
             "노동", "복지", "의료", "병원", "날씨", "지진"],
    "세계": ["미국", "중국", "일본", "러시아", "우크라이나", "유럽", "EU", "트럼프",
             "연준", "Fed", "중동", "이스라엘", "백악관"],
    "IT/과학": ["AI", "인공지능", "반도체", "삼성전자", "SK하이닉스", "네이버", "카카오",
                "스마트폰", "앱", "플랫폼", "우주", "연구진"],
    "생활/문화": ["여행", "공연", "전시", "영화", "음식", "건강", "책", "축제"],
}

# 언론사 도메인 → 이름
PRESS_MAP = {
    "chosun.com": "조선일보", "joongang.co.kr": "중앙일보", "donga.com": "동아일보",
    "hani.co.kr": "한겨레", "khan.co.kr": "경향신문", "hankookilbo.com": "한국일보",
    "seoul.co.kr": "서울신문", "segye.com": "세계일보", "kmib.co.kr": "국민일보",
    "munhwa.com": "문화일보", "naeil.com": "내일신문", "mk.co.kr": "매일경제",
    "hankyung.com": "한국경제", "sedaily.com": "서울경제", "edaily.co.kr": "이데일리",
    "mt.co.kr": "머니투데이", "fnnews.com": "파이낸셜뉴스", "asiae.co.kr": "아시아경제",
    "heraldcorp.com": "헤럴드경제", "etnews.com": "전자신문", "dt.co.kr": "디지털타임스",
    "yna.co.kr": "연합뉴스", "news1.kr": "뉴스1", "newsis.com": "뉴시스",
    "kbs.co.kr": "KBS", "imbc.com": "MBC", "sbs.co.kr": "SBS", "ytn.co.kr": "YTN",
    "jtbc.co.kr": "JTBC", "mbn.co.kr": "MBN", "ichannela.com": "채널A", "tvchosun.com": "TV조선",
    "ohmynews.com": "오마이뉴스", "pressian.com": "프레시안", "nocutnews.co.kr": "노컷뉴스",
    "busan.com": "부산일보", "kookje.co.kr": "국제신문", "imaeil.com": "매일신문",
    "kwnews.co.kr": "강원일보", "kyeongin.com": "경인일보", "joongdo.co.kr": "중도일보",
    "ajunews.com": "아주경제", "bizwatch.co.kr": "비즈워치", "biz.chosun.com": "조선비즈",
}

# 해외 매체: (표시 이름, 네이버 검색어, 본문에서 찾을 패턴, 영문명, 도메인)
FOREIGN = [
    ("블룸버그", "블룸버그", r"블룸버그", "Bloomberg", "bloomberg.com"),
    ("로이터", "로이터", r"로이터", "Reuters", "reuters.com"),
    ("WSJ", "월스트리트저널", r"월스트리트저널|월스트리트 저널|(?<![A-Za-z])WSJ(?![A-Za-z])", "Wall Street Journal", "wsj.com"),
    ("FT", "파이낸셜타임스", r"파이낸셜타임스|(?<![A-Za-z])FT(?![A-Za-z])", "Financial Times", "ft.com"),
    ("NYT", "뉴욕타임스", r"뉴욕타임스|(?<![A-Za-z])NYT(?![A-Za-z])", "New York Times", "nytimes.com"),
    ("WP", "워싱턴포스트", r"워싱턴포스트|(?<![A-Za-z])WP(?![A-Za-z])", "Washington Post", "washingtonpost.com"),
    ("CNN", "CNN", r"(?<![A-Za-z])CNN(?![A-Za-z])", "CNN", "cnn.com"),
    ("CNBC", "CNBC", r"CNBC", "CNBC", "cnbc.com"),
    ("AP", "AP통신", r"AP\s?통신", "Associated Press", "apnews.com"),
    ("AFP", "AFP통신", r"AFP\s?통신", "AFP", "afp.com"),
    ("BBC", "BBC", r"(?<![A-Za-z])BBC(?![A-Za-z])", "BBC", "bbc.com"),
    ("이코노미스트", "이코노미스트지", r"이코노미스트지|英\s?이코노미스트|영국\s?이코노미스트", "The Economist", "economist.com"),
    ("폴리티코", "폴리티코", r"폴리티코", "Politico", "politico.com"),
    ("악시오스", "악시오스", r"악시오스", "Axios", "axios.com"),
    ("가디언", "가디언", r"가디언", "The Guardian", "theguardian.com"),
    ("포브스", "포브스", r"포브스", "Forbes", "forbes.com"),
    ("마켓워치", "마켓워치", r"마켓워치", "MarketWatch", "marketwatch.com"),
    ("배런스", "배런스", r"배런스", "Barron's", "barrons.com"),
    ("닛케이", "니혼게이자이", r"니혼게이자이|닛케이(?!\s?(지수|225|평균|주가))", "Nikkei", "nikkei.com"),
    ("요미우리", "요미우리신문", r"요미우리", "Yomiuri Shimbun", "yomiuri.co.jp"),
    ("아사히", "아사히신문", r"아사히신문", "Asahi Shimbun", "asahi.com"),
    ("교도", "교도통신", r"교도통신", "Kyodo News", "kyodonews.net"),
    ("NHK", "NHK", r"NHK", "NHK", "nhk.or.jp"),
    ("신화", "신화통신", r"신화통신", "Xinhua", "xinhuanet.com"),
    ("글로벌타임스", "글로벌타임스", r"글로벌타임스|환구시보", "Global Times", "globaltimes.cn"),
    ("SCMP", "사우스차이나모닝포스트", r"사우스차이나모닝포스트|SCMP", "South China Morning Post", "scmp.com"),
]

# 사진 출처·지역 바이라인처럼 '인용'이 아닌 표기는 지움: (워싱턴=연합뉴스), AFP 연합뉴스, ⓒ로이터=뉴스1
BYLINE_RE = re.compile(
    r"[(（\[][^()（）\[\]]{0,20}=[^()（）\[\]]{0,20}[)）\]]"
    r"|[ⓒ©]?\s*(AFP|로이터|AP|EPA|신화|교도|UPI|게티이미지)\s*(=|/)\s*\S+"
    r"|(AFP|로이터|AP|EPA|UPI)\s*연합뉴스"
)

# 오피니언 판별: 제목 앞 [ ] 안에 이런 말이 있으면 사설·칼럼
EDITORIAL_TAGS = ("사설", "社說")
COLUMN_TAGS = ("칼럼", "시론", "기고", "논단", "기자수첩", "데스크", "시평", "포럼", "오피니언",
               "특파원", "만물상", "분수대", "횡설수설", "여적", "천자칼럼", "필동정담", "지평선",
               "씨줄날줄", "광화문", "세상읽기", "아침을 열며", "기자의 눈", "기자의 시각", "편집국에서",
               "한마당", "프리즘", "인사이트", "기고문", "시사논평", "열린세상", "발언대")
TAG_RE = re.compile(r"^\s*[\[【<〈(]\s*([^\]】>〉)]{1,20}?)\s*[\]】>〉)]")
OPINION_QUERIES = ["사설", "칼럼", "시론", "기고", "기자수첩", "논단", "데스크 칼럼"]


def clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def section_from_link(link: str):
    if "naver.com" not in link:
        return None
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
    return SID_MAP.get((qs.get("sid") or qs.get("sid1") or [None])[0])


def section_from_rules(text: str) -> str:
    scores = {sec: sum(text.count(w) for w in words) for sec, words in RULES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "기타"


def press_from_url(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    for domain, name in PRESS_MAP.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host.removeprefix("www.").removeprefix("m.")


def opinion_kind(title: str, link: str):
    """'사설' / '칼럼' / None"""
    m = TAG_RE.match(title)
    if m:
        tag = m.group(1)
        if any(t in tag for t in EDITORIAL_TAGS):
            return "사설"
        if any(t in tag for t in COLUMN_TAGS):
            return "칼럼"
    if section_from_link(link) == "오피니언":
        return "칼럼"
    return None


# ─────────────────────────────── API 호출 ───────────────────────────────

def http_json(url, headers=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def search_recent(query, since, cid, secret):
    """네이버 뉴스를 최신순으로 넘기다가 기준 시각보다 오래된 기사가 나오면 멈춤"""
    out, seen = [], set()
    for start in range(1, 1001, 100):
        params = urllib.parse.urlencode({"query": query, "display": 100, "start": start, "sort": "date"})
        data = http_json(f"{NAVER_URL}?{params}", {
            "X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": secret})
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
            orig = it.get("originallink") or link
            title, desc = clean(it["title"]), clean(it["description"])
            sec = section_from_link(link)
            out.append({
                "title": title, "description": desc, "url": orig,
                "naver_url": link if "naver.com" in link else None,
                "press": press_from_url(orig),
                "published": pub.astimezone(KST).isoformat(),
                "section": sec or section_from_rules(f"{title} {desc}"),
                "classified_by": "naver" if sec else "rule",
                "_link": link,
            })
        if too_old:
            break
        time.sleep(0.15)
    return out


def claude(prompt, key, model, system=None, max_tokens=8000):
    """Claude API 호출 → 텍스트"""
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    for attempt in range(2):
        try:
            res = http_json("https://api.anthropic.com/v1/messages", {
                "content-type": "application/json", "x-api-key": key,
                "anthropic-version": "2023-06-01"}, body, timeout=180)
            return "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
        except Exception as e:
            if attempt:
                raise
            print(f"  Claude 재시도: {e}")
            time.sleep(5)


def parse_json(text):
    """응답 속 JSON 덩어리 꺼내기"""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    text = m.group(1) if m else text
    start = min([i for i in (text.find("["), text.find("{")) if i >= 0], default=0)
    return json.loads(text[start:])


# ─────────────────────────────── 1. 키워드 뉴스 ───────────────────────────────

def strip_private(items):
    for a in items:
        a.pop("_link", None)
    return items


def fetch_keyword(kw, since, cid, secret):
    arts = search_recent(kw, since, cid, secret)
    for a in arts:
        kind = opinion_kind(a["title"], a["_link"])
        if kind:
            a["section"] = kind
    return strip_private(arts)


# ─────────────────────────────── 2. 해외 매체 인용 ───────────────────────────────

def detect_outlets(title, desc):
    text = BYLINE_RE.sub(" ", f"{title} {desc}")
    return [name for name, _, pat, _, _ in FOREIGN if re.search(pat, text)]


def fetch_foreign(since, cid, secret):
    by_url = {}
    for name, query, *_ in FOREIGN:
        try:
            arts = search_recent(query, since, cid, secret)
        except Exception as e:
            print(f"  {name}: 실패 - {e}")
            continue
        n = 0
        for a in arts:
            outlets = detect_outlets(a["title"], a["description"])
            if name not in outlets or opinion_kind(a["title"], a["_link"]):
                continue
            if a["url"] in by_url:
                for o in outlets:
                    if o not in by_url[a["url"]]["outlets"]:
                        by_url[a["url"]]["outlets"].append(o)
            else:
                a["outlets"] = outlets
                by_url[a["url"]] = a
            n += 1
        print(f"  {name}: {n}건")
    return strip_private(list(by_url.values()))


def add_original_queries(items, key, model):
    """한국어 기사 → 해외 원문을 찾을 영어 검색어 (Claude)"""
    for i in range(0, len(items), 60):
        chunk = items[i:i + 60]
        lines = "\n".join(f'{j}. [{",".join(a["outlets"])}] {a["title"]} — {a["description"][:150]}'
                          for j, a in enumerate(chunk))
        prompt = (
            "아래는 해외 매체를 인용한 한국 기사들이야. 각 기사가 인용한 해외 원문 기사를 "
            "구글 뉴스에서 찾을 수 있도록, 원문에 쓰였을 법한 핵심 영어 검색어(3~7단어, 고유명사 위주)를 만들어줘. "
            "따옴표·사이트명은 넣지 마.\n"
            '출력은 JSON 객체 하나만: {"0": "검색어", "1": "검색어", ...}\n\n' + lines)
        try:
            res = parse_json(claude(prompt, key, model, max_tokens=4000))
            for j, a in enumerate(chunk):
                q = res.get(str(j))
                if q:
                    a["en_query"] = str(q)[:120]
        except Exception as e:
            print(f"  원문 검색어 생성 실패: {e}")


# ─────────────────────────────── 3. 사설·칼럼 + 이슈 ───────────────────────────────

def fetch_opinion(since, keywords, cid, secret):
    by_url = {}
    for q in OPINION_QUERIES:
        try:
            arts = search_recent(q, since, cid, secret)
        except Exception as e:
            print(f"  {q}: 실패 - {e}")
            continue
        for a in arts:
            kind = opinion_kind(a["title"], a["_link"])
            if not kind or a["url"] in by_url:
                continue
            text = f"{a['title']} {a['description']}".lower()
            a.update(section=kind, kind=kind, keywords=[k for k in keywords if k.lower() in text])
            by_url[a["url"]] = a
    items = sorted(by_url.values(), key=lambda a: a["published"], reverse=True)
    return strip_private(items)


def issues_by_ai(items, key, model):
    eds = [(i, a) for i, a in enumerate(items) if a["kind"] == "사설"] or list(enumerate(items))
    cols = [(i, a) for i, a in enumerate(items) if a["kind"] == "칼럼"][:150]
    fmt = lambda pairs: "\n".join(f'{i}. ({a["press"]}) {a["title"]} — {a["description"][:120]}' for i, a in pairs)
    prompt = f"""오늘 한국 신문들의 사설과 칼럼 목록이야. 같은 사안을 다룬 글끼리 묶어서 '오늘의 이슈'를 뽑아줘.

규칙:
- 이슈는 여러 신문이 다룬 순서대로 최대 10개. 사설에서 다룬 신문 수를 가장 중요하게 봐.
- issue: 15자 이내의 짧은 이름 (예: "의대 정원 갈등", "기준금리 동결")
- summary: 이 사안이 무엇인지 1~2문장, 중립적으로
- stances: 사설을 쓴 신문별 논조를 한 줄씩. 제목과 요약에 드러난 내용만 근거로 하고, 추측하지 마.
- ids: 이 이슈에 해당하는 글 번호 전부 (사설+칼럼)

출력은 JSON 배열 하나만:
[{{"issue": "...", "summary": "...", "stances": [{{"press": "조선일보", "view": "..."}}], "ids": [0, 5]}}]

[사설]
{fmt(eds)}

[칼럼]
{fmt(cols)}"""
    res = parse_json(claude(prompt, key, model, max_tokens=8000))
    out = []
    for it in res[:10]:
        ids = [int(i) for i in it.get("ids", []) if str(i).isdigit() and int(i) < len(items)]
        out.append({
            "issue": str(it.get("issue", ""))[:40], "summary": str(it.get("summary", "")),
            "stances": [{"press": str(s.get("press", "")), "view": str(s.get("view", ""))}
                        for s in it.get("stances", [])][:15],
            "ids": ids, "papers": len({items[i]["press"] for i in ids}),
        })
    return out


JOSA = sorted(["으로부터", "에서는", "에게서", "으로는", "이라는", "라는", "에서", "에게", "으로", "까지",
               "부터", "보다", "처럼", "마저", "조차", "이나", "이며", "이고", "하고", "과의", "와의",
               "의", "이", "가", "은", "는", "을", "를", "에", "와", "과", "도", "만", "로", "나", "냐"],
              key=len, reverse=True)
STOP = set("""사설 칼럼 시론 기고 논단 기자수첩 데스크 오피니언 정부 우리 국민 대한 위해 위한 문제 필요
해야 한다 한다면 이번 지금 오늘 내일 어제 이제 다시 결국 모두 누구 무엇 어떻게 무슨 이유 때문 대해 관련
없다 있다 된다 않다 아니다 하라 말라 해선 안된다 안 못 왜 더 또 수 것 등 및 그 이 저 새 각 전 후 중
年 月 日 vs 시대 사회 나라 한국 대한민국""".split())


def tokens(text):
    out = set()
    for w in re.findall(r"[가-힣A-Za-z0-9·]+", TAG_RE.sub("", text)):
        for j in JOSA:
            if w.endswith(j) and len(w) - len(j) >= 2:
                w = w[: -len(j)]
                break
        if len(w) >= 2 and w not in STOP and not w.isdigit():
            out.add(w)
    return out


def issues_by_frequency(items):
    """AI 없이: 여러 신문 제목에 함께 나온 단어로 이슈 추정"""
    press_of = defaultdict(set)
    ids_of = defaultdict(list)
    for i, a in enumerate(items):
        weight_title = tokens(a["title"])
        for t in weight_title | tokens(a["description"][:80]):
            press_of[t].add(a["press"])
            ids_of[t].append(i)
    ranked = sorted(press_of, key=lambda t: (len(press_of[t]), len(ids_of[t])), reverse=True)
    out, used = [], set()
    for t in ranked:
        ids = ids_of[t]
        if len(press_of[t]) < 2 or len(set(ids) & used) > len(ids) * 0.6:
            continue
        used |= set(ids)
        out.append({"issue": t, "summary": "", "stances": [], "ids": ids, "papers": len(press_of[t])})
        if len(out) == 10:
            break
    return out


# ─────────────────────────────── 4. 경제지표 ───────────────────────────────

# 한국은행 100대 지표 이름 → 원래 발표 기관
KR_AGENCY = [
    (r"소비자물가|생활물가|근원물가", "통계청", "https://kosis.kr"),
    (r"실업률|고용률|취업자|경제활동", "통계청", "https://kosis.kr"),
    (r"산업생산|서비스업생산|소매판매|설비투자|건설기성|경기종합|동행지수|선행지수", "통계청", "https://kosis.kr"),
    (r"인구|출생|사망|가구", "통계청", "https://kosis.kr"),
    (r"수출|수입|무역", "관세청·산업통상자원부", "https://unipass.customs.go.kr/ets/"),
    (r"코스피|코스닥|주가", "한국거래소", "http://data.krx.co.kr"),
    (r"국고채|회사채|CD|콜금리", "금융투자협회·한국은행", "https://ecos.bok.or.kr"),
]

US_SERIES = [
    # (FRED ID, 분류, 이름, 단위, 계산, 발표 기관, 기관 링크)
    ("CPIAUCSL", "물가", "소비자물가지수(CPI)", "%", "yoy", "미 노동통계국(BLS)", "https://www.bls.gov/cpi/"),
    ("CPILFESL", "물가", "근원 CPI", "%", "yoy", "미 노동통계국(BLS)", "https://www.bls.gov/cpi/"),
    ("PCEPILFE", "물가", "근원 PCE 물가", "%", "yoy", "미 경제분석국(BEA)", "https://www.bea.gov/data/income-saving/personal-income"),
    ("UNRATE", "고용", "실업률", "%", "level", "미 노동통계국(BLS)", "https://www.bls.gov/cps/"),
    ("PAYEMS", "고용", "비농업 고용 증감", "천 명", "diff", "미 노동통계국(BLS)", "https://www.bls.gov/ces/"),
    ("FEDFUNDS", "금리", "연방기금금리(월평균)", "%", "level", "연방준비제도(Fed)", "https://www.federalreserve.gov/monetarypolicy/openmarket.htm"),
    ("DGS10", "금리", "국채 10년물 금리", "%", "level", "미 재무부", "https://home.treasury.gov/resource-center/data-chart-center/interest-rates"),
    ("A191RL1Q225SBEA", "성장", "실질 GDP 성장률(연율)", "%", "level", "미 경제분석국(BEA)", "https://www.bea.gov/data/gdp/gross-domestic-product"),
]


def fetch_ecos(key):
    url = f"https://ecos.bok.or.kr/api/KeyStatisticList/{key}/json/kr/1/200"
    data = http_json(url)
    rows = (data.get("KeyStatisticList") or {}).get("row") or []
    if not rows:
        raise RuntimeError(str(data)[:200])
    out = []
    for r in rows:
        name = r.get("KEYSTAT_NAME", "")
        agency, link = "한국은행", "https://ecos.bok.or.kr"
        for pat, ag, ln in KR_AGENCY:
            if re.search(pat, name):
                agency, link = ag, ln
                break
        out.append({
            "group": r.get("CLASS_NAME", ""), "name": name, "value": r.get("DATA_VALUE", ""),
            "unit": r.get("UNIT_NAME", ""), "period": r.get("CYCLE", ""),
            "agency": agency, "agency_url": link, "source": "한국은행 ECOS",
            "source_url": "https://ecos.bok.or.kr",
        })
    return out


def fetch_fred(key):
    out = []
    for sid, group, name, unit, calc, agency, link in US_SERIES:
        try:
            q = urllib.parse.urlencode({"series_id": sid, "api_key": key, "file_type": "json",
                                        "sort_order": "desc", "limit": 14})
            obs = [o for o in http_json(f"https://api.stlouisfed.org/fred/series/observations?{q}")
                   .get("observations", []) if o.get("value") not in (None, ".")]
            if len(obs) < 2:
                continue
            v = [float(o["value"]) for o in obs]
            if calc == "yoy":
                if len(v) < 13:
                    continue
                cur, prev = (v[0] / v[12] - 1) * 100, (v[1] / v[13] - 1) * 100 if len(v) > 13 else None
            elif calc == "diff":
                cur, prev = v[0] - v[1], v[1] - v[2] if len(v) > 2 else None
            else:
                cur, prev = v[0], v[1]
            out.append({
                "group": group, "name": name, "value": f"{cur:,.2f}" if abs(cur) < 1000 else f"{cur:,.0f}",
                "unit": unit, "period": obs[0]["date"], "prev": None if prev is None else round(prev, 2),
                "change": None if prev is None else round(cur - prev, 2),
                "agency": agency, "agency_url": link, "source": "FRED",
                "source_url": f"https://fred.stlouisfed.org/series/{sid}",
            })
        except Exception as e:
            print(f"  FRED {sid}: 실패 - {e}")
        time.sleep(0.3)
    return out


# ─────────────────────────────── 5. 분야별 논문 (OpenAlex) ───────────────────────────────

OPENALEX_SELECT = "id,doi,title,publication_date,cited_by_count,primary_location,authorships,primary_topic"


def paper_terms(field, ai_key, ai_model):
    terms = [t for t in field.get("paper_terms", []) if t.strip()]
    if terms or not ai_key:
        return terms or field.get("keywords", [])
    prompt = ("다음은 사용자가 관심 있는 분야와 뉴스 키워드야. 이 분야의 최신 학술 논문을 OpenAlex에서 찾을 "
              "영어 검색어를 3~5개 만들어줘. 학계에서 실제로 쓰는 용어로, 각 1~4단어.\n"
              f"분야: {field.get('name')}\n키워드: {', '.join(field.get('keywords', []))}\n"
              '출력은 JSON 배열 하나만: ["term1", "term2"]')
    try:
        return [str(t) for t in parse_json(claude(prompt, ai_key, ai_model, max_tokens=1000))][:5]
    except Exception as e:
        print(f"  논문 검색어 번역 실패: {e}")
        return field.get("keywords", [])


def openalex_get(params, key):
    if key:
        params["api_key"] = key
    return http_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params),
                     {"User-Agent": "issue-tracking-site"})


def paper_row(w):
    loc = (w.get("primary_location") or {}).get("source") or {}
    return {
        "title": w.get("title") or "", "date": w.get("publication_date", ""),
        "venue": loc.get("display_name") or "", "cited": w.get("cited_by_count", 0),
        "authors": [((a.get("author") or {}).get("display_name") or "") for a in (w.get("authorships") or [])[:3]],
        "topic": (w.get("primary_topic") or {}).get("display_name") or "",
        "url": w.get("doi") or w.get("id") or "",
    }


def fetch_papers(field, key, ai_key, ai_model, today):
    terms = paper_terms(field, ai_key, ai_model)
    search = " OR ".join(f'"{t}"' if " " in t else t for t in terms)
    day = lambda n: (today - timedelta(days=n)).strftime("%Y-%m-%d")
    out = {"terms": terms, "recent": [], "cited": [], "topics": []}
    try:
        out["recent"] = [paper_row(w) for w in openalex_get({
            "search": search, "filter": f"from_publication_date:{day(30)},to_publication_date:{day(0)}",
            "sort": "publication_date:desc", "per-page": 8, "select": OPENALEX_SELECT}, key).get("results", [])]
        out["cited"] = [paper_row(w) for w in openalex_get({
            "search": search, "filter": f"from_publication_date:{day(365)}",
            "sort": "cited_by_count:desc", "per-page": 8, "select": OPENALEX_SELECT}, key).get("results", [])]
        groups = openalex_get({"search": search, "filter": f"from_publication_date:{day(90)}",
                               "group_by": "primary_topic.id"}, key).get("group_by", [])
        out["topics"] = [{"name": g.get("key_display_name"), "count": g.get("count")} for g in groups[:10]]
    except Exception as e:
        print(f"  OpenAlex 실패: {e}")
        out["error"] = str(e)[:200]
    return out


# ─────────────────────────────── 테스트용 가짜 데이터 ───────────────────────────────

def mock_data(keywords):
    now = datetime.now(KST)
    t = lambda m: (now - timedelta(minutes=m)).isoformat()
    def art(title, url, sec, m, desc="테스트용 요약문입니다. 실제 키를 넣으면 진짜 기사가 표시됩니다."):
        return {"title": title, "description": desc, "url": url,
                "naver_url": url if "naver" in url else None, "press": press_from_url(url),
                "published": t(m), "section": sec, "classified_by": "naver"}
    kw = {k: [art(f"[테스트] {k} 관련 {s} 기사 {i+1}",
                  f"https://n.news.naver.com/mnews/article/00{i}/{abs(hash(k))%10**8}{i}?sid={sid}", s, 40*i)
              for i, (s, sid) in enumerate([("정치", 100), ("경제", 101), ("경제", 101), ("세계", 104), ("사회", 102)])]
          for k in keywords}
    foreign = [
        {**art("블룸버그 \"연준, 12월 금리 인하 속도 조절 검토\"", "https://www.yna.co.kr/view/1", "경제", 30,
               "블룸버그통신은 23일(현지시간) 연준 관계자들을 인용해 금리 인하 속도를 늦출 수 있다고 보도했다."),
         "outlets": ["블룸버그"], "en_query": "Fed officials weigh slowing pace of rate cuts December"},
        {**art("WSJ \"엔비디아, 한국 반도체 공급망 확대\"…로이터도 보도", "https://www.hankyung.com/a/2", "경제", 90,
               "월스트리트저널(WSJ)과 로이터에 따르면 엔비디아는 한국 협력사를 늘린다."),
         "outlets": ["WSJ", "로이터"], "en_query": "Nvidia expands South Korea chip supply chain"},
        {**art("NYT \"트럼프 행정부, 대중 관세 추가 검토\"", "https://www.donga.com/news/3", "세계", 120,
               "뉴욕타임스(NYT)는 백악관이 중국산 제품에 대한 추가 관세를 검토 중이라고 전했다."),
         "outlets": ["NYT"]},
        {**art("닛케이 \"日 정부, 한일 정상회담 연내 개최 조율\"", "https://www.yna.co.kr/view/4", "정치", 200,
               "니혼게이자이신문은 일본 정부가 한일 정상회담 일정을 조율 중이라고 보도했다."),
         "outlets": ["닛케이"], "en_query": "Japan South Korea summit year end Nikkei"},
    ]
    op = [
        ("https://www.chosun.com/opinion/editorial/1", "[사설] 물가 다시 들썩, 금리 인하 서두를 때 아니다", "사설"),
        ("https://www.hani.co.kr/arti/opinion/editorial/2", "[사설] 고물가에 서민 부담, 금리 인하로 숨통 틔워야", "사설"),
        ("https://www.hankyung.com/article/3", "[사설] 환율 1400원대 고착, 기업 부담 커진다", "사설"),
        ("https://www.donga.com/news/Opinion/4", "[사설] 국회 예산 심사, 정쟁보다 민생 우선", "사설"),
        ("https://www.khan.co.kr/opinion/editorial/5", "[사설] 예산안 졸속 심사 되풀이 안 된다", "사설"),
        ("https://www.joongang.co.kr/article/6", "[시론] 금리 인하의 역설", "칼럼"),
        ("https://www.mk.co.kr/news/7", "[기자수첩] 구직급여 반복수급, 고칠 때다", "칼럼"),
    ]
    opinion = [{**art(ti, u, k, 300 + 20 * i), "kind": k,
                "keywords": [w for w in keywords if w.lower() in ti.lower()]} for i, (u, ti, k) in enumerate(op)]
    issues = {"method": "ai", "items": [
        {"issue": "금리 인하 논쟁", "summary": "물가 반등 속 한국은행의 금리 인하 시점을 두고 의견이 갈렸다.",
         "stances": [{"press": "조선일보", "view": "물가 불안 속 인하는 이르다"},
                     {"press": "한겨레", "view": "서민 부담 덜려면 인하해야"}], "ids": [0, 1, 5], "papers": 3},
        {"issue": "예산안 심사", "summary": "국회의 내년도 예산안 심사가 시작됐다.",
         "stances": [{"press": "동아일보", "view": "정쟁보다 민생 예산 우선"},
                     {"press": "경향신문", "view": "졸속 심사 관행 끊어야"}], "ids": [3, 4], "papers": 2},
        {"issue": "고환율", "summary": "원/달러 환율이 1400원대에 머물고 있다.",
         "stances": [{"press": "한국경제", "view": "기업 비용 부담 경고"}], "ids": [2], "papers": 1},
    ]}
    indicators = {"kr": [
        {"group": "시장금리", "name": "한국은행 기준금리", "value": "2.5", "unit": "%", "period": "20261023",
         "agency": "한국은행", "agency_url": "https://ecos.bok.or.kr", "source": "한국은행 ECOS", "source_url": "https://ecos.bok.or.kr"},
        {"group": "물가", "name": "소비자물가지수", "value": "117.3", "unit": "2020=100", "period": "202609",
         "agency": "통계청", "agency_url": "https://kosis.kr", "source": "한국은행 ECOS", "source_url": "https://ecos.bok.or.kr"},
        {"group": "고용", "name": "실업률", "value": "2.5", "unit": "%", "period": "202609",
         "agency": "통계청", "agency_url": "https://kosis.kr", "source": "한국은행 ECOS", "source_url": "https://ecos.bok.or.kr"},
        {"group": "환율", "name": "원/달러 환율(종가)", "value": "1,402.5", "unit": "원", "period": "20261023",
         "agency": "한국은행", "agency_url": "https://ecos.bok.or.kr", "source": "한국은행 ECOS", "source_url": "https://ecos.bok.or.kr"},
    ], "us": [
        {"group": "물가", "name": "소비자물가지수(CPI)", "value": "2.95", "unit": "%", "period": "2026-09-01",
         "prev": 2.9, "change": 0.05, "agency": "미 노동통계국(BLS)", "agency_url": "https://www.bls.gov/cpi/",
         "source": "FRED", "source_url": "https://fred.stlouisfed.org/series/CPIAUCSL"},
        {"group": "고용", "name": "실업률", "value": "4.30", "unit": "%", "period": "2026-09-01",
         "prev": 4.2, "change": 0.1, "agency": "미 노동통계국(BLS)", "agency_url": "https://www.bls.gov/cps/",
         "source": "FRED", "source_url": "https://fred.stlouisfed.org/series/UNRATE"},
    ]}
    return kw, foreign, opinion, issues, indicators


# ─────────────────────────────── 실행 ───────────────────────────────

def main():
    mock = "--mock" in sys.argv
    config = json.loads((ROOT / "keywords.json").read_text(encoding="utf-8"))
    fields = [f for f in config.get("fields", []) if f.get("name")]
    keywords = list(dict.fromkeys(config.get("keywords", []) + [k for f in fields for k in f.get("keywords", [])]))
    env = os.environ.get
    cid, secret = env("NAVER_CLIENT_ID", ""), env("NAVER_CLIENT_SECRET", "")
    ai_key, ai_model = env("ANTHROPIC_API_KEY", ""), config.get("ai_model", "claude-haiku-5-5")
    if not mock and not (cid and secret):
        sys.exit("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 없습니다.")

    now = datetime.now(KST)
    since = now - timedelta(hours=config.get("lookback_hours", 24))
    result = {"date": now.strftime("%Y-%m-%d"), "generated_at": now.isoformat(),
              "outlet_meta": {n: {"en": en, "domain": dom} for n, _, _, en, dom in FOREIGN},
              "config": {"keywords": config.get("keywords", []), "fields": fields,
                         "lookback_hours": config.get("lookback_hours", 24), "keep_days": config.get("keep_days", 30)},
              "papers": {}}

    if mock:
        kw, foreign, opinion, issues, indicators = mock_data(keywords)
        result.update(keywords=kw, foreign=foreign, opinion=opinion, issues=issues, indicators=indicators)
        for f in fields:
            if f.get("papers"):
                result["papers"][f["name"]] = {"terms": ["semiconductor", "large language model"], "topics": [
                    {"name": "Advanced Memory and Neural Computing", "count": 812}, {"name": "Semiconductor materials and devices", "count": 640}],
                    "recent": [{"title": "Scaling laws for 3D-stacked DRAM in AI accelerators", "date": "2026-10-20", "venue": "Nature Electronics",
                                "cited": 2, "authors": ["J. Kim", "S. Lee"], "topic": "Advanced Memory", "url": "https://doi.org/10.1038/test1"}],
                    "cited": [{"title": "Efficient inference of large language models on edge devices", "date": "2026-02-11", "venue": "Science",
                               "cited": 214, "authors": ["A. Smith"], "topic": "Neural computing", "url": "https://doi.org/10.1126/test2"}]}
    else:
        # 1) 키워드
        result["keywords"] = {}
        for k in keywords:
            try:
                result["keywords"][k] = fetch_keyword(k, since, cid, secret)
                print(f"{k}: {len(result['keywords'][k])}건")
            except Exception as e:
                print(f"{k}: 실패 - {e}")
                result["keywords"][k] = []

        # 2) 해외 인용
        print("해외 매체 인용 수집")
        try:
            result["foreign"] = fetch_foreign(since, cid, secret)
            if ai_key and result["foreign"]:
                add_original_queries(result["foreign"], ai_key, ai_model)
        except Exception as e:
            print(f"해외 인용: 실패 - {e}")
            result["foreign"] = []

        # 3) 사설·칼럼 + 이슈
        print("사설·칼럼 수집")
        try:
            op = fetch_opinion(since, keywords, cid, secret)
            result["opinion"] = op
            print(f"  사설 {sum(a['kind'] == '사설' for a in op)}건, 칼럼 {sum(a['kind'] == '칼럼' for a in op)}건")
            issues = None
            if ai_key and op:
                try:
                    issues = {"method": "ai", "items": issues_by_ai(op, ai_key, ai_model)}
                except Exception as e:
                    print(f"  AI 이슈 분석 실패, 단어 빈도로 대체: {e}")
            result["issues"] = issues or {"method": "frequency", "items": issues_by_frequency(op)}
        except Exception as e:
            print(f"사설·칼럼: 실패 - {e}")
            result["opinion"], result["issues"] = [], {"method": "none", "items": []}

        # 4) 경제지표
        result["indicators"] = {"kr": [], "us": []}
        if env("ECOS_API_KEY"):
            try:
                result["indicators"]["kr"] = fetch_ecos(env("ECOS_API_KEY"))
                print(f"ECOS 지표 {len(result['indicators']['kr'])}개")
            except Exception as e:
                print(f"ECOS: 실패 - {e}")
        if env("FRED_API_KEY"):
            result["indicators"]["us"] = fetch_fred(env("FRED_API_KEY"))
            print(f"FRED 지표 {len(result['indicators']['us'])}개")

    if not mock:
        for f in fields:
            if f.get("papers"):
                print(f"논문: {f['name']}")
                result["papers"][f["name"]] = fetch_papers(f, env("OPENALEX_API_KEY", ""), ai_key, ai_model, now)

    print(f"해외 인용 {len(result['foreign'])}건, 오피니언 {len(result['opinion'])}건, "
          f"이슈 {len(result['issues']['items'])}개")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"{result['date']}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

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
