"""数据源插件基类（对应方案 4.2 插件接口）。

每个数据源独立声明能力；`status` 说明当前可用性：
- available：本版本可立即使用；
- planned：M2 里程碑接入（需配置密钥/遵守来源许可）；
- restricted：仅允许在许可范围内引用公开索引，不做抓取。
"""
from dataclasses import dataclass, field


@dataclass
class ProviderCapability:
    name: str
    display_name: str
    source_type: str
    status: str = "planned"
    requires_key: bool = False
    supports_discover: bool = True
    supports_fetch: bool = False
    rate_limit: str = ""
    license_notes: str = ""
    supported_regions: list = field(default_factory=lambda: ["cn"])


class SearchProvider:
    """所有数据源插件的基类。"""

    capability: ProviderCapability

    def discover(self, query: dict, on_step=None, cancelled=None) -> dict:
        """执行发现：返回 {discovered, extracted, merged, geo_located, errors}。"""
        raise NotImplementedError

    def check_policy(self, url: str) -> bool:
        """检查 URL 是否处于本源允许访问的范围（robots/许可声明等）。"""
        return False

    def health_check(self) -> dict:
        return {"name": self.capability.display_name, "status": self.capability.status, "ok": None}
