import functools
import re

from streamget import StreamData

from ...utils.utils import trace_error_decorator as trace_errors


def trace_error_decorator(func):
    @trace_errors
    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except Exception as exc:
            message = str(exc)
            patterns = {
                "user_deleted": r"(?:用户|账号|帐号)(?:已经|已被|已|被)?注销|account has been deleted",
                "user_not_found": r"(?:用户|账号|帐号)不存在|(?:user|account) (?:does not exist|not found)",
                "user_muted": r"(?:用户|账号|帐号)(?:已经|已被|已|被)?禁言|(?:user|account) (?:has been |is )?muted",
                "user_banned": r"(?:用户|账号|帐号)(?:已经|已被|已|被)?封禁|(?:user|account) (?:has been |is )?banned",
            }
            for status, pattern in patterns.items():
                if re.search(pattern, message, re.IGNORECASE):
                    return StreamData(platform=self.platform, is_live=False, extra={"account_status": status})
            raise

    return wrapper
