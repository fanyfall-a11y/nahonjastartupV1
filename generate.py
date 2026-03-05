import os
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from google import genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import yagmail

# --- Configuration ---
KST = timezone(timedelta(hours=9))

ITEM_IDS = [x.strip() for x in os.getenv("ITEM_IDS", "").split(",") if x.strip()]
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL")
GOOGLE_SERVICE_ACCOUNT_KEY_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "generate_log.txt"


# --- Logging ---
def log(message):
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{ts}] {message}"
    print(log_msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_msg + "\n")


# --- Helpers ---
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


# --- Google Drive Helpers ---
def get_drive_service():
    try:
        if not GOOGLE_SERVICE_ACCOUNT_KEY_JSON:
            return None
        creds_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_KEY_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=["https://www.googleapis.com/auth/drive"]
        )
        return build("drive", "v3", credentials=credentials)
    except Exception as e:
        log(f"Drive Auth Error: {e}")
        return None


def get_or_create_folder(service, parent_id, folder_name):
    q = f"mimeType='application/vnd.google-apps.folder' and trashed=false and name='{folder_name}' and '{parent_id}' in parents"
    results = service.files().list(q=q, fields="files(id)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def upload_file_to_drive(service, folder_id, file_path):
    file_name = Path(file_path).name
    meta = {"name": file_name, "parents": [folder_id]}
    media = MediaFileUpload(str(file_path), mimetype="text/plain")
    service.files().create(body=meta, media_body=media, fields="id").execute()


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

    # genai client는 generate_content 호출 시 생성
    data = load_json_data(date_str)
    target_items = get_items_by_ids(data, ITEM_IDS)

    if not target_items:
        log("매칭된 항목 없음")
        return

    log(f"매칭 항목 {len(target_items)}건 처리 시작")

    service = get_drive_service()
    date_folder_id = None
    if service and GOOGLE_DRIVE_FOLDER_ID:
        date_folder_id = get_or_create_folder(service, GOOGLE_DRIVE_FOLDER_ID, date_str)
    else:
        log("Drive 업로드 생략 (credentials 없음)")

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

            # 네이버 블로그
            naver = generate_content(
                f"다음 지원사업 정보로 네이버 블로그용 포스팅을 작성해줘. "
                f"2000자 내외, 마크다운 형식. 구성: 도입부→사업개요→지원대상→지원내용(금액 포함)→신청방법→마무리. 검색 최적화 고려.\n\n{context}"
            )
            if naver:
                (item_dir / "01_네이버블로그.txt").write_text(naver, encoding="utf-8")

            # 티스토리
            tistory = generate_content(
                f"다음 지원사업 정보로 티스토리 블로그용 포스팅을 작성해줘. "
                f"1500자 내외, HTML 태그(h2, h3, ul, li 등) 적극 활용.\n\n{context}"
            )
            if tistory:
                (item_dir / "02_티스토리.txt").write_text(tistory, encoding="utf-8")

            # 인스타그램
            insta = generate_content(
                f"다음 지원사업 정보로 인스타그램 캡션을 작성해줘. "
                f"본문 300자 이내, 마지막 줄에 해시태그 10개.\n\n{context}"
            )
            if insta:
                (item_dir / "03_인스타그램.txt").write_text(insta, encoding="utf-8")

            # 요약
            summary = f"제목: {title}\nURL: {url}\n주관기관: {org}\n신청기간: {period}\n지원금액: {amount}\n\n지원내용:\n{content}"
            (item_dir / "00_요약.txt").write_text(summary, encoding="utf-8")

            # Drive 업로드
            folder_link = None
            if date_folder_id and service:
                try:
                    item_folder_id = get_or_create_folder(service, date_folder_id, folder_name)
                    for fp in sorted(item_dir.iterdir()):
                        upload_file_to_drive(service, item_folder_id, fp)
                    folder_meta = service.files().get(fileId=item_folder_id, fields="webViewLink").execute()
                    folder_link = folder_meta.get("webViewLink")
                    log(f"Drive 업로드 완료: {folder_link}")
                except Exception as e:
                    log(f"Drive 업로드 실패 ({title}): {e}")

            email_results.append({"title": title, "link": folder_link})
            log(f"✅ 완료: {title[:40]}")

        except Exception as e:
            log(f"처리 실패 ({item.get('id')}): {e}")
            continue

    # 이메일 발송
    if GMAIL_USER and GMAIL_APP_PASSWORD and TO_EMAIL and email_results:
        try:
            yag = yagmail.SMTP(GMAIL_USER, GMAIL_APP_PASSWORD)
            subject = f"✅ 블로그 초안 생성 {len(email_results)}건 - {date_str}"
            lines = []
            for r in email_results:
                link = r["link"] or "Drive 생략"
                lines.append(f"- {r['title']}\n  {link}")
            yag.send(TO_EMAIL, subject, "\n\n".join(lines))
            log("✅ 이메일 발송 완료")
        except Exception as e:
            log(f"이메일 발송 실패: {e}")

    log(f"=== 종료: {len(email_results)}건 처리 완료 ===")


if __name__ == "__main__":
    main()
