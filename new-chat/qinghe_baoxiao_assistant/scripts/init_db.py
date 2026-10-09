"""
初始化脚本：创建用户账号 + 示例报销单据
运行：python scripts/init_db.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.document_db import DocumentDB
import yaml

with open(PROJECT_ROOT / "config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

db = DocumentDB(str(PROJECT_ROOT / config["paths"]["db_path"]))

# 创建三位主角账号
users = [
    ("emp_001", "小林", "employee", "产品部"),
    ("admin_qiaojie", "乔姐", "admin", "行政部"),
    ("finance_zhoumin", "周敏", "finance", "财务部"),
]
for uid, name, role, dept in users:
    db.add_user(uid, name, role, dept)
    print(f"  ✓ 用户已创建：{name}（{role}）")

# 创建示例单据（小林的出差报销）
docs = [
    ("R-0001", "小林", "emp_001", "差旅费报销", 2850.00, "material_review"),
    ("R-0002", "小林", "emp_001", "办公费报销", 320.50, "dept_approval"),
]
for doc_id, emp_name, emp_id, exp_type, amount, status in docs:
    db.add_document(doc_id, emp_name, emp_id, exp_type, amount, status)
    print(f"  ✓ 单据已创建：{doc_id}（{status}）")

print(f"\n初始化完成！当前共 {db.count_documents()} 条单据记录。")
