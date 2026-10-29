# NOTICE — 第三方组件与数据来源说明

JobMap 遵循 MIT 许可证分发代码。本文件说明仓库内随包分发的第三方资源及其许可边界。

## 随包分发的第三方前端资源

| 组件 | 版本 | 许可证 | 位置 |
|---|---|---|---|
| Vue.js | 3.4.x | MIT | `frontend/vendor/vue.global.prod.js` |
| Leaflet | 1.9.4 | BSD-2-Clause | `frontend/vendor/leaflet/` |

两者均为其官方发布的产物文件，未做修改。

## 在线地图底图

- 默认底图使用 OpenStreetMap 标准瓦片服务，遵循其[瓦片使用政策](https://operations.osmfoundation.org/policies/tiles/)：
  禁止批量预取或离线抓取瓦片，正常交互浏览可用，必须显示归属信息。
- 底图源可在「设置」中替换为任何你有合法使用权的瓦片服务（含 GCJ-02 坐标系底图，程序会自动做 WGS-84 ↔ GCJ-02 换算）。
- **离线说明：** 离线时底图不可用（灰色画布），职位列表、地址文本与导出功能不受影响。

## 职位数据

- 仓库内置的 `sample-data/sample_jobs.json` 为**虚构演示数据**：公司、薪资、地址、链接均非真实，
  仅供功能演示，已在数据库与界面中标记 `演示`。不得将演示数据当作真实招聘信息引用。
- 用户通过导入功能写入的数据归用户所有，保存在本机 SQLite 数据库（`data/jobmap.db`），不上传任何服务器。
- 本工具不抓取需登录/验证码/付费才能访问的平台内容，不规避任何访问控制；外部来源抓取能力（M2 里程碑）
  仅在来源许可允许的范围内启用，并保留来源归属与原文链接。

## Python 依赖

fastapi / uvicorn / SQLAlchemy / Jinja2 / httpx / pydantic 等均按其各自开源许可证使用，详见各项目主页。
