import json
import re
from collections.abc import Awaitable, Callable

from streamget import KwaiLiveStream
from streamget.requests.async_http import async_req


class KuaishouLiveStream(KwaiLiveStream):
    async def fetch_web_stream_data(
        self,
        url: str,
        process_data: bool = True,
        on_cookies: Callable[[dict[str, str]], Awaitable[None]] | None = None,
    ) -> dict:
        url = url.strip()
        self.pc_headers = self._get_pc_headers() | {"cookie": self.cookies or "", "referer": url}
        response = await async_req(
            url=url, proxy_addr=self.proxy_addr, headers=self.pc_headers, return_cookies=True, include_cookies=True
        )
        if isinstance(response, tuple):
            html, cookies = response
            if cookies and on_cookies is not None:
                await on_cookies(cookies)
        else:
            html = response
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
        live_stream = room_data.get("liveStream") or {}
        play_urls = live_stream.get("playUrls")
        if play_urls:
            if "h264" in play_urls:
                adaptation_set = play_urls["h264"].get("adaptationSet") or {}
            else:
                adaptation_set = play_urls[0].get("adaptationSet") or {}
            representations = [item for item in adaptation_set.get("representation") or [] if item.get("url")]
            if representations:
                result.update({"flv_url_list": representations, "is_live": True})
                return result

        # The page can report living=False even while it supplies a live stream.
        if author.get("living") is False:
            return result
        raise RuntimeError("快手直播间未返回可用直播流，无法确认开播状态")
