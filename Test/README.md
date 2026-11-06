# Test · 测试交付说明

> 对应任务要求：`Test: 1、正常/边界/对抗/权限/故障场景的量化测试方案（md + docx）；2、每个场景一份可视化测试过程文档；3、MVP 上线部署网站地址（文档）`

## 目录

| 路径 | 内容 |
|---|---|
| `01-测试方案/测试方案.md` | 测试方案源文件（范围、策略、56 条用例分布、指标、出口准则） |
| `01-测试方案/测试方案.docx` | 同上，排版版（含 2 张图） |
| `02-场景过程/正常场景测试.docx` | 12 条主路径用例 + 过程图 + 结果图 |
| `02-场景过程/边界场景测试.docx` | 13 条阈值/时效/输入用例 + 缺陷 D-02 |
| `02-场景过程/对抗场景测试.docx` | 12 条注入/诱导/域外/数据套取用例 |
| `02-场景过程/权限场景测试.docx` | 10 条认证/授权/越权/令牌用例 |
| `02-场景过程/故障场景测试.docx` | 9 条降级/非法输入/服务不可用例 |
| `03-部署/上线部署地址与访问说明.md` | 访问地址、启动方式、验证步骤 |
| `03-部署/上线部署地址与访问说明.docx` | 同上，排版版 |
| `_scripts/run_tests.py` | 真实执行脚本（urllib，零依赖），输出 `_runtime/results.json` |
| `_scripts/bench.py` | 延迟基线脚本，输出 `_runtime/latency.json` |
| `_scripts/gen_test_docs.py` | 由 results/latency 生成本目录全部 docx |
| `_runtime/` | 测试库、结果 json、服务 PID（可清理） |

## 结果摘要

| 场景 | 通过/总数 | 结论 |
|---|---|---|
| 正常 | 12/12 | 主路径与确认闸门符合预期 |
| 边界 | 12/13 | 1 个 P2 缺陷 D-02（发票校验未叠加住宿标准） |
| 对抗 | 12/12 | 不编造、不越权、未确认不落库 |
| 权限 | 10/10 | 越权 404、登出失效、后台双守卫 |
| 故障 | 9/9 | 模型不可达 2.1s 降级；非法输入均 4xx |

**合计 55/56，通过率 98.2%。**

## 复现步骤

```bash
# 1) 起隔离实例（不污染交付库）
$env:REIMB_DATA_DIR="Test/_runtime/data"
$env:REIMB_DB_PATH="Test/_runtime/data/test.db"
python run.py --port 8124 --no-browser

# 2) 执行
python Test/_scripts/run_tests.py --base http://127.0.0.1:8124
python Test/_scripts/bench.py --base http://127.0.0.1:8124

# 3) 重生成文档
python Test/_scripts/gen_test_docs.py
```
