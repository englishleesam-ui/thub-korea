# -*- coding: utf-8 -*-
"""
T-HUB Korea 뉴스 자동 업데이트 스크립트
- Google News RSS에서 인도 경제 / 인도 스타트업·T-Hub 뉴스를 수집
- Anthropic API(Claude)로 제목을 자연스러운 한국어로 번역
- 사이트가 읽는 news.json 형식으로 저장
- 수집 실패 시 기존 news.json을 보존하여 사이트가 비지 않도록 함
표준 라이브러리만 사용 (별도 설치 불필요)
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

KST = timezone(timedelta(hours=9))
NEWS_FILE = "news.json"
MAX_PER_CATEGORY = 6
USER_AGENT = "Mozilla/5.0 (compatible; THubKoreaNewsBot/1.0)"

RSS_QUERIES = {
    # 카테고리: [(검색어, 최근 N일), ...]  앞 쿼리에서 부족하면 뒤 쿼리로 보충
    "india": [
        ("India economy when:7d", 7),
        ("India business market when:7d", 7),
    ],
    "thub": [
        ('"T-Hub" Hyderabad when:30d', 30),
        ("India startup funding tech when:7d", 7),
    ],
}


def fetch_rss(query: str):
    """Google News RSS에서 기사 목록을 가져온다."""
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        xml_data = resp.read()

    root = ET.fromstring(xml_data)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        src_el = item.find("source")
        source = src_el.text.strip() if (src_el is not None and src_el.text) else ""

        if not title or not link:
            continue

        # Google News 제목 끝의 " - 매체명" 제거
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)].strip()

        try:
            dt = parsedate_to_datetime(pub).astimezone(KST)
            date_str = dt.strftime("%Y-%m-%d")
        except Exception:
            dt = datetime.now(KST)
            date_str = dt.strftime("%Y-%m-%d")

        items.append(
            {"title": title, "url": link, "source": source or "Google News",
             "date": date_str, "_dt": dt}
        )
    return items


def collect_category(category: str):
    """카테고리별로 기사를 수집하고 중복 제거 후 최신순 상위 N개 반환."""
    collected, seen = [], set()
    for query, _days in RSS_QUERIES[category]:
        if len(collected) >= MAX_PER_CATEGORY:
            break
        try:
            for it in fetch_rss(query):
                key = it["title"].lower()[:60]
                if key in seen:
                    continue
                seen.add(key)
                collected.append(it)
        except Exception as e:
            print(f"[경고] RSS 수집 실패 ({category} / {query}): {e}")

    collected.sort(key=lambda x: x["_dt"], reverse=True)
    return collected[:MAX_PER_CATEGORY]


def translate_titles(titles):
    """Anthropic API로 제목들을 한국어 뉴스 헤드라인으로 번역.
    실패하면 None을 반환하여 영어 원문을 그대로 쓰게 한다."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("[경고] ANTHROPIC_API_KEY가 없어 번역을 건너뜁니다 (영어 제목 사용).")
        return None

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    prompt = (
        "다음 영어 뉴스 헤드라인들을 자연스러운 한국어 뉴스 헤드라인으로 번역하세요.\n"
        "- 한국 경제 신문 헤드라인처럼 간결하게\n"
        "- 고유명사(T-Hub, KOTRA 등)는 원어 유지 가능\n"
        "- 반드시 번역문만 담은 JSON 문자열 배열로만 답하세요. 마크다운 금지.\n"
        f"- 배열 길이는 정확히 {len(titles)}개\n\n{numbered}"
    )
    body = json.dumps({
        "model": "claude-haiku-4-5",
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = "".join(b.get("text", "") for b in data.get("content", []))
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
        translated = json.loads(text)
        if isinstance(translated, list) and len(translated) == len(titles):
            return [str(t).strip() for t in translated]
        print("[경고] 번역 결과 형식이 맞지 않아 영어 제목을 사용합니다.")
    except Exception as e:
        print(f"[경고] 번역 실패: {e} — 영어 제목을 사용합니다.")
    return None


def load_existing():
    try:
        with open(NEWS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_updated": "", "articles": []}


def main():
    existing = load_existing()
    final_articles = []
    any_success = False

    for category in ("india", "thub"):
        items = collect_category(category)
        if items:
            any_success = True
            titles = [it["title"] for it in items]
            ko = translate_titles(titles)
            for i, it in enumerate(items):
                final_articles.append({
                    "category": category,
                    "title_ko": ko[i] if ko else it["title"],
                    "source": it["source"],
                    "date": it["date"],
                    "url": it["url"],
                })
            print(f"[완료] {category}: {len(items)}건 수집")
        else:
            # 수집 실패 시 기존 기사 유지 → 사이트가 비지 않음
            kept = [a for a in existing.get("articles", [])
                    if a.get("category") == category]
            final_articles.extend(kept)
            print(f"[유지] {category}: 새 기사 수집 실패, 기존 {len(kept)}건 유지")

    if not any_success and not final_articles:
        print("[중단] 수집된 기사가 전혀 없어 news.json을 변경하지 않습니다.")
        sys.exit(0)

    result = {
        "last_updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M (KST)"),
        "articles": final_articles,
    }
    with open(NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[저장] news.json 갱신 완료 — 총 {len(final_articles)}건")


if __name__ == "__main__":
    main()
