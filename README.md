# 오늘의 키워드 뉴스

매일 아침 7시(한국시간)에 `keywords.json`의 키워드로 네이버 뉴스를 수집하고, 정치·경제 등 섹션별로 분류해 웹페이지에 보여줍니다.

## 파일 구성
- `keywords.json` — 받아볼 키워드 목록 (여기만 고치면 됨)
- `fetch_news.py` — 기사 수집·분류 스크립트
- `docs/index.html` — 웹페이지
- `.github/workflows/daily-news.yml` — 매일 자동 실행 설정

## 설정 순서 (처음 한 번만)
1. **GitHub 저장소 만들기**: github.com → New repository → 이름 입력(예: `news`) → Public → Create
2. **파일 올리기**: 저장소 화면의 "uploading an existing file" → 압축 푼 폴더 안의 파일·폴더 전부 끌어다 놓기 → Commit
   (`.github` 폴더가 숨김 폴더라 안 보이면, 저장소에서 Add file → Create new file → 이름에 `.github/workflows/daily-news.yml` 입력 후 내용 붙여넣기)
3. **API 키 등록**: Settings → Secrets and variables → Actions → New repository secret
   - `NAVER_CLIENT_ID` = 네이버 API HUB의 Client ID
   - `NAVER_CLIENT_SECRET` = Client Secret
4. **사이트 켜기**: Settings → Pages → Source: Deploy from a branch → Branch: `main`, 폴더: `/docs` → Save
5. **첫 수집 실행**: Actions 탭 → "매일 뉴스 수집" → Run workflow
6. 1~2분 뒤 `https://<내아이디>.github.io/<저장소이름>/` 접속

## 키워드 바꾸기
GitHub에서 `keywords.json`을 열고 연필 아이콘으로 수정 → Commit. 다음 실행부터 반영돼요 (바로 보려면 5번처럼 수동 실행).

- `lookback_hours`: 최근 몇 시간 기사를 모을지 (기본 24)
- `keep_days`: 며칠치 기록을 남길지 (기본 30)

## 분류 방식
1. 네이버 뉴스 링크의 섹션 번호(`sid`)로 분류 — 네이버 화면과 동일
2. 네이버 링크가 없는 기사는 `fetch_news.py`의 `RULES` 단어 목록으로 분류 (단어 추가 가능)
3. 둘 다 안 걸리면 "기타"

## 내 컴퓨터에서 테스트
```
python fetch_news.py --mock          # API 키 없이 가짜 데이터 생성
cd docs && python -m http.server     # http://localhost:8000 접속
```
