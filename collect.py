import os, json, re, asyncio, requests, html
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
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
            params = {'crtfcKey': api_key, 'dataType': 'json', 'pageUnit': 100, 'pageIndex': page_idx}
            resp = requests.get(url, params=params, timeout=30)
            data_list = resp.json().get('jsonArray', [])
            if not data_list: break
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
        params = {'serviceKey': api_key, 'page': 1, 'perPage': 50, 'returnType': 'json', 'cond[rcrt_prgs_yn::EQ]': 'Y'}
        resp = requests.get(BASE_URL, params=params, timeout=30)
        data_list = resp.json().get('data', [])
        for it in data_list:
            title = it.get('biz_pbanc_nm', '')
            org = it.get('pbanc_ntrp_nm', '')
            url = it.get('detl_pg_url', '') or ''
            date_val = it.get('pbanc_rcpt_end_dt', '')
            if date_val:
                date = f"{date_val[:4]}-{date_val[4:6]}-{date_val[6:8]}" if len(date_val) == 8 and date_val.isdigit() else date_val[:10]
            else:
                date = ''
            uid = str(it.get('pbanc_sn', ''))
            item = {'id':'kstartup_'+uid,'source':'kstartup','title':title,'url':url,'date':date,'org':org,'region':extract_region(title,org),'category':extract_category(title),'isTarget':False,'detail':{'period':it.get('pbanc_ctnt',''),'eligibility':it.get('aply_trgt_ctnt',''),'content':'','amount':''}}
            item['isTarget'] = is_target(item)
            items.append(item)
    except Exception as e:
        print(f'[kstartup API 오류] {e}')
    return items

def fetch_detail(item, cache):
    iid = item['id']
    original_content = item.get('detail', {}).get('content', '')
    if iid in cache:
        item['detail'] = cache[iid]
        if not item['detail'].get('content') and original_content:
            item['detail']['content'] = original_content
            cache[iid]['content'] = original_content
        return
    try:
        resp = requests.get(item['url'], headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        detail = {'period':'','eligibility':'','content':'','amount':''}
        source = item.get('source', '')
        if source == 'bizinfo':
            for li in soup.find_all('li'):
                s = li.find('span', class_='s_title')
                if not s: continue
                t = s.get_text(strip=True)
                li_str = str(li)
                val_raw = li_str.split('class="txt">', 1)[-1] if 'class="txt">' in li_str else ''
                val = re.sub(r'<[^>]+>', ' ', val_raw)
                val = re.sub(r'\s+', ' ', val).strip()[:300]
                if t in ['신청기간','접수기간','모집기간','공모기간'] and not detail['period']:
                    detail['period'] = val
                elif t == '사업개요' and not detail['content']:
                    detail['content'] = val
        elif source == 'kstartup':
            for p in soup.find_all('p', class_='title'):
                if '지원내용' in p.get_text():
                    ul = p.find_next('ul', class_='dot_list-wrap')
                    if ul:
                        detail['content'] = re.sub(r'\s+', ' ', ul.get_text()).strip()[:300]
                    break
            p_tit = soup.find('p', class_='tit', string=re.compile('신청기간'))
            if p_tit:
                txt_p = p_tit.find_next('p', class_='txt')
                if txt_p:
                    detail['period'] = re.sub(r'\s+', ' ', txt_p.get_text()).strip()
        if not detail['period']:
            body_text = soup.get_text()
            m = re.search(r'(\d{4}[.\-]\d{2}[.\-]\d{2})\s*[~～]\s*(\d{4}[.\-]\d{2}[.\-]\d{2})', body_text)
            if m: detail['period'] = m.group(0)
        if detail['period'] and not item.get('date'):
            item['date'] = extract_deadline_from_period(detail['period'])
        if not detail['content'] and original_content:
            detail['content'] = original_content
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

def title_similarity(t1, t2):
    def normalize(s):
        s = re.sub(r'20\d{2}년?도?\s*', '', s)
        s = re.sub(r'(공고|모집|사업공고|지원사업|공모|신청|접수|안내)\s*$', '', s.strip())
        s = re.sub(r'[\s\[\]\(\)\-_·]', '', s)
        return s
    def bigrams(s):
        return set(s[i:i+2] for i in range(len(s)-1)) if len(s) >= 2 else set()
    n1, n2 = normalize(t1), normalize(t2)
    b1, b2 = bigrams(n1), bigrams(n2)
    if not b1 or not b2:
        return 0.0
    return len(b1 & b2) / len(b1 | b2)

def score_item(item):
    score = 0
    title = item.get('title', '')
    detail = item.get('detail', {})
    content = detail.get('content', '')
    eligibility = detail.get('eligibility', '')
    org = item.get('org', '')
    region = item.get('region', '')
    text_support = title + ' ' + content
    if any(k in text_support for k in ['보조금','지원금','출연금','사업화자금','사업비','운영비']): score += 4
    elif any(k in text_support for k in ['바우처','쿠폰']): score += 3
    elif any(k in text_support for k in ['컨설팅','멘토링','코칭','자문']): score += 2
    elif any(k in text_support for k in ['교육','훈련','아카데미','캠프','시설','공간','입주','판로','마케팅','홍보']): score += 1
    text_target = title + ' ' + eligibility
    if any(k in text_target for k in ['예비창업자','초기창업자']): score += 3
    elif any(k in text_target for k in ['소상공인']): score += 2
    elif any(k in text_target for k in ['중소기업']): score += 1
    if region == '전국': score += 2
    else: score += 1
    if any(k in org for k in ['중소벤처기업부','창업진흥원','중진공','TIPS','기술보증기금','신용보증기금','소상공인시장진흥공단']): score += 2
    else: score += 1
    return score

def summarize_content(text, title=''):
    if not text: return ''
    text = html.unescape(text).strip()
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\r?\n', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'(\d),(\d{3})', r'\1\2', text)
    amounts = re.findall(r'[\d,]+억\s*원?|[\d,]+천만\s*원?|[\d,]+만\s*원?', text)
    text = re.sub(r'「[^」]+」', '', text)
    text = re.sub(r'(공고하오니|알려드립니다|안내드립니다|참여 바랍니다|신청 바랍니다).*', '', text)
    text = re.sub(r'(다음과 같이|안내하오니|아래와 같이|이하 같음).*', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'\([^)]*자세한[^)]*\)', '', text)
    text = re.sub(r'\([^)]*첨부파일[^)]*\)', '', text)
    support_match = re.search(r'([^,.。]*?(?:지원|제공|선발|모집)[^,.。]*)', text)
    def is_valid_core(s):
        if len(s) < 6: return False
        if re.fullmatch(r'[\d\s년도\.\-~～]+', s): return False
        if re.match(r'^[을를이가의에]\s', s): return False
        return True
    result = ''
    if support_match:
        core = support_match.group(1).strip()
        if not is_valid_core(core): return ''
        result = core
        if not any(a in core for a in amounts) and amounts:
            result = f"{core} (최대 {amounts[0]})"
    else:
        first_sentence = re.split(r'[.。]', text)[0].strip()
        if not is_valid_core(first_sentence): return ''
        result = first_sentence
        if amounts:
            result = f"{first_sentence} (최대 {amounts[0]})"
    result = re.sub(r'(.{6,})\s+\1', r'\1', result)
    sentences = re.split(r'[,，]', result)
    if len(sentences) >= 2:
        unique = [sentences[0]]
        for s in sentences[1:]:
            if s.strip() not in sentences[0]:
                unique.append(s)
        result = ','.join(unique)
    result = re.sub(r'\s+[을를이가]\s+', ' ', result).strip()
    result = re.sub(r'\s+[을를이가]\s*$', '', result).strip()
    result = re.sub(r'\s+\S{2,8}(합니다|됩니다)\s*$', '', result).strip()
    result = re.sub(r'\s+\S*(하고자|하기위해|하기 위해|위하여|하기위한)\s*\S*[을를이가]\s*$', '', result).strip()
    if len(result) > 80:
        last_space = result.rfind(' ', 0, 80)
        result = (result[:last_space] if last_space != -1 else result[:80]) + '...'
    return result

def format_item(i):
    d = i.get('detail', {})
    eligibility = html.unescape(d.get('eligibility', '') or '').strip()[:60]
    raw_content = d.get('content', '') or ''
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
    to_raw = os.environ.get('TO_EMAIL','nagairams1@gmail.com')
    to = [e.strip() for e in to_raw.split(',') if e.strip()]
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

    bizinfo_items = await collect_bizinfo()
    kstartup_items = await collect_kstartup()

    all_items = bizinfo_items + kstartup_items

    dedup_items = []
    for item in all_items:
        title = item.get('title', '')
        is_dup = any(title_similarity(title, existing.get('title', '')) >= 0.6 for existing in dedup_items)
        if not is_dup:
            dedup_items.append(item)
    all_items = dedup_items

    for item in all_items:
        fetch_detail(item, cache)

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
    new_items = sorted(new_items, key=score_item, reverse=True)[:20]

    try:
        await send_email(new_items, deadline_items)
    except Exception as e:
        print(f'[이메일 오류] {e}')

    for item in all_items:
        collected_ids[item['id']] = today
    with open(ids_path, 'w', encoding='utf-8') as f: json.dump(collected_ids, f, ensure_ascii=False, indent=2)

    print(f'완료: 전체 {len(all_items)}건, 추천 {output["targetCount"]}건, 신규(상위20) {len(new_items)}건, 오늘마감 {len(deadline_items)}건')

asyncio.run(main())
