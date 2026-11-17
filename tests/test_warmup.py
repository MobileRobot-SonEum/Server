from app.config import LLMSettings, Settings
from app.llm.warmup import warmup_targets


def test_warmup_only_self_hosted_and_dedup():
    s = Settings(_env_file=None,
                 teacher_llm=LLMSettings(base_url="http://ollama:11434/v1", model="qwen3:14b"),
                 child_llm=LLMSettings(base_url="http://ollama:11434/v1/", model="qwen3:14b"),
                 child_allowed_llm_hosts="ollama")
    targets = warmup_targets(s)
    assert [t.model for t in targets] == ["qwen3:14b"]  # 공용 모델은 한 번만
    assert targets[0].timeout_s == 300.0


def test_warmup_skips_external_teacher():
    s = Settings(_env_file=None,
                 teacher_llm=LLMSettings(base_url="https://generativelanguage.googleapis.com/v1beta/openai/", model="g"),
                 child_llm=LLMSettings(base_url="http://localhost:11434/v1", model="qwen3:4b"))
    assert [t.model for t in warmup_targets(s)] == ["qwen3:4b"]
