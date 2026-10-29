"""数据源注册表。"""
from backend.providers.base import ProviderCapability, SearchProvider
from backend.providers.sample_provider import SampleProvider


class UserImportProvider(SearchProvider):
    """用户导入：粘贴 URL / 上传 CSV/JSON（M0-M1 可用；URL 抓取在 M2 开放）。"""

    capability = ProviderCapability(
        name="user_import", display_name="用户导入", source_type="user_import",
        status="available", supports_discover=False,
        license_notes="数据由用户自行提供并负责来源合法性；仅保存元数据与原链接。",
    )


class OfficialSiteProvider(SearchProvider):
    """企业官网招聘页抓取（M2）。"""

    capability = ProviderCapability(
        name="official_site", display_name="企业官网招聘页", source_type="official_site",
        status="planned", rate_limit="按域名低并发，遇 429/禁爬即停",
        license_notes="仅访问公开可访问页面，遵守 robots 与站点条款。",
    )


class GovPublicProvider(SearchProvider):
    """政府/高校/事业单位公开招聘公告（M2）。"""

    capability = ProviderCapability(
        name="gov_public", display_name="公开招聘公告", source_type="gov_public",
        status="planned", license_notes="公开公告信息，保留原文链接与发布日期。",
    )


class SearchEngineProvider(SearchProvider):
    """授权搜索 API 发现源（M2，需要用户自备 API Key）。"""

    capability = ProviderCapability(
        name="search_engine", display_name="搜索 API 发现", source_type="aggregator",
        status="planned", requires_key=True,
        license_notes="用户自备合法 API Key；密钥保存在本机，不进仓库。",
    )


class JobBoardPublicProvider(SearchProvider):
    """受限招聘平台：仅引用其公开索引中的标题/链接，不抓取登录内容。"""

    capability = ProviderCapability(
        name="job_boards_public", display_name="招聘平台公开页", source_type="platform_public",
        status="restricted", supports_fetch=False,
        license_notes="不规避登录/验证码/反爬；无法访问时标记 access_restricted，不伪装成功。",
    )


_ALL = [SampleProvider(), UserImportProvider(), OfficialSiteProvider(),
        GovPublicProvider(), SearchEngineProvider(), JobBoardPublicProvider()]

REGISTRY = {p.capability.name: p for p in _ALL}


def list_providers() -> list[dict]:
    return [
        {
            **vars(p.capability),
            "health": p.health_check(),
        }
        for p in _ALL
    ]
