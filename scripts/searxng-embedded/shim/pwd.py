"""Windows 垫片：SearXNG valkeydb 顶层 `import pwd`（Unix 专用）仅为可导入；
本项目关闭 limiter/不配置 valkey，运行期不会调用。仅限嵌入式自托管场景使用。
"""
def getpwnam(*a, **k):
    raise NotImplementedError("pwd shim on Windows")

def getpwuid(*a, **k):
    raise NotImplementedError("pwd shim on Windows")
