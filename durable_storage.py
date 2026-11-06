"""Durable SQLite backup in a private Supabase row; never put credentials in Git."""
import base64
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

ROW_ID = "tatsuki_main_database"

class RemoteStorageError(RuntimeError):
    pass

def _request(url, key, method="GET", payload=None):
    headers = {"apikey": key, "Accept": "application/json"}
    # New sb_secret_ keys are API keys, not JWT bearer tokens.
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = "Bearer " + key
    if payload is not None:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")) if method == "GET" else None
    except urllib.error.HTTPError as exc:
        # Show only allowlisted diagnostic fields; never print URLs, headers, keys,
        # arbitrary server messages, or request bodies.
        error_code = "unknown"
        try:
            error_data = json.loads(exc.read(4096).decode("utf-8"))
            candidate = error_data.get("code", "")
            if isinstance(candidate, str) and candidate in {
                "PGRST301", "PGRST302", "PGRST116", "42501",
                "42P01", "PGRST205", "PGRST202",
            }:
                error_code = candidate
        except (ValueError, UnicodeDecodeError, AttributeError):
            pass
        request_stage = "読込（GET）" if method == "GET" else "保存（POST）"
        raise RemoteStorageError(
            f"Supabase接続診断：{request_stage}でHTTP {exc.code} "
            f"（エラー分類：{error_code}）。"
            "鍵や個人情報は表示していません。データは上書きしません。"
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        detail = type(reason).__name__ if reason is not None else type(exc).__name__
        raise RemoteStorageError(f"Supabase接続エラー（{detail}）。URL・ネットワーク・秘密鍵の設定を確認してください。データは上書きしません。") from exc
    except (ValueError, TimeoutError) as exc:
        raise RemoteStorageError(f"Supabase通信エラー（{type(exc).__name__}）。データは上書きしません。") from exc

class DurableConnection(sqlite3.Connection):
    remote_url = None
    remote_key = None
    sync_enabled = False

    def commit(self):
        super().commit()
        if self.sync_enabled:
            self.sync_remote()

    def sync_remote(self):
        if not self.remote_url or not self.remote_key:
            return
        blob = base64.b64encode(self.serialize()).decode("ascii")
        _request(self.remote_url, self.remote_key, "POST",
                 [{"id": ROW_ID, "data": {"format": "sqlite-base64-v1", "blob": blob}}])

def open_database(path, secrets):
    """Restore Supabase snapshot before app tables are initialized; fail closed on errors."""
    url = secrets.get("SUPABASE_URL", "").strip()
    key = secrets.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if bool(url) != bool(key):
        raise RemoteStorageError("SupabaseのURLと秘密鍵の両方をSecretsに設定してください。")
    if url and (not url.startswith("https://") or not key.startswith(("sb_secret_", "eyJ"))):
        raise RemoteStorageError("Supabaseの接続設定を確認してください。")
    conn = sqlite3.connect(":memory:" if url else str(path), factory=DurableConnection)
    if not url:
        return conn, False
    endpoint = url.rstrip("/") + "/rest/v1/tatsuki_storage"
    query = endpoint + "?id=eq." + urllib.parse.quote(ROW_ID) + "&select=data"
    rows = _request(query, key)
    if not isinstance(rows, list):
        raise RemoteStorageError("Supabaseから予期しない応答が返りました。")
    if rows:
        try:
            data = rows[0]["data"]
            if data["format"] != "sqlite-base64-v1":
                raise ValueError("unsupported backup")
            conn.deserialize(base64.b64decode(data["blob"], validate=True))
        except (KeyError, ValueError, TypeError, sqlite3.DatabaseError) as exc:
            raise RemoteStorageError("保存済みデータを読み込めません。上書きせず停止します。") from exc
    conn.remote_url = endpoint
    conn.remote_key = key
    conn.sync_enabled = True
    # When remote is empty, create the initial snapshot. Subsequent schema changes
    # and user actions are automatically synchronized on commit.
    if not rows:
        conn.sync_remote()
    return conn, True
