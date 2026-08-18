#!/usr/bin/env python3
"""Publish nevertwice-embed to the Hugging Face Hub - one command, owner-authenticated.

    huggingface-cli login          # once (write token)
    python hf_publish.py           # uploads to <username>/nevertwice-embed
    python hf_publish.py --repo someone/other-name

Uploads: the merged checkpoint (sentence-transformers loadable), the f16 GGUF (Ollama
`ollama pull hf.co/<repo>` works off it), the LoRA adapter (retrainable artifact), and the
model card from hf_card/README.md.
"""
import argparse
import sys
from pathlib import Path

from huggingface_hub import HfApi

HERE = Path(__file__).parent
MERGED = HERE / "models" / "universal_v1_merged"
ADAPTER = HERE / "models" / "universal_v1"
GGUF = HERE / "models" / "nevertwice-embed-f16.gguf"
CARD = HERE / "hf_card" / "README.md"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None, help="target repo id (default <you>/nevertwice-embed)")
    args = ap.parse_args()
    api = HfApi()
    user = api.whoami()["name"]
    repo = args.repo or f"{user}/nevertwice-embed"
    print(f"publishing to {repo} as {user}")
    api.create_repo(repo, repo_type="model", exist_ok=True)
    # folders first, curated card LAST and README.md excluded from the folder upload:
    # sentence-transformers' .save() drops its own auto-generated README.md into the
    # checkpoint dir, and uploading the folder after the card silently clobbered the
    # curated card at repo root (review 2026-08 Dc7)
    api.upload_folder(folder_path=str(MERGED), repo_id=repo,
                      ignore_patterns=["README.md"],
                      commit_message="merged checkpoint (bge-m3 + universal-v1 LoRA, verified identical to adapter model)")
    api.upload_folder(folder_path=str(ADAPTER), repo_id=repo, path_in_repo="lora_adapter",
                      ignore_patterns=["README.md"],
                      commit_message="LoRA adapter (retrainable artifact)")
    api.upload_file(path_or_fileobj=str(GGUF), path_in_repo="nevertwice-embed-f16.gguf",
                    repo_id=repo, commit_message="f16 GGUF (ollama-importable)")
    api.upload_file(path_or_fileobj=str(CARD), path_in_repo="README.md",
                    repo_id=repo, commit_message="model card")
    print(f"DONE: https://huggingface.co/{repo}")


if __name__ == "__main__":
    for p in (MERGED, ADAPTER, GGUF, CARD):
        if not p.exists():
            sys.exit(f"missing artifact: {p}")
    # existence is not loadability (review 2026-08 D12): without the ST pooling files
    # the published repo loads MEAN-pooled downstream - refuse to publish a bare
    # checkpoint (run merge_and_verify.py first; it saves and verifies them)
    for req in ("modules.json", "1_Pooling"):
        if not (MERGED / req).exists():
            sys.exit(f"merged checkpoint lacks {req} - it would load mean-pooled "
                     f"downstream; run merge_and_verify.py first")
    main()
