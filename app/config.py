"""全局配置：全部通过环境变量覆盖，方便在本地 / 容器 / 私有化环境切换。

设计原则：
1. 代码里不写死任何密钥，密钥只从环境变量（或 .env）读取；
2. 所有路径都是"相对仓库根目录"可解析的，clone 下来即可跑；
3. 供应商可替换：LLM 走 OpenAI 兼容协议，换供应商只改 BASE_URL / MODEL。
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """极简 .env 解析（不引入 python-dotenv，保持零依赖）。

    只支持 `KEY=VALUE` 与 `#` 注释，已存在的环境变量优先，不覆盖。
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


BASE_DIR = Path(__file__).resolve().parent.parent
_load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class Settings:
    """集中配置对象。测试里可以直接改属性做覆盖。"""

    def __init__(self) -> None:
        self.base_dir: Path = BASE_DIR
        # ---- 数据目录 ----
        self.data_dir: Path = Path(_env("REIMB_DATA_DIR", str(BASE_DIR / "data")))
        self.db_path: Path = Path(_env("REIMB_DB_PATH", str(self.data_dir / "reimb.db")))
        self.policies_dir: Path = self.data_dir / "policies"

        # ---- 鉴权 ----
        # 生产必须通过环境变量注入；默认值仅用于本地演示
        self.jwt_secret: str = _env("JWT_SECRET", "dev-only-secret-change-me")
        self.jwt_ttl_hours: int = int(_env("JWT_TTL_HOURS", "12"))

        # ---- LLM（OpenAI 兼容协议，可换供应商）----
        self.llm_base_url: str = _env("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        self.llm_model: str = _env("LLM_MODEL", "deepseek-chat")
        self.llm_api_key: str = _env("LLM_API_KEY", "") or _env("DEEPSEEK_API_KEY", "") or _env("OPENAI_API_KEY", "")
        self.llm_timeout: int = int(_env("LLM_TIMEOUT", "60"))
        self.llm_temperature: float = float(_env("LLM_TEMPERATURE", "0.2"))

        # ---- 检索参数 ----
        self.chunk_size: int = int(_env("CHUNK_SIZE", "500"))
        self.chunk_overlap: int = int(_env("CHUNK_OVERLAP", "50"))
        self.top_k: int = int(_env("RETRIEVAL_TOP_K", "3"))
        self.rrf_k: int = int(_env("RRF_K", "60"))  # RRF 平滑常数，业界常用 60

        # ---- Agent ----
        self.max_agent_rounds: int = int(_env("MAX_AGENT_ROUNDS", "5"))
        self.history_rounds: int = int(_env("HISTORY_ROUNDS", "6"))  # 最近 6 轮历史进上下文

        # ---- 演示账号 ----
        self.seed_admin_user: str = _env("SEED_ADMIN_USER", "admin")
        self.seed_admin_pwd: str = _env("SEED_ADMIN_PWD", "admin123")
        self.seed_employee_user: str = _env("SEED_EMPLOYEE_USER", "zhangwei")
        self.seed_employee_pwd: str = _env("SEED_EMPLOYEE_PWD", "emp123")

    # 企业信息（发票抬头/税号校验用，属于业务配置而非密钥）
    company_name: str = "某某科技有限公司"
    company_tax_id: str = "91110108MA01ABCD2X"

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_api_key)


settings = Settings()
