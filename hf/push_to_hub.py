"""Upload the AgentOrchBench dataset to the Hugging Face Hub.

    pip install huggingface_hub
    export HF_TOKEN=...                       # a token with write access to your account
    python hf/build_dataset.py
    python hf/push_to_hub.py --repo Sajib-006/AgentOrchBench            # private by default
    python hf/push_to_hub.py --repo Sajib-006/AgentOrchBench --public   # when ready to release
"""
import argparse
from pathlib import Path

from huggingface_hub import HfApi

ap = argparse.ArgumentParser()
ap.add_argument("--repo", required=True)
ap.add_argument("--public", action="store_true")
a = ap.parse_args()

api = HfApi()
api.create_repo(a.repo, repo_type="dataset", private=not a.public, exist_ok=True)
if a.public:
    api.update_repo_settings(a.repo, repo_type="dataset", private=False)
api.upload_folder(repo_id=a.repo, repo_type="dataset",
                  folder_path=Path(__file__).resolve().parent / "agentorchbench",
                  commit_message="AgentOrchBench dataset")
print(f"https://huggingface.co/datasets/{a.repo}")
