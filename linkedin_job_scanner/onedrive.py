from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .config import PROJECT_ROOT, resolve_path


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DEFAULT_SCOPES = ["Files.ReadWrite", "User.Read"]


def onedrive_ready(config: dict[str, Any]) -> tuple[bool, str]:
    onedrive_config = config.get("onedrive", {})
    if not onedrive_config.get("enabled"):
        return False, "OneDrive integration is disabled."
    if not str(onedrive_config.get("client_id", "")).strip():
        return False, "Set onedrive.client_id in config.json."
    if not str(onedrive_config.get("tenant_id", "")).strip():
        return False, "Set onedrive.tenant_id in config.json."
    if not str(onedrive_config.get("resume_folder_id", "")).strip() and not str(onedrive_config.get("resume_folder_path", "")).strip():
        return False, "Set onedrive.resume_folder_id or onedrive.resume_folder_path in config.json."
    return True, ""


def upload_docx_to_onedrive(config: dict[str, Any], docx_path: str | Path, name: str) -> tuple[str, str]:
    ready, reason = onedrive_ready(config)
    if not ready:
        print(f"OneDrive upload skipped: {reason}")
        return "", ""

    path = Path(docx_path)
    if not path.exists() or path.stat().st_size == 0:
        return "", ""

    folder_id = _resume_folder_id(config)
    filename = name if name.lower().endswith(".docx") else f"{name}.docx"
    item = _upload_file_to_folder(config, folder_id, filename, path, DOCX_MIME)
    return str(item.get("id", "")), _sharing_or_web_url(config, item)


def update_onedrive_docx(config: dict[str, Any], item_id: str, docx_path: str | Path) -> tuple[str, str]:
    ready, reason = onedrive_ready(config)
    if not ready:
        print(f"OneDrive update skipped: {reason}")
        return "", ""

    path = Path(docx_path)
    if not item_id or not path.exists() or path.stat().st_size == 0:
        return "", ""

    try:
        item = _json_request(
            config,
            "PUT",
            f"{GRAPH_ROOT}/me/drive/items/{item_id}/content",
            data=path.read_bytes(),
            headers={"Content-Type": DOCX_MIME},
        )
    except RuntimeError as exc:
        if "423" in str(exc) or "resourceLocked" in str(exc):
            backup_name = _locked_docx_backup_name(path)
            print(
                "OneDrive resume file is locked/open; uploading this reformatted resume "
                f"as replacement copy: {backup_name}"
            )
            replacement = _upload_file_to_folder(
                config,
                _resume_folder_id(config),
                backup_name,
                path,
                DOCX_MIME,
            )
            return str(replacement.get("id", "")), _sharing_or_web_url(config, replacement)
        raise
    return str(item.get("id", item_id)), _sharing_or_web_url(config, item)


def upload_excel_to_onedrive(config: dict[str, Any], excel_path: str | Path) -> tuple[str, str]:
    onedrive_config = config.get("onedrive", {})
    if not onedrive_config.get("enabled") or not onedrive_config.get("upload_excel", True):
        return "", ""
    ready, reason = onedrive_ready(config)
    if not ready:
        print(f"OneDrive Excel upload skipped: {reason}")
        return "", ""
    path = Path(excel_path)
    if not path.exists() or path.stat().st_size == 0:
        return "", ""

    folder_id = str(onedrive_config.get("excel_folder_id", "")).strip()
    if not folder_id:
        folder_path = str(onedrive_config.get("excel_folder_path", "")).strip() or str(onedrive_config.get("resume_folder_path", "")).strip()
        folder_id = _ensure_folder_path(config, folder_path) if folder_path else "root"
    try:
        item = _upload_file_to_folder(
            config,
            folder_id,
            path.name,
            path,
            XLSX_MIME,
        )
    except RuntimeError as exc:
        if "423" in str(exc) or "resourceLocked" in str(exc):
            print(
                "OneDrive Excel workbook is locked/open; keeping one workbook only. "
                "Close linkedin_job_results.xlsx in OneDrive/Excel and the next run will update it."
            )
            return "", ""
        raise
    return str(item.get("id", "")), _sharing_or_web_url(config, item)


def download_excel_from_onedrive(config: dict[str, Any], excel_path: str | Path) -> bool:
    onedrive_config = config.get("onedrive", {})
    if not onedrive_config.get("enabled") or not onedrive_config.get("upload_excel", True):
        return False
    ready, _reason = onedrive_ready(config)
    if not ready:
        return False

    output_path = Path(excel_path)
    item_id = str(onedrive_config.get("excel_file_id", "")).strip()
    item = {"id": item_id} if item_id else None
    if not item:
        folder_path = str(onedrive_config.get("excel_folder_path", "")).strip() or str(onedrive_config.get("resume_folder_path", "")).strip()
        filename = str(onedrive_config.get("excel_file_name", output_path.name)).strip() or output_path.name
        lookup_path = "/".join(part for part in [folder_path.strip("/"), filename] if part)
        item = _get_item_by_path(config, lookup_path) if lookup_path else None
    if not item:
        return False

    response = _request(config, "GET", f"{GRAPH_ROOT}/me/drive/items/{item['id']}/content", allow_redirects=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)
    return True


def download_trusted_onedrive_resume_sources(config: dict[str, Any], excel_path: str | Path) -> int:
    try:
        from .excel_report import read_excel_application_status
        from .text_utils import safe_filename
    except ImportError:
        return 0

    trusted_root = Path(str(config.get("trusted_resume_root", ""))).expanduser()
    if not trusted_root:
        return 0
    trusted_root.mkdir(parents=True, exist_ok=True)

    rows = read_excel_application_status(excel_path)
    count = 0
    for job_id, row in rows.items():
        applied = str(row.get("Applied", "")).strip().lower() == "applied"
        use_as_source = str(row.get("Use As Source", "")).strip().lower() in {"yes", "y", "true", "1", "source"}
        if not applied and not use_as_source:
            continue

        item_id = str(row.get("OneDrive Resume ID", "")).strip()
        if not item_id and row.get("OneDrive Resume Link"):
            try:
                item_id = resolve_onedrive_item_id(config, str(row.get("OneDrive Resume Link", "")))
            except Exception as exc:
                print(f"Could not resolve trusted OneDrive link for {job_id}: {exc}")
                continue
        if not item_id:
            continue

        filename = safe_filename(f"{job_id}_applied_onedrive_resume", "applied_onedrive_resume")
        output_path = trusted_root / f"{filename}.docx"
        try:
            if download_onedrive_docx(config, item_id, output_path):
                count += 1
        except Exception as exc:
            print(f"Could not download trusted OneDrive DOCX {item_id}: {exc}")
    if count:
        print(f"Downloaded {count} applied/approved OneDrive resumes as trusted source resumes.")
    return count


def download_onedrive_docx(config: dict[str, Any], item_id: str, output_path: str | Path) -> bool:
    ready, _reason = onedrive_ready(config)
    if not ready or not item_id:
        return False
    response = _request(config, "GET", f"{GRAPH_ROOT}/me/drive/items/{item_id}/content", allow_redirects=True)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return True


def resolve_onedrive_item_id(config: dict[str, Any], value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        encoded = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")
        item = _json_request(config, "GET", f"{GRAPH_ROOT}/shares/u!{encoded}/driveItem")
        return str(item.get("id", ""))
    return value


def _resume_folder_id(config: dict[str, Any]) -> str:
    onedrive_config = config.get("onedrive", {})
    folder_id = str(onedrive_config.get("resume_folder_id", "")).strip()
    if folder_id:
        return folder_id
    return _ensure_folder_path(config, str(onedrive_config.get("resume_folder_path", "")).strip())


def _ensure_folder_path(config: dict[str, Any], folder_path: str) -> str:
    folder_path = folder_path.strip("/")
    if not folder_path:
        return "root"

    parent_id = "root"
    built: list[str] = []
    for part in [item for item in folder_path.split("/") if item]:
        built.append(part)
        existing = _get_item_by_path(config, "/".join(built))
        if existing:
            parent_id = str(existing["id"])
            continue
        created = _json_request(
            config,
            "POST",
            f"{GRAPH_ROOT}/me/drive/items/{parent_id}/children",
            json_body={
                "name": part,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "rename",
            },
        )
        parent_id = str(created["id"])
    return parent_id


def _get_item_by_path(config: dict[str, Any], path: str) -> dict[str, Any] | None:
    encoded = quote(path.strip("/"), safe="/")
    response = _request(config, "GET", f"{GRAPH_ROOT}/me/drive/root:/{encoded}", raise_for_status=False)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def _upload_file_to_folder(config: dict[str, Any], folder_id: str, filename: str, path: Path, mime_type: str) -> dict[str, Any]:
    encoded_name = quote(filename)
    if folder_id == "root":
        url = f"{GRAPH_ROOT}/me/drive/root:/{encoded_name}:/content"
    else:
        url = f"{GRAPH_ROOT}/me/drive/items/{folder_id}:/{encoded_name}:/content"
    response = _request(
        config,
        "PUT",
        url,
        data=path.read_bytes(),
        headers={"Content-Type": mime_type},
    )
    return response.json()


def _locked_docx_backup_name(path: Path) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{path.stem}_replacement_{timestamp}{path.suffix}"


def _sharing_or_web_url(config: dict[str, Any], item: dict[str, Any]) -> str:
    onedrive_config = config.get("onedrive", {})
    item_id = str(item.get("id", ""))
    if item_id and onedrive_config.get("create_sharing_links", True):
        try:
            created = _json_request(
                config,
                "POST",
                f"{GRAPH_ROOT}/me/drive/items/{item_id}/createLink",
                json_body={"type": "edit", "scope": str(onedrive_config.get("sharing_scope", "anonymous"))},
            )
            link = created.get("link", {})
            return str(link.get("webUrl", "") or item.get("webUrl", ""))
        except Exception as exc:
            print(f"OneDrive sharing link creation failed; using webUrl: {exc}")
    return str(item.get("webUrl", ""))


def _json_request(
    config: dict[str, Any],
    method: str,
    url: str,
    json_body: dict[str, Any] | None = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = _request(config, method, url, json_body=json_body, data=data, headers=headers)
    if not response.content:
        return {}
    return response.json()


def _request(
    config: dict[str, Any],
    method: str,
    url: str,
    json_body: dict[str, Any] | None = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    allow_redirects: bool = True,
    raise_for_status: bool = True,
) -> requests.Response:
    token = _access_token(config)
    request_headers = {"Authorization": f"Bearer {token}"}
    if headers:
        request_headers.update(headers)
    response = requests.request(
        method,
        url,
        json=json_body,
        data=data,
        headers=request_headers,
        timeout=90,
        allow_redirects=allow_redirects,
    )
    if raise_for_status:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"Microsoft Graph request failed: {response.status_code} {response.text}") from exc
    return response


def _access_token(config: dict[str, Any]) -> str:
    try:
        import msal
    except ImportError as exc:
        raise RuntimeError("Install Microsoft auth libraries first: python -m pip install -r requirements.txt") from exc

    onedrive_config = config.get("onedrive", {})
    client_id = str(onedrive_config.get("client_id", "")).strip()
    tenant_id = str(onedrive_config.get("tenant_id", "consumers")).strip() or "consumers"
    scopes = [str(item) for item in onedrive_config.get("scopes", DEFAULT_SCOPES)]
    token_path = resolve_path(str(onedrive_config.get("oauth_token_json", "onedrive_token.json")), PROJECT_ROOT)

    cache = msal.SerializableTokenCache()
    if token_path.exists():
        cache.deserialize(token_path.read_text(encoding="utf-8"))
    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
    )
    account = app.get_accounts()[0] if app.get_accounts() else None
    result = app.acquire_token_silent(scopes, account=account)
    if not result:
        flow = app.initiate_device_flow(scopes=scopes)
        if "user_code" not in flow:
            raise RuntimeError(f"Could not start Microsoft device flow: {flow}")
        print(flow["message"])
        result = app.acquire_token_by_device_flow(flow)
    if cache.has_state_changed:
        token_path.write_text(cache.serialize(), encoding="utf-8")
    if not result or "access_token" not in result:
        raise RuntimeError(f"Microsoft authentication failed: {result}")
    return str(result["access_token"])
