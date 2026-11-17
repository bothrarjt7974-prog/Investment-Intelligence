"""
Claude API client.

All Claude API calls in this codebase route through this module.
This gives us a single place to handle retries, logging, token tracking,
and model selection.

Usage:
    from src.llm.claude_client import claude

    response = claude.extract(
        prompt_template="claim_extraction_v5",
        content=transcript_text,
        variables={"expert_name": "Ankur Warikoo"},
    )
"""

from __future__ import annotations

from pathlib import Path

from anthropic import Anthropic, BadRequestError
from loguru import logger
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from src.config.settings import settings

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


def should_retry_exception(exc: Exception) -> bool:
    """
    Return whether a retry is appropriate for this Anthropic error.

    Known limitation: billing errors are detected by string matching on the
    error message — brittle if Anthropic rewords it (worst case: 3 pointless
    retries on an unrecoverable billing error, then the same failure). The
    SDK does not expose a structured error code for this today.
    """
    if isinstance(exc, BadRequestError):
        message = str(exc).lower()
        return not any(token in message for token in ("credit balance", "insufficient credits", "quota"))
    return True


class ClaudeClient:
    """
    Wrapper around the Anthropic SDK.

    Responsibilities:
    - Load prompts from prompts/ directory by name + version
    - Apply variable substitution
    - Retry on transient errors
    - Log all calls for cost tracking
    - Pick the right model for the task (bulk vs synthesis)
    """

    def __init__(self) -> None:
        # SDK default timeout is up to 10 minutes per call — on a flaky
        # connection that means a single stalled request can block an
        # unattended cron run for that long before tenacity even gets a
        # chance to retry. A short-enough-to-fail-fast timeout lets the
        # existing retry decorator do its job instead.
        self.client = Anthropic(api_key=settings.anthropic_api_key, timeout=120.0)

    def load_prompt(self, prompt_name: str) -> str:
        """Load a prompt by name from prompts/ directory. Expects <name>.md file."""
        path = PROMPTS_DIR / f"{prompt_name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Prompt not found: {path}")
        return path.read_text(encoding="utf-8")

    def _substitute(self, template: str, variables: dict[str, str]) -> str:
        """Replace {{var}} placeholders in the template with values."""
        for key, value in variables.items():
            template = template.replace(f"{{{{{key}}}}}", value)
        return template

    @retry(
        retry=retry_if_exception(should_retry_exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def call(
        self,
        prompt_name: str,
        content: str,
        variables: dict[str, str] | None = None,
        model: str | None = None,
        max_tokens: int = 8192,
        effort: str = "low",
    ) -> str:
        """
        Make a Claude API call.

        Args:
            prompt_name: name of the prompt file in prompts/ (without .md)
            content: the actual content to process (transcript, claim, etc.)
            variables: optional {{var}} substitutions in the prompt template
            model: override default model selection (otherwise uses bulk model)
            max_tokens: response token limit
            effort: output_config.effort — on the 5.5-generation models,
                extended thinking cannot be disabled and its tokens count
                against max_tokens. "low" is the right default for this
                method's callers (classification/filtering/extraction —
                none of them are long-horizon reasoning tasks), and it
                leaves more of max_tokens for the actual response, which
                matters because this is the exact failure mode
                (claim_extraction_v5's "malformed JSON" fix) a thinking
                budget eating into max_tokens would reintroduce.

        Returns:
            The text content of Claude's response.
        """
        template = self.load_prompt(prompt_name)
        if variables:
            template = self._substitute(template, variables)

        # Inject content into the prompt — convention: prompt ends with "Content:"
        full_prompt = f"{template}\n\nContent:\n{content}"

        model_to_use = model or settings.claude_model_bulk

        logger.debug(
            f"Calling Claude | prompt={prompt_name} | model={model_to_use} "
            f"| content_chars={len(content)}"
        )

        message = self.client.messages.create(
            model=model_to_use,
            max_tokens=max_tokens,
            output_config={"effort": effort},
            messages=[{"role": "user", "content": full_prompt}],
        )

        # Concatenate all text blocks in the response
        text_blocks = [block.text for block in message.content if hasattr(block, "text")]
        result = "\n".join(text_blocks)

        # A response cut off at max_tokens is the likeliest cause of the
        # repeat "Malformed JSON" extraction failures (long stock lists) —
        # name it explicitly instead of letting it surface as a parse error.
        if message.stop_reason == "max_tokens":
            logger.warning(
                f"Claude response TRUNCATED at max_tokens={max_tokens} | prompt={prompt_name}"
            )

        # INFO, not DEBUG: cron logs run at INFO, and token counts are the
        # only spend signal we have (no cost table at MVP) — burying them in
        # DEBUG made API spend invisible in every scheduled run.
        logger.info(
            f"Claude usage | prompt={prompt_name} | model={model_to_use} "
            f"| input_tokens={message.usage.input_tokens} "
            f"| output_tokens={message.usage.output_tokens}"
        )

        return result

    def synthesize(
        self,
        prompt_name: str,
        content: str,
        variables: dict[str, str] | None = None,
        max_tokens: int = 16000,
        effort: str = "medium",
    ) -> str:
        """
        Call Claude for high-stakes synthesis using the Opus model.
        Use this for: morning briefing generation, complex reasoning comparison,
        final consolidated outputs.

        effort defaults to "medium" (Opus 5.5's own default) — theme
        synthesis and guidance generation are genuine multi-step reasoning,
        unlike call()'s classification-style callers, so they keep more
        thinking budget. max_tokens raised to 16000 for the same reason as
        call()'s default: thinking tokens now come out of this same budget.
        """
        return self.call(
            prompt_name=prompt_name,
            content=content,
            variables=variables,
            model=settings.claude_model_synthesis,
            max_tokens=max_tokens,
            effort=effort,
        )


# Singleton instance — import this throughout the codebase
claude = ClaudeClient()
