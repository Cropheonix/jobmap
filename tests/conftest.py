"""测试夹具：使用独立临时数据库，避免污染本地数据。"""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="jobmap_test_")
os.environ["JOBMAP_DB"] = "sqlite:///" + os.path.join(_tmp, "test.db").replace("\\", "/")
