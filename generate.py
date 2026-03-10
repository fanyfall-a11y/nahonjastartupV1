import os
import json
import re
import html
import asyncio
import io
import zipfile
import requests
import pdfplumber
from urllib.parse import urljoin
from datetime import datetime, timezone, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
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


def check_prohibited_words(text: str) -> list:
    prohibited = ["교정", "치료", "의료기기", "진단", "처방", "치유", "완치"]
    return [w for w in prohibited if w in text]


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


def _to_str(v):
    """Gemini가 list를 반환할 경우 줄바꿈으로 합쳐 문자열로 변환"""
    if isinstance(v, list):
        return '\n'.join(str(x) for x in v)
    return str(v) if v is not None else ''


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


STAGE_THEMES = {
    1: {"bg": "linear-gradient(160deg, #0d2d6e 0%, #1a4fa0 40%, #0a1e4a 100%)", "primary": "#1a4fa0", "secondary": "#2563c7", "accent": "#7ec8ff", "header_bar": "linear-gradient(90deg, #1a4fa0, #2563c7)", "badge": "1단계 · 기본소개"},
    2: {"bg": "linear-gradient(160deg, #0d4f1c 0%, #1e7c3a 40%, #0a2e12 100%)", "primary": "#1e7c3a", "secondary": "#2a9e4d", "accent": "#7effa0", "header_bar": "linear-gradient(90deg, #1e7c3a, #2a9e4d)", "badge": "2단계 · 자격확인"},
    3: {"bg": "linear-gradient(160deg, #2d0d6e 0%, #5a1fa0 40%, #1a0a4a 100%)", "primary": "#5a1fa0", "secondary": "#7c2acc", "accent": "#c47eff", "header_bar": "linear-gradient(90deg, #5a1fa0, #7c2acc)", "badge": "3단계 · 전략심화"},
    4: {"bg": "linear-gradient(160deg, #6e0d0d 0%, #a01a1a 40%, #4a0a0a 100%)", "primary": "#a01a1a", "secondary": "#c72525", "accent": "#ff9e9e", "header_bar": "linear-gradient(90deg, #a01a1a, #c72525)", "badge": "4단계 · 마감긴박"},
}


def make_card1_html(title, region, ai_ment, deadline, today_str, stage=1):
    theme = STAGE_THEMES[stage]
    region_e = html.escape(region)
    ai_ment_e = html.escape(ai_ment).replace('\n', '<br>')
    today_str_e = html.escape(today_str)
    badge_e = html.escape(theme['badge'])
    words = title.split()
    mid = len(words) // 2
    line1_e = html.escape(" ".join(words[:mid]) if words else "")
    line2_e = html.escape(" ".join(words[mid:]) if len(words) > 1 else "")

    if stage == 4:
        try:
            m = re.search(r'(\d{4})[-.\s]+(\d{1,2})[-.\s]+(\d{1,2})', deadline)
            if m:
                from datetime import datetime as _dt
                d_date = _dt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                diff = (d_date - datetime.now(KST).replace(tzinfo=None)).days
                deadline_label = f"마감 D-{diff}일 🔥" if diff >= 0 else "마감 완료"
            else:
                deadline_label = "마감 임박 🔥"
        except Exception:
            deadline_label = "마감 임박 🔥"
    else:
        deadline_label = f"⏰ 마감 {html.escape(deadline)}" if deadline and deadline.strip() else ""

    deadline_html = f"""
        <div style='display:inline-block; background:rgba(255,255,255,0.18); border:2px solid {theme["accent"]};
                    border-radius:50px; padding:14px 40px; color:white; font-size:38px; font-weight:bold; width:fit-content;'>
            {deadline_label}
        </div>""" if deadline_label else ""

    result = f"""<!DOCTYPE html><html><head><meta charset='utf-8'></head>
    <body style='margin:0;padding:0;'>
    <div style='width:1080px; height:1350px; position:relative; overflow:hidden; display:flex; flex-direction:column;
                font-family:Noto Sans KR, sans-serif; word-break:keep-all; overflow-wrap:break-word;
                background:{theme["bg"]}; color:white;'>
        <div style='position:absolute; width:600px; height:600px; top:-100px; right:-150px; border-radius:50%;
                    background:rgba(255,255,255,0.04);'></div>
        <div style='position:absolute; width:450px; height:450px; bottom:-80px; left:-120px; border-radius:50%;
                    background:rgba(255,255,255,0.04);'></div>
        <div style='padding:36px 60px; display:flex; justify-content:space-between; align-items:center; position:relative; z-index:10;'>
            <div style='font-size:36px; font-weight:900;'>🔷 나혼자창업</div>
            <div style='background:rgba(255,255,255,0.15); border:1px solid {theme["accent"]}; color:{theme["accent"]};
                        padding:8px 24px; border-radius:50px; font-size:30px; font-weight:bold;'>{badge_e}</div>
        </div>
        <div style='flex:1; display:flex; flex-direction:column; justify-content:center; gap:36px; padding:0 30px; position:relative; z-index:10;'>
            <div style='display:inline-block; background:rgba(255,255,255,0.15); border:1.5px solid rgba(255,255,255,0.35);
                        border-radius:50px; padding:10px 32px; color:white; font-size:36px; width:fit-content;'>
                📍 {region_e} 지원사업
            </div>
            <div>
                <div style='color:white; font-size:100px; font-weight:900; line-height:1.15; text-shadow:0 4px 20px rgba(0,0,0,0.4);'>{line1_e}</div>
                <div style='color:{theme["accent"]}; font-size:92px; font-weight:900; line-height:1.15; text-shadow:0 4px 20px rgba(0,0,0,0.4);'>{line2_e}</div>
            </div>
            <div style='background:rgba(255,255,255,0.12); border-left:5px solid {theme["accent"]};
                        padding:22px 36px; border-radius:14px; color:white; font-size:44px; line-height:1.65; text-align:center;'>
                {ai_ment_e}
            </div>
            {deadline_html}
        </div>
        <div style='background:rgba(0,0,0,0.25); padding:26px 60px; display:flex; justify-content:space-between; align-items:center; position:relative; z-index:10;'>
            <div style='font-size:36px; opacity:0.85;'>💡 대표님들을 위한 BIZ-TIP</div>
            <div style='font-size:30px; opacity:0.6;'>{today_str_e}</div>
        </div>
    </div>
    </body></html>"""
    return result


def make_card2_html(ai_ment, ai_target, today_str, stage=1):
    theme = STAGE_THEMES[stage]
    ai_ment_e = html.escape(ai_ment).replace('\n', '<br>')
    ai_target_e = html.escape(ai_target).replace('\n', '<br>')
    today_str_e = html.escape(today_str)
    labels = {
        1: ("사업목적", "신청자격"),
        2: ("✅ 신청 가능한 분", "❌ 신청 불가한 분"),
        3: ("💡 핵심 전략", "💸 자금 용도"),
        4: ("🔥 마감 알림", "✅ 최종 체크리스트"),
    }
    label1, label2 = labels.get(stage, labels[1])

    result = f"""<!DOCTYPE html><html><head><meta charset='utf-8'></head>
    <body style='margin:0;padding:0;'>
    <div style='width:1080px; height:1350px; background:#f8f8f8; font-family:Noto Sans KR, sans-serif; word-break:keep-all; overflow-wrap:break-word;
                display:flex; flex-direction:column;'>
        <div style='background:{theme["header_bar"]}; padding:24px 60px; color:white;'>
            <div style='font-size:36px; font-weight:bold;'>🔷 나혼자창업 BIZ-TIP</div>
        </div>
        <div style='flex:1; margin:24px 30px; border-radius:24px; padding:40px; background:white;
                    box-shadow:0 8px 32px rgba(0,0,0,0.08); display:flex; flex-direction:column; gap:28px; overflow:hidden;'>
            <div>
                <div style='display:inline-block; background:{theme["secondary"]}; color:white; padding:10px 24px; border-radius:20px;
                            font-size:36px; font-weight:bold; margin-bottom:16px;'>{label1}</div>
                <div style='font-size:42px; line-height:1.8; color:#333;'>{ai_ment_e}</div>
            </div>
            <div style='height:2px; background:#f0f0f0; flex-shrink:0;'></div>
            <div>
                <div style='display:inline-block; background:{theme["secondary"]}; color:white; padding:10px 24px; border-radius:20px;
                            font-size:36px; font-weight:bold; margin-bottom:16px;'>{label2}</div>
                <div style='font-size:42px; line-height:1.9; color:#444;'>{ai_target_e}</div>
            </div>
        </div>
        <div style='background:{theme["header_bar"]}; padding:20px 60px; color:white;
                    display:flex; justify-content:space-between; align-items:center;'>
            <div style='font-weight:bold; font-size:36px;'>🔷 나혼자창업</div>
            <div style='font-size:30px;'>{today_str_e}</div>
        </div>
    </div>
    </body></html>"""
    return result


def make_card3_html(ai_amount, method, today_str, stage=1):
    theme = STAGE_THEMES[stage]
    ai_amount_e = html.escape(ai_amount).replace('\n', '<br>')
    today_str_e = html.escape(today_str)
    labels = {
        1: ("지원내용", "신청방법"),
        2: ("📋 필요 서류", "📅 신청 일정"),
        3: ("📊 단계별 구조", "📝 사업계획서 팁"),
        4: ("⚠️ 흔한 실수 TOP3", "🚨 놓치면 안 되는 것"),
    }
    label1, label2 = labels.get(stage, labels[1])

    method_html = ""
    if method and method.strip():
        method_e = html.escape(method).replace('\n', '<br>')
        method_html = f"""
            <div style='height:2px; background:#f0f0f0; flex-shrink:0;'></div>
            <div>
                <div style='display:inline-block; background:{theme["secondary"]}; color:white; padding:10px 24px; border-radius:20px;
                            font-size:36px; font-weight:bold; margin-bottom:16px;'>{label2}</div>
                <div style='font-size:42px; line-height:1.9; color:#444;'>{method_e}</div>
            </div>"""

    result = f"""<!DOCTYPE html><html><head><meta charset='utf-8'></head>
    <body style='margin:0;padding:0;'>
    <div style='width:1080px; height:1350px; background:#f8f8f8; font-family:Noto Sans KR, sans-serif; word-break:keep-all; overflow-wrap:break-word;
                display:flex; flex-direction:column;'>
        <div style='background:{theme["header_bar"]}; padding:24px 60px; color:white;'>
            <div style='font-size:36px; font-weight:bold;'>🔷 나혼자창업 BIZ-TIP</div>
        </div>
        <div style='flex:1; margin:24px 30px; border-radius:24px; padding:40px; background:white;
                    box-shadow:0 8px 32px rgba(0,0,0,0.08); display:flex; flex-direction:column; gap:28px; overflow:hidden;'>
            <div>
                <div style='display:inline-block; background:{theme["secondary"]}; color:white; padding:10px 24px; border-radius:20px;
                            font-size:36px; font-weight:bold; margin-bottom:16px;'>{label1}</div>
                <div style='font-size:42px; line-height:1.9; color:#444;'>{ai_amount_e}</div>
            </div>
            {method_html}
        </div>
        <div style='background:{theme["header_bar"]}; padding:20px 60px; color:white;
                    display:flex; justify-content:space-between; align-items:center;'>
            <div style='font-weight:bold; font-size:36px;'>🔷 나혼자창업</div>
            <div style='font-size:30px;'>{today_str_e}</div>
        </div>
    </div>
    </body></html>"""
    return result


def make_card4_html(deadline, org, contact, url, today_str, stage=1, extra_content=""):
    theme = STAGE_THEMES[stage]
    deadline_e = html.escape(deadline)
    org_e = html.escape(org)
    contact_e = html.escape(contact)
    url_e = html.escape(url)
    today_str_e = html.escape(today_str)
    extra_e = html.escape(extra_content).replace('\n', '<br>')

    if stage == 1:
        contact_line = f"📞 문의처: {contact_e}<br>" if contact and contact.strip() else ""
        sec1_label = "신청 일정"
        sec1_body = f"📅 신청기간: {deadline_e}<br>🏢 주관기관: {org_e}"
        sec2_label = "문의 및 신청"
        sec2_body = f"{contact_line}🔗 공고 원문: <a href='{url_e}' style='color:{theme['secondary']};text-decoration:none;'>{url_e[:60]}...</a>"
    elif stage == 2:
        sec1_label, sec1_body = "👀 다음 편 예고", extra_e
        sec2_label = "🔗 공고 원문"
        sec2_body = f"<a href='{url_e}' style='color:{theme['secondary']};text-decoration:none;word-break:break-all;'>{url_e}</a>"
    elif stage == 3:
        sec1_label, sec1_body = "📝 핵심 팁 요약", extra_e
        sec2_label = "🔗 공고 원문"
        sec2_body = f"<a href='{url_e}' style='color:{theme['secondary']};text-decoration:none;word-break:break-all;'>{url_e}</a>"
    else:
        sec1_label, sec1_body = "🚨 지금 바로 신청!", extra_e
        sec2_label = "🔗 공고 원문"
        sec2_body = f"<a href='{url_e}' style='color:{theme['secondary']};text-decoration:none;word-break:break-all;'>{url_e}</a>"

    result = f"""<!DOCTYPE html><html><head><meta charset='utf-8'></head>
    <body style='margin:0;padding:0;'>
    <div style='width:1080px; height:1350px; background:#f8f8f8; font-family:Noto Sans KR, sans-serif; word-break:keep-all; overflow-wrap:break-word;
                display:flex; flex-direction:column;'>
        <div style='background:{theme["header_bar"]}; padding:24px 60px; color:white;'>
            <div style='font-size:36px; font-weight:bold;'>🔷 나혼자창업 BIZ-TIP</div>
        </div>
        <div style='flex:1; margin:24px 30px; border-radius:24px; padding:40px; background:white;
                    box-shadow:0 8px 32px rgba(0,0,0,0.08); display:flex; flex-direction:column; gap:28px; overflow:hidden;'>
            <div>
                <div style='display:inline-block; background:{theme["secondary"]}; color:white; padding:10px 24px; border-radius:20px;
                            font-size:36px; font-weight:bold; margin-bottom:16px;'>{sec1_label}</div>
                <div style='font-size:42px; line-height:2.0; color:#333;'>{sec1_body}</div>
            </div>
            <div style='height:2px; background:#f0f0f0; flex-shrink:0;'></div>
            <div>
                <div style='display:inline-block; background:{theme["secondary"]}; color:white; padding:10px 24px; border-radius:20px;
                            font-size:36px; font-weight:bold; margin-bottom:16px;'>{sec2_label}</div>
                <div style='font-size:42px; line-height:2.0; color:#444; word-break:break-all;'>{sec2_body}</div>
            </div>
            <div style='font-size:30px; color:#aaa; text-align:center; padding:16px; margin-top:auto;'>
                ※ 자세한 내용은 공고 원문을 확인하세요
            </div>
        </div>
        <div style='background:{theme["header_bar"]}; padding:20px 60px; color:white;
                    display:flex; justify-content:space-between; align-items:center;'>
            <div style='font-weight:bold; font-size:36px;'>🔷 나혼자창업</div>
            <div style='font-size:30px;'>{today_str_e}</div>
        </div>
    </div>
    </body></html>"""
    return result


def optimize_images_for_platforms(item_dir: Path, title: str, region: str, cta_text: str = "이 정보가 도움됐다면 저장/공유해주세요!") -> None:
    try:
        files = sorted(item_dir.glob("0[5678]_*.png"))
        if not files:
            return

        font_paths = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"
        ]
        font_path = None
        for p in font_paths:
            if Path(p).exists():
                font_path = p
                break

        def get_font(size):
            return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()

        region_map = {
            "전국": "national", "서울": "seoul", "경기": "gyeonggi", "부산": "busan",
            "인천": "incheon", "대구": "daegu", "광주": "gwangju", "대전": "daejeon",
            "울산": "ulsan", "제주": "jeju"
        }
        region_slug = region_map.get(region, "korea")
        title_slug = "-".join(word.lower() for word in re.sub(r'[^\w\s]', '', title).split()[:4])
        base_slug = f"{region_slug}-{title_slug}-2026"

        def add_watermark(img):
            img_rgba = img.convert("RGBA")
            overlay = Image.new("RGBA", img_rgba.size, (255, 255, 255, 0))
            draw = ImageDraw.Draw(overlay)
            w, h = img_rgba.size
            draw.text((w - 200, h - 60), "나혼자창업", font=get_font(36), fill=(255, 255, 255, 128))
            return Image.alpha_composite(img_rgba, overlay).convert("RGB")

        # naver/
        naver_dir = item_dir / "naver"
        naver_dir.mkdir(exist_ok=True)
        for f in files:
            with Image.open(f) as img:
                wm_img = add_watermark(img)
                w, h = wm_img.size
                new_img = Image.new("RGB", (w, h + 80), (255, 255, 255))
                new_img.paste(wm_img, (0, 0))
                draw = ImageDraw.Draw(new_img)
                notice_text = "본 포스팅은 '나혼자창업' 브랜드 운영자가 직접 작성한 홍보성 콘텐츠입니다"
                font22 = get_font(22)
                text_bbox = draw.textbbox((0, 0), notice_text, font=font22)
                text_w = text_bbox[2] - text_bbox[0]
                text_h = text_bbox[3] - text_bbox[1]
                draw.text(((w - text_w) / 2, h + (80 - text_h) / 2), notice_text, fill="black", font=font22)
                new_img.save(naver_dir / f.name)

        # tistory/
        tistory_dir = item_dir / "tistory"
        tistory_dir.mkdir(exist_ok=True)
        for idx, f in enumerate(files, 1):
            with Image.open(f) as img:
                wm_img = add_watermark(img)
                wm_img.save(tistory_dir / f"{base_slug}-{idx:02d}.webp", "WEBP", quality=85)
                (tistory_dir / f"{base_slug}-{idx:02d}.txt").write_text(f"{title} - {region} 지원사업 카드뉴스 {idx}번", encoding="utf-8")

        # blogspot/
        blogspot_dir = item_dir / "blogspot"
        blogspot_dir.mkdir(exist_ok=True)
        for idx, f in enumerate(files, 1):
            with Image.open(f) as img:
                wm_img = add_watermark(img)
                wm_img.save(blogspot_dir / f"{base_slug}-{idx:02d}.webp", "WEBP", quality=85)
                (blogspot_dir / f"{base_slug}-{idx:02d}.txt").write_text(f"{title} - {region} 지원사업 카드뉴스 {idx}번", encoding="utf-8")

        # instagram/
        insta_dir = item_dir / "instagram"
        insta_dir.mkdir(exist_ok=True)
        for f in files:
            with Image.open(f) as img:
                cropped = img.crop((0, 135, 1080, 1215))
                wm_img = add_watermark(cropped)
                if "08_" in f.name:
                    final_rgba = wm_img.convert("RGBA")
                    draw = ImageDraw.Draw(final_rgba)
                    draw.rectangle([(0, 980), (1080, 1080)], fill=(26, 79, 160, 200))
                    font28 = get_font(28)
                    bbox = draw.textbbox((0, 0), cta_text, font=font28)
                    text_w = bbox[2] - bbox[0]
                    text_h = bbox[3] - bbox[1]
                    draw.text(((1080 - text_w) / 2, 980 + (100 - text_h) / 2), cta_text, fill="white", font=font28)
                    wm_img = final_rgba.convert("RGB")
                wm_img.save(insta_dir / f.name)

    except Exception as e:
        print(f"이미지 최적화 실패: {e}")


async def generate_card_images(item_data: dict, output_dir: str, page, stage=1):
    today_str = datetime.now(KST).strftime('%Y. %m. %d.')
    os.makedirs(output_dir, exist_ok=True)
    filenames = {
        1: ['05_썸네일.png', '06_사업목적_신청자격.png', '07_지원내용.png', '08_신청정보.png'],
        2: ['05_썸네일.png', '06_자격체크리스트.png', '07_제외대상_서류.png', '08_다음편예고.png'],
        3: ['05_썸네일.png', '06_핵심전략_자금용도.png', '07_단계구조_계획서팁.png', '08_핵심팁요약.png'],
        4: ['05_썸네일.png', '06_마감알림_체크리스트.png', '07_흔한실수.png', '08_CTA.png'],
    }
    stage_files = filenames.get(stage, filenames[1])
    extra_content = item_data.get('method', '') if stage != 1 else ""
    cards = [
        (make_card1_html(item_data['title'], item_data['region'], item_data['ai_ment'], item_data['deadline'], today_str, stage=stage), stage_files[0]),
        (make_card2_html(item_data['ai_ment'], item_data['ai_target'], today_str, stage=stage), stage_files[1]),
        (make_card3_html(item_data['ai_amount'], item_data.get('method', ''), today_str, stage=stage), stage_files[2]),
        (make_card4_html(item_data['deadline'], item_data['org'], item_data['contact'], item_data['url'], today_str, stage=stage, extra_content=extra_content), stage_files[3]),
    ]
    for html_content, filename in cards:
        await html_to_image(html_content, os.path.join(output_dir, filename), page)


async def fetch_pdf_text(page) -> str:
    try:
        links = await page.query_selector_all('a')

        base_url = page.url
        pdf_url = ""
        # 우선순위 1: bizinfo (fileDown.do)
        for link in links:
            href = await link.get_attribute('href')
            if href and 'fileDown.do' in href:
                pdf_url = urljoin(base_url, href)
                break

        # 우선순위 2: .pdf 포함 href 또는 download 속성
        if not pdf_url:
            for link in links:
                href = await link.get_attribute('href')
                download_attr = await link.get_attribute('download')
                if (href and '.pdf' in href.lower()) or download_attr:
                    pdf_url = urljoin(base_url, href)
                    break

        if not pdf_url:
            return ""

        cookies = await page.context.cookies()
        cookie_dict = {c['name']: c['value'] for c in cookies}
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

        response = requests.get(pdf_url, cookies=cookie_dict, headers=headers, timeout=20)
        if response.status_code != 200 or response.content[:4] != b'%PDF':
            return ""

        text_content = ""
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            for p in pdf.pages[:6]:
                text = p.extract_text()
                if text:
                    text_content += text + "\n"

        return text_content[:6000]

    except Exception as e:
        print(f"PDF 추출 실패: {e}")
        return ""


async def enrich_item(item: dict, page) -> dict:
    """API로 받은 item에 상세 페이지 내용 추가"""
    try:
        await page.goto(item['url'], wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(1000)

        body_text = await page.evaluate("document.body.innerText")

        detail = await page.evaluate("""
        () => {
            const FIELDS = {
                eligibility: ['지원대상','신청자격','참여대상','대상기업'],
                content:     ['지원내용','사업내용','지원사항','공고내용'],
                amount:      ['지원규모','지원금액','지원한도','지원내역'],
                method:      ['신청방법','접수방법','신청절차'],
                period:      ['신청기간','접수기간','모집기간','공모기간'],
                contact:     ['문의처','담당자','연락처'],
            };
            const result = {};
            for (const [key, keywords] of Object.entries(FIELDS)) {
                for (const th of document.querySelectorAll('th')) {
                    const thText = th.innerText.replace(/\\s/g,'');
                    if (keywords.some(k => thText.includes(k.replace(/\\s/g,'')))) {
                        let sib = th.nextElementSibling;
                        while (sib && sib.tagName === 'TH') sib = sib.nextElementSibling;
                        if (sib && sib.tagName === 'TD') {
                            const val = sib.innerText.trim().slice(0,300);
                            if (val.length > 2) { result[key] = val; break; }
                        }
                    }
                }
            }
            return result;
        }
        """)

        pdf_text = await fetch_pdf_text(page)
        if pdf_text:
            item['body_text'] = body_text[:2000] + "\n\n=== PDF 첨부파일 내용 ===\n" + pdf_text
        else:
            item['body_text'] = body_text[:5000]
        item.update(detail)

    except Exception as e:
        print(f"상세 크롤링 실패 ({item.get('url','')}): {e}")
        item['body_text'] = f"""사업명: {item.get('title','')}
지원대상: {item.get('eligibility','')}
지원내용: {item.get('content','')}
지원금액: {item.get('amount','')}
신청기간: {item.get('period','')}""".strip()

    return item


async def main():
    date_str = os.getenv("GENERATE_DATE") or datetime.now(KST).strftime("%Y-%m-%d")
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

                safe_title = sanitize_filename(title)
                folder_name = f"{region}_{safe_title[:40]}"
                item_dir = output_base / folder_name
                item_dir.mkdir(parents=True, exist_ok=True)

                item = await enrich_item(item, page)

                period = item.get("period") or detail.get("period", "")
                content = item.get("content") or detail.get("content", "")
                amount = item.get("amount") or detail.get("amount", "")
                contact = item.get("contact") or detail.get("contact", "")

                combined_text = f"""=== API 제공 정보 ===
사업명: {title}
주관기관: {org}
지역: {region}
지원대상: {item.get('eligibility', '정보없음')}
지원내용: {content}
지원금액: {amount}
신청방법: {item.get('method', '정보없음')}
신청기간: {period}
상세링크: {url}

=== 공고 원문 전체 내용 ===
{item.get('body_text', '')}"""

                # Stage 1~4 통합 생성 (1회 호출)
                integrated_prompt = (
                    f"다음 지원사업 정보로 Stage 1~4까지의 모든 콘텐츠를 하나의 JSON 형식으로 생성해줘.\n\n"
                    f"[데이터]\n{combined_text}\n\n"
                    f"[요구사항]\n"
                    f"### Stage 1 - 발견/소개 (파란색)\n"
                    f"- naver: 2000자 마크다운. 톤=친근 따뜻. '안녕하세요 :)'로 시작. 독자=1인창업가. 구성=도입(고민공감)→개요→지원대상→지원금액→신청방법→마무리. 이모지/불릿 포함. SEO 키워드: 지원사업명, 창업지원, 정부지원금, 소상공인지원. H2: 지원대상/지원내용/신청방법/주의사항. 내부링크 힌트: 마지막에 \"다음 글에서 신청자격을 상세히 알려드릴게요\" 포함.\n"
                    f"- tistory: 1500자 HTML(h2,h3,ul,li,strong,table). 톤=깔끔 정보중심. SEO 제목에 '2026 + 사업명 + 신청방법'. 표(table) 1개 이상 포함.\n"
                    f"- blogspot: 1500자 HTML(h2,h3,ul,li). 톤=간결 명확. SEO 제목/첫문단에 '사업명+지원금액+신청대상'.\n"
                    f"- insta: 300자 이내. 톤=짧고 임팩트. 이모지. 금액/마감/대상 핵심. CTA='👉 링크는 프로필에서!'. 해시태그 10개.\n"
                    f"- card: ment='이런 지원사업이 있어요!' 톤으로 핵심 1~2줄(이모지포함). target=지원자격 불릿 3줄. amount=지원금액/내용 불릿 3줄. method=신청방법 1~2줄.\n\n"
                    f"### Stage 2 - 자격확인 (초록색)\n"
                    f"- naver: 2000자 마크다운. 톤='나 해당될까?' 체크리스트 중심. 구성=도입(해당여부 공감)→자격요건 체크리스트(표)→제외대상→FAQ 3가지(Q&A형식)→서류준비리스트→마무리. SEO 키워드: 신청자격, 지원대상, 자격요건. H2: 신청가능대상/제외대상/필요서류/FAQ. 마지막에 \"다음 글에서는 4천만 원을 제대로 쓰는 사업계획서 전략을 알려드립니다\" 포함.\n"
                    f"- tistory: 1500자 HTML. 자격요건 표(table) 필수 포함.\n"
                    f"- blogspot: 1500자 HTML.\n"
                    f"- insta: 300자 이내. '신청 전 딱 3가지만 확인하세요' 톤. CTA='자격 요건 잊지 않게 저장해두세요!'.\n"
                    f"- card: ment='나 해당될까? 지금 확인하세요 ✅' 톤으로 1~2줄. checklist=신청자격 불릿 3줄. exclusions=제외대상 불릿 3줄. next_teaser=다음편예고 1줄.\n\n"
                    f"### Stage 3 - 전략/심화 (보라색)\n"
                    f"- naver: 2000자 마크다운. 톤='이렇게 쓰면 최대 효과!' 전문가 조언. 구성=도입(받는것보다 쓰는것이 중요)→자금용도별 활용전략→1단계/2단계 구조설명→사업계획서 핵심포인트 3가지→자기부담금0원/인건비 강조→마무리. SEO 키워드: 사업계획서, 자금활용, 창업지원금 사용처. H2: 자금용도/단계별구조/사업계획서팁/주의사항. 숫자/금액 구체적으로. 마지막에 \"다음 글: 마감 전 최종 체크리스트\" 예고.\n"
                    f"- tistory: 1500자 HTML.\n"
                    f"- blogspot: 1500자 HTML.\n"
                    f"- insta: 300자 이내. 전문가 조언 톤. 숫자 강조. CTA='자금 활용 팁 공유하기 💙'.\n"
                    f"- card: ment='이렇게 쓰면 최대 효과! 💡' 톤으로 1~2줄. fund_usage=자금용도 불릿 3줄. stage_structure=1단계/2단계 구조 1~2줄. biz_plan_tips=사업계획서 핵심팁 1~2줄.\n\n"
                    f"### Stage 4 - 마감긴박감 (빨간색)\n"
                    f"- naver: 2000자 마크다운. 톤='지금 당장!' 긴박감. 구성=도입(마감시간 명시)→제출전 최종체크리스트(표)→흔한실수 TOP3→신청링크+절차1분요약→손실회피('놓치면 내년까지 없는 기회')→마무리. SEO 키워드: 마감일, 신청방법, 창업지원 마감. H2: 마감일정/최종체크리스트/신청방법/자주하는실수. 마감일 날짜 텍스트 명시. '댓글로 궁금한 점 남겨주세요' 포함.\n"
                    f"- tistory: 1500자 HTML. 마감표+체크리스트 포함.\n"
                    f"- blogspot: 1500자 HTML.\n"
                    f"- insta: 300자 이내. 첫문구='오늘 16시 종료!'. 손실회피 심리 자극. CTA='제출 전 이 체크리스트 꼭 확인하세요!'.\n"
                    f"- card: ment='마감 D-N일! 지금 바로 신청하세요 🔥' 톤으로 1~2줄(마감긴박감). checklist=제출전 체크리스트 불릿 3줄. deadline_warning=마감경고 1줄. cta=행동촉구 1줄.\n\n"
                    f"[JSON 출력 구조]\n"
                    f"{{\n"
                    f"  \"stage1\": {{\"naver\":\"...\",\"tistory\":\"...\",\"blogspot\":\"...\",\"insta\":\"...\",\"card\":{{\"ment\":\"...\",\"target\":\"...\",\"amount\":\"...\",\"method\":\"...\"}}}},\n"
                    f"  \"stage2\": {{\"naver\":\"...\",\"tistory\":\"...\",\"blogspot\":\"...\",\"insta\":\"...\",\"card\":{{\"ment\":\"...\",\"checklist\":\"...\",\"exclusions\":\"...\",\"next_teaser\":\"...\"}}}},\n"
                    f"  \"stage3\": {{\"naver\":\"...\",\"tistory\":\"...\",\"blogspot\":\"...\",\"insta\":\"...\",\"card\":{{\"ment\":\"...\",\"fund_usage\":\"...\",\"stage_structure\":\"...\",\"biz_plan_tips\":\"...\"}}}},\n"
                    f"  \"stage4\": {{\"naver\":\"...\",\"tistory\":\"...\",\"blogspot\":\"...\",\"insta\":\"...\",\"card\":{{\"ment\":\"...\",\"checklist\":\"...\",\"deadline_warning\":\"...\",\"cta\":\"...\"}}}}\n"
                    f"}}\n\n"
                    f"위 구조를 준수하여 JSON만 출력해. 줄바꿈은 \\n 사용."
                )

                response = generate_content(integrated_prompt)
                all_data = {}
                if response:
                    try:
                        json_str = response.strip()
                        if "```json" in json_str:
                            json_str = json_str.split("```json")[1].split("```")[0].strip()
                        elif "```" in json_str:
                            json_str = json_str.split("```")[1].split("```")[0].strip()
                        def _clean_str(m):
                            s = m.group()
                            return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', s).replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
                        json_str = re.sub(r'"(?:[^"\\]|\\.)*"', _clean_str, json_str, flags=re.DOTALL)
                        all_data = json.loads(json_str)
                    except Exception as e:
                        log(f"통합 JSON 파싱 실패: {e}")

                stage1_data = all_data.get("stage1", {})
                stage2_data = all_data.get("stage2", {})
                stage3_data = all_data.get("stage3", {})
                stage4_data = all_data.get("stage4", {})

                # Stage 1 처리
                if stage1_data:
                    naver = stage1_data.get("naver", "")
                    tistory = stage1_data.get("tistory", "")
                    blogspot = stage1_data.get("blogspot", "")
                    insta = stage1_data.get("insta", "")

                    card1 = stage1_data.get("card", {})
                    ai_ment = _to_str(card1.get("ment", ""))
                    ai_target = _to_str(card1.get("target", ""))
                    ai_amount = _to_str(card1.get("amount", ""))
                    method_text = _to_str(card1.get("method", ""))

                    if not ai_ment: ai_ment = f"📢 {title[:30]}..."
                    if not ai_target: ai_target = "• 해당 지역 사업자\n• 소상공인·창업자\n• 업력 무관"
                    if not ai_amount: ai_amount = "• 사업화 자금 지원\n• 컨설팅 지원\n• 교육 참여"
                    if not method_text: method_text = "온라인 신청"

                    (item_dir / "01_네이버블로그.txt").write_text(naver, encoding="utf-8")
                    (item_dir / "02_티스토리.txt").write_text(tistory, encoding="utf-8")
                    (item_dir / "03_블로그스팟.txt").write_text(blogspot, encoding="utf-8")
                    (item_dir / "04_인스타그램.txt").write_text(insta, encoding="utf-8")

                    summary = (
                        f"제목: {title}\n"
                        f"URL: {url}\n"
                        f"주관기관: {org}\n"
                        f"신청기간: {period}\n"
                        f"지원금액: {amount}\n\n"
                        f"[지원대상]\n{item.get('eligibility', '')}\n\n"
                        f"[지원내용]\n{content}\n\n"
                        f"[신청방법]\n{item.get('method', '')}\n\n"
                        f"[카드뉴스 핵심멘트]\n{ai_ment}"
                    )
                    (item_dir / "00_요약.txt").write_text(summary, encoding="utf-8")

                    item_data = {
                        'title': title, 'region': region, 'deadline': period,
                        'org': org, 'contact': contact, 'url': url,
                        'ai_ment': ai_ment, 'ai_target': ai_target,
                        'ai_amount': ai_amount, 'method': method_text,
                    }
                    await generate_card_images(item_data, str(item_dir), page, stage=1)
                    optimize_images_for_platforms(item_dir, title, region)
                    log(f"✅ Stage1 카드뉴스 생성 완료: {title[:40]}")

                # Stage 2 처리
                if stage2_data:
                    stage2_dir = item_dir / "Stage_2_상세"
                    stage2_dir.mkdir(exist_ok=True)
                    s2_naver = stage2_data.get("naver", "")
                    (stage2_dir / "01_네이버블로그.txt").write_text(s2_naver, encoding="utf-8")
                    (stage2_dir / "02_티스토리.txt").write_text(stage2_data.get("tistory", ""), encoding="utf-8")
                    (stage2_dir / "03_블로그스팟.txt").write_text(stage2_data.get("blogspot", ""), encoding="utf-8")
                    (stage2_dir / "04_인스타그램.txt").write_text(stage2_data.get("insta", ""), encoding="utf-8")
                    words2 = check_prohibited_words(s2_naver)
                    if words2:
                        log(f"⚠️ Stage2 금칙어 발견: {words2}")
                    card2 = stage2_data.get("card", {})
                    stage2_card_data = {
                        'title': title, 'region': region, 'deadline': period,
                        'org': org, 'contact': contact, 'url': url,
                        'ai_ment': _to_str(card2.get('ment', '')), 'ai_target': _to_str(card2.get('checklist', '')),
                        'ai_amount': _to_str(card2.get('exclusions', '')), 'method': _to_str(card2.get('next_teaser', '')),
                    }
                    await generate_card_images(stage2_card_data, str(stage2_dir), page, stage=2)
                    optimize_images_for_platforms(stage2_dir, title, region, cta_text="자격 요건 잊지 않게 저장해두세요!")
                    log(f"✅ Stage2 생성 완료: {title[:40]}")

                # Stage 3 처리
                if stage3_data:
                    stage3_dir = item_dir / "Stage_3_심화"
                    stage3_dir.mkdir(exist_ok=True)
                    s3_naver = stage3_data.get("naver", "")
                    (stage3_dir / "01_네이버블로그.txt").write_text(s3_naver, encoding="utf-8")
                    (stage3_dir / "02_티스토리.txt").write_text(stage3_data.get("tistory", ""), encoding="utf-8")
                    (stage3_dir / "03_블로그스팟.txt").write_text(stage3_data.get("blogspot", ""), encoding="utf-8")
                    (stage3_dir / "04_인스타그램.txt").write_text(stage3_data.get("insta", ""), encoding="utf-8")
                    words3 = check_prohibited_words(s3_naver)
                    if words3:
                        log(f"⚠️ Stage3 금칙어 발견: {words3}")
                    card3 = stage3_data.get("card", {})
                    stage3_card_data = {
                        'title': title, 'region': region, 'deadline': period,
                        'org': org, 'contact': contact, 'url': url,
                        'ai_ment': _to_str(card3.get('ment', '')), 'ai_target': _to_str(card3.get('fund_usage', '')),
                        'ai_amount': _to_str(card3.get('stage_structure', '')), 'method': _to_str(card3.get('biz_plan_tips', '')),
                    }
                    await generate_card_images(stage3_card_data, str(stage3_dir), page, stage=3)
                    optimize_images_for_platforms(stage3_dir, title, region, cta_text="자금 활용 팁 공유하기 💙")
                    log(f"✅ Stage3 생성 완료: {title[:40]}")

                # Stage 4 처리
                if stage4_data:
                    stage4_dir = item_dir / "Stage_4_마감"
                    stage4_dir.mkdir(exist_ok=True)
                    s4_naver = stage4_data.get("naver", "")
                    (stage4_dir / "01_네이버블로그.txt").write_text(s4_naver, encoding="utf-8")
                    (stage4_dir / "02_티스토리.txt").write_text(stage4_data.get("tistory", ""), encoding="utf-8")
                    (stage4_dir / "03_블로그스팟.txt").write_text(stage4_data.get("blogspot", ""), encoding="utf-8")
                    (stage4_dir / "04_인스타그램.txt").write_text(stage4_data.get("insta", ""), encoding="utf-8")
                    words4 = check_prohibited_words(s4_naver)
                    if words4:
                        log(f"⚠️ Stage4 금칙어 발견: {words4}")
                    card4 = stage4_data.get("card", {})
                    stage4_card_data = {
                        'title': title, 'region': region, 'deadline': period,
                        'org': org, 'contact': contact, 'url': url,
                        'ai_ment': _to_str(card4.get('ment', '')), 'ai_target': _to_str(card4.get('checklist', '')),
                        'ai_amount': _to_str(card4.get('deadline_warning', '')), 'method': _to_str(card4.get('cta', '')),
                    }
                    await generate_card_images(stage4_card_data, str(stage4_dir), page, stage=4)
                    optimize_images_for_platforms(stage4_dir, title, region, cta_text="제출 전 이 체크리스트 꼭 확인하세요!")
                    log(f"✅ Stage4 생성 완료: {title[:40]}")

                email_results.append({"title": title, "url": url, "item_dir": item_dir})
                log(f"✅ 완료: {title[:40]}")

            except Exception as e:
                log(f"처리 실패 ({item.get('id')}): {e}")
                continue

        await browser.close()

    if GMAIL_USER and GMAIL_APP_PASSWORD and TO_EMAIL and email_results:
        try:
            yag = yagmail.SMTP(GMAIL_USER, GMAIL_APP_PASSWORD)
            for r in email_results:
                safe_title = re.sub(r'[\\/:*?"<>|]', '_', r['title'])
                zip_path = str(Path(r["item_dir"]).parent / f"{safe_title[:50]}.zip")
                file_count = 0
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for file_path in Path(r["item_dir"]).rglob("*"):
                        if file_path.is_file():
                            zipf.write(file_path, arcname=file_path.relative_to(r["item_dir"]))
                            file_count += 1
                if file_count == 0:
                    os.remove(zip_path)
                    log(f"⚠️ 이메일 건너뜀 (생성된 파일 없음): {r['title'][:40]}")
                    continue
                zip_size_mb = Path(zip_path).stat().st_size / 1024 / 1024
                subject = f"📝 블로그 초안 - {r['title'][:40]}"
                body = f"Stage 1~4 콘텐츠 생성 완료\n총 {file_count}개 파일 ({zip_size_mb:.1f}MB)\n\n제목: {r['title']}\n원문: {r['url']}\n\n첨부파일을 확인하세요."
                yag.send(TO_EMAIL, subject, body, attachments=[zip_path])
                log(f"✅ 이메일 발송: {r['title'][:40]} ({file_count}개 파일, {zip_size_mb:.1f}MB)")
                os.remove(zip_path)
        except Exception as e:
            log(f"이메일 발송 실패: {e}")

    log(f"=== 종료: {len(email_results)}건 처리 완료 ===")


if __name__ == "__main__":
    asyncio.run(main())
