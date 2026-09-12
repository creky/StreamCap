import json
import re

from streamget import KwaiLiveStream
from streamget.requests.async_http import async_req


class KuaishouLiveStream(KwaiLiveStream):
    async def fetch_web_stream_data(self, url: str, process_data: bool = True) -> dict:
        self.pc_headers = self._get_pc_headers() | {"cookie": self.cookies or "", "referer": url}
        if self.cookies and self.cookies.strip():
            name, status = await self.get_user_info(url)
            if not status:
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

        live_stream = room_data.get("liveStream")
        if not live_stream:
            raise RuntimeError("快手直播间未返回直播流，无法确认开播状态")

        result = {
            "type": 2,
            "is_live": False,
            "live_url": url,
            "anchor_name": room_data["author"].get("name", ""),
        }
        play_urls = live_stream.get("playUrls")
        if play_urls:
            if "h264" in play_urls:
                adaptation_set = play_urls["h264"].get("adaptationSet")
                if not adaptation_set:
                    return result
            else:
                adaptation_set = play_urls[0]["adaptationSet"]
            representations = adaptation_set["representation"]
            if representations:
                result.update({"flv_url_list": representations, "is_live": True})
        return result
