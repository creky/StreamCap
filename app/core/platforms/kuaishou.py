import json
import re
from urllib.parse import urlsplit

from streamget import KwaiLiveStream
from streamget.requests.async_http import async_req


class KuaishouLiveStream(KwaiLiveStream):
    async def get_user_info(self, url: str) -> tuple[str | None, bool | None]:
        uid = urlsplit(url).path.rstrip("/").split("/")[-1]
        api = (
            "https://live.kuaishou.com/live_api/baseuser/userinfo/byid"
            f"?__NS_hxfalcon=&caver=2&principalId={uid}"
        )
        response = await async_req(url=api, proxy_addr=self.proxy_addr, headers=self.pc_headers)
        try:
            data = json.loads(response).get("data") or {}
        except json.JSONDecodeError:
            return None, None
        user_info = data.get("userInfo") or {}
        if data.get("result") != 1 or not user_info:
            return None, None
        status = user_info.get("living")
        return user_info.get("name"), status if isinstance(status, bool) else None

    async def fetch_web_stream_data(self, url: str, process_data: bool = True) -> dict:
        url = url.strip()
        self.pc_headers = self._get_pc_headers() | {"cookie": self.cookies or "", "referer": url}
        if self.cookies and self.cookies.strip():
            name, status = await self.get_user_info(url)
            if status is False and name:
                return {"anchor_name": name, "is_live": False, "live_url": url, "type": 2}

        html = await async_req(url=url, proxy_addr=self.proxy_addr, headers=self.pc_headers)
        state = re.search(r"<script>window\.__INITIAL_STATE__=(.*?);\(function\(\)\{var s;", html, re.DOTALL)
        if not state:
            raise RuntimeError("快手直播页未返回直播数据，请检查登录状态或访问限制")

        # The full state contains JavaScript values and generic ban messages; parse only the room data.
        room = re.search(r'(\{"liveStream".*?),"gameInfo', state.group(1), re.DOTALL)
        if not room:
            raise RuntimeError("快手直播页缺少直播间数据，无法确认开播状态")
        try:
            room_data = json.loads(room.group(1) + "}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("快手直播间数据解析失败") from exc

        error = room_data.get("errorType")
        if error:
            raise RuntimeError(f"快手直播间错误：{error.get('title', '')}{error.get('content', '')}")

        author = room_data.get("author") or {}
        result = {
            "type": 2,
            "is_live": False,
            "live_url": url,
            "anchor_name": author.get("name", ""),
        }
        if not result["anchor_name"]:
            raise RuntimeError("快手直播页缺少主播信息，请检查登录状态或访问限制")
        if author.get("living") is False:
            return result

        live_stream = room_data.get("liveStream")
        if not live_stream:
            raise RuntimeError("快手直播间未返回直播流，无法确认开播状态")

        play_urls = live_stream.get("playUrls")
        if play_urls:
            if "h264" in play_urls:
                adaptation_set = play_urls["h264"].get("adaptationSet")
                if not adaptation_set:
                    raise RuntimeError("快手直播间未返回可用直播流，无法确认开播状态")
            else:
                adaptation_set = play_urls[0]["adaptationSet"]
            representations = adaptation_set["representation"]
            if representations:
                result.update({"flv_url_list": representations, "is_live": True})
        if not result["is_live"]:
            raise RuntimeError("快手直播间未返回可用直播流，无法确认开播状态")
        return result
