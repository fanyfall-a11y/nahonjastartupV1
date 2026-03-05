import os
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from google import genai
import yagmail

# --- Configuration ---
KST = timezone(timedelta(hours=9))

ITEM_IDS = [x.strip() for x in os.getenv("ITEM_IDS", "").split(",") if x.strip()]
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
TO_EMAIL = os.getenv("GENERATE_TO_EMAIL")  # 블로그 초안 전용 수신 이메일

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "generate_log.txt"


# --- Logging ---
def log(message):
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{ts}] {message}"
    print(log_msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_msg + "\n")


def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def load_json_data(date_str):
    data_path = BASE_DIR / "docs" / "daily" / f"{date_str}.json"
    if not data_path.exists():
        log(f"Data file not found: {data_path}")
        return {}
    try:
        with open(data_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"Error loading JSON: {e}")
        return {}


def get_items_by_ids(data, target_ids):
    found_items = []
    id_set = set(target_ids)
    if not data or "sources" not in data:
        return found_items
    for source_data in data["sources"].values():
        for item in source_data.get("items", []):
            if item["id"] in id_set:
                found_items.append(item)
                if len(found_items) >= 10:
                    return found_items
    return found_items


def generate_content(prompt):
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return response.text
    except Exception as e:
        log(f"Gemini API Error: {e}")
        return None


# --- Main Process ---
def main():
    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    output_base = BASE_DIR / "output" / date_str

    log(f"=== 블로그 초안 생성 시작 ({date_str}) ===")
    log(f"처리 대상 IDs: {ITEM_IDS}")

    if not GEMINI_API_KEY:
        log("Error: GEMINI_API_KEY 없음")
        return

    if not ITEM_IDS:
        log("Error: ITEM_IDS 없음")
        return

    data = load_json_data(date_str)
    target_items = get_items_by_ids(data, ITEM_IDS)

    if not target_items:
        log("매칭된 항목 없음")
        return

    log(f"매칭 항목 {len(target_items)}건 처리 시작")

    email_results = []

    for item in target_items:
        try:
            title = item.get("title", "")
            org = item.get("org", "")
            region = item.get("region", "국내")
            url = item.get("url", "")
            detail = item.get("detail", {})
            period = detail.get("period", "")
            content = detail.get("content", "")
            amount = detail.get("amount", "")

            safe_title = sanitize_filename(title)
            folder_name = f"{region}_{safe_title[:40]}"
            item_dir = output_base / folder_name
            item_dir.mkdir(parents=True, exist_ok=True)

            context = f"""제목: {title}
주관기관: {org}
지역: {region}
신청기간: {period}
지원내용: {content}
지원금액: {amount}
상세링크: {url}"""

            naver = generate_content(
                f"다음 지원사업 정보로 네이버 블로그 포스팅을 작성해줘.\n"
                f"- 톤: 친근하고 따뜻하게. '안녕하세요 :)' 로 시작. 독자는 1인창업가·소상공인 대표님.\n"
                f"- 분량: 2000자 내외, 마크다운 형식\n"
                f"- 구성: 도입부(공감) → 사업개요 → 지원대상 → 지원내용·금액 → 신청방법·기간 → 마무리(신청 독려)\n"
                f"- 소제목마다 이모지 1개, 핵심 정보는 불릿으로 정리\n"
                f"- 네이버 검색 키워드(지원사업명, 창업지원, 정부지원금) 자연스럽게 포함\n\n{context}"
            )
            if naver:
                (item_dir / "01_네이버블로그.txt").write_text(naver, encoding="utf-8")

            tistory = generate_content(
                f"다음 지원사업 정보로 티스토리 블로그 포스팅을 작성해줘.\n"
                f"- 톤: 깔끔하고 정보 중심. 전문성 있게. 불필요한 감탄사 없이 핵심만.\n"
                f"- 분량: 1500자 내외, HTML 태그(h2, h3, ul, li, strong) 적극 활용\n"
                f"- 구성: 개요 → 지원대상 → 지원내용 → 신청방법 → 유의사항\n"
                f"- 표(table)로 핵심 정보 정리 포함\n"
                f"- 티스토리 SEO: 제목에 '2026 + 사업명 + 신청방법' 키워드 포함\n\n{context}"
            )
            if tistory:
                (item_dir / "02_티스토리.txt").write_text(tistory, encoding="utf-8")

            blogspot = generate_content(
                f"다음 지원사업 정보로 블로그스팟(Blogger) 포스팅을 한국어로 작성해줘.\n"
                f"- 톤: 간결하고 명확하게. 검색자가 원하는 정보를 빠르게 찾을 수 있게.\n"
                f"- 분량: 1500자 내외, HTML 태그(h2, h3, ul, li) 사용\n"
                f"- 구성: 한줄 요약 → 지원내용 → 신청자격 → 신청기간·방법 → 원문링크 안내\n"
                f"- 구글 검색 SEO: 제목과 첫 문단에 핵심 키워드(지원사업명+지원금액+신청대상) 집중 배치\n\n{context}"
            )
            if blogspot:
                (item_dir / "03_블로그스팟.txt").write_text(blogspot, encoding="utf-8")

            insta = generate_content(
                f"다음 지원사업 정보로 인스타그램 캡션을 작성해줘.\n"
                f"- 톤: 짧고 임팩트 있게. 첫 줄에 시선을 끄는 한 문장.\n"
                f"- 분량: 본문 300자 이내\n"
                f"- 이모지 적극 활용 (각 줄 앞에 관련 이모지)\n"
                f"- 지원금액·마감일·신청대상을 핵심만 간결하게\n"
                f"- 마지막에 '👉 링크는 프로필에서!' CTA 포함\n"
                f"- 마지막 줄: 관련 해시태그 10개 (창업지원, 정부지원금, 소상공인 등)\n\n{context}"
            )
            if insta:
                (item_dir / "04_인스타그램.txt").write_text(insta, encoding="utf-8")

            summary = f"제목: {title}\nURL: {url}\n주관기관: {org}\n신청기간: {period}\n지원금액: {amount}\n\n지원내용:\n{content}"
            (item_dir / "00_요약.txt").write_text(summary, encoding="utf-8")

            attachments = sorted(item_dir.iterdir())
            email_results.append({"title": title, "url": url, "dir": item_dir, "attachments": attachments})
            log(f"✅ 완료: {title[:40]}")

        except Exception as e:
            log(f"처리 실패 ({item.get('id')}): {e}")
            continue

    # 이메일 발송 - 항목별로 각각 발송 (첨부파일 포함)
    if GMAIL_USER and GMAIL_APP_PASSWORD and TO_EMAIL and email_results:
        try:
            yag = yagmail.SMTP(GMAIL_USER, GMAIL_APP_PASSWORD)
            for r in email_results:
                subject = f"📝 블로그 초안 - {r['title'][:40]}"
                body = f"제목: {r['title']}\n원문: {r['url']}\n\n첨부파일을 확인하세요."
                yag.send(TO_EMAIL, subject, body, attachments=[str(fp) for fp in r["attachments"]])
                log(f"✅ 이메일 발송: {r['title'][:40]}")
        except Exception as e:
            log(f"이메일 발송 실패: {e}")

    log(f"=== 종료: {len(email_results)}건 처리 완료 ===")


if __name__ == "__main__":
    main()
