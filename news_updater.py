"""
T-HUB Korea 뉴스 자동 업데이트 스크립트
- 인도 경제 뉴스 + T-HUB 뉴스 검색
- 제목 한국어 자동 번역
- news.json 업데이트
"""

import os
import json
import requests
from datetime import datetime, timezone
from pathlib import Path

try:
    from deep_translator import GoogleTranslator
    HAS_TRANSLATOR = True
except ImportError:
    HAS_TRANSLATOR = False

NEWS_API_KEY = os.environ.get('NEWS_API_KEY')
NEWS_FILE    = Path(__file__).parent / 'news.json'
MAX_PER_CAT  = 8

QUERIES = {
    'india': '"India economy" OR "Indian economy" OR "India GDP" OR "India growth"',
    'thub':  '"T-HUB" Hyderabad',
}

# 관련성 필터: 제목에 이 단어 중 하나라도 있어야 통과
FILTERS = {
    'india': ['india', 'indian', 'modi', 'mumbai', 'delhi', 'bangalore', 'bengaluru',
              'hyderabad', 'rupee', 'sensex', 'nifty', 'rbi'],
    'thub':  ['t-hub', 'thub', 'hyderabad', 'telangana'],
}


def translate(text):
    if not HAS_TRANSLATOR or not text:
        return text
    try:
        return GoogleTranslator(source='en', target='ko').translate(text[:450])
    except Exception:
        return text


def fetch(query, category):
    params = {
        'q':        query,
        'language': 'en',
        'sortBy':   'publishedAt',
        'pageSize': 15,
        'apiKey':   NEWS_API_KEY,
    }
    try:
        resp = requests.get('https://newsapi.org/v2/everything', params=params, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f'  API 오류 [{category}]: {e}')
        return []

    keywords = FILTERS.get(category, [])
    results = []
    for item in resp.json().get('articles', []):
        title = item.get('title', '')
        url   = item.get('url', '')
        if not title or not url or title == '[Removed]':
            continue
        if keywords and not any(k in title.lower() for k in keywords):
            continue
        results.append({
            'title_ko': translate(title),
            'title_en': title,
            'url':      url,
            'source':   item.get('source', {}).get('name', ''),
            'date':     (item.get('publishedAt') or '')[:10],
            'category': category,
        })
        if len(results) >= MAX_PER_CAT:
            break
    return results


def main():
    if not NEWS_API_KEY:
        print('NEWS_API_KEY 환경변수가 없습니다.')
        return

    # 기존 데이터 로드
    if NEWS_FILE.exists():
        existing = json.loads(NEWS_FILE.read_text(encoding='utf-8'))
        seen_urls = {a['url'] for a in existing.get('articles', [])}
    else:
        existing  = {'articles': []}
        seen_urls = set()

    # 새 기사 검색
    new_articles = []
    for category, query in QUERIES.items():
        print(f'검색 중: {category}')
        for art in fetch(query, category):
            if art['url'] not in seen_urls:
                new_articles.append(art)
                seen_urls.add(art['url'])

    if not new_articles:
        print('새 기사 없음 — news.json 유지')
        return

    # 병합: 새 기사 앞에, 카테고리별 최대 유지
    all_articles = new_articles + existing.get('articles', [])
    india = [a for a in all_articles if a['category'] == 'india'][:MAX_PER_CAT]
    thub  = [a for a in all_articles if a['category'] == 'thub'][:MAX_PER_CAT]

    output = {
        'last_updated': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        'articles':     india + thub,
    }

    NEWS_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    print(f'완료: 새 기사 {len(new_articles)}개 추가')


if __name__ == '__main__':
    main()
