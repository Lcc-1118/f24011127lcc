"""
青禾报销助手_zxr - 主入口
命令行交互式对话
"""
import sys
import os
import yaml
from pathlib import Path

# 将项目根目录加入 path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.policy_kb import PolicyKnowledgeBase
from app.document_db import DocumentDB
from app.agent import ReimbursementAgent


def load_config() -> dict:
    with open(PROJECT_ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    config = load_config()

    policy_dir = PROJECT_ROOT / config["paths"]["policy_dir"]
    db_path = PROJECT_ROOT / config["paths"]["db_path"]

    kb = PolicyKnowledgeBase(str(policy_dir))
    db = DocumentDB(str(db_path))
    agent = ReimbursementAgent(kb, db, config)

    print("=" * 60)
    print(f"  {config['app']['name']} v{config['app']['version']}")
    print(f"  {config['app']['description']}")
    print("=" * 60)
    print()
    print("可用账号：")
    print("  emp_001        → 小林（普通员工）")
    print("  admin_qiaojie  → 乔姐（行政专员/制度维护者）")
    print("  finance_zhoumin → 周敏（财务专员/业务事实核对者）")
    print()
    print("输入 exit 退出。")
    print("-" * 60)

    while True:
        try:
            query = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not query:
            continue
        if query.lower() in ("exit", "quit", "退出"):
            print("再见！")
            break

        # 登录指令
        if query.startswith("login "):
            user_id = query.replace("login ", "").strip()
            print(f"\n助手：{agent.login(user_id)}")
            continue

        # 授权指令
        if "授权" in query and "单据" in query:
            print(f"\n助手：{agent.authorize_self_query()}")
            continue

        response = agent.chat(query)
        print(f"\n助手：{response}")


if __name__ == "__main__":
    main()
