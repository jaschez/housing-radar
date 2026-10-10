"""One text-in, text-out call to Claude, with interchangeable backends.

  claude-cli      The Claude Code CLI (`claude -p`), using whatever account it is logged
                  into. `model` is a CLI alias such as `sonnet` or `opus`. No tools are
                  allowed: the prompt carries all the data and the answer is plain text.
  anthropic-api   The Anthropic API through the official Python SDK (`pip install
                  anthropic`). Credentials as the SDK resolves them (ANTHROPIC_API_KEY in
                  the environment or in the workspace secrets.env). `model` is an API model
                  id such as `claude-opus-5-5`.

Both return the model's text; parsing is the caller's job.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import workspace

# Models that accept the server-side refusal fallback (beta). On these, a request the
# model declines is re-run on a fallback model inside the same call.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-opus-5"}


class LLMError(RuntimeError):
    pass


def complete(prompt: str, *, backend: str, model: str, timeout: int = 1800) -> str:
    if backend == "claude-cli":
        return _claude_cli(prompt, model, timeout)
    if backend == "anthropic-api":
        return _anthropic_api(prompt, model, timeout)
    raise LLMError(f"unknown LLM backend: {backend}")


def _claude_cli(prompt: str, model: str, timeout: int) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise LLMError("backend claude-cli needs the `claude` command (Claude Code) in PATH")
    r = subprocess.run([exe, "-p", "--model", model, "--output-format", "text", "--permission-mode", "dontAsk"],
                       input=prompt, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise LLMError(f"claude -p exited {r.returncode}: {(r.stderr or r.stdout)[-500:]}")
    return r.stdout


def _anthropic_api(prompt: str, model: str, timeout: int) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise LLMError("backend anthropic-api needs `pip install anthropic`") from e
    key = workspace.secrets().get("ANTHROPIC_API_KEY")
    if key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = key
    client = anthropic.Anthropic(timeout=timeout)
    kwargs = dict(model=model, max_tokens=64000, messages=[{"role": "user", "content": prompt}])
    try:
        # Streaming: long input and output; get_final_message() returns the whole answer.
        if model in FALLBACK_MODELS:
            with client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                             **kwargs) as stream:
                msg = stream.get_final_message()
        else:
            with client.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise LLMError("Anthropic API: invalid or missing credentials (ANTHROPIC_API_KEY)") from e
    except anthropic.NotFoundError as e:
        raise LLMError(f"Anthropic API: unknown model {model}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Anthropic API: network error") from e
    if msg.stop_reason == "refusal":
        raise LLMError("the model declined the request (stop_reason=refusal)")
    if msg.stop_reason == "max_tokens":
        raise LLMError("the answer hit max_tokens; evaluate fewer listings per day")
    return "".join(b.text for b in msg.content if b.type == "text")
