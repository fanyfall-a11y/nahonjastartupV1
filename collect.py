import os, json, re, asyncio, requests, html
from datetime import datetime, timezone, timedelta
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
    if any(k in title for k in ['채용','입찰','구매','물품','R&D','연구개발','기술개발','제조업','소부장','기술이전','실증']): return False
    return True

def extract_deadline_from_period(period):
    if not period: return ''
    matches = re.findall(r'(\d{4})[.\-]\s*(\d{1,2})[.\-]\s*(\d{1,2})', period)
    if not matches: return ''
    y, m, d = matches[-1]
    return f'{y}-{int(m):02d}-{int(d):02d}'

async def collect_bizinfo():
    api_key = os.environ.get('BIZINFO_API_KEY', '')
    url = 'https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do'

    items = []
    page_idx = 1
    tot_cnt = 0

    while True:
        try:
            params = {
                'crtfcKey': api_key,
                'dataType': 'json',
                'pageUnit': 100,
                'pageIndex': page_idx
            }
            resp = requests.get(url, params=params, timeout=30)
            data_list = resp.json().get('jsonArray', [])

            if not data_list:
                break

            if page_idx == 1:
                tot_cnt = int(data_list[0].get('totCnt', 0))

            for it in data_list:
                raw_date_range = it.get('reqstBeginEndDe', '')
                date_str = ''
                matches = re.findall(r'(\d{4})[.\-](\d{2})[.\-](\d{2})', raw_date_range)
                if matches:
                    last_match = matches[-1]
                    date_str = f'{last_match[0]}-{last_match[1]}-{last_match[2]}'

                title = it.get('pblancNm', '')
                org = it.get('jrsdInsttNm', '')
                url_val = it.get('pblancUrl', '') or ''
                if url_val and not url_val.startswith('http'):
                    url_val = 'https://www.bizinfo.go.kr' + url_val
                item = {
                    'id': 'bizinfo_' + str(it.get('pblancId', '')),
                    'source': 'bizinfo',
                    'title': title,
                    'url': url_val,
                    'date': date_str,
                    'org': org,
                    'region': extract_region(title, org),
                    'category': extract_category(title),
                    'isTarget': False,
                    'detail': {
                        'period': raw_date_range,
                        'eligibility': it.get('trgetNm', ''),
                        'content': it.get('bsnsSumryCn', '')[:300],
                        'amount': ''
                    }
                }
                item['isTarget'] = is_target(item)
                items.append(item)

            if len(items) >= 50 or len(items) >= tot_cnt:
                break

            page_idx += 1

        except Exception as e:
            print(f'[bizinfo API 오류] {e}')
            break

    return items

async def collect_kstartup():
    BASE_URL = 'https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01'
    api_key = os.environ.get('KSTARTUP_API_KEY', '')

    items = []
    try:
        params = {
            'serviceKey': api_key,
            'page': 1,
            'perPage': 50,
            'returnType': 'json',
            'cond[rcrt_prgs_yn::EQ]': 'Y'
        }
        resp = requests.get(BASE_URL, params=params, timeout=30)
        data_list = resp.json().get('data', [])

        for it in data_list:
            title = it.get('biz_pbanc_nm', '')
            org = it.get('pbanc_ntrp_nm', '')
            url = it.get('detl_pg_url', '') or ''
            date_val = it.get('pbanc_rcpt_end_dt', '')
            date = date_val[:10] if date_val else ''
            uid = str(it.get('pbanc_sn', ''))
            item = {'id':'kstartup_'+uid,'source':'kstartup','title':title,'url':url,'date':date,'org':org,'region':extract_region(title,org),'category':extract_category(title),'isTarget':False,'detail':{'period':it.get('pbanc_ctnt',''),'eligibility':it.get('aply_trgt_ctnt',''),'content':'','amount':''}}
            item['isTarget'] = is_target(item)
            items.append(item)

    except Exception as e:
        print(f'[kstartup API 오류] {e}')

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

def classify_support_type(title, content):
    text = title + ' ' + content
    if any(k in text for k in ['융자','대출','보증','금융']): return '💰 [융자/대출]'
    if any(k in text for k in ['바우처','쿠폰']): return '🎫 [바우처]'
    if any(k in text for k in ['보조금','지원금','출연금','R&D자금']): return '💵 [보조금]'
    if any(k in text for k in ['사업화자금','사업비','운영비']): return '💼 [사업화자금]'
    if any(k in text for k in ['컨설팅','멘토링','코칭','자문']): return '🤝 [컨설팅비용]'
    if any(k in text for k in ['교육비','훈련비','수강료']): return '📚 [교육비지원]'
    if any(k in text for k in ['교육','훈련','아카데미','캠프']): return '🎓 [교육프로그램]'
    if any(k in text for k in ['시설','공간','입주']): return '🏢 [공간지원]'
    if any(k in text for k in ['판로','마케팅','홍보','전시']): return '📣 [판로지원]'
    if any(k in text for k in ['사업화','창업지원']): return '🚀 [사업화지원]'
    return '📋 [지원사업]'

def summarize_content(text, title=''):
    if not text: return ''
    text = html.unescape(text).strip()
    text = re.sub(r'(공고하오니|알려드립니다|안내드립니다|참여 바랍니다|신청 바랍니다).*', '', text)
    text = re.sub(r'\r?\n', ' ', text).strip()
    amounts = re.findall(r'[\d,]+억\s*원?|[\d,]+천만\s*원?|[\d,]+만\s*원?', text)
    # 핵심 동사구 추출: '~을 지원', '~을 제공'
    support_match = re.search(r'([^,。.]{10,40}(?:지원|제공|선발|모집))', text)
    core = support_match.group(1).strip() if support_match else re.split(r'[.。]', text)[0].strip()[:60]
    return core

def format_item(i):
    d = i.get('detail', {})
    eligibility = html.unescape(d.get('eligibility', '') or '').strip()[:60]
    raw_content = d.get('content', '') or d.get('period', '') or ''
    support_type = classify_support_type(i.get('title',''), raw_content)
    content = summarize_content(raw_content, i.get('title',''))
    region = i.get('region', '전국')
    REGION_PATTERN = r'^\[(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주|전국)\]\s*'
    clean_title = re.sub(REGION_PATTERN, '', i['title'])
    lines = [f"📌 [{region}] {clean_title}  {support_type}", f'⏰ 마감: {i["date"]}  |  🏛️ {i.get("org","")}']
    if eligibility: lines.append(f'👥 대상: {eligibility}')
    if content: lines.append(f'💡 내용: {content}')
    lines.append(f'🔗 {i["url"]}')
    return '\n'.join(lines)

async def send_email(new_items, deadline_items):
    if not new_items and not deadline_items: return
    user = os.environ.get('GMAIL_USER','')
    pw = os.environ.get('GMAIL_APP_PASSWORD','')
    to = os.environ.get('TO_EMAIL','nagairams1@gmail.com')
    if not user or not pw: return
    subject = f'[나혼자창업] 신규 {len(new_items)}건 / 오늘마감 {len(deadline_items)}건 - {today}'
    body = ''
    if new_items:
        body += '🆕 === 신규 추천 공고 ===\n\n'
        body += '\n\n'.join([format_item(i) for i in new_items])
    if deadline_items:
        body += '\n\n🔥 === 오늘 마감 공고 ===\n\n'
        body += '\n\n'.join([format_item(i) for i in deadline_items])
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
        kstartup_items = await collect_kstartup()

        all_items = bizinfo_items + kstartup_items

        # 제목 정규화 기반 중복 제거
        seen_keys = {}
        dedup_items = []
        for item in all_items:
            t = re.sub(r'20\d{2}년?도?\s*', '', item.get('title', ''))
            key = re.sub(r'[\s\[\]\(\)]', '', t)[:15]
            if key not in seen_keys:
                seen_keys[key] = True
                dedup_items.append(item)
        all_items = dedup_items

        for item in all_items:
            await fetch_detail(page, item, cache)

        await browser.close()

    source_meta = {
        'bizinfo':  {'id':'bizinfo', 'name':'기업마당',  'icon':'🏢','color':'#1a4fa0','items':bizinfo_items},
        'kstartup': {'id':'kstartup','name':'K-Startup','icon':'🚀','color':'#e8360e','items':kstartup_items},
    }
    for key, meta in source_meta.items():
        meta['count'] = len(meta['items'])
        meta['targetCount'] = sum(1 for i in meta['items'] if i['isTarget'])

    deadline_items = [i for i in all_items if i.get('date') == today]

    output = {
        'date': today,
        'total': len(all_items),
        'targetCount': sum(1 for i in all_items if i['isTarget']),
        'sources': {k: {**{kk:vv for kk,vv in v.items() if kk!='items'}, 'items':v['items']} for k,v in source_meta.items()},
        'todayDeadline': deadline_items
    }

    daily_path = f'docs/daily/{today}.json'
    with open(daily_path, 'w', encoding='utf-8') as f: json.dump(output, f, ensure_ascii=False, indent=2)
    with open('docs/today-list.json', 'w', encoding='utf-8') as f: json.dump(output, f, ensure_ascii=False, indent=2)
    with open(cache_path, 'w', encoding='utf-8') as f: json.dump(cache, f, ensure_ascii=False, indent=2)

    new_items = [i for i in all_items if i['isTarget'] and i['id'] not in collected_ids]
    try:
        await send_email(new_items, deadline_items)
    except Exception as e:
        print(f'[이메일 오류] {e}')

    for item in all_items:
        collected_ids[item['id']] = today
    with open(ids_path, 'w', encoding='utf-8') as f: json.dump(collected_ids, f, ensure_ascii=False, indent=2)

    print(f'완료: 전체 {len(all_items)}건, 추천 {output["targetCount"]}건, 신규 {len(new_items)}건, 오늘마감 {len(deadline_items)}건')

asyncio.run(main())
