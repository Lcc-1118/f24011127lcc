"""
自动化测试脚本：验证四位主角的核心流程
运行：python scripts/test_flows.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml
from app.policy_kb import PolicyKnowledgeBase
from app.document_db import DocumentDB
from app.agent import ReimbursementAgent

with open(PROJECT_ROOT / "config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

kb = PolicyKnowledgeBase(str(PROJECT_ROOT / config["paths"]["policy_dir"]))
db = DocumentDB(str(PROJECT_ROOT / config["paths"]["db_path"]))
agent = ReimbursementAgent(kb, db, config)

SEP = "=" * 60

def show(title, response):
    print(f"\n{SEP}")
    print(f"【测试】{title}")
    print(SEP)
    print(response)

# ===== 角色1：小林（员工）=====
show("小林登录", agent.login("emp_001"))

show("小林问：报销需要什么材料？", agent.chat("报销需要准备什么材料？"))

show("小林问：出差住宿标准是多少？", agent.chat("出差住宿标准是多少？"))

show("小林问：我的报销到哪一步了？（未授权）", agent.chat("我的报销到哪一步了？"))

show("小林授权", agent.authorize_self_query())

show("小林问：我的报销到哪一步了？（已授权）", agent.chat("我的报销到哪一步了？"))

# ===== 角色2：乔姐（行政/制度维护）=====
show("乔姐登录", agent.login("admin_qiaojie"))

show("乔姐查看制度列表", agent.chat("发布制度"))

# ===== 角色3：周敏（财务/业务事实核对）=====
show("周敏登录", agent.login("finance_zhoumin"))

show("周敏查看所有单据", agent.chat("查看所有单据"))

show("周敏查指定单据 R-0001", agent.chat("R-0001状态怎么样？"))

print(f"\n{SEP}")
print("全部测试完成！")
print(SEP)
