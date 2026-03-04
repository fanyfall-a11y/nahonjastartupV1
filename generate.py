import os, json, re, asyncio, textwrap
from datetime import datetime, timezone, timedelta
from pathlib import Path
from playwright.async_api import async_playwright
import google.generativeai as genai
import yagmail
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

KST = timezone(timedelta(hours=9))
today = datetime.now(KST).strftime('%Y-%m-%d')


def log(msg):
    ts = datetime.now(KST).strftime('%Y. %-m. %-d. %p %-I:%M:%S').replace('AM', '오전').replace('PM', '오후')
    line = f'[{ts}] {msg}'
    print(line)
    with open('auto_log.txt', 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def make_safe_name(s, maxlen=40):
    s = re.sub(r'[\\/:*?"<>|]', '_', s)
    return s[:maxlen].strip()


async def scrape_detail(page, url):
    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
    result = {'url': url, 'title': '', 'org': '', 'deadline': '', 'eligibility': '',
              'content': '', 'amount': '', 'method': '', 'contact': '', 'body_text': ''}
    for sel in ['h1', 'h2', '.tit', '.title']:
        el = await page.query_selector(sel)
        if el:
            result['title'] = (await el.inner_text()).strip()
            break
    field_map = {
        'org': ['주관기관', '담당부서', '주관기관명'],
        'deadline': ['신청기간', '접수기간', '모집기간', '공모기간'],
        'eligibility': ['지원대상', '신청자격', '참여대상'],
        'content': ['지원내용', '사업내용', '지원사항'],
        'amount': ['지원규모', '지원금액', '지원한도'],
        'method': ['신청방법', '접수방법'],
        'contact': ['문의처', '담당자', '문의'],
    }
    ths = await page.query_selector_all('th')
    for th in ths:
        th_txt = (await th.inner_text()).strip()
        for field, labels in field_map.items():
            if th_txt in labels and not result[field]:
                td = await th.evaluate_handle('el => el.nextElementSibling')
                if td:
                    val = (await td.inner_text()).strip()[:300]
                    if val not in {'', '-', '·', '해당없음', '없음'}:
                        result[field] = val
    result['body_text'] = await page.evaluate('document.body.innerText')
    return result


async def generate_content(detail):
    genai.configure(api_key=os.environ.get('GEMINI_API_KEY', ''))
    model = genai.GenerativeModel('gemini-1.5-flash')
    bt = detail['body_text'][:2000]

    prompts = {
        'ai_ment': f'다음 지원사업 공고를 SNS 카드뉴스 첫 장에 쓸 핵심 멘트로 작성해줘. 1~2줄, 이모지 1~2개, 누가/얼마/어떤혜택인지 핵심만. 뻔한 표현 절대 금지.\n공고: {bt}',
        'ai_target': f'다음 공고의 신청자격을 불릿 3~4개로 간결하게 요약해줘. 각 줄 앞에 • 기호.\n공고: {bt}',
        'ai_amount': f'다음 공고의 지원내용/지원금액을 불릿 3~4개로 간결하게 요약해줘. 각 줄 앞에 • 기호.\n공고: {bt}',
        'ai_naver': f'다음 공고를 네이버 블로그용 포스팅으로 작성해줘. 2000자 내외, 마크다운 형식. 구성: 도입부→사업개요→지원대상→지원내용→신청방법→마무리.\n공고: {bt}',
        'ai_tistory': f'다음 공고를 티스토리 블로그용 포스팅으로 작성해줘. 2000자 내외, HTML 태그 포함 가능.\n공고: {bt}',
        'ai_blogspot': f'다음 공고를 블로그스팟용 포스팅으로 작성해줘. 2000자 내외.\n공고: {bt}',
        'ai_insta': f'다음 공고를 인스타그램 캡션으로 작성해줘. 300자 이내, 해시태그 10개 포함.\n공고: {bt}',
    }
    result = {}
    for key, prompt in prompts.items():
        try:
            resp = await model.generate_content_async(prompt)
            result[key] = resp.text.strip()
        except Exception as e:
            result[key] = f'[생성 실패: {e}]'
    return result


async def html_to_image(html_content, output_path, page):
    await page.set_viewport_size({'width': 1080, 'height': 1350})
    await page.set_content(html_content, wait_until='networkidle')
    await page.screenshot(path=output_path, type='png', clip={'x': 0, 'y': 0, 'width': 1080, 'height': 1350})


def make_card1_html(detail, ai):
    title = detail['title']
    title1 = title[:20]
    title2 = title[20:40] if len(title) > 20 else ''
    deadline = detail['deadline'] or '마감일 확인 필요'
    region = '전국'
    ment = ai.get('ai_ment', '').replace('<', '&lt;').replace('>', '&gt;')
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<style>*{{margin:0;padding:0;box-sizing:border-box;font-family:'Noto Sans KR',sans-serif;}}</style>
</head><body>
<div style="width:1080px;height:1350px;background:linear-gradient(160deg,#0d2d6e 0%,#1a4fa0 40%,#0a1e4a 100%);display:flex;flex-direction:column;justify-content:space-between;padding:50px 60px;">
  <div style="display:flex;justify-content:space-between;color:white;font-size:28px;font-weight:700;">
    <span>🔷 나혼자창업</span><span>{today}</span>
  </div>
  <div style="display:flex;flex-direction:column;gap:40px;">
    <div style="display:inline-block;border:1.5px solid rgba(255,255,255,0.5);border-radius:50px;padding:10px 28px;color:white;font-size:26px;width:fit-content;">📍 {region} 지원사업</div>
    <div>
      <div style="color:white;font-size:70px;font-weight:900;line-height:1.2;">{title1}</div>
      <div style="color:#7ec8ff;font-size:70px;font-weight:900;line-height:1.2;">{title2}</div>
    </div>
    <div style="background:rgba(255,255,255,0.1);border-left:5px solid #7ec8ff;border-radius:0 16px 16px 0;padding:28px 32px;color:white;font-size:30px;line-height:1.6;">{ment}</div>
    <div style="background:rgba(255,220,0,0.25);border-radius:50px;padding:14px 32px;color:white;font-size:28px;font-weight:700;width:fit-content;">⏰ 마감 {deadline}</div>
  </div>
  <div style="color:white;font-size:26px;font-weight:600;text-align:center;">💡 대표님들을 위한 BIZ-TIP</div>
</div></body></html>'''


def make_card2_html(detail, ai):
    ment = ai.get('ai_ment', '').replace('<', '&lt;').replace('>', '&gt;')
    target = ai.get('ai_target', '').replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br>')
    badge = 'style="background:#1a4fa0;color:white;border-radius:8px;padding:6px 18px;font-size:24px;font-weight:700;display:inline-block;margin-bottom:16px;"'
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8"><style>*{{margin:0;padding:0;box-sizing:border-box;font-family:"Noto Sans KR",sans-serif;}}</style></head>
<body style="background:#f0f5ff;width:1080px;height:1350px;display:flex;flex-direction:column;">
  <div style="background:#1a4fa0;padding:30px 50px;color:white;font-size:28px;font-weight:700;">💡 대표님들을 위한 BIZ-TIP</div>
  <div style="flex:1;background:white;margin:24px;border-radius:24px;box-shadow:0 4px 24px rgba(0,0,0,0.08);padding:48px 50px;display:flex;flex-direction:column;gap:32px;">
    <div>
      <div {badge}>사업목적</div>
      <div style="font-size:28px;line-height:1.8;color:#222;">{ment}</div>
    </div>
    <hr style="border:none;border-top:1.5px solid #e0e8f5;">
    <div>
      <div {badge}>신청자격</div>
      <div style="font-size:26px;line-height:1.9;color:#333;">{target}</div>
    </div>
  </div>
  <div style="background:#1a4fa0;padding:24px 50px;color:white;font-size:26px;font-weight:700;">🔷 나혼자창업</div>
</body></html>'''


def make_card3_html(detail, ai):
    amount = ai.get('ai_amount', '').replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br>')
    method = detail.get('method', '').replace('<', '&lt;').replace('>', '&gt;')
    badge = 'style="background:#1a4fa0;color:white;border-radius:8px;padding:6px 18px;font-size:24px;font-weight:700;display:inline-block;margin-bottom:16px;"'
    method_section = f'<hr style="border:none;border-top:1.5px solid #e0e8f5;"><div><div {badge}>신청방법</div><div style="font-size:26px;line-height:1.9;color:#333;">{method}</div></div>' if method else ''
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8"><style>*{{margin:0;padding:0;box-sizing:border-box;font-family:"Noto Sans KR",sans-serif;}}</style></head>
<body style="background:#f0f5ff;width:1080px;height:1350px;display:flex;flex-direction:column;">
  <div style="background:#1a4fa0;padding:30px 50px;color:white;font-size:28px;font-weight:700;">💡 대표님들을 위한 BIZ-TIP</div>
  <div style="flex:1;background:white;margin:24px;border-radius:24px;box-shadow:0 4px 24px rgba(0,0,0,0.08);padding:48px 50px;display:flex;flex-direction:column;gap:32px;">
    <div><div {badge}>지원내용</div><div style="font-size:26px;line-height:1.9;color:#333;">{amount}</div></div>
    {method_section}
  </div>
  <div style="background:#1a4fa0;padding:24px 50px;color:white;font-size:26px;font-weight:700;">🔷 나혼자창업</div>
</body></html>'''


def make_card4_html(detail, ai):
    deadline = detail.get('deadline', '확인 필요')
    org = detail.get('org', '')
    contact = detail.get('contact', '')
    url = detail.get('url', '')
    def row(label, val):
        if not val: return ''
        return f'<div style="display:flex;gap:20px;align-items:flex-start;font-size:26px;line-height:1.7;"><span style="background:#e8f0fe;color:#1a4fa0;border-radius:8px;padding:4px 16px;font-weight:700;white-space:nowrap;">{label}</span><span style="color:#333;">{val}</span></div>'
    rows = row('마감일', deadline) + row('주관기관', org) + row('문의처', contact) + row('원문URL', url)
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8"><style>*{{margin:0;padding:0;box-sizing:border-box;font-family:"Noto Sans KR",sans-serif;}}</style></head>
<body style="background:#f0f5ff;width:1080px;height:1350px;display:flex;flex-direction:column;">
  <div style="background:#1a4fa0;padding:30px 50px;color:white;font-size:28px;font-weight:700;">💡 대표님들을 위한 BIZ-TIP</div>
  <div style="flex:1;background:white;margin:24px;border-radius:24px;box-shadow:0 4px 24px rgba(0,0,0,0.08);padding:48px 50px;display:flex;flex-direction:column;gap:28px;">
    <div style="font-size:32px;font-weight:800;color:#1a4fa0;border-bottom:2px solid #e0e8f5;padding-bottom:20px;">📋 신청 정보</div>
    {rows}
    <div style="margin-top:auto;background:#f0f5ff;border-radius:16px;padding:24px;font-size:24px;color:#1a4fa0;font-weight:600;text-align:center;">자세한 내용은 공고 원문을 확인하세요</div>
  </div>
  <div style="background:#1a4fa0;padding:24px 50px;color:white;font-size:26px;font-weight:700;">🔷 나혼자창업</div>
</body></html>'''


def save_files(out_dir, detail, ai):
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with open(f'{out_dir}/00_멘트_요약.txt', 'w', encoding='utf-8') as f:
        f.write(f'{ai["ai_ment"]}\n\n=신청자격=\n{ai["ai_target"]}\n\n=지원내용=\n{ai["ai_amount"]}\n\n신청기간: {detail["deadline"]}\n문의처: {detail["contact"]}\nURL: {detail["url"]}')
    with open(f'{out_dir}/05_네이버블로그.txt', 'w', encoding='utf-8') as f: f.write(ai['ai_naver'])
    with open(f'{out_dir}/06_티스토리.txt', 'w', encoding='utf-8') as f: f.write(ai['ai_tistory'])
    with open(f'{out_dir}/07_블로그스팟.txt', 'w', encoding='utf-8') as f: f.write(ai['ai_blogspot'])
    with open(f'{out_dir}/08_인스타그램.txt', 'w', encoding='utf-8') as f: f.write(ai['ai_insta'])


def get_drive_service():
    key_data = json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_KEY'])
    creds = service_account.Credentials.from_service_account_info(
        key_data, scopes=['https://www.googleapis.com/auth/drive'])
    return build('drive', 'v3', credentials=creds)


def get_or_create_folder(service, name, parent_id):
    q = f'name="{name}" and "{parent_id}" in parents and mimeType="application/vnd.google-apps.folder" and trashed=false'
    res = service.files().list(q=q, fields='files(id)').execute()
    if res['files']: return res['files'][0]['id']
    meta = {'name': name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent_id]}
    f = service.files().create(body=meta, fields='id').execute()
    return f['id']


def upload_to_drive(service, folder_id, file_path):
    name = Path(file_path).name
    mime = 'image/png' if name.endswith('.png') else 'text/plain'
    meta = {'name': name, 'parents': [folder_id]}
    media = MediaFileUpload(file_path, mimetype=mime)
    service.files().create(body=meta, media_body=media, fields='id').execute()


async def main():
    log('=== 콘텐츠 생성 시작 ===')
    urls = [u.strip() for u in os.environ.get('TARGET_URLS', '').split(',') if u.strip()][:3]
    if not urls:
        log('TARGET_URLS 없음')
        return
    root_folder = os.environ.get('GOOGLE_DRIVE_FOLDER_ID', '')
    drive = get_drive_service() if root_folder else None
    date_fid = get_or_create_folder(drive, today, root_folder) if drive else None
    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none'])
        page = await browser.new_page()
        page.set_default_timeout(30000)
        for i, url in enumerate(urls, 1):
            log(f'{i}. 스크래핑: {url}')
            detail = await scrape_detail(page, url)
            log(f'{i}. 제목: {detail["title"]}')
            ai = await generate_content(detail)
            region = '전국'
            safe = make_safe_name(detail['title'])
            out = f'output/{today}/{region}_{safe}'
            save_files(out, detail, ai)
            for fn, mk in [
                ('01_썸네일.png', make_card1_html),
                ('02_사업목적_신청자격.png', make_card2_html),
                ('03_지원내용.png', make_card3_html),
                ('04_신청정보.png', make_card4_html)
            ]:
                await html_to_image(mk(detail, ai), f'{out}/{fn}', page)
            log(f'{i}. ✅ 카드 4장 완료')
            if drive:
                sfid = get_or_create_folder(drive, f'{region}_{safe}', date_fid)
                for fp in sorted(Path(out).iterdir()):
                    upload_to_drive(drive, sfid, str(fp))
                link = f'https://drive.google.com/drive/folders/{sfid}'
                log(f'{i}. ✅ Drive: {link}')
                results.append({'title': detail['title'], 'link': link})
            else:
                results.append({'title': detail['title'], 'link': out})
        await browser.close()
    u2 = os.environ.get('GMAIL_USER', '')
    pw2 = os.environ.get('GMAIL_APP_PASSWORD', '')
    if results and u2 and pw2:
        yag = yagmail.SMTP(u2, pw2)
        to = os.environ.get('TO_EMAIL', 'nagairams1@gmail.com')
        body = '\n\n'.join([f'{r["title"]}\n{r["link"]}' for r in results])
        yag.send(to, f'✅ 콘텐츠 생성 완료 {len(results)}건 - {today}', body)
        log('✅ 이메일 발송')
    log('=== 종료 ===')


asyncio.run(main())
