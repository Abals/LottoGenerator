"""카카오톡 '나에게 보내기' (카카오 로그인 REST API + 메시지 API).

토큰은 Windows DPAPI로 암호화해서 현재 Windows 사용자만 풀 수 있게 저장한다.
"""

import base64
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from lotto_core import data_dir

AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
LOGOUT_URL = "https://kapi.kakao.com/v1/user/logout"
DEFAULT_PORT = 8765
TEXT_LIMIT = 200          # 텍스트 템플릿 최대 길이
LINK_URL = "https://www.dhlottery.co.kr"


class KakaoError(Exception):
    pass


# ---------------------------------------------------------------- 암호화 저장 (DPAPI)
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB),
                                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(_BLOB)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.POINTER(_BLOB),
                                            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.POINTER(_BLOB)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _UI_FORBIDDEN = 0x01

    def _dpapi(data, protect):
        buf = ctypes.create_string_buffer(data, len(data))
        inp = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = _BLOB()
        if protect:
            ok = _crypt32.CryptProtectData(ctypes.byref(inp), "LottoGenerator", None, None, None,
                                           _UI_FORBIDDEN, ctypes.byref(out))
        else:
            ok = _crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None,
                                             _UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    def protect(text):
        return "dpapi:" + base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode("ascii")

    def unprotect(stored):
        if stored.startswith("dpapi:"):
            return _dpapi(base64.b64decode(stored[6:]), False).decode("utf-8")
        if stored.startswith("plain:"):
            return base64.b64decode(stored[6:]).decode("utf-8")
        raise ValueError("unknown format")
else:  # 개발용 (Windows가 아닐 때는 암호화 없이 저장)
    def protect(text):
        return "plain:" + base64.b64encode(text.encode("utf-8")).decode("ascii")

    def unprotect(stored):
        if stored.startswith("plain:"):
            return base64.b64decode(stored[6:]).decode("utf-8")
        raise ValueError("이 컴퓨터에서 저장한 값이 아닙니다")


def _secure_path(name):
    return os.path.join(data_dir(), name)


def save_secret(name, obj):
    path = _secure_path(name)
    if obj is None:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    with open(path, "w", encoding="ascii") as f:
        f.write(protect(json.dumps(obj)))


def load_secret(name):
    try:
        with open(_secure_path(name), "r", encoding="ascii") as f:
            return json.loads(unprotect(f.read().strip()))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- HTTP
def _post(url, form, token=None):
    data = urllib.parse.urlencode(form).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except ValueError:
            body = {}
        err = KakaoError(_explain(e.code, body))
        err.status, err.body = e.code, body
        raise err
    except (urllib.error.URLError, OSError) as e:
        raise KakaoError("카카오 서버에 연결할 수 없습니다: %s" % getattr(e, "reason", e))


def _explain(status, body):
    code = body.get("error_code") or body.get("code")
    raw = body.get("error_description") or body.get("msg") or ""
    hints = {
        "KOE101": "REST API 키가 잘못되었습니다.",
        "KOE006": "Redirect URI가 카카오 디벨로퍼스에 등록되어 있지 않습니다.",
        "KOE010": "Client Secret이 맞지 않거나, 사용 설정인데 입력하지 않았습니다.",
        "KOE320": "인가 코드가 만료되었습니다. 다시 로그인해 주세요.",
        "KOE322": "로그인 정보가 만료되었습니다. 다시 로그인해 주세요.",
        -401: "로그인이 만료되었습니다. 다시 로그인해 주세요.",
        -402: "'카카오톡 메시지 전송' 동의가 필요합니다. 다시 로그인해서 동의해 주세요.",
        -403: "앱 설정 문제로 거부되었습니다. 카카오 로그인이 켜져 있는지 확인해 주세요.",
    }
    msg = hints.get(code)
    if body.get("error") == "invalid_grant" and not msg:
        msg = "로그인 정보가 만료되었습니다. 다시 로그인해 주세요."
    return "%s (HTTP %s %s %s)" % (msg or "카카오 오류", status, code or "", raw)


# ---------------------------------------------------------------- 클라이언트
class KakaoClient:
    TOKEN_FILE = "kakao_token.dat"
    CONFIG_FILE = "kakao_config.dat"

    def __init__(self):
        cfg = load_secret(self.CONFIG_FILE) or {}
        self.rest_api_key = cfg.get("rest_api_key", "")
        self.client_secret = cfg.get("client_secret", "")
        self.port = int(cfg.get("port", DEFAULT_PORT))
        self.tokens = load_secret(self.TOKEN_FILE)
        self._lock = threading.Lock()

    # ---- 설정
    @property
    def redirect_uri(self):
        return "http://localhost:%d/callback" % self.port

    def save_config(self, rest_api_key, client_secret, port):
        port = int(port)
        if not 1024 <= port <= 65535:
            raise KakaoError("포트는 1024~65535 사이로 입력해 주세요.")
        changed = rest_api_key.strip() != self.rest_api_key
        self.rest_api_key = rest_api_key.strip()
        self.client_secret = client_secret.strip()
        self.port = port
        save_secret(self.CONFIG_FILE, {"rest_api_key": self.rest_api_key,
                                       "client_secret": self.client_secret, "port": self.port})
        if changed:                 # 다른 앱 키로 바꾸면 기존 토큰은 쓸 수 없다
            self._set_tokens(None)

    def is_configured(self):
        return bool(self.rest_api_key)

    def is_logged_in(self):
        return bool(self.tokens and self.tokens.get("refresh_token"))

    def status_text(self):
        if not self.is_configured():
            return "REST API 키를 먼저 입력해 주세요."
        if not self.is_logged_in():
            return "로그인 필요"
        exp = self.tokens.get("refresh_expires_at")
        if exp:
            return "로그인됨 · 자동 로그인 유지 기한 %s" % time.strftime("%Y-%m-%d", time.localtime(exp))
        return "로그인됨"

    def _set_tokens(self, tokens):
        self.tokens = tokens
        save_secret(self.TOKEN_FILE, tokens)

    def _store_token_response(self, res):
        now = time.time()
        tokens = dict(self.tokens or {})
        tokens["access_token"] = res["access_token"]
        tokens["expires_at"] = now + int(res.get("expires_in", 0))
        if res.get("refresh_token"):            # 갱신 응답에는 기한이 1개월 미만일 때만 새 값이 온다
            tokens["refresh_token"] = res["refresh_token"]
            tokens["refresh_expires_at"] = now + int(res.get("refresh_token_expires_in", 0))
        self._set_tokens(tokens)

    # ---- 로그인 (브라우저 → localhost 콜백)
    def login(self, timeout=180, open_browser=webbrowser.open):
        if not self.is_configured():
            raise KakaoError("REST API 키를 먼저 저장해 주세요.")
        state = secrets.token_urlsafe(16)
        result = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                if url.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                q = urllib.parse.parse_qs(url.query)
                result.update({k: v[0] for k, v in q.items()})
                ok = "code" in q and q.get("state", [""])[0] == state
                body = ("<h2>%s</h2><p>이 창을 닫고 로또 번호 생성기로 돌아가세요.</p>" %
                        ("카카오 로그인 완료" if ok else "카카오 로그인 실패")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        try:
            server = HTTPServer(("127.0.0.1", self.port), Handler)
        except OSError:
            raise KakaoError("포트 %d을(를) 다른 프로그램이 쓰고 있습니다. 설정에서 포트를 바꾸고, "
                             "카카오 디벨로퍼스의 Redirect URI도 같이 바꿔 주세요." % self.port)
        server.timeout = 1
        try:
            params = {"response_type": "code", "client_id": self.rest_api_key,
                      "redirect_uri": self.redirect_uri, "scope": "talk_message", "state": state}
            open_browser(AUTH_URL + "?" + urllib.parse.urlencode(params))
            deadline = time.time() + timeout
            while "code" not in result and "error" not in result and time.time() < deadline:
                server.handle_request()
        finally:
            server.server_close()

        if "error" in result:
            if result.get("error") == "access_denied":
                raise KakaoError("카카오 로그인 또는 동의를 취소했습니다.")
            raise KakaoError("카카오 로그인 실패: %s %s" % (result.get("error"),
                                                       result.get("error_description", "")))
        if "code" not in result:
            raise KakaoError("로그인 시간이 초과되었습니다. 다시 시도해 주세요.")
        if result.get("state") != state:
            raise KakaoError("로그인 응답 검증에 실패했습니다. 다시 시도해 주세요.")

        form = {"grant_type": "authorization_code", "client_id": self.rest_api_key,
                "redirect_uri": self.redirect_uri, "code": result["code"]}
        if self.client_secret:
            form["client_secret"] = self.client_secret
        with self._lock:
            self.tokens = None
            self._store_token_response(_post(TOKEN_URL, form))

    # ---- 토큰 갱신
    def _refresh(self):
        form = {"grant_type": "refresh_token", "client_id": self.rest_api_key,
                "refresh_token": self.tokens["refresh_token"]}
        if self.client_secret:
            form["client_secret"] = self.client_secret
        try:
            self._store_token_response(_post(TOKEN_URL, form))
        except KakaoError as e:
            if getattr(e, "status", 0) in (400, 401):
                self._set_tokens(None)          # 리프레시 토큰도 만료 → 다시 로그인
            raise

    def _access_token(self, force_refresh=False):
        if not self.is_logged_in():
            raise KakaoError("카카오 로그인이 필요합니다. '카카오톡 설정' 탭에서 로그인해 주세요.")
        if force_refresh or time.time() > self.tokens.get("expires_at", 0) - 60:
            self._refresh()
        return self.tokens["access_token"]

    # ---- 메시지
    def send_to_me(self, text):
        if len(text) > TEXT_LIMIT:
            text = text[:TEXT_LIMIT - 1] + "…"
        template = {"object_type": "text", "text": text,
                    "link": {"web_url": LINK_URL, "mobile_web_url": LINK_URL},
                    "button_title": "동행복권 열기"}
        form = {"template_object": json.dumps(template, ensure_ascii=False)}
        with self._lock:
            try:
                return _post(MEMO_URL, form, self._access_token())
            except KakaoError as e:
                if getattr(e, "status", 0) != 401:
                    raise
                return _post(MEMO_URL, form, self._access_token(force_refresh=True))

    def logout(self):
        """카카오 쪽 토큰을 만료시키고 이 PC에 저장된 토큰을 지운다."""
        with self._lock:
            try:
                if self.tokens and self.tokens.get("access_token"):
                    _post(LOGOUT_URL, {}, self.tokens["access_token"])
            except KakaoError:
                pass
            finally:
                self._set_tokens(None)

    def forget_everything(self):
        self.logout()
        save_secret(self.CONFIG_FILE, None)
        self.rest_api_key = self.client_secret = ""
        self.port = DEFAULT_PORT
