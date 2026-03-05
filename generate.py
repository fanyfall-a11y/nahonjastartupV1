import os
import json
import re
import html
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

from google import genai
import yagmail
from playwright.async_api import async_playwright

KST = timezone(timedelta(hours=9))

ITEM_IDS = [x.strip() for x in os.getenv("ITEM_IDS", "").split(",") if x.strip()]
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
TO_EMAIL = os.getenv("GENERATE_TO_EMAIL")

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "generate_log.txt"


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


async def html_to_image(html_content: str, output_path: str, page):
    await page.set_viewport_size({'width': 1080, 'height': 1350})
    await page.set_content(html_content, wait_until='networkidle')
    await page.screenshot(path=output_path, type='png', clip={'x': 0, 'y': 0, 'width': 1080, 'height': 1350})


def make_card1_html(title, region, ai_ment, deadline, today_str):
    words = title.split()
    mid = len(words) // 2
    line1 = " ".join(words[:mid]) if words else ""
    line2 = " ".join(words[mid:]) if len(words) > 1 else ""

    title_e = html.escape(title)
    region_e = html.escape(region)
    ai_ment_e = html.escape(ai_ment)
    today_str_e = html.escape(today_str)
    line1_e = html.escape(line1)
    line2_e = html.escape(line2)

    deadline_html = ""
    if deadline and deadline.strip():
        deadline_e = html.escape(deadline)
        deadline_html = f"""
        <div style='display:inline-block; background:rgba(255,200,0,0.2); border:2px solid rgba(255,200,0,0.55);
                    border-radius:50px; padding:14px 40px; color:white; font-size:28px; font-weight:bold; width:fit-content;'>
            ⏰ 마감 {deadline_e}
        </div>
        """

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'></head>
    <body style='margin:0;padding:0;'>
    <div style='width:1080px; height:1350px; position:relative; overflow:hidden; display:flex; flex-direction:column;
                font-family:Noto Sans KR, sans-serif; word-break:keep-all;
                background:linear-gradient(160deg, #0d2d6e 0%, #1a4fa0 40%, #0a1e4a 100%); color:white;'>
        <div style='position:absolute; width:600px; height:600px; top:-100px; right:-150px; border-radius:50%;
                    background:rgba(255,255,255,0.04);'></div>
        <div style='position:absolute; width:450px; height:450px; bottom:-80px; left:-120px; border-radius:50%;
                    background:rgba(255,255,255,0.04);'></div>
        <div style='padding:36px 60px; display:flex; justify-content:space-between; align-items:center; position:relative; z-index:10;'>
            <div style='font-size:28px; font-weight:900;'>🔷 나혼자창업</div>
            <div style='font-size:22px; opacity:0.6;'>{today_str_e}</div>
        </div>
        <div style='flex:1; display:flex; flex-direction:column; justify-content:center; gap:36px; padding:0 60px; position:relative; z-index:10;'>
            <div style='display:inline-block; background:rgba(255,255,255,0.15); border:1.5px solid rgba(255,255,255,0.35);
                        border-radius:50px; padding:10px 32px; color:white; font-size:26px; width:fit-content;'>
                📍 {region_e} 지원사업
            </div>
            <div>
                <div style='color:white; font-size:72px; font-weight:900; line-height:1.2; text-shadow:0 4px 20px rgba(0,0,0,0.4);'>{line1_e}</div>
                <div style='color:#7ec8ff; font-size:65px; font-weight:900; line-height:1.2; text-shadow:0 4px 20px rgba(0,0,0,0.4);'>{line2_e}</div>
            </div>
            <div style='background:rgba(255,255,255,0.12); border-left:5px solid #7ec8ff;
                        padding:22px 36px; border-radius:14px; color:white; font-size:30px; line-height:1.65; text-align:center;'>
                {ai_ment_e}
            </div>
            {deadline_html}
        </div>
        <div style='background:rgba(0,0,0,0.25); padding:26px 60px; display:flex; justify-content:space-between; align-items:center; position:relative; z-index:10;'>
            <div style='font-size:24px; opacity:0.85;'>💡 대표님들을 위한 BIZ-TIP</div>
            <div style='font-size:24px; opacity:0.85;'>▶ 공고 원문 확인</div>
        </div>
    </div>
    </body></html>"""


def make_card2_html(ai_ment, ai_target, today_str):
    ai_ment_e = html.escape(ai_ment).replace('\n', '<br>')
    ai_target_e = html.escape(ai_target).replace('\n', '<br>')
    today_str_e = html.escape(today_str)

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'></head>
    <body style='margin:0;padding:0;'>
    <div style='width:1080px; height:1350px; background:#f0f5ff; font-family:Noto Sans KR, sans-serif; word-break:keep-all;
                display:flex; flex-direction:column;'>
        <div style='background:linear-gradient(90deg, #1a4fa0, #2563c7); padding:24px 60px; color:white;'>
            <div style='font-size:26px; font-weight:bold;'>💡 대표님들을 위한 BIZ-TIP</div>
        </div>
        <div style='flex:1; margin:30px 40px; border-radius:24px; padding:50px; background:white;
                    box-shadow:0 8px 32px rgba(37,99,199,0.1); display:flex; flex-direction:column; gap:40px; overflow:hidden;'>
            <div>
                <div style='display:inline-block; background:#2563c7; color:white; padding:10px 24px; border-radius:20px;
                            font-size:24px; font-weight:bold; margin-bottom:16px;'>사업목적</div>
                <div style='font-size:28px; line-height:1.8; color:#333;'>{ai_ment_e}</div>
            </div>
            <div style='height:2px; background:#e8f0fe; flex-shrink:0;'></div>
            <div>
                <div style='display:inline-block; background:#2563c7; color:white; padding:10px 24px; border-radius:20px;
                            font-size:24px; font-weight:bold; margin-bottom:16px;'>신청자격</div>
                <div style='font-size:26px; line-height:1.9; color:#444;'>{ai_target_e}</div>
            </div>
        </div>
        <div style='background:linear-gradient(90deg, #1a4fa0, #2563c7); padding:20px 60px; color:white;
                    display:flex; justify-content:space-between; align-items:center;'>
            <div style='font-weight:bold;'>🔷 나혼자창업</div>
            <div style='font-size:22px;'>{today_str_e}</div>
        </div>
    </div>
    </body></html>"""


def make_card3_html(ai_amount, method, today_str):
    ai_amount_e = html.escape(ai_amount).replace('\n', '<br>')
    today_str_e = html.escape(today_str)

    method_html = ""
    if method and method.strip():
        method_e = html.escape(method).replace('\n', '<br>')
        method_html = f"""
            <div style='height:2px; background:#e8f0fe; flex-shrink:0;'></div>
            <div>
                <div style='display:inline-block; background:#2563c7; color:white; padding:10px 24px; border-radius:20px;
                            font-size:24px; font-weight:bold; margin-bottom:16px;'>신청방법</div>
                <div style='font-size:26px; line-height:1.9; color:#444;'>{method_e}</div>
            </div>"""

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'></head>
    <body style='margin:0;padding:0;'>
    <div style='width:1080px; height:1350px; background:#f0f5ff; font-family:Noto Sans KR, sans-serif; word-break:keep-all;
                display:flex; flex-direction:column;'>
        <div style='background:linear-gradient(90deg, #1a4fa0, #2563c7); padding:24px 60px; color:white;'>
            <div style='font-size:26px; font-weight:bold;'>💡 대표님들을 위한 BIZ-TIP</div>
        </div>
        <div style='flex:1; margin:30px 40px; border-radius:24px; padding:50px; background:white;
                    box-shadow:0 8px 32px rgba(37,99,199,0.1); display:flex; flex-direction:column; gap:40px; overflow:hidden;'>
            <div>
                <div style='display:inline-block; background:#2563c7; color:white; padding:10px 24px; border-radius:20px;
                            font-size:24px; font-weight:bold; margin-bottom:16px;'>지원내용</div>
                <div style='font-size:26px; line-height:1.9; color:#444;'>{ai_amount_e}</div>
            </div>
            {method_html}
        </div>
        <div style='background:linear-gradient(90deg, #1a4fa0, #2563c7); padding:20px 60px; color:white;
                    display:flex; justify-content:space-between; align-items:center;'>
            <div style='font-weight:bold;'>🔷 나혼자창업</div>
            <div style='font-size:22px;'>{today_str_e}</div>
        </div>
    </div>
    </body></html>"""


def make_card4_html(deadline, org, contact, url, today_str):
    deadline_e = html.escape(deadline)
    org_e = html.escape(org)
    contact_e = html.escape(contact)
    url_e = html.escape(url)
    today_str_e = html.escape(today_str)

    contact_line = f"📞 문의처: {contact_e}<br>" if contact and contact.strip() else ""

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'></head>
    <body style='margin:0;padding:0;'>
    <div style='width:1080px; height:1350px; background:#f0f5ff; font-family:Noto Sans KR, sans-serif; word-break:keep-all;
                display:flex; flex-direction:column;'>
        <div style='background:linear-gradient(90deg, #1a4fa0, #2563c7); padding:24px 60px; color:white;'>
            <div style='font-size:26px; font-weight:bold;'>💡 대표님들을 위한 BIZ-TIP</div>
        </div>
        <div style='flex:1; margin:30px 40px; border-radius:24px; padding:50px; background:white;
                    box-shadow:0 8px 32px rgba(37,99,199,0.1); display:flex; flex-direction:column; gap:40px; overflow:hidden;'>
            <div>
                <div style='display:inline-block; background:#2563c7; color:white; padding:10px 24px; border-radius:20px;
                            font-size:24px; font-weight:bold; margin-bottom:16px;'>신청 일정</div>
                <div style='font-size:28px; line-height:2.0; color:#333;'>
                    📅 신청기간: {deadline_e}<br>🏢 주관기관: {org_e}
                </div>
            </div>
            <div style='height:2px; background:#e8f0fe; flex-shrink:0;'></div>
            <div>
                <div style='display:inline-block; background:#2563c7; color:white; padding:10px 24px; border-radius:20px;
                            font-size:24px; font-weight:bold; margin-bottom:16px;'>문의 및 신청</div>
                <div style='font-size:26px; line-height:2.0; color:#444;'>
                    {contact_line}🔗 공고 원문: <a href='{url_e}' style='color:#2563c7; text-decoration:none;'>{url_e[:60]}...</a>
                </div>
            </div>
            <div style='font-size:20px; color:#aaa; text-align:center; padding:16px; margin-top:auto;'>
                ※ 자세한 내용은 공고 원문을 확인하세요
            </div>
        </div>
        <div style='background:linear-gradient(90deg, #1a4fa0, #2563c7); padding:20px 60px; color:white;
                    display:flex; justify-content:space-between; align-items:center;'>
            <div style='font-weight:bold;'>🔷 나혼자창업</div>
            <div style='font-size:22px;'>{today_str_e}</div>
        </div>
    </div>
    </body></html>"""


async def generate_card_images(item_data: dict, output_dir: str, page):
    today_str = datetime.now(KST).strftime('%Y. %m. %d.')
    os.makedirs(output_dir, exist_ok=True)
    cards = [
        (make_card1_html(item_data['title'], item_data['region'], item_data['ai_ment'], item_data['deadline'], today_str), '05_썸네일.png'),
        (make_card2_html(item_data['ai_ment'], item_data['ai_target'], today_str), '06_사업목적_신청자격.png'),
        (make_card3_html(item_data['ai_amount'], item_data.get('method', ''), today_str), '07_지원내용.png'),
        (make_card4_html(item_data['deadline'], item_data['org'], item_data['contact'], item_data['url'], today_str), '08_신청정보.png'),
    ]
    for html_content, filename in cards:
        await html_to_image(html_content, os.path.join(output_dir, filename), page)


async def main():
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

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none']
        )
        page = await browser.new_page()

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
                contact = detail.get("contact", "")

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

                # 블로그 초안 생성
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

                # 카드뉴스용 짧은 텍스트 생성
                card_text = generate_content(
                    f"다음 지원사업 정보를 바탕으로 카드뉴스용 텍스트를 JSON 형식으로 추출해줘. "
                    f"키는 ment, target, amount, method. 줄바꿈은 \\n, 불릿은 •.\n\n{context}\n\n"
                    f"형식:\n{{\"ment\": \"핵심 1~2줄(이모지포함)\", "
                    f"\"target\": \"• 자격1\\n• 자격2\\n• 자격3\", "
                    f"\"amount\": \"• 지원내용1\\n• 지원내용2\\n• 지원내용3\", "
                    f"\"method\": \"신청방법 1~2줄\"}}"
                )

                ai_ment, ai_target, ai_amount, method_text = "", "", "", ""
                if card_text:
                    try:
                        json_str = card_text
                        if "```json" in card_text:
                            json_str = card_text.split("```json")[1].split("```")[0].strip()
                        elif "```" in card_text:
                            json_str = card_text.split("```")[1].split("```")[0].strip()
                        card_data = json.loads(json_str)
                        ai_ment = card_data.get("ment", "")
                        ai_target = card_data.get("target", "")
                        ai_amount = card_data.get("amount", "")
                        method_text = card_data.get("method", "")
                    except Exception as e:
                        log(f"카드 JSON 파싱 실패: {e}")

                if not ai_ment: ai_ment = f"📢 {title[:30]}..."
                if not ai_target: ai_target = "• 해당 지역 사업자\n• 소상공인·창업자\n• 업력 무관"
                if not ai_amount: ai_amount = "• 사업화 자금 지원\n• 컨설팅 지원\n• 교육 참여"
                if not method_text: method_text = "온라인 신청"

                # 카드뉴스 PNG 생성
                item_data = {
                    'title': title, 'region': region, 'deadline': period,
                    'org': org, 'contact': contact, 'url': url,
                    'ai_ment': ai_ment, 'ai_target': ai_target,
                    'ai_amount': ai_amount, 'method': method_text,
                }
                await generate_card_images(item_data, str(item_dir), page)
                log(f"✅ 카드뉴스 생성 완료: {title[:40]}")

                summary = f"제목: {title}\nURL: {url}\n주관기관: {org}\n신청기간: {period}\n지원금액: {amount}\n\n지원내용:\n{content}"
                (item_dir / "00_요약.txt").write_text(summary, encoding="utf-8")

                attachments = sorted(item_dir.iterdir())
                email_results.append({"title": title, "url": url, "attachments": attachments})
                log(f"✅ 완료: {title[:40]}")

            except Exception as e:
                log(f"처리 실패 ({item.get('id')}): {e}")
                continue

        await browser.close()

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
    asyncio.run(main())
