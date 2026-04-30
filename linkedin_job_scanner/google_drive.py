from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, resolve_path


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def google_drive_ready(config: dict[str, Any]) -> tuple[bool, str]:
    drive_config = config.get("google_drive", {})
    if not drive_config.get("enabled"):
        return False, "Google Drive integration is disabled."
    folder_id = str(drive_config.get("resume_folder_id", "")).strip()
    if not folder_id:
        return False, "Set google_drive.resume_folder_id in config.json."
    auth_mode = str(drive_config.get("auth_mode", "service_account")).lower()
    if auth_mode == "oauth":
        if not str(drive_config.get("oauth_client_json", "")).strip():
            return False, "Set google_drive.oauth_client_json before creating Google Docs."
        return True, ""
    if not _service_account_json(config):
        return False, "Set google_sheets.service_account_json in config.json."
    return True, ""


def upload_docx_as_google_doc(config: dict[str, Any], docx_path: str | Path, name: str) -> tuple[str, str]:
    drive_config = config.get("google_drive", {})
    if not drive_config.get("enabled") or not drive_config.get("create_google_doc_for_each_resume", True):
        return "", ""

    ready, reason = google_drive_ready(config)
    if not ready:
        print(f"Google Doc upload skipped: {reason}")
        return "", ""

    path = Path(docx_path)
    if not path.exists() or path.stat().st_size == 0:
        return "", ""

    folder_id = str(drive_config.get("resume_folder_id", "")).strip()
    service = _drive_service_with_config(config)
    media = _media_file_upload(str(path), DOCX_MIME)
    metadata = {
        "name": name.removesuffix(".docx"),
        "mimeType": GOOGLE_DOC_MIME,
        "parents": [folder_id],
    }
    created = service.files().create(body=metadata, media_body=media, fields="id,webViewLink").execute()
    return str(created.get("id", "")), str(created.get("webViewLink", ""))


def update_google_doc_from_docx(config: dict[str, Any], google_doc_id: str, docx_path: str | Path) -> tuple[str, str]:
    ready, reason = google_drive_ready(config)
    if not ready:
        print(f"Google Doc update skipped: {reason}")
        return "", ""
    path = Path(docx_path)
    if not google_doc_id or not path.exists() or path.stat().st_size == 0:
        return "", ""

    service = _drive_service_with_config(config)
    media = _media_file_upload(str(path), DOCX_MIME)
    updated = service.files().update(fileId=google_doc_id, media_body=media, fields="id,webViewLink").execute()
    return str(updated.get("id", google_doc_id)), str(updated.get("webViewLink", ""))


def export_google_doc_as_docx(config: dict[str, Any], google_doc_id: str, output_path: str | Path) -> bool:
    ready, _reason = google_drive_ready(config)
    if not ready or not google_doc_id:
        return False
    service = _drive_service_with_config(config)
    data = service.files().export(fileId=google_doc_id, mimeType=DOCX_MIME).execute()
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def extract_google_doc_id(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if re.fullmatch(r"[-_A-Za-z0-9]+", value):
        return value
    match = re.search(r"/document/d/([^/]+)", value)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([^&]+)", value)
    return match.group(1) if match else ""


def _service_account_json(config: dict[str, Any]) -> str:
    return str(config.get("google_sheets", {}).get("service_account_json", "")).strip()


def _drive_service(service_account_json: str) -> Any:
    return _drive_service_with_config({"google_sheets": {"service_account_json": service_account_json}})


def _drive_service_with_config(config: dict[str, Any]) -> Any:
    drive_config = config.get("google_drive", {})
    if str(drive_config.get("auth_mode", "service_account")).lower() == "oauth":
        credentials = _oauth_credentials(config)
    else:
        credentials = _service_account_credentials(_service_account_json(config))
    return _build_drive(credentials)


def _service_account_credentials(service_account_json: str) -> Any:
    try:
        from google.oauth2 import service_account
    except ImportError as exc:
        raise RuntimeError("Install Google API client first: python -m pip install -r requirements.txt") from exc

    return service_account.Credentials.from_service_account_file(
        service_account_json,
        scopes=DRIVE_SCOPES,
    )


def _oauth_credentials(config: dict[str, Any]) -> Any:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("Install Google auth libraries first: python -m pip install -r requirements.txt") from exc

    drive_config = config.get("google_drive", {})
    client_json = str(drive_config.get("oauth_client_json", "")).strip()
    token_json = str(drive_config.get("oauth_token_json", "google_drive_token.json")).strip()
    if not client_json:
        raise RuntimeError("Google Drive OAuth is enabled, but google_drive.oauth_client_json is blank in config.json.")

    client_path = resolve_path(client_json, PROJECT_ROOT)
    token_path = resolve_path(token_json, PROJECT_ROOT)
    credentials = None
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), DRIVE_SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_path), DRIVE_SCOPES)
        credentials = flow.run_local_server(port=0)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


def _build_drive(credentials: Any) -> Any:
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _media_file_upload(path: str, mimetype: str) -> Any:
    from googleapiclient.http import MediaFileUpload

    return MediaFileUpload(path, mimetype=mimetype, resumable=False)
