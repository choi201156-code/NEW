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
  CHATKU_API_KEY, CHATKU_BASE_URL        선택: Claude 대신 경희대 챗쿠(팩트챗)로 AI 분석
  CHATKU_MODEL                           선택: 챗쿠에서 쓸 모델 (기본 claude-sonnet-5)

실행:
  python fetch_news.py          # 실제 수집
  python fetch_news.py --mock   # API 키 없이 가짜 데이터로 화면 테스트
"""
import hashlib
import html
import importlib
import math
import subprocess
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
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
# '사설'만으로 검색하면 사설 학원·사설 구급차 같은 기사에 묻혀서, 주요 신문은 신문 이름과 함께 한 번 더 검색
MAJOR_PAPERS = ["조선일보", "중앙일보", "동아일보", "한겨레", "경향신문", "한국일보", "서울신문", "세계일보",
                "국민일보", "문화일보", "매일경제", "한국경제", "서울경제", "파이낸셜뉴스", "아시아경제", "헤럴드경제", "이데일리"]


def clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def section_from_link(link: str):
    if "naver.com" not in link:
        return None
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
    return SID_MAP.get((qs.get("sid") or qs.get("sid1") or [None])[0])


# 스포츠: 네이버 스포츠 기사는 섹션 번호가 없어서 엉뚱한 분야로 가던 문제 → 먼저 걸러 '스포츠'로
SPORTS_HOSTS = ("sports", "football", "soccer", "baseball", "golf", "isplus.com", "osen.co.kr", "xportsnews", "stoo.com",
                "mhnse.com", "goal.com", "fourfourtwo", "interfootball", "besteleven", "spotvnews", "stnsports", "tennis")
SPORTS_RE = re.compile(r"감독|대표팀|국가대표|A매치|월드컵|올림픽|아시안게임|K리그|KBO|EPL|MLB|NBA|챔피언스리그|프리미어리그|"
                       r"라리가|분데스리가|세리에|국왕컵|코파|이적|선발 출전|득점|홈런|투수|타자|이닝|우승|결승골|"
                       r"축구|야구|농구|배구|골프|테니스|손흥민|이강인|김민재|황희찬|오타니|김하성|이정후")


SPORTS_STRONG = re.compile(r"A매치|K리그|KBO|EPL|MLB|NBA|챔피언스리그|프리미어리그|라리가|분데스리가|세리에A|월드시리즈|"
                           r"손흥민|이강인|김민재|황희찬|황인범|오타니|김하성|이정후|축구대표팀|야구대표팀")


def is_sports(title: str, url: str = "") -> bool:
    host = urllib.parse.urlparse(url).netloc.lower() + urllib.parse.urlparse(url).path.lower()[:20]
    if any(h in host for h in SPORTS_HOSTS):
        return True
    if SPORTS_STRONG.search(title):          # 리그·선수 이름처럼 하나만 나와도 스포츠인 말
        return True
    return len(set(SPORTS_RE.findall(title))) >= 2


def section_from_rules(text: str) -> str:
    scores = {sec: sum(text.count(w) for w in words) for sec, words in RULES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "기타"


# 네이버가 이름을 안 주는 언론사: 인터넷 주소 → 이름
PRESS_NAMES = {"yna.co.kr":"연합뉴스","yonhapnewstv.co.kr":"연합뉴스TV","news1.kr":"뉴스1","newsis.com":"뉴시스","newspim.com":"뉴스핌","news.einfomax.co.kr":"연합인포맥스","einfomax.co.kr":"연합인포맥스","etoday.co.kr":"이투데이","bravo.etoday.co.kr":"브라보마이라이프","enter.etoday.co.kr":"이투데이","dailian.co.kr":"데일리안","asiatoday.co.kr":"아시아투데이","en.asiatoday.co.kr":"아시아투데이","breaknews.com":"브레이크뉴스","wowtv.co.kr":"한국경제TV","inews24.com":"아이뉴스24","joynews24.com":"조이뉴스24","wikitree.co.kr":"위키트리","newscj.com":"천지일보","ekn.kr":"에너지경제","news.tf.co.kr":"더팩트","tf.co.kr":"더팩트","cstimes.com":"컨슈머타임스","kukinews.com":"쿠키뉴스","businesspost.co.kr":"비즈니스포스트","ebn.co.kr":"EBN","newsworks.co.kr":"뉴스웍스","joseilbo.com":"조세일보","news2day.co.kr":"뉴스투데이","newstomato.com":"뉴스토마토","thepublic.kr":"더퍼블릭","metroseoul.co.kr":"메트로신문","newdaily.co.kr":"뉴데일리","biz.newdaily.co.kr":"뉴데일리경제","cc.newdaily.co.kr":"뉴데일리 충청","tk.newdaily.co.kr":"뉴데일리 대구경북","pk.newdaily.co.kr":"뉴데일리 부산경남","gj.newdaily.co.kr":"뉴데일리 호남","newsway.co.kr":"뉴스웨이","viva100.com":"브릿지경제","ddaily.co.kr":"디지털데일리","seoulfn.com":"서울파이낸스","daily.hankooki.com":"데일리한국","hankooki.com":"한국아이닷컴","weekly.hankooki.com":"주간한국","sports.hankooki.com":"스포츠한국","golfhankook.hankooki.com":"골프한국","dailysportshankook.co.kr":"데일리스포츠한국","asiatime.co.kr":"아시아타임즈","edaily.co.kr":"이데일리","fntimes.com":"한국금융신문","mt.co.kr":"머니투데이","news.mtn.co.kr":"머니투데이방송","mediapen.com":"미디어펜","dnews.co.kr":"대한경제","econovill.com":"이코노믹리뷰","mhnse.com":"MHN스포츠","mhns.co.kr":"MHN","zdnet.co.kr":"지디넷코리아","sisajournal.com":"시사저널","sisajournal-e.com":"시사저널e","cnbnews.com":"CNB뉴스","sportschosun.com":"스포츠조선","mydaily.co.kr":"마이데일리","news.bbsi.co.kr":"BBS뉴스","koreatimes.com":"미주한국일보","economist.co.kr":"이코노미스트","mk.co.kr":"매일경제","mbn.mk.co.kr":"MBN","huffingtonpost.kr":"허프포스트코리아","ajunews.com":"아주경제","insight.co.kr":"인사이트","biz.sbs.co.kr":"SBS Biz","news.sbs.co.kr":"SBS","digitaltoday.co.kr":"디지털투데이","xportsnews.com":"엑스포츠뉴스","biz.heraldcorp.com":"헤럴드경제","heraldmuse.com":"헤럴드뮤즈","realfoods.co.kr":"리얼푸드","sedaily.com":"서울경제","fnnews.com":"파이낸셜뉴스","sportsseoul.com":"스포츠서울","news.kbs.co.kr":"KBS","tvreport.co.kr":"TV리포트","view.asiae.co.kr":"아시아경제","asiae.co.kr":"아시아경제","segyebiz.com":"세계비즈","osen.co.kr":"OSEN","biz.chosun.com":"조선비즈","it.chosun.com":"IT조선","economychosun.com":"이코노미조선","investchosun.com":"인베스트조선","realty.chosun.com":"땅집고","weekly.chosun.com":"주간조선","monthly.chosun.com":"월간조선","futurechosun.com":"더나은미래","digitalchosun.dizzo.com":"디지틀조선일보","gamechosun.co.kr":"게임조선","news.tvchosun.com":"TV조선","betanews.net":"베타뉴스","nongmin.com":"농민신문","ytn.co.kr":"YTN","joongang.co.kr":"중앙일보","koreajoongangdaily.com":"코리아중앙데일리","hankyung.com":"한국경제","magazine.hankyung.com":"한경비즈니스","donga.com":"동아일보","weekly.donga.com":"주간동아","dongascience.com":"동아사이언스","hankookilbo.com":"한국일보","chosun.com":"조선일보","hani.co.kr":"한겨레","segye.com":"세계일보","munhwa.com":"문화일보","kmib.co.kr":"국민일보","seoul.co.kr":"서울신문","khan.co.kr":"경향신문","nocutnews.co.kr":"노컷뉴스","ohmynews.com":"오마이뉴스","pressian.com":"프레시안","etnews.com":"전자신문","naeil.com":"내일신문","dt.co.kr":"디지털타임스","imnews.imbc.com":"MBC","news.jtbc.co.kr":"JTBC","mbn.co.kr":"MBN","mbnmoney.mbn.co.kr":"매일경제TV","ichannela.com":"채널A","news.ebs.co.kr":"EBS","ktv.go.kr":"KTV 국민방송","korea.kr":"대한민국 정책브리핑","natv.go.kr":"국회방송","arirang.com":"아리랑TV","voakorea.com":"VOA 한국어","tbs.seoul.kr":"TBS","news.cpbc.co.kr":"가톨릭평화방송","cbci.co.kr":"CBS","btnnews.tv":"BTN불교TV","vop.co.kr":"민중의소리","mediatoday.co.kr":"미디어오늘","sisain.co.kr":"시사IN","newstapa.org":"뉴스타파","hankyoreh.com":"한겨레","kyosu.net":"교수신문","journalist.or.kr":"기자협회보","labortoday.co.kr":"매일노동뉴스","womennews.co.kr":"여성신문","dailynk.com":"데일리NK","tongilnews.com":"통일뉴스","kookbang.dema.mil.kr":"국방일보","thebell.co.kr":"더벨","dealsite.co.kr":"딜사이트","news.dealsitetv.com":"딜사이트TV","newsprime.co.kr":"프라임경제","news.bizwatch.co.kr":"비즈워치","bizwatch.co.kr":"비즈워치","bloter.net":"블로터","venturesquare.net":"벤처스퀘어","platum.kr":"플래텀","byline.network":"바이라인네트워크","techm.kr":"테크M","thelec.kr":"디일렉","aitimes.com":"AI타임스","aitimes.kr":"인공지능신문","hellot.net":"헬로티","irobotnews.com":"로봇신문","kbench.com":"케이벤치","itworld.co.kr":"ITWorld","cnet.co.kr":"씨넷코리아","ilovepc.co.kr":"아이러브PC","itdaily.kr":"아이티데일리","datanet.co.kr":"데이터넷","boannews.com":"보안뉴스","dailysecu.com":"데일리시큐","koit.co.kr":"정보통신신문","itbiznews.com":"IT비즈뉴스","epnc.co.kr":"테크월드뉴스","e-science.co.kr":"e사이언스","hellodd.com":"헬로디디","techholic.co.kr":"테크홀릭","kr.aving.net":"에이빙","inven.co.kr":"인벤","thisisgame.com":"디스이즈게임","gamemeca.com":"게임메카","gametoc.co.kr":"게임톡","khgames.co.kr":"경향게임스","gameple.co.kr":"게임플","gamevu.co.kr":"게임뷰","game.dailyesports.com":"데일리e스포츠","tokenpost.kr":"토큰포스트","coinreaders.com":"코인리더스","decenter.kr":"디센터","pinpointnews.co.kr":"핀포인트뉴스","job-post.co.kr":"잡포스트","joongangenews.com":"중앙이코노미뉴스","polinews.co.kr":"폴리뉴스","enewstoday.co.kr":"이뉴스투데이","gokorea.kr":"공감신문","lawissue.co.kr":"로이슈","econotimes.com":"이코노타임즈","sentv.co.kr":"서울경제TV","the-today.com":"더투데이","newsquest.co.kr":"뉴스퀘스트","ppss.kr":"ㅍㅍㅅㅅ","obsnews.co.kr":"OBS","popcornnews.net":"팝콘뉴스","ziksir.com":"직썰","newsclaim.co.kr":"뉴스클레임","sateconomy.co.kr":"시사경제신문","slist.kr":"싱글리스트","getnews.co.kr":"글로벌경제신문","vegannews.co.kr":"비건뉴스","kfenews.co.kr":"한국금융경제신문","newstnt.com":"뉴스티앤티","newsian.co.kr":"뉴시안","megaeconomy.co.kr":"메가경제","fntoday.co.kr":"파이낸셜투데이","pointdaily.co.kr":"포인트데일리","finomy.com":"현대경제신문","greened.kr":"녹색경제신문","the-biz.co.kr":"더비즈","m-economynews.com":"M이코노미뉴스","biztribune.co.kr":"비즈트리뷴","pennmike.com":"펜앤드마이크","financialpost.co.kr":"파이낸셜포스트","koreareport.co.kr":"코리아리포트","weeklytoday.com":"위클리오늘","newsworker.co.kr":"뉴스워커","bizwnews.com":"비즈월드","00news.co.kr":"공공뉴스","straightnews.co.kr":"스트레이트뉴스","choicenews.co.kr":"초이스경제","wikileaks-kr.org":"위키리크스한국","queen.co.kr":"여성종합뉴스 퀸","newsfreezone.co.kr":"뉴스프리존","tfmedia.co.kr":"TF미디어","bntnews.co.kr":"bnt뉴스","junggi.co.kr":"중기이코노미","smartbizn.com":"스마트비즈","newsroad.co.kr":"뉴스로드","industrynews.co.kr":"인더스트리뉴스","tournews21.com":"투어코리아","globalepic.co.kr":"글로벌에픽","whitepaper.co.kr":"화이트페이퍼","ngonews.kr":"NGO뉴스","fetv.co.kr":"FETV","srtimes.kr":"SR타임스","thefairnews.co.kr":"더페어","smedaily.co.kr":"중소기업신문","ftoday.co.kr":"파이낸스투데이","newstown.co.kr":"뉴스타운","newsdream.kr":"뉴스드림","moneystorm.kr":"머니스톰","thepowernews.co.kr":"더파워","dailypop.kr":"데일리팝","mdtoday.co.kr":"메디컬투데이","businesskorea.co.kr":"비즈니스코리아","skyedaily.com":"스카이데일리","paxetv.com":"팍스경제TV","starnewskorea.com":"스타뉴스","sisaon.co.kr":"시사오늘","mediawatch.kr":"미디어워치","businessplus.kr":"비즈니스플러스","startuptoday.co.kr":"스타트업투데이","ttlnews.com":"티티엘뉴스","thefirstmedia.net":"더퍼스트","youthdaily.co.kr":"청년일보","theguru.co.kr":"더구루","econonews.co.kr":"이코노뉴스","aflnews.co.kr":"농수축산신문","sisaweek.com":"시사위크","asiaa.co.kr":"아시아에이","ntoday.co.kr":"엔투데이","ilemonde.com":"르몽드 디플로마티크","isplus.com":"일간스포츠","spotvnews.co.kr":"스포티비뉴스","sisafocus.co.kr":"시사포커스","wolyo.co.kr":"월요신문","seouleconews.com":"서울이코노미뉴스","opinionnews.co.kr":"오피니언뉴스","kpenews.com":"한국정책신문","fpn119.co.kr":"FPN 소방방재신문","medipana.com":"메디파나뉴스","thereport.co.kr":"더리포트","viewsnnews.com":"뷰스앤뉴스","sisunnews.co.kr":"시선뉴스","consumernews.co.kr":"소비자가만드는신문","woodkorea.co.kr":"한국목재신문","medicalworldnews.co.kr":"메디컬월드뉴스","efnews.co.kr":"이에프뉴스","s-journal.co.kr":"S저널","lcnews.co.kr":"라이센스뉴스","bzeronews.com":"비제로뉴스","tenasia.co.kr":"텐아시아","sportsworldi.com":"스포츠월드","delighti.co.kr":"딜라이트닷컴","safetimes.co.kr":"세이프타임즈","ezyeconomy.com":"이지경제","smarttoday.co.kr":"스마트투데이","koreaittimes.com":"코리아IT타임스","newslock.co.kr":"뉴스락","ilyosisa.co.kr":"일요시사","livebiz.today":"라이브뉴스","press9.kr":"프레스나인","ceoscoredaily.com":"CEO스코어데일리","issuenbiz.com":"이슈앤비즈","pointe.co.kr":"포인트경제","m-joongang.com":"마이경제","kizmom.com":"키즈맘","lawtv.kr":"법률방송","inews365.com":"충북일보","thevaluenews.co.kr":"더밸류뉴스","nspna.com":"NSP통신","newswhoplus.com":"뉴스후플러스","ikld.kr":"국토일보","mediafine.co.kr":"미디어파인","fourfourtwo.co.kr":"포포투","kormedi.com":"코메디닷컴","bokuennews.com":"보건뉴스","bigtanews.co.kr":"빅터뉴스","agrinet.co.kr":"한국농어민신문","autodaily.co.kr":"오토데일리","newspost.kr":"뉴스포스트","insightkorea.co.kr":"인사이트코리아","newsfc.co.kr":"뉴스FC","theviewers.co.kr":"더뷰어스","discoverynews.kr":"디스커버리뉴스","raonnews.com":"라온뉴스","thebigdata.co.kr":"빅데이터뉴스","womaneconomy.co.kr":"여성경제신문","stoo.com":"스포츠투데이","mstoday.co.kr":"메트로시티투데이","theopiniontimes.news":"오피니언타임스","livesnews.com":"라이브스뉴스","greenpostkorea.co.kr":"그린포스트코리아","wsobi.com":"소비자경제","catchnews.kr":"캐치뉴스","kor.theasian.asia":"아시아엔","rightknow.co.kr":"알권리","apparelnews.co.kr":"어패럴뉴스","womentimes.co.kr":"여성타임스","swtvnews.com":"소비자TV","newstopkorea.com":"뉴스탑코리아","hg-times.com":"한강타임즈","globale.co.kr":"글로벌이코노믹","g-enews.com":"글로벌이코노믹","enetnews.co.kr":"이넷뉴스","dailypharm.com":"데일리팜","new.dailypharm.com":"데일리팜","intn.co.kr":"정보통신뉴스","beyondpost.co.kr":"비욘드포스트","topdaily.kr":"톱데일리","socialvalue.kr":"소셜밸류","thetracker.co.kr":"더트래커","kpinews.kr":"KPI뉴스","bosa.co.kr":"의학신문","stnsports.co.kr":"STN스포츠","naeil.co.kr":"내일신문","newsverse.kr":"뉴스버스","christiantoday.co.kr":"크리스천투데이","mediaus.co.kr":"미디어스","worktoday.co.kr":"워크투데이","newsbrite.net":"뉴스브라이트","smarttimes.co.kr":"스마트타임스","economytalk.kr":"이코노미톡뉴스","snmnews.com":"에스앤엠뉴스","gpkorea.com":"골프포스트","koreadaily.com":"미주중앙일보","eroun.net":"이로운넷","ekoreanews.co.kr":"이코리아","pressman.kr":"프레스맨","sportalkorea.com":"스포탈코리아","newsen.com":"뉴스엔","artinsight.co.kr":"아트인사이트","rapportian.com":"라포르시안","amnews.co.kr":"농업경제신문","ddanzi.com":"딴지일보","fortunekorea.co.kr":"포춘코리아","kdfnews.com":"한국면세뉴스","fieldnews.kr":"필드뉴스","newsmap.co.kr":"뉴스맵","seoultimes.news":"서울타임스","inthenews.co.kr":"인더뉴스","webeconomy.co.kr":"웹이코노미","sisacast.kr":"시사캐스트","hbnpress.com":"HBN뉴스","ktnews.com":"한국섬유신문","energy-news.co.kr":"에너지신문","weeklytrade.co.kr":"위클리트레이드","news.skbroadband.com":"SK브로드밴드","slownews.kr":"슬로우뉴스","dailymedi.com":"데일리메디","1conomynews.co.kr":"1코노미뉴스","lkp.news":"리버티코리아포스트","insnews.co.kr":"한국보험신문","financialreview.co.kr":"파이낸셜리뷰","dailybrief.co.kr":"데일리브리프","meconomynews.com":"시장경제","kbiznews.co.kr":"중소기업뉴스","dream.kotra.or.kr":"KOTRA 해외시장뉴스","ferrotimes.com":"철강금속신문","pharmnews.com":"팜뉴스","todaykorea.co.kr":"투데이코리아","kpanews.co.kr":"약사공론","nextdaily.co.kr":"넥스트데일리","newsinside.kr":"뉴스인사이드","atstar1.com":"앳스타일","onews.tv":"오뉴스","energydaily.co.kr":"에너지데일리","hitnews.co.kr":"히트뉴스","lawtalknews.co.kr":"로톡뉴스","lawtimes.co.kr":"법률신문","thescoop.co.kr":"더스쿠프","factin.co.kr":"팩트인","niceeconomy.co.kr":"나이스경제","updownnews.co.kr":"업다운뉴스","gosiweek.com":"고시위크","ablenews.co.kr":"에이블뉴스","welfarenews.net":"웰페어뉴스","esgeconomy.com":"ESG경제","newscham.net":"참세상","apnews.kr":"AP신문","tleaves.co.kr":"티리브스","areyou.co.kr":"아유경제","wemakenews.co.kr":"위메이크뉴스","thedailypost.kr":"데일리포스트","docdocdoc.co.kr":"청년의사","medigatenews.com":"메디게이트뉴스","insidevina.com":"인사이드비나","nbntv.co.kr":"NBN","nbntv.kr":"NBN","babytimes.co.kr":"베이비타임즈","e2news.com":"이투뉴스","news.unn.net":"한국대학신문","ikpnews.net":"한국농정신문","datasom.co.kr":"데이터솜","peoplewatch.co.kr":"피플워치","besteleven.com":"베스트일레븐","interfootball.co.kr":"인터풋볼","marieclairekorea.com":"마리끌레르","handmk.com":"한국농촌경제신문","todayenergy.kr":"투데이에너지","tennis.co.kr":"테니스코리아","starnewsk.com":"스타뉴스","ngetnews.com":"뉴스저널리즘","withinnews.co.kr":"위드인뉴스","lifein.news":"라이프인","amenews.kr":"신소재경제","biotimes.co.kr":"바이오타임즈","dailycar.co.kr":"데일리카","medisobizanews.com":"메디소비자뉴스","civicnews.com":"시빅뉴스","newswatch.kr":"뉴스워치","goodnews1.com":"굿뉴스","lecturernews.com":"교수신문","itooza.com":"아이투자","100ssd.co.kr":"백세시대","electimes.com":"전기신문","beminor.com":"비마이너","pressian.co.kr":"프레시안","ilyoseoul.co.kr":"일요서울","kbanker.co.kr":"대한금융신문","celuvmedia.com":"셀럽미디어","rookie.co.kr":"루키","dailyvet.co.kr":"데일리벳","k-health.com":"헬스경향","medical-tribune.co.kr":"메디칼트리뷴","epj.co.kr":"전력경제신문","netongs.com":"넷통뉴스","taxtimes.co.kr":"한국세정신문","idsn.co.kr":"치과신문","taxwatch.co.kr":"택스워치","koreastocknews.com":"코리아스탁뉴스","newswell.co.kr":"뉴스웰","dizzotv.com":"디지틀조선TV","bizhankook.com":"비즈한국","worknworld.kctu.org":"노동과세계","lawleader.co.kr":"로리더","farminsight.net":"팜인사이트","news.appstory.co.kr":"앱스토리","dailysmart.co.kr":"데일리스마트","gqkorea.co.kr":"GQ코리아","ibabynews.com":"베이비뉴스","foodnews.co.kr":"식품음료신문","ilyo.co.kr":"일요신문","safetynews.co.kr":"안전신문","upkoreanews.kr":"업코리아","newsq.co.kr":"뉴스큐","bulkyo21.com":"불교신문","tennispeople.kr":"테니스피플","knpnews.com":"한국뉴스투데이","sisanews24.co.kr":"시사뉴스24","ksg.co.kr":"코리아쉬핑가제트","akomnews.com":"한의신문","kr.people.com.cn":"인민망 한국어판","worldkorean.net":"월드코리안뉴스","kenews.co.kr":"한국농어촌방송","housingherald.co.kr":"하우징헤럴드","datanews.co.kr":"데이터뉴스","traveltimes.co.kr":"여행신문","interview365.com":"인터뷰365","koscaj.com":"대한전문건설신문","whosaeng.com":"후생신보","thegolftimes.co.kr":"더골프타임즈","the-pr.co.kr":"더피알","thedrive.co.kr":"더드라이브","esquirekorea.co.kr":"에스콰이어","wkorea.com":"W코리아","maniareport.com":"마니아타임즈","footballist.co.kr":"풋볼리스트","tgdaily.co.kr":"TG데일리","bizwork.co.kr":"비즈워크","newstree.kr":"뉴스트리","doctorsnews.co.kr":"의협신문","gobalnews.com":"고발뉴스","imwood.co.kr":"나무신문","widedaily.com":"와이드경제","newsmp.com":"의약뉴스","ikoreanspirit.com":"코리안스피릿","engdaily.com":"엔지니어링데일리","dispatch.co.kr":"디스패치","technoa.co.kr":"테크노아","hyunbulnews.com":"현대불교신문","worklaw.co.kr":"노동법률","kmecnews.co.kr":"전기공사저널","catholicnews.co.kr":"가톨릭뉴스 지금여기","farmnmarket.com":"팜&마켓","newsfarm.co.kr":"뉴스팜","ktsketch.co.kr":"KT스케치","cwn.kr":"CWN","tvdaily.co.kr":"티브이데일리","the-stock.kr":"더스탁","ize.co.kr":"아이즈","fsnews.co.kr":"금융소비자뉴스","ch.yes24.com":"채널예스","edupress.kr":"에듀프레스","dentalnews.or.kr":"치의신보","aptn.co.kr":"한국아파트신문","koreahealthlog.com":"코리아헬스로그","thesportstimes.co.kr":"더스포츠타임스","newsmaker.or.kr":"뉴스메이커","allurekorea.com":"얼루어코리아","spochoo.com":"스포추","youngnong.co.kr":"농업인신문","newscape.co.kr":"뉴스케이프","radiokorea.com":"라디오코리아","medicaltimes.com":"메디칼타임즈","mtnews.net":"의료기기뉴스","apsk.co.kr":"한국산업안전보건신문","gasnews.com":"가스신문","newspenguin.com":"뉴스펭귄","redian.org":"레디앙","rank5.kr":"랭크파이브","marketnews.co.kr":"마켓뉴스","ftimes.kr":"파이낸셜타임즈","news.hidoc.co.kr":"하이닥","laborplus.co.kr":"참여와혁신","thebk.co.kr":"더뱅크","pn.or.kr":"평화뉴스","beopbo.com":"법보신문","elle.co.kr":"엘르코리아","digitalbizon.com":"디지털비즈온","impacton.net":"임팩트온","newsnjoy.or.kr":"뉴스앤조이","harpersbazaar.co.kr":"하퍼스바자","fashionn.com":"패션엔","stardailynews.co.kr":"스타데일리뉴스","lec.co.kr":"법률저널","kmedinfo.co.kr":"한국의약통신","medicopharma.co.kr":"메디코파마뉴스","veritas-a.com":"베리타스알파","lak.co.kr":"환경과조경","banronbodo.com":"반론보도닷컴","yakup.com":"약업신문","konas.net":"코나스","thebilliards.kr":"더빌리어즈","mkhealth.co.kr":"매경헬스","dandinews.com":"단디뉴스","engjournal.co.kr":"엔지니어링저널","pckworld.com":"기독공보","journal.kobeta.com":"방송과기술","itimes.co.kr":"인천일보","news.ifm.kr":"경인방송","kgnews.co.kr":"경기신문","m-i.kr":"매일일보","hansbiz.co.kr":"한스경제","topstarnews.net":"톱스타뉴스","gukjenews.com":"국제뉴스","sidae.com":"시대일보","jasm.co.kr":"자유일보","4th.kr":"포쓰저널","newsgn.com":"뉴스경남","iminju.net":"민주신문","ksmnews.co.kr":"경상매일신문","goodmorningcc.com":"굿모닝충청","jeonmae.co.kr":"전국매일신문","jibs.co.kr":"JIBS","goodkyung.com":"굿경제","thepowernews.kr":"더파워","sisaweek.co.kr":"시사위크","iusm.co.kr":"울산매일","ulsanpress.net":"울산신문","ksilbo.co.kr":"경상일보","usmbc.co.kr":"울산MBC","ubc.co.kr":"UBC울산방송","idaegu.co.kr":"대구신문","idaegu.com":"대구일보","sjbnews.com":"새전북신문","jnilbo.com":"전남일보","gnmaeil.com":"경남매일","gndomin.com":"경남도민신문","namdonews.com":"남도일보","kjdaily.com":"광주매일신문","ujeil.com":"울산제일일보","kyeonggi.com":"경기일보","kyongbuk.co.kr":"경북일보","incheonilbo.com":"인천일보","shinailbo.co.kr":"신아일보","ggilbo.com":"금강일보","daejonilbo.com":"대전일보","kado.net":"강원도민일보","cctimes.kr":"충청타임즈","gnnews.co.kr":"경남신문","ccdn.co.kr":"충청일보","ikbc.co.kr":"KBC광주방송","dynews.co.kr":"동양일보","idomin.com":"경남도민일보","hidomin.com":"경북도민일보","knnews.co.kr":"경남신문","dkilbo.com":"대경일보","kbmaeil.com":"경북매일신문","kihoilbo.co.kr":"기호일보","jndn.com":"전남매일","jemin.com":"제민일보","ccdailynews.com":"충청매일","kbsm.net":"경북신문","joongboo.com":"중부일보","kwangju.co.kr":"광주일보","cctoday.co.kr":"충청투데이","jbnews.com":"중부매일","chungnamilbo.co.kr":"충남일보","jeollailbo.com":"전라일보","jjan.kr":"전북일보","siminilbo.co.kr":"시민일보","yeongnam.com":"영남일보","kjmbc.co.kr":"광주MBC","ysmbc.co.kr":"여수MBC","tjb.co.kr":"TJB대전방송","jtv.co.kr":"JTV전주방송","mpmbc.co.kr":"목포MBC","domin.co.kr":"전북도민일보","dgmbc.com":"대구MBC","suwonilbo.kr":"수원일보","news.lghellovision.net":"LG헬로비전","incheontoday.com":"인천투데이","nongaek.com":"오늘의 사설","g1tv.co.kr":"G1강원민방","cjb.co.kr":"CJB청주방송","jejusori.net":"제주의소리","jeonmin.co.kr":"전민일보","busan.com":"부산일보","andongmbc.co.kr":"안동MBC","chmbc.co.kr":"춘천MBC","gimhaenews.co.kr":"김해뉴스","news.knn.co.kr":"KNN","tjmbc.co.kr":"대전MBC","tbc.co.kr":"TBC대구방송","phmbc.co.kr":"포항MBC","incheonnews.com":"인천뉴스","ccreview.co.kr":"충청리뷰","jejunews.com":"제주일보","imaeil.com":"매일신문","jejumbc.com":"제주MBC","jmbc.co.kr":"전주MBC","mdilbo.com":"무등일보","samdailbo.com":"삼다일보","ccn.hcn.co.kr":"HCN충북방송","econotelling.com":"이코노텔링","news.mbccb.co.kr":"MBC충북","yangsanilbo.com":"양산일보","incheonin.com":"인천in","headlinejeju.co.kr":"헤드라인제주","mbceg.co.kr":"MBC경남","gjdream.com":"광주드림","mygoyang.com":"마이고양","mediajeju.com":"미디어제주","jejumaeil.net":"제주매일","jejudomin.co.kr":"제주도민일보","dtnews24.com":"디트뉴스24","ibuan.com":"부안독립신문","dongponews.net":"동포뉴스","busanmbc.co.kr":"부산MBC","newsjeju.net":"뉴스제주","siminsori.com":"시민의소리","srn.hcn.co.kr":"HCN서초방송","hdhy.co.kr":"현대해양","kookje.co.kr":"국제신문","kwnews.co.kr":"강원일보","kyeongin.com":"경인일보","joongdo.co.kr":"중도일보","newsgn.co.kr":"뉴스경남","n.news.naver.com":"네이버뉴스","nytimes.com":"뉴욕타임스","bbc.com":"BBC","the-today.kr":"더투데이","ekoreanews.com":"이코리아","ohmynews.co.kr":"오마이뉴스","sejungilbo.com":"세정일보"}


def press_from_url(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.").removeprefix("m.")
    h = host
    while "." in h:                      # 긴 주소부터: biz.newdaily.co.kr → newdaily.co.kr
        if h in PRESS_NAMES:
            return PRESS_NAMES[h]
        h = h.split(".", 1)[1]
    for domain, name in PRESS_MAP.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host


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


def search_recent(query, since, cid, secret, max_pages=10):
    """네이버 뉴스를 최신순으로 넘기다가 기준 시각보다 오래된 기사가 나오면 멈춤"""
    out, seen = [], set()
    for start in range(1, max_pages * 100 + 1, 100):
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
            if not sec and is_sports(title, orig):
                sec = "스포츠"
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


AI = {"provider": "anthropic", "base": ""}   # main()에서 설정


def chatku_url(base, path):
    base = base.strip().rstrip("/")
    base = re.sub(r"/chat/completions$", "", base)
    if not base.endswith("/v1/gateway"):
        base = re.sub(r"/v1$", "", base) + "/v1/gateway"
    return f"{base}/{path}"


def chatku_chat(prompt, key, model, system, max_tokens):
    """
    챗쿠 모델은 답을 쓰기 전에 '속으로 생각하는' 데 토큰을 많이 씀 (보이지 않는 추론).
    그래서 max_tokens를 넉넉히 주고, 생각만 하다 끝나서 답이 비면 한도를 늘려 다시 시도.
    """
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    budget = max_tokens + 12000
    for _ in range(3):
        body = {"model": model, "messages": msgs, "max_tokens": budget}
        if not AI.get("no_effort"):
            body["reasoning_effort"] = "low"      # 생각을 짧게 (게이트웨이가 모르는 옵션이면 빼고 다시)
        try:
            res = http_json(chatku_url(AI["base"], "chat/completions/"), {
                "content-type": "application/json", "Authorization": f"Bearer {key}"}, body, timeout=180)
        except urllib.error.HTTPError as e:
            if e.code in (400, 422) and not AI.get("no_effort"):
                AI["no_effort"] = True
                continue
            raise ValueError(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")
        ch = res["choices"][0]
        c = (ch.get("message") or {}).get("content") or ""
        c = c if isinstance(c, str) else "".join(x.get("text", "") for x in c)
        if ch.get("finish_reason") == "length" and budget < 32000:
            budget = min(32000, budget * 2)      # 생각하느라 한도를 다 씀 → 늘려서 다시
            continue
        if not c.strip():
            raise ValueError(f"빈 응답 (finish_reason={ch.get('finish_reason')}, usage={res.get('usage')})")
        return c
    raise ValueError(f"한도를 늘려도 답이 끝나지 않음 (usage={res.get('usage')})")


def claude(prompt, key, model, system=None, max_tokens=8000):
    """AI 호출 → 텍스트 (Claude 직접 또는 챗쿠 게이트웨이)"""
    for attempt in range(2):
        try:
            if AI["provider"] == "chatku":
                return chatku_chat(prompt, key, model, system, max_tokens)
            body = {"model": model, "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}]}
            if system:
                body["system"] = system
            res = http_json("https://api.anthropic.com/v1/messages", {
                "content-type": "application/json", "x-api-key": key,
                "anthropic-version": "2023-06-01"}, body, timeout=180)
            return "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
        except Exception as e:
            if attempt:
                raise
            print(f"  Claude 재시도: {e}")
            time.sleep(5)


def chatku_probe(key, model):
    """챗쿠 게이트웨이가 한 번에 내보내는 답의 길이 한도를 재 봄 (결과는 데이터 파일 ai_status.probe 에 기록)"""
    out = {}
    try:
        ms = http_json(chatku_url(AI["base"], "models/"), {"Authorization": f"Bearer {key}"}, timeout=30)
        lst = ms.get("data", ms) if isinstance(ms, dict) else ms
        hit = [m for m in (lst or []) if isinstance(m, dict) and model in (m.get("id"), m.get("name"), m.get("model"))]
        out["model_info"] = hit[0] if hit else f"목록에 {model} 없음 (모델 {len(lst or [])}개)"
    except Exception as e:
        out["model_info"] = f"모델 목록 실패: {str(e)[:150]}"
    for want in (8000, 2000):
        t0 = time.time()
        try:
            res = http_json(chatku_url(AI["base"], "chat/completions/"), {
                "content-type": "application/json", "Authorization": f"Bearer {key}"},
                {"model": model, "max_tokens": want, "messages": [{"role": "user", "content":
                 "1부터 3000까지의 숫자를 쉼표로 이어서 끝까지 써 줘. 다른 말은 하지 마."}]}, timeout=180)
            ch = (res.get("choices") or [{}])[0]
            c = (ch.get("message") or {}).get("content") or ""
            c = c if isinstance(c, str) else "".join(x.get("text", "") for x in c)
            nums = re.findall(r"\d+", c)
            out[f"max_tokens_{want}"] = {"finish_reason": ch.get("finish_reason"), "usage": res.get("usage"),
                                         "chars": len(c), "last_number": nums[-1] if nums else None,
                                         "seconds": round(time.time() - t0, 1)}
        except urllib.error.HTTPError as e:
            out[f"max_tokens_{want}"] = f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}"
        except Exception as e:
            out[f"max_tokens_{want}"] = f"실패: {str(e)[:200]}"
    print(f"  챗쿠 길이 측정: {json.dumps(out, ensure_ascii=False)[:600]}")
    return out


def parse_json(text):
    """응답 속 JSON 덩어리 꺼내기 (중간에 잘린 응답도 살릴 수 있는 만큼 살림)"""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)(```|$)", text)
    text = m.group(1) if m else text
    idx = [i for i in (text.find("["), text.find("{")) if i >= 0]
    if not idx:
        raise ValueError(f"JSON이 없는 응답: {text[:120]!r}")
    text = text[min(idx):].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 잘린 응답: 완성된 항목이 끝나는 지점마다 열린 괄호를 닫아 보며 가장 긴 것을 살림
    stack, ins, esc, cuts = [], False, False, []
    for i, ch in enumerate(text):
        if ins:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                ins = False
            continue
        if ch == '"':
            ins = True
        elif ch in "[{":
            stack.append("]" if ch == "[" else "}")
        elif ch in "]}" and stack:
            stack.pop()
            cuts.append((i, "".join(reversed(stack))))
    for i, closers in reversed(cuts):
        try:
            return json.loads(text[:i + 1] + closers)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"JSON 해석 실패: {text[:120]!r}")


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


# 본문에서만 언급돼도 인용으로 인정할 검색어 — '○○통신·○○신문'처럼 사진 출처 표기에는 잘 안 쓰는 형태
BODY_QUERY = {
    "블룸버그": "블룸버그통신", "로이터": "로이터통신", "WSJ": "월스트리트저널", "FT": "파이낸셜타임스",
    "NYT": "뉴욕타임스", "WP": "워싱턴포스트", "AP": "AP통신", "AFP": "AFP통신", "이코노미스트": "이코노미스트지",
    "닛케이": "니혼게이자이신문", "요미우리": "요미우리신문", "아사히": "아사히신문", "교도": "교도통신",
    "신화": "신화통신", "글로벌타임스": "글로벌타임스", "SCMP": "사우스차이나모닝포스트",
}
PHOTO_RE = re.compile(r"^\s*[\[【(]\s*(포토|사진|화보|이 ?시각|영상|그래픽|카드뉴스|인포그래픽)")


def fetch_foreign(since, cid, secret):
    """
    해외 매체 이름으로 네이버 뉴스를 검색.
    - 제목·요약에 매체명이 나오면 확실한 인용
    - '○○통신'처럼 인용할 때 쓰는 이름으로 검색돼 걸린 기사는, 요약엔 없어도 본문 인용으로 인정 (body)
    """
    by_url = {}

    def add(a, outlets, body=False):
        if a["url"] not in by_url:
            a.update(outlets=[], body_outlets=[])
            by_url[a["url"]] = a
        it = by_url[a["url"]]
        for o in outlets:
            if o not in it["outlets"]:
                it["outlets"].append(o)
            if body and o not in it["body_outlets"]:
                it["body_outlets"].append(o)
            if not body and o in it["body_outlets"]:
                it["body_outlets"].remove(o)

    for name, query, *_ in FOREIGN:
        queries = [(query, False)]
        if name in BODY_QUERY:
            queries = [(q, q == BODY_QUERY[name]) for q in dict.fromkeys([query, BODY_QUERY[name]])]
        n = 0
        for q, body_ok in queries:
            try:
                arts = search_recent(q, since, cid, secret)
            except Exception as e:
                print(f"  {name}({q}): 실패 - {e}")
                continue
            for a in arts:
                if opinion_kind(a["title"], a["_link"]) or PHOTO_RE.match(a["title"]):
                    continue
                found = detect_outlets(a["title"], a["description"])
                if name in found:
                    add(a, found); n += 1
                elif body_ok:
                    add(a, [name], body=True); n += 1
        print(f"  {name}: {n}건")
    return strip_private(list(by_url.values()))


def add_original_queries(items, key, model, limit=180):
    """한국어 기사 → 해외 원문을 찾을 영어 검색어 (AI). 오래 걸리지 않게 최신 기사 일부만, 이미 만든 건 건너뜀"""
    todo = [a for a in sorted(items, key=lambda a: a["published"], reverse=True) if not a.get("en_query")][:limit]
    for i in range(0, len(todo), 60):
        if time.time() - START > 18 * 60:
            print("  시간이 오래 걸려 원문 검색어 만들기를 멈춰요")
            break
        chunk = todo[i:i + 60]
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
    """사설은 전날 저녁에 미리 올라오는 경우가 많아서 36시간 범위로 찾음"""
    since = min(since, datetime.now(KST) - timedelta(hours=36))
    by_url = {}
    for q in OPINION_QUERIES + [f"{p} 사설" for p in MAJOR_PAPERS]:
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
    """1단계: 같은 사안끼리 묶기(짧은 출력) → 2단계: 이슈마다 설명·신문별 메시지(짧은 출력 여러 번)"""
    eds = [(i, a) for i, a in enumerate(items) if a["kind"] == "사설"][:220] or list(enumerate(items))[:220]
    cols = [(i, a) for i, a in enumerate(items) if a["kind"] == "칼럼"][:80]
    fmt = lambda pairs, n: "\n".join(f'{i}. ({a["press"]}) {a["title"]} — {a["description"][:n]}' for i, a in pairs)
    prompt = f"""오늘 한국 신문들의 사설과 칼럼 목록이야. 같은 사안을 다룬 글끼리 묶어줘.

규칙:
- 이슈는 오늘 일어난 '구체적인 사안'이어야 해: 특정 사건·발언·정책 결정·판결·인사처럼 날짜가 있는 일.
  'AI', '미래', '저출생', '경제'처럼 늘 다루는 넓은 주제는 이슈가 아니야.
- 사설 2개 신문 이상이 다룬 사안만, 사설을 쓴 신문 수가 많은 순서대로 최대 7개.
- issue: 15자 이내의 짧은 이름 (예: "김정은 당일치기 방중", "가상자산 과세 또 유예")
- ids: 이 이슈에 해당하는 글 번호 전부 (사설+칼럼)
- 설명은 쓰지 말고 아래 형식의 JSON 배열 하나만 출력해:
[{{"issue": "...", "ids": [0, 5]}}]

[사설]
{fmt(eds, 80)}

[칼럼]
{fmt(cols, 60)}"""
    groups = parse_json(claude(prompt, key, model, max_tokens=1500))
    out = []
    for g in groups[:7]:
        ids = [int(i) for i in g.get("ids", []) if str(i).isdigit() and int(i) < len(items)]
        if not ids:
            continue
        it = {"issue": str(g.get("issue", ""))[:40], "summary": "", "explain": "", "stances": [],
              "ids": ids, "papers": len({items[i]["press"] for i in ids})}
        arts = "\n".join(f'- ({items[i]["press"]}, {items[i]["kind"]}) {items[i]["title"]} — {items[i]["description"][:160]}'
                         for i in ids[:20])
        try:
            d = parse_json(claude(f"""오늘 한국 신문들이 '{it["issue"]}' 사안을 다룬 사설·칼럼이야.

{arts}

아래 JSON 객체 하나만 출력해:
{{"summary": "이 사안이 무엇인지 1~2문장, 중립적으로",
 "explain": "처음 보는 사람을 위한 배경 설명 3문장 (왜 쟁점인지, 무엇이 걸려 있는지). 확실하지 않은 수치는 쓰지 마",
 "stances": [{{"press": "신문 이름", "view": "이 신문이 독자에게 던지는 메시지(주장·요구·경고) 한 줄, 위 제목·요약에 드러난 것만"}}]}}""",
                                  key, model, max_tokens=1500))
            it["summary"] = str(d.get("summary", ""))
            it["explain"] = str(d.get("explain", ""))
            it["stances"] = [{"press": str(x.get("press", "")), "view": str(x.get("view", ""))}
                             for x in d.get("stances", []) if isinstance(x, dict)][:15]
        except Exception as e:
            print(f"  '{it['issue']}' 설명 실패: {e}")
            AI.setdefault("errors", []).append(f"'{it['issue']}' 설명 실패: {str(e)[:150]}")
        out.append(it)
    out.sort(key=lambda x: -x["papers"])
    return out


JOSA = sorted(["으로부터", "에서는", "에게서", "으로는", "이라는", "라는", "에서", "에게", "으로", "까지",
               "부터", "보다", "처럼", "마저", "조차", "이나", "이며", "이고", "하고", "과의", "와의",
               "의", "이", "가", "은", "는", "을", "를", "에", "와", "과", "도", "만", "로", "나", "냐"],
              key=len, reverse=True)
STOP = set("""지난 따라 같은 것은 것이 말이다 정치적 경제적 사회적 이런 그런 어떤 모든 우리의 지금의 위한 통해 대해 대한 아니라 함께 앞으로 여전히 무엇을 누구를 얼마나 사람 사람들 시작 이유
사설 칼럼 시론 기고 논단 기자수첩 데스크 오피니언 정부 우리 국민 대한 위해 위한 문제 필요
해야 한다 한다면 이번 지금 오늘 내일 어제 이제 다시 결국 모두 누구 무엇 어떻게 무슨 이유 때문 대해 관련
없다 있다 된다 않다 아니다 하라 말라 해선 안된다 안 못 왜 더 또 수 것 등 및 그 이 저 새 각 전 후 중
年 月 日 vs 시대 사회 나라 한국 대한민국""".split())


def tokens(text):
    out = set()
    for w in re.findall(r"[가-힣A-Za-z0-9·]+", TAG_RE.sub("", text)):
        for j in ([] if w in ("김정은", "김여정") else JOSA):
            if w.endswith(j) and len(w) - len(j) >= 2:
                w = w[: -len(j)]
                break
        if (len(w) >= 2 and w not in STOP and not w.isdigit()
                and not re.fullmatch(r"\d+(일|월|년|%|명|원|개|건|세|위|차|배|조|억|만)?", w)
                and not re.search(r"(했다|한다|이다|된다|있다|없다|말이다|하라|해야|겠다)$", w)):
            out.add(w)
    return out


def issues_by_frequency(items):
    """AI 없이: 여러 신문 제목에 함께 나온 단어로 이슈 추정"""
    press_of = defaultdict(set)
    ids_of = defaultdict(list)
    for i, a in enumerate(items):
        for t in tokens(a["title"]):
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


# ─────────────────────────────── 6. 단어 뽑기 · 이슈 랭킹 ───────────────────────────────
#
# 의미 있는 단어: 한국어 형태소 분석기(Kiwi)로 '명사'만 뽑음 → '먼저'(부사), '던진'(동사) 같은 말은 처음부터 안 잡힘
# 오늘의 말: 지난 7일보다 '유난히 많이 나온' 명사만 → '지역', '책임'처럼 매일 나오는 말은 자연히 빠짐

KEEP_WORDS = ["국민의힘", "더불어민주당", "조국혁신당", "개혁신당", "대통령실", "기준금리", "가상자산",
              "부동산", "반도체", "인공지능", "김정은", "트럼프", "시진핑", "푸틴", "네타냐후", "젤렌스키"]
GENERIC = set("""만에 보도 엄중 여사 지역 책임 문제 상황 이유 가능성 계획 관계자 오늘 내일 어제 올해 내년 이번 지난해 최근 관련 대상 결과
방안 필요 확대 강화 추진 논란 우려 입장 발표 지적 강조 전망 예정 경우 수준 이상 이하 사실 시간 오전 오후 현지시간
기자 뉴스 사진 영상 속보 단독 종합 포토 등 것 때 중 전 후 뒤 앞 측 씨 명 개 건 곳 만 억 조 원 일 월 년 주 날
시대 사회 나라 미래 위기 변화 시작 과정 의미 생각 모습 이야기 사람 사람들 우리 자신 누구 무엇 하나 모두 가운데
가지 정도 부분 내용 의견 질문 답 말 글 칼럼 사설 시론 기고 기자수첩 데스크 오피니언 논단 기사 독자 인터뷰 단신
첫 새 최대 최고 최저 역대 사상 직접 이날 당시 현재 향후 이후 이전 동안 기간 마련 진행 개최 참석 방문 확인 제기
해결 대응 지원 운영 시행 도입 검토 협의 논의 합의 요구 촉구 비판 반발 주장 설명 언급 공개 계획 목표 성과""".split())
_KIWI = None


def kiwi():
    """Kiwi 형태소 분석기 (GitHub Actions에서는 없으면 자동 설치). 실패하면 None → 간이 방식"""
    global _KIWI
    if _KIWI is None:
        _KIWI = False
        try:
            try:
                from kiwipiepy import Kiwi
            except ImportError:
                if not os.environ.get("GITHUB_ACTIONS"):
                    raise
                print("형태소 분석기(Kiwi) 설치 중…")
                subprocess.run([sys.executable, "-m", "pip", "install", "-q", "kiwipiepy"], check=False)
                importlib.invalidate_caches()
                from kiwipiepy import Kiwi
            k = Kiwi()
            for w in KEEP_WORDS:
                try:
                    k.add_user_word(w, "NNP")
                except Exception:
                    pass
            _KIWI = k
            print("형태소 분석기(Kiwi) 사용")
        except Exception as e:
            print(f"Kiwi 없이 간이 방식으로 단어를 뽑아요: {e}")
    return _KIWI or None


def _ok_word(w):
    if len(w) < 2 or w in GENERIC or w in STOP:
        return False
    if re.fullmatch(r"[\d.,]+(일|월|년|%|명|원|개|건|세|위|차|배|조|억|만|분기|시|분)?", w):
        return False
    if re.fullmatch(r"[a-z]+", w):          # 소문자 영어 조각은 버림 (AI, GDP 같은 대문자 약어는 살림)
        return False
    return True


_NOUN_CACHE = {}


def nouns(text):
    """제목 → 의미 있는 명사 목록 (붙어 있는 명사는 합침: 가상+자산 → 가상자산)"""
    text = TAG_RE.sub("", text or "")
    if text in _NOUN_CACHE:
        return _NOUN_CACHE[text]
    out = []
    k = kiwi()
    if k:
        try:
            cur, end = "", -1
            for t in k.tokenize(text):
                if t.tag in ("NNG", "NNP", "SL", "SH"):
                    if cur and t.start == end:
                        cur += t.form
                    else:
                        if cur:
                            out.append(cur)
                        cur = t.form
                    end = t.start + t.len
                else:
                    if cur:
                        out.append(cur)
                    cur, end = "", -1
            if cur:
                out.append(cur)
        except Exception:
            out = list(tokens(text))
    else:
        for w in KEEP_WORDS:
            if w in text:
                out.append(w)
                text = text.replace(w, " ")
        out += [w for w in tokens(text) if not re.search(r"(던|진|한|된|할|인|는|운|런|른|던가|하며|하고|해도)$", w)]
    res = [w for w in dict.fromkeys(out) if _ok_word(w)]
    _NOUN_CACHE[text] = res
    return res


def trending_terms(today_titles, past_title_lists, top=40, min_count=3):
    """오늘 제목에서 지난 며칠보다 '유난히 많이 나온' 명사"""
    df = Counter()
    for t in today_titles:
        df.update(set(nouns(t)))
    n = max(1, len(today_titles))
    bg, m = Counter(), 0
    for titles in past_title_lists:
        for t in titles:
            bg.update(set(nouns(t)))
        m += len(titles)
    rows = []
    for w, c in df.items():
        if c < min_count:
            continue
        if m:
            ratio = ((c + 0.5) / (n + 1)) / ((bg[w] + 0.5) / (m + 1))
            if ratio < 1.3:
                continue
            score = c * math.log2(ratio)
        else:
            ratio, score = None, c
        rows.append({"term": w, "count": c, "ratio": None if ratio is None else round(ratio, 1), "score": round(score, 2)})
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:top]


# 실적·시황처럼 매일 반복되는 정형 기사에 늘 나오는 말: 묶는 기준에서 약하게(0.3) 침
WEAK = set("""분기 순익 순이익 영업이익 영업익 매출 매출액 실적 누적 잠정 전년 전년비 동기 대비 증가 감소 개선 부진 흑자 적자 전환 기록 최대 최저 역대
마감 장마감 개장 출발 하락 상승 반등 급등 급락 약세 강세 보합 순매수 순매도 매수 매도 거래 거래량 종가 지수 외국인 기관 개인 소폭 혼조 연속 대통령 李대통령 이대통령 발언 국무회의 회의 언급
분기 순익 순이익""".split())
# 제목에 이런 말이 있으면 정형 기사 → 회사 이름 같은 고유한 말이 겹쳐야만 묶음
ROUTINE_RE = re.compile(r"분기|순익|순이익|영업이익|영업익|매출|실적|잠정|마감|장마감|개장|출발|종가")


def cluster_titles(items, min_press=3, limit=15, common_share=0.08):
    """같은 사안을 다룬 기사끼리 묶기 → 여러 언론사가 다룬 순서로 정렬"""
    toks = [set(nouns(a["title"])) for a in items]
    n = len(items)
    df = Counter(w for T in toks for w in T)
    common = {w for w, c in df.items() if c > max(30, n * common_share)}
    weak = common | WEAK
    weight = lambda w: 0.3 if w in weak else 1.0
    clusters, index = [], defaultdict(set)
    for i in sorted(range(n), key=lambda i: items[i].get("published", "")):
        T = toks[i]
        if len(T) < 2:
            continue
        cand = set().union(*(index[w] for w in T)) if T else set()
        routine = bool(ROUTINE_RE.search(items[i]["title"]))
        best, bs = None, 0.0
        for ci in cand:
            c = clusters[ci]
            ov = T & c["core"]
            strong = [w for w in ov if w not in weak]
            # 정형 기사: 회사 이름처럼 고유한 말이 하나 이상 겹쳐야 함 (그러면 1.5점부터 묶음)
            if routine and not strong:
                continue
            sc = sum(weight(w) for w in ov)
            if sc > bs:
                best, bs = ci, sc
        if best is not None and bs >= (1.5 if routine else 2):
            c = clusters[best]
            c["members"].append(i)
            c["cnt"].update(T)
            need = max(1, math.ceil(len(c["members"]) * 0.4))
            c["core"] = {w for w, k in c["cnt"].items() if k >= need} or c["core"]
            for w in c["core"]:
                index[w].add(best)
        else:
            clusters.append({"members": [i], "cnt": Counter(T), "core": set(T)})
            for w in T:
                index[w].add(len(clusters) - 1)
    out = []
    for c in clusters:
        press = {items[i]["press"] for i in c["members"]}
        if len(press) < min_press:
            continue
        terms = [w for w, _ in c["cnt"].most_common() if w not in weak][:5] or [w for w, _ in c["cnt"].most_common(3)]
        rep = max(c["members"], key=lambda i: (len(toks[i] & set(terms[:3])), items[i]["press"] in PRESS_MAP.values()))
        out.append({"members": c["members"], "terms": terms, "press": len(press), "rep": rep})
    out.sort(key=lambda c: (c["press"], len(c["members"])), reverse=True)
    # 같은 사안이 둘로 나뉜 경우 합치기 (핵심어 5개 중 2개 이상 겹치면)
    merged = []
    for c in out:
        tgt = next((m for m in merged if len(set(m["terms"][:5]) & set(c["terms"][:5])) >= 3), None)
        if tgt:
            tgt["members"] = list(dict.fromkeys(tgt["members"] + c["members"]))
            tgt["press"] = len({items[i]["press"] for i in tgt["members"]})
        else:
            merged.append(c)
    merged.sort(key=lambda c: (c["press"], len(c["members"])), reverse=True)
    return merged[:limit]


def keyness(today_titles, past_title_lists):
    """단어 → '오늘이 평소의 몇 배인가' 함수"""
    df = Counter(w for t in today_titles for w in set(nouns(t)))
    bg = Counter(w for ts in past_title_lists for t in ts for w in set(nouns(t)))
    n, m = max(1, len(today_titles)), sum(len(ts) for ts in past_title_lists)
    if not m:
        return lambda w: 10.0
    return lambda w: ((df[w] + 0.5) / (n + 1)) / ((bg[w] + 0.5) / (m + 1))


VAGUE = set("시급 심각 강화 필요 대책 촉구 우려 해야 빼고 전조 합계 기억 탄력 증명 계기 과제 경고 교훈 대비 신호 숙제 의미 고민 지적 주문 당부".split())


def issues_by_cluster(items, ratio, rank=None):
    """
    AI 없이 사설 이슈 묶기.
    - 이슈는 '사설'끼리 묶어서 정함 (칼럼·기고는 주제가 넓어서 나중에 붙이기만 함)
    - 'AI', '미래'처럼 늘 나오는 말로만 묶인 건 이슈가 아님 → 핵심어 중 하나는 평소보다 2배 이상 많이 나온 말이어야 함
    """
    eds_idx = [i for i, a in enumerate(items) if a.get("kind") == "사설"]
    eds = [items[i] for i in eds_idx]
    out = []
    for c in cluster_titles(eds, min_press=2, limit=25, common_share=0.2):
        spec = [w for w in c["terms"][:4] if ratio(w) >= 2]
        if not spec:
            continue
        terms = spec + [w for w in c["terms"][:4] if w not in spec]
        ids = [eds_idx[i] for i in c["members"]]
        key3 = set(terms[:3])
        for j, a in enumerate(items):           # 같은 사안을 다룬 칼럼·기고 붙이기
            if a.get("kind") != "사설" and len(key3 & set(nouns(a["title"]))) >= min(2, len(key3)):
                ids.append(j)
        news = None
        for r in rank or []:
            if len(set(r["terms"][:4]) & key3) >= min(2, len(key3)) or (spec[0] in r["terms"][:3] and ratio(spec[0]) >= 5):
                news = {"id": r["id"], "press": r["press"], "count": r["count"]}
                break
        rep_title = TAG_RE.sub("", eds[c["rep"]]["title"])
        name = [w for w in terms if w not in VAGUE][:3]
        name.sort(key=lambda w: rep_title.find(w) if w in rep_title else 99)
        out.append({"issue": " ".join(name[:3]) or " · ".join(terms[:2]), "summary": eds[c["rep"]]["title"], "stances": [],
                    "ids": ids, "papers": c["press"], "news": news})
    # 같은 사안이 둘로 나뉜 경우 합치기: 묶음 안 제목들에서 많이 나온 말 8개 중 2개 이상 겹치면 같은 사안
    def top8(x):
        c = Counter(w for i in x["ids"] if items[i].get("kind") == "사설" for w in nouns(items[i]["title"]) if w not in VAGUE)
        return {w for w, _ in c.most_common(8)}
    merged = []
    for x in sorted(out, key=lambda x: x["papers"], reverse=True):
        t = top8(x)
        tgt = next((m for m in merged if len(m["_t"] & t) >= 2), None)
        if tgt:
            tgt["ids"] = list(dict.fromkeys(tgt["ids"] + x["ids"]))
            tgt["papers"] = len({items[i]["press"] for i in tgt["ids"] if items[i].get("kind") == "사설"})
            tgt["news"] = tgt["news"] or x["news"]
            tgt["_t"] |= t
        else:
            x["_t"] = t
            merged.append(x)
    for x in merged:
        x.pop("_t", None)
    merged.sort(key=lambda x: (x["papers"], (x["news"] or {}).get("press", 0)), reverse=True)
    return merged[:10]


# 넓게 모으는 기사: 이슈 랭킹(정치·경제·세계)용 + 사이트 방문자가 각자 정한 키워드를 찾을 수 있게 다른 분야도
SECTION_SEEDS = {"정치": ["대통령", "국회", "여야", "정부"], "경제": ["경제", "금리", "증시", "부동산", "기업", "수출"],
                 "세계": ["미국", "중국", "일본", "트럼프", "외신"],
                 "사회": ["경찰", "검찰", "법원", "교육", "노동", "의료"], "IT/과학": ["AI", "반도체", "과학", "플랫폼"],
                 "생활/문화": ["문화", "건강", "여행", "공연", "전시", "영화", "음식", "축제", "날씨", "교통", "복지"]}
POOL_SECTIONS = ("정치", "경제", "세계", "사회", "IT/과학", "생활/문화")
RANK_SECTIONS = ("정치", "경제", "세계", "사회", "IT/과학", "생활/문화")


def compact(a):
    return {"t": a["title"], "u": a["url"], "p": a["press"], "s": a["section"], "d": a["published"]}


def fetch_pool(since, cid, secret):
    """이슈 랭킹과 방문자 키워드 찾기용으로 기사를 넓게 모음 (제목 위주)"""
    pool = {}
    for sec, qs in SECTION_SEEDS.items():
        for q in qs:
            try:
                arts = search_recent(q, since, cid, secret, max_pages=5)
            except Exception as e:
                print(f"  {q}: 실패 - {e}")
                continue
            for a in arts:
                if a["section"] in POOL_SECTIONS and not opinion_kind(a["title"], a["_link"]) and not PHOTO_RE.match(a["title"]):
                    pool[a["url"]] = compact(a)
    return pool


def title_hits(titles, terms, need=2):
    need = min(need, len(terms))
    return sum(1 for t in titles if sum(1 for w in terms if w in t) >= need)


def build_issue_rank(pool_items, foreign, past_days, ai_key, ai_model):
    items = [{"title": x["t"], "url": x["u"], "press": x["p"], "section": x["s"], "published": x["d"]}
             for x in pool_items if x["s"] in RANK_SECTIONS]
    fmap = {a["url"]: a.get("outlets", []) for a in foreign}
    out = []
    for c in cluster_titles(items, min_press=3, limit=30)[:25]:
        mem = sorted((items[i] for i in c["members"]), key=lambda a: a["published"], reverse=True)
        secs = Counter(a["section"] for a in mem)
        terms = c["terms"][:4]
        hist = [{"date": dt, "count": title_hits(titles, terms[:3])} for dt, titles in past_days]
        oc = Counter(o for a in mem for o in fmap.get(a["url"], []))
        seen, arts = set(), []
        for a in mem:
            if a["press"] in seen:
                continue
            seen.add(a["press"])
            arts.append({"title": a["title"], "url": a["url"], "press": a["press"], "published": a["published"]})
        rep = items[c["rep"]]
        out.append({
            "id": hashlib.md5(" ".join(sorted(terms[:3])).encode()).hexdigest()[:10],
            "label": " · ".join(terms[:3]), "terms": terms,
            # 실적·시황 기사가 절반 이상이면 회사가 IT 기업이어도 '경제'로 (네이버 섹션은 언론사마다 달라서)
            "section": "경제" if sum(1 for a in mem if ROUTINE_RE.search(a["title"])) * 2 >= len(mem) else secs.most_common(1)[0][0],
            "press": c["press"], "count": len(mem), "title": rep["title"], "url": rep["url"],
            "articles": arts[:12], "outlets": oc.most_common(6), "history": hist,
            "new": bool(hist) and hist[-1]["count"] == 0, "summary": "", "_mem": mem,
        })
    if ai_key and out:
        out = review_issue_clusters(out, fmap, ai_key, ai_model)  # AI 검수: 섞인 기사·모음·홍보성 소식 걸러내기
    if ai_key and out:
        for b in range(0, len(out), 5):
            chunk = list(enumerate(out))[b:b + 5]
            try:
                lines = "\n".join(f'{i}. [{x["section"]}] 핵심어: {x["label"]}{" / 중심 사건: " + x["event"] if x.get("event") else ""} / 대표 제목: {x["title"]} / 다른 제목: '
                                  + " | ".join(a["title"] for a in x["articles"][1:4]) for i, x in chunk)
                res = parse_json(claude(
                    "오늘 한국 뉴스에서 여러 언론사가 함께 다룬 사안들이야. 각 사안에 대해 "
                    "label(15자 이내의 짧은 이슈 이름)과 summary(무슨 일인지 1문장, 중립적으로, 제목에 드러난 사실만)를 만들어줘.\n"
                    '출력은 JSON 객체 하나만: {"번호": {"label": "...", "summary": "..."}, ...}\n\n' + lines,
                    ai_key, ai_model, max_tokens=1200))
                for i, x in chunk:
                    r = res.get(str(i)) or {}
                    x["label"] = str(r.get("label") or x["label"])[:30]
                    x["summary"] = str(r.get("summary") or "")
            except Exception as e:
                print(f"  이슈 이름 붙이기(AI) 실패: {e}")
                AI.setdefault("errors", []).append(f"이슈 랭킹 이름 붙이기 실패({b + 1}~{b + 5}위): {str(e)[:150]}")
        out = merge_same_issues(out, fmap, ai_key, ai_model)
    for x in out:
        x.pop("_mem", None)
        x.pop("_keep", None)
    return out


def refresh_issue(x, mem, fmap):
    """검수로 기사 목록이 바뀐 이슈의 언론사 수·기사 수·대표 기사·해외 인용·분야를 다시 계산"""
    mem = sorted(mem, key=lambda a: a["published"], reverse=True)
    seen, arts = set(), []
    for a in mem:
        if a["press"] not in seen:
            seen.add(a["press"])
            arts.append({"title": a["title"], "url": a["url"], "press": a["press"], "published": a["published"]})
    keep = x.get("_keep") or []
    rep_a = max(mem, key=lambda a: sum(1 for w in keep if w.lower() in a["title"].lower())) if keep else mem[0]
    secs = Counter(a["section"] for a in mem)
    x.update(_mem=mem, press=len(seen), count=len(mem), articles=arts[:12], title=rep_a["title"], url=rep_a["url"],
             outlets=Counter(o for a in mem for o in fmap.get(a["url"], [])).most_common(6),
             # 분야는 build_issue_rank와 같은 기준 (실적·시황 기사가 절반 이상이면 '경제')
             section="경제" if sum(1 for a in mem if ROUTINE_RE.search(a["title"])) * 2 >= len(mem) else secs.most_common(1)[0][0])
    return x


def review_issue_clusters(out, fmap, ai_key, ai_model, batch=4, sample=20):
    """AI 검수: 규칙이 묶은 이슈마다 기사 제목을 보여주고
    ① 중심 사건 ② 그 사건과 다른 기사 ③ 하나의 사건이라 보기 어려운 '모음' ④ 보도자료성 홍보 소식인지 판정 →
    다른 기사는 빼고, 모음·홍보는 랭킹에서 제외하고, 언론사 수로 다시 정렬.
    AI가 실패한 묶음은 그대로 둠 (수집은 멈추지 않음)."""
    kept, dropped = [], []
    for b in range(0, len(out), batch):
        chunk = list(enumerate(out))[b:b + batch]
        samples = {}
        for i, x in chunk:
            seen_t, seen_p, pick = set(), set(), []
            for pass_ in (0, 1):  # 먼저 언론사가 겹치지 않게, 모자라면 나머지에서
                for a in x["_mem"]:
                    t = re.sub(r"\s+", " ", a["title"]).strip()
                    if t in seen_t or (pass_ == 0 and a["press"] in seen_p):
                        continue
                    seen_t.add(t); seen_p.add(a["press"]); pick.append(t)
                    if len(pick) >= sample:
                        break
                if len(pick) >= sample:
                    break
            samples[i] = pick
        lines = []
        for i, x in chunk:
            lines.append(f"[묶음 {i}] 규칙이 뽑은 핵심어: {', '.join(x['terms'][:4])}")
            lines += [f"  {i}-{j}. {t}" for j, t in enumerate(samples[i])]
        try:
            res = parse_json(claude(
                "아래는 뉴스 제목의 단어가 겹친다는 규칙으로 묶은 '오늘의 이슈' 후보들이야. 단어만 겹치고 실제로는 다른 사건인 기사가 섞였을 수 있어. "
                "묶음마다 다음을 판정해줘.\n"
                "- event: 가장 많은 제목이 다룬 하나의 구체적 사건 (누가·무엇을, 20자 이내)\n"
                "- keep: 그 사건 제목에 실제로 들어 있는 고유한 단어 1~3개 (회사·인물·기관·지명·법안·고유 사건명). "
                "'실적', '환율', 'AI', '투자', '대통령'처럼 여러 사건에 두루 쓰는 일반어는 넣지 마.\n"
                "- off: 중심 사건과 다른 사건을 다룬 제목 번호 (예: 다른 회사의 실적, 다른 나라의 선거, 다른 발언)\n"
                "- kind: single(대부분 한 사건) / mixed(여러 사건이 섞였지만 중심 사건이 분명함) / "
                "none(여러 회사 실적 모음, 'AI 투자' 같은 넓은 주제처럼 하나의 사건이라 보기 어려움) / "
                "promo(기업·기관이 낸 보도자료를 여러 언론이 받아쓴 홍보성 소식: 창립 기념, 수상, 제품·서비스 출시 홍보, "
                "협약·후원, 사회공헌·ESG 성과 홍보 등. 단, 사회적 파장이 큰 정책·사건·대형 거래는 promo가 아님)\n"
                "- 확실하지 않은 제목은 off에 넣지 말고, 확실하지 않으면 kind는 single로 해.\n"
                '출력은 JSON 객체 하나만: {"묶음번호": {"event": "...", "keep": ["..."], "off": [번호], "kind": "single"}, ...}\n\n'
                + "\n".join(lines), ai_key, ai_model, max_tokens=1500))
        except Exception as e:
            print(f"  이슈 검수(AI) 실패: {e}")
            AI.setdefault("errors", []).append(f"이슈 랭킹 검수 실패({b + 1}~{b + batch}번 묶음): {str(e)[:150]}")
            kept += [x for _, x in chunk]
            continue
        for i, x in chunk:
            r = (res or {}).get(str(i)) if isinstance(res, dict) else None
            if not isinstance(r, dict):
                kept.append(x)
                continue
            kind = str(r.get("kind", "single")).strip().lower()
            if kind in ("none", "promo"):
                dropped.append(f"{', '.join(x['terms'][:3])}({'모음' if kind == 'none' else '홍보성'})")
                continue
            pick = samples[i]
            off_t = set()
            for j in r.get("off") or []:
                m = re.search(r"(\d+)$", str(j))
                if m and int(m.group(1)) < len(pick):
                    off_t.add(pick[int(m.group(1))])
            low = [t.lower() for t in pick]
            keep = [str(w).strip() for w in (r.get("keep") or []) if str(w).strip()]
            keep = [w for w in keep if len(w) >= 2 and any(w.lower() in t for t in low)][:3]
            norm = lambda t: re.sub(r"\s+", " ", t).strip()
            mem = [a for a in x["_mem"] if norm(a["title"]) not in off_t
                   and (not keep or any(w.lower() in a["title"].lower() for w in keep))]
            if len({a["press"] for a in mem}) < 3:
                dropped.append(f"{', '.join(x['terms'][:3])}(남은 기사 부족)")
                continue
            x["_keep"] = keep
            x["event"] = str(r.get("event") or "")[:40]
            if len(mem) != len(x["_mem"]):
                print(f"  검수: {', '.join(x['terms'][:3])} → 기사 {len(x['_mem'])}→{len(mem)}개 (중심: {x['event']})")
            kept.append(refresh_issue(x, mem, fmap))
    if dropped:
        print(f"  검수로 뺀 묶음 {len(dropped)}개: {' / '.join(dropped)}")
    kept.sort(key=lambda x: (x["press"], x["count"]), reverse=True)
    return kept


def merge_same_issues(out, fmap, ai_key, ai_model):
    """단어 묶기로는 갈라진 같은 사안 합치기 (예: '코스피 6000선 탈환'과 '코스피 6000선 회복')
    AI에게 이슈 이름·요약 목록을 한 번에 보여주고, 같은 사건인 번호끼리만 묶게 함. 실패하면 그대로 둠."""
    if len(out) < 2:
        return out
    try:
        lines = "\n".join(f'{i}. [{x["section"]}] {x["label"]} / {x["summary"] or x["title"]} / 대표 제목: {x["title"]}' for i, x in enumerate(out))
        res = parse_json(claude(
            "아래는 오늘 뉴스에서 여러 언론사가 함께 다룬 이슈 목록이야. 사실상 같은 사건·사안을 다룬 번호끼리 묶어줘.\n"
            "- 같은 사건을 다른 표현으로 쓴 것(예: '코스피 6000선 탈환'과 '코스피 6000선 회복')만 묶어.\n"
            "- 같은 날 같은 시장의 같은 움직임은 같은 사건이야 (예: '코스피 급등·사이드카 발동'과 '코스피 6000선 회복').\n"
            "- 같은 회사의 같은 발표를 다른 측면에서 쓴 것도 같은 사건이야 (예: '삼성전자 3분기 실적'과 '삼성전자 메모리 수요 확대' 전망).\n"
            "- 주제만 비슷한 다른 사건(예: 서로 다른 회사의 실적 발표, 서로 다른 나라의 선거)은 묶지 마.\n"
            "- 확실하지 않으면 묶지 마.\n"
            '출력은 JSON 객체 하나만: {"groups": [[1, 7]]} (묶을 것이 없으면 {"groups": []})\n\n' + lines,
            ai_key, ai_model, max_tokens=400))
        groups = res.get("groups") if isinstance(res, dict) else None
    except Exception as e:
        print(f"  같은 이슈 합치기(AI) 실패: {e}")
        AI.setdefault("errors", []).append(f"이슈 랭킹 중복 합치기 실패: {str(e)[:150]}")
        return out
    drop = set()
    for g in groups or []:
        if not isinstance(g, list):
            continue
        idx = sorted({int(i) for i in g if str(i).strip().isdigit() and 0 <= int(i) < len(out)} - drop)
        if len(idx) < 2:
            continue
        main = max(idx, key=lambda i: (out[i]["press"], out[i]["count"]))
        m = out[main]
        mem = list({a["url"]: a for i in idx for a in out[i]["_mem"]}.values())
        mem.sort(key=lambda a: a["published"], reverse=True)
        seen, arts = set(), []
        for a in mem:
            if a["press"] not in seen:
                seen.add(a["press"])
                arts.append({"title": a["title"], "url": a["url"], "press": a["press"], "published": a["published"]})
        m["_mem"], m["press"], m["count"], m["articles"] = mem, len(seen), len(mem), arts[:12]
        m["outlets"] = Counter(o for a in mem for o in fmap.get(a["url"], [])).most_common(6)
        m["terms"] = list(dict.fromkeys(m["terms"] + [w for i in idx if i != main for w in out[i]["terms"]]))[:6]
        drop.update(i for i in idx if i != main)
        print(f"  같은 이슈 합침: {' + '.join(out[i]['label'] for i in idx)}")
    out = [x for i, x in enumerate(out) if i not in drop]
    out.sort(key=lambda x: (x["press"], x["count"]), reverse=True)
    return out


def load_day(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def day_titles(day_json, pool_json=None):
    titles = []
    if pool_json:
        titles += [x["t"] for x in pool_json]
    for arr in (day_json or {}).get("keywords", {}).values():
        titles += [a["title"] for a in arr]
    titles += [a["title"] for a in (day_json or {}).get("foreign", [])]
    return list(dict.fromkeys(titles))


def merge_by_url(new, old):
    """같은 날 앞선 수집분과 합치기 (하루 여러 번 돌 때 기사가 쌓이도록)"""
    seen = {a["url"] for a in new}
    return new + [a for a in (old or []) if a["url"] not in seen]


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
                                        "sort_order": "desc", "limit": 16})
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


# 영역별 논문: 분야를 만들지 않아도 볼 수 있게, 영역마다 고정 영어 검색어로 하루 한 번 모음 (AI 안 씀)
AREA_PAPER_TERMS = {
    "IT/과학": ["artificial intelligence", "large language model", "semiconductor", "quantum computing", "robotics"],
    "경제": ["monetary policy", "inflation", "exchange rate", "labor market", "household debt"],
    "사회": ["population aging", "fertility decline", "income inequality", "public health", "education policy"],
    "정치": ["democracy", "elections", "political polarization", "public policy", "legislature"],
    "세계": ["international trade", "tariffs", "geopolitics", "economic sanctions", "climate policy"],
    "도시·지역": ["urban economics", "regional development", "housing policy", "agglomeration", "smart city"],
}


def fetch_area_papers(key, today):
    day = lambda n: (today - timedelta(days=n)).strftime("%Y-%m-%d")
    out = {}
    for area, terms in AREA_PAPER_TERMS.items():
        search = " OR ".join(f'"{t}"' if " " in t else t for t in terms)
        row = {"terms": terms, "recent": [], "cited": []}
        try:
            row["recent"] = [paper_row(w) for w in openalex_get({
                "search": search, "filter": f"from_publication_date:{day(30)},to_publication_date:{day(0)}",
                "sort": "publication_date:desc", "per-page": 8, "select": OPENALEX_SELECT}, key).get("results", [])]
            row["cited"] = [paper_row(w) for w in openalex_get({
                "search": search, "filter": f"from_publication_date:{day(365)}",
                "sort": "cited_by_count:desc", "per-page": 8, "select": OPENALEX_SELECT}, key).get("results", [])]
        except Exception as e:
            print(f"  영역 논문({area}) 실패: {e}")
            row["error"] = str(e)[:200]
        out[area] = row
        time.sleep(0.3)
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
        {**art("김정은, 단둥 방문 마치고 귀국…왕후닝 배웅", "https://www.yna.co.kr/view/5", "정치", 60,
               "김정은 북한 국무위원장이 24일 중국 랴오닝성 단둥 방문 일정을 마치고 북한으로 돌아갔다."),
         "outlets": ["신화"], "body_outlets": ["신화"], "en_query": "Kim Jong Un Dandong Wang Huning"},
    ]
    for a in foreign:
        a.setdefault("body_outlets", [])
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

START = time.time()


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)   # Actions 로그에 진행 상황이 바로바로 보이게
    except Exception:
        pass
    mock = "--mock" in sys.argv
    config = json.loads((ROOT / "keywords.json").read_text(encoding="utf-8"))
    fields = [f for f in config.get("fields", []) if f.get("name")]
    keywords = list(dict.fromkeys(config.get("keywords", []) + [k for f in fields for k in f.get("keywords", [])]))
    env = os.environ.get
    cid, secret = env("NAVER_CLIENT_ID", ""), env("NAVER_CLIENT_SECRET", "")
    ai_key, ai_model = env("ANTHROPIC_API_KEY", ""), config.get("ai_model", "claude-haiku-5-5")
    if env("CHATKU_API_KEY") and env("CHATKU_BASE_URL"):
        AI.update(provider="chatku", base=env("CHATKU_BASE_URL"))
        ai_key, ai_model = env("CHATKU_API_KEY"), env("CHATKU_MODEL") or "claude-sonnet-5"
        if env("CHATKU_PROBE"):          # 필요할 때만: Secrets에 CHATKU_PROBE=1 을 넣으면 길이 측정
            AI["probe"] = chatku_probe(ai_key, ai_model)
        print(f"AI 분석: 챗쿠 ({ai_model})")
    elif ai_key:
        print(f"AI 분석: Claude ({ai_model})")
    if not mock and not (cid and secret):
        sys.exit("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 없습니다.")

    now = datetime.now(KST)
    since = now - timedelta(hours=config.get("lookback_hours", 24))
    result = {"date": now.strftime("%Y-%m-%d"), "generated_at": now.isoformat(),
              "outlet_meta": {n: {"en": en, "domain": dom} for n, _, _, en, dom in FOREIGN},
              "config": {"keywords": config.get("keywords", []), "fields": fields, "stopwords": config.get("stopwords", []),
                         "lookback_hours": config.get("lookback_hours", 24), "keep_days": config.get("keep_days", 30),
                         "chatku_base": config.get("chatku_base", "")},
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
        except Exception as e:
            print(f"해외 인용: 실패 - {e}")
            result["foreign"] = []

        # 3) 사설·칼럼 + 이슈
        print("사설·칼럼 수집")
        try:
            op = fetch_opinion(since, keywords, cid, secret)
            result["opinion"] = op
            print(f"  사설 {sum(a['kind'] == '사설' for a in op)}건, 칼럼 {sum(a['kind'] == '칼럼' for a in op)}건")
            result["issues"] = None  # 아래에서 같은 날 앞선 수집분과 합친 뒤 계산
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
        # 5) 같은 날 앞선 수집분과 합치기 (하루 여러 번 돌아도 기사가 쌓이도록)
        prev = load_day(DATA_DIR / f"{result['date']}.json")
        if prev:
            for k in keywords:
                result["keywords"][k] = merge_by_url(result["keywords"].get(k, []), (prev.get("keywords") or {}).get(k))
            old_q = {a["url"]: a.get("en_query") for a in prev.get("foreign", []) if a.get("en_query")}
            result["foreign"] = merge_by_url(result["foreign"], prev.get("foreign"))
            for a in result["foreign"]:
                if not a.get("en_query") and a["url"] in old_q:
                    a["en_query"] = old_q[a["url"]]
            result["opinion"] = sorted(merge_by_url(result["opinion"], prev.get("opinion")),
                                       key=lambda a: a["published"], reverse=True)
            for c in ("kr", "us"):
                if not result["indicators"][c]:
                    result["indicators"][c] = (prev.get("indicators") or {}).get(c, [])
            print("오늘 앞선 수집분과 합쳤어요")
        if ai_key and result["foreign"]:
            print("해외 원문 검색어 만들기 (AI)")
            add_original_queries(result["foreign"], ai_key, ai_model)

        # 7) 이슈 랭킹용 기사 모으기 (정치·경제·세계)
        print("이슈 랭킹용 기사 수집")
        pool_path = DATA_DIR / f"pool-{result['date']}.json"
        pool = fetch_pool(since, cid, secret)
        for arr in list(result["keywords"].values()) + [result["foreign"]]:
            for a in arr:
                if a["section"] in RANK_SECTIONS:
                    pool.setdefault(a["url"], compact(a))
        for x in (load_day(pool_path) or []):
            pool.setdefault(x["u"], x)
        pool_items = sorted(pool.values(), key=lambda x: x["d"], reverse=True)
        print(f"  {len(pool_items)}건")

        # 8) 지난 7일과 비교한 '오늘의 말' + 이슈 랭킹
        past = []
        for i in range(7, 0, -1):
            dt = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            dj = load_day(DATA_DIR / f"{dt}.json")
            if dj:
                past.append((dt, day_titles(dj, load_day(DATA_DIR / f"pool-{dt}.json"))))
        today_titles = list(dict.fromkeys([x["t"] for x in pool_items] + day_titles(result)))
        result["trending"] = trending_terms(today_titles, [t for _, t in past])
        result["issue_rank"] = build_issue_rank(pool_items, result["foreign"], past, ai_key, ai_model)

        # 9) 사설 이슈 (구체적인 사안만, 오늘 기사 랭킹과 연결)
        op = result["opinion"]
        issues = None
        if ai_key and op:
            try:
                issues = {"method": "ai", "items": issues_by_ai(op, ai_key, ai_model)}
                ratio = keyness(today_titles + [a["title"] for a in op], [t for _, t in past])
                for it in issues["items"]:
                    words = [w for w in nouns(it["issue"] + " " + it["summary"]) if ratio(w) >= 2][:4]
                    r = next((r for r in result["issue_rank"] if len(set(r["terms"][:4]) & set(words)) >= 2), None)
                    it["news"] = r and {"id": r["id"], "press": r["press"], "count": r["count"]}
            except Exception as e:
                print(f"  AI 이슈 분석 실패, 기사 묶기로 대체: {e}")
                result["ai_error"] = f"사설 이슈 AI 분석 실패: {str(e)[:300]}"
        if not issues:
            ratio = keyness(today_titles + [a["title"] for a in op], [t for _, t in past])
            issues = {"method": "cluster", "items": issues_by_cluster(op, ratio, result["issue_rank"])}
        result["issues"] = issues
        if ai_key:
            result["ai_status"] = {"provider": AI["provider"], "model": ai_model,
                                   "errors": ([result["ai_error"]] if result.get("ai_error") else []) + AI.get("errors", []),
                                   "probe": AI.get("probe")}
        print(f"오늘의 말 {len(result['trending'])}개, 이슈 랭킹 {len(result['issue_rank'])}개")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        pool_path.write_text(json.dumps(pool_items, ensure_ascii=False), encoding="utf-8")

        # 영역별 논문: 같은 날 이미 모았으면 다시 부르지 않음 (하루 한 번)
        prev_day = load_day(DATA_DIR / f"{result['date']}.json") or {}
        if prev_day.get("area_papers") and not any(v.get("error") for v in prev_day["area_papers"].values()):
            result["area_papers"] = prev_day["area_papers"]
        else:
            print("영역별 논문 (OpenAlex)")
            result["area_papers"] = fetch_area_papers(env("OPENALEX_API_KEY", ""), now)

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
    for f in DATA_DIR.glob("pool-????-??-??.json"):
        if f.stem[5:] < (now - timedelta(days=10)).strftime("%Y-%m-%d"):
            f.unlink()
    (DATA_DIR / "index.json").write_text(
        json.dumps({"dates": sorted(dates, reverse=True)}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
