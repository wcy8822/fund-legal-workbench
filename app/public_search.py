"""Optional public search adapter; callers must authorize a deidentified question.

Configuration is not a connectivity, entitlement, or legal-accuracy check. This
module reads no project records and fetches no result URLs. ``sent_externally``
conservatively records an attempted external request, not confirmed receipt.

Official contracts: https://docs.tavily.com/documentation/api-reference/endpoint/search
and https://docs.tavily.com/documentation/keyless .
"""
import ipaddress
import json
import os
import re
import time
import uuid
from datetime import date
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

import httpx


_ENDPOINT = "https://api.tavily.com/search"
_DOMAINS = ("npc.gov.cn", "csrc.gov.cn", "amac.org.cn", "court.gov.cn", "gov.cn")
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_RESULTS = 8
_MAX_STREAM_SECONDS = 15.0
_PURPOSES = {"current_rules", "as_of", "recent_updates"}
_SECRET_PARAMETER_NAMES = {
    "key", "apikey", "token", "accesstoken", "refreshtoken", "idtoken",
    "auth", "authorization", "bearer", "secret", "clientsecret", "password",
    "passwd", "pwd", "credential", "credentials", "signature", "sig",
    "session", "sessionid", "code", "authcode", "ticket", "accesskey",
    "accesskeyid", "securitytoken", "xamzsignature", "xamzcredential",
    "xamzsecuritytoken", "xgoogsignature", "xgoogcredential",
}
_EVIDENCE_WARNINGS = [
    "结果仅为搜索候选与摘要；尚未访问原文，也未核验准确引文、公布/施行时间或效力。",
    "检索时间、页面标注日期和法律判断时点分别记录；没有结果不等于法律或案例不存在。",
]


class _ResponseTooLarge(Exception):
    pass


class _StreamDeadlineExceeded(Exception):
    pass


def _configuration():
    mode = os.environ.get("FWB_PUBLIC_SEARCH_MODE", "disabled").strip().lower()
    config = {"provider": "tavily", "mode": "disabled", "available": False,
              "status": "disabled", "notice": "公开搜索默认关闭；尚未执行任何外部检索。"}
    if mode not in {"disabled", "keyless", "api_key"}:
        config.update(status="invalid_mode", notice="公开搜索模式无效，保持关闭；请检查服务端配置。")
        return config, ""
    config["mode"] = mode
    if mode == "disabled":
        return config, ""
    if mode == "keyless":
        config.update(available=True, status="configured_unverified",
                      notice="已选择无密钥模式；提供方有限流，当前配置尚未证明实际可用或适合处理公司资料。")
        return config, ""
    key = os.environ.get("FWB_TAVILY_API_KEY", "").strip()
    if not key:
        config.update(status="missing_api_key", notice="已选择 API key 模式，但尚未配置服务端密钥；不会发起请求。")
        return config, ""
    if len(key) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in key):
        config.update(status="invalid_api_key", notice="服务端密钥格式不可用于请求；不会发起请求。")
        return config, ""
    config.update(available=True, status="configured_unverified",
                  notice="已配置 API key 模式；尚未验证账号授权、配额、连通性或数据处理条件。")
    return config, key


def get_public_search_config() -> dict:
    """Return configuration metadata only; never expose a credential."""
    config, _ = _configuration()
    return config


def _coverage(purpose: str, sent: bool) -> str:
    if not sent:
        return "未执行公开检索；没有形成外部搜索覆盖结论。"
    scope = "只请求所列官方域名的通用搜索，最多保留 8 条候选；未逐条取回原文，结果并非穷尽。"
    if purpose == "recent_updates":
        return scope + "最近一个月是提供方的检索时间筛选，不是法律施行或失效时间筛选。"
    return scope + "未按发布时间排除较早材料；仍须核对判断时点有效的规则及后续修订。"


def _outcome(status: str, sent: bool, purpose: str, *, results=None, warnings=None) -> dict:
    return {"provider": "tavily", "status": status, "sent_externally": sent,
            "results": results or [], "warnings": list(warnings or []),
            "coverage": _coverage(purpose, sent)}


def _public_payload(question: str, jurisdiction: str, as_of: str, purpose: str):
    if not all(isinstance(value, str) for value in (question, jurisdiction, as_of, purpose)):
        return None
    if not 1 <= len(question.strip()) <= 2000 or not 1 <= len(jurisdiction.strip()) <= 80:
        return None
    if purpose not in _PURPOSES:
        return None
    try:
        if date.fromisoformat(as_of).isoformat() != as_of:
            return None
    except ValueError:
        return None
    # These are the only user-supplied values sent to the provider. The route is
    # responsible for obtaining disclosure confirmation before calling here.
    query = " ".join(question.split()) + f" 法域：{' '.join(jurisdiction.split())}；判断时点：{as_of}。"
    payload = {"query": query, "topic": "general", "search_depth": "basic",
               "max_results": _MAX_RESULTS, "include_domains": list(_DOMAINS),
               "include_domains_mode": "restrict", "include_answer": False,
               "include_raw_content": False, "include_images": False,
               "include_published_date": True, "auto_parameters": False}
    if purpose == "recent_updates":
        payload["time_range"] = "month"
    return payload


def _has_secret_parameter(query: str) -> bool:
    try:
        pairs = parse_qsl(query, keep_blank_values=True, max_num_fields=100)
    except ValueError:
        return True
    for name, _ in pairs:
        normalized = re.sub(r"[^a-z0-9]", "", unquote(name).lower())
        if (normalized in _SECRET_PARAMETER_NAMES or normalized.endswith("token")
                or "secret" in normalized or "password" in normalized):
            return True
    return False


def _official_url(value, secret: str = "") -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
        return ""
    value = value.strip()
    if (not value or "\\" in value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
            or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", value, re.I)):
        return ""
    if secret and (secret in value or secret in unquote(value)):
        return ""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or not host or len(host) > 253
                or not re.fullmatch(r"[a-z0-9.-]+", host)):
            return ""
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
               for label in host.split(".")):
            return ""
        try:
            ipaddress.ip_address(host)
            return ""
        except ValueError:
            pass
        if not any(host == domain or host.endswith("." + domain) for domain in _DOMAINS):
            return ""
        if _has_secret_parameter(parsed.query) or _has_secret_parameter(parsed.fragment):
            return ""
        # Reject credential-like parameter names even when the whole query was
        # encoded once more. Do not strip such parameters and invent a new link.
        if _has_secret_parameter(unquote(parsed.query)) or _has_secret_parameter(unquote(parsed.fragment)):
            return ""
        return urlunsplit(("https", host, parsed.path, parsed.query, parsed.fragment))
    except ValueError:
        return ""


def _text(value, limit: int, secret: str = "") -> str:
    if not isinstance(value, str):
        return ""
    if secret:
        value = value.replace(secret, "[已隐去]")
    return "".join(char for char in value if ord(char) >= 32 or char in "\n\t").strip()[:limit]


def _http_failure(code: int):
    if code in (401, 403):
        return "authentication_failed", "公开搜索服务拒绝认证或访问；请核对所选模式与账号授权，未自动重试。"
    if code in (429, 432, 433, 434):
        return "rate_limited", "公开搜索服务触发限流、配额或临时访问限制；未自动重试，未将其解释为没有法律依据。"
    if 300 <= code < 400:
        return "error", "公开搜索服务返回重定向；适配器没有跟随，也没有取得可用结果。"
    return "error", "公开搜索服务未返回成功响应；没有取得可用结果，请稍后或前往官方入口手工检索。"


def search_public(question: str, jurisdiction: str, as_of: str, purpose: str) -> dict:
    """Search only the supplied public question; return unverified candidates.

    There are no retries, project lookups, result-URL fetches, or model calls.
    Enabled mode and a present key do not establish permission to send data.
    """
    config, key = _configuration()
    if not config["available"]:
        return _outcome("not_configured", False, purpose, warnings=[config["notice"]])
    payload = _public_payload(question, jurisdiction, as_of, purpose)
    if payload is None:
        return _outcome("error", False, purpose,
                        warnings=["公开法律问题、法域、判断时点或检索用途无效；没有发起外部请求。"])
    headers = {"Accept": "application/json", "Accept-Encoding": "identity",
               "Content-Type": "application/json"}
    if config["mode"] == "api_key":
        headers["Authorization"] = "Bearer " + key
    else:
        headers["X-Tavily-Access-Mode"] = "keyless"
    sent = False
    try:
        transport = httpx.HTTPTransport(retries=0, trust_env=False)
        timeout = httpx.Timeout(10.0, connect=5.0, read=10.0, write=10.0, pool=5.0)
        with httpx.Client(transport=transport, timeout=timeout, follow_redirects=False,
                          trust_env=False, headers=headers) as client:
            sent = True
            deadline = time.monotonic() + _MAX_STREAM_SECONDS
            with client.stream("POST", _ENDPOINT, json=payload) as response:
                if response.status_code != 200:
                    status, warning = _http_failure(response.status_code)
                    return _outcome(status, sent, purpose, warnings=[warning])
                # Identity encoding plus iter_raw keeps both compressed and
                # decoded expansion from evading the response-size boundary.
                if response.headers.get("content-encoding", "").lower().strip() not in ("", "identity"):
                    return _outcome("error", sent, purpose,
                                    warnings=["搜索服务返回了当前适配器不接受的压缩响应；未解析结果。"])
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > _MAX_RESPONSE_BYTES:
                    raise _ResponseTooLarge
                body = bytearray()
                for chunk in response.iter_raw():
                    if time.monotonic() > deadline:
                        raise _StreamDeadlineExceeded
                    if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise _ResponseTooLarge
                    body.extend(chunk)
            data = json.loads(body)
        if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("results"), list):
            return _outcome("error", sent, purpose,
                            warnings=["搜索服务没有返回预期的候选结果结构；没有将该响应视为有效检索。"])
    except _ResponseTooLarge:
        return _outcome("error", sent, purpose, warnings=["搜索响应超过 1 MiB 限制，已停止读取；没有采纳部分结果。"])
    except (httpx.TimeoutException, _StreamDeadlineExceeded):
        return _outcome("error", sent, purpose, warnings=["公开搜索请求或响应读取超时；未重试，没有取得完整结果。"])
    except (httpx.HTTPError, OSError, ValueError, UnicodeError, RecursionError):
        return _outcome("error", sent, purpose,
                        warnings=["公开搜索连接或响应解析失败；未返回提供方原始错误、响应正文或凭据。"])
    results, seen = [], set()
    discarded = 0
    for item in data["results"][:64]:
        if not isinstance(item, dict):
            discarded += 1
            continue
        url = _official_url(item.get("url"), key)
        if not url or url in seen:
            discarded += 1
            continue
        seen.add(url)
        results.append({
            "id": str(uuid.uuid4()),
            "title": _text(item.get("title"), 500, key) or "官方来源候选（标题待核对）",
            "kind": "public_candidate", "locator": "搜索摘要",
            "excerpt": _text(item.get("content"), 5000, key), "url": url,
            "reported_published_at": _text(item.get("published_date"), 100, key),
            "evidence_status": "unverified_search_excerpt",
        })
        if len(results) == _MAX_RESULTS:
            break
    warnings = list(_EVIDENCE_WARNINGS)
    if discarded:
        warnings.append(f"已跳过 {discarded} 条格式不符、重复或不符合链接要求的候选；未访问这些链接。")
    if not results:
        warnings.append("本次没有取得符合范围的可用候选；这不表示不存在相关法律、案例或内部依据。")
    return _outcome("completed" if results else "no_results", sent, purpose,
                    results=results, warnings=warnings)
