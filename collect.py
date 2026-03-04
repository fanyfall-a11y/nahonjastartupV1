import os, json, re, asyncio, requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, parse_qs
from playwright.async_api import async_playwright
import yagmail

KST = timezone(timedelta(hours=9))
today = datetime.now(KST).strftime('%Y-%m-%d')

REGIONS = ['서울','부산','대구','인천','광주','대전','울산','세종','경기','강원','충북','충남','전북','전남','경북','경남','제주']

def extract_region(title, org=''):
    m = re.search(r'\[(' + '|'.join(REGIONS) + r')\]', title)
    if m: return m.group(1)
    for r in REGIONS:
        if r in title or r in org: return r
    return '전국'

def extract_category(title):
    for cat, kws in [('창업교육',['교육','훈련','아카데미','캠프','부트캠프']),('컨설팅/멘토링',['컨설팅','멘토링','코칭','자문']),('글로벌',['글로벌','수출','해외','국제']),('시설제공',['시설','공간','입주','센터']),('자금지원',['융자','대출','보증','금융','자금']),('판로/마케팅',['판로','마케팅','홍보','유통','쇼핑몰'])]:
        if any(k in title for k in kws): return cat
    return '사업화'

def is_target(item):
    title = item.get('title','')
    date = item.get('date','')
    if date and date < today: return False
    if not any(k in title for k in ['창업','스타트업','소상공인','중소기업','바우처','지원금','보조금','사업화','멘토링','컨설팅','글로벌']): return False
    if any(k in title for k in ['채용','입찰','구매','물품']): return False
    return True

def extract_deadline_from_period(period):
    if not period: return ''
    matches = re.findall(r'(\d{4})[.\-]\s*(\d{1,2})[.\-]\s*(\d{1,2})', period)
    if not matches: return ''
    y, m, d = matches[-1]
    return f'{y}-{int(m):02d}-{int(d):02d}'

async def collect_bizinfo():
    api_key = os.environ.get('BIZINFO_API_KEY','')
    url = f'https://apis.data.go.kr/B552735/businessInfo/getBusinessInfoList?serviceKey={api_key}&pageNo=1&numOfRows=150&returnType=json&schEndAt=N'

    try:
        resp = requests.get(url, timeout=30)
    except requests.RequestException:
        return []

    if not resp.text:
        return []

    try:
        raw = resp.json().get('response', {}).get('body', {}).get('items', {}).get('item')
    except ValueError:
        return []

    if not raw:
        return []

    if isinstance(raw, dict):
        raw = [raw]

    items = []
    for it in raw:
        rd = it.get('reqstEndDt','')
        date = f'{rd[:4]}-{rd[4:6]}-{rd[6:]}' if len(rd)==8 else ''
        title = it['pblancNm']
        org = it.get('jrsdInsttNm','')
        item = {'id':'bizinfo_'+it['pblancId'],'source':'bizinfo','title':title,'url':it.get('pblancUrl',''),'date':date,'org':org,'region':extract_region(title,org),'category':extract_category(title),'isTarget':False,'detail':{'period':'','eligibility':'','content':'','amount':''}}
        item['isTarget'] = is_target(item)
        items.append(item)
    return items

async def collect_kstartup(page):
    BASE = 'https://www.k-startup.go.kr'
    await page.goto(BASE+'/web/contents/bizpbanc-ongoing.do', wait_until='domcontentloaded')
    await page.wait_for_timeout(2000)
    items = []
    for li in await page.query_selector_all('.board_list-wrap ul li'):
        try:
            el = await li.query_selector('p.tit,a.tit')
            if not el: continue
            t = (await el.inner_text()).strip()
            a = await li.query_selector('a')
            href = await a.get_attribute('href')
            url = urljoin(BASE, href) if href else ''
            d_el = await li.query_selector('.date')
            date = (await d_el.inner_text()).strip() if d_el else ''
            nums = re.findall(r'\d+', href or '')
            uid = nums[-1] if nums else re.sub(r'\W','',t)[:20]
            item = {'id':'kstartup_'+uid,'source':'kstartup','title':t,'url':url,'date':date,'region':extract_region(t),'category':extract_category(t),'isTarget':False,'detail':{'period':'','eligibility':'','content':'','amount':''}}
            item['isTarget'] = is_target(item)
            items.append(item)
            if len(items)>=15: break
        except: continue
    return items

async def collect_sbiz(page):
    BASE = 'https://www.semas.or.kr'
    print(f'[sbiz] 크롤링 시작: {BASE}...')
    await page.goto(BASE+'/web/board/webBoardList.kmdc?bCd=2001&pNm=BOA0121', wait_until='domcontentloaded')
    await page.wait_for_timeout(3000)
    html = await page.content()
    print('[sbiz HTML]', html[1500:3500])
    items = []
    for tr in await page.query_selector_all('table tbody tr'):
        try:
            a = await tr.query_selector('td:nth-child(2) a')
            if not a: continue
            t = (await a.inner_text()).strip()
            href = await a.get_attribute('href')
            url = BASE + (href or '')
            d_el = await tr.query_selector('td:nth-child(4)')
            date = (await d_el.inner_text()).strip() if d_el else ''
            nums = re.findall(r'\d+', href or '')
            uid = nums[-1] if nums else re.sub(r'\W','',t)[:20]
            item = {'id':'sbiz_'+uid,'source':'sbiz','title':t,'url':url,'date':date,'region':extract_region(t),'category':extract_category(t),'isTarget':False,'detail':{'period':'','eligibility':'','content':'','amount':''}}
            item['isTarget'] = is_target(item)
            items.append(item)
            if len(items)>=10: break
        except Exception as e: print(f'[sbiz 오류] {e}'); continue
    return items

async def collect_smtech(page):
    BASE = 'https://www.smtech.go.kr'
    print(f'[smtech] 크롤링 시작: {BASE}...')
    await page.goto(BASE+'/front/ifg/no/notice02_list.do', wait_until='domcontentloaded')
    await page.wait_for_timeout(3000)
    trs = await page.query_selector_all('table tbody tr')
    print(f'[smtech] tr 개수: {len(trs)}')
    if trs:
        print('[smtech 첫번째 tr]', await trs[0].inner_html())
    items = []
    for tr in trs:
        try:
            a = await tr.query_selector('td.tl a')
            if not a: continue
            t = (await a.inner_text()).strip()
            href = await a.get_attribute('href')
            url = urljoin(BASE, href) if href else ''
            date = ''
            for td in await tr.query_selector_all('td'):
                txt = (await td.inner_text()).strip()
                if re.search(r'\d{4}\.\d{2}\.\d{2}', txt): date = txt; break
            nums = re.findall(r'\d+', href or '')
            uid = nums[-1] if nums else re.sub(r'\W','',t)[:20]
            item = {'id':'smtech_'+uid,'source':'smtech','title':t,'url':url,'date':date,'region':extract_region(t),'category':extract_category(t),'isTarget':False,'detail':{'period':'','eligibility':'','content':'','amount':''}}
            item['isTarget'] = is_target(item)
            items.append(item)
            if len(items)>=15: break
        except Exception as e: print(f'[smtech 오류] {e}'); continue
    return items

async def fetch_detail(page, item, cache):
    iid = item['id']
    if iid in cache:
        item['detail'] = cache[iid]
        return
    try:
        await page.goto(item['url'], wait_until='domcontentloaded', timeout=30000)
        detail = {'period':'','eligibility':'','content':'','amount':''}
        TRASH = {'구 분','구분','-','·','해당없음','없음',''}
        th_map = {
            'period': ['신청기간','접수기간','모집기간','공모기간'],
            'eligibility': ['지원대상','지원자격','신청자격','참여대상'],
            'content': ['지원내용','사업내용','지원사항'],
            'amount': ['지원규모','지원금액','지원한도']
        }
        rows = await page.query_selector_all('th')
        for th in rows:
            th_txt = (await th.inner_text()).strip()
            for key, labels in th_map.items():
                if th_txt in labels:
                    td = await th.evaluate_handle('el => el.nextElementSibling')
                    val = (await td.inner_text()).strip()[:300] if td else ''
                    if val not in TRASH:
                        detail[key] = val
        if not detail['period']:
            body = await page.inner_text('body')
            m = re.search(r'(\d{4}[.\-]\d{2}[.\-]\d{2})\s*[~～]\s*(\d{4}[.\-]\d{2}[.\-]\d{2})', body)
            if m: detail['period'] = m.group(0)
        if detail['period'] and not item['date']:
            item['date'] = extract_deadline_from_period(detail['period'])
        item['detail'] = detail
        cache[iid] = detail
    except:
        pass

async def send_email(new_items):
    if not new_items: return
    user = os.environ.get('GMAIL_USER','')
    pw = os.environ.get('GMAIL_APP_PASSWORD','')
    to = os.environ.get('TO_EMAIL','nagairams1@gmail.com')
    if not user or not pw: return
    subject = f'[나혼자창업] 오늘의 추천 공고 {len(new_items)}건 - {today}'
    body = '\n\n'.join([f'{i["title"]}\n마감: {i["date"]}\n{i["url"]}' for i in new_items])
    yag = yagmail.SMTP(user, pw)
    yag.send(to, subject, body)

async def main():
    cache_path = 'docs/detail-cache.json'
    ids_path = 'collected_ids.json'
    with open(cache_path) as f: cache = json.load(f)
    with open(ids_path) as f: collected_ids = json.load(f)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=['--no-sandbox','--disable-dev-shm-usage'])
        page = await browser.new_page()
        page.set_default_timeout(30000)

        bizinfo_items = await collect_bizinfo()
        kstartup_items = await collect_kstartup(page)
        sbiz_items = await collect_sbiz(page)
        smtech_items = await collect_smtech(page)

        all_items = bizinfo_items + kstartup_items + sbiz_items + smtech_items
        for item in all_items:
            await fetch_detail(page, item, cache)

        await browser.close()

    source_meta = {
        'bizinfo':  {'id':'bizinfo', 'name':'기업마당',    'icon':'🏢','color':'#1a4fa0','items':bizinfo_items},
        'kstartup': {'id':'kstartup','name':'K-Startup',  'icon':'🚀','color':'#e8360e','items':kstartup_items},
        'sbiz':     {'id':'sbiz',    'name':'소상공인마당','icon':'🏪','color':'#2ecc71','items':sbiz_items},
        'smtech':   {'id':'smtech',  'name':'중소기업기술','icon':'🔬','color':'#9b59b6','items':smtech_items},
    }
    for key, meta in source_meta.items():
        meta['count'] = len(meta['items'])
        meta['targetCount'] = sum(1 for i in meta['items'] if i['isTarget'])

    output = {
        'date': today,
        'total': len(all_items),
        'targetCount': sum(1 for i in all_items if i['isTarget']),
        'sources': {k: {**{kk:vv for kk,vv in v.items() if kk!='items'}, 'items':v['items']} for k,v in source_meta.items()}
    }

    daily_path = f'docs/daily/{today}.json'
    with open(daily_path, 'w', encoding='utf-8') as f: json.dump(output, f, ensure_ascii=False, indent=2)
    with open('docs/today-list.json', 'w', encoding='utf-8') as f: json.dump(output, f, ensure_ascii=False, indent=2)
    with open(cache_path, 'w', encoding='utf-8') as f: json.dump(cache, f, ensure_ascii=False, indent=2)

    new_items = [i for i in all_items if i['isTarget'] and i['id'] not in collected_ids]
    try:
        await send_email(new_items)
    except Exception as e:
        print(f'[이메일 오류] {e}')

    for item in all_items:
        collected_ids[item['id']] = today
    with open(ids_path, 'w', encoding='utf-8') as f: json.dump(collected_ids, f, ensure_ascii=False, indent=2)

    print(f'완료: 전체 {len(all_items)}건, 추천 {output["targetCount"]}건, 신규 {len(new_items)}건')

asyncio.run(main())
