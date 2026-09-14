import asyncio
import hashlib
import json
import re
import time
import weakref
from html import unescape
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import httpx
from streamget import DouyinLiveStream as BaseDouyinLiveStream
from streamget.platforms.douyin.ab_sign import ab_sign
from streamget.utils import handle_proxy_addr

from ...utils.logger import logger


class DouyinLiveStream(BaseDouyinLiveStream):
    _states = weakref.WeakKeyDictionary()

    def _state(self):
        loop = asyncio.get_running_loop()
        state = self._states.setdefault(loop, {"requests": {}, "users": {}})
        context = hashlib.sha256(f"{self.proxy_addr}|{self.cookies}".encode()).hexdigest()
        return state, context

    async def _request(self, client, url, source, headers, params=None):
        target = urlsplit(url)
        request_id = hashlib.sha256((url + urlencode(params or {})).encode()).hexdigest()[:10]
        started = time.monotonic()
        logger.info(
            "Douyin request start: source={}, request={}, host={}, path={}, cookie_present={}, proxy_present={}",
            source,
            request_id,
            target.hostname,
            target.path,
            bool(self.cookies),
            bool(self.proxy_addr),
        )
        try:
            response = await client.get(url, headers=headers, params=params, follow_redirects=True)
            body = response.text
            final = urlsplit(str(response.url))
            logger.info(
                "Douyin response: source={}, request={}, http={}, host={}, path={}, bytes={}, "
                "elapsed_ms={}, redirects={}, body_sha256={}, unique_id_field={}, challenge_hint={}",
                source,
                request_id,
                response.status_code,
                final.hostname,
                final.path,
                len(response.content),
                int((time.monotonic() - started) * 1000),
                len(response.history),
                hashlib.sha256(response.content).hexdigest()[:16],
                bool(re.search(r"unique_id|uniqueId", body)),
                any(marker in body.lower() for marker in ("验证码", "安全验证", "verifycenter", "captcha")),
            )
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            logger.warning(
                "Douyin request failed: source={}, request={}, error={}", source, request_id, type(exc).__name__
            )
            raise RuntimeError(f"抖音 {source} 请求失败：{type(exc).__name__}") from None

    @staticmethod
    def _page_users(text, sec_uid):
        # Decode data, never execute page scripts; only accept the requested user's object.
        fragments = [unescape(unquote(text))]
        decoder = json.JSONDecoder()
        for fragment in fragments:
            for match in re.finditer(r'[{"]', fragment):
                try:
                    value, _ = decoder.raw_decode(fragment, match.start())
                except ValueError:
                    continue
                if isinstance(value, str) and "sec" in value and "{" in value and value not in fragments:
                    if len(fragments) < 8:
                        fragments.append(value)
                if isinstance(value, dict):
                    user_sec = value.get("sec_uid") or value.get("secUid") or value.get("sec_user_id")
                    if sec_uid and user_sec == sec_uid:
                        yield value

    def _remember_user(self, user, source, sec_uid=None):
        sec_uid = user.get("sec_uid") or user.get("secUid") or sec_uid
        unique_id = user.get("unique_id") or user.get("uniqueId")
        if unique_id and sec_uid:
            state, context = self._state()
            now = time.monotonic()
            state["users"] = {key: value for key, value in state["users"].items() if value[0] > now}
            state["users"][(context, sec_uid)] = (now + 21600, str(unique_id))
            logger.info("Douyin unique_id resolved: source={}, sec_uid={}, unique_id={}", source, sec_uid, unique_id)
        return str(unique_id) if unique_id else None

    async def _profile(self, client, sec_uid, source):
        profile_url = "https://www.douyin.com/user/" + sec_uid
        headers = self.pc_headers.copy() | {"referer": profile_url}
        try:
            if source == "profile_api":
                params = {
                    "device_platform": "webapp",
                    "aid": "6383",
                    "channel": "channel_pc_web",
                    "sec_user_id": sec_uid,
                    "publish_video_strategy_type": "2",
                    "personal_center_strategy_type": "1",
                    "browser_language": "zh-CN",
                    "browser_platform": "Win32",
                    "browser_name": "Chrome",
                    "browser_version": "141.0.0.0",
                }
                query = urlencode(params)
                params["a_bogus"] = ab_sign(query, headers["user-agent"])
                response = await self._request(
                    client,
                    "https://www.douyin.com/aweme/v1/web/user/profile/other/",
                    source,
                    headers,
                    params,
                )
                data = response.json()
                user = data.get("user") or {}
                logger.info(
                    "Douyin profile result: source={}, status_code={}, user_present={}, unique_id_present={}",
                    source,
                    data.get("status_code"),
                    bool(user),
                    bool(user.get("unique_id")),
                )
                if user.get("sec_uid") == sec_uid:
                    return user
            else:
                url = profile_url if source == "profile_page" else "https://www.iesdouyin.com/share/user/" + sec_uid
                if source == "share_profile":
                    headers = self.mobile_headers.copy()
                response = await self._request(client, url, source, headers)
                users = list(self._page_users(response.text, sec_uid))
                if users:
                    return max(users, key=lambda user: bool(user.get("unique_id") or user.get("uniqueId")))
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Douyin profile unavailable: source={}, sec_uid={}, error={}", source, sec_uid, type(exc).__name__
            )
        logger.warning("Douyin unique_id missing: source={}, sec_uid={}, trying_next_source=true", source, sec_uid)
        return {}

    async def _room(self, client, web_rid=None, room_id=None, sec_uid=None):
        headers = self.pc_headers.copy() if web_rid else self.mobile_headers.copy()
        if web_rid:
            source = "web_room"
            url = "https://live.douyin.com/webcast/room/web/enter/"
            params = {
                "aid": "6383",
                "app_name": "douyin_web",
                "live_id": "1",
                "device_platform": "web",
                "language": "zh-CN",
                "browser_language": "zh-CN",
                "browser_platform": "Win32",
                "browser_name": "Chrome",
                "browser_version": "141.0.0.0",
                "web_rid": web_rid,
                "enter_from": "web_live",
                "is_need_double_stream": "false",
                "msToken": "",
            }
        else:
            source = "reflow_room"
            url = "https://webcast.amemv.com/webcast/room/reflow/info/"
            params = {
                "type_id": "0",
                "live_id": "1",
                "room_id": room_id,
                "sec_user_id": sec_uid,
                "version_code": "99.99.99",
                "app_id": "1128",
                "is_need_double_stream": "true",
            }
        params["a_bogus"] = ab_sign(urlencode(params), headers["user-agent"])
        try:
            if web_rid and not self.cookies:
                # Preserve Streamget's anonymous web session instead of replacing its Cookie.
                logger.info("Douyin request start: source=web_room_compat, web_rid={}, cookie_present=false", web_rid)
                data = await super()._get_web_stream_data(web_rid, process_data=False)
            else:
                response = await self._request(client, url, source, headers, params)
                data = response.json()
            payload = data.get("data") or {}
            room = (payload.get("data") or [{}])[0] if web_rid else payload.get("room") or {}
            owner = room.get("owner") or {}
            user = payload.get("user") or {}
            if user and (not owner or user.get("sec_uid") == owner.get("sec_uid")):
                owner = user | owner
                if not owner.get("unique_id") and user.get("unique_id"):
                    owner["unique_id"] = user["unique_id"]
            logger.info(
                "Douyin room result: source={}, status_code={}, room_present={}, unique_id_present={}",
                source,
                data.get("status_code"),
                bool(room),
                bool(owner.get("unique_id")),
            )
            if room and room.get("status") in (2, 4):
                if sec_uid and owner.get("sec_uid") and owner["sec_uid"] != sec_uid:
                    logger.warning("Douyin room rejected: source={}, reason=owner_sec_uid_mismatch", source)
                    return None
                if sec_uid and not owner.get("sec_uid"):
                    owner["sec_uid"] = sec_uid
                room["owner"] = owner
                room["anchor_name"] = owner.get("nickname")
                rid = web_rid or owner.get("web_rid")
                room["live_url"] = "https://live.douyin.com/" + str(rid) if rid else None
                self._remember_user(owner, source, sec_uid)
                return room
        except (RuntimeError, ValueError) as exc:
            logger.warning("Douyin room unavailable: source={}, error={}", source, type(exc).__name__)
        return None

    async def _resolve(self, url):
        parts = urlsplit(url)
        sec_uid = None
        user = {}
        async with httpx.AsyncClient(proxy=handle_proxy_addr(self.proxy_addr), timeout=10) as client:
            if parts.hostname == "live.douyin.com":
                room = await self._room(client, web_rid=parts.path.rstrip("/").rsplit("/", 1)[-1])
                if room:
                    return room, room.get("owner") or {}
            else:
                response = await self._request(client, url, "share_redirect", self.pc_headers.copy())
                parts = urlsplit(str(response.url))
                query = parse_qs(parts.query)
                sec_uid = (query.get("sec_user_id") or [None])[0]
                if "/user/" in parts.path:
                    sec_uid = parts.path.rstrip("/").rsplit("/", 1)[-1]
                if parts.hostname == "live.douyin.com":
                    room = await self._room(client, web_rid=parts.path.rstrip("/").rsplit("/", 1)[-1])
                    if room:
                        return room, room.get("owner") or {}
                if "reflow/" in parts.path and sec_uid:
                    room = await self._room(client, room_id=parts.path.rstrip("/").rsplit("/", 1)[-1], sec_uid=sec_uid)
                    if room:
                        return room, room.get("owner") or {}
                for candidate in self._page_users(response.text, sec_uid):
                    user.update({key: value for key, value in candidate.items() if value is not None and value != ""})
                self._remember_user(user, "share_page", sec_uid)
            if sec_uid:
                state, context = self._state()
                cached = state["users"].get((context, sec_uid))
                if cached and cached[0] > time.monotonic() and not (user.get("unique_id") or user.get("uniqueId")):
                    user["unique_id"] = cached[1]
                    logger.info("Douyin unique_id resolved: source=cache, sec_uid={}", sec_uid)
                attempted = set()
                for source in ("share_page", "profile_api", "profile_page", "share_profile"):
                    if source != "share_page":
                        candidate = await self._profile(client, sec_uid, source)
                        user.update(
                            {key: value for key, value in candidate.items() if value is not None and value != ""}
                        )
                        self._remember_user(user, source, sec_uid)
                    web_rid = user.get("web_rid") or user.get("webRid")
                    room_id = user.get("room_id_str") or user.get("room_id")
                    room_data = user.get("room_data")
                    if isinstance(room_data, str):
                        try:
                            room_data = json.loads(room_data)
                        except ValueError:
                            room_data = None
                    if isinstance(room_data, dict):
                        room_id = room_id or room_data.get("id_str") or room_data.get("id")
                        web_rid = web_rid or (room_data.get("owner") or {}).get("web_rid")
                    for kind, identifier in (("web_rid", web_rid), ("room_id", room_id)):
                        if identifier and str(identifier) != "0" and (kind, str(identifier)) not in attempted:
                            attempted.add((kind, str(identifier)))
                            room = await self._room(client, **{kind: str(identifier)}, sec_uid=sec_uid)
                            if room:
                                return room, (room.get("owner") or {}) | user
                    # Retain compatibility with profile links that only expose a public Douyin number.
                    unique_id = user.get("unique_id") or user.get("uniqueId")
                    if unique_id and ("web_rid", str(unique_id)) not in attempted:
                        attempted.add(("web_rid", str(unique_id)))
                        room = await self._room(client, web_rid=str(unique_id), sec_uid=sec_uid)
                        if room:
                            return room, (room.get("owner") or {}) | user
            logger.warning(
                "Douyin room unresolved: sec_uid_present={}, unique_id_present={}",
                bool(sec_uid),
                bool(user.get("unique_id") or user.get("uniqueId")),
            )
            return None, user | ({"sec_uid": sec_uid} if sec_uid else {})

    async def resolve(self, url):
        state, context = self._state()
        key = (context, url)
        now = time.monotonic()
        state["requests"] = {
            key: value for key, value in state["requests"].items() if not value[1].done() or value[0] > now
        }
        entry = state["requests"].get(key)
        if entry is None:

            async def run():
                try:
                    async with asyncio.timeout(60):
                        return await self._resolve(url)
                except Exception:
                    state["requests"].pop(key, None)
                    raise

            task = asyncio.create_task(run())
            # Observe failures even if the UI cancels its waiter during a shared request.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            entry = (now + 15, task)
            state["requests"][key] = entry
        else:
            logger.debug("Douyin reuse room request: pending={}", not entry[1].done())
        return await asyncio.shield(entry[1])

    async def get_unique_id(self, url):
        logger.info("Douyin unique_id lookup start: url={}", urlsplit(url)._replace(query="", fragment="").geturl())
        room, user = await self.resolve(url)
        unique_id = self._remember_user(user, "room_or_profile")
        sec_uid = user.get("sec_uid") or user.get("secUid")
        if not unique_id and sec_uid:
            state, context = self._state()
            cached = state["users"].get((context, sec_uid))
            if cached and cached[0] > time.monotonic():
                logger.info("Douyin unique_id resolved: source=cache, sec_uid={}", sec_uid)
                return cached[1]
            async with httpx.AsyncClient(proxy=handle_proxy_addr(self.proxy_addr), timeout=8) as client:
                for source in ("profile_api", "profile_page", "share_profile") if room else ():
                    candidate = await self._profile(client, sec_uid, source)
                    unique_id = self._remember_user(candidate, source, sec_uid)
                    if unique_id:
                        break
        logger.log(
            "INFO" if unique_id else "WARNING",
            "Douyin unique_id lookup end: resolved={}, room_available={}, sec_uid_present={}",
            bool(unique_id),
            bool(room),
            bool(sec_uid),
        )
        return unique_id

    async def fetch_app_stream_data(self, url, process_data=True, stream_orientation=1):
        room, user = await self.resolve(url)
        if not room:
            raise RuntimeError("抖音未返回可用直播间数据，可能受到访问限制；请查看 Douyin request/response 日志")
        room = dict(room)
        room["owner"] = user | {
            key: value for key, value in (room.get("owner") or {}).items() if value is not None and value != ""
        }
        if not process_data or room.get("status") == 4:
            return room
        stream = dict(room.get("stream_url") or {})
        origin_data = ((stream.get("live_core_sdk_data") or {}).get("pull_data") or {}).get("stream_data")
        if origin_data:
            origin = (json.loads(origin_data).get("data") or {}).get("origin", {}).get("main")
            if origin:
                codec = json.loads(origin.get("sdk_params") or "{}").get("VCodec") or ""
                for field, key in (("hls_pull_url_map", "hls"), ("flv_pull_url", "flv")):
                    if origin.get(key):
                        stream[field] = {"ORIGIN": origin[key] + "&codec=" + codec, **(stream.get(field) or {})}
        if not stream.get("hls_pull_url_map") or not stream.get("flv_pull_url"):
            raise RuntimeError("抖音直播间正在直播，但未返回可用直播流，请检查访问限制")
        room.update(stream_url=stream, stream_orientation=1)
        return room

    async def fetch_web_stream_data(self, url, process_data=True):
        return await self.fetch_app_stream_data(url, process_data)
